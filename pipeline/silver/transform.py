"""Normalize existing VK Bronze JSON into PostgreSQL Silver."""

import json
import os
import sys
import unicodedata
from datetime import datetime, timezone

from botocore.config import Config
import boto3

from pipeline.common.db import create_connection


def create_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["S3_ENDPOINT"],
        aws_access_key_id=os.environ["S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["S3_SECRET_KEY"],
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}),
    )


def as_count(value):
    """Return a safe non-negative integer for VK counters."""
    if isinstance(value, dict):
        value = value.get("count", 0)

    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def normalize_text(value):
    if not isinstance(value, str):
        return ""
    return unicodedata.normalize("NFC", value).strip()


def normalize_publication(document):
    publication = document.get("publication")

    if not isinstance(publication, dict):
        raise ValueError("Missing publication object")

    owner_id = publication.get("owner_id")
    publication_id = publication.get("id")

    if owner_id is None or publication_id is None:
        raise ValueError("Publication has no owner_id or id")

    try:
        owner_id = int(owner_id)
        publication_id = int(publication_id)
    except (TypeError, ValueError) as error:
        raise ValueError("Invalid VK publication identifiers") from error

    timestamp = publication.get("date")
    published_at = None

    if timestamp is not None:
        try:
            published_at = datetime.fromtimestamp(
                int(timestamp), tz=timezone.utc
            )
        except (TypeError, ValueError, OverflowError, OSError):
            published_at = None

    attachments = publication.get("attachments") or []

    if not isinstance(attachments, list):
        attachments = []

    return {
        "owner_id": owner_id,
        "publication_id": publication_id,
        "from_id": publication.get("from_id"),
        "published_at": published_at,
        "text": normalize_text(publication.get("text")),
        "post_type": publication.get("post_type"),
        "likes_count": as_count(publication.get("likes")),
        "reposts_count": as_count(publication.get("reposts")),
        "comments_count": as_count(publication.get("comments")),
        "views_count": as_count(publication.get("views")),
        "attachments": attachments,
        "source": document.get("source"),
        "source_url": (
            f"https://vk.com/wall{owner_id}_{publication_id}"
        ),
        "raw_json": json.dumps(
            document, ensure_ascii=False, separators=(",", ":")
        ),
    }


def mark_status(conn, object_id, status, error=None):
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO ecofusion.silver_processing_status
                (source_object_id, status, error_message, processed_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (source_object_id)
            DO UPDATE SET
                status = EXCLUDED.status,
                error_message = EXCLUDED.error_message,
                processed_at = NOW()
            """,
            (object_id, status, error[:4000] if error else None),
        )


def transform_one(conn, client, record):
    object_id, filename, bucket, object_key = record

    response = client.get_object(Bucket=bucket, Key=object_key)
    body = response["Body"]

    try:
        document = json.loads(body.read().decode("utf-8-sig"))
    finally:
        body.close()

    if not isinstance(document, dict):
        raise ValueError("Top-level JSON must be an object")

    item = normalize_publication(document)

    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO ecofusion.silver_publications (
                source_object_id, owner_id, publication_id, from_id,
                published_at, text, post_type, likes_count,
                reposts_count, comments_count, views_count,
                attachment_count, source, source_url, raw_json,
                processed_at
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s::jsonb, NOW()
            )
            ON CONFLICT (owner_id, publication_id)
            DO UPDATE SET
                source_object_id = EXCLUDED.source_object_id,
                from_id = EXCLUDED.from_id,
                published_at = EXCLUDED.published_at,
                text = EXCLUDED.text,
                post_type = EXCLUDED.post_type,
                likes_count = EXCLUDED.likes_count,
                reposts_count = EXCLUDED.reposts_count,
                comments_count = EXCLUDED.comments_count,
                views_count = EXCLUDED.views_count,
                attachment_count = EXCLUDED.attachment_count,
                source = EXCLUDED.source,
                source_url = EXCLUDED.source_url,
                raw_json = EXCLUDED.raw_json,
                processed_at = NOW()
            RETURNING silver_id
            """,
            (
                object_id,
                item["owner_id"],
                item["publication_id"],
                item["from_id"],
                item["published_at"],
                item["text"],
                item["post_type"],
                item["likes_count"],
                item["reposts_count"],
                item["comments_count"],
                item["views_count"],
                len(item["attachments"]),
                item["source"],
                item["source_url"],
                item["raw_json"],
            ),
        )

        silver_id = cursor.fetchone()[0]

        # Rebuild child rows deterministically on every rerun.
        cursor.execute(
            "DELETE FROM ecofusion.silver_attachments WHERE silver_id = %s",
            (silver_id,),
        )

        for position, attachment in enumerate(item["attachments"]):
            if not isinstance(attachment, dict):
                continue

            attachment_type = str(
                attachment.get("type") or "unknown"
            )

            nested = attachment.get(attachment_type)
            attachment_id = (
                nested.get("id")
                if isinstance(nested, dict)
                else None
            )

            try:
                attachment_id = (
                    int(attachment_id)
                    if attachment_id is not None
                    else None
                )
            except (TypeError, ValueError):
                attachment_id = None

            payload = json.dumps(
                attachment, ensure_ascii=False, separators=(",", ":")
            )

            cursor.execute(
                """
                INSERT INTO ecofusion.silver_attachments
                    (silver_id, position, attachment_type,
                     attachment_id, payload)
                VALUES (%s, %s, %s, %s, %s::jsonb)
                """,
                (
                    silver_id, position, attachment_type,
                    attachment_id, payload,
                ),
            )

        mark_status(conn, object_id, "completed")

    return {
        "object_id": object_id,
        "file": filename,
        "silver_id": silver_id,
        "owner_id": item["owner_id"],
        "publication_id": item["publication_id"],
        "attachments": len(item["attachments"]),
        "status": "completed",
    }


def main():
    with create_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT object_id, source_file, bucket, object_key
                FROM ecofusion.bronze_objects
                WHERE object_key LIKE 'vk/publications/%%'
                ORDER BY object_id
                """
            )
            records = cursor.fetchall()

    if not records:
        print("No VK Bronze objects found.")
        sys.exit(1)

    client = create_s3_client()
    completed = 0
    failed = 0

    print(f"VK Bronze objects to process: {len(records)}")

    for record in records:
        object_id = record[0]

        try:
            # Each publication and its attachments commit together.
            with create_connection() as conn:
                result = transform_one(conn, client, record)

            print(json.dumps(result, ensure_ascii=False))
            completed += 1

        except Exception as error:
            failed += 1
            message = f"{type(error).__name__}: {error}"

            # Record the failure separately so other records can proceed.
            try:
                with create_connection() as conn:
                    mark_status(conn, object_id, "failed", message)
            except Exception as status_error:
                print(
                    f"Could not save failure status for {object_id}: "
                    f"{status_error}",
                    file=sys.stderr,
                )

            print(
                json.dumps({
                    "object_id": object_id,
                    "file": record[1],
                    "status": "failed",
                    "error": message,
                }, ensure_ascii=False),
                file=sys.stderr,
            )

    print("\n===== SILVER TRANSFORMATION SUMMARY =====")
    print(f"Completed: {completed}")
    print(f"Failed: {failed}")

    if failed:
        print("SILVER TRANSFORMATION: FAILED")
        sys.exit(1)

    print("SILVER TRANSFORMATION: SUCCESS")


if __name__ == "__main__":
    main()
