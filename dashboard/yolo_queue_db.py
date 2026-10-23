import argparse
import json
import math
import re
import sys
from pathlib import Path

import os
import psycopg2
from psycopg2.extras import RealDictCursor, Json

# ECOFUSION_WORKER_STANDALONE_DB_V1
def connect(read_only=True):
    required = (
        "POSTGRES_HOST",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
    )

    missing = [
        key for key in required
        if not os.environ.get(key)
    ]

    if missing:
        raise RuntimeError(
            "Missing database environment variables: "
            + ", ".join(missing)
        )

    connection = psycopg2.connect(
        host=os.environ["POSTGRES_HOST"],
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ["POSTGRES_DB"],
        connect_timeout=10,
    )

    if read_only:
        connection.set_session(readonly=True)

    return connection


def output(value):
    print(json.dumps(value, ensure_ascii=False))


def inspect():
    with connect() as db:
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT status, count(*) AS n
                FROM ecofusion.panorama_analysis_jobs
                GROUP BY status
            """)
            statuses = {
                r["status"]: int(r["n"])
                for r in cur.fetchall()
            }

            cur.execute("""
                SELECT count(*) AS n
                FROM ecofusion.gold_yolo_run_detections
            """)
            gold = int(cur.fetchone()["n"])

    output({
        "mode": "read_only",
        "statuses": statuses,
        "gold_results": gold
    })


def claim():
    with connect(read_only=False) as db:
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    j.job_id,
                    j.silver_id,
                    j.search_run_id,
                    r.selected_count
                FROM ecofusion.panorama_analysis_jobs j
                LEFT JOIN ecofusion.panorama_search_runs r
                  ON r.search_run_id = j.search_run_id
                 AND r.silver_id = j.silver_id
                WHERE j.status = 'queued'
                ORDER BY j.job_id
                FOR UPDATE OF j SKIP LOCKED
                LIMIT 1
            """)

            job = cur.fetchone()
            if job is None:
                output({"status": "empty"})
                return

            job_id = int(job["job_id"])
            sid = int(job["silver_id"])
            run_id = job["search_run_id"]
            expected_candidates = job["selected_count"]

            if run_id is None or expected_candidates is None:
                raise RuntimeError(
                    "Queued job lacks a valid search run: "
                    + str(job_id)
                )

            run_id = int(run_id)

            cur.execute("""
                SELECT count(*) AS n
                FROM ecofusion.panorama_search_run_candidates
                WHERE search_run_id = %s
            """, (run_id,))

            members = int(cur.fetchone()["n"])

            if (
                members < 1
                or members > 5
                or members != int(expected_candidates)
            ):
                raise RuntimeError(
                    "Invalid candidate membership for job "
                    + str(job_id)
                )

            cur.execute("""
                SELECT count(*) AS n
                FROM ecofusion.gold_yolo_run_detections
                WHERE search_run_id = %s
            """, (run_id,))
            existing = int(cur.fetchone()["n"])

            if existing != 0:
                raise RuntimeError(
                    "Gold results already exist for queued run "
                    + str(run_id)
                )

            cur.execute("""
                UPDATE ecofusion.panorama_analysis_jobs
                SET status = 'running',
                    started_at = now(),
                    finished_at = NULL,
                    error_message = NULL
                WHERE job_id = %s
                  AND status = 'queued'
                RETURNING job_id
            """, (job_id,))

            if cur.fetchone() is None:
                raise RuntimeError("Could not claim queued job")

        db.commit()

    output({
        "status": "claimed",
        "job_id": job_id,
        "silver_id": sid,
        "search_run_id": run_id,
        "candidates": members
    })


def validate_and_finish(job_id):
    with connect(read_only=False) as db:
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT job_id, silver_id, search_run_id, status
                FROM ecofusion.panorama_analysis_jobs
                WHERE job_id = %s
                FOR UPDATE
            """, (job_id,))

            job = cur.fetchone()

            if job is None or job["status"] != "running":
                raise RuntimeError("Job is not running")

            sid = int(job["silver_id"])
            run_id = int(job["search_run_id"])

            cur.execute("""
                SELECT m.candidate_id
                FROM ecofusion.panorama_search_run_candidates m
                JOIN ecofusion.gold_panorama_candidates c
                  ON c.candidate_id = m.candidate_id
                 AND c.silver_id = %s
                WHERE m.search_run_id = %s
                ORDER BY m.selection_order
            """, (sid, run_id))

            ids = [int(r["candidate_id"]) for r in cur.fetchall()]
            if not ids or len(ids) > 5 or len(ids) != len(set(ids)):
                raise RuntimeError("Invalid search-run candidates")

            root = Path(
                "/data/yolo/search_run_results"
            ) / f"run_{run_id}"

            expected_files = {
                f"candidate_{cid}_part{part}.json": (cid, part)
                for cid in ids
                for part in range(4, 8)
            }

            actual_files = {
                p.name for p in root.glob("*.json")
            } if root.is_dir() else set()

            if actual_files != set(expected_files):
                raise RuntimeError(
                    f"Result file mismatch: expected "
                    f"{len(expected_files)}, found {len(actual_files)}"
                )

            prepared = []

            for filename, (cid, part) in sorted(
                expected_files.items()
            ):
                data = json.loads(
                    (root / filename).read_text(encoding="utf-8")
                )

                required = {
                    "search_run_id": run_id,
                    "silver_id": sid,
                    "candidate_id": cid,
                    "image_part": part,
                    "model_name": "ecofusion_yolo11n_best.pt",
                    "image_path":
                        f"/images/{sid}/candidate_{cid}_part{part}.jpg"
                }

                for key, value in required.items():
                    if data.get(key) != value:
                        raise RuntimeError(
                            f"Invalid {key} in {filename}"
                        )

                threshold = float(
                    data.get("confidence_threshold", -1)
                )
                if not math.isfinite(threshold) or threshold != 0.5:
                    raise RuntimeError("Invalid confidence threshold")

                detections = data.get("detections")
                if not isinstance(detections, list):
                    raise RuntimeError("Invalid detections JSON")

                image = (
                    Path("/data/panorama_images")
                    / str(sid)
                    / f"candidate_{cid}_part{part}.jpg"
                )
                if not image.is_file():
                    raise RuntimeError(
                        f"Source image missing: {image.name}"
                    )

                for d in detections:
                    if not isinstance(d, dict):
                        raise RuntimeError("Invalid detection object")
                    if d.get("class") not in ("dump", "rubbish"):
                        raise RuntimeError("Unexpected object class")
                    if d.get("review_status") != "requires_review":
                        raise RuntimeError("Invalid review status")

                    confidence = float(d.get("confidence", -1))
                    if (
                        not math.isfinite(confidence)
                        or not 0.5 <= confidence <= 1
                    ):
                        raise RuntimeError("Invalid object confidence")

                    box = d.get("bbox_xyxy")
                    if not isinstance(box, list) or len(box) != 4:
                        raise RuntimeError("Invalid detection box")
                    coords = [float(x) for x in box]
                    if not all(
                        math.isfinite(x) and x >= 0 for x in coords
                    ):
                        raise RuntimeError("Invalid box coordinates")
                    if not (
                        coords[2] > coords[0]
                        and coords[3] > coords[1]
                    ):
                        raise RuntimeError("Invalid box geometry")

                prepared.append((
                    run_id, cid, part,
                    required["image_path"],
                    len(detections), detections
                ))

            cur.execute("""
                SELECT count(*) AS n
                FROM ecofusion.gold_yolo_run_detections
                WHERE search_run_id = %s
            """, (run_id,))

            if int(cur.fetchone()["n"]) != 0:
                raise RuntimeError(
                    "Existing YOLO Gold results; refusing overwrite"
                )

            for run, cid, part, image_path, count, det in prepared:
                cur.execute("""
                    INSERT INTO ecofusion.gold_yolo_run_detections
                    (
                        search_run_id, candidate_id, image_part,
                        image_path, model_name,
                        confidence_threshold, detection_count,
                        detections
                    )
                    VALUES (
                        %s, %s, %s, %s,
                        'ecofusion_yolo11n_best.pt',
                        0.50, %s, %s
                    )
                """, (
                    run, cid, part, image_path,
                    count, Json(det)
                ))

            cur.execute("""
                UPDATE ecofusion.panorama_analysis_jobs
                SET status = 'completed',
                    finished_at = now(),
                    error_message = NULL
                WHERE job_id = %s
                  AND status = 'running'
                RETURNING job_id
            """, (job_id,))

            if cur.fetchone() is None:
                raise RuntimeError("Job completion update failed")

        db.commit()

    output({
        "status": "completed",
        "job_id": job_id,
        "search_run_id": run_id,
        "saved_fragments": len(prepared),
        "detections": sum(r[4] for r in prepared)
    })


def fail(job_id, reason):
    with connect(read_only=False) as db:
        with db.cursor() as cur:
            cur.execute("""
                UPDATE ecofusion.panorama_analysis_jobs
                SET status = 'failed',
                    finished_at = now(),
                    error_message = %s
                WHERE job_id = %s
                  AND status = 'running'
                RETURNING job_id
            """, (reason[:1000], job_id))
            changed = cur.fetchone()

        db.commit()

    output({
        "status": "failed" if changed else "not_updated",
        "job_id": job_id
    })


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=("inspect", "claim", "finish", "fail")
    )
    parser.add_argument("--job-id", type=int)
    parser.add_argument("--reason", default="Worker execution failed")
    args = parser.parse_args()

    if args.action == "inspect":
        inspect()
    elif args.action == "claim":
        claim()
    elif args.action == "finish":
        if not args.job_id:
            parser.error("--job-id required")
        validate_and_finish(args.job_id)
    elif args.action == "fail":
        if not args.job_id:
            parser.error("--job-id required")
        fail(args.job_id, args.reason)


if __name__ == "__main__":
    main()
