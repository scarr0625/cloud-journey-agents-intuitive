"""Durable execution and recovery, independent of the shared batch wrappers."""

from __future__ import annotations

from dataclasses import asdict
import logging

from .checkpoints import CheckpointStore, OperationBusy
from .contracts import BatchWorkflow, BusinessGateway, BusinessProgress, JobResult
from .database import build_durable_engine, durable_session_factory
from .models import BatchAgent, CheckpointStage, CheckpointStatus, OperationCheckpoint

logger = logging.getLogger("cloud_journey_agents.durability")


class BatchRuntime:
    def __init__(self, store: CheckpointStore, business: BusinessGateway):
        self.store, self.business = store, business

    def run(
        self,
        workflow: BatchWorkflow,
        journey_id: str,
        workflow_run_id: str,
        *,
        mode: str = "resume",
    ) -> JobResult:
        agent = BatchAgent(workflow.agent)
        if mode not in workflow.modes:
            raise ValueError(
                f"{agent.value} supports modes: {', '.join(workflow.modes)}"
            )
        checkpoint = self.store.claim(journey_id, agent, workflow_run_id)
        try:
            # Journey DB is authoritative if the previous job died after its
            # business commit but before saving the corresponding checkpoint.
            progress = self.business.read_progress(journey_id, checkpoint.operation_key)
            if progress is not None and (
                checkpoint.current_stage,
                checkpoint.checkpoint_status,
                checkpoint.external_reference,
                checkpoint.result_reference,
            ) != (
                progress.stage,
                progress.status,
                progress.external_reference,
                progress.result_reference,
            ):
                checkpoint = self._save_progress(checkpoint, progress)
            elif (
                progress is None
                and checkpoint.checkpoint_status == CheckpointStatus.COMPLETED.value
            ):
                raise ValueError(
                    "Completed checkpoint has no persisted business outcome"
                )

            if checkpoint.checkpoint_status != CheckpointStatus.COMPLETED.value:
                step = workflow.next_step(checkpoint, mode)
                if step is not None:
                    if step.mark_running:
                        checkpoint = self._running(checkpoint, step.stage)
                    progress = step.execute(self.business, checkpoint)
                    checkpoint = self._save_progress(checkpoint, progress)
            self.store.finish(checkpoint)
        except OperationBusy:
            # Never allow a stale worker to overwrite a new owner's progress.
            raise
        except Exception as exc:
            checkpoint = self.store.save(
                checkpoint,
                stage=CheckpointStage(checkpoint.current_stage),
                status=CheckpointStatus.FAILED,
                error=str(exc),
            )
            self.store.finish(checkpoint, error=str(exc))
            raise
        return JobResult(
            checkpoint.latest_execution_id,
            checkpoint.checkpoint_id,
            checkpoint.checkpoint_status,
            checkpoint.current_stage,
            checkpoint.external_reference,
            checkpoint.result_reference,
            progress.successful if progress is not None else True,
        )

    def _running(
        self, checkpoint: OperationCheckpoint, stage: CheckpointStage
    ) -> OperationCheckpoint:
        return self.store.save(checkpoint, stage=stage, status=CheckpointStatus.RUNNING)

    def _save_progress(
        self, checkpoint: OperationCheckpoint, progress: BusinessProgress
    ) -> OperationCheckpoint:
        return self.store.save(
            checkpoint,
            stage=CheckpointStage(progress.stage),
            status=CheckpointStatus(progress.status),
            external_reference=progress.external_reference,
            result_reference=progress.result_reference,
        )


def execute_job(
    workflow: BatchWorkflow,
    journey_id: str,
    workflow_run_id: str,
    *,
    mode: str = "resume",
    business: BusinessGateway | None = None,
) -> JobResult:
    """Run one durable invocation using an injected or default MCP gateway.

    Supply ``business`` to reuse another repository's business/MCP implementation.
    The default adapter is loaded only when no gateway is supplied.
    """
    if mode not in workflow.modes:
        raise ValueError(f"Unsupported mode for {workflow.agent.value}: {mode}")
    engine = build_durable_engine()
    try:
        # Migrations are applied centrally; runtime identities only need data access.
        if business is None:
            from .mcp_gateway import build_mcp_business_gateway

            business = build_mcp_business_gateway(workflow.agent)
        runtime = BatchRuntime(
            CheckpointStore(durable_session_factory(engine)),
            business,
        )
        result = runtime.run(workflow, journey_id, workflow_run_id, mode=mode)
        logger.info(
            "Batch invocation finished",
            extra={"journey_fields": {"agent": workflow.agent.value, **asdict(result)}},
        )
        return result
    finally:
        engine.dispose()
