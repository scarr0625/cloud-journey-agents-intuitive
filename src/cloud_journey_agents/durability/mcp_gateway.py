"""Optional adapter for this repository's private MCP business contract."""

from .contracts import BusinessProgress
from .models import BatchAgent
from ..guardrails import BATCH_TOOLS
from ..mcp import McpClient, McpError


class McpBusinessGateway:
    def __init__(self, client: McpClient):
        self.client = client

    @staticmethod
    def _progress(result, journey_id: str, operation_key: str) -> BusinessProgress:
        if not isinstance(result, dict):
            raise McpError("A business operation must return an object")
        if (
            result.get("journey_id") != journey_id
            or result.get("operation_key") != operation_key
        ):
            raise McpError("MCP result does not match the requested Journey operation")
        reference = result.get("result_reference")
        if not isinstance(reference, str) or not reference:
            raise McpError("MCP success requires a persisted result_reference")
        outcome = result.get("outcome")
        mappings = {
            "apm-validation": ("APM_VALIDATION", {"VALID"}, {"INVALID"}),
            "ad-provisioning": (
                "AD_POLLING",
                {"PROVISIONED"},
                {"REJECTED", "FAILED", "CANCELLED"},
            ),
            "app-factory-validation": (
                "APP_FACTORY_VALIDATION",
                {"READY_TO_PROVISION"},
                {"VALIDATION_ERROR"},
            ),
        }
        stage, success, failure = mappings[operation_key]
        waiting = operation_key == "ad-provisioning" and outcome == "PENDING"
        if not waiting and outcome not in success | failure:
            raise McpError(
                f"Unsupported business outcome for {operation_key}: {outcome}"
            )
        external = result.get("external_reference")
        if operation_key == "ad-provisioning" and (
            not isinstance(external, str) or not external
        ):
            raise McpError("An AD operation must include its external_reference")
        return BusinessProgress(
            stage,
            "WAITING" if waiting else "COMPLETED",
            reference,
            external,
            outcome not in failure,
        )

    def read_progress(
        self, journey_id: str, operation_key: str
    ) -> BusinessProgress | None:
        result = self.client.call(
            "get_journey_operation",
            {"journey_id": journey_id, "operation_key": operation_key},
        )
        return (
            None
            if result is None
            else self._progress(result, journey_id, operation_key)
        )

    def validate_apm(self, journey_id: str) -> BusinessProgress:
        result = self.client.call(
            "validate_apm",
            {
                "journey_id": journey_id,
                "idempotency_key": f"{journey_id}:apm-validation",
            },
        )
        return self._progress(result, journey_id, "apm-validation")

    def submit_ad(self, journey_id: str, idempotency_key: str) -> BusinessProgress:
        result = self.client.call(
            "submit_ad_provisioning",
            {
                "journey_id": journey_id,
                "idempotency_key": idempotency_key,
            },
        )
        return self._progress(result, journey_id, "ad-provisioning")

    def poll_ad(self, journey_id: str, request_id: str) -> BusinessProgress:
        result = self.client.call(
            "poll_ad_provisioning", {"journey_id": journey_id, "request_id": request_id}
        )
        progress = self._progress(result, journey_id, "ad-provisioning")
        if progress.external_reference != request_id:
            raise McpError("AD polling must retain the original external_reference")
        return progress

    def validate_app_factory(self, journey_id: str) -> BusinessProgress:
        result = self.client.call(
            "validate_app_factory",
            {
                "journey_id": journey_id,
                "idempotency_key": f"{journey_id}:app-factory-validation",
            },
        )
        return self._progress(result, journey_id, "app-factory-validation")


def build_mcp_business_gateway(agent: BatchAgent) -> McpBusinessGateway:
    return McpBusinessGateway(McpClient(BATCH_TOOLS[agent.value]))
