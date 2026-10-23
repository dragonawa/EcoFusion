BEGIN;

WITH recovered AS (
    UPDATE ecofusion.pipeline_runs
    SET
        status = 'failed',
        finished_at = NOW(),
        error_message = 'interrupted: stale test run'
    WHERE run_id = '00000000-0000-4000-8000-000000000025'
      AND pipeline_name = 'bronze_recovery_test'
      AND status = 'running'
      AND started_at < NOW() - INTERVAL '5 minutes'
    RETURNING run_id
)
INSERT INTO ecofusion.pipeline_events (
    run_id,
    event_type,
    event_message
)
SELECT
    run_id,
    'recovery',
    'Stale test run marked failed after interruption'
FROM recovered;

COMMIT;

SELECT
    run_id,
    pipeline_name,
    status,
    error_message,
    finished_at
FROM ecofusion.pipeline_runs
WHERE run_id = '00000000-0000-4000-8000-000000000025';
