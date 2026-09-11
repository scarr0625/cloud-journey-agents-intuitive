"""The sole authority for Journey state transitions."""

from __future__ import annotations

import logging
import secrets
from collections.abc import Collection
from dataclasses import dataclass
from enum import Enum
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .models import (
    AccessGroupMember,
    ApmGroupAssignment,
    Journey,
    JourneyEvent,
)

logger = logging.getLogger(__name__)


class JourneyState(str, Enum):
    CREATED = "CREATED"
    VALIDATING_APM = "VALIDATING_APM"
    APM_VALIDATED = "APM_VALIDATED"
    DISCOVERING_CLOUD_SERVICES = "DISCOVERING_CLOUD_SERVICES"
    COLLECTING_ASSET_INVENTORY = "COLLECTING_ASSET_INVENTORY"
    ASSET_INVENTORY_COMPLETE = "ASSET_INVENTORY_COMPLETE"
    PROVISIONING_AGENT_IDENTITY = "PROVISIONING_AGENT_IDENTITY"
    AGENT_IDENTITY_READY = "AGENT_IDENTITY_READY"
    PREPARING_APP_FACTORY = "PREPARING_APP_FACTORY"
    APP_FACTORY_READY = "APP_FACTORY_READY"
    GENERATING_PLAN = "GENERATING_PLAN"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUBMITTING_CLOUD_BUILD = "SUBMITTING_CLOUD_BUILD"
    CLOUD_BUILD_RUNNING = "CLOUD_BUILD_RUNNING"
    VALIDATING_DEPLOYMENT = "VALIDATING_DEPLOYMENT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"


@dataclass(frozen=True)
class DomainEventDefinition:
    event_type: str
    description: str


JOURNEY_STARTED_EVENT = DomainEventDefinition(
    "JourneyStarted", "New Journey created"
)
JOURNEY_DATA_CHANGED_EVENT = DomainEventDefinition(
    "JourneyDataChanged", "User/System Data updated"
)

# Business events from the Journey event catalog. These are emitted alongside
# the lower-level STATE_TRANSITION audit event at the durable checkpoint that
# represents the business outcome.
DOMAIN_EVENTS_BY_STATE: dict[JourneyState, tuple[DomainEventDefinition, ...]] = {
    JourneyState.ASSET_INVENTORY_COMPLETE: (
        DomainEventDefinition("ChecklistCalculated", "Checklist Refreshed"),
    ),
    JourneyState.WAITING_FOR_APPROVAL: (
        DomainEventDefinition("GovernanceTicketCreated", "Governance Started"),
    ),
    JourneyState.APPROVED: (
        DomainEventDefinition("GovernanceStatusChanged", "Approval Changed"),
    ),
    JourneyState.REJECTED: (
        DomainEventDefinition("GovernanceStatusChanged", "Approval Changed"),
    ),
    JourneyState.PROVISIONING_AGENT_IDENTITY: (
        DomainEventDefinition("MyAccessRequestSubmitted", "Access Request Created"),
    ),
    JourneyState.AGENT_IDENTITY_READY: (
        DomainEventDefinition("MyAccessStatusChanged", "Access Updated"),
    ),
    JourneyState.PREPARING_APP_FACTORY: (
        DomainEventDefinition(
            "DependencyCompleted", "External dependency completed"
        ),
    ),
    JourneyState.APP_FACTORY_READY: (
        DomainEventDefinition("ReadinessEvaluated", "Readiness Decision Produced"),
        DomainEventDefinition(
            "AppFactoryManifestPublished", "Manifest Generated"
        ),
    ),
    JourneyState.SUBMITTING_CLOUD_BUILD: (
        DomainEventDefinition("ProvisioningStarted", "Deployment Started"),
    ),
    JourneyState.CLOUD_BUILD_RUNNING: (
        DomainEventDefinition("ProvisioningStatusChanged", "Deployment Changed"),
    ),
    JourneyState.VALIDATING_DEPLOYMENT: (
        DomainEventDefinition("ProvisioningStatusChanged", "Deployment Changed"),
    ),
    JourneyState.COMPLETED: (
        DomainEventDefinition("ProvisioningCompleted", "Deployment Finished"),
        DomainEventDefinition("JourneyTransitionedToBAU", "Journey Completed"),
    ),
}


PROCESSING_STATES = {
    JourneyState.VALIDATING_APM,
    JourneyState.DISCOVERING_CLOUD_SERVICES,
    JourneyState.COLLECTING_ASSET_INVENTORY,
    JourneyState.PROVISIONING_AGENT_IDENTITY,
    JourneyState.PREPARING_APP_FACTORY,
    JourneyState.GENERATING_PLAN,
    JourneyState.SUBMITTING_CLOUD_BUILD,
    JourneyState.CLOUD_BUILD_RUNNING,
    JourneyState.VALIDATING_DEPLOYMENT,
}

ALLOWED_TRANSITIONS: dict[JourneyState, frozenset[JourneyState]] = {
    JourneyState.CREATED: frozenset({JourneyState.VALIDATING_APM}),
    JourneyState.VALIDATING_APM: frozenset({JourneyState.APM_VALIDATED, JourneyState.FAILED}),
    JourneyState.APM_VALIDATED: frozenset({JourneyState.DISCOVERING_CLOUD_SERVICES}),
    JourneyState.DISCOVERING_CLOUD_SERVICES: frozenset(
        {JourneyState.COLLECTING_ASSET_INVENTORY, JourneyState.FAILED}
    ),
    JourneyState.COLLECTING_ASSET_INVENTORY: frozenset(
        {JourneyState.ASSET_INVENTORY_COMPLETE, JourneyState.FAILED}
    ),
    JourneyState.ASSET_INVENTORY_COMPLETE: frozenset({JourneyState.GENERATING_PLAN}),
    JourneyState.PROVISIONING_AGENT_IDENTITY: frozenset(
        {JourneyState.AGENT_IDENTITY_READY, JourneyState.FAILED}
    ),
    JourneyState.AGENT_IDENTITY_READY: frozenset(
        {JourneyState.PREPARING_APP_FACTORY}
    ),
    JourneyState.PREPARING_APP_FACTORY: frozenset(
        {JourneyState.APP_FACTORY_READY, JourneyState.FAILED}
    ),
    JourneyState.APP_FACTORY_READY: frozenset({JourneyState.SUBMITTING_CLOUD_BUILD}),
    JourneyState.GENERATING_PLAN: frozenset(
        {JourneyState.WAITING_FOR_APPROVAL, JourneyState.FAILED}
    ),
    JourneyState.WAITING_FOR_APPROVAL: frozenset(
        {JourneyState.APPROVED, JourneyState.REJECTED}
    ),
    JourneyState.APPROVED: frozenset({JourneyState.PROVISIONING_AGENT_IDENTITY}),
    JourneyState.REJECTED: frozenset(),
    JourneyState.SUBMITTING_CLOUD_BUILD: frozenset(
        {JourneyState.CLOUD_BUILD_RUNNING, JourneyState.FAILED}
    ),
    JourneyState.CLOUD_BUILD_RUNNING: frozenset(
        {JourneyState.VALIDATING_DEPLOYMENT, JourneyState.FAILED}
    ),
    JourneyState.VALIDATING_DEPLOYMENT: frozenset(
        {JourneyState.COMPLETED, JourneyState.FAILED}
    ),
    JourneyState.COMPLETED: frozenset(),
    JourneyState.FAILED: frozenset({JourneyState.RETRYING}),
    JourneyState.RETRYING: frozenset(PROCESSING_STATES),
}


class JourneyError(Exception):
    """Base class for user-visible Journey failures."""


class JourneyNotFound(JourneyError):
    def __init__(self, journey_id: str):
        super().__init__(f"Journey {journey_id} was not found")
        self.journey_id = journey_id


class InvalidTransition(JourneyError):
    def __init__(self, journey_id: str, from_state: JourneyState, to_state: JourneyState):
        super().__init__(
            f"Invalid transition for {journey_id}: {from_state.value} -> {to_state.value}"
        )
        self.journey_id = journey_id
        self.from_state = from_state
        self.to_state = to_state


class ConcurrentTransition(JourneyError):
    pass


class DuplicateApmId(JourneyError):
    def __init__(self, apm_id: str):
        super().__init__("Unable to create a Journey for the requested APM ID")
        self.apm_id = apm_id


class JourneyPersistenceError(JourneyError):
    def __init__(self):
        super().__init__("Unable to persist the Journey because of a database constraint")


class JourneyUnavailable(JourneyError):
    """Non-disclosing response for missing or inaccessible Journey data."""

    def __init__(self):
        super().__init__("No accessible Journey was found for the supplied identifier")


@dataclass(frozen=True)
class Actor:
    actor_type: str
    actor_id: str


@dataclass(frozen=True)
class TransitionResult:
    journey_id: str
    from_state: JourneyState
    to_state: JourneyState
    version: int


def step_name(state: JourneyState) -> str:
    return state.value.lower()


class StateMachine:
    """Validates and persists state transitions with lock + version protection."""

    def __init__(self, session_factory: sessionmaker[Session]):
        self._session_factory = session_factory

    @staticmethod
    def _domain_event(
        *,
        journey_id: str,
        definition: DomainEventDefinition,
        from_state: str | None,
        to_state: str,
        actor: Actor,
        trigger: str,
        source_metadata: dict[str, Any] | None = None,
    ) -> JourneyEvent:
        return JourneyEvent(
            journey_id=journey_id,
            event_type=definition.event_type,
            from_state=from_state,
            to_state=to_state,
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
            message=definition.description,
            event_metadata={
                **(source_metadata or {}),
                "domain_event": True,
                "description": definition.description,
                "trigger": trigger,
            },
        )

    def create_journey(
        self,
        *,
        apm_id: str,
        requested_by: str,
        requested_by_email: str,
        role: str,
        access_group_id: str,
        owner_subject: str | None = None,
        context: dict[str, Any] | None = None,
        journey_id: str | None = None,
    ) -> Journey:
        journey = Journey(
            id=journey_id or f"J-{secrets.token_hex(4).upper()}",
            apm_id=apm_id,
            status=JourneyState.CREATED.value,
            current_step=step_name(JourneyState.CREATED),
            version=1,
            requested_by=requested_by,
            requested_by_email=requested_by_email,
            owner_subject=owner_subject or requested_by,
            access_group_id=access_group_id,
            role=role,
            context=context or {},
        )
        try:
            with self._session_factory.begin() as session:
                session.add(journey)
                session.add(
                    JourneyEvent(
                        journey_id=journey.id,
                        event_type="JOURNEY_CREATED",
                        from_state=None,
                        to_state=JourneyState.CREATED.value,
                        actor_type="USER",
                        actor_id=requested_by,
                        message=f"Journey created for {apm_id}",
                        event_metadata={},
                    )
                )
                session.add(
                    self._domain_event(
                        journey_id=journey.id,
                        definition=JOURNEY_STARTED_EVENT,
                        from_state=None,
                        to_state=JourneyState.CREATED.value,
                        actor=Actor("USER", requested_by),
                        trigger="JOURNEY_CREATED",
                        source_metadata={
                            "apm_id": apm_id,
                            "access_group_id": access_group_id,
                        },
                    )
                )
        except IntegrityError as exc:
            if self._is_duplicate_apm_error(exc):
                raise DuplicateApmId(apm_id) from exc
            logger.exception("Journey creation failed because of a database constraint")
            raise JourneyPersistenceError() from exc
        return journey

    @staticmethod
    def _is_duplicate_apm_error(exc: IntegrityError) -> bool:
        original = getattr(exc, "orig", None)
        diagnostic = getattr(original, "diag", None)
        constraint_name = getattr(diagnostic, "constraint_name", None)
        if constraint_name in {"uq_journeys_apm_id", "ix_journeys_apm_id"}:
            return True
        message = str(original or exc).lower()
        return (
            "unique" in message
            and "journeys" in message
            and "apm_id" in message
        )

    def transition(
        self,
        journey_id: str,
        to_state: JourneyState,
        *,
        actor: Actor,
        message: str | None = None,
        metadata: dict[str, Any] | None = None,
        last_error: str | None = None,
    ) -> TransitionResult:
        with self._session_factory.begin() as session:
            # PostgreSQL holds this row lock until the event and state commit together.
            journey = session.execute(
                select(Journey).where(Journey.id == journey_id).with_for_update()
            ).scalar_one_or_none()
            if journey is None:
                raise JourneyNotFound(journey_id)

            from_state = JourneyState(journey.status)
            if to_state not in ALLOWED_TRANSITIONS[from_state]:
                raise InvalidTransition(journey_id, from_state, to_state)

            next_version = journey.version + 1
            values: dict[str, Any] = {
                "status": to_state.value,
                "current_step": step_name(to_state),
                "version": next_version,
            }
            if to_state == JourneyState.FAILED:
                values["last_error"] = last_error or message
            elif to_state != JourneyState.RETRYING:
                values["last_error"] = None

            # The predicate is a second line of defense on databases where FOR UPDATE
            # is unavailable (including the lightweight SQLite unit-test backend).
            result = session.execute(
                update(Journey)
                .where(
                    Journey.id == journey_id,
                    Journey.status == from_state.value,
                    Journey.version == journey.version,
                )
                .values(**values)
            )
            if result.rowcount != 1:
                raise ConcurrentTransition(
                    f"Journey {journey_id} changed during transition"
                )

            session.add(
                JourneyEvent(
                    journey_id=journey_id,
                    event_type="STATE_TRANSITION",
                    from_state=from_state.value,
                    to_state=to_state.value,
                    actor_type=actor.actor_type,
                    actor_id=actor.actor_id,
                    message=message,
                    event_metadata=metadata or {},
                )
            )
            session.add_all(
                self._domain_event(
                    journey_id=journey_id,
                    definition=definition,
                    from_state=from_state.value,
                    to_state=to_state.value,
                    actor=actor,
                    trigger="STATE_TRANSITION",
                    source_metadata=metadata,
                )
                for definition in DOMAIN_EVENTS_BY_STATE.get(to_state, ())
            )
            return TransitionResult(journey_id, from_state, to_state, next_version)

    def record_event(
        self,
        journey_id: str,
        *,
        event_type: str,
        actor: Actor,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._session_factory.begin() as session:
            journey = session.get(Journey, journey_id)
            if journey is None:
                raise JourneyNotFound(journey_id)
            session.add(
                JourneyEvent(
                    journey_id=journey_id,
                    event_type=event_type,
                    from_state=journey.status,
                    to_state=journey.status,
                    actor_type=actor.actor_type,
                    actor_id=actor.actor_id,
                    message=message,
                    event_metadata=metadata or {},
                )
            )

    def merge_context(
        self,
        journey_id: str,
        updates: dict[str, Any],
        *,
        actor: Actor,
        message: str,
    ) -> dict[str, Any]:
        """Persist gathered Journey knowledge without changing its state/version."""
        with self._session_factory.begin() as session:
            journey = session.execute(
                select(Journey).where(Journey.id == journey_id).with_for_update()
            ).scalar_one_or_none()
            if journey is None:
                raise JourneyNotFound(journey_id)
            merged = dict(journey.context or {})
            merged.update(updates)
            session.execute(
                update(Journey)
                .where(Journey.id == journey_id)
                .values(context=merged)
            )
            session.add(
                JourneyEvent(
                    journey_id=journey_id,
                    event_type="CONTEXT_UPDATED",
                    from_state=journey.status,
                    to_state=journey.status,
                    actor_type=actor.actor_type,
                    actor_id=actor.actor_id,
                    message=message,
                    event_metadata={"updated_sections": sorted(updates)},
                )
            )
            session.add(
                self._domain_event(
                    journey_id=journey_id,
                    definition=JOURNEY_DATA_CHANGED_EVENT,
                    from_state=journey.status,
                    to_state=journey.status,
                    actor=actor,
                    trigger="CONTEXT_UPDATED",
                    source_metadata={"updated_sections": sorted(updates)},
                )
            )
            return merged

    def get_journey(self, journey_id: str) -> Journey:
        with self._session_factory() as session:
            journey = session.get(Journey, journey_id)
            if journey is None:
                raise JourneyNotFound(journey_id)
            session.expunge(journey)
            return journey

    def find_journey_by_apm_id(self, apm_id: str) -> Journey | None:
        """Internal unscoped lookup; callers must apply ownership before returning it."""
        with self._session_factory() as session:
            journey = session.scalar(select(Journey).where(Journey.apm_id == apm_id))
            if journey is not None:
                session.expunge(journey)
            return journey

    def get_apm_access_group(self, apm_id: str) -> str | None:
        """Return the group mapped to an APM ID, if it is configured."""
        with self._session_factory() as session:
            return session.scalar(
                select(ApmGroupAssignment.group_id).where(
                    ApmGroupAssignment.apm_id == apm_id
                )
            )

    def get_access_groups_for_user(self, user_subject: str) -> frozenset[str]:
        with self._session_factory() as session:
            return frozenset(
                session.scalars(
                    select(AccessGroupMember.group_id).where(
                        AccessGroupMember.user_subject == user_subject
                    )
                )
            )

    def list_apm_ids_for_groups(self, group_ids: Collection[str]) -> list[str]:
        if not group_ids:
            return []
        with self._session_factory() as session:
            return list(
                session.scalars(
                    select(ApmGroupAssignment.apm_id)
                    .where(ApmGroupAssignment.group_id.in_(group_ids))
                    .order_by(ApmGroupAssignment.apm_id)
                )
            )

    def get_group_journey(
        self, journey_id: str, group_ids: Collection[str]
    ) -> Journey:
        """Return a Journey only when it belongs to one of the caller's groups."""
        if not group_ids:
            raise JourneyUnavailable()
        with self._session_factory() as session:
            journey = session.scalar(
                select(Journey)
                .where(
                    Journey.id == journey_id,
                    Journey.access_group_id.in_(group_ids),
                )
            )
            if journey is None:
                raise JourneyUnavailable()
            session.expunge(journey)
            return journey

    def get_group_journey_by_apm_id(
        self, apm_id: str, group_ids: Collection[str]
    ) -> Journey:
        if not group_ids:
            raise JourneyUnavailable()
        with self._session_factory() as session:
            journey = session.scalar(
                select(Journey)
                .where(
                    Journey.apm_id == apm_id,
                    Journey.access_group_id.in_(group_ids),
                )
            )
            if journey is None:
                raise JourneyUnavailable()
            session.expunge(journey)
            return journey

    def get_events(self, journey_id: str) -> list[JourneyEvent]:
        with self._session_factory() as session:
            if session.get(Journey, journey_id) is None:
                raise JourneyNotFound(journey_id)
            events = list(
                session.scalars(
                    select(JourneyEvent)
                    .where(JourneyEvent.journey_id == journey_id)
                    .order_by(JourneyEvent.id)
                )
            )
            for event in events:
                session.expunge(event)
            return events
