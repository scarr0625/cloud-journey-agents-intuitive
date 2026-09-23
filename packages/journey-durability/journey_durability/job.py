"""Shared CLI wiring; each deployed entry point fixes its own agent identity."""

import argparse
from dataclasses import asdict
import json

from dotenv import load_dotenv
from journey_mcp.client import BATCH_TOOLS, McpClient

from .checkpoints import CheckpointStore
from .database import build_durable_engine, durable_session_factory
from .mcp_gateway import McpBusinessGateway
from .models import BatchAgent
from .runtime import BatchRuntime


def run_job(agent: BatchAgent, argv: list[str] | None = None) -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=f"Run one {agent.value} invocation")
    parser.add_argument("--journey-id", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    if agent == BatchAgent.AD_PROVISIONING:
        parser.add_argument(
            "--mode", choices=["submit", "poll", "resume"], default="resume"
        )
    args = parser.parse_args(argv)
    engine = build_durable_engine()
    try:
        # Migrations are applied centrally; a deployed job never creates tables.
        runtime = BatchRuntime(
            CheckpointStore(durable_session_factory(engine)),
            McpBusinessGateway(McpClient(BATCH_TOOLS[agent.value])),
        )
        result = runtime.run(
            agent,
            args.journey_id,
            args.workflow_run_id,
            mode=getattr(args, "mode", "resume"),
        )
        print(json.dumps(asdict(result)))
        if not result.successful:
            raise SystemExit(2)
    finally:
        engine.dispose()
