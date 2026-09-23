"""Cloud Run Job / local entry point for one declared agent invocation."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os

from journey_durability.runtime import BatchRuntime
from .cloud_journey.business_operations import LocalBusinessGateway
from journey_durability.checkpoints import CheckpointStore
from .cloud_journey.database import SessionLocal, engine, init_db
from journey_durability.database import build_durable_engine, durable_session_factory, init_durable_db
from journey_durability.models import BatchAgent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", choices=[agent.value for agent in BatchAgent], required=True)
    parser.add_argument("--journey-id", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--mode", choices=["resume", "submit", "poll"], default="resume")
    args = parser.parse_args()
    durable_engine = build_durable_engine()
    try:
        init_db()
        init_durable_db(durable_engine)
        runtime = BatchRuntime(
            CheckpointStore(durable_session_factory(durable_engine)),
            LocalBusinessGateway(SessionLocal, pending_polls=int(os.getenv("SIMULATED_MYACCESS_PENDING_POLLS", "1"))),
        )
        print(json.dumps(asdict(runtime.run(BatchAgent(args.agent), args.journey_id, args.workflow_run_id, mode=args.mode))))
    finally:
        durable_engine.dispose()
        engine.dispose()


if __name__ == "__main__":
    main()
