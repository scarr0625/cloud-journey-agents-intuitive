"""Select the App Factory readiness and schema-validation workflow step.

The step delegates authoritative checks and business transitions to the
business gateway. Its result describes readiness or a validation error;
actual downstream provisioning is outside this agent's checkpoint scope.

WORKFLOW fixes the agent identity and supports resume mode. The shared
runtime checks persisted progress before choosing this step, allowing
repeated invocations to reuse the same business result and checkpoint.
"""

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
