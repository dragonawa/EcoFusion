"""Inspect VK publication records stored in Bronze."""

import json
import os
import re
import sys

from pipeline.common.db import create_connection
from pipeline.bronze.ingest import create_s3_client


TEXT_KEYS = {
    "text", "message", "content", "description", "body"
}

DATE_KEYS = {
    "date", "published_at", "created_at", "timestamp"
}

ID_KEYS = {
    "id", "post_id", "owner_id", "from_id"
}


def find_values(obj, keys, path="$"):
    """Find selected fields in nested JSON without assuming its schema."""
    found = []

    if isinstance(obj, dict):
        for key, value in obj.items():
            current = f"{path}.{key}"

            if key.lower() in keys and value is not None:
                if isinstance(value, (str, int, float)):
                    found.append((current, str(value)))

            found.extend(find_values(value, keys, current))

    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            found.extend(
                find_values(value, keys, f"{path}[{index}]")
            )

    return found


def main():
    with create_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT object_id, source_file, bucket, object_key
                FROM ecofusion.bronze_objects
                WHERE object_key LIKE 'vk/publications/%%'
                ORDER BY object_id DESC
                LIMIT 50
                """
            )
            records = cur.fetchall()

    print(f"VK objects selected: {len(records)}")

    if not records:
        print("No VK objects found in Bronze.")
        sys.exit(1)

    client = create_s3_client()
    inspected = 0
    errors = 0
    empty_text = 0

    for object_id, filename, bucket, key in records:
        print("\n" + "=" * 65)
        print(f"Object ID: {object_id}")
        print(f"File: {filename}")
        print(f"S3 key: {key}")

        try:
            response = client.get_object(
                Bucket=bucket,
                Key=key,
            )

            body = response["Body"]
            try:
                raw = body.read()
            finally:
                body.close()

            data = json.loads(raw.decode("utf-8-sig"))
            texts = find_values(data, TEXT_KEYS)
            dates = find_values(data, DATE_KEYS)
            identifiers = find_values(data, ID_KEYS)

            # Prefer actual publication text fields.
            preferred = [
                (path, value)
                for path, value in texts
                if path.lower().endswith((".text", ".message"))
            ]

            selected_text = preferred or texts

            print("JSON: VALID")
            print("Text fields found:", len(texts))

            if selected_text:
                path, text = max(
                    selected_text,
                    key=lambda item: len(item[1])
                )
                print("Text field:", path)
                print("Text length:", len(text))
                print("Text preview:")
                print(text[:1200].replace("\x00", " "))
            else:
                empty_text += 1
                print("WARNING: No recognized text field found")

            print("Date fields:", json.dumps(
                dates[:5], ensure_ascii=False
            ))
            print("Identifier fields:", json.dumps(
                identifiers[:10], ensure_ascii=False
            ))

            inspected += 1

        except Exception as exc:
            errors += 1
            print("ERROR:", str(exc)[:500])

    print("\n" + "=" * 65)
    print("STEP 33.7 SUMMARY")
    print("Objects selected:", len(records))
    print("Objects inspected:", inspected)
    print("Objects with errors:", errors)
    print("Objects without recognized text:", empty_text)

    if errors:
        print("STEP 33.7: COMPLETED WITH ERRORS")
        sys.exit(1)

    print("STEP 33.7: INSPECTION COMPLETED")


if __name__ == "__main__":
    main()
