"""Local Workflows simulator: launch declared jobs in order and wait for each."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from uuid import uuid4


def run_job(
    agent: str, journey_id: str, workflow_run_id: str, mode: str = "resume"
) -> dict:
    # Each invocation runs in a fresh process. The workflow itself has no
    # checkpoint connection, and an individual job never launches another job.
    modules = {
        "apm-validation-agent": "agent_apm_validation.server",
        "ad-provisioning-agent": "agent_ad_provisioning.server",
        "app-factory-helper-agent": "agent_app_factory.server",
    }
    if os.getenv("JOURNEY_WORKFLOW_BACKEND", "mcp") == "local":
        command = [
            sys.executable,
            "-m",
            "journey_poc.batch_job",
            "--agent",
            agent,
            "--mode",
            mode,
        ]
    else:
        command = [sys.executable, "-m", modules[agent]]
        if agent == "ad-provisioning-agent":
            command.extend(["--mode", mode])
    command.extend(["--journey-id", journey_id, "--workflow-run-id", workflow_run_id])
    completed = subprocess.run(command, check=False, text=True, capture_output=True)
    if completed.returncode not in {0, 2}:
        completed.check_returncode()
    result = json.loads(completed.stdout)
    if completed.returncode == 2 and result.get("successful") is not False:
        completed.check_returncode()
    return result


def run_workflow(
    journey_id: str,
    workflow_run_id: str,
    *,
    max_polls: int = 10,
    poll_seconds: float = 1,
) -> dict:
    if max_polls < 1 or poll_seconds < 0:
        raise ValueError(
            "max_polls must be positive and poll_seconds cannot be negative"
        )
    validation = run_job("apm-validation-agent", journey_id, workflow_run_id)
    if not validation.get("successful", True):
        return {
            "workflow_run_id": workflow_run_id,
            "outcome": "BUSINESS_ERROR",
            "operation": validation,
        }
    submission = run_job("ad-provisioning-agent", journey_id, workflow_run_id, "submit")
    result = submission
    for _ in range(max_polls):
        if not result.get("successful", True):
            return {
                "workflow_run_id": workflow_run_id,
                "outcome": "BUSINESS_ERROR",
                "operation": result,
            }
        if result["checkpoint_status"] == "COMPLETED":
            break
        time.sleep(poll_seconds)
        result = run_job("ad-provisioning-agent", journey_id, workflow_run_id, "poll")
    if result["checkpoint_status"] != "COMPLETED":
        # Bounded workflow invocation; a later run continues with the same ID.
        return {
            "workflow_run_id": workflow_run_id,
            "outcome": "WAITING",
            "operation": result,
        }
    if not result.get("successful", True):
        return {
            "workflow_run_id": workflow_run_id,
            "outcome": "BUSINESS_ERROR",
            "operation": result,
        }
    result = run_job("app-factory-helper-agent", journey_id, workflow_run_id)
    return {
        "workflow_run_id": workflow_run_id,
        "outcome": "COMPLETED" if result.get("successful", True) else "BUSINESS_ERROR",
        "operation": result,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journey-id", required=True)
    parser.add_argument("--workflow-run-id", default=None)
    parser.add_argument("--max-polls", type=int, default=10)
    parser.add_argument("--poll-seconds", type=float, default=1)
    args = parser.parse_args()
    print(
        json.dumps(
            run_workflow(
                args.journey_id,
                args.workflow_run_id or str(uuid4()),
                max_polls=args.max_polls,
                poll_seconds=args.poll_seconds,
            )
        )
    )


if __name__ == "__main__":
    main()
