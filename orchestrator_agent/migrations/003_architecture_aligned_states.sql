-- Move active Journey projections from the original PoC names to the
-- architecture-aligned state names. Historical journey_events are left intact;
-- a migration transition is appended so the audit path ends at the new projection.

BEGIN;

INSERT INTO journey_events (
    journey_id,
    event_type,
    from_state,
    to_state,
    actor_type,
    actor_id,
    message,
    metadata
)
SELECT
    id,
    'STATE_TRANSITION',
    status,
    CASE status
        WHEN 'COLLECTING_INVENTORY' THEN 'COLLECTING_ASSET_INVENTORY'
        WHEN 'INVENTORY_COMPLETE' THEN 'ASSET_INVENTORY_COMPLETE'
        WHEN 'PROVISIONING' THEN 'CLOUD_BUILD_RUNNING'
        WHEN 'VALIDATING_RESULT' THEN 'VALIDATING_DEPLOYMENT'
    END,
    'SYSTEM',
    'migration-003',
    'Renamed active Journey state for the architecture-aligned workflow',
    '{"migration":"003_architecture_aligned_states"}'::json
FROM journeys
WHERE status IN (
    'COLLECTING_INVENTORY',
    'INVENTORY_COMPLETE',
    'PROVISIONING',
    'VALIDATING_RESULT'
);

UPDATE journeys
SET status = CASE status
        WHEN 'COLLECTING_INVENTORY' THEN 'COLLECTING_ASSET_INVENTORY'
        WHEN 'INVENTORY_COMPLETE' THEN 'ASSET_INVENTORY_COMPLETE'
        WHEN 'PROVISIONING' THEN 'CLOUD_BUILD_RUNNING'
        WHEN 'VALIDATING_RESULT' THEN 'VALIDATING_DEPLOYMENT'
        ELSE status
    END,
    current_step = CASE current_step
        WHEN 'collecting_inventory' THEN 'collecting_asset_inventory'
        WHEN 'inventory_complete' THEN 'asset_inventory_complete'
        WHEN 'provisioning' THEN 'cloud_build_running'
        WHEN 'validating_result' THEN 'validating_deployment'
        ELSE current_step
    END,
    version = version + 1
WHERE status IN (
    'COLLECTING_INVENTORY',
    'INVENTORY_COMPLETE',
    'PROVISIONING',
    'VALIDATING_RESULT'
);

COMMIT;
