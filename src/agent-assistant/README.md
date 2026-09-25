# agent-assistant

This folder builds one independent Cloud Run service.
Build from the **repository root**, so Docker can include `src/cloud_journey_agents`:

```sh
docker build -f src/agent-assistant/Dockerfile -t agent-assistant .
```

Configuration is documented in `.env.example`. Inject real credentials using
Cloud Run secrets. The image contains this agent and its shared dependencies;
it does not include other agent folders or the local business simulator.

The Python package is `agent_assistant` (the package manifest maps `app/` to this
unique name, avoiding collisions between five packages all named `app`).

```sh
uvicorn agent_assistant.server:app --port 8000
```

The endpoints are `/health`, `/healthz`, and `POST /v1/query`. The query request
accepts `query` and optional `session_id`; verified user authentication is passed
in `X-User-Authorization`. Sessions use Session DB; no business or checkpoint
SQL models are imported.

Prompts, tools, and routing stay in `app/`. Shared identity, guardrails, MCP,
configuration, logging, and session persistence live in
`src/cloud_journey_agents/`; this image installs the shared distribution's
`chat` dependency extra. Chat tools never load batch checkpoint infrastructure
or fall back to direct business SQL when MCP is unavailable.

The `app/` layout follows the original agent structure:

| File | Responsibility |
| --- | --- |
| `server.py` | HTTP entry point, health and query endpoints |
| `agent.py` | Compose the model, prompt, and tools |
| `context.py` | Verified request identity for MCP delegation |
| `prompt.py` | Assistant instructions |
| `sessions.py` | Bind the agent to the shared conversation runtime |
| `settings.py` | Agent model configuration |
| `tools.py` | Read-only Journey status tools |
