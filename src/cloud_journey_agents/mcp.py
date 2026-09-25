"""Typed callers use this transport; each caller supplies an explicit tool allowlist."""

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
    pass


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
            raise McpError(f"MCP tool {name} reported an error")
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
            raise McpError(f"MCP tool {name} returned an unsuccessful result")
        return payload
