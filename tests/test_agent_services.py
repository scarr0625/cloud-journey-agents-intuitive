from types import SimpleNamespace

import httpx
import pytest

from agent_assistant import agent as assistant, tools as assistant_tools
from agent_orchestrator import agent as orchestrator, tools as orchestrator_tools
from cloud_journey_agents.identity import verified_identity_state
from cloud_journey_agents.identity import current_user_token, authenticated_user


def test_http_agent_tool_boundaries():
    assert {tool.__name__ for tool in assistant.root_agent.tools} == {
        "get_journey_status",
        "get_journey_status_by_apm_id",
    }
    assert {tool.__name__ for tool in orchestrator.root_agent.tools} == {
        "query_assistant"
    }


def test_orchestrator_preserves_downstream_session_without_storing_token(monkeypatch):
    state = verified_identity_state({"subject": "user-1", "email": "user@example.com"})
    state["assistant_session_id"] = "existing-assistant-session"
    context = SimpleNamespace(state=state, user_id="user-1")
    monkeypatch.setenv("ASSISTANT_URL", "https://assistant.test")
    monkeypatch.delenv("ASSISTANT_CLOUD_RUN_AUDIENCE", raising=False)
    seen = {}

    def post(url, **kwargs):
        seen.update(kwargs)
        return httpx.Response(
            200,
            json={"answer": "Waiting", "session_id": "existing-assistant-session"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(orchestrator_tools.httpx, "post", post)
    token = current_user_token.set("ephemeral-token")
    try:
        result = orchestrator_tools.query_assistant("Progress?", context)
    finally:
        current_user_token.reset(token)
    assert result == {"ok": True, "answer": "Waiting"}
    assert seen["json"]["session_id"] == "existing-assistant-session"
    assert seen["headers"]["X-User-Authorization"] == "Bearer ephemeral-token"
    assert "ephemeral-token" not in str(state)


def test_assistant_requires_request_identity_and_calls_only_authorized_status(
    monkeypatch,
):
    calls = []

    class Client:
        def __init__(self, allowed):
            assert allowed == {"get_journey_status", "get_journey_status_by_apm_id"}

        def call(self, name, arguments, **kwargs):
            calls.append((name, arguments, kwargs))
            return {"journey_id": "J-123"}

    monkeypatch.setattr(assistant_tools, "McpClient", Client)
    state = verified_identity_state({"subject": "user-1", "email": "user@example.com"})
    context = SimpleNamespace(state=state, user_id="user-1")
    assert assistant_tools.get_journey_status("J-123", context)["ok"] is False
    token = current_user_token.set("ephemeral-token")
    try:
        assert (
            assistant_tools.get_journey_status("J-123", context)["journey_id"]
            == "J-123"
        )
        context.user_id = "spoofed-user"
        assert assistant_tools.get_journey_status("J-123", context)["ok"] is False
    finally:
        current_user_token.reset(token)
    assert len(calls) == 1
    assert "ephemeral-token" not in str(state)


def test_authentication_context_is_cleared_even_when_processing_fails(monkeypatch):
    from cloud_journey_agents import identity as user_auth

    monkeypatch.setattr(
        user_auth, "verify_user", lambda _: ({"subject": "user-1"}, "ephemeral-token")
    )
    with pytest.raises(RuntimeError):
        with authenticated_user("Bearer supplied"):
            assert current_user_token.get() == "ephemeral-token"
            raise RuntimeError("failed turn")
    assert current_user_token.get() == ""


def test_real_runner_keeps_request_token_and_persists_only_conversation(
    monkeypatch, tmp_path
):
    from google.adk.agents import BaseAgent
    from google.adk.events import Event
    from google.genai import types
    from cloud_journey_agents.sessions.conversation import ConversationRuntime, QueryRequest

    seen = []

    class AuthenticatedAgent(BaseAgent):
        async def _run_async_impl(self, ctx):
            seen.append(current_user_token.get())
            yield Event(
                author=self.name,
                content=types.Content(
                    role="model", parts=[types.Part(text="Waiting for MyAccess")]
                ),
            )

    monkeypatch.setenv(
        "SESSION_DATABASE_URL", f"sqlite:///{tmp_path / 'conversation.sqlite'}"
    )
    identity = {"subject": "user-1", "email": "first@example.com"}
    session_id = None
    # Restart the runtime, rotate the request token, and refresh identity claims
    # while retaining the same conversation.
    for request_token in ["first-ephemeral-token", "second-ephemeral-token"]:
        runtime = ConversationRuntime(AuthenticatedAgent(name="test_agent"), "test_app")
        handle = current_user_token.set(request_token)
        try:
            result = runtime.query(
                QueryRequest(query="Status?", session_id=session_id),
                user_id=identity["subject"],
                state=verified_identity_state(identity),
            )
            session_id = result.session_id
            session = runtime.sessions.get_session_sync(
                app_name="test_app", user_id=identity["subject"], session_id=session_id
            )
            assert session.state["auth:email"] == identity["email"]
            assert "ephemeral-token" not in session.model_dump_json()
            assert result.answer == "Waiting for MyAccess"
        finally:
            current_user_token.reset(handle)
            runtime.sessions.close()
        identity["email"] = "updated@example.com"
    assert seen == ["first-ephemeral-token", "second-ephemeral-token"]
    assert len([event for event in session.events if event.author == "user"]) == 2
    assert current_user_token.get() == ""
