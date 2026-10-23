"""Discover actual JSON field paths in VK Bronze records."""

import json
import sys
from collections import Counter
from datetime import datetime, timezone

from pipeline.common.db import create_connection
from pipeline.bronze.ingest import create_s3_client


def walk(value, path="$", depth=0):
    """Yield JSON paths and value types, without printing full contents."""
    if isinstance(value, dict):
        yield path, "object", len(value)

        for key, child in value.items():
            child_path = f"{path}.{key}"
            yield child_path, type(child).__name__, None
            if depth < 8 and isinstance(child, (dict, list)):
                yield from walk(child, child_path, depth + 1)

    elif isinstance(value, list):
        yield path, "array", len(value)

        # Inspect only the first two elements to limit output.
        for index, child in enumerate(value[:2]):
            child_path = f"{path}[{index}]"
            yield child_path, type(child).__name__, None
            if depth < 8 and isinstance(child, (dict, list)):
                yield from walk(child, child_path, depth + 1)


def main():
    with create_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT object_id, source_file, bucket, object_key
                FROM ecofusion.bronze_objects
                WHERE object_key LIKE 'vk/publications/%%'
                ORDER BY object_id
                LIMIT 12
                """
            )
            records = cur.fetchall()

    if not records:
        print("ERROR: No VK Bronze objects registered.")
        sys.exit(1)

    client = create_s3_client()
    field_types = Counter()
    checked = 0
    errors = []

    print(f"Registered VK objects selected: {len(records)}")

    for object_id, filename, bucket, key in records:
        try:
            response = client.get_object(Bucket=bucket, Key=key)
            body = response["Body"]

            try:
                raw = body.read()
            finally:
                body.close()

            document = json.loads(raw.decode("utf-8-sig"))

            if not isinstance(document, dict):
                raise ValueError("Top-level JSON is not an object")

            print("\n" + "-" * 60)
            print(f"Object ID: {object_id}")
            print(f"File: {filename}")
            print(f"Top-level keys: {list(document.keys())}")

            for path, value_type, count in walk(document):
                field_types[(path, value_type)] += 1

            publication = document.get("publication")

            if isinstance(publication, dict):
                print("Publication keys:", list(publication.keys()))

                text_value = publication.get("text")
                print(
                    "Publication text length:",
                    len(text_value) if isinstance(text_value, str) else "N/A",
                )

                for field in (
                    "id", "owner_id", "from_id", "date",
                    "text", "attachments", "geo"
                ):
                    if field in publication:
                        value = publication[field]

                        if field == "text" and isinstance(value, str):
                            print("text preview:", repr(value[:180]))
                        elif field == "attachments" and isinstance(value, list):
                            print("attachment count:", len(value))
                            print(
                                "attachment types:",
                                [
                                    item.get("type", "unknown")
                                    for item in value[:10]
                                    if isinstance(item, dict)
                                ],
                            )
                        elif field == "geo":
                            print("geo type:", type(value).__name__)
                        else:
                            print(f"{field}: {value}")

            checked += 1

        except Exception as exc:
            errors.append({
                "object_id": object_id,
                "file": filename,
                "error": str(exc),
            })

    print("\n===== FIELD FREQUENCY SUMMARY =====")
    for (path, value_type), count in sorted(field_types.items()):
        if count >= 2 or path == "$":
            print(f"{count:>2}/{len(records)}  {value_type:<10} {path}")

    print("\n===== DISCOVERY SUMMARY =====")
    print("Objects selected:", len(records))
    print("Objects inspected:", checked)
    print("Errors:", len(errors))

    for error in errors:
        print(json.dumps(error, ensure_ascii=False))

    if errors:
        print("STEP 34.1: COMPLETED WITH ERRORS")
        sys.exit(1)

    print("STEP 34.1: SUCCESS - SCHEMA DISCOVERED")


if __name__ == "__main__":
    main()
