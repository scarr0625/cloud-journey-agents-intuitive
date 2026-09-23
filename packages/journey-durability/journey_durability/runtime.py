"""One non-interactive job invocation; jobs never call one another."""

from __future__ import annotations

from dataclasses import dataclass

from .business import BusinessGateway, BusinessProgress
from .checkpoints import CheckpointStore, OperationBusy
from .models import BatchAgent, CheckpointStage, CheckpointStatus, OperationCheckpoint


@dataclass(frozen=True)
class JobResult:
    execution_id: str
    checkpoint_id: str
    checkpoint_status: str
    current_stage: str
    external_reference: str | None
    result_reference: str | None
    successful: bool = True


class BatchRuntime:
    def __init__(self, store: CheckpointStore, business: BusinessGateway):
        self.store, self.business = store, business

    def run(
        self,
        agent: BatchAgent,
        journey_id: str,
        workflow_run_id: str,
        *,
        mode: str = "resume",
    ) -> JobResult:
        agent = BatchAgent(agent)
        if mode not in {"resume", "submit", "poll"}:
            raise ValueError("mode must be resume, submit, or poll")
        if agent != BatchAgent.AD_PROVISIONING and mode != "resume":
            raise ValueError("Only AD Provisioning has submit and poll modes")
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
                if agent == BatchAgent.AD_PROVISIONING:
                    if checkpoint.external_reference:
                        # The submission step is idempotent. Only a later poll
                        # (or resume invocation) observes the existing request.
                        if mode != "submit":
                            progress = self.business.poll_ad(
                                journey_id, checkpoint.external_reference
                            )
                            checkpoint = self._save_progress(checkpoint, progress)
                    else:
                        if mode == "poll":
                            raise ValueError(
                                "AD polling requires a saved MyAccess request ID"
                            )
                        checkpoint = self._running(
                            checkpoint, CheckpointStage.AD_SUBMISSION
                        )
                        progress = self.business.submit_ad(
                            journey_id, f"{journey_id}:{checkpoint.operation_key}"
                        )
                        checkpoint = self._save_progress(checkpoint, progress)
                else:
                    stage = (
                        CheckpointStage.APM_VALIDATION
                        if agent == BatchAgent.APM_VALIDATION
                        else CheckpointStage.APP_FACTORY_VALIDATION
                    )
                    checkpoint = self._running(checkpoint, stage)
                    progress = (
                        self.business.validate_apm(journey_id)
                        if agent == BatchAgent.APM_VALIDATION
                        else self.business.validate_app_factory(journey_id)
                    )
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
