"""Refresh EcoFusion geographic candidates from PostgreSQL."""

import json
import os
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

from spatial_context import extract_spatial_context
from address_landmarks import extract_address_landmarks


TARGET = Path("/data/vk_geo_candidates.json")


def clean_streets(streets):
    result = []

    for street in streets:
        street = street.strip()

        if (
            len(street.split()) >= 2
            or re.match(r"^(?:ул\.|ш\.)\s*\S+", street, re.I)
        ):
            if street not in result:
                result.append(street)

    return result


def classify_address(text, address):
    streets = address["street_candidates"]
    houses = address["house_numbers"]
    landmarks = address["landmarks"]

    nearby = False

    for street in streets:
        for match in re.finditer(re.escape(street), text, re.I):
            fragment = text[
                max(0, match.start() - 120):
                min(len(text), match.end() + 120)
            ]

            for house in houses:
                if re.search(
                    rf"(?<!\d){re.escape(house)}(?!\d)",
                    fragment
                ):
                    nearby = True

    if streets and houses and nearby:
        return "street_house_nearby"

    if streets and houses:
        return "street_house_unlinked"

    if streets:
        return "street_candidate_only"

    if landmarks:
        return "landmark_candidate_only"

    return "insufficient_address_data"


def main():
    connection = psycopg2.connect(
        host=os.environ.get(
            "POSTGRES_HOST", "ecofusion-postgres"
        ),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        connect_timeout=10,
    )

    try:
        with connection.cursor(
            cursor_factory=RealDictCursor
        ) as cursor:
            cursor.execute("""
                SELECT silver_id, text
                FROM ecofusion.silver_publications
                ORDER BY silver_id
            """)
            publications = cursor.fetchall()
    finally:
        connection.close()

    results = []
    statistics = Counter()

    for publication in publications:
        sid = publication["silver_id"]
        text = publication["text"] or ""

        spatial = extract_spatial_context(text)

        if not spatial:
            continue

        address = extract_address_landmarks(text)

        address["street_candidates"] = clean_streets(
            address["street_candidates"]
        )

        status = classify_address(text, address)

        statistics[status] += 1

        results.append({
            "silver_id": sid,
            "spatial_relations": spatial,
            "address_landmarks": address,
            "address_status": status,
            "geocoding_status": "not_requested",
            "panorama_status": "not_requested",
        })

    # Validate before replacing the current file.
    identifiers = [x["silver_id"] for x in results]

    if len(identifiers) != len(set(identifiers)):
        raise RuntimeError("Duplicate silver_id in result")

    if not publications:
        raise RuntimeError("No publications found in PostgreSQL")

    if len(results) > len(publications):
        raise RuntimeError("Invalid candidate count")

    # Keep a timestamped backup.
    if TARGET.exists():
        stamp = datetime.now(timezone.utc).strftime(
            "%Y%m%d_%H%M%S_%f"
        )
        backup = TARGET.with_name(
            f"vk_geo_candidates_backup_{stamp}.json"
        )
        shutil.copy2(TARGET, backup)

    temporary = TARGET.with_suffix(".tmp")

    temporary.write_text(
        json.dumps(
            results,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    temporary.replace(TARGET)

    print("=== ECOFUSION GEO REFRESH ===")
    print("Publications in PostgreSQL:", len(publications))
    print("Spatial candidates:", len(results))

    for status, count in statistics.most_common():
        print(f"{status}: {count}")

    for sid in (4, 144, 170, 231, 283):
        item = next(
            (x for x in results if x["silver_id"] == sid),
            None
        )

        if item:
            print(
                f"ID {sid}:",
                item["address_landmarks"]["street_candidates"]
            )
        else:
            print(f"ID {sid}: no spatial candidate")

    print("Output:", TARGET)
    print("STEP 36.7.185: SUCCESS")


if __name__ == "__main__":
    main()
