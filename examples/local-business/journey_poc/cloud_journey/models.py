"""Journey business records and audit history in cloud-journey-db."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class AccessGroup(Base):
    __tablename__ = "access_groups"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)


class AccessGroupMember(Base):
    __tablename__ = "access_group_members"

    group_id: Mapped[str] = mapped_column(
        ForeignKey("access_groups.id", ondelete="CASCADE"), primary_key=True
    )
    user_subject: Mapped[str] = mapped_column(String(256), primary_key=True)


class ApmGroupAssignment(Base):
    """Maps each APM ID to the one group allowed to access it."""

    __tablename__ = "apm_group_assignments"

    apm_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    group_id: Mapped[str] = mapped_column(
        ForeignKey("access_groups.id", ondelete="CASCADE"), nullable=False, index=True
    )


class Journey(Base):
    __tablename__ = "journeys"
    __table_args__ = (UniqueConstraint("apm_id", name="uq_journeys_apm_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    apm_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    current_step: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    requested_by_email: Mapped[str] = mapped_column(String(320), nullable=False)
    owner_subject: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    access_group_id: Mapped[str] = mapped_column(
        ForeignKey("access_groups.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    context: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    events: Mapped[list["JourneyEvent"]] = relationship(
        back_populates="journey", order_by="JourneyEvent.id"
    )
    operations: Mapped[list["JourneyOperation"]] = relationship(
        back_populates="journey", order_by="JourneyOperation.created_at"
    )


class JourneyEvent(Base):
    __tablename__ = "journey_events"
    __table_args__ = (Index("ix_journey_events_journey_created", "journey_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    journey_id: Mapped[str] = mapped_column(
        ForeignKey("journeys.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    from_state: Mapped[str | None] = mapped_column(String(40), nullable=True)
    to_state: Mapped[str | None] = mapped_column(String(40), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    journey: Mapped[Journey] = relationship(back_populates="events")


class JourneyOperation(Base):
    __tablename__ = "journey_operations"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    journey_id: Mapped[str] = mapped_column(
        ForeignKey("journeys.id", ondelete="CASCADE"), nullable=False, index=True
    )
    operation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    journey: Mapped[Journey] = relationship(back_populates="operations")


class JourneyOperationStatus(Base):
    """Business-facing progress; never used as the batch execution checkpoint."""

    __tablename__ = "journey_operation_status"

    journey_id: Mapped[str] = mapped_column(ForeignKey("journeys.id", ondelete="CASCADE"), primary_key=True)
    operation_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    result_reference: Mapped[str] = mapped_column(String(256), nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class JourneyExternalDependency(Base):
    __tablename__ = "journey_external_dependency"

    journey_id: Mapped[str] = mapped_column(ForeignKey("journeys.id", ondelete="CASCADE"), primary_key=True)
    dependency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    external_reference: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
