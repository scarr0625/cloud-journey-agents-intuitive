"""The Assistant's entire model-visible business tool allowlist."""

from google.adk.tools import ToolContext
from journey_mcp.client import McpClient, McpError, READ_TOOLS
from journey_mcp.identity import get_verified_identity, VerifiedIdentityRequired
from journey_mcp.user_auth import current_user_token


def _read(tool: str, arguments: dict, context: ToolContext) -> dict:
    try:
        get_verified_identity(context.state, expected_subject=context.user_id)
        token = current_user_token.get()
        if not token:
            raise VerifiedIdentityRequired()
        result = McpClient(READ_TOOLS).call(tool, arguments, user_token=token)
        if not isinstance(result, dict):
            raise McpError("Journey status must be a structured object")
        return result
    except (McpError, VerifiedIdentityRequired) as exc:
        return {"ok": False, "message": str(exc)}


def get_journey_status(journey_id: str, tool_context: ToolContext) -> dict:
    """Read authorized persisted business progress for a Journey."""
    return _read("get_journey_status", {"journey_id": journey_id}, tool_context)


def get_journey_status_by_apm_id(apm_id: str, tool_context: ToolContext) -> dict:
    """Read authorized persisted Journey progress using its APM ID."""
    return _read("get_journey_status_by_apm_id", {"apm_id": apm_id}, tool_context)


STATUS_TOOLS = [get_journey_status, get_journey_status_by_apm_id]
