-- Compatibility entry point for local PostgreSQL initialization.
-- Includes are relative to this file, both on the host and in Docker Compose.
\set ON_ERROR_STOP on
\ir ../migrations/bootstrap.sql
