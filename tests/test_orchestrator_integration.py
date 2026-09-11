from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from google.genai import types

from orchestrator_agent.app.cloud_journey.capability import DURABLE_JOURNEY_TOOLS
from orchestrator_agent.app.cloud_journey.identity import (
    VERIFIED_USER_SUBJECT_KEY,
    verified_identity_state,
)
from orchestrator_agent.app import main


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


def test_playground_starts_locked_and_renders_journey_workspace(monkeypatch) -> None:
    monkeypatch.setattr(main, "OAUTH_CLIENT_ID", "oauth-client")

    html = main.playground()

    assert '<fieldset class="interaction" id="interaction" disabled>' in html
    assert "Portfolio Overview" in html
    assert "Journey phases" in html
    assert "WAITING_FOR_APPROVAL: 1" in html


def test_playground_keeps_per_apm_chat_sessions_and_refreshes_progress(
    monkeypatch,
) -> None:
    monkeypatch.setattr(main, "OAUTH_CLIENT_ID", "oauth-client")

    html = main.playground()

    assert "const chatKey = apm => apm ? `apm:${apm}` : 'portfolio'" in html
    assert "if (chat.sessionId) requestBody.session_id = chat.sessionId" in html
    assert "chat.sessionId = data.session_id" in html
    assert "Journey progress updated:" in html
    assert "setInterval(refreshActiveJourney, 10000)" in html


def test_journey_progress_endpoint_uses_verified_google_subject(monkeypatch) -> None:
    class FakeService:
        def status_by_apm_id_for_subject(self, apm_id: str, subject: str):
            return {"ok": True, "apm_id": apm_id, "subject": subject}

    monkeypatch.setattr(main, "OAUTH_CLIENT_ID", "oauth-client")
    monkeypatch.setattr(main, "get_service", lambda: FakeService())
    monkeypatch.setattr(
        main.id_token,
        "verify_oauth2_token",
        lambda *_args, **_kwargs: {
            "aud": "oauth-client",
            "sub": "google-subject-789",
            "email_verified": True,
            "email": "journey-owner@example.com",
        },
    )

    response = main.journey_status_by_apm(
        "100401", x_user_authorization="Bearer verified-token"
    )

    assert response == {
        "ok": True,
        "apm_id": "100401",
        "subject": "google-subject-789",
    }


def test_journey_portfolio_is_scoped_to_verified_users_groups(monkeypatch) -> None:
    visible = SimpleNamespace(id="J-VISIBLE", access_group_id="GROUP_1")
    hidden = SimpleNamespace(id="J-HIDDEN", access_group_id="GROUP_2")

    class FakeStateMachine:
        def get_access_groups_for_user(self, subject: str):
            assert subject == "google-subject-789"
            return frozenset({"GROUP_1"})

        def list_apm_ids_for_groups(self, groups):
            assert groups == frozenset({"GROUP_1"})
            return ["100401", "100402"]

        def find_journey_by_apm_id(self, apm_id: str):
            return visible if apm_id == "100401" else hidden

    class FakeService:
        state_machine = FakeStateMachine()

        def status(self, journey_id: str):
            assert journey_id == "J-VISIBLE"
            return {"journey_id": journey_id, "apm_id": "100401"}

    monkeypatch.setattr(main, "OAUTH_CLIENT_ID", "oauth-client")
    monkeypatch.setattr(main, "get_service", lambda: FakeService())
    monkeypatch.setattr(
        main.id_token,
        "verify_oauth2_token",
        lambda *_args, **_kwargs: {
            "aud": "oauth-client",
            "sub": "google-subject-789",
            "email_verified": True,
            "email": "journey-owner@example.com",
        },
    )

    response = main.journey_portfolio(
        x_user_authorization="Bearer verified-token"
    )

    assert response == {"journeys": [{"journey_id": "J-VISIBLE", "apm_id": "100401"}]}
