"""Shared validation rules for identifiers, MCP calls, and local SQL reads.

Chat tools accept one Journey or APM lookup at a time. Batch identities
receive explicit tool allowlists for their own business operations. These
checks run before transport so requests outside those contracts fail early.

The SQL helper accepts structured SELECT statements and rejects write
constructs, including write CTEs. journey_db.py additionally enforces a
read-only database transaction because SQL shape alone cannot establish
that every called database function is free of side effects.
"""

import re


class GuardrailError(ValueError):
    pass


CANONICAL_APM_ID_PATTERN = re.compile(r"^APM00\d{4}$")
READ_TOOLS = frozenset({"get_journey_status", "get_journey_status_by_apm_id"})
BATCH_TOOLS = {
    "apm-validation-agent": frozenset({"get_journey_operation", "validate_apm"}),
    "ad-provisioning-agent": frozenset(
        {"get_journey_operation", "submit_ad_provisioning", "poll_ad_provisioning"}
    ),
    "app-factory-helper-agent": frozenset(
        {"get_journey_operation", "validate_app_factory"}
    ),
}


def normalize_apm_id(value: str) -> str:
    """Return the existing APM00#### representation used by the local demo."""
    compact = re.sub(r"[\s-]+", "", value.strip().upper())
    number = compact.removeprefix("APM")
    if len(number) == 4 and number.isdigit():
        number = f"00{number}"
    canonical = f"APM{number}"
    if not CANONICAL_APM_ID_PATTERN.fullmatch(canonical):
        raise GuardrailError(
            "APM ID must use the APM00#### convention, for example APM004001."
        )
    return canonical


def require_allowed_tool(name: str, allowed_tools: frozenset[str]) -> None:
    if name not in allowed_tools:
        raise GuardrailError(f"This agent cannot call MCP tool {name}")


def validate_read_arguments(name: str, arguments: dict) -> dict:
    """Chat exposes one authorized lookup at a time, with no write/bulk options."""
    require_allowed_tool(name, READ_TOOLS)
    key = "apm_id" if name == "get_journey_status_by_apm_id" else "journey_id"
    if set(arguments) != {key} or not isinstance(arguments[key], str):
        raise GuardrailError(f"{name} requires one {key}")
    value = arguments[key].strip()
    if not value:
        raise GuardrailError(f"{key} is required")
    return {key: normalize_apm_id(value) if key == "apm_id" else value}


def require_read_only_statement(statement) -> None:
    """Accept SQLAlchemy SELECT expressions; raw SQL and DML CTEs are rejected.

    The database must also enforce a read-only transaction. SQL syntax alone
    cannot prove that a function called by a SELECT has no side effects.
    """
    from sqlalchemy.sql import Select, visitors
    from sqlalchemy.sql.dml import Delete, Insert, Update
    from sqlalchemy.sql.elements import TextClause

    if not isinstance(statement, Select) or any(
        isinstance(node, (Delete, Insert, Update, TextClause))
        for node in visitors.iterate(statement)
    ):
        raise GuardrailError("Only structured read-only SELECT statements are allowed")
