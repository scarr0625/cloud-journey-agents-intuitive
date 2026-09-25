"""Persist checkpoint ownership and progress exclusively through Schwab MCP.

The server atomically claims leases, compares fencing versions, saves audit
events, and finishes executions. Each mutation has a stable idempotency ID
for transport retries. Agents validate returned identity/version fields and
stop on ambiguous persistence failures rather than overwrite uncertain state.
"""

from uuid import uuid4

from .models import BatchAgent, CheckpointStage, CheckpointStatus, OperationCheckpoint
from ..guardrails import DURABLE_TOOLS
from ..mcp import McpClient, McpError, call_idempotent


class CheckpointError(McpError):
    """Persistence failed or its returned state cannot be trusted."""


class OperationBusy(CheckpointError):
    """Another invocation owns the operation, or this invocation lost its lease."""


OPERATIONS = {
    BatchAgent.APM_VALIDATION: ("apm-validation", {CheckpointStage.APM_VALIDATION}),
    BatchAgent.AD_PROVISIONING: (
        "ad-provisioning", {CheckpointStage.AD_SUBMISSION, CheckpointStage.AD_POLLING}
    ),
    BatchAgent.APP_FACTORY_HELPER: (
        "app-factory-validation", {CheckpointStage.APP_FACTORY_VALIDATION}
    ),
}


class CheckpointStore:
    def __init__(self, agent: BatchAgent, *, client=None):
        self.agent = BatchAgent(agent)
        self.client = client if client is not None else McpClient(DURABLE_TOOLS)

    def _mutate(self, name, arguments):
        arguments = {**arguments, "mutation_id": str(uuid4())}
        try:
            return call_idempotent(self.client, name, arguments)
        except McpError as exc:
            if exc.code in {"OPERATION_BUSY", "STALE_CHECKPOINT", "LEASE_EXPIRED"}:
                raise OperationBusy("The operation is busy or its lease was lost", code=exc.code) from exc
            raise CheckpointError("Durable persistence through MCP failed", code=exc.code) from exc

    def _snapshot(self, payload, *, journey_id, workflow_run_id):
        try:
            checkpoint = OperationCheckpoint.model_validate(payload["checkpoint"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CheckpointError("Invalid MCP checkpoint response") from exc
        operation, stages = OPERATIONS[self.agent]
        if (
            checkpoint.agent_name != self.agent
            or checkpoint.journey_id != journey_id
            or checkpoint.workflow_run_id != workflow_run_id
            or checkpoint.operation_key != operation
            or checkpoint.current_stage not in stages
        ):
            raise CheckpointError("MCP checkpoint does not match the requested operation")
        if checkpoint.checkpoint_status in {CheckpointStatus.WAITING, CheckpointStatus.COMPLETED}:
            if not checkpoint.result_reference:
                raise CheckpointError("Recorded progress requires a business result reference")
        if checkpoint.checkpoint_status == CheckpointStatus.WAITING and self.agent != BatchAgent.AD_PROVISIONING:
            raise CheckpointError("Only AD provisioning supports pending external work")
        if self.agent == BatchAgent.AD_PROVISIONING and checkpoint.current_stage == CheckpointStage.AD_POLLING:
            if not checkpoint.external_reference:
                raise CheckpointError("AD polling requires the saved provider request ID")
        return checkpoint

    def claim(self, journey_id, agent, workflow_run_id):
        if BatchAgent(agent) != self.agent or not journey_id or not workflow_run_id:
            raise ValueError("The agent and operation identifiers must match this store")
        payload = self._mutate("claim_durable_operation", {
            "journey_id": journey_id, "agent_name": self.agent.value,
            "workflow_run_id": workflow_run_id,
        })
        checkpoint = self._snapshot(payload, journey_id=journey_id, workflow_run_id=workflow_run_id)
        if checkpoint.lease_expires_at is None:
            raise CheckpointError("A claimed checkpoint must have a lease")
        return checkpoint

    def _ownership(self, checkpoint):
        if checkpoint.agent_name != self.agent:
            raise ValueError("Checkpoint belongs to another agent")
        return {
            "journey_id": checkpoint.journey_id,
            "agent_name": self.agent.value,
            "workflow_run_id": checkpoint.workflow_run_id,
            "checkpoint_id": checkpoint.checkpoint_id,
            "execution_id": checkpoint.latest_execution_id,
            "expected_version": checkpoint.version,
        }

    def _updated(self, payload, previous, *, finished=False):
        saved = self._snapshot(payload, journey_id=previous.journey_id, workflow_run_id=previous.workflow_run_id)
        if (
            saved.checkpoint_id != previous.checkpoint_id
            or saved.latest_execution_id != previous.latest_execution_id
            or saved.version != previous.version + 1
            or (saved.lease_expires_at is None) != finished
        ):
            raise CheckpointError("MCP checkpoint ownership/version acknowledgment is invalid")
        return saved

    def save(self, checkpoint, *, stage, status, external_reference=None, result_reference=None, error=None):
        changes = {
            "current_stage": CheckpointStage(stage).value,
            "checkpoint_status": CheckpointStatus(status).value,
            "external_reference": external_reference or checkpoint.external_reference,
            "result_reference": result_reference or checkpoint.result_reference,
            "last_error": error,
        }
        payload = self._mutate("save_durable_checkpoint", {**self._ownership(checkpoint), **changes})
        saved = self._updated(payload, checkpoint)
        if any(getattr(saved, key) != value for key, value in changes.items()):
            raise CheckpointError("MCP did not acknowledge the requested checkpoint changes")
        return saved

    def finish(self, checkpoint, *, error=None):
        payload = self._mutate("finish_durable_operation", {
            **self._ownership(checkpoint), "error": error,
        })
        finished = self._updated(payload, checkpoint, finished=True)
        for field in ("current_stage", "checkpoint_status", "external_reference", "result_reference"):
            if getattr(finished, field) != getattr(checkpoint, field):
                raise CheckpointError("Finishing an invocation must preserve operation progress")
