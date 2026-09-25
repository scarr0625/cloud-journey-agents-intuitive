"""The Assistant's runtime; session storage is implemented by the shared package."""

from functools import lru_cache

from cloud_journey_agents.sessions.conversation import ConversationRuntime

from .agent import root_agent
from .settings import APP_NAME


@lru_cache
def get_runtime() -> ConversationRuntime:
    return ConversationRuntime(root_agent, APP_NAME)
