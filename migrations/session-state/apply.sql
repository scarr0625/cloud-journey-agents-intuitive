-- Apply to Session DB, using the schema-owner/migration identity.
\set ON_ERROR_STOP on
SET search_path TO public;
\ir 000_adk_sessions.sql
\ir 001_mcp_session_protocol.sql
