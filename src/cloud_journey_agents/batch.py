"""Compatibility imports for callers of the PoC's earlier batch API.

Workflow contracts, MCP result handling, and checkpoint execution now live
under cloud_journey_agents.durability. Re-exporting those names here lets
older callers keep their imports while all implementations share one source.

The three batch agents import durability directly. This file is outside
the durability copy set: keep the main repository's own batch.py when
porting the capability, and connect its business operations through the
runtime's business= gateway parameter.
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
