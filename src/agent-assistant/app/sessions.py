"""Bind the Assistant's agent definition to the shared conversation runtime.

get_runtime() constructs the runner and MCP session client on first use
and caches the result for this process. Importing the agent's HTTP module
therefore does not require a live MCP connection for its health route.

The shared sessions package owns persistence and cleanup. This file only
supplies the Assistant's root agent and application name so its stored
conversation namespace remains distinct from the Orchestrator's.
"""

from functools import lru_cache

from cloud_journey_agents.sessions.conversation import ConversationRuntime

from .agent import root_agent
from .settings import APP_NAME


@lru_cache
def get_runtime() -> ConversationRuntime:
    return ConversationRuntime(root_agent, APP_NAME)
