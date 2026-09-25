-- Schwab MCP persistence protocol; apply with the database migration identity.
-- Existing ADK sessions require a reviewed revision/event-order backfill before use.

BEGIN;
SET LOCAL search_path TO public;

CREATE TABLE IF NOT EXISTS mcp_mutation_receipt (
	principal_id VARCHAR(64) NOT NULL,
	tool_name VARCHAR(64) NOT NULL,
	mutation_id VARCHAR(64) NOT NULL,
	resource_key VARCHAR(512) NOT NULL,
	request_hash VARCHAR(64) NOT NULL,
	response JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (principal_id, tool_name, mutation_id)
);

CREATE INDEX IF NOT EXISTS ix_mcp_mutation_receipt_resource_key ON mcp_mutation_receipt (resource_key);

CREATE TABLE IF NOT EXISTS mcp_session_tombstone (
	app_name VARCHAR(128) NOT NULL,
	user_id VARCHAR(128) NOT NULL,
	session_id VARCHAR(128) NOT NULL,
	deleted_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (app_name, user_id, session_id)
);

CREATE TABLE IF NOT EXISTS mcp_session_revision (
	app_name VARCHAR(128) NOT NULL,
	user_id VARCHAR(128) NOT NULL,
	session_id VARCHAR(128) NOT NULL,
	version BIGINT NOT NULL,
	PRIMARY KEY (app_name, user_id, session_id),
	CONSTRAINT ck_mcp_session_version CHECK (version > 0),
	FOREIGN KEY(app_name, user_id, session_id) REFERENCES sessions (app_name, user_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS mcp_session_event (
	app_name VARCHAR(128) NOT NULL,
	user_id VARCHAR(128) NOT NULL,
	session_id VARCHAR(128) NOT NULL,
	event_id VARCHAR(128) NOT NULL,
	sequence BIGINT NOT NULL,
	event_hash VARCHAR(64) NOT NULL,
	PRIMARY KEY (app_name, user_id, session_id, event_id),
	CONSTRAINT uq_mcp_session_event_sequence UNIQUE (app_name, user_id, session_id, sequence),
	FOREIGN KEY(event_id, app_name, user_id, session_id) REFERENCES events (id, app_name, user_id, session_id) ON DELETE CASCADE
);

COMMIT;
