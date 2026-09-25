-- Create three databases and their current schemas, without demo identities/data.
-- Usage: psql -X -U MIGRATION_OWNER -d postgres -f migrations/bootstrap.sql
\set ON_ERROR_STOP on
\ir 000_create_databases.sql

\connect :business_db
\ir business-state/apply.sql

\connect :durable_db
\ir durable-state/apply.sql

\connect :session_db
\ir session-state/apply.sql
