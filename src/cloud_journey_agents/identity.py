"""Verify user identity and supply workload credentials for downstream calls.

HTTP handlers verify Google sign-in tokens against the configured audience
and optional allowed domains. Only verified identity claims are copied
into conversation state; tools bind those claims to the runtime user ID.

The delegated user token is held in a ContextVar for the current request
and reset when that request ends. Service-to-service Google ID tokens are
cached separately by audience and refreshed before expiry. Keeping both
paths here gives all agents the same authentication behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
import contextvars
from contextlib import contextmanager
from threading import Lock
import time
from typing import Any, Mapping

from google.auth import jwt
from google.auth.exceptions import TransportError
from google.auth.transport.requests import Request
from google.oauth2 import id_token

from .config import setting

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


current_user_token = contextvars.ContextVar("journey_user_token", default="")


class UserAuthenticationError(ValueError):
    pass


def verify_user(bearer: str | None) -> tuple[dict[str, str], str]:
    audience = setting("OAUTH_CLIENT_ID")
    if not audience:
        raise UserAuthenticationError("OAUTH_CLIENT_ID is required")
    raw = (bearer or "").strip()
    token = raw[7:].strip() if raw[:7].lower() == "bearer " else raw
    if not token:
        raise UserAuthenticationError("Sign-in required")
    try:
        info = id_token.verify_oauth2_token(token, Request(), audience)
    except (ValueError, TransportError) as exc:
        raise UserAuthenticationError("Unable to verify sign-in") from exc
    email = str(info.get("email") or "").strip().lower()
    subject = str(info.get("sub") or "").strip()
    domains = {
        value.strip().lower()
        for value in setting("ALLOWED_USER_DOMAINS").split(",")
        if value.strip()
    }
    if (
        info.get("aud") != audience
        or info.get("email_verified") not in (True, "true")
        or not subject
        or "@" not in email
        or (domains and email.rsplit("@", 1)[1] not in domains)
    ):
        raise UserAuthenticationError("Sign-in is not authorized for this application")
    return {
        "subject": subject,
        "email": email,
        "name": str(info.get("name") or ""),
    }, token


@contextmanager
def authenticated_user(bearer: str | None):
    identity, token = verify_user(bearer)
    context = current_user_token.set(token)
    try:
        yield identity
    finally:
        current_user_token.reset(context)


_service_tokens: dict[str, tuple[str, float]] = {}
_service_token_lock = Lock()


def service_identity_token(audience: str) -> str:
    """Cache workload ID tokens by audience, refreshing before their expiry."""
    if not audience:
        raise ValueError("Service token audience is required")
    with _service_token_lock:
        cached = _service_tokens.get(audience)
        if cached and cached[1] > time.time() + 60:
            return cached[0]
        token = id_token.fetch_id_token(Request(), audience)
        # This locally fetched token is decoded only to learn its expiration.
        # Receiving services remain responsible for verifying its signature.
        expires = float(jwt.decode(token, verify=False)["exp"])
        _service_tokens[audience] = (token, expires)
        return token
