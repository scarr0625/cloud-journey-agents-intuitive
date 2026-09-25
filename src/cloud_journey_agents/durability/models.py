"""Wire models for checkpoint state returned by Schwab MCP.

Agents hold validated snapshots, not ORM entities or database connections.
Schwab owns durable transactions, server-clock leases, and fencing versions.
Database schema and SQL handlers live only in the Schwab server-side reference.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator


class CheckpointStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class BatchAgent(str, Enum):
    APM_VALIDATION = "apm-validation-agent"
    AD_PROVISIONING = "ad-provisioning-agent"
    APP_FACTORY_HELPER = "app-factory-helper-agent"


class CheckpointStage(str, Enum):
    APM_VALIDATION = "APM_VALIDATION"
    AD_SUBMISSION = "AD_SUBMISSION"
    AD_POLLING = "AD_POLLING"
    APP_FACTORY_VALIDATION = "APP_FACTORY_VALIDATION"


class OperationCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    checkpoint_id: str = Field(min_length=1)
    journey_id: str = Field(min_length=1)
    agent_name: BatchAgent
    workflow_run_id: str = Field(min_length=1)
    latest_execution_id: str = Field(min_length=1)
    operation_key: str = Field(min_length=1)
    current_stage: CheckpointStage
    checkpoint_status: CheckpointStatus
    external_reference: str | None = None
    result_reference: str | None = None
    last_error: str | None = None
    version: StrictInt = Field(gt=0)
    lease_expires_at: datetime | None

    @field_validator("lease_expires_at")
    @classmethod
    def aware_lease(cls, value):
        if value is not None and value.utcoffset() is None:
            raise ValueError("MCP leases must include a timezone")
        return value
