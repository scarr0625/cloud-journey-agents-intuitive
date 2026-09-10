"""Unified HTTP orchestrator for specialists and durable Cloud Journeys."""

from __future__ import annotations

import contextvars
import json
import os
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv

load_dotenv()

# ADK reads these values while its modules are imported, so set defaults first.
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService, Session
from google.auth.exceptions import TransportError
from google.auth.transport.requests import Request
from google.genai import types
from google.oauth2 import id_token
from pydantic import BaseModel, Field

from cloud_journey.capability import (
    DURABLE_JOURNEY_INSTRUCTION,
    DURABLE_JOURNEY_TOOLS,
)
from cloud_journey.identity import verified_identity_state

APP_NAME = "orchestrator"
MODEL = os.environ.get("ORCHESTRATOR_MODEL", "gemini-3.6-flash")
INVENTORY_AGENT_URL = os.environ.get("INVENTORY_AGENT_URL", "").rstrip("/")
APM_AGENT_URL = os.environ.get("APM_AGENT_URL", "").rstrip("/")
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "90"))

# Setting OAUTH_CLIENT_ID enables end-user sign-in. Without it, specialist
# routing can still use the service identity, but Journey tools reject access.
OAUTH_CLIENT_ID = os.environ.get("OAUTH_CLIENT_ID", "").strip()
ALLOWED_USER_DOMAINS = {
    domain.strip().lower().lstrip("@")
    for domain in os.environ.get("ALLOWED_USER_DOMAINS", "").split(",")
    if domain.strip()
}
# The signed-in user token for the current request. It is forwarded separately
# from the service identity used for the downstream Cloud Run IAM check.
_user_token: contextvars.ContextVar[str] = contextvars.ContextVar(
    "orchestrator_user_token", default=""
)


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=8000)
    session_id: str | None = Field(
        default=None,
        min_length=16,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )


class QueryResponse(BaseModel):
    answer: str
    session_id: str


def _verify_user(bearer: str | None) -> dict[str, Any]:
    """Validate a Google Identity Services ID token from the browser."""

    raw = (bearer or "").strip()
    token = raw[7:].strip() if raw[:7].lower() == "bearer " else raw
    if not token:
        raise HTTPException(status_code=401, detail="Sign-in required.")

    try:
        info = id_token.verify_oauth2_token(token, Request(), OAUTH_CLIENT_ID)
    except TransportError as exc:
        raise HTTPException(
            status_code=503, detail=f"Could not verify sign-in: {exc}"
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=401, detail="Sign-in token is not valid."
        ) from exc

    if info.get("aud") != OAUTH_CLIENT_ID:
        raise HTTPException(
            status_code=401,
            detail="Sign-in token was issued for another application.",
        )
    if info.get("email_verified") not in ("true", True):
        raise HTTPException(
            status_code=401, detail="Google account email is unverified."
        )

    email = str(info.get("email") or "").strip().lower()
    subject = str(info.get("sub") or "").strip()
    if not subject:
        raise HTTPException(status_code=401, detail="Sign-in token has no subject.")
    if not email or "@" not in email:
        raise HTTPException(status_code=401, detail="Sign-in token has no valid email.")
    if ALLOWED_USER_DOMAINS and email.rsplit("@", 1)[-1] not in ALLOWED_USER_DOMAINS:
        raise HTTPException(
            status_code=403,
            detail=(
                f"{email} signed in successfully but is outside the permitted "
                "domains for this application."
            ),
        )

    _user_token.set(token)
    return {
        "subject": subject,
        "email": email,
        "name": str(info.get("name") or ""),
    }


def _call_agent(base_url: str, path: str, label: str, query: str) -> dict[str, Any]:
    """POST a query to a private downstream agent as this service account."""

    if not base_url:
        return {"error": f"{label} is not configured on the Orchestrator."}

    try:
        service_token = id_token.fetch_id_token(Request(), base_url)
        headers = {"Authorization": f"Bearer {service_token}"}

        # Authorization authenticates this service to Cloud Run. The user's ID
        # token travels in a distinct header so the two identities do not clash.
        user_token = _user_token.get()
        if user_token:
            headers["X-User-Authorization"] = f"Bearer {user_token}"

        response = httpx.post(
            f"{base_url}{path}",
            json={"query": query},
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        return {"error": f"{label} request failed: {exc}"}

    if isinstance(payload, dict):
        return payload
    return {"result": payload}


def query_asset_inventory(query: str) -> dict[str, Any]:
    """Ask the Asset Inventory Agent about Google Cloud infrastructure.

    Use this for what is deployed or running in the GCP project: Cloud Run
    services, VMs, storage buckets, databases and instances, IAM principals,
    service accounts, networks, and asset counts by type.
    """

    return _call_agent(
        INVENTORY_AGENT_URL,
        "/v1/inventory/query",
        "Asset Inventory Agent",
        query,
    )


def query_apm(query: str) -> dict[str, Any]:
    """Ask the APM Agent about the application portfolio.

    Use this for APM IDs, application names, owners, business units,
    environments, criticality, support groups, on-call details, SLAs,
    application dependencies, and operational incidents.
    """

    return _call_agent(APM_AGENT_URL, "/v1/apm/query", "APM Agent", query)


root_agent = Agent(
    name="orchestrator_agent",
    model=MODEL,
    description=(
        "Routes APM and cloud-infrastructure questions and owns the durable "
        "Cloud Journey lifecycle."
    ),
    instruction=(
        "You are the main Cloud orchestration agent. You route domain questions "
        "to specialist agents and directly coordinate the durable Cloud Journey "
        "lifecycle. Specialist facts live behind their tools; authoritative "
        "Journey state lives in PostgreSQL behind the Journey tools.\n\n"
        "SPECIALIST ROUTING RULES\n"
        "- query_asset_inventory: Google Cloud infrastructure. Use it for what "
        "is deployed or running in the cloud project: Cloud Run services, VMs, "
        "buckets, database instances, IAM bindings, service accounts, networks, "
        "and asset counts.\n"
        "- query_apm: the application portfolio. Use it for business applications "
        "and their records: APM IDs (APM######), names, owners, business units, "
        "environments, criticality, support groups, on-call details, SLAs, "
        "dependencies, and incidents (INC-####).\n\n"
        "DECIDING BETWEEN THEM\n"
        "The distinction is the application catalogue versus the cloud project. "
        "'Who owns the Order Router?' is APM. 'Which Cloud Run services are "
        "running?' is inventory. A Cloud SQL instance is infrastructure; the "
        "application that uses it is APM.\n"
        "- If a question needs both (for example, 'which cloud resources back "
        "APM001234?'), call both tools and combine the results, identifying the "
        "source of each part.\n"
        "- If routing is genuinely ambiguous, ask one short clarifying question.\n"
        "- If a question needs neither specialist and is not about a Journey, "
        "answer briefly without a tool call.\n\n"
        "REPORTING\n"
        "Never claim infrastructure or application facts from memory. Preserve "
        "each agent's evidence rather than summarizing it away, and report tool "
        "errors clearly. When an answer is based on the sample catalogue, retain "
        "that caveat.\n\n"
        + DURABLE_JOURNEY_INSTRUCTION
    ),
    tools=[query_asset_inventory, query_apm, *DURABLE_JOURNEY_TOOLS],
)


# Conversation state is process-local and improves multi-turn interaction. The
# Journey itself remains recoverable from PostgreSQL by Journey ID or APM ID.
session_service = InMemorySessionService()
runner = Runner(
    agent=root_agent,
    app_name=APP_NAME,
    session_service=session_service,
)


def _get_or_create_session(
    user_id: str,
    requested_session_id: str | None,
    initial_state: dict[str, str] | None = None,
) -> Session:
    """Reuse a live ADK session or recreate it after a process restart."""

    session_id = requested_session_id or uuid4().hex
    session = session_service.get_session_sync(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=session_id,
    )
    if session is not None:
        if initial_state is None or all(
            session.state.get(key) == value for key, value in initial_state.items()
        ):
            return session
        # Trusted claims changed or are missing. Recreate only this process-local
        # conversation; the PostgreSQL Journey remains unaffected and recoverable.
        session_service.delete_session_sync(
            app_name=APP_NAME,
            user_id=user_id,
            session_id=session_id,
        )
    return session_service.create_session_sync(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=session_id,
        state=initial_state,
    )


app = FastAPI(title="Cloud Journey Orchestrator", version="2.0.0")


@app.get("/health")
@app.get("/healthz")
def health() -> dict[str, str]:
    """Return a lightweight liveness response."""

    return {"status": "ok"}


PLAYGROUND_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Cloud Journey Orchestrator</title>
  <style>
    :root { color-scheme: light; font-family: Georgia, serif; background: #f3f0e9; color: #17312b; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; }
    main { width: min(900px, 92vw); padding: 36px; box-sizing: border-box; }
    h1 { font-size: clamp(2rem, 5vw, 4.5rem); line-height: .95; max-width: 680px; margin: 0 0 14px; }
    p { font-family: system-ui, sans-serif; color: #53645e; }
    textarea { width: 100%; min-height: 120px; box-sizing: border-box; border: 1px solid #aab9ae; background: #fffdf8; padding: 16px; font: 1rem system-ui, sans-serif; }
    button { margin-top: 12px; border: 0; background: #17312b; color: #fffdf8; padding: 10px 18px; cursor: pointer; font: 600 .9rem system-ui, sans-serif; }
    button:disabled { opacity: .55; cursor: wait; }
    .examples { margin-top: 18px; display: flex; flex-wrap: wrap; gap: 8px; }
    .examples button { margin: 0; background: transparent; border: 1px solid #aab9ae; color: #17312b; padding: 7px 12px; font: 500 .8rem system-ui, sans-serif; }
    .examples button:hover { border-color: #17312b; }
    .examples .tag { font-weight: 700; color: #65766f; margin-right: 6px; }
    .who { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 22px; min-height: 40px; }
    .who .signed { font: .85rem system-ui, sans-serif; color: #17312b; background: #e7ece7; border: 1px solid #aab9ae; padding: 7px 12px; }
    .who button.signout { margin: 0; background: transparent; border: 1px solid #aab9ae; color: #65766f; padding: 6px 11px; font: 500 .78rem system-ui, sans-serif; }
    .gate { opacity: .4; pointer-events: none; }
    pre { white-space: pre-wrap; background: #17312b; color: #f5f1e8; padding: 20px; margin-top: 26px; min-height: 90px; font: .95rem/1.5 ui-monospace, monospace; }
    .status { font: .8rem system-ui, sans-serif; color: #65766f; }
  </style>
</head>
<body>
  <main>
    <div class="status">SCHWAB CLOUD JOURNEY ORCHESTRATOR</div>
    <div class="who" id="who"></div>
    <h1>Ask about the estate.</h1>
    <p>The Orchestrator owns the durable Journey lifecycle and consults the <strong>Asset Inventory Agent</strong> and <strong>APM Agent</strong> for specialist facts.</p>
    <textarea id="query">Start a durable Cloud Journey for APM 100401.</textarea>
    <button id="send">Run query</button>
    <div class="examples">
      <button data-q="Start a durable Cloud Journey for APM 100401."><span class="tag">JOURNEY</span>start</button>
      <button data-q="Show the durable status and history for APM 100401."><span class="tag">JOURNEY</span>status</button>
      <button data-q="Who owns APM001234, and what incidents does it have?"><span class="tag">APM</span>owner + incidents</button>
      <button data-q="List every tier-1 application and its support group."><span class="tag">APM</span>tier-1 portfolio</button>
      <button data-q="Which applications does the Client Portal Gateway depend on?"><span class="tag">APM</span>dependencies</button>
      <button data-q="List all Cloud Run services in projects/schwab-agent-poc."><span class="tag">INFRA</span>Cloud Run services</button>
      <button data-q="What storage buckets exist in this project?"><span class="tag">INFRA</span>buckets</button>
      <button data-q="Which cloud resources back APM001234?"><span class="tag">BOTH</span>cross-domain</button>
    </div>
    <pre id="result">Ready.</pre>
  </main>
  <script src="https://accounts.google.com/gsi/client" async defer></script>
  <script>
    const CLIENT_ID = __CLIENT_ID_JSON__;
    const who = document.getElementById('who');
    const queryInput = document.getElementById('query');
    const send = document.getElementById('send');
    const result = document.getElementById('result');
    let userIdToken = null;
    let sessionId = sessionStorage.getItem('orchestratorSessionId');

    function gate(enabled) {
      send.disabled = !enabled;
      document.querySelector('.examples').classList.toggle('gate', !enabled);
      queryInput.disabled = !enabled;
    }

    function signedIn(token, profile) {
      userIdToken = token;
      who.innerHTML = '';
      const tag = document.createElement('span');
      tag.className = 'signed';
      tag.innerHTML = 'Signed in as <b></b>';
      tag.querySelector('b').textContent = profile.email || 'Google user';
      const signOut = document.createElement('button');
      signOut.className = 'signout';
      signOut.textContent = 'Sign out';
      signOut.addEventListener('click', () => {
        google.accounts.id.disableAutoSelect();
        userIdToken = null;
        sessionId = null;
        sessionStorage.removeItem('orchestratorSessionId');
        renderSignIn();
      });
      who.append(tag, signOut);
      gate(true);
    }

    function decodeClaims(credential) {
      const encoded = credential.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
      const padded = encoded.padEnd(Math.ceil(encoded.length / 4) * 4, '=');
      return JSON.parse(decodeURIComponent(Array.from(atob(padded), c =>
        '%' + c.charCodeAt(0).toString(16).padStart(2, '0')).join('')));
    }

    function renderSignIn() {
      who.innerHTML = '';
      const slot = document.createElement('div');
      who.append(slot);
      gate(false);
      google.accounts.id.initialize({
        client_id: CLIENT_ID,
        callback: response => signedIn(response.credential, decodeClaims(response.credential))
      });
      google.accounts.id.renderButton(slot, {theme: 'outline', size: 'large', text: 'signin_with'});
      google.accounts.id.prompt();
    }

    window.addEventListener('load', () => {
      if (!CLIENT_ID) {
        who.innerHTML = '<span class="signed">Sign-in is not configured; requests run as the service.</span>';
        gate(true);
        return;
      }
      const ready = setInterval(() => {
        if (window.google && google.accounts && google.accounts.id) {
          clearInterval(ready);
          renderSignIn();
        }
      }, 100);
    });

    document.querySelectorAll('.examples button').forEach(button => {
      button.addEventListener('click', () => {
        queryInput.value = button.dataset.q;
        queryInput.focus();
      });
    });

    send.addEventListener('click', async () => {
      send.disabled = true;
      result.textContent = 'Querying the Orchestrator...';
      try {
        const headers = {'Content-Type': 'application/json'};
        if (userIdToken) headers['X-User-Authorization'] = 'Bearer ' + userIdToken;
        const requestBody = {query: queryInput.value};
        if (sessionId) requestBody.session_id = sessionId;
        const response = await fetch('/v1/query', {
          method: 'POST',
          headers,
          body: JSON.stringify(requestBody)
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
        sessionId = data.session_id;
        sessionStorage.setItem('orchestratorSessionId', sessionId);
        result.textContent = data.answer;
      } catch (error) {
        result.textContent = `Request failed: ${error.message}`;
      } finally {
        send.disabled = false;
      }
    });
  </script>
</body>
</html>
"""


@app.get("/playground", response_class=HTMLResponse)
def playground() -> str:
    """Serve a small same-origin UI for exercising the orchestrator."""

    return PLAYGROUND_HTML.replace("__CLIENT_ID_JSON__", json.dumps(OAUTH_CLIENT_ID))


@app.post("/v1/query", response_model=QueryResponse)
def query(
    request: QueryRequest,
    x_user_authorization: str | None = Header(default=None),
) -> QueryResponse:
    """Route one user query through the ADK orchestrator."""

    context_token = _user_token.set("")
    try:
        user_id = "service-client"
        trusted_state = None
        if OAUTH_CLIENT_ID:
            user = _verify_user(x_user_authorization)
            user_id = user["subject"]
            trusted_state = verified_identity_state(user)

        session = _get_or_create_session(
            user_id, request.session_id, initial_state=trusted_state
        )
        message = types.Content(
            role="user", parts=[types.Part.from_text(text=request.query)]
        )

        text_parts: list[str] = []
        for event in runner.run(
            user_id=user_id,
            session_id=session.id,
            new_message=message,
        ):
            if not event.is_final_response() or not event.content:
                continue
            text_parts.extend(
                part.text for part in (event.content.parts or []) if part.text
            )

        if not text_parts:
            raise HTTPException(status_code=502, detail="Orchestrator returned no text.")
        return QueryResponse(answer="\n".join(text_parts), session_id=session.id)
    finally:
        _user_token.reset(context_token)
