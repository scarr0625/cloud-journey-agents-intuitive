"""Supplemental persistence-protocol tables used by the reference and migrations.

Existing durable and ADK v1 tables remain the underlying state stores. Mutation
receipts provide replay after lost replies; session revisions and event sequence
records supply optimistic concurrency and deterministic event ordering.
"""

from sqlalchemy import (
    BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint,
    JSON, String, Table, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB


def protocol_tables(metadata, *, sessions=False):
    Table("mcp_mutation_receipt", metadata,
        Column("principal_id", String(64), primary_key=True),
        Column("tool_name", String(64), primary_key=True),
        Column("mutation_id", String(64), primary_key=True),
        Column("resource_key", String(512), nullable=False, index=True),
        Column("request_hash", String(64), nullable=False),
        Column("response", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )
    if not sessions:
        return
    scope = ["app_name", "user_id", "session_id"]
    Table("mcp_session_revision", metadata,
        *(Column(key, String(128), primary_key=True) for key in scope),
        Column("version", BigInteger, nullable=False),
        CheckConstraint("version > 0", name="ck_mcp_session_version"),
        ForeignKeyConstraint(scope, ["sessions.app_name", "sessions.user_id", "sessions.id"], ondelete="CASCADE"),
    )
    Table("mcp_session_event", metadata,
        *(Column(key, String(128), primary_key=True) for key in scope),
        Column("event_id", String(128), primary_key=True),
        Column("sequence", BigInteger, nullable=False),
        Column("event_hash", String(64), nullable=False),
        UniqueConstraint(*scope, "sequence", name="uq_mcp_session_event_sequence"),
        ForeignKeyConstraint(
            ["event_id", *scope],
            ["events.id", "events.app_name", "events.user_id", "events.session_id"],
            ondelete="CASCADE",
        ),
    )
    Table("mcp_session_tombstone", metadata,
        *(Column(key, String(128), primary_key=True) for key in scope),
        Column("deleted_at", DateTime(timezone=True), nullable=False),
    )
