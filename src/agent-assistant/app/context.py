"""Bind an Assistant tool call to the user verified for the current request.

Conversation state carries verified identity claims, but a stored session
alone is insufficient for a new downstream request. The tool's user ID
must match those claims and an ephemeral delegated token must be present.

Tools use this helper before calling MCP. It returns the request token
from shared identity context without adding that credential to session
state; missing or mismatched identity stops the lookup.
"""

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
