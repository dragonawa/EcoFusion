"""EcoFusion VK collector for illegal dumps and waste reports."""

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from datetime import datetime, timezone

from pipeline.bronze.ingest import create_s3_client
from pipeline.common.registry import (
    start_run,
    register_object,
    add_event,
    finish_run,
)

API_URL = "https://api.vk.com/method/newsfeed.search"
API_VERSION = os.environ.get("VK_API_VERSION", "5.199")
BUCKET = os.environ.get("BRONZE_BUCKET", "ecofusion-bronze")

# Focus on the problem categories represented by the
# Environmental Ryazan Alliance dump map.
DEFAULT_QUERIES = [
    "несанкционированная свалка",
    "стихийная свалка",
    "свалка мусора",
    "свалка отходов",
    "мусорная свалка",
    "мусор в лесу",
    "мусор у реки",
    "мусор на берегу",
    "мусор у дороги",
    "захламление территории",
    "свалка строительного мусора",
    "свалили мусор",
    "сброс мусора",
    "незаконный сброс отходов",
    "незаконное размещение отходов",
    "ликвидация свалки",
    "уборка свалки",
    "убрали свалку",
    "вывоз мусора",
]


def search_vk(token, query, count):
    """Search VK public newsfeed."""
    form = urllib.parse.urlencode({
        "q": query,
        "count": count,
        "access_token": token,
        "v": API_VERSION,
    }).encode("utf-8")

    request = urllib.request.Request(
        API_URL,
        data=form,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "EcoFusion-VK-Collector/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise RuntimeError(
            f"VK HTTP error: {error.code}"
        ) from None
    except urllib.error.URLError as error:
        raise RuntimeError(
            f"VK connection error: {error.reason}"
        ) from None

    if "error" in data:
        error = data["error"]
        raise RuntimeError(
            f"VK API error {error.get('error_code')}: "
            f"{error.get('error_msg', 'Unknown error')}"
        )

    return data.get("response", {}).get("items", [])


def object_exists(client, bucket, key):
    """Return True only when the object exists."""
    from botocore.exceptions import ClientError

    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as error:
        details = error.response.get("Error", {})
        status = error.response.get(
            "ResponseMetadata", {}
        ).get("HTTPStatusCode")

        if (
            details.get("Code") in (
                "404", "NoSuchKey", "NotFound"
            )
            or status == 404
        ):
            return False

        raise


def collect():
    token = os.environ.get("VK_SERVICE_TOKEN", "").strip()

    if not token:
        raise RuntimeError("VK_SERVICE_TOKEN is not configured")

    count = int(os.environ.get("VK_COUNT", "10"))

    if not 1 <= count <= 100:
        raise ValueError("VK_COUNT must be between 1 and 100")

    configured = os.environ.get("VK_SEARCH_QUERIES", "")
    queries = (
        [q.strip() for q in configured.split(";") if q.strip()]
        if configured
        else DEFAULT_QUERIES
    )

    run_id = str(uuid.uuid4())
    registered_run = False
    new_objects = 0
    existing_objects = 0
    total_found = 0
    seen = set()

    try:
        start_run(run_id, pipeline_name="vk_collector")
        registered_run = True

        add_event(
            run_id,
            "started",
            f"VK collection started; queries={len(queries)}",
        )

        client = create_s3_client()

        for query in queries:
            items = search_vk(token, query, count)
            total_found += len(items)

            for item in items:
                owner_id = item.get("owner_id")
                post_id = item.get("id")

                if owner_id is None or post_id is None:
                    continue

                identity = (owner_id, post_id)

                # A post returned by several queries is processed once.
                if identity in seen:
                    continue

                seen.add(identity)

                # Deterministic source record: no collection timestamp
                # or search phrase that would change the content hash.
                payload = {
                    "source": "vk",
                    "publication": item,
                }

                content = json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")

                digest = hashlib.sha256(content).hexdigest()

                source_file = f"vk_{owner_id}_{post_id}.json"
                object_key = (
                    f"vk/publications/{owner_id}/{post_id}/"
                    f"{digest}.json"
                )

                if object_exists(client, BUCKET, object_key):
                    existing_objects += 1
                    status = "already_exists"
                else:
                    client.put_object(
                        Bucket=BUCKET,
                        Key=object_key,
                        Body=content,
                        ContentType="application/json; charset=utf-8",
                        Metadata={"sha256": digest},
                    )
                    new_objects += 1
                    status = "ingested"

                metadata = {
                    "sha256": digest,
                    "source_file": source_file,
                    "bucket": BUCKET,
                    "object_key": object_key,
                }

                register_object(metadata, len(content))

                add_event(
                    run_id,
                    status,
                    f"VK post owner={owner_id}, id={post_id}",
                )

            time.sleep(0.35)

        final_status = (
            "completed"
            if new_objects > 0 or total_found == 0
            else "skipped"
        )

        summary = {
            "run_id": run_id,
            "queries": len(queries),
            "publications_returned": total_found,
            "unique_publications": len(seen),
            "new_objects": new_objects,
            "existing_objects": existing_objects,
            "bucket": BUCKET,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "status": final_status,
        }

        add_event(
            run_id,
            "completed",
            json.dumps(summary, ensure_ascii=False),
        )
        finish_run(run_id, final_status)

        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"VK COLLECTOR: {final_status.upper()}")

    except Exception as error:
        if registered_run:
            try:
                add_event(run_id, "error", str(error))
                finish_run(run_id, "failed", str(error))
            except Exception:
                pass

        raise


if __name__ == "__main__":
    try:
        collect()
    except Exception as error:
        print(f"VK COLLECTOR ERROR: {error}", file=sys.stderr)
        sys.exit(1)
