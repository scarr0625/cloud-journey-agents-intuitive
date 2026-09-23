"""Transactional checkpoint storage, imported by batch jobs only."""

from __future__ import annotations

from datetime import timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .models import (
    AgentExecution,
    BatchAgent,
    CheckpointEvent,
    CheckpointStage,
    CheckpointStatus,
    OperationCheckpoint,
    new_id,
)
from .clock import utc_now


class OperationBusy(RuntimeError):
    """Another invocation owns the operation, or this invocation lost its lease."""


OPERATIONS = {
    BatchAgent.APM_VALIDATION: ("apm-validation", CheckpointStage.APM_VALIDATION),
    BatchAgent.AD_PROVISIONING: ("ad-provisioning", CheckpointStage.AD_SUBMISSION),
    BatchAgent.APP_FACTORY_HELPER: (
        "app-factory-validation",
        CheckpointStage.APP_FACTORY_VALIDATION,
    ),
}


class CheckpointStore:
    def __init__(
        self, session_factory: sessionmaker[Session], *, lease_seconds: int = 300
    ):
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self.session_factory = session_factory
        self.lease_seconds = lease_seconds

    def claim(
        self, journey_id: str, agent: BatchAgent, workflow_run_id: str
    ) -> OperationCheckpoint:
        agent = BatchAgent(agent)  # Reject orchestrator/chat and unknown agents.
        if not journey_id or not workflow_run_id:
            raise ValueError("journey_id and workflow_run_id are required")
        # A simultaneous first insert can lose the unique-key race. Retry the
        # whole transaction, then observe the winning invocation's lease.
        try:
            return self._claim(journey_id, agent, workflow_run_id)
        except IntegrityError:
            return self._claim(journey_id, agent, workflow_run_id)

    def _claim(
        self, journey_id: str, agent: BatchAgent, workflow_run_id: str
    ) -> OperationCheckpoint:
        operation_key, initial_stage = OPERATIONS[agent]
        now = utc_now()
        with self.session_factory.begin() as session:
            checkpoint = session.scalar(
                select(OperationCheckpoint)
                .where(
                    OperationCheckpoint.journey_id == journey_id,
                    OperationCheckpoint.operation_key == operation_key,
                )
                .with_for_update()
            )
            if checkpoint is not None and checkpoint.lease_expires_at is not None:
                expires_at = checkpoint.lease_expires_at
                if expires_at.tzinfo is None:  # SQLite does not retain time zones.
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if expires_at > now:
                    raise OperationBusy("The operation is already running")
                previous = session.get(AgentExecution, checkpoint.latest_execution_id)
                if previous.execution_result == "RUNNING":
                    previous.execution_result = "INTERRUPTED"
                    previous.ended_at = now
                    previous.error = (
                        "Execution lease expired before the invocation finished"
                    )

            execution = AgentExecution(
                execution_id=new_id(),
                journey_id=journey_id,
                agent_name=agent.value,
                workflow_run_id=workflow_run_id,
                execution_result="RUNNING",
            )
            session.add(execution)
            session.flush()
            lease = now + timedelta(seconds=self.lease_seconds)
            if checkpoint is None:
                checkpoint = OperationCheckpoint(
                    checkpoint_id=new_id(),
                    journey_id=journey_id,
                    latest_execution_id=execution.execution_id,
                    operation_key=operation_key,
                    current_stage=initial_stage.value,
                    checkpoint_status=CheckpointStatus.PENDING.value,
                    lease_expires_at=lease,
                    version=1,
                )
                session.add(checkpoint)
                session.flush()
                session.add(
                    CheckpointEvent(
                        checkpoint_id=checkpoint.checkpoint_id,
                        execution_id=execution.execution_id,
                        previous_status=None,
                        new_status=CheckpointStatus.PENDING.value,
                        stage=initial_stage.value,
                    )
                )
            else:
                claimed = session.execute(
                    update(OperationCheckpoint)
                    .where(
                        OperationCheckpoint.checkpoint_id == checkpoint.checkpoint_id,
                        OperationCheckpoint.version == checkpoint.version,
                    )
                    .values(
                        latest_execution_id=execution.execution_id,
                        lease_expires_at=lease,
                        version=checkpoint.version + 1,
                        updated_at=now,
                    )
                )
                if claimed.rowcount != 1:
                    raise OperationBusy("The operation was claimed concurrently")
                session.refresh(checkpoint)
            session.expunge(checkpoint)
            return checkpoint

    def save(
        self,
        checkpoint: OperationCheckpoint,
        *,
        stage: CheckpointStage,
        status: CheckpointStatus,
        external_reference: str | None = None,
        result_reference: str | None = None,
        error: str | None = None,
    ) -> OperationCheckpoint:
        stage, status = CheckpointStage(stage), CheckpointStatus(status)
        now = utc_now()
        with self.session_factory.begin() as session:
            changed = session.execute(
                update(OperationCheckpoint)
                .where(
                    OperationCheckpoint.checkpoint_id == checkpoint.checkpoint_id,
                    OperationCheckpoint.latest_execution_id
                    == checkpoint.latest_execution_id,
                    OperationCheckpoint.version == checkpoint.version,
                    OperationCheckpoint.lease_expires_at > now,
                )
                .values(
                    current_stage=stage.value,
                    checkpoint_status=status.value,
                    external_reference=external_reference
                    or checkpoint.external_reference,
                    result_reference=result_reference or checkpoint.result_reference,
                    last_error=error,
                    version=checkpoint.version + 1,
                    updated_at=now,
                )
            )
            if changed.rowcount != 1:
                raise OperationBusy("The invocation no longer owns this checkpoint")
            session.add(
                CheckpointEvent(
                    checkpoint_id=checkpoint.checkpoint_id,
                    execution_id=checkpoint.latest_execution_id,
                    previous_status=checkpoint.checkpoint_status,
                    new_status=status.value,
                    stage=stage.value,
                )
            )
            saved = session.get(OperationCheckpoint, checkpoint.checkpoint_id)
            session.expunge(saved)
            return saved

    def finish(
        self, checkpoint: OperationCheckpoint, *, error: str | None = None
    ) -> None:
        """A successful invocation can leave an operation WAITING."""
        now = utc_now()
        with self.session_factory.begin() as session:
            changed = session.execute(
                update(OperationCheckpoint)
                .where(
                    OperationCheckpoint.checkpoint_id == checkpoint.checkpoint_id,
                    OperationCheckpoint.latest_execution_id
                    == checkpoint.latest_execution_id,
                    OperationCheckpoint.version == checkpoint.version,
                    OperationCheckpoint.lease_expires_at > now,
                )
                .values(lease_expires_at=None)
            )
            if changed.rowcount != 1:
                raise OperationBusy("The invocation no longer owns this checkpoint")
            session.execute(
                update(AgentExecution)
                .where(
                    AgentExecution.execution_id == checkpoint.latest_execution_id,
                )
                .values(
                    execution_result="FAILED" if error else "SUCCEEDED",
                    ended_at=now,
                    error=error,
                )
            )
