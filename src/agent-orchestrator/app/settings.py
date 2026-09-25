"""Configure the Orchestrator's model, application identity, and Assistant route.

Local .env defaults are loaded through shared configuration. The model
and application constants are selected at import time; helper functions
read the Assistant URL and optional Cloud Run audience when routing occurs.

The audience is used for workload authentication, while the current
user's token is forwarded separately by tools.py. Keeping these settings
here allows the same routing code to serve local and deployed environments.
"""

from cloud_journey_agents.config import load_config, setting

load_config()

APP_NAME = "journey_orchestrator"
APP_TITLE = "Journey Orchestrator"
MODEL = setting("ORCHESTRATOR_MODEL", "gemini-2.5-flash")


def assistant_url() -> str:
    return setting("ASSISTANT_URL").rstrip("/")


def assistant_audience() -> str:
    return setting("ASSISTANT_CLOUD_RUN_AUDIENCE")
