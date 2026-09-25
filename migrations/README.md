# Three independent state databases

These are **Schwab-side** migrations. Agent service accounts have no database
permissions: every agent reads/writes state through the authenticated Schwab MCP
server. See the [server reference](../references/schwab-mcp/README.md) for tool
handlers, schemas, ownership, and transaction rules.

These scripts target PostgreSQL 16 (also the local Compose version). Each state
store has its own database; Journey IDs in the durable database are logical
references, not cross-database foreign keys.

| Directory | Default database | Contents | Runtime consumer |
| --- | --- | --- | --- |
| `business-state/` | `cloud-journey-db` (Journey DB) | Journeys, group authorization, business audit, operation results, external dependencies | Client Data API; optional local business simulator |
| `durable-state/` | `durable-state-db` | Execution, checkpoint, audit, and `mcp_mutation_receipt` tables | Schwab MCP durable handlers |
| `session-state/` | `session-db` | ADK v1 tables plus MCP revisions, event sequence, deletion markers, and mutation receipts | Schwab MCP session handlers |

## Create all three databases and schemas

Run from the repository root with `psql`, using a migration identity that can
create databases and owns their schemas. Use a password prompt, `.pgpass`, or your
existing connection configuration. Do not wrap the bootstrap in `--single-transaction`:
PostgreSQL cannot run `CREATE DATABASE` in a transaction.

```powershell
psql -X -h 127.0.0.1 -U journey -d postgres -f migrations/bootstrap.sql
```

The default database names match the existing environment examples. If you want
the business database named `journey-db`, override it explicitly and update
`DATABASE_URL`/`DB_NAME` in its consumer:

```powershell
psql -X -h 127.0.0.1 -U journey -d postgres -v business_db=journey-db -f migrations/bootstrap.sql
```

`durable_db` and `session_db` can also be overridden. All three names must differ.
`000_create_databases.sql` creates only missing databases, owned by the connection
user. `bootstrap.sql` then connects to each database and runs that directory's
`apply.sql`. SQL errors stop the entire run, including nested scripts, through
[`ON_ERROR_STOP`](https://www.postgresql.org/docs/16/app-psql.html#APP-PSQL-VARIABLES).
Each numbered schema file has its own
transaction; the bootstrap is not atomic across databases. If a later database
fails, fix the error and rerun against the current baselines.

The bootstrap preserves existing databases and records. It is a current-schema
bootstrap, not a general migration/repair tool for arbitrary existing schemas.
It creates no application users, demo group memberships, or sample Journeys.

## Apply schemas to databases created separately

When Cloud SQL or your database team creates the databases, use each entry point
with that database's migration owner:

```powershell
psql -X -h DB_HOST -U MIGRATION_OWNER -d cloud-journey-db -f migrations/business-state/apply.sql
psql -X -h DB_HOST -U MIGRATION_OWNER -d durable-state-db -f migrations/durable-state/apply.sql
psql -X -h DB_HOST -U MIGRATION_OWNER -d session-db -f migrations/session-state/apply.sql
```

Schemas live in `public`. Give only Schwab server-side persistence identities
`CONNECT`, schema `USAGE`, and required table/sequence data privileges. Keep schema
ownership and database creation with the migration identity. No chat or batch
agent identity receives DB access, credentials, or Cloud SQL permissions. The
production business schema remains owned by Schwab's Data API; adapt this reference
through its existing migration pipeline rather than replacing production tables.

`durable-state/apply.sql` includes the checkpoint baseline and atomic mutation
receipt table. `session-state/apply.sql` includes ADK v1 and MCP protocol sidecars.
The receipt and actual state mutation must commit together on the MCP server.
These are fresh-schema/idempotent bootstrap scripts, not a migration ledger or
automatic upgrade of arbitrary existing installations.

## Business schema and historical migrations

For a **new database**, `business-state/apply.sql` runs:

1. `000_initial_schema.sql`: the current core tables, including normalized groups
   and Journey ownership.
2. `005_business_operation_progress.sql`: business operation projections and
   external provisioning references.

Files `001`–`004` are retained for the previous PoC database. They backfill owners,
seed example access groups, rename historical states, and normalize old APM IDs.
They are deliberately excluded from new-database initialization. For a legacy
upgrade, review its current schema and identity mappings, then apply the missing
numbered migrations in order using the existing migration history. In particular,
review the demo identities and group assignments in `002` and `004` before using
them outside the PoC. Do not automatically replay them on production authorization
data. No destructive refresh/normalization scripts under `scripts/` are invoked.

## Session schema compatibility

`session-state/000_adk_sessions.sql` matches **google-adk 2.9.2**, schema **v1**.
The agent, development, and server-reference manifests pin that release so an unattended dependency
upgrade cannot silently change the required session schema. The schema check in
`tests/test_migration_schemas.py` compares the checked-in table/index DDL to ADK's
PostgreSQL ORM definitions; review the migration and that test when upgrading ADK.

ADK session state and event payloads use JSONB. Its timestamps are naive UTC
(`TIMESTAMP WITHOUT TIME ZONE`), matching ADK's serializer. This differs from the
business and durable tables, which use timestamps with time zones.

The migration writes `adk_internal_metadata.schema_version = '1'` through the
`key`/`value` metadata row. It refuses unknown versions and existing unversioned
session tables; it never relabels legacy Pickle payloads as JSON. Migrate an
existing v0 session database with the ADK migration tooling before applying this
baseline. The MCP reference reflects existing tables and never creates them.

`001_mcp_session_protocol.sql` adds revisions, event order/hash, deletion markers,
and mutation receipts without changing ADK's base tables. Existing ADK sessions
require a reviewed offline backfill of revisions and event sequence/hash before
cutover. Without a revision the handlers return `MIGRATION_REQUIRED`. See the
server reference for backfill constraints; existing v1 data is not automatically
assigned a guessed order. All writers must use the MCP handlers after cutover.

## Local Docker Compose

```powershell
docker compose up -d
```

On a **new volume**, PostgreSQL's initialization hook calls `bootstrap.sql` and
creates all three schemas. Docker does not rerun initialization hooks for existing
volumes. To apply the baselines to an existing local instance without deleting its
volume:

```powershell
docker compose exec postgres psql -X -U journey -d postgres -f /migrations/bootstrap.sql
```

For a legacy local database, review the historical business/session upgrade notes
above first. The existing `scripts/init_databases.sql` is a compatibility wrapper
around the same bootstrap.
