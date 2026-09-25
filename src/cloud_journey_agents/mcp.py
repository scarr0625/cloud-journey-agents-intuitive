"""Call the private MCP service through an explicit per-caller tool allowlist.

A call validates its tool and any chat-read arguments, prepares workload
and delegated-user headers, and initializes the MCP transport. It confirms
that the requested tool is advertised before invoking business behavior.
Missing tools, transport failures, and unsuccessful results raise McpError.

Synchronous tools run the async SDK in a separate thread, allowing callers
that already have an event loop. The transport returns decoded results;
business adapters interpret their meaning. Client construction performs
no network request, and MCP failure never triggers direct database access.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from typing import Any

import httpx

from .config import setting
from .guardrails import (
    GuardrailError,
    READ_TOOLS,
    require_allowed_tool,
    validate_read_arguments,
)
from .identity import service_identity_token


class McpError(RuntimeError):
    def __init__(self, message: str, *, code: str | None = None, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def call_idempotent(client, name, arguments, *, user_token=""):
    """Retry one transport failure with the identical server-deduplicated mutation ID."""
    if not arguments.get("mutation_id"):
        raise ValueError("A persistence mutation requires mutation_id")
    for attempt in range(2):
        try:
            return client.call(name, arguments, user_token=user_token)
        except McpError as exc:
            if not exc.retryable or attempt:
                raise


class McpClient:
    def __init__(self, allowed_tools: frozenset[str], *, url: str | None = None):
        self.allowed_tools = allowed_tools
        self.url = url or setting("MCP_URL")
        self.timeout = float(setting("MCP_TIMEOUT_SECONDS", "90"))

    def _headers(self, user_token: str) -> dict[str, str]:
        headers = {}
        audience = setting("MCP_CLOUD_RUN_AUDIENCE")
        if audience:
            headers["X-Serverless-Authorization"] = (
                f"Bearer {service_identity_token(audience)}"
            )
        bearer = setting("MCP_BEARER_TOKEN")
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        if user_token:
            # Configure this to the client's already-established user delegation
            # contract. The MCP server must independently verify the token.
            header = setting("MCP_USER_AUTH_HEADER", "X-User-Authorization")
            if header not in {"Authorization", "X-User-Authorization"}:
                raise McpError("Unsupported MCP_USER_AUTH_HEADER")
            headers[header] = f"Bearer {user_token}"
        return headers

    def call(
        self, name: str, arguments: dict[str, Any], *, user_token: str = ""
    ) -> Any:
        try:
            require_allowed_tool(name, self.allowed_tools)
            if name in READ_TOOLS:
                arguments = validate_read_arguments(name, arguments)
        except GuardrailError as exc:
            raise McpError(str(exc)) from exc
        if not self.url:
            raise McpError("MCP_URL is required")
        headers = self._headers(user_token)
        # ADK may invoke synchronous tools from a running async loop. Execute
        # the SDK in its own thread, with per-call headers and no persisted token.
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(
                lambda: asyncio.run(self._bounded_call(name, arguments, headers))
            ).result()

    async def _bounded_call(self, name, arguments, headers):
        try:
            return await asyncio.wait_for(
                self._call(name, arguments, headers), self.timeout
            )
        except McpError:
            raise
        except (TimeoutError, httpx.TransportError) as exc:
            raise McpError(
                f"MCP tool {name} transport failed", retryable=True
            ) from exc
        except Exception as exc:
            # Preserve the cause for diagnostics without returning auth headers.
            raise McpError(f"MCP tool {name} failed ({type(exc).__name__})") from exc

    async def _call(self, name, arguments, headers):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        async with httpx.AsyncClient(headers=headers, timeout=self.timeout) as http:
            async with streamable_http_client(self.url, http_client=http) as (
                read,
                write,
                _,
            ):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    # A missing tool must fail before invoking business behavior.
                    cursor = None
                    seen_cursors = set()
                    while True:
                        available = await session.list_tools(cursor=cursor)
                        if name in {tool.name for tool in available.tools}:
                            break
                        cursor = available.nextCursor
                        if not cursor or cursor in seen_cursors:
                            raise McpError(f"MCP tool {name} is unavailable")
                        seen_cursors.add(cursor)
                    response = await session.call_tool(name, arguments)
        if response.isError:
            payload = response.structuredContent
            if payload is None:
                blocks = [block.text for block in response.content if block.type == "text"]
                try:
                    payload = json.loads(blocks[0]) if len(blocks) == 1 else None
                except (ValueError, TypeError):
                    payload = None
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            code = error.get("code") if isinstance(error, dict) else None
            raise McpError(f"MCP tool {name} reported an error", code=code)
        if response.structuredContent is not None:
            payload = response.structuredContent
        else:
            content = [block.text for block in response.content if block.type == "text"]
            if len(content) != 1:
                raise McpError(
                    f"MCP tool {name} must return a structured result or one JSON block"
                )
            payload = json.loads(content[0])
        if isinstance(payload, dict) and payload.get("ok") is False:
            error = payload.get("error", {})
            code = error.get("code") if isinstance(error, dict) else None
            raise McpError(f"MCP tool {name} returned an unsuccessful result", code=code)
        return payload
