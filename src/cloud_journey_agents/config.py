"""Common environment access for agent entry points and infrastructure.

Entry points call load_config() to load local .env defaults without
replacing values supplied by the deployment. Consumers then read settings
or require a nonempty value when their operation needs it.

The legacy local-read flag is retained for the historical SQL simulator only;
the deployed journey_db helper always rejects direct access. Importing this
module does not load a .env file or validate every workload's configuration.
"""

import os

from dotenv import load_dotenv


def load_config() -> None:
    """Load local defaults without replacing deployment-injected settings."""
    load_dotenv()


def setting(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def required_setting(name: str) -> str:
    value = setting(name)
    if not value.strip():
        raise ValueError(f"{name} is required")
    return value


def local_database_reads_enabled() -> bool:
    return (
        setting("ALLOW_LOCAL_DB_READS").lower() == "true"
        and not setting("K_SERVICE")
        and not setting("CLOUD_RUN_JOB")
    )
