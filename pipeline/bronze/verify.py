"""Verify consistency between PostgreSQL and Bronze S3."""

import hashlib
import json
import sys

from pipeline.common.db import create_connection
from pipeline.bronze.ingest import create_s3_client


def verify_object(client, record):
    object_id, expected_hash, bucket, key, expected_size = record

    digest = hashlib.sha256()
    total_size = 0

    response = client.get_object(Bucket=bucket, Key=key)
    body = response["Body"]

    try:
        for chunk in body.iter_chunks(chunk_size=1024 * 1024):
            if chunk:
                digest.update(chunk)
                total_size += len(chunk)
    finally:
        body.close()

    actual_hash = digest.hexdigest()
    hash_ok = actual_hash == expected_hash.strip().lower()

    size_ok = (
        expected_size is not None
        and total_size == expected_size
    )

    return {
        "object_id": object_id,
        "bucket": bucket,
        "object_key": key,
        "sha256_match": hash_ok,
        "size_match": size_ok,
        "verified_size": total_size,
        "status": "OK" if hash_ok and size_ok else "MISMATCH",
    }


def main():
    with create_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT object_id, sha256, bucket,
                       object_key, file_size_bytes
                FROM ecofusion.bronze_objects
                ORDER BY object_id
                """
            )
            records = cursor.fetchall()

    print(f"Registered objects: {len(records)}")

    if not records:
        print("CONSISTENCY CHECK: NO REGISTERED OBJECTS")
        sys.exit(1)

    client = create_s3_client()
    failures = 0

    for record in records:
        try:
            result = verify_object(client, record)
        except Exception as error:
            failures += 1
            print(json.dumps({
                "object_id": record[0],
                "status": "ERROR",
                "error": str(error),
            }, ensure_ascii=False))
            continue

        print(json.dumps(result, ensure_ascii=False, indent=2))

        if result["status"] != "OK":
            failures += 1

    if failures:
        print(f"CONSISTENCY CHECK: FAILED ({failures} objects)")
        sys.exit(1)

    print("CONSISTENCY CHECK: SUCCESS")


if __name__ == "__main__":
    main()
