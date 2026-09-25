"""Bind the Orchestrator to a shared runtime for its own persisted conversation.

get_runtime() lazily creates and caches ConversationRuntime with this
agent's application name. That runtime stores the conversation and the
downstream Assistant session reference under the Orchestrator's namespace.

MCP persistence and runner lifecycle belong to the shared sessions
package. This file provides application wiring, and health checks can
run before the conversation runtime is constructed.
"""

from functools import lru_cache

from cloud_journey_agents.sessions.conversation import ConversationRuntime

from .agent import root_agent
from .settings import APP_NAME


@lru_cache
def get_runtime() -> ConversationRuntime:
    return ConversationRuntime(root_agent, APP_NAME)
