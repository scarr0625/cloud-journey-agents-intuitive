"""Run one agent-apm-validation job; Workflows supplies the Journey and run IDs."""

from journey_durability.job import run_job
from journey_durability.models import BatchAgent


def main(argv=None):
    run_job(BatchAgent.APM_VALIDATION, argv)


if __name__ == "__main__":
    main()
