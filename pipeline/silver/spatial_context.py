"""EcoFusion: conservative spatial context extraction from VK text."""

import re
from dataclasses import asdict, dataclass


WASTE = re.compile(
    r"\b(?:свалк\w*|мусор\w*|отход\w*|"
    r"помо[йе]к\w*|строительн\w+\s+хлам\w*)\b",
    re.IGNORECASE,
)

RULES = [
    (
        "OPPOSITE",
        r"(?:напротив|через дорогу)",
        "relative_opposite",
    ),
    (
        "BEHIND",
        r"(?:за домом|за зданием|за гаражами|позади здания)",
        "requires_orientation",
    ),
    (
        "FRONT",
        r"(?:перед домом|перед зданием|у подъезда|у входа)",
        "requires_orientation",
    ),
    (
        "BETWEEN",
        r"(?:между домами|между зданиями|между гаражами)",
        "requires_two_landmarks",
    ),
    (
        "NEAR",
        r"(?:рядом\s+с|рядом\s+со|возле|недалеко\s+от|"
        r"вблизи|около\s+(?!\d))",
        "proximity_only",
    ),
]

LANDMARK = re.compile(
    r"\b(?:дом\w*|здани\w*|гараж\w*|подъезд\w*|"
    r"магазин\w*|остановк\w*|дорог\w*|улиц\w*|"
    r"лес\w*|котлован\w*|двор\w*|въезд\w*)\b",
    re.IGNORECASE,
)


@dataclass
class SpatialCandidate:
    relation: str
    phrase: str
    context: str
    direction_strategy: str
    status: str


def extract_spatial_context(text):
    """Find plausible waste-related spatial relationships.

    These are candidates, not resolved geographic coordinates.
    """
    if not text:
        return []

    normalized = re.sub(r"\s+", " ", text)
    sentences = re.split(r"(?<=[.!?])\s+", normalized)

    candidates = []

    for sentence in sentences:
        waste_matches = list(WASTE.finditer(sentence))

        if not waste_matches:
            continue

        for relation, pattern, strategy in RULES:
            for match in re.finditer(
                pattern, sentence, re.IGNORECASE
            ):
                # The relation must be sufficiently close to waste.
                distance = min(
                    min(
                        abs(match.start() - w.end()),
                        abs(w.start() - match.end())
                    )
                    for w in waste_matches
                )

                if distance > 100:
                    continue

                # A spatial relation requires an identifiable
                # landmark. Avoid inventing an object.
                has_landmark = bool(
                    LANDMARK.search(sentence)
                )

                candidates.append(
                    SpatialCandidate(
                        relation=relation,
                        phrase=match.group(0),
                        context=sentence[:240],
                        direction_strategy=strategy,
                        status=(
                            "candidate"
                            if has_landmark
                            else "missing_landmark"
                        ),
                    )
                )

    return [asdict(item) for item in candidates]


if __name__ == "__main__":
    samples = {
        "True opposite":
            "Свалка напротив дома 35 на улице Комсомольской.",
        "True behind":
            "Гигантская куча строительного мусора за гаражами по шоссе Фрезер.",
        "True front":
            "Жители жалуются на горы мусора у подъезда дома 66.",
        "Quantity false positive":
            "Уберут свалки объёмом около 4 тысяч кубометров.",
        "Unrelated behind":
            "Самое сложное позади. Земля набита ветками.",
        "Unrelated garages":
            "Из-за гаражей появлялись животные.",
        "Near forest":
            "Мусор рядом с лесом.",
    }

    for name, text in samples.items():
        results = extract_spatial_context(text)
        print(f"{name}: {results}")

    print("STEP 36.7.136: SUCCESS")
