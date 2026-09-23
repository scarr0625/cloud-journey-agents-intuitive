-- Run ONLY against durable-state-db. Journey IDs are logical references, not foreign keys.

BEGIN;

CREATE TABLE IF NOT EXISTS agent_execution (
    execution_id VARCHAR(36) NOT NULL,
    journey_id VARCHAR(32) NOT NULL,
    agent_name VARCHAR(64) NOT NULL,
    workflow_run_id VARCHAR(256) NOT NULL,
    execution_result VARCHAR(20) NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE NOT NULL,
    ended_at TIMESTAMP WITH TIME ZONE,
    error TEXT,
    PRIMARY KEY (execution_id),
    CONSTRAINT ck_execution_agent CHECK (agent_name IN ('apm-validation-agent', 'ad-provisioning-agent', 'app-factory-helper-agent'))
);

CREATE INDEX IF NOT EXISTS ix_agent_execution_journey_id ON agent_execution (journey_id);

CREATE INDEX IF NOT EXISTS ix_agent_execution_workflow_run_id ON agent_execution (workflow_run_id);

CREATE TABLE IF NOT EXISTS operation_checkpoint (
    checkpoint_id VARCHAR(36) NOT NULL,
    journey_id VARCHAR(32) NOT NULL,
    latest_execution_id VARCHAR(36) NOT NULL,
    operation_key VARCHAR(128) NOT NULL,
    current_stage VARCHAR(64) NOT NULL,
    checkpoint_status VARCHAR(20) NOT NULL,
    external_reference VARCHAR(256),
    result_reference VARCHAR(256),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    last_error TEXT,
    version INTEGER NOT NULL,
    lease_expires_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (checkpoint_id),
    CONSTRAINT uq_checkpoint_operation UNIQUE (journey_id, operation_key),
    CONSTRAINT ck_checkpoint_status CHECK (checkpoint_status IN ('PENDING', 'RUNNING', 'WAITING', 'COMPLETED', 'FAILED')),
    FOREIGN KEY(latest_execution_id) REFERENCES agent_execution (execution_id)
);

CREATE INDEX IF NOT EXISTS ix_operation_checkpoint_journey_id ON operation_checkpoint (journey_id);

CREATE TABLE IF NOT EXISTS checkpoint_event (
    checkpoint_event_id VARCHAR(36) NOT NULL,
    checkpoint_id VARCHAR(36) NOT NULL,
    execution_id VARCHAR(36) NOT NULL,
    previous_status VARCHAR(20),
    new_status VARCHAR(20) NOT NULL,
    stage VARCHAR(64) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (checkpoint_event_id),
    CONSTRAINT ck_event_new_status CHECK (new_status IN ('PENDING', 'RUNNING', 'WAITING', 'COMPLETED', 'FAILED')),
    CONSTRAINT ck_event_previous_status CHECK (previous_status IS NULL OR previous_status IN ('PENDING', 'RUNNING', 'WAITING', 'COMPLETED', 'FAILED')),
    FOREIGN KEY(checkpoint_id) REFERENCES operation_checkpoint (checkpoint_id),
    FOREIGN KEY(execution_id) REFERENCES agent_execution (execution_id)
);

CREATE INDEX IF NOT EXISTS ix_checkpoint_event_checkpoint_id ON checkpoint_event (checkpoint_id);

CREATE INDEX IF NOT EXISTS ix_checkpoint_event_execution_id ON checkpoint_event (execution_id);

COMMIT;
