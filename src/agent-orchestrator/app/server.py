"""HTTP entry point for authenticated Orchestrator conversations.

Uvicorn serves health routes and POST /v1/query. Each query verifies the
delegated sign-in token, establishes request-scoped identity, and invokes
the Orchestrator's persistent conversation runtime. Authentication failures
are returned as HTTP 401.

The runtime is created lazily through sessions.py. Routing to the Assistant
happens through the agent's tool during the turn, keeping HTTP validation,
conversation persistence, and downstream delegation in separate modules.
"""

from . import settings

from fastapi import FastAPI, Header, HTTPException

from cloud_journey_agents.identity import (
    UserAuthenticationError,
    authenticated_user,
    verified_identity_state,
)
from cloud_journey_agents.logs import configure_logging
from cloud_journey_agents.mcp import McpError
from cloud_journey_agents.sessions.conversation import QueryRequest, QueryResponse

from .sessions import get_runtime

configure_logging()
app = FastAPI(title=settings.APP_TITLE)


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
    except McpError as exc:
        status = 409 if exc.code in {"STALE_SESSION", "ALREADY_EXISTS", "EVENT_ALREADY_RECORDED"} else 503
        if exc.code in {"FORBIDDEN", "UNAUTHENTICATED"}:
            status = 403
        raise HTTPException(status_code=status, detail="MCP session operation could not be completed") from exc
