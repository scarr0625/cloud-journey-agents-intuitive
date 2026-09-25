"""Contracts between agent business steps and durable execution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .models import BatchAgent, CheckpointStage, OperationCheckpoint


@dataclass(frozen=True)
class BusinessProgress:
    stage: str
    status: str
    result_reference: str
    external_reference: str | None = None
    # COMPLETED means the operation finished; a negative business result must
    # also stop Workflows from launching the next agent.
    successful: bool = True


class BusinessGateway(Protocol):
    def read_progress(
        self, journey_id: str, operation_key: str
    ) -> BusinessProgress | None: ...
    def validate_apm(self, journey_id: str) -> BusinessProgress: ...
    def submit_ad(self, journey_id: str, idempotency_key: str) -> BusinessProgress: ...
    def poll_ad(self, journey_id: str, request_id: str) -> BusinessProgress: ...
    def validate_app_factory(self, journey_id: str) -> BusinessProgress: ...


@dataclass(frozen=True)
class WorkflowStep:
    stage: CheckpointStage
    execute: Callable[[BusinessGateway, OperationCheckpoint], BusinessProgress]
    mark_running: bool = True


class BatchWorkflow(Protocol):
    """An agent owns step selection; infrastructure owns durable execution."""

    agent: BatchAgent
    modes: tuple[str, ...]

    def next_step(
        self, checkpoint: OperationCheckpoint, mode: str
    ) -> WorkflowStep | None: ...


@dataclass(frozen=True)
class JobResult:
    execution_id: str
    checkpoint_id: str
    checkpoint_status: str
    current_stage: str
    external_reference: str | None
    result_reference: str | None
    successful: bool = True
