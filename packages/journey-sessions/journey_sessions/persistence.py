"""ADK-managed conversation storage, separate from business and checkpoint DBs."""

from __future__ import annotations

import asyncio
import os
import sys
import threading
from typing import Any

from google.adk.sessions import BaseSessionService, DatabaseSessionService


class PersistentSessionService(BaseSessionService):
    """Use one event loop for ADK's async database connections and session locks.

    The existing HTTP handlers and Runner.run use different threads/loops. Both
    sync handlers and async runner callbacks delegate to this same ADK service.
    ADK owns the physical tables, serialization, state deltas, and concurrency.
    """

    def __init__(self, db_url: str):
        self._service = DatabaseSessionService(db_url=db_url)
        # psycopg's async driver requires a selector loop on Windows.
        self._loop = (
            asyncio.SelectorEventLoop()
            if sys.platform == "win32"
            else asyncio.new_event_loop()
        )
        self._thread = threading.Thread(
            target=self._loop.run_forever, daemon=True, name="session-db"
        )
        self._thread.start()

    def _submit(self, method: str, **kwargs: Any):
        return asyncio.run_coroutine_threadsafe(
            getattr(self._service, method)(**kwargs), self._loop
        )

    async def create_session(self, **kwargs):
        return await asyncio.wrap_future(self._submit("create_session", **kwargs))

    async def get_session(self, **kwargs):
        return await asyncio.wrap_future(self._submit("get_session", **kwargs))

    async def list_sessions(self, **kwargs):
        return await asyncio.wrap_future(self._submit("list_sessions", **kwargs))

    async def delete_session(self, **kwargs):
        return await asyncio.wrap_future(self._submit("delete_session", **kwargs))

    async def append_event(self, session, event):
        return await asyncio.wrap_future(
            self._submit("append_event", session=session, event=event)
        )

    def create_session_sync(self, **kwargs):
        return self._submit("create_session", **kwargs).result()

    def get_session_sync(self, **kwargs):
        return self._submit("get_session", **kwargs).result()

    def append_event_sync(self, session, event):
        return self._submit("append_event", session=session, event=event).result()

    def delete_session_sync(self, **kwargs):
        return self._submit("delete_session", **kwargs).result()

    def close(self):
        if not self._thread.is_alive():
            return
        try:
            asyncio.run_coroutine_threadsafe(self._service.close(), self._loop).result()
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join()
            self._loop.close()


def build_session_service() -> PersistentSessionService:
    # Cloud Run can use a Unix socket or Auth Proxy in the explicit URL. Do not
    # fall back to Journey DB or ephemeral storage when configuration is absent.
    url = os.getenv(
        "SESSION_DATABASE_URL",
        "postgresql+psycopg://journey:journey@localhost:5432/session-db",
    )
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgres://")
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
    elif url.startswith("sqlite://"):
        url = "sqlite+aiosqlite://" + url.removeprefix("sqlite://")
    return PersistentSessionService(url)
