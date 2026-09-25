# Deploy the five agent images to Cloud Run

All database connections belong to Schwab's MCP/business services. Agent service
accounts need MCP invocation and their approved model/service permissions only.
Do not grant agents Cloud SQL access, database credentials, table privileges, or
database network attachments. This repository does not deploy Schwab's MCP host.

## Schwab prerequisites

Implement the [requested MCP tools](SCHWAB_MCP_TOOL_REQUEST.md), using the
[server reference](references/schwab-mcp/README.md) and
[migration guide](migrations/README.md). Apply schema changes with a migration
identity. Bind each authenticated workload to its permitted batch agent or chat
application, and validate delegated user identity on every session/status call.
Complete PostgreSQL, authentication, and business-provider acceptance tests before
enabling production traffic. The checked-in reference is not a deployed service.

## Build context

Build from the repository root and push to the approved Artifact Registry:

```powershell
docker build -f src/agent-apm-validation/Dockerfile -t REGION-docker.pkg.dev/PROJECT/REPOSITORY/agent-apm-validation:VERSION .
docker build -f src/agent-ad-provisioning/Dockerfile -t REGION-docker.pkg.dev/PROJECT/REPOSITORY/agent-ad-provisioning:VERSION .
docker build -f src/agent-app-factory/Dockerfile -t REGION-docker.pkg.dev/PROJECT/REPOSITORY/agent-app-factory:VERSION .
docker build -f src/agent-assistant/Dockerfile -t REGION-docker.pkg.dev/PROJECT/REPOSITORY/agent-assistant:VERSION .
docker build -f src/agent-orchestrator/Dockerfile -t REGION-docker.pkg.dev/PROJECT/REPOSITORY/agent-orchestrator:VERSION .
```

The root context lets each Dockerfile copy `src/pyproject.toml`, the shared
`src/cloud_journey_agents/` code, and that agent's own `app/`. Batch images use the
`batch` extra; chat images use `chat`. Shared changes require rebuilding consumers.
Neither the Schwab reference nor historical SQL simulator is included in agent
images. The shared Python package has no separate network deployment.

## MCP connection configuration

Set `MCP_URL` on **all five agents**. The transport uses Streamable HTTP and checks
tool availability before calling. `MCP_CLOUD_RUN_AUDIENCE` enables a workload ID
token in `X-Serverless-Authorization`; optional `MCP_BEARER_TOKEN` is an application
credential from the approved secret store. Use the authentication mechanism agreed
with Schwab; adapt the central transport if its token exchange differs.

Both chat agents forward the verified end-user token using `MCP_USER_AUTH_HEADER`
(`X-User-Authorization` by default). Schwab must verify that token independently
and authorize its subject. Never treat an unverified header or user_id argument
as identity. Neither tokens nor database secrets belong in conversation state.

## Batch jobs

Deploy APM Validation, AD Provisioning, and App Factory as separate Cloud Run Jobs,
each with its own workload identity. The image fixes agent identity; there is no
caller-controlled `--agent` option. Each identity needs its business tools plus
durable claim/save/finish tools, restricted to its operation.

Example after pushing the image (replace placeholders):

```powershell
gcloud run jobs deploy agent-apm-validation --image=REGION-docker.pkg.dev/PROJECT/REPOSITORY/agent-apm-validation:VERSION --region=REGION --service-account=APM_JOB_SA --tasks=1 --parallelism=1 --task-timeout=240s --max-retries=0 --set-env-vars="MCP_URL=https://PRIVATE_MCP/mcp,MCP_CLOUD_RUN_AUDIENCE=https://PRIVATE_MCP"
```

Configure AD and App Factory similarly. Keep execution timeout below Schwab's
checkpoint lease (reference default: 300 seconds). A killed worker's lease may
need to expire before another invocation claims it. There is no lease-renewal
tool, so business operations must finish within the bounded invocation.

Workflows passes `--journey-id` and `--workflow-run-id`; AD accepts `--mode submit`
or `--mode poll`. Pending human approval is polled in later invocations after
releasing the lease. Exit 0 may mean WAITING; exit 2 is a recorded negative
business outcome. Inspect results before advancing. See [workflows/README.md](workflows/README.md).

The optional `/health` and `POST /v1/run` service interface uses the same runtime.
Override the entry point to `uvicorn` with arguments
`agent_ad_provisioning.server:app,--host,0.0.0.0,--port,8080` (substitute the agent).
Keep Cloud Run authentication enabled and allow only approved workflow callers.
Use the same MCP permissions and a request timeout below the lease. Input is
`journey_id`, `workflow_run_id`, and optional `mode`; inspect `checkpoint_status`
and `successful`, even on HTTP 200. Lease conflicts return 409; MCP persistence
unavailability returns 503.

## Chat HTTP services

Deploy Assistant and Orchestrator as separate authenticated services on port 8080.
Set `OAUTH_CLIENT_ID` and optional `ALLOWED_USER_DOMAINS` for verified inbound user
authentication. Configure MCP on both services for session persistence. Assistant
also needs its two authorized business status reads; Orchestrator needs no batch
or business-write permissions. Session tools are internal runtime calls.

Set `ASSISTANT_URL` and `ASSISTANT_CLOUD_RUN_AUDIENCE` on Orchestrator and grant it
invoker access to Assistant. The downstream request uses workload authentication
and forwards user identity separately; both services verify the end user.

```powershell
gcloud run deploy agent-assistant --image=REGION-docker.pkg.dev/PROJECT/REPOSITORY/agent-assistant:VERSION --region=REGION --service-account=ASSISTANT_SA --no-allow-unauthenticated --set-env-vars="OAUTH_CLIENT_ID=CLIENT_ID,MCP_URL=https://PRIVATE_MCP/mcp,MCP_CLOUD_RUN_AUDIENCE=https://PRIVATE_MCP"
```

Deploy Orchestrator similarly, adding Assistant routing settings. Configure the
approved Gemini/Vertex AI model and credentials using each `.env.example`.
There is no `SESSION_DATABASE_URL` or `DURABLE_DATABASE_URL` on deployed agents.
MCP failures fail the request; they never trigger local database fallback.

## Validation scope

Repository tests cover MCP persistence contracts, lost replies, stale ownership,
session identity/state, batch recovery, packaging boundaries, and reference DDL.
Schwab must validate its actual MCP authentication, PostgreSQL concurrency,
database privileges, Cloud Run IAM/network, and external provider idempotency.
