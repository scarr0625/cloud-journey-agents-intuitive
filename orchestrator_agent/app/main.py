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

from .cloud_journey.capability import (
    DURABLE_JOURNEY_INSTRUCTION,
    DURABLE_JOURNEY_TOOLS,
)
from .cloud_journey.identity import verified_identity_state
from .cloud_journey.state_machine import JourneyError
from .cloud_journey.tools import ApmAccessDenied, get_service

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


PLAYGROUND_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Cloud Journey Orchestrator</title>
  <style>
    :root {
      color-scheme: dark;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #020f17;
      color: #edf6ff;
      --canvas: #020f17;
      --panel: #0c1829;
      --panel-2: #111f32;
      --line: #28465e;
      --muted: #8ca4bd;
      --blue: #3b9be3;
      --blue-2: #183b5d;
      --amber: #f4b315;
      --green: #30c49d;
      --red: #f26d6d;
    }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; background: radial-gradient(circle at 50% -20%, #07344b 0, var(--canvas) 42%); }
    button, input, textarea { font: inherit; }
    button { border: 0; cursor: pointer; }
    button:disabled { opacity: .45; cursor: not-allowed; }
    a { color: #8cc8f4; }
    .shell { min-height: 100vh; }
    .topbar { height: 68px; padding: 0 max(22px, calc((100vw - 1190px) / 2)); display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid #183248; background: rgba(2, 15, 23, .88); backdrop-filter: blur(14px); position: sticky; top: 0; z-index: 20; }
    .brand { display: flex; align-items: center; gap: 10px; font-weight: 750; letter-spacing: -.01em; }
    .cloud-mark { width: 34px; height: 34px; display: grid; place-items: center; color: #78c7ff; border: 1px solid #245576; border-radius: 10px; background: linear-gradient(145deg, #0e3955, #092037); }
    .cloud-mark.large { width: 66px; height: 66px; border-radius: 18px; font-size: 30px; margin: 0 auto 22px; }
    .who { display: flex; align-items: center; gap: 10px; min-height: 40px; }
    .who .signed { color: #c9d9e8; font-size: .8rem; padding: 7px 11px; border: 1px solid var(--line); border-radius: 18px; background: #0c1c2b; }
    .signout, .ghost, .secondary { color: #dcecff; background: transparent; border: 1px solid #3a5871; border-radius: 8px; padding: 9px 13px; font-weight: 650; }
    .primary { color: white; background: linear-gradient(180deg, #3b9be3, #2479b9); border: 1px solid #55adf0; border-radius: 8px; padding: 10px 15px; font-weight: 700; box-shadow: 0 8px 24px rgba(27, 123, 190, .18); }
    .page { width: min(1190px, calc(100vw - 40px)); margin: 0 auto; padding: 38px 0 70px; }
    .eyebrow { margin: 0 0 10px; color: #7f99b3; font-size: .72rem; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }
    h1, h2, h3, p { margin-top: 0; }
    h1 { margin-bottom: 8px; font-size: clamp(1.55rem, 3vw, 2.1rem); letter-spacing: -.025em; }
    h2 { font-size: 1.1rem; }
    p { color: #a9bdd1; line-height: 1.55; }
    .hidden { display: none !important; }
    .auth-view { min-height: calc(100vh - 150px); display: grid; place-items: center; text-align: center; }
    .auth-card { width: min(660px, 100%); }
    .auth-card h1 { font-size: clamp(2rem, 5vw, 3.1rem); }
    .auth-card p { max-width: 560px; margin: 0 auto 25px; }
    .auth-status { color: #91a9c0; font-size: .86rem; min-height: 22px; margin: 16px 0; }
    .mock-composer { width: min(820px, 100%); min-height: 112px; margin-top: 54px; border: 1px solid var(--line); border-radius: 15px; background: var(--panel); opacity: .42; padding: 18px; text-align: left; color: var(--muted); }
    .workspace-head { display: flex; justify-content: space-between; gap: 20px; align-items: flex-start; margin-bottom: 24px; }
    .workspace-head p { margin: 0; }
    .tabbar { display: flex; gap: 5px; margin-bottom: 28px; border-bottom: 1px solid #163146; }
    .tab { padding: 12px 14px; background: transparent; color: var(--muted); border-bottom: 2px solid transparent; font-weight: 700; }
    .tab.active { color: white; border-bottom-color: var(--blue); }
    .panel { background: rgba(12, 24, 41, .96); border: 1px solid var(--line); border-radius: 14px; }
    .empty { text-align: center; padding-top: 16px; }
    .empty > p { max-width: 720px; margin: 0 auto 20px; }
    .steps-card { max-width: 900px; margin: 56px auto 20px; padding: 22px; text-align: left; }
    .stepper { display: grid; grid-template-columns: repeat(5, 1fr); margin-top: 24px; }
    .step { position: relative; text-align: center; min-width: 0; }
    .step:not(:last-child)::after { content: ""; position: absolute; height: 2px; left: calc(50% + 17px); right: calc(-50% + 17px); top: 16px; background: #385974; }
    .step.done:not(:last-child)::after { background: var(--blue); }
    .step-dot { width: 32px; height: 32px; margin: 0 auto 10px; display: grid; place-items: center; border: 2px solid #41627c; border-radius: 50%; background: #102034; color: #91a8bf; font-size: .8rem; font-weight: 800; position: relative; z-index: 1; }
    .step.current .step-dot { border-color: var(--amber); color: #ffe082; box-shadow: 0 0 0 4px rgba(244, 179, 21, .13); }
    .step.done .step-dot { border-color: var(--blue); background: #287fb9; color: white; }
    .step strong { display: block; font-size: .77rem; color: #e7f1fb; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .step small { color: #7892ab; font-size: .7rem; }
    .checklist { max-width: 900px; margin: 18px auto 0; padding: 20px; text-align: left; }
    .check-row { display: flex; gap: 14px; padding: 15px; margin-top: 10px; border: 1px solid #2d4b63; background: #142b47; border-radius: 10px; }
    .number { width: 27px; height: 27px; flex: 0 0 auto; display: grid; place-items: center; border-radius: 50%; background: #2d8ac7; font-weight: 800; font-size: .8rem; }
    .check-row strong { font-size: .84rem; }
    .check-row small { display: block; color: #8fa9c4; margin-top: 3px; }
    .progress-card { padding: 18px 16px 16px; margin-bottom: 20px; }
    .progress-top { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
    .notice { display: flex; align-items: center; gap: 15px; border-left: 4px solid var(--amber); padding: 16px; margin: 18px 0; background: #101d1d; }
    .notice-icon { width: 38px; height: 38px; display: grid; place-items: center; color: var(--amber); background: #3b3209; border-radius: 9px; }
    .notice strong, .notice small { display: block; }
    .notice small { color: var(--muted); margin-top: 3px; }
    .table-wrap { overflow: auto; }
    table { width: 100%; border-collapse: collapse; font-size: .82rem; }
    th { color: #8098b1; text-align: left; font-size: .68rem; text-transform: uppercase; letter-spacing: .06em; }
    th, td { padding: 13px 12px; border-bottom: 1px solid #29455b; }
    tr:last-child td { border-bottom: 0; }
    .status-pill { display: inline-flex; padding: 5px 9px; border-radius: 14px; color: #8ecbfa; background: #102d47; font-size: .7rem; }
    .status-pill.amber { color: #ffc94f; background: #3a3214; }
    .status-pill.green { color: #42d4ac; background: #0c3935; }
    .status-pill.red { color: #ff8a8a; background: #3a2531; }
    .detail-layout { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 20px; }
    .phase-list { padding: 16px; }
    .phase-item { position: relative; padding: 15px 0 18px 40px; border-bottom: 1px solid #29455b; }
    .phase-item.current { margin: 4px -8px; padding-left: 48px; padding-right: 8px; border-radius: 10px; background: rgba(59, 155, 227, .07); }
    .phase-item.current .phase-bullet { left: 8px; }
    .phase-item:last-child { border-bottom: 0; }
    .phase-bullet { position: absolute; left: 0; top: 13px; width: 27px; height: 27px; display: grid; place-items: center; border: 2px solid #456882; border-radius: 50%; color: #8da7bf; font-size: .72rem; font-weight: 800; }
    .phase-item.done .phase-bullet { background: #2c87c3; color: white; border-color: #2c87c3; }
    .phase-item.current .phase-bullet { color: #ffd15b; border-color: var(--amber); }
    .phase-item h3 { margin: 0 0 6px; font-size: .88rem; }
    .phase-item p { margin: 0; font-size: .79rem; }
    .phase-state { position: absolute; right: 0; top: 13px; }
    .subtasks { margin-top: 15px; padding: 14px; background: #132136; border: 1px solid #2c4a62; border-radius: 10px; }
    .subtask { display: flex; justify-content: space-between; gap: 12px; padding: 10px 0; border-bottom: 1px solid #2d465b; font-size: .78rem; }
    .subtask:last-child { border-bottom: 0; }
    .side-stack { display: grid; gap: 18px; align-content: start; }
    .side-card { padding: 16px; }
    .compass-callout { border-left: 3px solid var(--blue); background: #14253a; padding: 13px; border-radius: 9px; color: #e5f2ff; font-size: .8rem; line-height: 1.5; }
    .timeline-item { padding: 11px 0; border-bottom: 1px solid #29455b; }
    .timeline-item:last-child { border-bottom: 0; }
    .timeline-item strong, .timeline-item small { display: block; font-size: .76rem; }
    .timeline-item small { color: var(--muted); margin-top: 4px; }
    .progress-update { margin: 0 0 18px; padding: 12px 15px; border: 1px solid #246a91; border-left: 4px solid var(--blue); border-radius: 9px; background: #0c2738; color: #cfeaff; font-size: .8rem; }
    dialog { width: min(470px, calc(100vw - 30px)); padding: 0; color: inherit; background: var(--panel); border: 1px solid #36566f; border-radius: 14px; box-shadow: 0 30px 100px #000; }
    dialog::backdrop { background: rgba(0, 8, 14, .78); backdrop-filter: blur(3px); }
    .dialog-body { padding: 24px; }
    .dialog-actions { display: flex; justify-content: flex-end; gap: 9px; margin-top: 20px; }
    input { width: 100%; color: white; background: #071521; border: 1px solid #36566f; border-radius: 8px; padding: 12px; outline: none; }
    input:focus, textarea:focus { border-color: var(--blue); box-shadow: 0 0 0 3px rgba(59, 155, 227, .13); }
    label { display: block; color: #b9cadb; font-size: .8rem; font-weight: 700; margin-bottom: 8px; }
    .compass-panel { position: fixed; right: 18px; bottom: 18px; width: min(470px, calc(100vw - 36px)); height: min(680px, calc(100vh - 96px)); display: grid; grid-template-rows: auto 1fr auto; background: #071725; border: 1px solid #31526a; border-radius: 18px; box-shadow: 0 30px 90px rgba(0,0,0,.55); z-index: 40; overflow: hidden; transform: translateY(16px); opacity: 0; pointer-events: none; transition: .2s ease; }
    .compass-panel.open { transform: none; opacity: 1; pointer-events: auto; }
    .chat-head { display: flex; align-items: center; justify-content: space-between; padding: 14px 16px; border-bottom: 1px solid #244158; }
    .chat-title strong, .chat-title small { display: block; }
    .chat-title small { margin-top: 3px; color: #7fa1bb; font-size: .68rem; font-weight: 650; }
    .chat-head button { background: transparent; color: #9fb4c7; font-size: 1.2rem; }
    .messages { overflow: auto; padding: 18px; }
    .message { max-width: 90%; margin-bottom: 14px; padding: 11px 13px; border-radius: 12px; white-space: pre-wrap; font-size: .82rem; line-height: 1.5; }
    .message.agent { background: #13283d; border-left: 3px solid var(--blue); }
    .message.user { margin-left: auto; background: #1d5681; }
    .interaction { border: 0; margin: 0; padding: 12px; min-width: 0; border-top: 1px solid #244158; }
    .interaction:disabled { opacity: .42; }
    .composer { display: grid; grid-template-columns: 1fr auto; gap: 8px; }
    textarea { width: 100%; min-height: 58px; max-height: 140px; resize: vertical; color: white; background: #0c1b2d; border: 1px solid #36566f; border-radius: 10px; padding: 11px; outline: none; }
    .send { width: 44px; height: 44px; align-self: end; border-radius: 50%; color: white; background: #2f94d5; font-size: 1rem; }
    .quick-actions { display: flex; gap: 6px; overflow-x: auto; padding-top: 8px; }
    .quick-actions button { flex: 0 0 auto; color: #a9c1d8; background: transparent; border: 1px solid #294b64; border-radius: 15px; padding: 5px 9px; font-size: .68rem; }
    .fab { position: fixed; right: 24px; bottom: 24px; z-index: 30; width: 56px; height: 56px; border-radius: 50%; background: linear-gradient(180deg, #42a9ed, #2479ba); color: white; box-shadow: 0 14px 35px #000; font-size: 22px; }
    .loading { display: inline-block; width: 13px; height: 13px; border: 2px solid #51718b; border-top-color: white; border-radius: 50%; animation: spin .8s linear infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }
    @media (max-width: 780px) {
      .page { width: min(100% - 24px, 1190px); padding-top: 24px; }
      .detail-layout { grid-template-columns: 1fr; }
      .step strong { font-size: .64rem; }
      .step small { display: none; }
      .workspace-head { display: block; }
      .workspace-head .primary { margin-top: 15px; }
      th:nth-child(4), td:nth-child(4) { display: none; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <header class="topbar">
      <div class="brand"><span class="cloud-mark" aria-hidden="true">☁</span> Cloud Compass</div>
      <div class="who" id="who"></div>
    </header>
    <main class="page">
      <section class="auth-view" id="auth-view">
        <div class="auth-card">
          <div class="cloud-mark large" aria-hidden="true">☁</div>
          <h1>Hi, I'm Cloud Compass</h1>
          <p>Your guided path from application discovery through governance, deployment, go-live, and Day-2 operations.</p>
          <div class="auth-status" id="auth-status" role="status">Sign in with Google to begin.</div>
          <div class="mock-composer">Ask me anything…</div>
        </div>
      </section>
      <section id="workspace" class="hidden">
        <nav class="tabbar" aria-label="Workspace">
          <button class="tab active" data-view="portfolio">Portfolio</button>
          <button class="tab" data-view="journey">Journey detail</button>
        </nav>
        <div id="screen"></div>
      </section>
    </main>
  </div>

  <dialog id="link-dialog">
    <form class="dialog-body" id="link-form">
      <p class="eyebrow">Start a Cloud Journey</p>
      <h2>Link an existing APM ID</h2>
      <p>Cloud Compass will verify your group access, recover an existing Journey, or start a new durable Journey.</p>
      <label for="apm-input">APM ID</label>
      <input id="apm-input" name="apm_id" placeholder="e.g. 100401" maxlength="64" required autocomplete="off">
      <div class="dialog-actions">
        <button type="button" class="secondary" id="cancel-link">Cancel</button>
        <button type="submit" class="primary" id="link-submit">Link APM ID →</button>
      </div>
    </form>
  </dialog>

  <button class="fab hidden" id="compass-fab" aria-label="Open Cloud Compass">☁</button>
  <aside class="compass-panel" id="compass-panel" aria-label="Cloud Compass agent">
    <div class="chat-head">
      <div class="chat-title"><strong>☁ &nbsp;Cloud Compass</strong><small id="chat-context">Portfolio conversation</small></div>
      <button id="close-chat" aria-label="Close agent">×</button>
    </div>
    <div class="messages" id="messages" aria-live="polite"></div>
    <fieldset class="interaction" id="interaction" disabled>
      <div class="composer">
        <textarea id="query" placeholder="Ask about your Cloud Journey…"></textarea>
        <button class="send" id="send" aria-label="Send query">➤</button>
      </div>
      <div class="quick-actions">
        <button type="button" data-q="Show the current durable Journey status and history.">Journey status</button>
        <button type="button" data-q="What information do you need from me next for this Journey?">What's next?</button>
        <button type="button" data-q="Explain the current governance and approval status.">Explain governance</button>
        <button type="button" data-q="Which cloud resources back this application?">Cloud resources</button>
      </div>
    </fieldset>
  </aside>
  <script src="https://accounts.google.com/gsi/client" async defer></script>
  <script>
    const CLIENT_ID = __CLIENT_ID_JSON__;
    const PHASES = [
      {name: 'Journey Start', detail: 'Discovery'},
      {name: 'Governance', detail: 'SAD · SDR · ARB'},
      {name: 'Deployment', detail: 'App Factory'},
      {name: 'CELT, Go Live', detail: 'Validation'},
      {name: 'BAU', detail: 'Day-2 ops'}
    ];
    const STATE_PHASE = {
      CREATED: 0, VALIDATING_APM: 0, APM_VALIDATED: 0,
      DISCOVERING_CLOUD_SERVICES: 0, COLLECTING_ASSET_INVENTORY: 0,
      ASSET_INVENTORY_COMPLETE: 0, GENERATING_PLAN: 1,
      WAITING_FOR_APPROVAL: 1, APPROVED: 1, REJECTED: 1,
      PROVISIONING_AGENT_IDENTITY: 2, AGENT_IDENTITY_READY: 2,
      PREPARING_APP_FACTORY: 2, APP_FACTORY_READY: 2,
      SUBMITTING_CLOUD_BUILD: 2, CLOUD_BUILD_RUNNING: 2,
      VALIDATING_DEPLOYMENT: 3, COMPLETED: 4
    };
    const who = document.getElementById('who');
    const authStatus = document.getElementById('auth-status');
    const authView = document.getElementById('auth-view');
    const workspace = document.getElementById('workspace');
    const screen = document.getElementById('screen');
    const interaction = document.getElementById('interaction');
    const queryInput = document.getElementById('query');
    const send = document.getElementById('send');
    const messagesEl = document.getElementById('messages');
    const chatContext = document.getElementById('chat-context');
    const compassPanel = document.getElementById('compass-panel');
    const compassFab = document.getElementById('compass-fab');
    const linkDialog = document.getElementById('link-dialog');
    const linkForm = document.getElementById('link-form');
    const apmInput = document.getElementById('apm-input');
    const linkSubmit = document.getElementById('link-submit');
    let userIdToken = null;
    let userProfile = null;
    let knownApms = [];
    let activeApm = null;
    let currentView = 'portfolio';
    let chatSessions = {};
    let progressTimer = null;
    const journeys = new Map();
    const journeyUpdates = new Map();

    const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[char]);
    const prettyState = value => String(value || 'Not started')
      .toLowerCase().replaceAll('_', ' ').replace(/\b\w/g, char => char.toUpperCase());
    const displayApm = value => /^\d+$/.test(value) ? `APM${value}` : value;
    const appName = journey => journey?.context?.inventory?.application_name || `Application ${displayApm(journey?.apm_id || '')}`;
    const storageKey = () => `cloudCompassApms:${userProfile?.sub || 'unknown'}`;
    const chatStorageKey = () => `cloudCompassChats:${userProfile?.sub || 'unknown'}`;
    const chatKey = apm => apm ? `apm:${apm}` : 'portfolio';

    function chatFor(apm = activeApm) {
      const key = chatKey(apm);
      if (!chatSessions[key]) {
        const introduction = apm
          ? `This conversation is only for ${displayApm(apm)}. Its context and agent session are separate from your other applications.`
          : 'Hi, I’m Cloud Compass. Link an APM ID or ask me about your application estate.';
        chatSessions[key] = {sessionId: null, messages: [{role: 'agent', text: introduction}]};
      }
      return chatSessions[key];
    }

    function saveChatSessions() {
      sessionStorage.setItem(chatStorageKey(), JSON.stringify(chatSessions));
    }

    function recordJourney(journey) {
      const previous = journeys.get(journey.apm_id);
      journeys.set(journey.apm_id, journey);
      if (previous && previous.current_state !== journey.current_state) {
        journeyUpdates.set(journey.apm_id, {
          from: previous.current_state,
          to: journey.current_state,
          at: new Date()
        });
      }
      return previous?.current_state !== journey.current_state;
    }

    function gate(enabled) {
      interaction.disabled = !enabled;
      compassFab.classList.toggle('hidden', !enabled);
    }

    function phaseIndex(journey) {
      if (!journey) return 0;
      if (journey.current_state === 'FAILED' || journey.current_state === 'RETRYING') {
        const path = journey.state_path || [];
        for (let index = path.length - 1; index >= 0; index--) {
          if (STATE_PHASE[path[index]] !== undefined) return STATE_PHASE[path[index]];
        }
      }
      return STATE_PHASE[journey.current_state] ?? 0;
    }

    function pillClass(state) {
      if (state === 'COMPLETED') return 'green';
      if (state === 'FAILED' || state === 'REJECTED') return 'red';
      if (state === 'WAITING_FOR_APPROVAL' || state === 'GENERATING_PLAN') return 'amber';
      return '';
    }

    function stepperMarkup(journey) {
      const current = phaseIndex(journey);
      const complete = journey?.current_state === 'COMPLETED';
      return `<div class="stepper">${PHASES.map((phase, index) => {
        const done = index < current || complete;
        const here = index === current && !complete;
        return `<div class="step ${done ? 'done' : ''} ${here ? 'current' : ''}">
          <div class="step-dot">${done ? '✓' : index + 1}</div>
          <strong>${escapeHtml(phase.name)}</strong><small>${escapeHtml(done ? 'Complete' : here ? prettyState(journey?.current_state) : phase.detail)}</small>
        </div>`;
      }).join('')}</div>`;
    }

    function renderEmpty() {
      screen.innerHTML = `<section class="empty">
        <div class="cloud-mark large" aria-hidden="true">☁</div>
        <h1>Welcome to your Cloud Journey</h1>
        <p>You don’t have any applications linked yet. Link an existing APM ID to take it from evaluation through governance and provisioning to Day-2 operations.</p>
        <button class="primary" data-action="link">Link an existing APM ID</button>
        <div class="steps-card panel"><p class="eyebrow">What happens next</p>${stepperMarkup(null)}</div>
        <div class="checklist panel">
          <p class="eyebrow">Before you start — have these ready</p>
          <div class="check-row"><span class="number">1</span><div><strong>Cost center & sponsoring office</strong><small>Needed for triage and the Cloud Front Door request</small></div></div>
          <div class="check-row"><span class="number">2</span><div><strong>An APM ID (or register a new product in APM)</strong><small>The system of record for the technology you’re onboarding</small></div></div>
          <div class="check-row"><span class="number">3</span><div><strong>Technology Fitness Assessment</strong><small>Input to the SAD and governance review</small></div></div>
        </div>
      </section>`;
    }

    function renderPortfolio() {
      const available = knownApms.map(apm => journeys.get(apm)).filter(Boolean);
      const focus = journeys.get(activeApm) || available[0];
      const blocked = focus && ['WAITING_FOR_APPROVAL', 'FAILED', 'REJECTED'].includes(focus.current_state);
      screen.innerHTML = `<div class="workspace-head"><div><h1>Portfolio Overview</h1><p>All applications you can access, and what needs your attention.</p></div><div><button class="secondary" data-action="refresh">Refresh progress</button> <button class="primary" data-action="link">+ Start a new Cloud Journey</button></div></div>
        ${focus ? `<section class="progress-card panel"><div class="progress-top"><p class="eyebrow">Journey progress — ${escapeHtml(displayApm(focus.apm_id))}</p><button class="secondary" data-action="open" data-apm="${escapeHtml(focus.apm_id)}">View full journey →</button></div>${stepperMarkup(focus)}</section>` : ''}
        ${blocked ? `<section class="notice panel"><span class="notice-icon">△</span><div><strong>${focus.current_state === 'WAITING_FOR_APPROVAL' ? 'Governance review is waiting for an external decision' : `Journey needs attention — ${prettyState(focus.current_state)}`}</strong><small>Cloud Compass can explain the durable status and recommended next action.</small></div><button class="secondary" data-action="ask-status">Ask Compass →</button></section>` : ''}
        <div class="progress-top"><p class="eyebrow">My applications</p></div>
        <div class="panel table-wrap"><table><thead><tr><th>APM ID</th><th>Application</th><th>Current phase</th><th>Durable state</th><th></th></tr></thead><tbody>
        ${knownApms.map(apm => {
          const journey = journeys.get(apm);
          if (!journey) return `<tr><td>${escapeHtml(displayApm(apm))}</td><td>Loading…</td><td>—</td><td><span class="loading"></span></td><td></td></tr>`;
          return `<tr><td>${escapeHtml(displayApm(apm))}</td><td>${escapeHtml(appName(journey))}</td><td><span class="status-pill ${pillClass(journey.current_state)}">${escapeHtml(PHASES[phaseIndex(journey)].name)}</span></td><td>${escapeHtml(prettyState(journey.current_state))}</td><td><button class="secondary" data-action="open" data-apm="${escapeHtml(apm)}">Open →</button></td></tr>`;
        }).join('')}</tbody></table></div>`;
    }

    function phaseDescription(index, journey) {
      const inventory = journey?.context?.inventory;
      return [
        inventory ? `Application discovery captured for ${inventory.application_name}.` : 'Validate the APM record and capture application discovery facts.',
        journey?.current_state === 'WAITING_FOR_APPROVAL' ? 'The proposed plan is at the independent human approval boundary.' : 'Architecture, security, and local governance review.',
        'Provision agent identity, prepare App Factory, and run the simulated build.',
        'Validate deployment readiness and coordinate controlled go-live.',
        'Transition to supported Day-2 operations.'
      ][index];
    }

    function timelineMarkup(journey) {
      const history = (journey?.history || []).slice(-5).reverse();
      if (!history.length) return '<p>No durable events yet.</p>';
      return history.map(event => `<div class="timeline-item"><strong>${escapeHtml(event.message || prettyState(event.event_type))}</strong><small>${escapeHtml(event.actor_type)} · ${new Date(event.created_at).toLocaleString()}</small></div>`).join('');
    }

    function renderDetail() {
      const journey = journeys.get(activeApm);
      if (!journey) { renderPortfolio(); return; }
      const current = phaseIndex(journey);
      const update = journeyUpdates.get(activeApm);
      const phaseItems = PHASES.map((phase, index) => {
        const done = index < current || journey.current_state === 'COMPLETED';
        const here = index === current && journey.current_state !== 'COMPLETED';
        const governance = index === 1 && here ? `<div class="subtasks">
          <div class="subtask"><span>Design / SAD</span><span class="status-pill green">Captured</span></div>
          <div class="subtask"><span>Security / SDR</span><span class="status-pill ${journey.current_state === 'WAITING_FOR_APPROVAL' ? 'green' : ''}">${journey.current_state === 'WAITING_FOR_APPROVAL' ? 'Ready' : 'Upcoming'}</span></div>
          <div class="subtask"><span>Architecture Review Board</span><span class="status-pill amber">${journey.current_state === 'WAITING_FOR_APPROVAL' ? 'In review' : 'Upcoming'}</span></div>
        </div>` : '';
        return `<div class="phase-item ${done ? 'done' : ''} ${here ? 'current' : ''}"><span class="phase-bullet">${done ? '✓' : index + 1}</span><h3>${index + 1} · ${escapeHtml(phase.name)}</h3><p>${escapeHtml(phaseDescription(index, journey))}</p><span class="phase-state status-pill ${here ? pillClass(journey.current_state) : ''}">${done ? 'Complete' : here ? escapeHtml(prettyState(journey.current_state)) : 'Upcoming'}</span>${governance}</div>`;
      }).join('');
      screen.innerHTML = `<div class="workspace-head"><div><h1>${escapeHtml(displayApm(journey.apm_id))} — ${escapeHtml(appName(journey))}</h1><p>Owner: ${escapeHtml(journey.requested_by_email)} · Group: ${escapeHtml(journey.access_group_id)} · <span class="status-pill ${pillClass(journey.current_state)}">${escapeHtml(prettyState(journey.current_state))}</span></p></div><div><button class="secondary" data-action="refresh">Refresh progress</button> <button class="primary" data-action="continue">Continue with Compass →</button></div></div>
        ${update ? `<div class="progress-update" role="status">Journey progress updated: <strong>${escapeHtml(prettyState(update.from))}</strong> → <strong>${escapeHtml(prettyState(update.to))}</strong></div>` : ''}
        <div class="detail-layout"><section class="phase-list panel"><p class="eyebrow">Journey phases</p>${phaseItems}</section>
        <aside class="side-stack"><section class="side-card panel"><div class="progress-top"><h2>☁ Cloud Compass</h2><span class="status-pill">Context-aware</span></div><div class="compass-callout">${journey.current_state === 'WAITING_FOR_APPROVAL' ? 'Your plan is waiting for independent approval. I can explain the governance boundary or check whether a decision has been recorded.' : `Your Journey is in ${prettyState(journey.current_state)}. I can gather missing details, explain blockers, and guide the next durable action.`}</div><button class="primary" data-action="continue">Ask Compass</button></section>
        <section class="side-card panel"><p class="eyebrow">Linked work</p><div class="timeline-item"><strong>${journey.current_state === 'WAITING_FOR_APPROVAL' ? 'Governance approval' : 'Cloud Journey workflow'}</strong><small>${escapeHtml(journey.journey_id)} · ${escapeHtml(prettyState(journey.current_state))}</small></div></section>
        <section class="side-card panel"><p class="eyebrow">Timeline</p>${timelineMarkup(journey)}</section></aside></div>`;
    }

    function renderScreen() {
      document.querySelectorAll('.tab').forEach(tab => tab.classList.toggle('active', tab.dataset.view === currentView));
      if (!knownApms.length) return renderEmpty();
      if (currentView === 'journey') return renderDetail();
      renderPortfolio();
    }

    function renderMessages() {
      const chat = chatFor();
      messagesEl.innerHTML = chat.messages.map(message => `<div class="message ${message.role}">${escapeHtml(message.text)}</div>`).join('');
      const journey = activeApm ? journeys.get(activeApm) : null;
      chatContext.textContent = activeApm
        ? `${displayApm(activeApm)} · ${journey ? appName(journey) : 'Application conversation'}`
        : 'Portfolio conversation';
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function openChat(prefill = '') {
      compassPanel.classList.add('open');
      compassFab.classList.add('hidden');
      if (prefill) queryInput.value = prefill;
      renderMessages();
      queryInput.focus();
    }

    function closeChat() {
      compassPanel.classList.remove('open');
      if (userIdToken) compassFab.classList.remove('hidden');
    }

    async function fetchJourney(apm) {
      const response = await fetch(`/v1/journeys/by-apm/${encodeURIComponent(apm)}`, {
        headers: {'X-User-Authorization': 'Bearer ' + userIdToken}
      });
      if (response.status === 404) return null;
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      return data;
    }

    async function refreshJourneys() {
      const portfolioChat = chatFor(null);
      try {
        const response = await fetch('/v1/journeys', {
          headers: {'X-User-Authorization': 'Bearer ' + userIdToken}
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
        for (const journey of data.journeys) {
          recordJourney(journey);
          if (!knownApms.includes(journey.apm_id)) knownApms.push(journey.apm_id);
        }
        if (!activeApm && knownApms.length) activeApm = knownApms[0];
        localStorage.setItem(storageKey(), JSON.stringify(knownApms));
      } catch (error) {
        portfolioChat.messages.push({role: 'agent', text: `Could not load your Journey portfolio: ${error.message}`});
      }
      await Promise.all(knownApms.map(async apm => {
        try {
          const journey = await fetchJourney(apm);
          if (journey) recordJourney(journey);
        } catch (error) {
          chatFor(apm).messages.push({role: 'agent', text: `Could not refresh ${displayApm(apm)}: ${error.message}`});
        }
      }));
      saveChatSessions();
      renderScreen();
      renderMessages();
    }

    async function refreshActiveJourney() {
      if (!userIdToken || !activeApm) return;
      try {
        const journey = await fetchJourney(activeApm);
        if (journey) {
          recordJourney(journey);
          renderScreen();
          renderMessages();
        }
      } catch (_error) {
        // Background refresh is best-effort; explicit actions report failures.
      }
    }

    async function runAgent(question, apm, chat) {
      const headers = {'Content-Type': 'application/json', 'X-User-Authorization': 'Bearer ' + userIdToken};
      const contextualQuestion = apm && !question.includes(apm) ? `${question}\n\nContext: APM ID ${apm}.` : question;
      const requestBody = {query: contextualQuestion};
      if (chat.sessionId) requestBody.session_id = chat.sessionId;
      const response = await fetch('/v1/query', {method: 'POST', headers, body: JSON.stringify(requestBody)});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      chat.sessionId = data.session_id;
      saveChatSessions();
      return data.answer;
    }

    async function sendQuery(question = queryInput.value.trim()) {
      if (!userIdToken || !question) return;
      const apm = activeApm;
      const chat = chatFor(apm);
      queryInput.value = '';
      chat.messages.push({role: 'user', text: question}, {role: 'agent', text: 'Working…'});
      saveChatSessions();
      openChat();
      send.disabled = true;
      try {
        const answer = await runAgent(question, apm, chat);
        chat.messages[chat.messages.length - 1] = {role: 'agent', text: answer};
        if (apm) {
          const journey = await fetchJourney(apm);
          if (journey) recordJourney(journey);
        }
        if (activeApm === apm) renderScreen();
      } catch (error) {
        chat.messages[chat.messages.length - 1] = {role: 'agent', text: `Request failed: ${error.message}`};
      } finally {
        send.disabled = false;
        saveChatSessions();
        if (activeApm === apm) renderMessages();
      }
    }

    async function linkApm(apm) {
      const normalized = apm.trim().replace(/^APM[- ]?/i, '');
      if (!normalized) return;
      linkSubmit.disabled = true;
      linkSubmit.innerHTML = '<span class="loading"></span> Linking';
      activeApm = normalized;
      const chat = chatFor(normalized);
      try {
        let journey = await fetchJourney(normalized);
        if (!journey) {
          linkDialog.close();
          chat.messages.push({role: 'agent', text: `I couldn’t find an existing Journey for ${displayApm(normalized)}. I’ll verify access and start one now.`});
          openChat();
          renderMessages();
          const answer = await runAgent(`Start a durable Cloud Journey for APM ${normalized}. Return its Journey ID and current durable state.`, normalized, chat);
          chat.messages.push({role: 'agent', text: answer});
          journey = await fetchJourney(normalized);
        }
        if (!journey) throw new Error('The Journey was not created. Ask Cloud Compass for details.');
        recordJourney(journey);
        if (!knownApms.includes(normalized)) knownApms.push(normalized);
        localStorage.setItem(storageKey(), JSON.stringify(knownApms));
        saveChatSessions();
        currentView = 'journey';
        linkDialog.close();
        renderScreen();
        renderMessages();
      } catch (error) {
        linkDialog.close();
        chat.messages.push({role: 'agent', text: `Could not link ${displayApm(normalized)}: ${error.message}`});
        saveChatSessions();
        openChat();
      } finally {
        linkSubmit.disabled = false;
        linkSubmit.textContent = 'Link APM ID →';
      }
    }

    function signedIn(token, profile) {
      userIdToken = token;
      userProfile = profile;
      try { knownApms = JSON.parse(localStorage.getItem(storageKey()) || '[]'); } catch { knownApms = []; }
      try { chatSessions = JSON.parse(sessionStorage.getItem(chatStorageKey()) || '{}'); } catch { chatSessions = {}; }
      activeApm = knownApms[0] || null;
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
        sessionStorage.removeItem(chatStorageKey());
        sessionStorage.removeItem('orchestratorSessionId');
        userIdToken = null;
        userProfile = null;
        knownApms = [];
        chatSessions = {};
        journeys.clear();
        journeyUpdates.clear();
        clearInterval(progressTimer);
        progressTimer = null;
        workspace.classList.add('hidden');
        authView.classList.remove('hidden');
        closeChat();
        renderSignIn();
      });
      who.append(tag, signOut);
      authView.classList.add('hidden');
      workspace.classList.remove('hidden');
      gate(true);
      renderScreen();
      renderMessages();
      refreshJourneys();
      clearInterval(progressTimer);
      progressTimer = setInterval(refreshActiveJourney, 10000);
    }

    function decodeClaims(credential) {
      const encoded = credential.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
      const padded = encoded.padEnd(Math.ceil(encoded.length / 4) * 4, '=');
      return JSON.parse(decodeURIComponent(Array.from(atob(padded), char =>
        '%' + char.charCodeAt(0).toString(16).padStart(2, '0')).join('')));
    }

    function renderSignIn() {
      who.innerHTML = '';
      const slot = document.createElement('div');
      who.append(slot);
      gate(false);
      authStatus.textContent = 'Sign in with Google to begin.';
      google.accounts.id.initialize({client_id: CLIENT_ID, callback: response => signedIn(response.credential, decodeClaims(response.credential))});
      google.accounts.id.renderButton(slot, {theme: 'filled_black', size: 'large', text: 'signin_with', shape: 'pill'});
      google.accounts.id.prompt();
    }

    window.addEventListener('load', () => {
      if (!CLIENT_ID) {
        who.innerHTML = '<span class="signed">Google sign-in is not configured.</span>';
        authStatus.textContent = 'Set OAUTH_CLIENT_ID to enable Google sign-in and Cloud Compass.';
        return;
      }
      let attempts = 0;
      const ready = setInterval(() => {
        if (window.google?.accounts?.id) { clearInterval(ready); renderSignIn(); }
        else if (++attempts >= 100) {
          clearInterval(ready);
          who.innerHTML = '<span class="signed">Google sign-in could not be loaded.</span>';
          authStatus.textContent = 'Reload the page to try Google sign-in again.';
        }
      }, 100);
    });

    screen.addEventListener('click', event => {
      const button = event.target.closest('button[data-action]');
      if (!button) return;
      if (button.dataset.action === 'link') { apmInput.value = ''; linkDialog.showModal(); setTimeout(() => apmInput.focus(), 50); }
      if (button.dataset.action === 'open') {
        activeApm = button.dataset.apm;
        currentView = 'journey';
        queryInput.value = '';
        renderScreen();
        renderMessages();
        refreshActiveJourney();
      }
      if (button.dataset.action === 'refresh') {
        if (currentView === 'journey') refreshActiveJourney();
        else refreshJourneys();
      }
      if (button.dataset.action === 'continue') openChat('What should I do next for this Cloud Journey?');
      if (button.dataset.action === 'ask-status') openChat('Explain the current blocker and tell me what happens next.');
    });
    document.querySelectorAll('.tab').forEach(tab => tab.addEventListener('click', () => { currentView = tab.dataset.view; renderScreen(); }));
    document.querySelectorAll('.quick-actions button').forEach(button => button.addEventListener('click', () => sendQuery(button.dataset.q)));
    linkForm.addEventListener('submit', event => { event.preventDefault(); linkApm(apmInput.value); });
    document.getElementById('cancel-link').addEventListener('click', () => linkDialog.close());
    compassFab.addEventListener('click', () => openChat());
    document.getElementById('close-chat').addEventListener('click', closeChat);
    send.addEventListener('click', () => sendQuery());
    queryInput.addEventListener('keydown', event => {
      if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); sendQuery(); }
    });
    renderMessages();
  </script>
</body>
</html>
"""


@app.get("/playground", response_class=HTMLResponse)
def playground() -> str:
    """Serve a small same-origin UI for exercising the orchestrator."""

    return PLAYGROUND_HTML.replace("__CLIENT_ID_JSON__", json.dumps(OAUTH_CLIENT_ID))


@app.get("/v1/journeys")
def journey_portfolio(
    x_user_authorization: str | None = Header(default=None),
) -> dict[str, list[dict[str, Any]]]:
    """List durable Journeys visible to the signed-in user's access groups."""

    if not OAUTH_CLIENT_ID:
        raise HTTPException(status_code=503, detail="Google sign-in is not configured.")

    context_token = _user_token.set("")
    try:
        user = _verify_user(x_user_authorization)
        service = get_service()
        groups = service.state_machine.get_access_groups_for_user(user["subject"])
        apm_ids = service.state_machine.list_apm_ids_for_groups(groups)
        journeys: list[dict[str, Any]] = []
        for apm_id in apm_ids:
            journey = service.state_machine.find_journey_by_apm_id(apm_id)
            if journey is not None and journey.access_group_id in groups:
                journeys.append(service.status(journey.id))
        return {"journeys": journeys}
    finally:
        _user_token.reset(context_token)


@app.get("/v1/journeys/by-apm/{apm_id}")
def journey_status_by_apm(
    apm_id: str,
    x_user_authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Return structured, access-controlled progress for the Journey UI."""

    if not OAUTH_CLIENT_ID:
        raise HTTPException(status_code=503, detail="Google sign-in is not configured.")

    context_token = _user_token.set("")
    try:
        user = _verify_user(x_user_authorization)
        try:
            return get_service().status_by_apm_id_for_subject(
                apm_id, user["subject"]
            )
        except ApmAccessDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except JourneyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        _user_token.reset(context_token)


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
