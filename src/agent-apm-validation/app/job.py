"""APM validation owns its validation step; shared code handles durability."""

from cloud_journey_agents.durability.contracts import WorkflowStep
from cloud_journey_agents.durability.models import BatchAgent, CheckpointStage


def validate_apm(business, checkpoint):
    return business.validate_apm(checkpoint.journey_id)


class ApmValidationWorkflow:
    agent = BatchAgent.APM_VALIDATION
    modes = ("resume",)

    def next_step(self, checkpoint, mode):
        return WorkflowStep(CheckpointStage.APM_VALIDATION, validate_apm)


WORKFLOW = ApmValidationWorkflow()
