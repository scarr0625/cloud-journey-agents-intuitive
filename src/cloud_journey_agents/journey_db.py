"""Retired direct-database helper retained to reject outdated agent integrations.

Every deployed agent must access business, durable, and session data through
Schwab MCP. The SQL-only local reference has moved under examples/local-business;
this compatibility entry point never opens or accepts a database connection.
"""

from .guardrails import GuardrailError


def read_rows(*args, **kwargs):
    raise GuardrailError("Direct database access is disabled; use Schwab MCP tools")
