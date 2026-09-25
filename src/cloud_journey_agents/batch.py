"""Compatibility imports for this PoC's earlier batch API.

New integrations use cloud_journey_agents.durability directly. This file is not
part of the durability copy set and need not replace another repo's batch.py.
"""

from .durability.contracts import (
    BatchWorkflow, BusinessGateway, BusinessProgress, JobResult, WorkflowStep,
)
from .durability.mcp_gateway import McpBusinessGateway
from .durability.runtime import BatchRuntime, execute_job

__all__ = [
    "BatchWorkflow", "BusinessGateway", "BusinessProgress", "JobResult",
    "WorkflowStep", "McpBusinessGateway", "BatchRuntime", "execute_job",
]
