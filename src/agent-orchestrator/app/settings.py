"""Orchestrator configuration, read through the shared environment helpers."""

from cloud_journey_agents.config import load_config, setting

load_config()

APP_NAME = "journey_orchestrator"
APP_TITLE = "Journey Orchestrator"
MODEL = setting("ORCHESTRATOR_MODEL", "gemini-2.5-flash")


def assistant_url() -> str:
    return setting("ASSISTANT_URL").rstrip("/")


def assistant_audience() -> str:
    return setting("ASSISTANT_CLOUD_RUN_AUDIENCE")
