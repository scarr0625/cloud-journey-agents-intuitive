# Durable Journey Agents

Five independently deployable agents share persistence and MCP libraries. Build
all Docker images with the repository root as the build context. The client owns
the existing private MCP server and Data API; this repository does not deploy them.

## Repository layout

```text
packages/
  journey-durability/       # Batch execution/checkpoint/history, MCP outcome adapter
  journey-sessions/         # ADK conversation persistence and runner lifecycle
  journey-mcp/              # MCP transport, tool allowlists, verified user identity
src/
  agent-apm-validation/     # Cloud Run Job
  agent-ad-provisioning/    # Cloud Run Job: submit and poll modes
  agent-app-factory/        # Cloud Run Job
  agent-assistant/          # Read-only HTTP service, calls private MCP
  agent-orchestrator/       # HTTP service, routes to Assistant
migrations/
  business/                # Local business simulator schema / reference for client
  durable-state/           # Centrally applied batch checkpoint schema
examples/local-business/   # Preserved interactive PoC and simulated business gateway
workflows/                 # Local workflow runner and production orchestration notes
tests/
```

Each folder under `src/` contains `app/`, its own `Dockerfile`, package manifest,
requirements, README, and `.env.example`. A shared package is installed inside
its consumers' images; it has no separate Cloud Run deployment. Package manifests
map each agent's `app/` to a unique import name, such as `agent_apm_validation`.

## Ownership and database connections

| Component | Direct connection | Business access |
| --- | --- | --- |
| APM Validation | Durable State DB | Private MCP -> Data API |
| AD Provisioning | Durable State DB | Private MCP -> Data API |
| App Factory | Durable State DB | Private MCP -> Data API |
| Assistant | Session DB | Read-only private MCP -> Data API |
| Orchestrator | Session DB | Routes questions to Assistant |
| Client Data API | Business DB | Enforces business transitions and authorization |

`JourneyState` remains business logic owned by the client's Data API. The SQL
models and transition simulator under `examples/local-business/` are a reference
and a runnable local demo. Production agent images do not include that package.
`journey_durability` has no business SQL model, database module, or session imports.

Only the three batch jobs load/save `agent_execution`, `operation_checkpoint`, and
`checkpoint_event`. Their five checkpoint statuses are `PENDING`, `RUNNING`,
`WAITING`, `COMPLETED`, and `FAILED`. AD submission and polling reuse one operation
key and the same MyAccess request ID. A saved negative business result completes
the operation but reports `successful=false`, so orchestration stops.

## Install for local development

Use Python 3.11 or newer:

```powershell
py -3.11 -m venv .venv
.venv/Scripts/Activate.ps1
python -m pip install -e ".[test]"
python -m pytest -q
```

The root distribution installs all source packages for development and tests.
Each Dockerfile installs only its own agent and required libraries. To install
just an agent locally, run its requirements from the repository root:

```powershell
python -m pip install -r src/agent-apm-validation/requirements.txt
```

## Configure and build the five agents

Use the `.env.example` in the agent's folder as a configuration reference. For
local runs copy the selected file to a root `.env`, or set environment variables.
Cloud Run should inject configuration and secrets directly. The root `.env.example`
is only for the optional local business simulator.

Build from the repository root:

```powershell
docker build -f src/agent-apm-validation/Dockerfile -t journey-apm .
docker build -f src/agent-ad-provisioning/Dockerfile -t journey-ad .
docker build -f src/agent-app-factory/Dockerfile -t journey-app-factory .
docker build -f src/agent-assistant/Dockerfile -t journey-assistant .
docker build -f src/agent-orchestrator/Dockerfile -t journey-orchestrator .
```

The trailing `.` is the root build context. Each Dockerfile copies its own source
and the necessary `packages/` directories. It does not copy all five agents.
See [GCP_DEPLOYMENT.md](GCP_DEPLOYMENT.md) for deployment and configuration details.

## Run the client-connected agents

Configure the client's `MCP_URL`, its workload authentication, and the matching
seven-tool contract in [MCP_CONTRACT.md](MCP_CONTRACT.md). The MCP transport uses
Streamable HTTP. Endpoint names, business outcomes, and delegated authentication
must be agreed with the client's server; they are not discovered or assumed.

Apply `migrations/durable-state/000_execution_checkpoints.sql` to Durable State DB
before running jobs. Runtime job identities need data privileges, not schema
creation privileges. Deployed jobs do not call `create_all()`.

```powershell
python -m agent_apm_validation.main --journey-id J-123 --workflow-run-id RUN-123
python -m agent_ad_provisioning.main --journey-id J-123 --workflow-run-id RUN-123 --mode submit
python -m agent_ad_provisioning.main --journey-id J-123 --workflow-run-id RUN-123 --mode poll
python -m agent_app_factory.main --journey-id J-123 --workflow-run-id RUN-123
```

Repeat polling in later invocations while pending. Run App Factory only after
AD business success. Jobs emit one JSON result on stdout: exit 0 can mean WAITING,
exit 2 indicates a recorded negative business result, and other exceptions fail
the invocation. For the local simulator of this job sequence:

```powershell
python -m workflows.local_workflow --journey-id J-123 --workflow-run-id RUN-123
```

Start the HTTP agents in separate terminals:

```powershell
uvicorn agent_assistant.main:app --port 8001
uvicorn agent_orchestrator.main:app --port 8000
```

Both serve `POST /v1/query`, `/health`, and `/healthz`. Configure `ASSISTANT_URL`
on the orchestrator. It retains the downstream Assistant session ID in its own
session context. Neither service exposes batch execution or business write tools.

## Preserved local business demo

The earlier interactive playground, business state machine, approval simulator,
and simulated MyAccess/App Factory operations are kept in
[examples/local-business](examples/local-business/README.md). They run without a
client MCP server and remain covered by the existing tests. Their broader
interactive workflow is separate from the five client-facing deployments.

The design reference is [durable_state_confluence.md](durable_state_confluence.md).
