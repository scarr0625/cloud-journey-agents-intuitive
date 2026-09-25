"""Batch execution records; this metadata belongs only in durable-state-db."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .clock import utc_now


class CheckpointStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class BatchAgent(str, Enum):
    APM_VALIDATION = "apm-validation-agent"
    AD_PROVISIONING = "ad-provisioning-agent"
    APP_FACTORY_HELPER = "app-factory-helper-agent"


class CheckpointStage(str, Enum):
    APM_VALIDATION = "APM_VALIDATION"
    AD_SUBMISSION = "AD_SUBMISSION"
    AD_POLLING = "AD_POLLING"
    APP_FACTORY_VALIDATION = "APP_FACTORY_VALIDATION"


def new_id() -> str:
    return str(uuid4())


class DurableBase(DeclarativeBase):
    pass


class AgentExecution(DurableBase):
    __tablename__ = "agent_execution"
    __table_args__ = (
        CheckConstraint(
            "agent_name IN ('apm-validation-agent', 'ad-provisioning-agent', "
            "'app-factory-helper-agent')",
            name="ck_execution_agent",
        ),
    )

    execution_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_id
    )
    # Existing PoC Journey IDs are J-..., so keep a logical string reference.
    journey_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_run_id: Mapped[str] = mapped_column(
        String(256), nullable=False, index=True
    )
    execution_result: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class OperationCheckpoint(DurableBase):
    __tablename__ = "operation_checkpoint"
    __table_args__ = (
        UniqueConstraint("journey_id", "operation_key", name="uq_checkpoint_operation"),
        CheckConstraint(
            "checkpoint_status IN ('PENDING', 'RUNNING', 'WAITING', 'COMPLETED', 'FAILED')",
            name="ck_checkpoint_status",
        ),
    )

    checkpoint_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_id
    )
    journey_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    latest_execution_id: Mapped[str] = mapped_column(
        ForeignKey("agent_execution.execution_id")
    )
    operation_key: Mapped[str] = mapped_column(String(128), nullable=False)
    current_stage: Mapped[str] = mapped_column(String(64), nullable=False)
    checkpoint_status: Mapped[str] = mapped_column(String(20), nullable=False)
    external_reference: Mapped[str | None] = mapped_column(String(256))
    result_reference: Mapped[str | None] = mapped_column(String(256))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CheckpointEvent(DurableBase):
    __tablename__ = "checkpoint_event"
    __table_args__ = (
        CheckConstraint(
            "new_status IN ('PENDING', 'RUNNING', 'WAITING', 'COMPLETED', 'FAILED')",
            name="ck_event_new_status",
        ),
        CheckConstraint(
            "previous_status IS NULL OR previous_status IN "
            "('PENDING', 'RUNNING', 'WAITING', 'COMPLETED', 'FAILED')",
            name="ck_event_previous_status",
        ),
    )

    checkpoint_event_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_id
    )
    checkpoint_id: Mapped[str] = mapped_column(
        ForeignKey("operation_checkpoint.checkpoint_id"), index=True
    )
    execution_id: Mapped[str] = mapped_column(
        ForeignKey("agent_execution.execution_id"), index=True
    )
    previous_status: Mapped[str | None] = mapped_column(String(20))
    new_status: Mapped[str] = mapped_column(String(20), nullable=False)
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
