"""Select the APM validation step for this agent's durable workflow.

The step asks the business gateway to validate one Journey's APM data.
The gateway owns the authoritative validation and persisted business
outcome; the shared runtime decides whether that operation needs execution
or can resume from its existing result.

WORKFLOW fixes this agent's identity and supports resume mode. Selecting
the step here performs no database access; app/durability.py connects it
to checkpoint ownership and recovery when an invocation starts.
"""

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
