from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from google.genai import types

from orchestrator_agent.cloud_journey.capability import DURABLE_JOURNEY_TOOLS
from orchestrator_agent.cloud_journey.identity import (
    VERIFIED_USER_SUBJECT_KEY,
    verified_identity_state,
)
from orchestrator_agent import main


def test_main_orchestrator_owns_durable_journey_tools() -> None:
    tool_names = {tool.__name__ for tool in main.root_agent.tools}
    journey_tool_names = {tool.__name__ for tool in DURABLE_JOURNEY_TOOLS}

    assert {"query_asset_inventory", "query_apm"} <= tool_names
    assert journey_tool_names <= tool_names
    assert "approve_journey" not in tool_names
    assert "reject_journey" not in tool_names
    assert "select_simulated_identity" not in tool_names


def test_http_session_is_reused_for_multi_turn_journey_context() -> None:
    session_id = uuid4().hex
    state = verified_identity_state(
        {
            "subject": "google-subject-123",
            "email": "user@example.com",
            "name": "User",
        }
    )
    first = main._get_or_create_session(
        "google-subject-123", session_id, initial_state=state
    )

    second = main._get_or_create_session("google-subject-123", session_id)

    assert second.id == first.id
    assert second.state[VERIFIED_USER_SUBJECT_KEY] == "google-subject-123"


def test_journey_only_query_does_not_require_specialist_urls(monkeypatch) -> None:
    class FakeRunner:
        def run(self, **_kwargs):
            content = types.Content(
                role="model", parts=[types.Part.from_text(text="Journey ready")]
            )
            yield SimpleNamespace(
                content=content,
                is_final_response=lambda: True,
            )

    monkeypatch.setattr(main, "runner", FakeRunner())
    monkeypatch.setattr(main, "INVENTORY_AGENT_URL", "")
    monkeypatch.setattr(main, "APM_AGENT_URL", "")

    response = main.query(main.QueryRequest(query="Show my Journey"))

    assert response.answer == "Journey ready"
    assert len(response.session_id) == 32


def test_verified_google_subject_is_injected_into_the_adk_session(monkeypatch) -> None:
    class FakeRunner:
        def run(self, **_kwargs):
            content = types.Content(
                role="model", parts=[types.Part.from_text(text="Verified")]
            )
            yield SimpleNamespace(content=content, is_final_response=lambda: True)

    verified_claims = {
        "aud": "oauth-client",
        "sub": "google-subject-456",
        "email_verified": "true",
        "email": "owner@example.com",
        "name": "Owner",
    }
    monkeypatch.setattr(main, "runner", FakeRunner())
    monkeypatch.setattr(main, "OAUTH_CLIENT_ID", "oauth-client")
    monkeypatch.setattr(
        main.id_token,
        "verify_oauth2_token",
        lambda *_args, **_kwargs: verified_claims,
    )

    response = main.query(
        main.QueryRequest(query="Start my Journey"),
        x_user_authorization="Bearer verified-token",
    )
    session = main.session_service.get_session_sync(
        app_name=main.APP_NAME,
        user_id="google-subject-456",
        session_id=response.session_id,
    )

    assert session is not None
    assert session.state[VERIFIED_USER_SUBJECT_KEY] == "google-subject-456"
    assert "verified-token" not in str(session.state)
