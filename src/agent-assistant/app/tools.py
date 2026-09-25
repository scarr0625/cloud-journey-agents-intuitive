"""Expose the Assistant's two model-visible Journey status lookups.

A tool first obtains verified request identity, then calls private MCP
with the shared read-only allowlist and the delegated user token. Results
must be structured objects; identity or MCP failures become explicit tool
error responses for the model to report.

STATUS_TOOLS is the complete tool list installed by agent.py. It includes
lookups by Journey ID and APM ID, with no business writes or direct SQL
fallback. Authoritative data and authorization remain with the MCP service.
"""

from google.adk.tools import ToolContext
from cloud_journey_agents.mcp import McpClient, McpError, READ_TOOLS
from cloud_journey_agents.identity import VerifiedIdentityRequired

from .context import verified_user_token


def _read(tool: str, arguments: dict, context: ToolContext) -> dict:
    try:
        token = verified_user_token(context)
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
