"""Database-owning handlers for Schwab's authenticated MCP host.

Every mutation and its retry receipt commit in one transaction. PostgreSQL
advisory locks serialize absent-row creation as well as updates; versions and
execution ownership fence stale agents. SQLite support exists for contract tests
in one reference process, not as a production concurrency implementation.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from threading import RLock
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import JSON, MetaData, and_, delete, func, insert, select, text, update

from .requests import REQUESTS

OPERATIONS = {
    "apm-validation-agent": ("apm-validation", "APM_VALIDATION"),
    "ad-provisioning-agent": ("ad-provisioning", "AD_SUBMISSION"),
    "app-factory-helper-agent": ("app-factory-validation", "APP_FACTORY_VALIDATION"),
}


class ToolError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code

    def mcp_result(self):
        return {"isError": True, "structuredContent": {
            "ok": False, "error": {"code": self.code, "message": str(self)},
        }}


@dataclass(frozen=True)
class Principal:
    """Construct only from host-verified workload/user credentials, never tool input."""

    workload: str
    user_id: str | None = None
    email: str = ""
    display_name: str = ""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class PersistenceService:
    def __init__(self, *, durable_engine, session_engine, batch_workloads,
                 chat_workloads, authorize_journey, lease_seconds=300):
        if not callable(authorize_journey) or lease_seconds <= 0:
            raise ValueError("A Journey authorization callback and positive lease are required")
        self.durable_engine, self.session_engine = durable_engine, session_engine
        self.batch_workloads, self.chat_workloads = dict(batch_workloads), dict(chat_workloads)
        self.authorize_journey, self.lease_seconds = authorize_journey, lease_seconds
        self._local_lock = RLock()
        self.durable = MetaData()
        self.durable.reflect(bind=durable_engine, only=[
            "agent_execution", "operation_checkpoint", "checkpoint_event", "mcp_mutation_receipt",
        ])
        self.sessions = MetaData()
        self.sessions.reflect(bind=session_engine, only=[
            "sessions", "events", "app_states", "user_states", "mcp_mutation_receipt",
            "mcp_session_revision", "mcp_session_event", "mcp_session_tombstone",
        ])
        if session_engine.dialect.name == "sqlite":
            # ADK's SQLite JSON adapter uses TEXT; reflection loses its codec.
            for name in ("sessions", "app_states", "user_states"):
                self.sessions.tables[name].c.state.type = JSON()
            self.sessions.tables["events"].c.event_data.type = JSON()

    def execute(self, tool, arguments, principal: Principal):
        if tool not in REQUESTS:
            raise ToolError("TOOL_UNAVAILABLE", "Unknown persistence tool")
        try:
            args = REQUESTS[tool].model_validate(arguments).model_dump(mode="json", by_alias=False)
        except ValidationError as exc:
            raise ToolError("INVALID_ARGUMENT", "Tool input does not match its schema") from exc
        if "durable" in tool:
            expected_agent = self.batch_workloads.get(principal.workload)
            if expected_agent != args["agent_name"] or expected_agent not in OPERATIONS:
                raise ToolError("FORBIDDEN", "Workload is not authorized for this agent")
            if not self.authorize_journey(principal, args["journey_id"]):
                raise ToolError("FORBIDDEN", "Journey operation is not authorized")
            resource = canonical([args["journey_id"], OPERATIONS[expected_agent][0]])
            return self._transaction(tool, args, principal, resource, resource,
                                     self.durable_engine, self.durable, self._durable)
        if (self.chat_workloads.get(principal.workload) != args["app_name"]
                or not principal.user_id or principal.user_id != args["user_id"]
                or not principal.email):
            raise ToolError("FORBIDDEN", "Verified workload/application/user scope is required")
        resource = canonical([args["app_name"], args["user_id"], args.get("session_id")])
        # App and user state can be shared by sessions: serialize their deltas too.
        lock_key = canonical(["session-app", args["app_name"]])
        return self._transaction(tool, args, principal, resource, lock_key,
                                 self.session_engine, self.sessions, self._session_tool)

    def _transaction(self, tool, args, principal, resource, lock_key, engine, metadata, handler):
        with self._local_lock, engine.begin() as connection:
            if engine.dialect.name == "postgresql":
                # Lock receipts before resources, including conflicting retries that
                # reuse a mutation ID for a different resource on another replica.
                lock_keys = []
                if args.get("mutation_id"):
                    lock_keys.append(canonical([
                        "receipt", principal.workload, principal.user_id, tool, args["mutation_id"],
                    ]))
                lock_keys.append(canonical(["resource", lock_key]))
                for key in lock_keys:
                    lock_id = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)
                    connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id})
            elif engine.dialect.name != "sqlite":
                raise ToolError("UNSUPPORTED_DATABASE", "Reference supports PostgreSQL and test SQLite")
            receipt = metadata.tables["mcp_mutation_receipt"]
            receipt_key = {
                "principal_id": digest([principal.workload, principal.user_id]),
                "tool_name": tool, "mutation_id": args.get("mutation_id"),
            }
            request_hash = digest(args)
            if args.get("mutation_id"):
                prior = connection.execute(select(receipt).filter_by(**receipt_key)).mappings().first()
                if prior:
                    if prior["request_hash"] != request_hash:
                        raise ToolError("IDEMPOTENCY_CONFLICT", "Mutation ID was reused with different input")
                    return prior["response"]
            # PostgreSQL CURRENT_TIMESTAMP is fixed at transaction start, before
            # lock waits. Evaluate the wall clock after acquiring the locks.
            clock = func.clock_timestamp() if engine.dialect.name == "postgresql" else func.current_timestamp()
            now = aware(connection.scalar(select(clock)))
            result = handler(connection, tool, args, principal, now, resource)
            if args.get("mutation_id"):
                connection.execute(insert(receipt).values(
                    **receipt_key, resource_key=resource, request_hash=request_hash,
                    response=result, created_at=now,
                ))
            return result

    def _snapshot(self, connection, checkpoint_id):
        cp = self.durable.tables["operation_checkpoint"]
        ex = self.durable.tables["agent_execution"]
        row = connection.execute(select(cp).where(cp.c.checkpoint_id == checkpoint_id)).mappings().one()
        execution = connection.execute(select(ex).where(ex.c.execution_id == row["latest_execution_id"])).mappings().one()
        keys = ("checkpoint_id", "journey_id", "latest_execution_id", "operation_key",
                "current_stage", "checkpoint_status", "external_reference", "result_reference",
                "last_error", "version")
        return {"checkpoint": {
            **{key: row[key] for key in keys}, "agent_name": execution["agent_name"],
            "workflow_run_id": execution["workflow_run_id"],
            "lease_expires_at": aware(row["lease_expires_at"]).isoformat() if row["lease_expires_at"] else None,
        }}

    def _durable(self, connection, tool, args, principal, now, resource):
        cp, ex, audit = (self.durable.tables[name] for name in (
            "operation_checkpoint", "agent_execution", "checkpoint_event"
        ))
        operation, initial_stage = OPERATIONS[args["agent_name"]]
        row = connection.execute(select(cp).where(
            cp.c.journey_id == args["journey_id"], cp.c.operation_key == operation
        ).with_for_update()).mappings().first()
        if tool == "claim_durable_operation":
            if row and row["lease_expires_at"]:
                if aware(row["lease_expires_at"]) > now:
                    raise ToolError("OPERATION_BUSY", "Operation has an active lease")
                connection.execute(update(ex).where(
                    ex.c.execution_id == row["latest_execution_id"], ex.c.execution_result == "RUNNING"
                ).values(execution_result="INTERRUPTED", ended_at=now, error="Execution lease expired"))
            execution_id = str(uuid4())
            connection.execute(insert(ex).values(
                execution_id=execution_id, journey_id=args["journey_id"], agent_name=args["agent_name"],
                workflow_run_id=args["workflow_run_id"], execution_result="RUNNING", started_at=now,
            ))
            values = dict(latest_execution_id=execution_id, updated_at=now,
                          lease_expires_at=now + timedelta(seconds=self.lease_seconds))
            if row:
                checkpoint_id = row["checkpoint_id"]
                connection.execute(update(cp).where(cp.c.checkpoint_id == checkpoint_id).values(
                    **values, version=row["version"] + 1,
                ))
            else:
                checkpoint_id = str(uuid4())
                connection.execute(insert(cp).values(
                    **values, checkpoint_id=checkpoint_id, journey_id=args["journey_id"],
                    operation_key=operation, current_stage=initial_stage,
                    checkpoint_status="PENDING", version=1,
                ))
                connection.execute(insert(audit).values(
                    checkpoint_event_id=str(uuid4()), checkpoint_id=checkpoint_id, execution_id=execution_id,
                    previous_status=None, new_status="PENDING", stage=initial_stage, created_at=now,
                ))
            return self._snapshot(connection, checkpoint_id)
        if not row or row["checkpoint_id"] != args["checkpoint_id"]:
            raise ToolError("STALE_CHECKPOINT", "Checkpoint does not match this operation")
        execution = connection.execute(select(ex).where(ex.c.execution_id == row["latest_execution_id"])).mappings().one()
        if (row["latest_execution_id"] != args["execution_id"] or row["version"] != args["expected_version"]
                or execution["workflow_run_id"] != args["workflow_run_id"]):
            raise ToolError("STALE_CHECKPOINT", "Checkpoint ownership or version changed")
        if not row["lease_expires_at"] or aware(row["lease_expires_at"]) <= now:
            raise ToolError("LEASE_EXPIRED", "The execution no longer owns an active lease")
        if tool == "save_durable_checkpoint":
            stage, status = args["current_stage"], args["checkpoint_status"]
            stages = {"AD_SUBMISSION", "AD_POLLING"} if operation == "ad-provisioning" else {initial_stage}
            if stage not in stages or (status == "WAITING" and operation != "ad-provisioning"):
                raise ToolError("INVALID_ARGUMENT", "Stage or status is invalid for this operation")
            if row["current_stage"] == "AD_POLLING" and stage != "AD_POLLING":
                raise ToolError("INVALID_ARGUMENT", "An AD request cannot return to submission")
            if status in {"COMPLETED", "WAITING"} and not args["result_reference"]:
                raise ToolError("INVALID_ARGUMENT", "Recorded progress needs its business result reference")
            if stage == "AD_POLLING" and not args["external_reference"]:
                raise ToolError("INVALID_ARGUMENT", "AD polling needs its provider request ID")
            if row["external_reference"] and args["external_reference"] != row["external_reference"]:
                raise ToolError("INVALID_ARGUMENT", "The provider request ID cannot change")
            changed = {key: args[key] for key in (
                "current_stage", "checkpoint_status", "external_reference", "result_reference", "last_error"
            )}
            connection.execute(update(cp).where(cp.c.checkpoint_id == row["checkpoint_id"]).values(
                **changed, updated_at=now, version=row["version"] + 1,
            ))
            connection.execute(insert(audit).values(
                checkpoint_event_id=str(uuid4()), checkpoint_id=row["checkpoint_id"],
                execution_id=args["execution_id"], previous_status=row["checkpoint_status"],
                new_status=status, stage=stage, created_at=now,
            ))
        else:
            connection.execute(update(cp).where(cp.c.checkpoint_id == row["checkpoint_id"]).values(
                lease_expires_at=None, updated_at=now, version=row["version"] + 1,
            ))
            connection.execute(update(ex).where(ex.c.execution_id == args["execution_id"]).values(
                execution_result="FAILED" if args["error"] else "SUCCEEDED",
                ended_at=now, error=args["error"],
            ))
        return self._snapshot(connection, row["checkpoint_id"])

    def _scope_where(self, table, args, *, session_column="session_id"):
        clauses = [table.c.app_name == args["app_name"], table.c.user_id == args["user_id"]]
        if "session_id" in args:
            clauses.append(table.c[session_column] == args["session_id"])
        return and_(*clauses)

    def _apply_state(self, connection, args, state, principal, now, *, initial=False):
        claims = {"auth:google_sub": principal.user_id, "auth:email": principal.email.lower(),
                  "auth:display_name": principal.display_name}
        for key, value in state.items():
            if not isinstance(key, str) or key.startswith("temp:"):
                raise ToolError("INVALID_ARGUMENT", "Temporary or invalid state keys cannot be persisted")
            if key.startswith("auth:") and (key not in claims or value != claims[key]):
                raise ToolError("FORBIDDEN", "Identity claims must match the verified user")
        local = {key: value for key, value in state.items() if not key.startswith(("app:", "user:"))}
        if initial:
            local.update(claims)
        for prefix, name in (("app:", "app_states"), ("user:", "user_states")):
            table = self.sessions.tables[name]
            identity = {"app_name": args["app_name"]}
            if prefix == "user:":
                identity["user_id"] = args["user_id"]
            delta = {key[len(prefix):]: value for key, value in state.items() if key.startswith(prefix)}
            if not delta:
                continue
            record = connection.execute(select(table).filter_by(**identity)).mappings().first()
            values = {"state": {**(record["state"] if record else {}), **delta}, "update_time": now.replace(tzinfo=None)}
            if record:
                connection.execute(update(table).filter_by(**identity).values(**values))
            else:
                connection.execute(insert(table).values(**identity, **values))
        return local

    def _session(self, connection, args, *, config=None, summary=False):
        sessions, revisions, events, sequence = (self.sessions.tables[name] for name in (
            "sessions", "mcp_session_revision", "events", "mcp_session_event"
        ))
        row = connection.execute(select(sessions).where(self._scope_where(sessions, args, session_column="id"))).mappings().first()
        if row is None:
            return None
        revision = connection.execute(select(revisions).where(self._scope_where(revisions, args))).mappings().first()
        if revision is None:
            raise ToolError("MIGRATION_REQUIRED", "Existing session needs a protocol revision backfill")
        state = dict(row["state"])
        for prefix, name in (("app:", "app_states"), ("user:", "user_states")):
            table = self.sessions.tables[name]
            keys = {"app_name": args["app_name"]}
            if prefix == "user:":
                keys["user_id"] = args["user_id"]
            shared = connection.execute(select(table).filter_by(**keys)).mappings().first()
            state.update({prefix + key: value for key, value in (shared["state"] if shared else {}).items()})
        join = and_(events.c.id == sequence.c.event_id, events.c.app_name == sequence.c.app_name,
                    events.c.user_id == sequence.c.user_id, events.c.session_id == sequence.c.session_id)
        stored_events = [] if summary else list(connection.scalars(select(events.c.event_data).select_from(
            events.join(sequence, join)
        ).where(self._scope_where(events, args)).order_by(sequence.c.sequence)))
        config = config or {}
        if config.get("after_timestamp") is not None:
            stored_events = [event for event in stored_events if event["timestamp"] >= config["after_timestamp"]]
        limit = config.get("num_recent_events")
        if limit is not None:
            stored_events = stored_events[-limit:] if limit else []
        return {"version": revision["version"], "session": {
            "app_name": args["app_name"], "user_id": args["user_id"], "id": args["session_id"],
            "state": {} if summary else state, "events": stored_events,
            "last_update_time": aware(row["update_time"]).timestamp(),
        }}

    def _session_tool(self, connection, tool, args, principal, now, resource):
        sessions, revisions, events, sequence, tombstone = (self.sessions.tables[name] for name in (
            "sessions", "mcp_session_revision", "events", "mcp_session_event", "mcp_session_tombstone"
        ))
        scope = {key: args[key] for key in ("app_name", "user_id", "session_id") if key in args}
        if tool == "list_agent_sessions":
            ids = connection.scalars(select(sessions.c.id).where(self._scope_where(sessions, args)).order_by(sessions.c.update_time, sessions.c.id))
            return {"sessions": [self._session(connection, {**scope, "session_id": sid}, summary=True) for sid in ids]}
        current = self._session(connection, scope)
        if tool == "get_agent_session":
            return self._session(connection, scope, config=args["config"])
        if tool == "create_agent_session":
            if current or connection.execute(select(tombstone).where(self._scope_where(tombstone, scope))).first():
                raise ToolError("ALREADY_EXISTS", "Session ID already exists or was retired")
            state = self._apply_state(connection, args, args["state"], principal, now, initial=True)
            connection.execute(insert(sessions).values(
                app_name=args["app_name"], user_id=args["user_id"], id=args["session_id"],
                state=state, create_time=now.replace(tzinfo=None), update_time=now.replace(tzinfo=None),
            ))
            connection.execute(insert(revisions).values(**scope, version=1))
            return self._session(connection, scope)
        if tool == "delete_agent_session":
            connection.execute(delete(sessions).where(self._scope_where(sessions, scope, session_column="id")))
            # Receipts can contain historical conversation text: retire them too.
            receipt = self.sessions.tables["mcp_mutation_receipt"]
            connection.execute(delete(receipt).where(receipt.c.resource_key == resource))
            if not connection.execute(select(tombstone).where(self._scope_where(tombstone, scope))).first():
                connection.execute(insert(tombstone).values(**scope, deleted_at=now))
            return {**scope, "deleted": True}
        if current is None:
            raise ToolError("SESSION_NOT_FOUND", "Session is unavailable")
        if current["version"] != args["expected_version"]:
            raise ToolError("STALE_SESSION", "Session revision changed; reload before continuing")
        event = args["event"]
        if event.get("partial") or not event.get("id"):
            raise ToolError("INVALID_ARGUMENT", "Only complete events with IDs can be persisted")
        event_identity = {**scope, "event_id": event["id"]}
        if connection.execute(select(sequence).filter_by(**event_identity)).first():
            raise ToolError("EVENT_ALREADY_RECORDED", "Event already recorded; reload the session")
        local = self._apply_state(connection, args, event["actions"].get("state_delta", {}), principal, now)
        row = connection.execute(select(sessions).where(self._scope_where(sessions, scope, session_column="id"))).mappings().one()
        new_version = current["version"] + 1
        connection.execute(insert(events).values(
            **scope, id=event["id"], invocation_id=event.get("invocation_id", ""),
            timestamp=datetime.fromtimestamp(event["timestamp"], timezone.utc).replace(tzinfo=None),
            event_data=event,
        ))
        connection.execute(insert(sequence).values(**event_identity, sequence=new_version, event_hash=digest(event)))
        connection.execute(update(sessions).where(self._scope_where(sessions, scope, session_column="id")).values(
            state={**row["state"], **local}, update_time=now.replace(tzinfo=None),
        ))
        connection.execute(update(revisions).where(self._scope_where(revisions, scope)).values(version=new_version))
        return self._session(connection, scope)
