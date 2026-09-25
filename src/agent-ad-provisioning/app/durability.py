"""Bind the AD Provisioning workflow to the shared durable execution runtime.

Both HTTP and CLI calls enter here with a Journey ID and workflow run ID.
WORKFLOW fixes the agent identity to ad-provisioning-agent;
the runtime owns checkpoint claims, reconciliation, saves, and cleanup.
Submit, poll, and resume share one checkpoint and saved MyAccess request ID.

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
    """Submit or poll using the shared checkpoint and saved MyAccess request ID."""
    return execute_workflow(
        WORKFLOW, journey_id, workflow_run_id, mode=mode, business=business
    )

