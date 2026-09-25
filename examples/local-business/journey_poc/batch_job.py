"""Cloud Run Job / local entry point for one declared agent invocation."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os

from cloud_journey_agents.durability.runtime import BatchRuntime
from agent_apm_validation.job import WORKFLOW as APM_WORKFLOW
from agent_ad_provisioning.job import WORKFLOW as AD_WORKFLOW
from agent_app_factory.job import WORKFLOW as APP_FACTORY_WORKFLOW

from .cloud_journey.business_operations import LocalBusinessGateway
from cloud_journey_agents.durability.checkpoints import CheckpointStore
from .cloud_journey.database import SessionLocal, engine, init_db
from cloud_journey_agents.durability.database import (
    build_durable_engine,
    durable_session_factory,
    init_durable_db,
)
from cloud_journey_agents.durability.models import BatchAgent

WORKFLOWS = {
    workflow.agent: workflow
    for workflow in (APM_WORKFLOW, AD_WORKFLOW, APP_FACTORY_WORKFLOW)
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--agent", choices=[agent.value for agent in BatchAgent], required=True
    )
    parser.add_argument("--journey-id", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument(
        "--mode", choices=["resume", "submit", "poll"], default="resume"
    )
    args = parser.parse_args()
    durable_engine = build_durable_engine()
    try:
        init_db()
        init_durable_db(durable_engine)
        runtime = BatchRuntime(
            CheckpointStore(durable_session_factory(durable_engine)),
            LocalBusinessGateway(
                SessionLocal,
                pending_polls=int(os.getenv("SIMULATED_MYACCESS_PENDING_POLLS", "1")),
            ),
        )
        result = runtime.run(
            WORKFLOWS[BatchAgent(args.agent)],
            args.journey_id,
            args.workflow_run_id,
            mode=args.mode,
        )
        print(json.dumps(asdict(result)))
    finally:
        durable_engine.dispose()
        engine.dispose()


if __name__ == "__main__":
    main()
