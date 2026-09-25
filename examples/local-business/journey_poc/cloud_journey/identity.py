"""Use the same verified identity context as the deployed agents."""

from cloud_journey_agents.identity import (
    VERIFIED_USER_EMAIL_KEY,
    VERIFIED_USER_NAME_KEY,
    VERIFIED_USER_SUBJECT_KEY,
    VerifiedGoogleIdentity,
    VerifiedIdentityRequired,
    get_verified_identity,
    verified_identity_state,
)

__all__ = [
    "VERIFIED_USER_EMAIL_KEY", "VERIFIED_USER_NAME_KEY", "VERIFIED_USER_SUBJECT_KEY",
    "VerifiedGoogleIdentity", "VerifiedIdentityRequired", "get_verified_identity",
    "verified_identity_state",
]
