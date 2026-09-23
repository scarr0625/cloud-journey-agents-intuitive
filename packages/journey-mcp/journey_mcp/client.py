"""Typed callers use this transport; each caller supplies an explicit tool allowlist."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from typing import Any

import httpx

READ_TOOLS = frozenset({"get_journey_status", "get_journey_status_by_apm_id"})
BATCH_TOOLS = {
    "apm-validation-agent": frozenset({"get_journey_operation", "validate_apm"}),
    "ad-provisioning-agent": frozenset(
        {"get_journey_operation", "submit_ad_provisioning", "poll_ad_provisioning"}
    ),
    "app-factory-helper-agent": frozenset(
        {"get_journey_operation", "validate_app_factory"}
    ),
}


class McpError(RuntimeError):
    pass


class McpClient:
    def __init__(self, allowed_tools: frozenset[str], *, url: str | None = None):
        self.allowed_tools = allowed_tools
        self.url = url or os.getenv("MCP_URL", "")
        self.timeout = float(os.getenv("MCP_TIMEOUT_SECONDS", "90"))

    def _headers(self, user_token: str) -> dict[str, str]:
        headers = {}
        audience = os.getenv("MCP_CLOUD_RUN_AUDIENCE", "")
        if audience:
            from google.auth.transport.requests import Request
            from google.oauth2.id_token import fetch_id_token

            headers["X-Serverless-Authorization"] = (
                f"Bearer {fetch_id_token(Request(), audience)}"
            )
        bearer = os.getenv("MCP_BEARER_TOKEN", "")
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        if user_token:
            # Configure this to the client's already-established user delegation
            # contract. The MCP server must independently verify the token.
            header = os.getenv("MCP_USER_AUTH_HEADER", "X-User-Authorization")
            if header not in {"Authorization", "X-User-Authorization"}:
                raise McpError("Unsupported MCP_USER_AUTH_HEADER")
            headers[header] = f"Bearer {user_token}"
        return headers

    def call(
        self, name: str, arguments: dict[str, Any], *, user_token: str = ""
    ) -> Any:
        if name not in self.allowed_tools:
            raise McpError(f"This agent cannot call MCP tool {name}")
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
