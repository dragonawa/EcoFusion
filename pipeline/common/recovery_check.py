"""EcoFusion pipeline recovery diagnostics."""

import json

from pipeline.common.db import create_connection


def main():
    with create_connection() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT status, COUNT(*)
                FROM ecofusion.pipeline_runs
                GROUP BY status
                ORDER BY status
            """)
            statuses = cur.fetchall()

            cur.execute("""
                SELECT run_id, started_at
                FROM ecofusion.pipeline_runs
                WHERE status = 'running'
                  AND started_at < NOW() - INTERVAL '5 minutes'
                ORDER BY started_at
            """)
            stale_runs = cur.fetchall()

            cur.execute("""
                SELECT COUNT(*)
                FROM ecofusion.bronze_objects
            """)
            object_count = cur.fetchone()[0]

            cur.execute("""
                SELECT COUNT(*)
                FROM ecofusion.pipeline_events
            """)
            event_count = cur.fetchone()[0]

    report = {
        "run_statuses": {
            status: count for status, count in statuses
        },
        "stale_runs": [
            {
                "run_id": str(run_id),
                "started_at": started_at.isoformat(),
            }
            for run_id, started_at in stale_runs
        ],
        "registered_objects": object_count,
        "recorded_events": event_count,
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))

    if stale_runs:
        print("RECOVERY DIAGNOSTICS: STALE RUNS DETECTED")
    else:
        print("RECOVERY DIAGNOSTICS: NO STALE RUNS")


if __name__ == "__main__":
    main()
