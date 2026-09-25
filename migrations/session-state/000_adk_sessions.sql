-- PostgreSQL session schema for google-adk 2.9.2, DatabaseSessionService v1.
-- Matches google.adk.sessions.schemas.v1; verify this file when upgrading ADK.
-- Session/app/user state and event payloads use JSONB, never checkpoint tables.

BEGIN;
SET LOCAL search_path TO public;

-- Do not relabel an existing legacy Pickle schema as JSON or overwrite its data.
DO $$
DECLARE
    existing_version TEXT;
BEGIN
    IF to_regclass('public.adk_internal_metadata') IS NOT NULL THEN
        SELECT value INTO existing_version
        FROM public.adk_internal_metadata WHERE key = 'schema_version';
        IF existing_version IS DISTINCT FROM '1' THEN
            RAISE EXCEPTION 'Unsupported ADK session schema version: %. Use the ADK migration tool first.', existing_version;
        END IF;
    ELSIF to_regclass('public.sessions') IS NOT NULL
       OR to_regclass('public.events') IS NOT NULL
       OR to_regclass('public.app_states') IS NOT NULL
       OR to_regclass('public.user_states') IS NOT NULL THEN
        RAISE EXCEPTION 'Unversioned session tables exist. Inspect/migrate the existing ADK database before applying this baseline.';
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS adk_internal_metadata (
    key VARCHAR(128) NOT NULL,
    value VARCHAR(256) NOT NULL,
    PRIMARY KEY (key)
);

CREATE TABLE IF NOT EXISTS app_states (
    app_name VARCHAR(128) NOT NULL,
    state JSONB NOT NULL,
    update_time TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    PRIMARY KEY (app_name)
);

CREATE TABLE IF NOT EXISTS sessions (
    app_name VARCHAR(128) NOT NULL,
    user_id VARCHAR(128) NOT NULL,
    id VARCHAR(128) NOT NULL,
    state JSONB NOT NULL,
    create_time TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    update_time TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    PRIMARY KEY (app_name, user_id, id)
);

CREATE TABLE IF NOT EXISTS user_states (
    app_name VARCHAR(128) NOT NULL,
    user_id VARCHAR(128) NOT NULL,
    state JSONB NOT NULL,
    update_time TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    PRIMARY KEY (app_name, user_id)
);

CREATE TABLE IF NOT EXISTS events (
    id VARCHAR(128) NOT NULL,
    app_name VARCHAR(128) NOT NULL,
    user_id VARCHAR(128) NOT NULL,
    session_id VARCHAR(128) NOT NULL,
    invocation_id VARCHAR(256) NOT NULL,
    timestamp TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    event_data JSONB,
    PRIMARY KEY (id, app_name, user_id, session_id),
    FOREIGN KEY(app_name, user_id, session_id)
        REFERENCES sessions (app_name, user_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_events_app_user_session_ts
    ON events (app_name, user_id, session_id, timestamp DESC);

INSERT INTO adk_internal_metadata (key, value)
VALUES ('schema_version', '1')
ON CONFLICT (key) DO NOTHING;

COMMIT;
