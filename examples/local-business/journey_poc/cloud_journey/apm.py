"""Use the same APM validation as the deployed agents."""

from cloud_journey_agents.guardrails import CANONICAL_APM_ID_PATTERN, normalize_apm_id

__all__ = ["CANONICAL_APM_ID_PATTERN", "normalize_apm_id"]
