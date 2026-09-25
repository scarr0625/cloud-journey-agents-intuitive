"""Explicit local-development access to read-only Journey business data.

A developer supplies an engine and a structured SELECT with the appropriate
authorization filters. The helper checks the shared SQL guardrail, applies
database-level read-only protection, and returns rows as dictionaries.

Access requires ALLOW_LOCAL_DB_READS and is disabled on Cloud Run. Deployed
agents obtain business data through MCP; they do not use this helper when
MCP fails. This module owns neither business models nor connection setup.
"""

from cloud_journey_agents.config import local_database_reads_enabled
from cloud_journey_agents.guardrails import GuardrailError, require_read_only_statement


def read_rows(engine, statement, parameters: dict | None = None) -> list[dict]:
    """Execute a developer-supplied SELECT in a database-enforced read-only scope.

    Callers supply authorized filters; this helper is not a model-visible SQL tool.
    No business schema or default connection is created by the shared package.
    """
    if not local_database_reads_enabled():
        raise GuardrailError("Direct business database reads are disabled")
    require_read_only_statement(statement)
    dialect = engine.dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise GuardrailError("Local reads require PostgreSQL or SQLite")
    with engine.connect() as connection:
        if dialect == "sqlite":
            previous = connection.exec_driver_sql("PRAGMA query_only").scalar()
            connection.exec_driver_sql("PRAGMA query_only = ON")
        else:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        try:
            return [
                dict(row)
                for row in connection.execute(statement, parameters or {}).mappings()
            ]
        finally:
            connection.rollback()
            if dialect == "sqlite":
                connection.exec_driver_sql(f"PRAGMA query_only = {int(previous)}")
