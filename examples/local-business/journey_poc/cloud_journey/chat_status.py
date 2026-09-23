"""Read-only business status tools for the Chat Assistant.

The local business gateway stands in for private MCP -> Data API in this PoC.
Only server-verified identity is passed to it; no checkpoint modules are used.
"""

from __future__ import annotations

from google.adk.tools import ToolContext

from .apm import normalize_apm_id
from .business_operations import LocalBusinessGateway
from .database import SessionLocal
from .identity import get_verified_identity
from .state_machine import StateMachine
from .tools import _tool_call


def get_journey_progress(journey_id: str, tool_context: ToolContext) -> dict:
    """Read the authorized Journey's stage, external waits and validation result."""
    def read():
        identity = get_verified_identity(tool_context.state, expected_subject=tool_context.user_id)
        return {"ok": True, **LocalBusinessGateway(SessionLocal).authorized_status(journey_id, identity.subject)}
    return _tool_call(read)


def get_journey_progress_by_apm_id(apm_id: str, tool_context: ToolContext) -> dict:
    """Find authorized business progress by canonical APM ID without starting work."""
    def read():
        identity = get_verified_identity(tool_context.state, expected_subject=tool_context.user_id)
        machine = StateMachine(SessionLocal)
        journey = machine.get_group_journey_by_apm_id(
            normalize_apm_id(apm_id), machine.get_access_groups_for_user(identity.subject),
        )
        return {"ok": True, **LocalBusinessGateway(SessionLocal).authorized_status(journey.id, identity.subject)}
    return _tool_call(read)


CHAT_ASSISTANT_TOOLS = [get_journey_progress, get_journey_progress_by_apm_id]
CHAT_ASSISTANT_INSTRUCTION = """
You explain authorized Journey business progress. Always use a status tool for
Journey facts. Explain the current stage, pending MyAccess request, validation
errors, or readiness using the persisted result. You cannot create, change, or
resume a Journey or a batch checkpoint. A COMPLETED operation is not a completed
Journey. READY_TO_PROVISION means validation succeeded; provisioning happens
outside this batch flow. External integrations in this PoC are simulated.
Use the Journey/APM ID established in this conversation or supplied by the user.
Report tool errors without inventing progress or inferring access from chat text.
"""
