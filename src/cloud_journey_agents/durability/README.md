# Porting durability into the main repository

Durability does not import `cloud_journey_agents.batch` or
`cloud_journey_agents.batch_server`. Keep the main repository's versions of both
files. The files with those names in this PoC are compatibility imports for older
callers; the three agents use the durability package directly.

## Files to copy and integration points

1. Copy this entire `cloud_journey_agents/durability/` directory into the main
   shared package. It contains the contracts, models, database connections,
   checkpoint store, runtime, optional MCP adapter, and optional HTTP/CLI wrapper.
2. Copy `app/durability.py` into APM Validation, AD Provisioning, and App Factory.
3. Bind each module to its agent's workflow. This repo exposes `WORKFLOW` in
   `app/job.py`, with `agent`, `modes`, and `next_step(checkpoint, mode)` as defined
   in `contracts.py`. Adapt that binding if the main repo's jobs have another
   interface; copying a module alone does not activate durability.
4. At the existing agent invocation point, call
   `durability.execute_job(journey_id, workflow_run_id, mode=mode)`. The call must
   encompass the business step so the runtime can claim its checkpoint before
   invoking it. Do not execute the old unwrapped business step a second time.
5. Apply `migrations/durable-state/000_execution_checkpoints.sql` to the Durable
   State DB, and supply `DURABLE_DATABASE_URL` or the existing `DURABLE_CLOUD_SQL_*`
   / `DURABLE_DB_*` configuration documented in each agent's `.env.example`.

Keep the main repo's routes and CLI parsing if you prefer. If adopting this repo's
complete durable HTTP/CLI adapter, the only server wiring needed here is:

```python
from cloud_journey_agents.durability.server import create_batch_app, run_job
from . import durability
from .job import WORKFLOW

app = create_batch_app(WORKFLOW, executor=durability.execute_job)

def main(argv=None):
    run_job(WORKFLOW, argv, executor=durability.execute_job)
```

Preserve the main application's authentication, request validation, logging, and
other routes when integrating into its existing server. A busy operation raises
`durability.checkpoints.OperationBusy` (HTTP 409 in the optional adapter). The
result includes `checkpoint_status` and `successful`; `WAITING` needs a later
invocation, and a completed negative business result must stop orchestration.

## Reuse the main repo's business implementation

`runtime.execute_job()` and each agent's `durability.execute_job()` accept a
`business=` gateway implementing `contracts.BusinessGateway`. This avoids any
dependency on the PoC's MCP adapter or on either shared batch file:

```python
result = durability.execute_job(
    journey_id,
    workflow_run_id,
    mode=mode,
    business=existing_business_gateway,
)
```

The gateway must return `BusinessProgress` from the business steps and must read
persisted operation results through `read_progress(journey_id, operation_key)`.
That read lets retries recover when a business operation committed before its
checkpoint was saved. Return the persisted result reference, retain the AD request
ID, and preserve idempotency keys when adapting existing operations. Keep
authorization and business transitions in the main repo's existing business path.

Without `business=`, the runtime lazily uses `mcp_gateway.py`. That adapter expects
this repo's `McpClient`, `McpError`, and `BATCH_TOOLS` interfaces, plus the response
contract in the root `MCP_CONTRACT.md`. If the main repo differs, inject its gateway
instead of replacing its MCP or guardrail modules.

## Shared helpers and dependencies

The core database configuration reuses `config.setting` and
`config.required_setting`. The optional server adapter additionally uses
`config.load_config` and `logs.configure_logging`. Verify those helper signatures
in the main repo or adapt these calls locally; its source is not available here.

Ensure the existing package manifest includes `cloud_journey_agents.durability`
and the required dependencies from the `batch` extra in `src/pyproject.toml`:
SQLAlchemy and the PostgreSQL driver, the Cloud SQL connector if used, and FastAPI
for the optional HTTP adapter (Uvicorn to serve it). Merge dependency entries into
the main manifest; replacing its whole manifest is unnecessary. No session or
business database migration is needed to add the durable checkpoint capability.
