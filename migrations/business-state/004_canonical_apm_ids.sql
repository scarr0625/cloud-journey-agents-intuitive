-- Normalize the original PoC identifiers to the canonical APM00#### format.

BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM journeys AS legacy
        JOIN journeys AS canonical
          ON canonical.apm_id = CASE legacy.apm_id
              WHEN '100401' THEN 'APM004001'
              WHEN '100402' THEN 'APM004002'
              WHEN '100403' THEN 'APM004003'
              WHEN '100404' THEN 'APM004004'
          END
        WHERE legacy.apm_id IN ('100401', '100402', '100403', '100404')
    ) THEN
        RAISE EXCEPTION
            'Both legacy and canonical Journey IDs exist; resolve duplicates before migration';
    END IF;
END $$;

UPDATE journeys
SET apm_id = CASE apm_id
    WHEN '100401' THEN 'APM004001'
    WHEN '100402' THEN 'APM004002'
    WHEN '100403' THEN 'APM004003'
    WHEN '100404' THEN 'APM004004'
END
WHERE apm_id IN ('100401', '100402', '100403', '100404');

INSERT INTO apm_group_assignments (apm_id, group_id)
VALUES
    ('APM004001', 'GROUP_1'),
    ('APM004002', 'GROUP_1'),
    ('APM004003', 'GROUP_2'),
    ('APM004004', 'GROUP_2')
ON CONFLICT (apm_id) DO UPDATE SET group_id = EXCLUDED.group_id;

DELETE FROM apm_group_assignments
WHERE apm_id IN ('100401', '100402', '100403', '100404');

COMMIT;
