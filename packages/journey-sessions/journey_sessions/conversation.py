"""Reusable ADK session lifecycle for separately deployed HTTP agents."""

import asyncio
import atexit
from uuid import uuid4

from google.adk.events import Event, EventActions
from google.adk.runners import Runner
from google.genai import types
from pydantic import BaseModel, Field

from .persistence import build_session_service


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
        self.sessions = build_session_service()
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
        return QueryResponse(answer="\n".join(parts), session_id=session.id)
