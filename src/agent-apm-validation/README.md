# agent-apm-validation

This folder builds one independent Cloud Run Job.
Build from the **repository root**, so Docker can include `src/cloud_journey_agents`:

```sh
docker build -f src/agent-apm-validation/Dockerfile -t agent-apm-validation .
```

Configuration is documented in `.env.example`. Inject real credentials using
Cloud Run secrets. The image contains this agent and its shared dependencies;
it does not include other agent folders or the local business simulator.

The Python package is `agent_apm_validation` (the package manifest maps `app/` to this
unique name, avoiding collisions between five packages all named `app`).

```sh
python -m agent_apm_validation.server --journey-id J-123 --workflow-run-id RUN-123
```

Apply `migrations/durable-state/000_execution_checkpoints.sql` centrally before
running jobs. This job never creates database tables. Business operations use
private MCP; only checkpoint persistence opens the Durable State DB.

The domain steps live in `app/job.py`. The agent's `app/durability.py` binds those
steps to the shared durable runtime; `app/server.py` uses that module for both
HTTP and CLI invocations. Each invocation opens the Durable State DB using
`DURABLE_*` configuration and releases its connection pool when finished.
Checkpoint claims, recovery, and state transitions remain in the shared package.

This integration imports `cloud_journey_agents.durability` directly and does not
require the shared `batch.py` or `batch_server.py`. For copying it into the main
repo while keeping that repo's batch implementations, follow the
[durability integration guide](../cloud_journey_agents/durability/README.md).

Shared execution, identity, MCP,
configuration, logging, and persistence live in `src/cloud_journey_agents/`;
this image installs the shared distribution's `batch` dependency extra.

The same workflow can run through the shared HTTP wrapper:

```sh
uvicorn agent_apm_validation.server:app --host 0.0.0.0 --port 8080
```

It serves `/health`, `/healthz`, and `POST /v1/run`. Send `journey_id` and
`workflow_run_id` in JSON, plus `mode` for AD submission/polling. The response
includes `checkpoint_status` and `successful`; inspect both before advancing the
workflow. Protect the service with Cloud Run IAM. To use this mode with the
Docker image, override its command with `uvicorn` and the arguments above.
