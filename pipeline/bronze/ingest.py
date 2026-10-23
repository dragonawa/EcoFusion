"""EcoFusion Bronze ingestion with PostgreSQL audit."""

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from pipeline.common.registry import (
    start_run,
    register_object,
    add_event,
    finish_run,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["S3_ENDPOINT"],
        aws_access_key_id=os.environ["S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["S3_SECRET_KEY"],
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}),
    )


def object_exists(client, bucket, key):
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as error:
        code = error.response["Error"]["Code"]
        if code in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def ingest_file(path: Path, bucket: str):
    if not path.is_file():
        raise FileNotFoundError(path)

    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()
    registered = False

    try:
        start_run(run_id)
        registered = True

        add_event(run_id, "started", f"Processing {path.name}")

        file_hash = sha256_file(path)
        file_size = path.stat().st_size
        object_key = f"publications/{file_hash}/{path.name}"
        metadata_key = f"metadata/{run_id}.json"

        client = create_s3_client()

        if object_exists(client, bucket, object_key):
            status = "already_exists"
            final_status = "skipped"
        else:
            client.upload_file(str(path), bucket, object_key)
            status = "ingested"
            final_status = "completed"

        metadata = {
            "run_id": run_id,
            "source_file": path.name,
            "sha256": file_hash,
            "file_size_bytes": file_size,
            "ingested_at": started_at,
            "bucket": bucket,
            "object_key": object_key,
            "status": status,
        }

        client.put_object(
            Bucket=bucket,
            Key=metadata_key,
            Body=json.dumps(metadata, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json",
        )

        register_object(metadata, file_size)
        add_event(run_id, status, f"Object: {object_key}")
        finish_run(run_id, final_status)

        return metadata

    except Exception as error:
        if registered:
            try:
                add_event(run_id, "error", str(error))
                finish_run(run_id, "failed", str(error))
            except Exception:
                pass
        raise


if __name__ == "__main__":
    source = Path(
        os.environ.get(
            "SOURCE_FILE",
            "/app/data/samples/publication_001.json",
        )
    )

    bucket = os.environ.get("BRONZE_BUCKET", "ecofusion-bronze")

    result = ingest_file(source, bucket)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"BRONZE INGESTION: {result['status'].upper()}")
