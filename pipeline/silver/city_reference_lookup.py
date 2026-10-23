"""EcoFusion: offline lookup of Russian city mentions."""

import json
import re
from pathlib import Path

REFERENCE = Path("/data/reference/city_regions_index.json")

SPECIAL_CITIES = {
    "москва": [{
        "city": "Москва",
        "region": "Москва",
        "region_type": "г",
        "fias_id": None,
    }],
    "санкт-петербург": [{
        "city": "Санкт-Петербург",
        "region": "Санкт-Петербург",
        "region_type": "г",
        "fias_id": None,
    }],
}

# Common grammatical forms used in VK publications.
EXPLICIT_FORMS = {
    "калуга": r"\bкалуг(?:а|е|у|ой|ою|и)\b",
    "рязань": r"\bрязан(?:ь|и|ью)\b",
    "химки": r"\bхимк(?:и|ах|ами|ам|ов)\b",
    "москва": r"\bмоскв(?:а|е|у|ой|ою|ы)\b",
    "санкт-петербург": (
        r"\bсанкт-петербург(?:а|е|ом|у)?\b"
    ),
}


def load_reference(path=REFERENCE):
    data = json.loads(
        Path(path).read_text(encoding="utf-8-sig")
    )

    for name, records in SPECIAL_CITIES.items():
        data.setdefault(name, records)

    return data


def find_city_mentions(text, reference=None):
    if not text:
        return []

    data = reference if reference is not None else load_reference()
    normalized = re.sub(r"\s+", " ", text.casefold())

    found = []

    for key, records in data.items():
        if len(key) < 4:
            continue

        # Require complete words, not substrings.
        pattern = EXPLICIT_FORMS.get(
            key,
            rf"(?<!\w){re.escape(key)}(?!\w)"
        )

        match = re.search(pattern, normalized)
        if not match:
            continue

        found.append({
            "city": records[0]["city"],
            "matched_text": match.group(0),
            "regions": sorted({
                f'{row["region_type"]} {row["region"]}'.strip()
                for row in records
            }),
            "ambiguous": len({
                (row["region_type"], row["region"])
                for row in records
            }) > 1,
            "status": "city_mention_not_verified_location",
        })

    return sorted(found, key=lambda x: x["city"])


if __name__ == "__main__":
    reference = load_reference()

    samples = [
        "Свалка возле дома 21 на ул. Дальней, г. Калуга.",
        "Мусор во дворе дома в Химках.",
        "Свалка в Москве.",
        "Отходы в Санкт-Петербурге.",
        "Свалка около Рязани.",
    ]

    for text in samples:
        result = find_city_mentions(text, reference)
        print("TEXT:", text)
        print("CITIES:", [
            (item["city"], item["regions"])
            for item in result
        ])
        assert result, f"City not found: {text}"

    assert any(
        item["city"] == "Калуга"
        for item in find_city_mentions(samples[0], reference)
    )

    print("STEP 36.7.167: SUCCESS")

