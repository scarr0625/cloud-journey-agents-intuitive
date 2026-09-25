"""Build database resources for durable checkpoints from DURABLE_* settings.

Jobs can use an explicit database URL or a Cloud SQL connector with
password or IAM authentication. This module creates SQLAlchemy engines
and session factories on demand; importing it opens no connection.

Only the Durable State DB belongs here. There is no fallback to the
business or conversation database when configuration is missing. The
runtime disposes its engine after each invocation, and connector shutdown
is registered for process exit. init_durable_db() is for local setup;
deployed tables are created by the centrally applied migration.
"""

import atexit

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from .models import DurableBase
from ..config import required_setting, setting


def build_durable_engine() -> Engine:
    url = setting("DURABLE_DATABASE_URL")
    instance = setting("DURABLE_CLOUD_SQL_INSTANCE")
    if url:
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url.removeprefix("postgres://")
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
        kwargs = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        return create_engine(url, **kwargs)
    if not instance:
        raise ValueError(
            "Set DURABLE_DATABASE_URL or DURABLE_CLOUD_SQL_INSTANCE for this batch job"
        )

    from google.cloud.sql.connector import Connector, IPTypes

    user = required_setting("DURABLE_DB_USER")
    iam_auth = setting("DURABLE_DB_IAM_AUTH", "false").lower() == "true"
    password = None if iam_auth else required_setting("DURABLE_DB_PASSWORD")
    ip_type = IPTypes[setting("DURABLE_DB_IP_TYPE", "PRIVATE").upper()]
    connector = Connector()
    atexit.register(connector.close)

    def connect():
        kwargs = {"user": user, "db": setting("DURABLE_DB_NAME", "durable-state-db")}
        if not iam_auth:
            kwargs["password"] = password
        return connector.connect(
            instance, "pg8000", ip_type=ip_type, enable_iam_auth=iam_auth, **kwargs
        )

    return create_engine("postgresql+pg8000://", creator=connect, pool_pre_ping=True)


def durable_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


def init_durable_db(engine: Engine) -> None:
    """Local development only; deployed jobs use centrally applied migrations."""
    DurableBase.metadata.create_all(engine)
