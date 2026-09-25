"""AD provisioning owns the request and polling steps of the same operation."""

from cloud_journey_agents.durability.contracts import WorkflowStep
from cloud_journey_agents.durability.models import BatchAgent, CheckpointStage


def request_provisioning(business, checkpoint):
    return business.submit_ad(
        checkpoint.journey_id, f"{checkpoint.journey_id}:{checkpoint.operation_key}"
    )


def poll_provisioning(business, checkpoint):
    return business.poll_ad(checkpoint.journey_id, checkpoint.external_reference)


class AdProvisioningWorkflow:
    agent = BatchAgent.AD_PROVISIONING
    modes = ("resume", "submit", "poll")

    def next_step(self, checkpoint, mode):
        if checkpoint.external_reference:
            # Submission retries retain the request. Only poll/resume observes it.
            if mode == "submit":
                return None
            return WorkflowStep(
                CheckpointStage.AD_POLLING, poll_provisioning, mark_running=False
            )
        if mode == "poll":
            raise ValueError("AD polling requires a saved MyAccess request ID")
        return WorkflowStep(CheckpointStage.AD_SUBMISSION, request_provisioning)


WORKFLOW = AdProvisioningWorkflow()
