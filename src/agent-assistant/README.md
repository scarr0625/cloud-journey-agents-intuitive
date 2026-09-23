# agent-assistant

This folder builds one independent Cloud Run service.
Build from the **repository root**, so Docker can include the shared packages:

```sh
docker build -f src/agent-assistant/Dockerfile -t agent-assistant .
```

Configuration is documented in `.env.example`. Inject real credentials using
Cloud Run secrets. The image contains this agent and its shared dependencies;
it does not include other agent folders or the local business simulator.

The Python package is `agent_assistant` (the package manifest maps `app/` to this
unique name, avoiding collisions between five packages all named `app`).

```sh
uvicorn agent_assistant.main:app --port 8000
```

The endpoints are `/health`, `/healthz`, and `POST /v1/query`. The query request
accepts `query` and optional `session_id`; verified user authentication is passed
in `X-User-Authorization`. Sessions use Session DB; no business or checkpoint
SQL models are imported.
