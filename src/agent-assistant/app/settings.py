"""Assistant configuration, read through the shared environment helpers."""

from cloud_journey_agents.config import load_config, setting

load_config()

APP_NAME = "journey_assistant"
APP_TITLE = "Journey Assistant"
MODEL = setting("ASSISTANT_MODEL", "gemini-2.5-flash")
