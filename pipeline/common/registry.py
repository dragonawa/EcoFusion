"""EcoFusion pipeline execution registry."""

from datetime import datetime, timezone

from pipeline.common.db import create_connection


def start_run(run_id, pipeline_name="bronze_ingestion"):
    with create_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ecofusion.pipeline_runs
                    (run_id, pipeline_name, layer, status)
                VALUES (%s, %s, 'bronze', 'running')
                """,
                (run_id, pipeline_name),
            )


def register_object(metadata, file_size):
    with create_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ecofusion.bronze_objects
                    (sha256, source_file, bucket, object_key,
                     file_size_bytes, last_checked_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (bucket, object_key)
                DO UPDATE SET
                    last_checked_at = NOW()
                """,
                (
                    metadata["sha256"],
                    metadata["source_file"],
                    metadata["bucket"],
                    metadata["object_key"],
                    file_size,
                ),
            )


def add_event(run_id, event_type, message):
    with create_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ecofusion.pipeline_events
                    (run_id, event_type, event_message)
                VALUES (%s, %s, %s)
                """,
                (run_id, event_type, message),
            )


def finish_run(run_id, status, error_message=None):
    if status not in ("completed", "failed", "skipped"):
        raise ValueError("Invalid final status")

    with create_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE ecofusion.pipeline_runs
                SET status = %s,
                    finished_at = %s,
                    error_message = %s
                WHERE run_id = %s
                """,
                (
                    status,
                    datetime.now(timezone.utc),
                    error_message,
                    run_id,
                ),
            )

            if cur.rowcount != 1:
                raise RuntimeError("Run ID not found")
