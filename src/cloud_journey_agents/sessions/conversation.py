"""Run a conversational turn with persisted ADK sessions and verified context.

ConversationRuntime binds one agent and application name to MCP session tools.
Each query retrieves or creates a session scoped to that app and user,
refreshes verified identity claims when needed, and runs the agent against
the stored conversation. The response includes the reusable session ID.

The synchronous entry point drives the async runner in its calling thread
so request-scoped identity ContextVars remain available to tools. Session
state is persisted by Schwab's MCP server. This runtime holds no database
connection or credentials and cannot fall back to local storage.
"""

import asyncio
import atexit
import logging
from uuid import uuid4

from google.adk.events import Event, EventActions
from google.adk.runners import Runner
from google.genai import types
from pydantic import BaseModel, Field

from .persistence import build_session_service

logger = logging.getLogger("cloud_journey_agents.sessions")


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=8000)
    session_id: str | None = Field(
        default=None, min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"
    )


class QueryResponse(BaseModel):
    answer: str
    session_id: str


class ConversationRuntime:
    def __init__(self, agent, app_name: str):
        self.app_name = app_name
        self.sessions = build_session_service(app_name)
        self.runner = Runner(
            agent=agent, app_name=app_name, session_service=self.sessions
        )
        atexit.register(self.sessions.close)

    def query(
        self, request: QueryRequest, *, user_id: str, state: dict
    ) -> QueryResponse:
        # The HTTP handler runs in a worker thread. Keep its request context in
        # this loop: Runner.run starts another thread and loses ContextVars.
        return asyncio.run(self.query_async(request, user_id=user_id, state=state))

    async def query_async(
        self, request: QueryRequest, *, user_id: str, state: dict
    ) -> QueryResponse:
        session_id = request.session_id or uuid4().hex
        keys = {"app_name": self.app_name, "user_id": user_id, "session_id": session_id}
        session = await self.sessions.get_session(**keys)
        if session is None:
            session = await self.sessions.create_session(**keys, state=state)
        elif any(session.state.get(key) != value for key, value in state.items()):
            await self.sessions.append_event(
                session, Event(author="system", actions=EventActions(state_delta=state))
            )
        parts = []
        message = types.Content(role="user", parts=[types.Part(text=request.query)])
        async for event in self.runner.run_async(
            user_id=user_id, session_id=session.id, new_message=message
        ):
            if event.is_final_response() and event.content:
                parts.extend(
                    part.text for part in event.content.parts or [] if part.text
                )
        if not parts:
            raise RuntimeError("Agent returned no text")
        logger.info(
            "Conversation completed", extra={"journey_fields": {"app": self.app_name}}
        )
        return QueryResponse(answer="\n".join(parts), session_id=session.id)
