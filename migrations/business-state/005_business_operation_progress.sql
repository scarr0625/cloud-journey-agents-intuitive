-- Run against the Journey business database. These are status projections, not execution checkpoints.

BEGIN;

CREATE TABLE IF NOT EXISTS journey_operation_status (
    journey_id VARCHAR(32) NOT NULL,
    operation_key VARCHAR(128) NOT NULL,
    stage VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    result_reference VARCHAR(256) NOT NULL,
    result JSON NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (journey_id, operation_key),
    FOREIGN KEY(journey_id) REFERENCES journeys (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS journey_external_dependency (
    journey_id VARCHAR(32) NOT NULL,
    dependency_key VARCHAR(128) NOT NULL,
    external_reference VARCHAR(256) NOT NULL,
    status VARCHAR(32) NOT NULL,
    observation_count INTEGER NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (journey_id, dependency_key),
    FOREIGN KEY(journey_id) REFERENCES journeys (id) ON DELETE CASCADE,
    UNIQUE (external_reference)
);

COMMIT;
