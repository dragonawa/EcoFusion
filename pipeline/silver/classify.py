"""Rule-based classification of environmental VK publications."""

import json
import re
import unicodedata

from pipeline.common.db import create_connection
from pipeline.silver.city_regions import CITY_REGIONS, CITY_REGION_MAP


RULES = {
    "illegal_dumping": [
        r"\bнесанкционированн\w*\s+свалк\w*",
        r"\bстихийн\w*\s+свалк\w*",
        r"\bсвалк\w*\s+мусор\w*",
        r"\bсвалк\w*\s+отход\w*",
        r"\bмусорн\w*\s+свалк\w*",
    ],
    "waste": [
        r"\bвывоз\w*\s+отход\w*",
        r"\bуборк\w*\s+мусор\w*",
        r"\bконтейнерн\w*\s+площадк\w*",
        r"\bпереполненн\w*\s+контейнер\w*",
        r"\bпереполненн\w*\s+мусорн\w*\s+контейнер\w*",
        r"\bбытов\w*\s+отход\w*",
        r"\bтверды\w*\s+коммунальн\w*\s+отход\w*",
        r"\bраздельн\w*\s+сбор\w*",
        r"\bпереработк\w*\s+отход\w*",
        r"\bвывоз\w*\s+мусор\w*",
    ],
    "water_pollution": [
        r"\bзагрязнен\w*\s+(?:вод\w*|рек\w*|озер\w*|мор\w*)",
        r"\bразлив\w*\s+(?:нефт\w*|нефтепродукт\w*)",
        r"\bзагрязнен\w*\s+сточн\w*\s+вод\w*",
        r"\bнеочищенн\w*\s+сток\w*",
    ],
    "air_pollution": [
        r"\bзагрязнен\w*\s+воздух\w*",
        r"\bвыброс\w*\s+(?:вредн\w*|загрязняющ\w*|токсичн\w*)",
        r"\bнеприятн\w*\s+запах\w*",
        r"\bдым\w*\s+(?:от|с)\s+предприят\w*",
    ],
    "forest": [
        r"\bнезаконн\w*\s+вырубк\w*",
        r"\bвырубк\w*\s+лес\w*",
        r"\bуничтожен\w*\s+лес\w*",
        r"\bлесн\w*\s+пожар\w*",
    ],
    "wildlife": [
        r"\bгибел\w*\s+(?:рыб\w*|животн\w*|птиц\w*)",
        r"\bбраконьер\w*",
        r"\bредк\w*\s+вид\w*",
        r"\bкрасн\w*\s+книг\w*",
    ],
    "environmental_complaint": [
        r"\bжалоб\w*",
        r"\bпрошу\s+проверить\b",
        r"\bобращени\w*\s+(?:в|к)\s+(?:прокуратур\w*|росприроднадзор\w*|администрац\w*)",
        r"\bтребу\w*\s+провер\w*",
        r"\bнарушен\w*\s+экологическ\w*\s+норм\w*",
    ],
    "environmental_action": [
        r"\bсубботник\w*",
        r"\bпосадк\w*\s+дерев\w*",
        r"\bуборк\w*\s+территори\w*",
        r"\bозеленен\w*",
        r"\bэкологическ\w*\s+акци\w*",
    ],
}

REGIONS = {
    "Республика Адыгея": [r"\bадыге[яи]\b"],
    "Республика Алтай": [r"\bреспублик\w+\s+алтай\b"],
    "Алтайский край": [r"\bалтайск\w+\s+кра[йяе]\b"],
    "Амурская область": [r"\bамурск\w+\s+област\w*\b"],
    "Архангельская область": [r"\bархангельск\w+\s+област\w*\b"],
    "Астраханская область": [r"\bастраханск\w+\s+област\w*\b"],
    "Белгородская область": [r"\bбелгородск\w+\s+област\w*\b"],
    "Брянская область": [r"\bбрянск\w+\s+област\w*\b"],
    "Владимирская область": [r"\bвладимирск\w+\s+област\w*\b"],
    "Волгоградская область": [r"\bволгоградск\w+\s+област\w*\b"],
    "Вологодская область": [r"\bвологодск\w+\s+област\w*\b"],
    "Воронежская область": [r"\bворонежск\w+\s+област\w*\b"],
    "Москва": [r"\bмоскв\w*\b"],
    "Московская область": [r"\bмосковск\w+\s+област\w*\b"],
    "Санкт-Петербург": [r"\bсанкт[\s-]*петербург\w*\b"],
    "Ленинградская область": [r"\bленинградск\w+\s+област\w*\b"],
    "Рязанская область": [r"\bрязанск\w+\s+област\w*\b"],
    "Ставропольский край": [r"\bставропольск\w+\s+кра[йяе]\b"],
    "Ханты-Мансийский автономный округ": [
        r"\bханты[\s-]*мансийск\w*\b",
        r"\bюгр\w*\b",
    ],
    "Краснодарский край": [r"\bкраснодарск\w+\s+кра[йяе]\b"],
    "Красноярский край": [r"\bкрасноярск\w+\s+кра[йяе]\b"],
    "Новосибирская область": [r"\bновосибирск\w+\s+област\w*\b"],
    "Омская область": [r"\bомск\w+\s+област\w*\b"],
    "Пермский край": [r"\bпермск\w+\s+кра[йяе]\b"],
    "Приморский край": [r"\bприморск\w+\s+кра[йяе]\b"],
    "Республика Башкортостан": [r"\bбашкортостан\w*\b"],
    "Республика Татарстан": [r"\bтатарстан\w*\b"],
    "Свердловская область": [r"\bсвердловск\w+\s+област\w*\b"],
    "Тульская область": [r"\bтульск\w+\s+област\w*\b"],
    "Тюменская область": [r"\bтюменск\w+\s+област\w*\b"],
    "Челябинская область": [r"\bчелябинск\w+\s+област\w*\b"],
    "Иркутская область": [r"\bиркутск\w+\s+област\w*\b"],
    "Калининградская область": [r"\bкалининградск\w+\s+област\w*\b"],
    "Камчатский край": [r"\bкамчатск\w+\s+кра[йяе]\b"],
    "Кемеровская область — Кузбасс": [
        r"\bкемеровск\w+\s+област\w*\b",
        r"\bкузбасс\w*\b",
    ],
    "Кировская область": [r"\bкировск\w+\s+област\w*\b"],
    "Костромская область": [r"\bкостромск\w+\s+област\w*\b"],
    "Курганская область": [r"\bкурганск\w+\s+област\w*\b"],
    "Курская область": [r"\bкурск\w+\s+област\w*\b"],
    "Липецкая область": [r"\bлипецк\w+\s+област\w*\b"],
    "Мурманская область": [r"\bмурманск\w+\s+област\w*\b"],
    "Нижегородская область": [r"\bнижегородск\w+\s+област\w*\b"],
    "Новгородская область": [r"\bновгородск\w+\s+област\w*\b"],
    "Оренбургская область": [r"\bоренбургск\w+\s+област\w*\b"],
    "Орловская область": [r"\bорловск\w+\s+област\w*\b"],
    "Пензенская область": [r"\bпензенск\w+\s+област\w*\b"],
    "Псковская область": [r"\bпсковск\w+\s+област\w*\b"],
    "Ростовская область": [r"\bростовск\w+\s+област\w*\b"],
    "Самарская область": [r"\bсамарск\w+\s+област\w*\b"],
    "Саратовская область": [r"\bсаратовск\w+\s+област\w*\b"],
    "Сахалинская область": [r"\bсахалинск\w+\s+област\w*\b"],
    "Смоленская область": [r"\bсмоленск\w+\s+област\w*\b"],
    "Тамбовская область": [r"\bтамбовск\w+\s+област\w*\b"],
    "Тверская область": [r"\bтверск\w+\s+област\w*\b"],
    "Томская область": [r"\bтомск\w+\s+област\w*\b"],
    "Ульяновская область": [r"\bульяновск\w+\s+област\w*\b"],
    "Ярославская область": [r"\bярославск\w+\s+област\w*\b"],
    "Республика Дагестан": [r"\bдагестан\w*\b"],
    "Республика Карелия": [r"\bкарели\w*\b"],
    "Республика Коми": [r"\bреспублик\w+\s+коми\b"],
    "Республика Крым": [r"\bкрым\w*\b"],
    "Республика Саха (Якутия)": [r"\bякут\w*\b", r"\bсаха\b"],
    "Республика Бурятия": [r"\bбуряти\w*\b"],
    "Республика Калмыкия": [r"\bкалмыки\w*\b"],
    "Республика Мордовия": [r"\bмордов\w*\b"],
    "Республика Тыва": [r"\bтыв\w*\b", r"\bтув\w*\b"],
    "Республика Хакасия": [r"\bхакаси\w*\b"],
    "Чеченская Республика": [r"\bчеченск\w+\s+республик\w*\b"],
    "Чувашская Республика": [r"\bчуваши\w*\b"],
    "Ямало-Ненецкий автономный округ": [r"\bямал\w*\b"],
    "Ненецкий автономный округ": [r"\bненецк\w*\s+автономн\w*\b"],
    "Еврейская автономная область": [r"\bеврейск\w+\s+автономн\w*\b"],
    "Забайкальский край": [r"\bзабайкальск\w+\s+кра[йяе]\b"],
    "Хабаровский край": [r"\bхабаровск\w+\s+кра[йяе]\b"],
    "Ивановская область": [r"\bивановск\w+\s+област\w*\b"],
    "Кабардино-Балкарская Республика": [
        r"\bкабардино[\s-]*балкар\w*\b"
    ],
    "Карачаево-Черкесская Республика": [
        r"\bкарачаево[\s-]*черкесск\w*\b"
    ],
    "Республика Северная Осетия — Алания": [
        r"\bсеверн\w+\s+осети\w*\b",
        r"\bалан\w*\b",
    ],
    "Республика Марий Эл": [r"\bмарий\s+эл\b"],
    "Удмуртская Республика": [r"\bудмурти\w*\b"],
}


def classify(text):
    normalized = unicodedata.normalize("NFC", text or "").lower()
    categories = []

    for category, patterns in RULES.items():
        if any(re.search(pattern, normalized) for pattern in patterns):
            categories.append(category)

    regions = [
        region
        for region, patterns in REGIONS.items()
        if any(re.search(pattern, normalized) for pattern in patterns)
    ]

    city_mentions = [
        city
        for city, patterns in CITY_REGIONS.items()
        if any(re.search(pattern, normalized) for pattern in patterns)
    ]

    city_region_hints = sorted({
        CITY_REGION_MAP[city]
        for city in city_mentions
        if city in CITY_REGION_MAP
    })

    # A region name alone is not proof of the incident location.
    # Multiple matches are retained as mentions, not resolved as fact.
    return {
        "categories": categories,
        "region_mentions": regions,
        "city_mentions": city_mentions,
        "city_region_hints": city_region_hints,
        "region": regions[0] if len(regions) == 1 else None,
        "region_confidence": (
            "single_explicit_mention" if len(regions) == 1
            else "ambiguous" if len(regions) > 1
            else "unknown"
        ),
        "needs_review": not bool(categories) or len(regions) != 1,
    }


def main():
    with create_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT silver_id, owner_id, publication_id, text
                FROM ecofusion.silver_publications
                ORDER BY silver_id
                """
            )
            records = cursor.fetchall()

            print(f"Silver publications to classify: {len(records)}")
            saved = 0

            for silver_id, owner_id, publication_id, text in records:
                result = classify(text)

                cursor.execute(
                    """
                    INSERT INTO ecofusion.silver_classifications (
                        silver_id,
                        owner_id,
                        publication_id,
                        categories,
                        region_mentions,
                        region,
                        region_confidence,
                        needs_review,
                        classifier_version,
                        classified_at
                    )
                    VALUES (
                        %s, %s, %s, %s::jsonb, %s::jsonb,
                        %s, %s, %s, %s, NOW()
                    )
                    ON CONFLICT (silver_id) DO UPDATE SET
                        owner_id = EXCLUDED.owner_id,
                        publication_id = EXCLUDED.publication_id,
                        categories = EXCLUDED.categories,
                        region_mentions = EXCLUDED.region_mentions,
                        region = EXCLUDED.region,
                        region_confidence = EXCLUDED.region_confidence,
                        needs_review = EXCLUDED.needs_review,
                        classifier_version = EXCLUDED.classifier_version,
                        classified_at = NOW()
                    """,
                    (
                        silver_id,
                        owner_id,
                        publication_id,
                        json.dumps(result["categories"], ensure_ascii=False),
                        json.dumps(result["region_mentions"], ensure_ascii=False),
                        result["region"],
                        result["region_confidence"],
                        result["needs_review"],
                        "35.4D",
                    ),
                )

                saved += 1
                print(json.dumps({
                    "silver_id": silver_id,
                    "owner_id": owner_id,
                    "publication_id": publication_id,
                    **result,
                    "saved": True,
                }, ensure_ascii=False))

    print(f"Classifications saved: {saved}")
    print("CLASSIFICATION PERSISTENCE: SUCCESS")


if __name__ == "__main__":
    main()



