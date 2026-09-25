"""Assistant HTTP server; composition and session wiring live in their own modules."""

from . import settings

from fastapi import FastAPI, Header, HTTPException

from cloud_journey_agents.identity import (
    UserAuthenticationError,
    authenticated_user,
    verified_identity_state,
)
from cloud_journey_agents.logs import configure_logging
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
