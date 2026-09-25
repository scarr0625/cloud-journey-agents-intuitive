"""App Factory readiness/schema validation is this agent's workflow step."""

from cloud_journey_agents.durability.contracts import WorkflowStep
from cloud_journey_agents.durability.models import BatchAgent, CheckpointStage


def validate_readiness(business, checkpoint):
    # The MCP/Data API owns authoritative schema checks and business transitions.
    return business.validate_app_factory(checkpoint.journey_id)


class AppFactoryWorkflow:
    agent = BatchAgent.APP_FACTORY_HELPER
    modes = ("resume",)

    def next_step(self, checkpoint, mode):
        return WorkflowStep(CheckpointStage.APP_FACTORY_VALIDATION, validate_readiness)


WORKFLOW = AppFactoryWorkflow()
