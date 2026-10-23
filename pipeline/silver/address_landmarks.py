"""EcoFusion: conservative extraction of address landmarks."""

import re
from dataclasses import dataclass, asdict


# Street-type words, including common abbreviations and inflections.
TYPE = (
    r"(?:ул\.|улиц(?:а|е|у|ы|ей)|"
    r"проспект(?:е|а|у|ом)?|пр-т|"
    r"шоссе|ш\.|"
    r"переул(?:ок|ке|ка|ку|ком)|пер\.|"
    r"бульвар(?:е|а|у|ом)?|"
    r"проезд(?:е|а|у|ом)?|"
    r"набережн(?:ая|ой|ую))"
)

# Street type first: "ул. Комсомольская", "ш. Фрезер".
STREET_PREFIX = re.compile(
    rf"(?<!\w){TYPE}\s*"
    r"(?P<name>[А-ЯЁ][А-ЯЁа-яё0-9-]*"
    r"(?:\s+[А-ЯЁ][А-ЯЁа-яё0-9-]*){0,3})",
    re.IGNORECASE,
)

# Street name first: "Юбилейном проспекте".
STREET_SUFFIX = re.compile(
    rf"\b(?P<name>[А-ЯЁ][А-ЯЁа-яё0-9-]*)\s+{TYPE}\b",
)

HOUSE = re.compile(
    r"(?<!\w)(?:дом(?:а|е|ом|у)?|д\.)\s*"
    r"(?:№\s*)?(?P<number>\d+[А-Яа-я]?(?:/\d+)?)",
    re.IGNORECASE,
)



HOUSE_REVERSE = re.compile(
    r"(?<!\w)(?P<number>\d+[А-Яа-я]?(?:/\d+)?)"
    r"\s+(?:дом(?:а|е|ом|у)?)\b",
    re.IGNORECASE,
)

LANDMARK = re.compile(
    r"\b(?:гараж\w*|магазин\w*|остановк\w*|"
    r"подъезд\w*|здани\w*|въезд\w*|"
    r"контейнерн\w*\s+площадк\w*)\b",
    re.IGNORECASE,
)


@dataclass
class AddressLandmarks:
    street_candidates: list
    house_numbers: list
    landmarks: list
    geocoding_status: str


def extract_address_landmarks(text):
    if not text:
        return asdict(AddressLandmarks([], [], [], "insufficient_data"))

    text = re.sub(r"\s+", " ", text)

    streets = []

    for match in STREET_PREFIX.finditer(text):
        streets.append(match.group(0).strip().rstrip(".,;"))

    for match in STREET_SUFFIX.finditer(text):
        streets.append(match.group(0).strip().rstrip(".,;"))

    houses = [
        match.group("number")
        for pattern in (HOUSE, HOUSE_REVERSE)
        for match in pattern.finditer(text)
    ]

    landmarks = [
        match.group(0)
        for match in LANDMARK.finditer(text)
    ]

    return asdict(AddressLandmarks(
        street_candidates=list(dict.fromkeys(streets)),
        house_numbers=list(dict.fromkeys(houses)),
        landmarks=list(dict.fromkeys(landmarks)),
        geocoding_status="requires_verification",
    ))


if __name__ == "__main__":
    tests = [
        (
            "Свалка напротив дома 35 на ул. Комсомольская.",
            "Комсомольская",
            "35",
        ),
        (
            "Горы мусора у подъезда дома №66 на Юбилейном проспекте.",
            "Юбилейном проспекте",
            "66",
        ),
        (
            "Гигантская куча строительного мусора за гаражами по ш. Фрезер.",
            "Фрезер",
            None,
        ),
    ]

    for text, expected_street, expected_house in tests:
        result = extract_address_landmarks(text)
        print("TEXT:", text)
        print("RESULT:", result)

        assert any(
            expected_street.lower() in s.lower()
            for s in result["street_candidates"]
        ), f"Street not detected: {expected_street}"

        if expected_house is not None:
            assert expected_house in result["house_numbers"], (
                f"House not detected: {expected_house}"
            )

    print("STEP 36.7.145: SUCCESS")

