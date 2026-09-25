"""Set the Assistant's application identity and configured model.

Shared configuration loads local .env defaults while preserving deployment
values. APP_NAME identifies the ADK agent and its session namespace;
APP_TITLE labels the HTTP app, and ASSISTANT_MODEL selects the model.

These values are read when the module is imported. Model selection belongs
to this agent, while database and identity settings remain with the shared
components that consume them.
"""

from cloud_journey_agents.config import load_config, setting

load_config()

APP_NAME = "journey_assistant"
APP_TITLE = "Journey Assistant"
MODEL = setting("ASSISTANT_MODEL", "gemini-2.5-flash")
