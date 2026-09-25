-- Apply to Durable State DB, using the schema-owner/migration identity.
\set ON_ERROR_STOP on
SET search_path TO public;
\ir 000_execution_checkpoints.sql
\ir 001_mcp_mutation_receipts.sql
