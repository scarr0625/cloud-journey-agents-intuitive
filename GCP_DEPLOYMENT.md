# Deploy the five agent images to Cloud Run

This repository supplies independent Dockerfiles, not a deployment of the client's
MCP/Data API. Build from the repository root and push each image to your approved
Artifact Registry repository. No cloud changes are performed by the local setup.

## Build context

```powershell
docker build -f src/agent-apm-validation/Dockerfile -t REGION-docker.pkg.dev/PROJECT/REPOSITORY/agent-apm-validation:VERSION .
docker build -f src/agent-ad-provisioning/Dockerfile -t REGION-docker.pkg.dev/PROJECT/REPOSITORY/agent-ad-provisioning:VERSION .
docker build -f src/agent-app-factory/Dockerfile -t REGION-docker.pkg.dev/PROJECT/REPOSITORY/agent-app-factory:VERSION .
docker build -f src/agent-assistant/Dockerfile -t REGION-docker.pkg.dev/PROJECT/REPOSITORY/agent-assistant:VERSION .
docker build -f src/agent-orchestrator/Dockerfile -t REGION-docker.pkg.dev/PROJECT/REPOSITORY/agent-orchestrator:VERSION .
```

Use the same context (`.`) in the client's Cloud Build pipeline. The Dockerfile
path selects the agent, while COPY paths are relative to the repository root.
Shared-library changes require rebuilding each image that consumes the library.

## Batch jobs

Deploy APM Validation, AD Provisioning, and App Factory as separate Cloud Run
Jobs. Each image fixes its agent identity; there is no caller-controlled --agent
argument. Give each job its own workload identity and corresponding MCP permissions.

- `DURABLE_DATABASE_URL` connects to durable-state-db, using the Auth Proxy/socket
  or another configured PostgreSQL endpoint.
- Alternatively set `DURABLE_CLOUD_SQL_INSTANCE`, `DURABLE_DB_USER`,
  `DURABLE_DB_NAME`, and `DURABLE_DB_PASSWORD`, or `DURABLE_DB_IAM_AUTH=true`.
  `DURABLE_DB_IP_TYPE` defaults to PRIVATE and must match network connectivity.
- `MCP_URL` is the client's Streamable HTTP endpoint. `MCP_CLOUD_RUN_AUDIENCE`
  requests a Google service ID token in X-Serverless-Authorization. Optional
  `MCP_BEARER_TOKEN` is an application credential supplied through a secret.
- Do not configure Business DB or Session DB connections on batch jobs.

Apply `migrations/durable-state/000_execution_checkpoints.sql` once through your
migration pipeline. Grant batch database identities the data privileges needed
for checkpoint transactions. Deployed jobs never create tables.

Example after pushing the image (replace every placeholder):

```powershell
gcloud run jobs deploy agent-apm-validation --image=REGION-docker.pkg.dev/PROJECT/REPOSITORY/agent-apm-validation:VERSION --region=REGION --service-account=APM_JOB_SA --tasks=1 --parallelism=1 --task-timeout=240s --max-retries=0 --set-env-vars="MCP_URL=https://PRIVATE_MCP/mcp,MCP_CLOUD_RUN_AUDIENCE=https://PRIVATE_MCP" --set-secrets="DURABLE_DATABASE_URL=DURABLE_URL_SECRET:latest"
```

Attach the Cloud SQL instance when using a Unix socket, or configure the network
and Cloud SQL connector as required by the selected connection method. Apply
corresponding configuration to the AD and App Factory jobs. Keep the job timeout
below the 300-second checkpoint lease. If a process is killed, a retry may need to
wait until the lease expires. Use controlled retries in Workflows; a terminal
negative business result should not be retried automatically.

Workflows passes `--journey-id` and `--workflow-run-id` to each execution. AD also
accepts `--mode submit` or `--mode poll`. See [workflows/README.md](workflows/README.md)
for ordering and the distinction between successful job execution and pending work.

## HTTP services

Deploy Assistant and Orchestrator as separate authenticated Cloud Run services.
Both listen on port 8080. Configure `SESSION_DATABASE_URL` to session-db and inject
verified Google user authentication configuration (`OAUTH_CLIENT_ID` and optional
`ALLOWED_USER_DOMAINS`). ADK manages its own session tables; apply the client's
chosen session-schema permissions/migration policy.

Configure `ASSISTANT_URL` and `ASSISTANT_CLOUD_RUN_AUDIENCE` on the orchestrator.
Grant its workload identity Cloud Run invoker access to the Assistant. It sends
its service ID token in Authorization and the user's verified token separately
in X-User-Authorization. Each service validates the end-user token.

Configure MCP on the Assistant as above, plus `MCP_USER_AUTH_HEADER` to match the
client's established delegation contract (X-User-Authorization by default).
The MCP server must validate the delegated token and enforce user-level access;
this header alone is not authorization. When the client uses an MCP-specific OAuth
access token rather than the existing Google delegation contract, integrate its
approved token exchange/provider before enabling that connection.

Example after pushing the image:

```powershell
gcloud run deploy agent-assistant --image=REGION-docker.pkg.dev/PROJECT/REPOSITORY/agent-assistant:VERSION --region=REGION --service-account=ASSISTANT_SA --no-allow-unauthenticated --set-env-vars="OAUTH_CLIENT_ID=CLIENT_ID,MCP_URL=https://PRIVATE_MCP/mcp,MCP_CLOUD_RUN_AUDIENCE=https://PRIVATE_MCP" --set-secrets="SESSION_DATABASE_URL=SESSION_URL_SECRET:latest"
```

Configure the selected Gemini/Vertex AI model and credentials using the agent's
`.env.example`. Deploy the orchestrator similarly with its Assistant URL and
appropriate inbound gateway/IAM policy. Neither HTTP identity should have
checkpoint database access. The old playground is an optional local example;
it is not included in these production images.

## Validation scope

Automated tests cover package boundaries, checkpoint recovery, the local business
simulator, MCP adapters/transport, session persistence, and agent routing. Real
client authorization, Data API transaction/idempotency guarantees, Cloud SQL,
Cloud Run IAM, and network access require validation in the client's environment.
