# Local business simulator and preserved interactive demo

This package contains the original business models, JourneyState transition rules,
interactive orchestrator/playground, authorization fixtures, and simulated
external operations. It is excluded from all five production agent images.

Run commands from the repository root after installing `pip install -e ".[test]"`.
Use the root `.env.example` for local database configuration. `docker compose up -d`
starts PostgreSQL and creates the three databases on a new volume. For existing
volumes, `scripts/init_databases.sql` can add missing databases without deleting data.

```powershell
uvicorn journey_poc.main:app --port 8000
```

Open `/playground`. The old interactive creation/discovery/approval/provisioning
flow remains available here, with its original verified-user/group authorization.
The external approval simulator is now:

```powershell
python -m journey_poc.cloud_journey.approval_backend J-YOUR-ID --decision approve
```

For the local batch workflow, start with a Journey at APM_VALIDATED with captured
inventory, or an approved interactive request:

```powershell
$env:JOURNEY_WORKFLOW_BACKEND = "local"
python -m workflows.local_workflow --journey-id J-YOUR-ID --workflow-run-id demo
```

The demo creates missing local tables. Production jobs require centrally applied
migrations and use private MCP. See [LEGACY_DEMO.md](LEGACY_DEMO.md) for the preserved
interactive scenarios. The original design document is at the repository root.
