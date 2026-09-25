"""Exercise each agent's durability binding through CLI and HTTP invocations."""

import importlib
import json
import sys

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from cloud_journey_agents.durability import mcp_gateway
from cloud_journey_agents.durability.database import (
    build_durable_engine,
    durable_session_factory,
    init_durable_db,
)
from cloud_journey_agents.durability.models import AgentExecution, OperationCheckpoint


@pytest.mark.parametrize(
    "package,agent_name,operation,tool,outcome,stage",
    [
        (
            "agent_apm_validation", "apm-validation-agent", "apm-validation",
            "validate_apm", "VALID", "APM_VALIDATION",
        ),
        (
            "agent_ad_provisioning", "ad-provisioning-agent", "ad-provisioning",
            "submit_ad_provisioning", "PENDING", "AD_POLLING",
        ),
        (
            "agent_app_factory", "app-factory-helper-agent", "app-factory-validation",
            "validate_app_factory", "READY_TO_PROVISION", "APP_FACTORY_VALIDATION",
        ),
    ],
)
def test_agent_cli_and_http_resume_durable_state(
    monkeypatch, tmp_path, capsys, package, agent_name, operation, tool, outcome, stage
):
    monkeypatch.setenv("DURABLE_DATABASE_URL", f"sqlite:///{tmp_path / 'durable.sqlite'}")
    # This setup stands in for centrally applied migrations, outside the agent.
    engine = build_durable_engine()
    init_durable_db(engine)
    engine.dispose()
    progress = {}
    writes = []
    expected_tools = {"get_journey_operation", tool}
    is_ad = operation == "ad-provisioning"
    if is_ad:
        expected_tools.add("poll_ad_provisioning")

    class Client:
        def __init__(self, allowed):
            assert allowed == expected_tools

        def call(self, name, arguments):
            assert name in expected_tools
            assert arguments["journey_id"] == "J-123"
            if name == "get_journey_operation":
                assert arguments["operation_key"] == operation
                return progress.copy() if progress else None
            writes.append(name)
            if name == "poll_ad_provisioning":
                assert arguments["request_id"] == "MA-123"
                progress.update(outcome="PROVISIONED", result_reference="result-polled")
            else:
                assert arguments["idempotency_key"] == f"J-123:{operation}"
                progress.update(
                    journey_id="J-123", operation_key=operation, outcome=outcome,
                    result_reference="result-123",
                    external_reference="MA-123" if is_ad else None,
                )
            return progress.copy()

    monkeypatch.setattr(mcp_gateway, "McpClient", Client)
    durability = importlib.import_module(f"{package}.durability")
    execute_workflow = durability.execute_workflow
    bound_workflows = []

    def execute(workflow, *args, **kwargs):
        bound_workflows.append(workflow)
        return execute_workflow(workflow, *args, **kwargs)

    monkeypatch.setattr(durability, "execute_workflow", execute)
    server = importlib.import_module(f"{package}.server")
    try:
        argv = ["--journey-id", "J-123", "--workflow-run-id", "cli-run"]
        if is_ad:
            argv += ["--mode", "submit"]
        server.main(argv)
        initial = json.loads(capsys.readouterr().out)
        assert initial["checkpoint_status"] == ("WAITING" if is_ad else "COMPLETED")
        assert initial["current_stage"] == stage

        # Each invocation creates a fresh runtime; the checkpoint survives it.
        with TestClient(server.app) as client:
            for run_id in ("http-run", "retry-run"):
                response = client.post("/v1/run", json={
                    "journey_id": "J-123", "workflow_run_id": run_id,
                    "mode": "poll" if is_ad else "resume",
                })
                assert response.status_code == 200
                result = response.json()
                assert result["checkpoint_id"] == initial["checkpoint_id"]
                assert result["execution_id"] != initial["execution_id"]
                assert result["checkpoint_status"] == "COMPLETED"
                assert result["successful"] is True
                assert result["external_reference"] == ("MA-123" if is_ad else None)

        assert bound_workflows == [durability.WORKFLOW] * 3
        assert writes == [tool] + (["poll_ad_provisioning"] if is_ad else [])
        with durable_session_factory(engine)() as session:
            checkpoint = session.scalars(select(OperationCheckpoint)).one()
            assert checkpoint.operation_key == operation
            executions = session.scalars(select(AgentExecution)).all()
            assert len(executions) == 3
            assert {execution.agent_name for execution in executions} == {agent_name}
            assert {execution.workflow_run_id for execution in executions} == {
                "cli-run", "http-run", "retry-run",
            }
    finally:
        engine.dispose()


def test_injected_business_gateway_works_without_batch_modules_or_mcp_adapter(
    monkeypatch, tmp_path
):
    # Model a main repo with different batch files and a different MCP interface.
    for name in (
        "cloud_journey_agents.batch", "cloud_journey_agents.batch_server",
        "cloud_journey_agents.durability.mcp_gateway",
    ):
        monkeypatch.setitem(sys.modules, name, None)
    from agent_apm_validation.durability import execute_job
    from cloud_journey_agents.durability.contracts import BusinessProgress

    monkeypatch.setenv("DURABLE_DATABASE_URL", f"sqlite:///{tmp_path / 'injected.sqlite'}")
    engine = build_durable_engine()
    try:
        init_durable_db(engine)
    finally:
        engine.dispose()

    class Business:
        progress = None
        validations = 0

        def read_progress(self, journey_id, operation_key):
            assert (journey_id, operation_key) == ("J-123", "apm-validation")
            return self.progress

        def validate_apm(self, journey_id):
            self.validations += 1
            self.progress = BusinessProgress("APM_VALIDATION", "COMPLETED", "result-123")
            return self.progress

    business = Business()
    first = execute_job("J-123", "run-1", business=business)
    resumed = execute_job("J-123", "run-2", business=business)
    assert first.checkpoint_id == resumed.checkpoint_id
    assert first.execution_id != resumed.execution_id
    assert resumed.checkpoint_status == "COMPLETED"
    assert business.validations == 1
