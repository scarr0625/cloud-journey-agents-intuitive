"""HTTP and CLI entry point for the AD provisioning agent."""

from cloud_journey_agents.durability.server import create_batch_app, run_job

from . import durability
from .job import WORKFLOW

app = create_batch_app(WORKFLOW, executor=durability.execute_job)


def main(argv=None):
    run_job(WORKFLOW, argv, executor=durability.execute_job)


if __name__ == "__main__":
    main()
