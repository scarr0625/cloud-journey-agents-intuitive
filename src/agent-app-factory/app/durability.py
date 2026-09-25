"""App Factory's binding to shared durable execution."""

from cloud_journey_agents.durability.contracts import BusinessGateway, JobResult
from cloud_journey_agents.durability.runtime import execute_job as execute_workflow

from .job import WORKFLOW


def execute_job(
    journey_id: str, workflow_run_id: str, *, mode: str = "resume",
    business: BusinessGateway | None = None,
) -> JobResult:
    """Run or resume this agent's App Factory validation checkpoint."""
    return execute_workflow(
        WORKFLOW, journey_id, workflow_run_id, mode=mode, business=business
    )

