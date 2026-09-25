-- Current baseline for a new Journey DB. Safe to repeat against this baseline.
-- 001-004 are historical PoC data upgrades, not required for fresh databases.
-- See migrations/README.md before upgrading an existing legacy database.
\set ON_ERROR_STOP on
SET search_path TO public;
\ir 000_initial_schema.sql
\ir 005_business_operation_progress.sql
