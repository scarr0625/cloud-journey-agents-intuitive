"""HTTP and CLI entry point for the App Factory agent.

Running python -m agent_app_factory.server performs one job invocation
and exits with a JSON result. Serving agent_app_factory.server:app
with Uvicorn exposes health routes and POST /v1/run instead; Uvicorn must
bind the port configured for the service deployment.

Both entry points call app/durability.py with this agent's fixed workflow.
Constructing the HTTP app does not start business work or open the Durable
State DB. Shared argument validation and result handling live in
cloud_journey_agents.durability.server.
"""

from cloud_journey_agents.durability.server import create_batch_app, run_job

from . import durability
from .job import WORKFLOW

app = create_batch_app(WORKFLOW, executor=durability.execute_job)


def main(argv=None):
    run_job(WORKFLOW, argv, executor=durability.execute_job)


if __name__ == "__main__":
    main()
