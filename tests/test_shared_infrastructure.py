import json
import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    Table,
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy.exc import OperationalError

from agent_ad_provisioning.job import WORKFLOW as AD_WORKFLOW
from agent_apm_validation.job import WORKFLOW as APM_WORKFLOW
from cloud_journey_agents import identity
from cloud_journey_agents.durability import server as batch_server
from cloud_journey_agents.durability.contracts import JobResult
from cloud_journey_agents.durability.checkpoints import OperationBusy
from cloud_journey_agents.guardrails import GuardrailError, require_read_only_statement
from cloud_journey_agents.journey_db import read_rows
from cloud_journey_agents.logs import JsonFormatter
from cloud_journey_agents.mcp import McpClient, McpError, READ_TOOLS


def test_service_tokens_are_cached_per_audience_and_refreshed(monkeypatch):
    monkeypatch.setattr(identity, "_service_tokens", {})
    now = [1000]
    calls = []
    monkeypatch.setattr(identity.time, "time", lambda: now[0])
    monkeypatch.setattr(identity.jwt, "decode", lambda *a, **kw: {"exp": now[0] + 300})

    def fetch(request, audience):
        calls.append(audience)
        return f"token-{len(calls)}"

    monkeypatch.setattr(identity.id_token, "fetch_id_token", fetch)
    assert identity.service_identity_token("assistant") == "token-1"
    assert identity.service_identity_token("assistant") == "token-1"
    assert identity.service_identity_token("mcp") == "token-2"
    now[0] += 241
    assert identity.service_identity_token("assistant") == "token-3"
    assert calls == ["assistant", "mcp", "assistant"]


@pytest.mark.parametrize(
    "arguments", [{"journey_id": ["J-1", "J-2"]}, {"journey_id": "J-1", "write": True}]
)
def test_chat_rejects_bulk_or_write_arguments_before_network(monkeypatch, arguments):
    client = McpClient(READ_TOOLS, url="https://example.invalid/mcp")
    monkeypatch.setattr(
        client, "_headers", lambda _: pytest.fail("unexpected network/auth")
    )
    with pytest.raises(McpError, match="requires one"):
        client.call("get_journey_status", arguments)


@pytest.mark.parametrize("deployed_marker", ["K_SERVICE", "CLOUD_RUN_JOB"])
def test_local_business_reads_are_disabled_on_cloud_run(monkeypatch, deployed_marker):
    monkeypatch.setenv("ALLOW_LOCAL_DB_READS", "true")
    monkeypatch.setenv(deployed_marker, "deployed-agent")
    with pytest.raises(GuardrailError, match="disabled"):
        read_rows(None, select(1))


def test_local_business_reads_require_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("ALLOW_LOCAL_DB_READS", raising=False)
    with pytest.raises(GuardrailError, match="disabled"):
        read_rows(None, select(1))


def test_sql_guard_rejects_raw_sql_and_write_ctes():
    records = Table("records", MetaData(), Column("id", Integer))
    for statement in (
        text("SELECT 1; DELETE FROM records"),
        records.delete(),
        select(records).add_cte(records.delete().cte("deleted")),
    ):
        with pytest.raises(GuardrailError, match="read-only SELECT"):
            require_read_only_statement(statement)


def test_local_database_enforces_read_only_and_restores_connection(monkeypatch):
    monkeypatch.setenv("ALLOW_LOCAL_DB_READS", "true")
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("CLOUD_RUN_JOB", raising=False)
    engine = create_engine("sqlite://")
    records = Table("records", MetaData(), Column("id", Integer))
    records.metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            connection.execute(records.insert().values(id=1))
        assert read_rows(engine, select(records)) == [{"id": 1}]
        with engine.connect() as connection:
            dbapi = connection.connection.driver_connection
            dbapi.create_function(
                "mutate", 0, lambda: dbapi.execute("INSERT INTO records VALUES (2)")
            )
        with pytest.raises(OperationalError):
            read_rows(engine, select(func.mutate()))
        assert read_rows(engine, select(records)) == [{"id": 1}]
        with engine.begin() as connection:
            assert connection.exec_driver_sql("PRAGMA query_only").scalar() == 0
            connection.execute(records.insert().values(id=3))
    finally:
        engine.dispose()


@pytest.mark.parametrize("status,successful", [("WAITING", True), ("COMPLETED", False)])
def test_batch_http_preserves_business_outcome_and_fixed_agent(
    monkeypatch, status, successful
):
    calls = []

    def execute(workflow, journey_id, workflow_run_id, *, mode):
        calls.append((workflow, journey_id, workflow_run_id, mode))
        return JobResult(
            "execution",
            "checkpoint",
            status,
            "AD_POLLING",
            "MA-123",
            "result",
            successful,
        )

    monkeypatch.setattr(batch_server, "execute_job", execute)
    with TestClient(batch_server.create_batch_app(AD_WORKFLOW)) as client:
        assert client.get("/health").json() == {"status": "ok"}
        response = client.post(
            "/v1/run",
            json={"journey_id": "J-123", "workflow_run_id": "run", "mode": "poll"},
        )
        assert response.status_code == 200
        assert response.json()["checkpoint_status"] == status
        assert response.json()["successful"] is successful
        assert (
            client.post(
                "/v1/run",
                json={
                    "journey_id": "J-123",
                    "workflow_run_id": "run",
                    "agent": "other",
                },
            ).status_code
            == 422
        )
    assert calls == [(AD_WORKFLOW, "J-123", "run", "poll")]


def test_batch_http_rejects_invalid_mode_and_reports_busy_operation(monkeypatch):
    def busy(*args, **kwargs):
        raise OperationBusy("The operation is already running")

    monkeypatch.setattr(batch_server, "execute_job", busy)
    with TestClient(batch_server.create_batch_app(APM_WORKFLOW)) as client:
        payload = {"journey_id": "J-123", "workflow_run_id": "run"}
        assert (
            client.post("/v1/run", json={**payload, "mode": "poll"}).status_code == 422
        )
        assert client.post("/v1/run", json=payload).status_code == 409


def test_batch_cli_keeps_negative_business_results_machine_readable(
    monkeypatch, capsys
):
    monkeypatch.setattr(
        batch_server,
        "execute_job",
        lambda *args, **kwargs: JobResult(
            "execution",
            "checkpoint",
            "COMPLETED",
            "APM_VALIDATION",
            None,
            "result",
            False,
        ),
    )
    with pytest.raises(SystemExit) as stopped:
        batch_server.run_job(
            APM_WORKFLOW, ["--journey-id", "J-123", "--workflow-run-id", "run"]
        )
    assert stopped.value.code == 2
    assert json.loads(capsys.readouterr().out)["successful"] is False


def test_structured_logs_keep_machine_readable_fields():
    record = logging.LogRecord(
        "cloud_journey_agents.batch", logging.INFO, __file__, 1, "Finished", (), None
    )
    record.journey_fields = {"checkpoint_status": "WAITING"}
    payload = json.loads(JsonFormatter().format(record))
    assert payload["message"] == "Finished"
    assert payload["severity"] == "INFO"
    assert payload["checkpoint_status"] == "WAITING"
