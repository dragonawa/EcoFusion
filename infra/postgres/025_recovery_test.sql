BEGIN;

INSERT INTO ecofusion.pipeline_runs (
    run_id,
    pipeline_name,
    layer,
    status,
    started_at
)
VALUES (
    '00000000-0000-4000-8000-000000000025',
    'bronze_recovery_test',
    'bronze',
    'running',
    NOW() - INTERVAL '10 minutes'
)
ON CONFLICT (run_id) DO NOTHING;

COMMIT;

SELECT
    run_id,
    pipeline_name,
    status,
    started_at
FROM ecofusion.pipeline_runs
WHERE run_id = '00000000-0000-4000-8000-000000000025';
