# Durable Journey Agents

Five independently deployable agents use one shared Python package,
`cloud_journey_agents`, for infrastructure and safety rules. Build
all Docker images with the repository root as the build context. The client owns
the existing private MCP server and Data API; this repository does not deploy them.

## Repository layout

```text
src/
  pyproject.toml           # One cloud-journey-agents distribution
  cloud_journey_agents/
    __init__.py            # Activate the operating system certificate trust store
    identity.py            # Verified user context and cached service ID tokens
    guardrails.py          # APM IDs, tool allowlists, single-read and SQL checks
    mcp.py                 # MCP transport and tool availability gate
    journey_db.py          # Reject outdated direct-database integrations
    batch.py               # Compatibility imports for earlier PoC callers
    batch_server.py        # Compatibility imports for earlier PoC entry points
    logs.py                # Structured JSON logging
    config.py              # Environment settings and required-value checks
    durability/            # Workflow recovery and MCP checkpoint client
    sessions/              # ADK session MCP client and runner lifecycle
  agent-apm-validation/     # Cloud Run Job
  agent-ad-provisioning/    # Cloud Run Job: submit and poll modes
  agent-app-factory/        # Cloud Run Job
  agent-assistant/          # Read-only HTTP service, calls private MCP
  agent-orchestrator/       # HTTP service, routes to Assistant
migrations/
  bootstrap.sql            # Create all three databases and their current schemas
  business-state/          # Journey business schema / reference for client
  durable-state/           # Centrally applied batch checkpoint schema
  session-state/           # ADK v1 conversation schema (pinned ADK release)
references/schwab-mcp/     # Server-side persistence handlers and integration guide
examples/local-business/   # Historical SQL simulator; never included in agent images
workflows/                 # Local workflow runner and production orchestration notes
tests/
```

Each `agent-*` folder under `src/` contains `app/`, its own `Dockerfile`, package
manifest, README, and `.env.example`. Optional `requirements.txt` files are local
installation shortcuts. `src/pyproject.toml` packages only `cloud_journey_agents`,
using the `batch` or `chat` dependency extra. The shared package is installed inside
each image; it has no separate Cloud Run deployment. Package manifests
map each agent's `app/` to a unique import name, such as `agent_apm_validation`.

The agent folders follow the original layout, with `server.py` as the server entry
point and `job.py` for batch workflow logic:

```text
src/agent-{apm-validation,ad-provisioning,app-factory}/app/
  __init__.py
  durability.py            # Bind this agent to the shared durable runtime
  job.py                   # Agent-owned workflow steps
  server.py                # HTTP app and CLI entry point

src/agent-{assistant,orchestrator}/app/
  __init__.py
  agent.py                 # Compose the model, prompt, and tools
  context.py               # Verified tool context and delegation
  prompt.py                # Agent instructions
  server.py                # HTTP app and endpoints
  sessions.py              # Bind this agent to the shared conversation runtime
  settings.py              # Agent configuration using shared config helpers
  tools.py                 # Agent-specific tools and routing
```

Both HTTP and CLI batch invocations go through the agent's `durability.py` module,
which fixes its workflow and delegates checkpoint persistence and recovery to the
shared runtime. AD submit and poll invocations retain the same saved request ID.

Durability has no dependency on `batch.py` or `batch_server.py`. When porting it
to the main repository, retain that repository's batch files and add the agent
call sites described in the [durability integration guide](src/cloud_journey_agents/durability/README.md).

Durable and session persistence, identity verification, and infrastructure live in
`src/cloud_journey_agents/`; the agent modules provide application wiring.

The original shared module names are retained under `src/cloud_journey_agents/`.
Durability and session support extend that package instead of creating separate
`journey-*` distributions. Import from `cloud_journey_agents`; the previous
`journey_mcp`, `journey_durability`, and `journey_sessions` imports are removed.

| Agent | Shared functionality | Agent-owned application logic |
| --- | --- | --- |
| Orchestrator | Identity, service tokens, logging, config, MCP sessions | Prompt and Assistant routing |
| Assistant | Identity forwarding, MCP gate, read guardrails, sessions | Prompt and status tools |
| APM Validation | Batch runtime, MCP operations, durability, HTTP/CLI wrapper | APM validation step in `app/job.py` |
| AD Provisioning | Same batch infrastructure | Submission and polling steps in `app/job.py` |
| App Factory | Same batch infrastructure | Readiness/schema validation step in `app/job.py` |

Shared code never imports an agent. It centralizes infrastructure and safety
rules; agents communicate through HTTP/MCP, not through the package. Each agent
retains its own image, dependencies, entry point, and service account. The
`batch` and `chat` dependency extras keep ADK/session dependencies out of batch
images. Importing the package opens no database connection.

## Ownership and database connections

**All deployed agents access state through Schwab MCP. Their service accounts
have no direct database permissions or database credentials.**

| Component | MCP capabilities | Direct database connection |
| --- | --- | --- |
| APM Validation | Its business operations and durable claim/save/finish | None |
| AD Provisioning | Submission/polling/recovery and durable claim/save/finish | None |
| App Factory | Readiness/recovery and durable claim/save/finish | None |
| Assistant | Authorized business reads and its users' sessions | None |
| Orchestrator | Its users' sessions; routes business questions to Assistant | None |
| Schwab MCP persistence handlers | Transactional durable/session tools | Durable State DB and Session DB |
| Schwab Data API/business services behind MCP | Business validation, transitions, authorization | Journey business DB |

`JourneyState` remains business logic owned by the client's Data API. The SQL
models and transition simulator under `examples/local-business/` are a reference
and a runnable local demo. Production agent images do not include that package.
`cloud_journey_agents.durability` contains checkpoint infrastructure and adapters
for executing agent steps; it has no business SQL models or session imports.
Its runtime accepts an injected business gateway so the main repo can reuse its
existing MCP-backed operations. `journey_db.py` now rejects every call, including
when the old local-read environment flag is set. MCP failure never enables a SQL
fallback. SQL implementations exist only in the historical simulator and the
Schwab server-side reference, outside deployed agent packages.

Only the three batch jobs call the durable tools. Schwab's server transacts on
`agent_execution`, `operation_checkpoint`, `checkpoint_event`, and mutation receipts.
The five checkpoint statuses are `PENDING`, `RUNNING`,
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
Each Dockerfile installs its own agent and the shared package with the required
dependency extra. To install
just an agent locally, run its requirements from the repository root:

```powershell
python -m pip install -r src/agent-apm-validation/requirements.txt
```

The equivalent installation using the manifests directly is:

```powershell
python -m pip install "./src[batch]" ./src/agent-apm-validation
```

## Create state databases on the Schwab side

Use the [migration guide](migrations/README.md) to create `cloud-journey-db`
(business state), `durable-state-db` (batch recovery), and `session-db` (ADK
conversation state). Use a Schwab-owned migration identity, never an agent
identity. From the repository root in the server/migration environment:

```powershell
psql -X -h 127.0.0.1 -U journey -d postgres -f migrations/bootstrap.sql
```

For local development, `docker compose up -d` performs this setup on a new volume.
For an existing volume, run the bootstrap command documented in the migration
guide. The new-database setup creates schemas without demo authorization data;
historical business data upgrades are preserved separately.

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

The trailing `.` is the root build context. Each Dockerfile copies its own source,
`src/pyproject.toml`, and `src/cloud_journey_agents/`, then installs
`cloud-journey-agents[batch]` or
`cloud-journey-agents[chat]` through the agent's manifest.
See [GCP_DEPLOYMENT.md](GCP_DEPLOYMENT.md) for deployment and configuration details.

## Run the client-connected agents

Configure the client's `MCP_URL`, its workload authentication, and the matching
contract in [MCP_CONTRACT.md](MCP_CONTRACT.md): five business capabilities, two
status reads, three durable tools, and five session tools. The MCP transport uses
Streamable HTTP. Endpoint names, business outcomes, and delegated authentication
must be agreed with the client's server; they are not discovered or assumed.

Schwab must implement the requested tools and apply the durable/session schemas
before the agents run. The [server reference](references/schwab-mcp/README.md)
contains handler code, tool schemas, and transaction requirements. No deployed
agent creates tables or opens database connections.

```powershell
python -m agent_apm_validation.server --journey-id J-123 --workflow-run-id RUN-123
python -m agent_ad_provisioning.server --journey-id J-123 --workflow-run-id RUN-123 --mode submit
python -m agent_ad_provisioning.server --journey-id J-123 --workflow-run-id RUN-123 --mode poll
python -m agent_app_factory.server --journey-id J-123 --workflow-run-id RUN-123
```

Repeat polling in later invocations while pending. Run App Factory only after
AD business success. Jobs emit one JSON result on stdout: exit 0 can mean WAITING,
exit 2 indicates a recorded negative business result, and other exceptions fail
the invocation. For the local simulator of this job sequence:

```powershell
python -m workflows.local_workflow --journey-id J-123 --workflow-run-id RUN-123
```

`workflows/` is optional local tooling used by the demo and tests. It is excluded
from every agent image. Production needs an external coordinator (such as Google
Workflows) to sequence the jobs and schedule polling; deploying the agents does
not require this local directory. See [workflows/README.md](workflows/README.md).

The three batch agents also expose the original HTTP service interface through
`cloud_journey_agents.durability.server`. For example:

```powershell
uvicorn agent_ad_provisioning.server:app --port 8002
```

`POST /v1/run` accepts `journey_id`, `workflow_run_id`, and an optional `mode`
(`submit`, `poll`, or `resume` for AD; only `resume` for other agents). It returns
the same result fields as the CLI. The image fixes the agent identity. Cloud Run
IAM must protect this endpoint. The default Docker entry points remain Jobs;
see the deployment guide for the service command override.

Start the HTTP agents in separate terminals:

```powershell
uvicorn agent_assistant.server:app --port 8001
uvicorn agent_orchestrator.server:app --port 8000
```

Both serve `POST /v1/query`, `/health`, and `/healthz`. Configure `ASSISTANT_URL`
on the orchestrator. It retains the downstream Assistant session ID in its own
session context. Neither service exposes batch execution or business write tools.

## Preserved local business demo

The earlier interactive playground, business state machine, approval simulator,
and simulated MyAccess/App Factory operations are kept in
[examples/local-business](examples/local-business/README.md). They run without a
client MCP server and remain covered by the existing tests. Their broader
interactive workflow uses historical direct SQL and is separate from the five
policy-compliant client-facing deployments. Use the MCP server reference for
current persistence integration work.

The design reference is [durable_state_confluence.md](durable_state_confluence.md).
