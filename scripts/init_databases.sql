-- psql script, also safe to run against an existing volume. It never renames,
-- copies, or deletes existing business records.
SELECT format('CREATE DATABASE %I OWNER journey', name)
FROM (VALUES ('cloud-journey-db'), ('durable-state-db'), ('session-db')) AS required(name)
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = required.name)
\gexec
