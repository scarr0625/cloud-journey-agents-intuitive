"""Trusted Google identity context shared by HTTP and Journey tool layers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

VERIFIED_USER_SUBJECT_KEY = "auth:google_sub"
VERIFIED_USER_EMAIL_KEY = "auth:email"
VERIFIED_USER_NAME_KEY = "auth:display_name"


class VerifiedIdentityRequired(Exception):
    """Raised when a Journey tool has no server-verified Google identity."""

    def __init__(self) -> None:
        super().__init__("A verified Google sign-in is required for Cloud Journeys.")


@dataclass(frozen=True)
class VerifiedGoogleIdentity:
    subject: str
    email: str
    display_name: str


def verified_identity_state(identity: Mapping[str, Any]) -> dict[str, str]:
    """Build model-hidden ADK session state from verified token claims."""

    subject = str(identity.get("subject") or "").strip()
    email = str(identity.get("email") or "").strip().lower()
    if not subject or not email:
        raise ValueError("Verified identity requires subject and email claims")
    return {
        VERIFIED_USER_SUBJECT_KEY: subject,
        VERIFIED_USER_EMAIL_KEY: email,
        VERIFIED_USER_NAME_KEY: str(identity.get("name") or "").strip(),
    }


def get_verified_identity(
    state: Mapping[str, Any], *, expected_subject: str
) -> VerifiedGoogleIdentity:
    """Read trusted session claims and bind them to ADK's runtime user ID."""

    subject = str(state.get(VERIFIED_USER_SUBJECT_KEY) or "").strip()
    email = str(state.get(VERIFIED_USER_EMAIL_KEY) or "").strip().lower()
    if not subject or not email or subject != expected_subject:
        raise VerifiedIdentityRequired()
    return VerifiedGoogleIdentity(
        subject=subject,
        email=email,
        display_name=str(state.get(VERIFIED_USER_NAME_KEY) or "").strip(),
    )


__all__ = [
    "VERIFIED_USER_EMAIL_KEY",
    "VERIFIED_USER_NAME_KEY",
    "VERIFIED_USER_SUBJECT_KEY",
    "VerifiedGoogleIdentity",
    "VerifiedIdentityRequired",
    "get_verified_identity",
    "verified_identity_state",
]
