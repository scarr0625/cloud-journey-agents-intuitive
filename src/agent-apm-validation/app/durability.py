"""Bind the APM Validation workflow to the shared durable execution runtime.

Both HTTP and CLI calls enter here with a Journey ID and workflow run ID.
WORKFLOW fixes the agent identity to apm-validation-agent;
the runtime owns checkpoint claims, reconciliation, saves, and cleanup.
Its single validation operation resumes from persisted business progress.

Supply business= to reuse the main repository's business implementation.
Otherwise the runtime uses its optional MCP gateway. This small binding
imports durability directly and does not require either shared batch file;
when porting it, connect WORKFLOW to the receiving agent's job definition.
"""

from cloud_journey_agents.durability.contracts import BusinessGateway, JobResult
from cloud_journey_agents.durability.runtime import execute_job as execute_workflow

from .job import WORKFLOW


def execute_job(
    journey_id: str, workflow_run_id: str, *, mode: str = "resume",
    business: BusinessGateway | None = None,
) -> JobResult:
    """Run or resume this agent's APM validation checkpoint."""
    return execute_workflow(
        WORKFLOW, journey_id, workflow_run_id, mode=mode, business=business
    )

