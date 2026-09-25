"""Data API business operations and local stand-in for private MCP.

External integrations are simulated, like the existing PoC. All results and
external references are persisted in Journey DB before being returned to a job.
This module has no access to the checkpoint store.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .models import Journey, JourneyEvent, JourneyExternalDependency, JourneyOperationStatus
from .state_machine import Actor, JourneyNotFound, JourneyState, StateMachine


from cloud_journey_agents.durability.contracts import BusinessGateway, BusinessProgress


class LocalBusinessGateway:
    """In-process private MCP / Data API adapter for the simulated PoC."""

    def __init__(self, session_factory: sessionmaker[Session], *, pending_polls: int = 1):
        if pending_polls < 0:
            raise ValueError("pending_polls cannot be negative")
        self.session_factory = session_factory
        self.pending_polls = pending_polls

    @staticmethod
    def _journey(session: Session, journey_id: str) -> Journey:
        journey = session.scalar(select(Journey).where(Journey.id == journey_id).with_for_update())
        if journey is None:
            raise JourneyNotFound(journey_id)
        return journey

    def _transition(self, session: Session, journey: Journey, target: JourneyState, agent: str) -> None:
        """Commit the business result and the central state machine's events together."""
        if journey.status == target.value:
            return
        StateMachine(self.session_factory).transition(
            journey.id, target, actor=Actor("AGENT", agent),
            message=f"Simulated batch operation: {target.value}",
            metadata={"simulated": True}, session=session,
        )
        session.refresh(journey)

    @staticmethod
    def _progress(session: Session, record: JourneyOperationStatus) -> BusinessProgress:
        dependency = session.get(JourneyExternalDependency, (record.journey_id, "myaccess"))
        return BusinessProgress(
            stage=record.stage, status=record.status, result_reference=record.result_reference,
            external_reference=(dependency.external_reference if dependency and record.operation_key == "ad-provisioning" else None),
            successful=not bool(record.result.get("validation_errors")),
        )

    def read_progress(self, journey_id: str, operation_key: str) -> BusinessProgress | None:
        with self.session_factory() as session:
            if session.get(Journey, journey_id) is None:
                raise JourneyNotFound(journey_id)
            record = session.get(JourneyOperationStatus, (journey_id, operation_key))
            return self._progress(session, record) if record else None

    @staticmethod
    def _record(session: Session, journey_id: str, key: str, stage: str, status: str, result: dict[str, Any]) -> JourneyOperationStatus:
        record = session.get(JourneyOperationStatus, (journey_id, key))
        if record is None:
            record = JourneyOperationStatus(journey_id=journey_id, operation_key=key)
            session.add(record)
        record.stage, record.status = stage, status
        record.result_reference = f"journey-result:{journey_id}:{key}"
        record.result = result
        session.flush()
        return record

    def validate_apm(self, journey_id: str) -> BusinessProgress:
        with self.session_factory.begin() as session:
            journey = self._journey(session, journey_id)
            existing = session.get(JourneyOperationStatus, (journey_id, "apm-validation"))
            if existing:
                return self._progress(session, existing)
            agent = "apm-validation-agent"
            if journey.status == JourneyState.CREATED.value:
                self._transition(session, journey, JourneyState.VALIDATING_APM, agent)
            if journey.status == JourneyState.VALIDATING_APM.value:
                self._transition(session, journey, JourneyState.APM_VALIDATED, agent)
            # Reconcile validation previously performed by the interactive flow.
            validated = journey.status == JourneyState.APM_VALIDATED.value or session.scalar(
                select(JourneyEvent.id).where(JourneyEvent.journey_id == journey_id,
                    JourneyEvent.to_state == JourneyState.APM_VALIDATED.value).limit(1)
            )
            if not validated:
                raise ValueError("The Journey has no confirmed APM validation outcome")
            record = self._record(session, journey_id, "apm-validation", "APM_VALIDATION", "COMPLETED", {"valid": True, "apm_id": journey.apm_id, "simulated": True})
            return self._progress(session, record)

    def submit_ad(self, journey_id: str, idempotency_key: str) -> BusinessProgress:
        with self.session_factory.begin() as session:
            journey = self._journey(session, journey_id)
            existing = session.get(JourneyOperationStatus, (journey_id, "ad-provisioning"))
            if existing:
                return self._progress(session, existing)
            # The simulated provider returns the same ID for the same operation.
            # A real MyAccess adapter must honor this idempotency key or reconcile
            # the provider's request before retrying an ambiguous submission.
            if idempotency_key != f"{journey_id}:ad-provisioning":
                raise ValueError("Invalid AD submission idempotency key")
            request_id = "MA-" + uuid5(NAMESPACE_URL, idempotency_key).hex
            self._transition(session, journey, JourneyState.PROVISIONING_AGENT_IDENTITY, "ad-provisioning-agent")
            dependency = JourneyExternalDependency(
                journey_id=journey_id, dependency_key="myaccess", external_reference=request_id,
                status="PENDING", observation_count=0,
            )
            session.add(dependency)
            record = self._record(session, journey_id, "ad-provisioning", "AD_POLLING", "WAITING", {"request_id": request_id, "pending": True, "simulated": True})
            return self._progress(session, record)

    def poll_ad(self, journey_id: str, request_id: str) -> BusinessProgress:
        with self.session_factory.begin() as session:
            journey = self._journey(session, journey_id)
            dependency = session.get(JourneyExternalDependency, (journey_id, "myaccess"))
            record = session.get(JourneyOperationStatus, (journey_id, "ad-provisioning"))
            if dependency is None or record is None or dependency.external_reference != request_id:
                raise ValueError("MyAccess request does not match this Journey")
            if record.status == "COMPLETED":
                return self._progress(session, record)
            dependency.observation_count += 1
            pending = dependency.observation_count <= self.pending_polls
            dependency.status = "PENDING" if pending else "COMPLETED"
            if not pending:
                self._transition(session, journey, JourneyState.AGENT_IDENTITY_READY, "ad-provisioning-agent")
            record = self._record(session, journey_id, "ad-provisioning", "AD_POLLING", "WAITING" if pending else "COMPLETED", {
                "request_id": request_id, "pending": pending,
                "observation_count": dependency.observation_count, "simulated": True,
            })
            return self._progress(session, record)

    def validate_app_factory(self, journey_id: str) -> BusinessProgress:
        with self.session_factory.begin() as session:
            journey = self._journey(session, journey_id)
            existing = session.get(JourneyOperationStatus, (journey_id, "app-factory-validation"))
            if existing:
                return self._progress(session, existing)
            agent = "app-factory-helper-agent"
            self._transition(session, journey, JourneyState.PREPARING_APP_FACTORY, agent)
            # The simulated validator requires a captured application inventory.
            errors = [] if journey.context.get("inventory") else ["Application inventory is required"]
            outcome = JourneyState.APP_FACTORY_VALIDATION_ERROR if errors else JourneyState.READY_TO_PROVISION
            self._transition(session, journey, outcome, agent)
            record = self._record(session, journey_id, "app-factory-validation", "APP_FACTORY_VALIDATION", "COMPLETED", {
                "outcome": outcome.value, "validation_errors": errors, "simulated": True,
            })
            return self._progress(session, record)

    def authorized_status(self, journey_id: str, user_subject: str) -> dict[str, Any]:
        """Read only Journey DB; the caller identity comes from the trusted server."""
        machine = StateMachine(self.session_factory)
        journey = machine.get_group_journey(journey_id, machine.get_access_groups_for_user(user_subject))
        with self.session_factory() as session:
            operations = session.scalars(select(JourneyOperationStatus).where(JourneyOperationStatus.journey_id == journey_id)).all()
            dependencies = session.scalars(select(JourneyExternalDependency).where(JourneyExternalDependency.journey_id == journey_id)).all()
            return {
                "journey_id": journey.id, "current_state": journey.status,
                "operations": [{"operation_key": r.operation_key, **asdict(self._progress(session, r)), "result": r.result} for r in operations],
                "external_dependencies": [{"dependency_key": d.dependency_key, "external_reference": d.external_reference, "status": d.status} for d in dependencies],
            }
