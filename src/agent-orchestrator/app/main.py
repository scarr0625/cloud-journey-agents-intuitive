"""Route user questions to the separately deployed read-only Assistant."""

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()

import httpx
from fastapi import FastAPI, Header, HTTPException
from google.adk.agents import Agent
from google.adk.tools import ToolContext
from google.auth.transport.requests import Request
from google.oauth2.id_token import fetch_id_token
from journey_mcp.identity import get_verified_identity, verified_identity_state
from journey_mcp.user_auth import (
    authenticated_user,
    current_user_token,
    UserAuthenticationError,
)
from journey_sessions.conversation import (
    ConversationRuntime,
    QueryRequest,
    QueryResponse,
)


def query_assistant(query: str, tool_context: ToolContext) -> dict:
    """Route a user question to the Journey Assistant with verified user context."""
    get_verified_identity(tool_context.state, expected_subject=tool_context.user_id)
    url = os.getenv("ASSISTANT_URL", "").rstrip("/")
    token = current_user_token.get()
    if not url or not token:
        return {
            "ok": False,
            "message": "Assistant URL and verified user context are required",
        }
    headers = {"X-User-Authorization": f"Bearer {token}"}
    audience = os.getenv("ASSISTANT_CLOUD_RUN_AUDIENCE", "")
    if audience:
        headers["Authorization"] = f"Bearer {fetch_id_token(Request(), audience)}"
    payload = {"query": query}
    session_id = tool_context.state.get("assistant_session_id")
    if session_id:
        payload["session_id"] = session_id
    try:
        response = httpx.post(
            f"{url}/v1/query", json=payload, headers=headers, timeout=90
        )
        response.raise_for_status()
        result = QueryResponse.model_validate(response.json())
    except (httpx.HTTPError, ValueError) as exc:
        return {
            "ok": False,
            "message": f"Assistant request failed ({type(exc).__name__})",
        }
    # Persist only the downstream session reference, never the delegated token.
    tool_context.state["assistant_session_id"] = result.session_id
    return {"ok": True, "answer": result.answer}


root_agent = Agent(
    name="journey_orchestrator",
    model=os.getenv("ORCHESTRATOR_MODEL", "gemini-2.5-flash"),
    instruction=(
        "Route every user question to query_assistant and report its answer. "
        "You coordinate conversation routing. Business operations and checkpoint "
        "execution are owned by the separate batch workflow. Report tool failures clearly."
    ),
    tools=[query_assistant],
)
app = FastAPI(title="Journey Orchestrator")


@lru_cache
def get_runtime() -> ConversationRuntime:
    return ConversationRuntime(root_agent, "journey_orchestrator")


@app.get("/health")
@app.get("/healthz")
def health():
    return {"status": "ok"}


@app.post("/v1/query", response_model=QueryResponse)
def query(
    request: QueryRequest, x_user_authorization: str | None = Header(default=None)
):
    try:
        with authenticated_user(x_user_authorization) as identity:
            return get_runtime().query(
                request,
                user_id=identity["subject"],
                state=verified_identity_state(identity),
            )
    except UserAuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
