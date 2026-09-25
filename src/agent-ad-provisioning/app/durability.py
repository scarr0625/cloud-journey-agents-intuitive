"""AD provisioning's binding to shared durable execution."""

from cloud_journey_agents.durability.contracts import BusinessGateway, JobResult
from cloud_journey_agents.durability.runtime import execute_job as execute_workflow

from .job import WORKFLOW


def execute_job(
    journey_id: str, workflow_run_id: str, *, mode: str = "resume",
    business: BusinessGateway | None = None,
) -> JobResult:
    """Submit or poll using the shared checkpoint and saved MyAccess request ID."""
    return execute_workflow(
        WORKFLOW, journey_id, workflow_run_id, mode=mode, business=business
    )

