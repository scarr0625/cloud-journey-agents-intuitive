from google.adk.events import Event, EventActions
from google.genai import types
from sqlalchemy import create_engine, inspect

from cloud_journey_agents.sessions.persistence import PersistentSessionService


def test_real_adk_runner_persists_async_turns_across_session_service_restart(tmp_path):
    from google.adk.agents import BaseAgent
    from google.adk.runners import Runner

    class StatusAgent(BaseAgent):
        async def _run_async_impl(self, ctx):
            yield Event(
                author=self.name,
                content=types.Content(
                    role="model", parts=[types.Part(text="Waiting for MyAccess")]
                ),
                actions=EventActions(state_delta={"last_operation": "ad-provisioning"}),
            )

    url = f"sqlite+aiosqlite:///{tmp_path / 'runner.sqlite'}"
    service = PersistentSessionService(url)
    keys = {
        "app_name": "test_app",
        "user_id": "verified-user",
        "session_id": "runner-session",
    }
    try:
        service.create_session_sync(**keys)
        runner = Runner(
            agent=StatusAgent(name="status_agent"),
            app_name=keys["app_name"],
            session_service=service,
        )
        events = list(
            runner.run(
                user_id=keys["user_id"],
                session_id=keys["session_id"],
                new_message=types.Content(
                    role="user", parts=[types.Part(text="Status?")]
                ),
            )
        )
        assert events[-1].content.parts[0].text == "Waiting for MyAccess"
    finally:
        service.close()
    restarted = PersistentSessionService(url)
    try:
        session = restarted.get_session_sync(**keys)
        assert [event.content.parts[0].text for event in session.events] == [
            "Status?",
            "Waiting for MyAccess",
        ]
        assert session.state["last_operation"] == "ad-provisioning"
    finally:
        restarted.close()


def test_conversation_history_and_context_survive_service_restart(tmp_path):
    path = tmp_path / "sessions.sqlite"
    url = f"sqlite+aiosqlite:///{path}"
    keys = {
        "app_name": "chat",
        "user_id": "verified-subject",
        "session_id": "stored-session",
    }
    service = PersistentSessionService(url)
    try:
        session = service.create_session_sync(**keys, state={"journey_id": "J-123"})
        service.append_event_sync(
            session,
            Event(
                author="user",
                content=types.Content(
                    role="user", parts=[types.Part(text="Show progress")]
                ),
                actions=EventActions(state_delta={"last_topic": "progress"}),
            ),
        )
    finally:
        service.close()
    restarted = PersistentSessionService(url)
    try:
        recovered = restarted.get_session_sync(**keys)
        assert recovered.state == {"journey_id": "J-123", "last_topic": "progress"}
        assert recovered.events[0].content.parts[0].text == "Show progress"
        assert restarted.get_session_sync(**{**keys, "user_id": "other-user"}) is None
        assert restarted.get_session_sync(**{**keys, "app_name": "other-app"}) is None
        engine = create_engine(f"sqlite:///{path}")
        assert not {
            "journeys",
            "operation_checkpoint",
            "agent_execution",
            "checkpoint_event",
        } & set(inspect(engine).get_table_names())
        engine.dispose()
    finally:
        restarted.close()
