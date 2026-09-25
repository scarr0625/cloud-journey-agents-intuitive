"""Typed input schemas for the eight MCP persistence tools.

The catalog is generated from these models so tool registration and validation
share the same argument contract. Authenticated identity is supplied by the host
outside these models; agent_name and user_id are assertions to verify, not trust.
"""

from typing import Annotated, Literal

from google.adk.events import Event
from pydantic import BaseModel, ConfigDict, Field, StrictInt

Text = Annotated[str, Field(min_length=1, pattern=r"\S")]
Agent = Literal["apm-validation-agent", "ad-provisioning-agent", "app-factory-helper-agent"]
Stage = Literal["APM_VALIDATION", "AD_SUBMISSION", "AD_POLLING", "APP_FACTORY_VALIDATION"]
Status = Literal["PENDING", "RUNNING", "WAITING", "COMPLETED", "FAILED"]


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Mutation(Request):
    mutation_id: Annotated[Text, Field(max_length=64)]


class Claim(Mutation):
    journey_id: Annotated[Text, Field(max_length=32)]
    agent_name: Agent
    workflow_run_id: Annotated[Text, Field(max_length=256)]


class Ownership(Claim):
    checkpoint_id: Annotated[Text, Field(max_length=36)]
    execution_id: Annotated[Text, Field(max_length=36)]
    expected_version: Annotated[StrictInt, Field(gt=0)]


class Save(Ownership):
    current_stage: Stage
    checkpoint_status: Status
    external_reference: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    result_reference: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    last_error: str | None = None


class Finish(Ownership):
    error: str | None = None


class SessionScope(Request):
    app_name: Annotated[Text, Field(max_length=128)]
    user_id: Annotated[Text, Field(max_length=128)]


class SessionKey(SessionScope):
    session_id: Annotated[Text, Field(max_length=128)]


class SessionMutation(SessionKey, Mutation):
    pass


class Create(SessionMutation):
    state: dict = Field(default_factory=dict)


class EventFilter(Request):
    num_recent_events: Annotated[StrictInt, Field(ge=0)] | None = None
    after_timestamp: float | None = None


class Get(SessionKey):
    config: EventFilter = Field(default_factory=EventFilter)


class Append(SessionMutation):
    expected_version: Annotated[StrictInt, Field(gt=0)]
    event: Event


REQUESTS = {
    "claim_durable_operation": Claim,
    "save_durable_checkpoint": Save,
    "finish_durable_operation": Finish,
    "create_agent_session": Create,
    "get_agent_session": Get,
    "list_agent_sessions": SessionScope,
    "append_agent_session_event": Append,
    "delete_agent_session": SessionMutation,
}

DESCRIPTIONS = {
    "claim_durable_operation": "Atomically acquire a Journey operation lease for an authorized batch workload.",
    "save_durable_checkpoint": "Save operation progress and audit using the current execution, lease, and version.",
    "finish_durable_operation": "Record the invocation outcome and release its lease without changing progress.",
    "create_agent_session": "Create a session scoped to the verified workload application and delegated user.",
    "get_agent_session": "Read an authorized ADK session and its concurrency version, with optional event filters.",
    "list_agent_sessions": "List session summaries within the verified application and user scope.",
    "append_agent_session_event": "Atomically append a complete ADK event, apply state deltas, and advance its session version.",
    "delete_agent_session": "Delete a scoped conversation and its replay data, retaining deletion protection.",
}


def tool_catalog():
    return [{
        "name": name, "description": DESCRIPTIONS[name],
        "inputSchema": model.model_json_schema(by_alias=False),
    } for name, model in REQUESTS.items()]
