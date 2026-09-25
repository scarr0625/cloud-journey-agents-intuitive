"""Persistence client/server contract tests, including uncertain transport outcomes."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from google.adk.events import Event, EventActions
import pytest
from sqlalchemy import event as sqlalchemy_event, func, select, update

from cloud_journey_agents.durability.checkpoints import CheckpointError, CheckpointStore, OperationBusy
from cloud_journey_agents.durability.models import BatchAgent, CheckpointStage, CheckpointStatus
from cloud_journey_agents.guardrails import DURABLE_TOOLS, SESSION_TOOLS
from cloud_journey_agents.identity import current_user_token
from cloud_journey_agents.mcp import McpError
from cloud_journey_agents.sessions.persistence import PersistentSessionService
from schwab_mcp_persistence.service import Principal, ToolError

APM = Principal("apm-validation-agent")
USER = Principal("test_app", "user-1", "user@example.com")
SCOPE = {"app_name": "test_app", "user_id": "user-1", "session_id": "session-123"}


def claim_args(**overrides):
    return {"journey_id": "J-123", "agent_name": APM.workload, "workflow_run_id": "run-1",
            "mutation_id": str(uuid4()), **overrides}


def test_server_claim_replay_and_concurrent_duplicate_are_one_execution(persistence_service):
    args = claim_args()
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: persistence_service.execute("claim_durable_operation", args, APM), range(2)))
    assert results[0] == results[1]
    with persistence_service.durable_engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(persistence_service.durable.tables["agent_execution"])) == 1
    with pytest.raises(ToolError) as conflict:
        persistence_service.execute("claim_durable_operation", {**args, "workflow_run_id": "different"}, APM)
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"


def test_server_authorizes_workload_and_journey_before_replaying(persistence_service):
    args = claim_args()
    persistence_service.execute("claim_durable_operation", args, APM)
    with pytest.raises(ToolError) as denied:
        persistence_service.execute("claim_durable_operation", args, Principal("journey_assistant"))
    assert denied.value.code == "FORBIDDEN"
    with pytest.raises(ToolError) as denied:
        persistence_service.execute("claim_durable_operation", claim_args(journey_id="other"), APM)
    assert denied.value.code == "FORBIDDEN"


def test_stale_worker_cannot_save_after_reclaim(persistence_service, mcp_client_factory):
    store = CheckpointStore(BatchAgent.APM_VALIDATION, client=mcp_client_factory(APM)(DURABLE_TOOLS))
    old = store.claim("J-123", BatchAgent.APM_VALIDATION, "run-1")
    with pytest.raises(OperationBusy):
        store.claim("J-123", BatchAgent.APM_VALIDATION, "run-2")
    table = persistence_service.durable.tables["operation_checkpoint"]
    with persistence_service.durable_engine.begin() as connection:
        connection.execute(update(table).values(lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=10)))
    current = store.claim("J-123", BatchAgent.APM_VALIDATION, "run-2")
    assert current.latest_execution_id != old.latest_execution_id
    with pytest.raises(OperationBusy):
        store.save(old, stage=CheckpointStage.APM_VALIDATION, status=CheckpointStatus.RUNNING)


def test_lost_claim_reply_retries_the_same_mutation(mcp_client_factory):
    base = mcp_client_factory(APM)(DURABLE_TOOLS)
    seen = []
    class LostReply:
        def call(self, name, args, **kwargs):
            seen.append(args["mutation_id"])
            result = base.call(name, args, **kwargs)
            if len(seen) == 1:
                raise McpError("reply lost after commit", retryable=True)
            return result
    result = CheckpointStore(BatchAgent.APM_VALIDATION, client=LostReply()).claim("J-123", BatchAgent.APM_VALIDATION, "run")
    assert len(seen) == 2 and seen[0] == seen[1]
    assert result.version == 1


def test_ambiguous_save_does_not_attempt_a_second_failure_write(monkeypatch, mcp_client_factory):
    from agent_apm_validation.job import WORKFLOW
    from cloud_journey_agents.durability.runtime import BatchRuntime
    client = mcp_client_factory(APM)(DURABLE_TOOLS)
    original = client.call
    mutations = []
    def call(name, args, **kwargs):
        mutations.append(name)
        result = original(name, args, **kwargs)
        if name == "save_durable_checkpoint":
            raise McpError("transport remains unavailable", retryable=True)
        return result
    monkeypatch.setattr(client, "call", call)
    class Business:
        def read_progress(self, *args):
            return None
        def validate_apm(self, *args):
            pytest.fail("Business work must not run without confirmed persistence")
    with pytest.raises(CheckpointError):
        BatchRuntime(CheckpointStore(BatchAgent.APM_VALIDATION, client=client), Business()).run(WORKFLOW, "J-123", "run")
    assert mutations == ["claim_durable_operation", "save_durable_checkpoint", "save_durable_checkpoint"]


def test_session_restart_temp_state_filters_and_stale_revision(mcp_client_factory):
    client = mcp_client_factory(USER)(SESSION_TOOLS)
    service = PersistentSessionService(app_name="test_app", client=client)
    token = current_user_token.set("verified-user-token")
    try:
        session = service.create_session_sync(**SCOPE, state={"journey_id": "J-123", "temp:discard": 1})
        stale = service.get_session_sync(**SCOPE)
        event = Event(author="user", actions=EventActions(state_delta={
            "topic": "status", "temp:local": True, "user:preference": "short", "app:setting": "shared",
        }))
        service.append_event_sync(session, event)
        assert session.state["temp:local"] is True
        restarted = PersistentSessionService(app_name="test_app", client=mcp_client_factory(USER)(SESSION_TOOLS))
        saved = restarted.get_session_sync(**SCOPE)
        assert saved.state["topic"] == "status"
        assert saved.state["user:preference"] == "short" and saved.state["app:setting"] == "shared"
        assert not any(key.startswith("temp:") for key in saved.state)
        assert len(saved.events) == 1
        with pytest.raises(McpError) as error:
            service.append_event_sync(stale, Event(author="user"))
        assert error.value.code == "STALE_SESSION"
        assert len(service.list_sessions_sync(app_name="test_app", user_id="user-1").sessions) == 1
        assert all(call[2] == "verified-user-token" for call in client.calls)
        assert "verified-user-token" not in str([call[1] for call in client.calls])
        service.delete_session_sync(**SCOPE)
        assert service.get_session_sync(**SCOPE) is None
        with pytest.raises(McpError) as retired:
            service.create_session_sync(**SCOPE)
        assert retired.value.code == "ALREADY_EXISTS"
    finally:
        current_user_token.reset(token)


def test_session_lost_append_reply_is_not_a_duplicate_event(mcp_client_factory):
    base = mcp_client_factory(USER)(SESSION_TOOLS)
    lost = []
    class LostReply:
        def call(self, name, args, **kwargs):
            result = base.call(name, args, **kwargs)
            if name == "append_agent_session_event":
                lost.append(args["mutation_id"])
                if len(lost) == 1:
                    raise McpError("reply lost", retryable=True)
            return result
    service = PersistentSessionService(app_name="test_app", client=LostReply())
    token = current_user_token.set("verified")
    try:
        session = service.create_session_sync(**SCOPE)
        service.append_event_sync(session, Event(author="user"))
        assert len(service.get_session_sync(**SCOPE).events) == 1
        assert len(lost) == 2 and lost[0] == lost[1]
    finally:
        current_user_token.reset(token)


def test_session_identity_and_claim_spoofing_are_rejected(mcp_client_factory):
    service = PersistentSessionService(app_name="test_app", client=mcp_client_factory(USER)(SESSION_TOOLS))
    with pytest.raises(McpError) as missing:
        service.create_session_sync(**SCOPE)
    assert missing.value.code == "UNAUTHENTICATED"
    token = current_user_token.set("verified")
    try:
        for override in ({"user_id": "other"}, {"state": {"auth:google_sub": "spoofed"}}):
            with pytest.raises(McpError) as denied:
                service.create_session_sync(**{**SCOPE, **override})
            assert denied.value.code == "FORBIDDEN"
        with pytest.raises(ValueError):
            service.get_session_sync(**{**SCOPE, "app_name": "other"})
    finally:
        current_user_token.reset(token)


@pytest.mark.parametrize("kind", ["durable", "session"])
def test_receipt_failure_rolls_back_the_entire_mutation(persistence_service, kind):
    server = persistence_service
    if kind == "durable":
        engine, metadata = server.durable_engine, server.durable
        tool, args, principal = "claim_durable_operation", claim_args(), APM
        checked_tables = ("agent_execution", "operation_checkpoint", "checkpoint_event", "mcp_mutation_receipt")
    else:
        engine, metadata = server.session_engine, server.sessions
        server.execute("create_agent_session", {**SCOPE, "mutation_id": str(uuid4())}, USER)
        tool, principal = "append_agent_session_event", USER
        args = {**SCOPE, "mutation_id": str(uuid4()), "expected_version": 1, "event": Event(
            author="user", actions=EventActions(state_delta={
                "topic": "rollback", "user:setting": "rollback", "app:setting": "rollback",
            }),
        ).model_dump(mode="json", by_alias=False)}
        checked_tables = ("events", "mcp_session_event", "user_states", "app_states")

    def fail_receipt(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO mcp_mutation_receipt"):
            raise RuntimeError("Simulated storage failure before commit")

    sqlalchemy_event.listen(engine, "before_cursor_execute", fail_receipt)
    try:
        with pytest.raises(RuntimeError, match="Simulated storage failure"):
            server.execute(tool, args, principal)
    finally:
        sqlalchemy_event.remove(engine, "before_cursor_execute", fail_receipt)
    with engine.connect() as connection:
        for name in checked_tables:
            assert connection.scalar(select(func.count()).select_from(metadata.tables[name])) == 0
    if kind == "session":
        saved = server.execute("get_agent_session", SCOPE, USER)
        assert saved["version"] == 1
        assert "topic" not in saved["session"]["state"]
    # With the fault cleared, the identical request can commit once.
    result = server.execute(tool, args, principal)
    assert server.execute(tool, args, principal) == result


@pytest.mark.parametrize("field,value", [
    ("journey_id", "J-other"), ("agent_name", "ad-provisioning-agent"),
    ("operation_key", "ad-provisioning"), ("workflow_run_id", "other"),
    ("version", True), ("lease_expires_at", None),
])
def test_checkpoint_client_rejects_untrusted_claim_acknowledgments(mcp_client_factory, field, value):
    base = mcp_client_factory(APM)(DURABLE_TOOLS)
    class InvalidReply:
        def call(self, name, args, **kwargs):
            response = base.call(name, args, **kwargs)
            response["checkpoint"][field] = value
            return response
    with pytest.raises(CheckpointError):
        CheckpointStore(BatchAgent.APM_VALIDATION, client=InvalidReply()).claim("J-123", BatchAgent.APM_VALIDATION, "run")
