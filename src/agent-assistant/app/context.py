"""Require verified, request-scoped identity before delegating MCP reads."""

from google.adk.tools import ToolContext

from cloud_journey_agents.identity import (
    VerifiedIdentityRequired,
    current_user_token,
    get_verified_identity,
)


def verified_user_token(tool_context: ToolContext) -> str:
    get_verified_identity(tool_context.state, expected_subject=tool_context.user_id)
    token = current_user_token.get()
    if not token:
        raise VerifiedIdentityRequired()
    return token
