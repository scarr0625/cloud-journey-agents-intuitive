"""ADK session persistence through authenticated Schwab MCP tools only.

The server commits session events, state deltas, and revision changes together.
Agents retain ADK Session objects in memory and send optimistic versions on
updates. Workload/application and delegated-user checks are enforced on both
sides; user tokens travel as headers and are never put into persistence payloads.
There is no database URL, SQL driver, worker loop, or local persistence fallback.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from google.adk.sessions import BaseSessionService, Session
from google.adk.sessions.base_session_service import ListSessionsResponse

from ..guardrails import SESSION_TOOLS
from ..identity import current_user_token
from ..mcp import McpClient, McpError, call_idempotent


class PersistentSessionService(BaseSessionService):
    def __init__(self, *, app_name: str, client=None):
        if not app_name:
            raise ValueError("Session persistence requires a fixed application name")
        self.app_name = app_name
        self.client = client if client is not None else McpClient(SESSION_TOOLS)

    def _scope(self, app_name, user_id, session_id=None):
        if app_name != self.app_name or not user_id:
            raise ValueError("Session application and user must match the request scope")
        if session_id is not None and not session_id:
            raise ValueError("Session ID must be nonempty")
        return {"app_name": app_name, "user_id": user_id, **(
            {"session_id": session_id} if session_id is not None else {}
        )}

    def _call(self, tool, arguments, *, mutation=False):
        token = current_user_token.get()
        if not token:
            raise McpError("Session persistence requires verified request identity", code="UNAUTHENTICATED")
        if mutation:
            return call_idempotent(self.client, tool, {
                **arguments, "mutation_id": str(uuid4()),
            }, user_token=token)
        return self.client.call(tool, arguments, user_token=token)

    def _decode(self, payload, scope):
        try:
            version = payload["version"]
            raw = payload["session"]
            if type(version) is not int or version < 1 or not {
                "id", "app_name", "user_id", "state", "events", "last_update_time"
            }.issubset(raw):
                raise ValueError("Incomplete session envelope")
            session = Session.model_validate(raw)
            if (
                session.app_name != scope["app_name"]
                or session.user_id != scope["user_id"]
                or ("session_id" in scope and session.id != scope["session_id"])
            ):
                raise ValueError("Mismatched session identity")
            if any(key.startswith("temp:") for key in session.state):
                raise ValueError("Temporary state must not be persisted")
            for event in session.events:
                if any(key.startswith("temp:") for key in event.actions.state_delta):
                    raise ValueError("Temporary event state must not be persisted")
            session._storage_update_marker = f"mcp:{version}"
            return session
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise McpError("Invalid MCP session response", code="INVALID_RESPONSE") from exc

    def create_session_sync(self, *, app_name, user_id, state=None, session_id=None):
        scope = self._scope(app_name, user_id, session_id or uuid4().hex)
        persistent = {key: value for key, value in (state or {}).items() if not key.startswith("temp:")}
        return self._decode(self._call("create_agent_session", {
            **scope, "state": persistent,
        }, mutation=True), scope)

    async def create_session(self, **kwargs):
        return await asyncio.to_thread(self.create_session_sync, **kwargs)

    def get_session_sync(self, *, app_name, user_id, session_id, config=None):
        scope = self._scope(app_name, user_id, session_id)
        arguments = {**scope, "config": config.model_dump(exclude_none=True) if config else {}}
        payload = self._call("get_agent_session", arguments)
        return None if payload is None else self._decode(payload, scope)

    async def get_session(self, **kwargs):
        return await asyncio.to_thread(self.get_session_sync, **kwargs)

    def list_sessions_sync(self, *, app_name, user_id=None):
        scope = self._scope(app_name, user_id)
        payload = self._call("list_agent_sessions", scope)
        if not isinstance(payload, dict) or not isinstance(payload.get("sessions"), list):
            raise McpError("Invalid MCP session list", code="INVALID_RESPONSE")
        return ListSessionsResponse(sessions=[self._decode(item, scope) for item in payload["sessions"]])

    async def list_sessions(self, **kwargs):
        return await asyncio.to_thread(self.list_sessions_sync, **kwargs)

    def delete_session_sync(self, *, app_name, user_id, session_id):
        scope = self._scope(app_name, user_id, session_id)
        payload = self._call("delete_agent_session", scope, mutation=True)
        if not isinstance(payload, dict) or payload != {**scope, "deleted": True}:
            raise McpError("Invalid MCP session deletion acknowledgment", code="INVALID_RESPONSE")

    async def delete_session(self, **kwargs):
        return await asyncio.to_thread(self.delete_session_sync, **kwargs)

    def append_event_sync(self, session, event):
        if event.partial:
            return event
        scope = self._scope(session.app_name, session.user_id, session.id)
        marker = session._storage_update_marker or ""
        if not marker.startswith("mcp:"):
            raise ValueError("Session must be loaded from MCP before appending events")
        version = int(marker.removeprefix("mcp:"))
        persistent_event = self._trim_temp_delta_state(event.model_copy(deep=True))
        wire_event = persistent_event.model_dump(mode="json", by_alias=False)
        payload = self._call("append_agent_session_event", {
            **scope, "expected_version": version, "event": wire_event,
        }, mutation=True)
        saved = self._decode(payload, scope)
        if saved._storage_update_marker != f"mcp:{version + 1}":
            raise McpError("Invalid session revision acknowledgment", code="INVALID_RESPONSE")
        events = [item for item in saved.events if item.id == event.id]
        if len(events) != 1 or events[0].model_dump(mode="json", by_alias=False) != wire_event:
            raise McpError("MCP did not acknowledge the appended event", code="INVALID_RESPONSE")
        temporary = {key: value for key, value in session.state.items() if key.startswith("temp:")}
        session.state = {**saved.state, **temporary}
        self._apply_temp_state(session, event)
        session.events = saved.events
        session.last_update_time = saved.last_update_time
        session._storage_update_marker = saved._storage_update_marker
        return events[0]

    async def append_event(self, session, event):
        return await asyncio.to_thread(self.append_event_sync, session, event)

    def close(self):
        """No agent-owned database resources exist to close."""


def build_session_service(app_name: str) -> PersistentSessionService:
    return PersistentSessionService(app_name=app_name)
