import subprocess

import pytest

from workflows import local_workflow as batch_workflow


def test_job_negative_business_exit_is_returned_to_workflow(monkeypatch):
    monkeypatch.setenv("JOURNEY_WORKFLOW_BACKEND", "mcp")
    monkeypatch.setattr(
        batch_workflow.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            2,
            stdout='{"checkpoint_status": "COMPLETED", "successful": false}',
            stderr="",
        ),
    )
    assert (
        batch_workflow.run_job("apm-validation-agent", "J-TEST", "run")["successful"]
        is False
    )


def test_job_runtime_failure_is_not_a_business_result(monkeypatch):
    monkeypatch.setenv("JOURNEY_WORKFLOW_BACKEND", "mcp")
    monkeypatch.setattr(
        batch_workflow.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 1, stdout="", stderr="connection failed"
        ),
    )
    with pytest.raises(subprocess.CalledProcessError):
        batch_workflow.run_job("apm-validation-agent", "J-TEST", "run")


def test_workflow_runs_declared_jobs_in_order_and_waits_for_ad(monkeypatch):
    calls = []

    def run_job(agent, journey_id, workflow_run_id, mode="resume"):
        calls.append((agent, mode, journey_id, workflow_run_id))
        pending = agent == "ad-provisioning-agent" and len(calls) < 4
        return {"checkpoint_status": "WAITING" if pending else "COMPLETED"}

    monkeypatch.setattr(batch_workflow, "run_job", run_job)
    result = batch_workflow.run_workflow("J-TEST", "workflow-run", poll_seconds=0)
    assert [(agent, mode) for agent, mode, *_ in calls] == [
        ("apm-validation-agent", "resume"),
        ("ad-provisioning-agent", "submit"),
        ("ad-provisioning-agent", "poll"),
        ("ad-provisioning-agent", "poll"),
        ("app-factory-helper-agent", "resume"),
    ]
    assert all(
        (journey, workflow) == ("J-TEST", "workflow-run")
        for _, _, journey, workflow in calls
    )
    assert result["outcome"] == "COMPLETED"


def test_pending_workflow_does_not_run_app_factory(monkeypatch):
    calls = []

    def run_job(agent, *_args):
        calls.append(agent)
        return {
            "checkpoint_status": (
                "COMPLETED" if agent == "apm-validation-agent" else "WAITING"
            )
        }

    monkeypatch.setattr(batch_workflow, "run_job", run_job)
    result = batch_workflow.run_workflow("J-TEST", "run", max_polls=2, poll_seconds=0)
    assert result["outcome"] == "WAITING"
    assert "app-factory-helper-agent" not in calls


def test_completed_invalid_validation_stops_workflow(monkeypatch):
    calls = []

    def run_job(agent, *_args):
        calls.append(agent)
        return {"checkpoint_status": "COMPLETED", "successful": False}

    monkeypatch.setattr(batch_workflow, "run_job", run_job)
    result = batch_workflow.run_workflow("J-TEST", "run", poll_seconds=0)
    assert result["outcome"] == "BUSINESS_ERROR"
    assert calls == ["apm-validation-agent"]
