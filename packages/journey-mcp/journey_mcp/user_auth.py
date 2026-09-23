"""Verified HTTP user identity and request-scoped delegation to the client's MCP."""

import contextvars
from contextlib import contextmanager
import os

from google.auth.exceptions import TransportError
from google.auth.transport.requests import Request
from google.oauth2 import id_token

current_user_token = contextvars.ContextVar("journey_user_token", default="")


class UserAuthenticationError(ValueError):
    pass


def verify_user(bearer: str | None) -> tuple[dict[str, str], str]:
    audience = os.getenv("OAUTH_CLIENT_ID", "")
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
        for value in os.getenv("ALLOWED_USER_DOMAINS", "").split(",")
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
