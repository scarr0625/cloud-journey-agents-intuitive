"""Verified request context and the downstream conversation reference."""

from google.adk.tools import ToolContext

from cloud_journey_agents.identity import current_user_token, get_verified_identity

ASSISTANT_SESSION_ID_KEY = "assistant_session_id"


def assistant_context(tool_context: ToolContext) -> tuple[str, str | None]:
    """Read request-only delegation and the persisted Assistant session ID."""
    get_verified_identity(tool_context.state, expected_subject=tool_context.user_id)
    return current_user_token.get(), tool_context.state.get(ASSISTANT_SESSION_ID_KEY)
