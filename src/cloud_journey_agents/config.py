"""Environment configuration shared by agent entry points and infrastructure."""

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
