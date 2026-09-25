from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from journey_poc.cloud_journey.models import Base
from journey_poc.cloud_journey.tools import JourneyService


@pytest.fixture
def engine(tmp_path) -> Iterator[Engine]:
    database_path = tmp_path / "journeys.sqlite"
    target = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(target, "connect")
    def configure_sqlite(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    Base.metadata.create_all(target)
    yield target
    target.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


@pytest.fixture
def service(session_factory: sessionmaker[Session]) -> JourneyService:
    return JourneyService(session_factory)


@pytest.fixture
def persistence_service():
    """Run the real Schwab-side handlers against isolated server-owned test DBs."""
    from google.adk.sessions.schemas.v1 import Base as SessionBase
    from sqlalchemy import MetaData
    from sqlalchemy.pool import StaticPool
    from journey_poc.durable_models import DurableBase
    from schwab_mcp_persistence.schema import protocol_tables
    from schwab_mcp_persistence.service import PersistenceService

    engines = []
    for source, sessions in ((DurableBase.metadata, False), (SessionBase.metadata, True)):
        target = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        with target.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        metadata = MetaData()
        for table in source.sorted_tables:
            table.to_metadata(metadata)
        protocol_tables(metadata, sessions=sessions)
        metadata.create_all(target)
        engines.append(target)
    instance = PersistenceService(
        durable_engine=engines[0], session_engine=engines[1],
        batch_workloads={name: name for name in (
            "apm-validation-agent", "ad-provisioning-agent", "app-factory-helper-agent",
        )},
        chat_workloads={name: name for name in ("journey_assistant", "journey_orchestrator", "test_app")},
        authorize_journey=lambda principal, journey_id: journey_id.startswith("J-"),
    )
    yield instance
    for target in engines:
        target.dispose()


@pytest.fixture
def mcp_client_factory(persistence_service):
    from cloud_journey_agents.mcp import McpError
    from schwab_mcp_persistence.service import ToolError

    def factory(principal):
        class Client:
            def __init__(self, allowed):
                self.allowed = allowed
                self.calls = []

            def call(self, name, arguments, *, user_token=""):
                assert name in self.allowed
                self.calls.append((name, arguments, user_token))
                verified = principal(user_token) if callable(principal) else principal
                try:
                    return persistence_service.execute(name, arguments, verified)
                except ToolError as exc:
                    raise McpError(str(exc), code=exc.code) from exc
        return Client
    return factory
