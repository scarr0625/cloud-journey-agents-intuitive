-- Schwab MCP persistence protocol; apply with the database migration identity.

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

COMMIT;
