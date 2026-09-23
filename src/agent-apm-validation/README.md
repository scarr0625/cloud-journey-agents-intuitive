# agent-apm-validation

This folder builds one independent Cloud Run Job.
Build from the **repository root**, so Docker can include the shared packages:

```sh
docker build -f src/agent-apm-validation/Dockerfile -t agent-apm-validation .
```

Configuration is documented in `.env.example`. Inject real credentials using
Cloud Run secrets. The image contains this agent and its shared dependencies;
it does not include other agent folders or the local business simulator.

The Python package is `agent_apm_validation` (the package manifest maps `app/` to this
unique name, avoiding collisions between five packages all named `app`).

```sh
python -m agent_apm_validation.main --journey-id J-123 --workflow-run-id RUN-123
```

Apply `migrations/durable-state/000_execution_checkpoints.sql` centrally before
running jobs. This job never creates database tables. Business operations use
private MCP; only checkpoint persistence opens the Durable State DB.
