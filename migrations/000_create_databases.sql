-- psql script. Connect to the postgres maintenance database as a migration owner.
-- CREATE DATABASE must run outside a transaction. Existing databases are retained.
\set ON_ERROR_STOP on

\if :{?business_db}
\else
  \set business_db cloud-journey-db
\endif
\if :{?durable_db}
\else
  \set durable_db durable-state-db
\endif
\if :{?session_db}
\else
  \set session_db session-db
\endif

SELECT :'business_db' <> :'durable_db'
   AND :'business_db' <> :'session_db'
   AND :'durable_db' <> :'session_db' AS separate_databases
\gset
\if :separate_databases
\else
  DO $$
  BEGIN
    RAISE EXCEPTION 'business_db, durable_db and session_db must be distinct.';
  END $$;
\endif

SELECT format('CREATE DATABASE %I', name)
FROM (VALUES (:'business_db'), (:'durable_db'), (:'session_db')) AS required(name)
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = required.name)
\gexec
