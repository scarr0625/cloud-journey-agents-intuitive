"""HTTP Assistant: authorized MCP reads plus its own persisted ADK sessions."""

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Header, HTTPException
from google.adk.agents import Agent
from journey_mcp.identity import verified_identity_state
from journey_mcp.user_auth import authenticated_user, UserAuthenticationError
from journey_sessions.conversation import (
    ConversationRuntime,
    QueryRequest,
    QueryResponse,
)

from .tools import STATUS_TOOLS

root_agent = Agent(
    name="journey_assistant",
    model=os.getenv("ASSISTANT_MODEL", "gemini-2.5-flash"),
    instruction=(
        "Explain authorized Journey business progress using the status tools. "
        "Report tool errors without inventing results. You cannot change Journeys, "
        "start jobs, or manage checkpoints. READY_TO_PROVISION describes readiness, "
        "and a completed operation does not mean the Journey has completed. "
        "Use an explicitly supplied Journey/APM ID or one established in this conversation."
    ),
    tools=STATUS_TOOLS,
)
app = FastAPI(title="Journey Assistant")


@lru_cache
def get_runtime() -> ConversationRuntime:
    return ConversationRuntime(root_agent, "journey_assistant")


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
