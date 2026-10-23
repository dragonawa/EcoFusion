"""EcoFusion local educational dashboard. Read-only PostgreSQL access."""
import datetime as dt
import json
import os
import re
from registry_lookup import lookup_registry
from panorama_planner import plan_parcel_search
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError as exc:
    raise SystemExit('Для дашборда требуется драйвер psycopg2 (образ ecofusion-dashboard:dev)') from exc

HERE = Path(__file__).resolve().parent
PORT = int(os.environ.get('DASHBOARD_PORT', '8787'))

def load_json_file(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        return {int(r['silver_id']): r for r in data}
    except (FileNotFoundError, OSError, ValueError, KeyError, TypeError):
        return {}

GEO = load_json_file('/data/vk_geo_candidates.json')
REVIEW = load_json_file('/data/vk_geocoding_review.json')

# Supplementary, read-only lookup. City mentions are NOT incident coordinates.
CITY_REFERENCE_PATH = Path('/data/reference/city_regions_index.json')
try:
    CITY_REFERENCE = json.loads(CITY_REFERENCE_PATH.read_text(encoding='utf-8-sig'))
except (FileNotFoundError, OSError, ValueError):
    CITY_REFERENCE = {}
CITY_REFERENCE.setdefault('москва', [{'city':'Москва','region':'Москва','region_type':'г'}])
CITY_REFERENCE.setdefault('санкт-петербург', [{'city':'Санкт-Петербург','region':'Санкт-Петербург','region_type':'г'}])
CITY_FORMS = {
    'калуга': r'калуг(?:а|е|у|ой|ою|и)',
    'химки': r'химк(?:и|ах|ами|ам|ов)',
    'москва': r'москв(?:а|е|у|ой|ою|ы)',
    'санкт-петербург': r'санкт-петербург(?:а|е|ом|у)?',
    'рязань': r'рязан(?:ь|и|ью)',
}
# Fast lookup: matching actual words/phrases is much cheaper than running
# 1082 separate regular expressions for every record in the journal.
from functools import lru_cache
CITY_EXPLICIT_PATTERNS = [
    (re.compile(r'(?<!\w)(?:' + pattern + r')(?!\w)', re.I), key)
    for key, pattern in CITY_FORMS.items()
]


def readable_region(row):
    region = (row.get('region') or '').strip()
    rt = (row.get('region_type') or '').strip()
    if rt == 'обл': return region + ' область'
    if rt == 'Респ': return 'Республика ' + region
    if rt == 'край': return region + ' край'
    if rt == 'г': return 'город ' + region
    return (rt + ' ' + region).strip()


@lru_cache(maxsize=2048)
def _cached_city_mentions(text):
    if not text:
        return ()
    words = re.findall(r'[а-яё]+(?:-[а-яё]+)*', text.casefold())
    keys = set()
    # Exact names, including multiword cities, without looping over the CSV.
    for width in range(1, 5):
        keys.update(' '.join(words[i:i + width]) for i in range(len(words) - width + 1))
    matched_keys = {key for key in keys if key in CITY_REFERENCE and len(key) >= 4}
    for pattern, key in CITY_EXPLICIT_PATTERNS:
        if pattern.search(text):
            matched_keys.add(key)
    found = {}
    for key in matched_keys:
        rows = CITY_REFERENCE.get(key, [])
        regions = sorted({readable_region(r) for r in rows if r.get('region')})
        for city in {r.get('city') for r in rows if r.get('city')}:
            found[city] = {'city':city, 'regions':regions,
                           'ambiguous':len(regions) != 1,
                           'basis':'упоминание в тексте; место события не подтверждено'}
    return tuple(sorted(found.values(), key=lambda x: x['city']))


def lookup_city_mentions(text):
    return list(_cached_city_mentions(text or ''))

def connect(read_only=True):
    return psycopg2.connect(
        host=os.environ.get('POSTGRES_HOST', 'ecofusion-postgres'),
        port=int(os.environ.get('POSTGRES_PORT', 5432)),
        dbname=os.environ.get('POSTGRES_DB'),
        user=os.environ.get('POSTGRES_USER'),
        password=os.environ.get('POSTGRES_PASSWORD'),
        connect_timeout=5,
        options='-c default_transaction_read_only=on' if read_only else '-c default_transaction_read_only=off',
    )

def safe_value(value):
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return str(value)

# Display-only contextual hints, not a geocoder or a verified incident location.
DISTRICT_SINGLE = re.compile(r'(?<!\w)([А-ЯЁ][а-яё-]{3,50}\s+муниципальн(?:ого|ом)\s+округ(?:а|е))', re.I)
DISTRICT_PAIR = re.compile(r'(?<!\w)([А-ЯЁ][а-яё-]{3,50})\s+и\s+([А-ЯЁ][а-яё-]{3,50})\s+муниципальн(?:ых|ые)\s+округ(?:ов|а)', re.I)
FORESTRY = re.compile(r'ГКУ\s+КО\s+[«\"]([^»\"]*лесничеств\w*)[»\"]', re.I)
FOREST_AREA = re.compile(r'лес(?:ах|а|у|ом|е)\s+([А-ЯЁ][а-яё-]+)', re.I)

def geographical_evidence(text):
    text = text or ''
    districts=[]
    for m in DISTRICT_PAIR.finditer(text):
        districts.append(m.group(0))
    districts.extend(m.group(1) for m in DISTRICT_SINGLE.finditer(text))
    forests=[m.group(1) for m in FORESTRY.finditer(text)]
    areas=['леса ' + m.group(1) for m in FOREST_AREA.finditer(text)]
    return {'municipal_districts':list(dict.fromkeys(districts))[:10],
            'forestries':list(dict.fromkeys(forests))[:10],
            'other_territories':list(dict.fromkeys(areas))[:10]}

def coherent_address_hint(sid, text, cities):
    # Require a single city/region, a single street and house, and a local
    # waste/relative-location phrase. This is an address for manual map search,
    # not evidence that dumping exists at a precise coordinate.
    geo=GEO.get(sid) or {}
    a=geo.get('address_landmarks') or {}
    streets=a.get('street_candidates') or []
    houses=a.get('house_numbers') or []
    matches=[x for x in cities if not x.get('ambiguous') and len(x.get('regions') or [])==1]
    if len(cities)!=1 or len(matches)!=1 or len(streets)!=1 or len(houses)!=1:
        return None
    if not geo.get('spatial_relations') or not WASTE_TERMS.search(text):
        return None
    # The address must be a single combined clue, not disconnected tokens.
    # Previous proximity check is necessary but not proof of event linkage.
    if geo.get('address_status') != 'street_house_nearby':
        return None
    relations=geo.get('spatial_relations') or []
    if not any(r.get('relation') in ('NEAR','OPPOSITE','BEHIND','FRONT') and
               r.get('status')=='candidate' for r in relations):
        return None
    # Explicit several incident localities precludes this optimistic hint.
    return {'city':matches[0]['city'],'region':matches[0]['regions'][0],
            'street':streets[0], 'house':houses[0],
            'basis':'адрес и отношение к отходам связаны в тексте; требуется проверка человеком'}

def summarize_geo(sid, text="", cities=None, evidence=None, linked=None):
    cities = cities or []
    evidence = evidence or {}
    item = GEO.get(sid) or {}
    review = (REVIEW.get(sid) or {}).get("review") or {}
    address = item.get("address_landmarks") or {}
    status = item.get("address_status")

    # An address or generic landmark alone does not imply waste.
    if not WASTE_TERMS.search(text or ""):
        return {
            "level": "low",
            "label": "Нет явных признаков отходов",
            "reason": "В тексте не обнаружены прямые признаки мусора или свалки. Географические слова сами по себе не основание для панорамной проверки."
        }

    if linked:
        return {
            "level": "promising",
            "label": "Можно искать место на карте",
            "reason": "В тексте связаны населённый пункт, дом, улица и размещение отходов. Координаты и наличие свалки не подтверждены."
        }

    if review.get("status") == "multiple_locations":
        return {
            "level": "medium",
            "label": "Несколько мест — требуется уточнение",
            "reason": "Сообщение содержит несколько территорий или событий. Нельзя автоматически выбрать одну точку для панорамы."
        }

    if evidence.get("municipal_districts") or evidence.get("forestries"):
        return {
            "level": "medium",
            "label": "Известна территория, не точка",
            "reason": "Указаны муниципальные округа или лесничества. Точные координаты отходов неизвестны."
        }

    if review.get("status") == "ready_for_geocoding":
        return {
            "level": "promising",
            "label": "Можно искать место на карте",
            "reason": "Адресный контекст предварительно проверен. Необходимо установить координаты и доступность панорамы."
        }

    if review.get("status"):
        return {
            "level": "medium",
            "label": "Нужно уточнить место",
            "reason": review.get("reason") or "Необходимо уточнить местоположение."
        }

    if status == "street_house_nearby":
        return {
            "level": "medium",
            "label": "Нужно уточнить место",
            "reason": "Улица и дом найдены рядом в тексте, но их связь с отходами ещё не подтверждена."
        }

    unique_cities = [
        c for c in cities
        if not c.get("ambiguous") and len(c.get("regions") or []) == 1
    ]

    if len(cities) == 1 and len(unique_cities) == 1:
        return {
            "level": "medium",
            "label": "Известен населённый пункт",
            "reason": "Найдено название города, но конкретное место размещения отходов не установлено. Нужна проверка контекста."
        }

    if status == "street_candidate_only":
        return {
            "level": "medium",
            "label": "Нужно уточнить место",
            "reason": "Найдена улица, но не определена точка размещения отходов."
        }

    if status == "landmark_candidate_only":
        return {
            "level": "low",
            "label": "Ориентир без точного места",
            "reason": "В тексте есть общий ориентир, например гаражи, остановка или здание. Его недостаточно для поиска панорамы."
        }

    return {
        "level": "low",
        "label": "Местоположение неизвестно",
        "reason": "Недостаточно географической информации для поиска конкретной панорамы."
    }


# Lightweight display-only filtering. Silver/Bronze records remain unchanged.
WASTE_TERMS = re.compile(
    r'свалк\w*|мусор\w*|отход\w*|помо[йе]к\w*|'
    r'захламл\w*|навал\w*|строительн\w+\s+хлам\w*', re.I
)

def normalized_text(value):
    return re.sub(r'\s+', ' ', (value or '').strip().casefold())

def waste_related(row):
    categories = row.get('categories') or []
    if isinstance(categories, dict):
        categories = list(categories)
    if isinstance(categories, str):
        categories = [categories]
    codes = {str(c).lower() for c in categories if isinstance(c, str)}
    if codes & {'waste', 'illegal_dumping', 'construction_waste', 'household_waste',
                'waste_collection', 'littering', 'recycling', 'cleanup'}:
        return True
    return bool(WASTE_TERMS.search(row.get('text') or ''))

def mark_duplicates(rows):
    # Rows are sorted newest first by LIST_SQL. Keep the latest of exact normalized texts.
    seen = {}
    groups = {}
    for row in rows:
        norm = normalized_text(row.get('text'))
        if not norm:
            row['duplicate_of'] = None
            continue
        if norm in seen:
            row['duplicate_of'] = seen[norm]
            groups[norm] = groups.get(norm, 1) + 1
        else:
            seen[norm] = row['silver_id']
            row['duplicate_of'] = None
    return len(groups), sum(count-1 for count in groups.values())

GEO_FILE = Path("/data/vk_geo_candidates.json")
GEO_MTIME_NS = (
    GEO_FILE.stat().st_mtime_ns
    if GEO_FILE.exists() else None
)

def refresh_geo_cache():
    global GEO, GEO_MTIME_NS

    try:
        modified = GEO_FILE.stat().st_mtime_ns
    except FileNotFoundError:
        return

    if modified == GEO_MTIME_NS:
        return

    # Keep the previous cache if the new file cannot be read.
    try:
        new_geo = load_json_file(GEO_FILE)
    except Exception:
        return

    GEO = new_geo
    GEO_MTIME_NS = modified
    print(
        f"Geo cache updated: {len(GEO)} candidates",
        flush=True
    )



# ECOFUSION_GOLD_STATUS_V1
def gold_decision_label(value):
    labels = {
        "confirmed": "Подтверждено",
        "refuted": "Опровергнуто",
        "insufficient_data": "Недостаточно данных",
    }
    if value is None:
        return "Не проверено"
    return labels.get(str(value).strip(), "Не проверено")


def enrich(row):
    refresh_geo_cache()
    obj = dict(row)
    sid = int(obj['silver_id'])
    obj['geo'] = GEO.get(sid)
    obj['review'] = REVIEW.get(sid)
    obj['city_mentions_reference'] = lookup_city_mentions(obj.get('text') or '')
    hints = obj['city_mentions_reference']
    obj['geographical_evidence'] = geographical_evidence(obj.get('text') or '')
    obj['coherent_location'] = coherent_address_hint(sid, obj.get('text') or '', hints)
    obj['recommendation'] = summarize_geo(sid, obj.get('text') or '', hints, obj['geographical_evidence'], obj['coherent_location'])
    verified = obj.get('event_region') or obj.get('region')
    linked = obj['coherent_location']
    territories = obj['geographical_evidence']
    if linked:
        obj['location_display'] = 'По сообщению: ' + linked['city'] + ', ' + linked['region']
    elif verified:
        obj['location_display'] = verified
    elif territories['municipal_districts'] or territories['forestries']:
        obj['location_display'] = 'Территории указаны, точное место неизвестно'
    elif len(hints)==1 and len(hints[0]['regions'])==1:
        obj['location_display'] = 'Упоминается: ' + hints[0]['city'] + ' (' + hints[0]['regions'][0] + ')'
    else:
        obj['location_display'] = 'Местоположение не установлено' 
    # Existing database observations are not treated as proof of waste.
    obj['dashboard_status'] = gold_decision_label(obj.get('gold_decision_status'))
    return obj

LIST_SQL = '''
SELECT p.silver_id,p.published_at,p.text,p.source_url,p.source,p.owner_id,
 p.publication_id,p.attachment_count,p.likes_count,p.reposts_count,
 p.comments_count,p.views_count,p.post_type,
 c.categories,c.region,c.event_region,c.region_confidence,c.needs_review,
 (SELECT count(*) FROM ecofusion.silver_attachments a WHERE a.silver_id=p.silver_id) AS saved_attachments,
 (SELECT count(*) FROM ecofusion.gold_panorama_observations o WHERE o.silver_id=p.silver_id) AS panorama_observations,
 (SELECT count(*) FROM ecofusion.gold_verification_results v WHERE v.silver_id=p.silver_id) AS gold_records
,
(
    SELECT v.status
    FROM ecofusion.gold_verification_results v
    WHERE v.silver_id = p.silver_id
    ORDER BY v.created_at DESC NULLS LAST,
             v.verification_id DESC
    LIMIT 1
) AS gold_decision_status

FROM ecofusion.silver_publications p
LEFT JOIN ecofusion.silver_classifications c ON c.silver_id=p.silver_id
WHERE (%s = '' OR p.text ILIKE %s OR p.silver_id::text = %s)
ORDER BY p.published_at DESC NULLS LAST, p.silver_id DESC
LIMIT %s OFFSET %s
'''

DETAIL_SQL = '''
SELECT p.*,c.categories,c.region_mentions,c.region,c.region_confidence,
 c.needs_review,c.classifier_version,c.classified_at,c.event_region,
 c.event_region_basis,c.event_region_confidence,c.region_classifier_version
FROM ecofusion.silver_publications p
LEFT JOIN ecofusion.silver_classifications c ON c.silver_id=p.silver_id
WHERE p.silver_id=%s
'''

class Handler(BaseHTTPRequestHandler):
    def send_json(self, obj, code=200):
        payload=json.dumps(obj, ensure_ascii=False, default=safe_value).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path=urlsplit(self.path)
        if path.path in ('/','/index.html'):
            data=(HERE/'index.html').read_bytes()
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        try:

            # ECOFUSION_PANORAMA_IMAGE_ROUTE_V1

            # ECOFUSION_FULL_PANORAMA_ROUTE_V1
            full_match = re.fullmatch(
                r'/api/panorama-full/(\d+)/(\d+)',
                path.path
            )

            if full_match:
                from pathlib import Path

                sid = int(full_match.group(1))
                cid = int(full_match.group(2))

                with connect() as db:
                    with db.cursor() as cur:
                        cur.execute("""
                            SELECT 1
                            FROM ecofusion.gold_panorama_candidates
                            WHERE silver_id = %s
                              AND candidate_id = %s
                            LIMIT 1
                        """, (sid, cid))

                        found = cur.fetchone() is not None

                if not found:
                    self.send_json({
                        "error": "Panorama not found"
                    }, 404)
                    return

                image_file = (
                    Path("/data/panorama_images")
                    / str(sid)
                    / f"candidate_{cid}_full.jpg"
                )

                if not image_file.is_file():
                    self.send_json({
                        "error": "Saved panorama image missing"
                    }, 404)
                    return

                if image_file.stat().st_size > 20_000_000:
                    self.send_json({
                        "error": "Image too large"
                    }, 422)
                    return

                data = image_file.read_bytes()

                if not data.startswith(bytes([255, 216, 255])):
                    self.send_json({
                        "error": "Invalid JPEG"
                    }, 422)
                    return

                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Cache-Control", "private, no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return

            image_match = re.fullmatch(
                r'/api/panorama-image/(\d+)/(\d+)/([4-7])',
                path.path
            )

            if image_match:
                from pathlib import Path

                sid = int(image_match.group(1))
                candidate_id = int(image_match.group(2))
                part = int(image_match.group(3))

                with connect() as db:
                    with db.cursor() as cur:
                        cur.execute("""
                            SELECT 1
                            FROM ecofusion.gold_panorama_candidates c
                             WHERE c.silver_id = %s
                               AND c.candidate_id = %s
                               AND (
                                   EXISTS (
                                       SELECT 1
                                       FROM ecofusion.gold_panorama_detection_results d
                                       WHERE d.candidate_id = c.candidate_id
                                         AND d.image_part = %s
                                   )
                                   OR EXISTS (
                                       SELECT 1
                                       FROM ecofusion.gold_yolo_run_detections y
                                       JOIN ecofusion.panorama_search_runs r
                                         ON r.search_run_id = y.search_run_id
                                       WHERE y.candidate_id = c.candidate_id
                                         AND r.silver_id = c.silver_id
                                         AND y.image_part = %s
                                         AND y.analysis_status = 'completed'
                                   )
                               )
                            LIMIT 1
                        """, (sid, candidate_id, part, part))

                        exists_in_gold = cur.fetchone() is not None

                if not exists_in_gold:
                    self.send_json({
                        "error": "Analyzed panorama fragment not found"
                    }, 404)
                    return

                image_file = (
                    Path("/data/panorama_images")
                    / str(sid)
                    / f"candidate_{candidate_id}_part{part}.jpg"
                )

                if not image_file.is_file():
                    self.send_json({
                        "error": "Panorama image file is missing"
                    }, 404)
                    return

                size = image_file.stat().st_size

                if size < 4 or size > 10_000_000:
                    self.send_json({
                        "error": "Invalid panorama image size"
                    }, 422)
                    return

                data = image_file.read_bytes()

                if not data.startswith(bytes([255, 216, 255])):
                    self.send_json({
                        "error": "Invalid JPEG image"
                    }, 422)
                    return

                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Cache-Control", "private, no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return


            # ECOFUSION_YOLO_RUN_LIST_API_V1
            yolo_list_match = re.fullmatch(
                r'/api/yolo-runs/([1-9][0-9]*)',
                path.path
            )

            if yolo_list_match:
                sid = int(yolo_list_match.group(1))

                with connect() as db:
                    with db.cursor(
                        cursor_factory=RealDictCursor
                    ) as cur:
                        cur.execute("""
                            SELECT
                                r.search_run_id,
                                r.silver_id,
                                r.search_mode,
                                r.created_at,
                                r.selected_count,
                                COALESCE(
                                    r.search_report->>'test_replay',
                                    'false'
                                ) = 'true' AS test_replay,
                                COUNT(d.yolo_result_id) AS fragment_count,
                                COALESCE(
                                    SUM(d.detection_count), 0
                                ) AS total_detections
                            FROM ecofusion.panorama_search_runs r
                            JOIN ecofusion.gold_yolo_run_detections d
                              ON d.search_run_id = r.search_run_id
                            WHERE r.silver_id = %s
                            GROUP BY r.search_run_id
                            ORDER BY r.search_run_id DESC
                        """, (sid,))
                        rows = cur.fetchall()

                runs = []

                for row in rows:
                    runs.append({
                        "search_run_id":
                            int(row["search_run_id"]),
                        "silver_id":
                            int(row["silver_id"]),
                        "search_mode":
                            row["search_mode"],
                        "created_at":
                            row["created_at"],
                        "selected_count":
                            int(row["selected_count"]),
                        "test_replay":
                            bool(row["test_replay"]),
                        "fragment_count":
                            int(row["fragment_count"]),
                        "total_detections":
                            int(row["total_detections"])
                    })

                self.send_json({
                    "silver_id": sid,
                    "run_count": len(runs),
                    "runs": runs
                })
                return

            # ECOFUSION_YOLO_RUN_API_V1
            yolo_match = re.fullmatch(
                r'/api/yolo-run-detections/([1-9][0-9]*)',
                path.path
            )

            if yolo_match:
                run_id = int(yolo_match.group(1))

                with connect() as db:
                    with db.cursor(
                        cursor_factory=RealDictCursor
                    ) as cur:
                        cur.execute("""
                            SELECT
                                search_run_id,
                                silver_id,
                                search_mode,
                                selected_count,
                                search_report
                            FROM ecofusion.panorama_search_runs
                            WHERE search_run_id = %s
                        """, (run_id,))

                        run = cur.fetchone()

                        if run is not None:
                            cur.execute("""
                                SELECT
                                    m.selection_order,
                                    m.candidate_id,
                                    m.discovery_source,
                                    c.panorama_name,
                                    c.panorama_latitude,
                                    c.panorama_longitude,
                                    d.image_part,
                                    d.image_path,
                                    d.model_name,
                                    d.confidence_threshold,
                                    d.detection_count,
                                    d.detections,
                                    d.analysis_status
                                FROM
                                    ecofusion.panorama_search_run_candidates m
                                JOIN ecofusion.gold_panorama_candidates c
                                  ON c.candidate_id = m.candidate_id
                                 AND c.silver_id = %s
                                LEFT JOIN ecofusion.gold_yolo_run_detections d
                                  ON d.search_run_id = m.search_run_id
                                 AND d.candidate_id = m.candidate_id
                                WHERE m.search_run_id = %s
                                ORDER BY
                                    m.selection_order,
                                    d.image_part
                            """, (
                                run["silver_id"], run_id
                            ))

                            records = cur.fetchall()

                if run is None:
                    self.send_json({
                        "error": "Search run not found"
                    }, 404)
                    return

                sid = int(run["silver_id"])
                candidates = {}
                fragments = []

                for record in records:
                    cid = int(record["candidate_id"])

                    if cid not in candidates:
                        candidates[cid] = {
                            "candidate_id": cid,
                            "selection_order": int(
                                record["selection_order"]
                            ),
                            "discovery_source":
                                record["discovery_source"],
                            "panorama_name":
                                record["panorama_name"],
                            "latitude":
                                record["panorama_latitude"],
                            "longitude":
                                record["panorama_longitude"],
                            "full_image_url": (
                                f"/api/panorama-full/{sid}/{cid}"
                            )
                        }

                    if record["image_part"] is None:
                        continue

                    part = int(record["image_part"])

                    fragments.append({
                        "candidate_id": cid,
                        "image_part": part,
                        "image_url": (
                            f"/api/panorama-image/"
                            f"{sid}/{cid}/{part}"
                        ),
                        "model_name": record["model_name"],
                        "confidence_threshold":
                            record["confidence_threshold"],
                        "detection_count": int(
                            record["detection_count"]
                        ),
                        "detections": record["detections"],
                        "analysis_status":
                            record["analysis_status"]
                    })

                report = run["search_report"] or {}

                self.send_json({
                    "search_run_id": run_id,
                    "silver_id": sid,
                    "search_mode": run["search_mode"],
                    "archival_import": bool(
                        report.get("archival_import", False)
                    ),
                    "test_replay": bool(
                        report.get("test_replay", False)
                    ),
                    "selected_count": int(
                        run["selected_count"]
                    ),
                    "candidates": list(candidates.values()),
                    "fragment_count": len(fragments),
                    "total_detections": sum(
                        f["detection_count"] for f in fragments
                    ),
                    "fragments": fragments,
                    "interpretation": (
                        "Результаты YOLO11n предварительные. "
                        "Обнаружение объектов не подтверждает "
                        "незаконное размещение отходов."
                    )
                })
                return

            # ECOFUSION_PANORAMA_DETECTIONS_API_V1
            detection_match = re.fullmatch(
                r'/api/panorama-detections/(\d+)',
                path.path
            )

            if detection_match:
                sid = int(detection_match.group(1))

                with connect() as db:
                    with db.cursor(cursor_factory=RealDictCursor) as cur:
                        cur.execute("""
                            SELECT
                                c.candidate_id,
                                d.image_part,
                                d.detection_count,
                                d.detections,
                                d.analysis_status
                            FROM ecofusion.gold_panorama_candidates c
                            JOIN ecofusion.gold_panorama_detection_results d
                              ON d.candidate_id = c.candidate_id
                            WHERE c.silver_id = %s
                            ORDER BY c.candidate_id, d.image_part
                        """, (sid,))

                        records = cur.fetchall()

                fragments = []

                for record in records:
                    candidate_id = int(record["candidate_id"])
                    part = int(record["image_part"])

                    fragments.append({
                        "candidate_id": candidate_id,
                        "image_part": part,
                        "image_url": (
                            f"/api/panorama-image/"
                            f"{sid}/{candidate_id}/{part}"
                        ),
                        "detection_count": int(
                            record["detection_count"]
                        ),
                        "detections": record["detections"],
                        "analysis_status": record["analysis_status"]
                    })

                self.send_json({
                    "silver_id": sid,
                    "fragment_count": len(fragments),
                    "total_raw_detections": sum(
                        item["detection_count"]
                        for item in fragments
                    ),
                    "fragments": fragments,
                    "interpretation": (
                        "Обнаружения модели являются предварительными "
                        "объектами-кандидатами, а не подтвержденными "
                        "несанкционированными свалками."
                    )
                })
                return

            if path.path=='/api/publications':
                qs=parse_qs(path.query)
                q=(qs.get('q',[''])[0])[:160].strip()
                page=max(1,min(10000,int(qs.get('page',['1'])[0])))
                limit=30
                sort=qs.get('sort',['recommendation'])[0]
                direction=qs.get('direction',['desc'])[0]
                allowed_sort={'recommendation','status','published_at','silver_id','attachments','region','categories','views_count','likes_count','comments_count'}
                if sort not in allowed_sort: sort='recommendation'
                if direction not in ('asc','desc'): direction='desc'
                filter_level=qs.get('level',['all'])[0]
                if filter_level not in ('all','promising','medium','low'): filter_level='all'
                show_repeats=qs.get('repeats',['0'])[0]=='1'
                waste_only=qs.get('waste_only',['1'])[0]!='0'
                with connect() as db:
                    with db.cursor(cursor_factory=RealDictCursor) as cur:
                        # Full dataset first, so duplicate representative is stable across filters.
                        cur.execute(LIST_SQL,('', '%%', '',100000,0))
                        all_items=[enrich(r) for r in cur.fetchall()]
                        cur.execute('SELECT count(*) AS n, count(DISTINCT silver_id) AS posts FROM ecofusion.silver_attachments')
                        a=cur.fetchone()
                duplicate_groups, duplicate_extras=mark_duplicates(all_items)
                for item in all_items:
                    item['waste_related']=waste_related(item)
                relevant_count=sum(x['waste_related'] for x in all_items)
                # Keep complete raw records in DB. Filter only the operator's journal.
                items=[x for x in all_items if (show_repeats or x['duplicate_of'] is None)
                       and (not waste_only or x['waste_related'])]
                if q:
                    items=[x for x in items if q.lower() in (x.get('text') or '').lower()
                           or str(x['silver_id'])==q]
                breakdown={level:sum(x['recommendation']['level']==level for x in items)
                           for level in ('promising','medium','low')}
                overview={'publications':len(all_items),'spatial_candidates':len(GEO),'attachments':a['n'],
                          'posts_with_attachments':a['posts'], 'duplicate_groups':duplicate_groups,
                          'duplicate_extras':duplicate_extras, 'waste_related':relevant_count,
                          'journal_before_recommendation':len(items), **breakdown}
                if filter_level!='all':
                    items=[x for x in items if x['recommendation']['level']==filter_level]
                def key(x):
                    v={'recommendation':{'low':0,'medium':1,'promising':2}.get(x['recommendation']['level'],0),
                       'status':x['dashboard_status'],
                       'published_at':x.get('published_at') or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
                       'silver_id':x['silver_id'],
                       'attachments':x.get('saved_attachments') or 0,
                       'region':x.get('event_region') or x.get('region') or '',
                       'categories':json.dumps(x.get('categories'),ensure_ascii=False,default=safe_value),
                       'views_count':x.get('views_count') or 0,
                       'likes_count':x.get('likes_count') or 0,
                       'comments_count':x.get('comments_count') or 0}[sort]
                    return v
                items.sort(key=key,reverse=direction=='desc')
                total=len(items)
                items=items[(page-1)*limit:page*limit]
                self.send_json({'total':total,'page':page,'page_size':limit,'items':items,'overview':overview,
                                'active_filter':filter_level,'matched_before_pagination':total})
                return
            match=re.fullmatch(r'/api/publications/(\d+)',path.path)
            if match:
                sid=int(match.group(1))
                with connect() as db:
                    with db.cursor(cursor_factory=RealDictCursor) as cur:
                        cur.execute(DETAIL_SQL,(sid,))
                        record=cur.fetchone()
                        if not record:
                            self.send_json({'error':'Публикация не найдена'},404);return
                        cur.execute('SELECT position,attachment_type,attachment_id,payload FROM ecofusion.silver_attachments WHERE silver_id=%s ORDER BY position,attachment_row_id',(sid,))
                        attachments=cur.fetchall()
                        cur.execute('SELECT * FROM ecofusion.gold_panorama_observations WHERE silver_id=%s ORDER BY created_at DESC',(sid,))
                        observations=cur.fetchall()
                        cur.execute('SELECT * FROM ecofusion.gold_verification_results WHERE silver_id=%s ORDER BY created_at DESC',(sid,))
                        verifications=cur.fetchall()
                        cur.execute(
                            'SELECT * FROM ecofusion.gold_panorama_preliminary_summary '
                            'WHERE silver_id=%s',
                            (sid,)
                        )
                        preliminary_summary=cur.fetchone()
                item=enrich(record)
                item['attachments']=attachments
                item['panorama_observations']=observations
                item['gold_verifications']=verifications
                item['dashboard_status'] = gold_decision_label(
                    max(
                        verifications,
                        key=lambda v: int(v.get('verification_id') or 0)
                    ).get('status')
                    if verifications else None
                )
                item['preliminary_summary']=preliminary_summary
                self.send_json(item)
                return
            self.send_json({'error':'Не найдено'},404)
        except Exception as exc:
            self.send_json({'error':f'Ошибка сервера: {type(exc).__name__}: {exc}'},500)

    def save_operator_verification(self, sid):
        """Save one explicitly selected manual assessment in Gold."""
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length <= 0 or length > 16384:
                self.send_json({'error': 'Некорректный размер запроса'}, 400)
                return

            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                self.send_json({'error': 'Ожидается JSON-объект'}, 400)
                return

            status = str(payload.get('status') or '').strip()
            note = str(payload.get('evidence_note') or '').strip()
            location = str(payload.get('location_text') or '').strip()

            allowed = {'confirmed', 'refuted', 'insufficient_data'}
            if status not in allowed:
                self.send_json({'error': 'Недопустимый статус'}, 400)
                return

            if len(note) < 10 or len(note) > 3000:
                self.send_json({
                    'error': 'Комментарий должен содержать от 10 до 3000 символов'
                }, 400)
                return

            if len(location) > 500:
                self.send_json({'error': 'Описание места слишком длинное'}, 400)
                return

            if not location:
                location = 'Местоположение не установлено'

            with connect(read_only=False) as db:
                with db.cursor() as cur:
                    cur.execute(
                        'SELECT 1 FROM ecofusion.silver_publications WHERE silver_id=%s',
                        (sid,)
                    )
                    if not cur.fetchone():
                        self.send_json({'error': 'Публикация не найдена'}, 404)
                        return

                    cur.execute("""
                        INSERT INTO ecofusion.gold_verification_results
                        (
                            silver_id,
                            location_text,
                            coordinate_role,
                            map_provider,
                            map_search_query,
                            status,
                            evidence_note,
                            source_url,
                            verification_method
                        )
                        VALUES
                        (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (silver_id) DO NOTHING
                        RETURNING verification_id
                    """, (
                        sid,
                        location,
                        'not_provided',
                        'not_checked',
                        '',
                        status,
                        note,
                        '',
                        'operator_manual_review'
                    ))

                    inserted = cur.fetchone()

                if inserted:
                    db.commit()
                else:
                    db.rollback()

            if not inserted:
                self.send_json({
                    'error': 'Для публикации уже есть результат Gold. '
                             'Существующая запись не изменена.'
                }, 409)
                return

            self.send_json({
                'status': 'saved',
                'verification_id': inserted[0],
                'silver_id': sid,
                'gold_status': status,
                'message': 'Решение аналитика сохранено в Gold'
            }, 201)

        except (ValueError, TypeError, json.JSONDecodeError):
            self.send_json({'error': 'Некорректные данные запроса'}, 400)
        except Exception as exc:
            self.send_json({
                'error': f'Ошибка сохранения: {type(exc).__name__}: {exc}'
            }, 500)


    def save_panorama_candidates(self, sid):
        """Save located panoramas, not visual verification results."""
        import math
        from decimal import Decimal

        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 32768:
                self.send_json({"error": "Invalid request size"}, 400)
                return

            data = json.loads(self.rfile.read(size))

            if not isinstance(data, dict):
                self.send_json({"error": "Expected JSON object"}, 400)
                return

            items = data.get("selected_panoramas")
            context = data.get("spatial_context") or {}
            search_query = data.get("search_query") or ""

            # ECOFUSION_SEARCH_RUN_PERSISTENCE_V1
            # Optional for backward compatibility.
            search_report = data.get("search_report")
            run_members = []
            search_run_id = None

            if not isinstance(items, list) or len(items) > 5:
                self.send_json({"error": "Invalid panorama list"}, 400)
                return

            if not isinstance(context, dict):
                self.send_json({"error": "Invalid spatial context"}, 400)
                return

            if not isinstance(search_query, str) or len(search_query) > 500:
                self.send_json({"error": "Invalid search query"}, 400)
                return

            if search_report is not None:
                if not isinstance(search_report, dict):
                    raise ValueError("Invalid search report")

                mode = search_report.get("search_mode")

                if mode not in ("parcel_geometry", "address_point"):
                    raise ValueError("Invalid search mode")

                locate_calls = search_report.get("search_points")
                neighbor_data = search_report.get(
                    "neighbor_discovery", {}
                )

                if not isinstance(neighbor_data, dict):
                    raise ValueError("Invalid neighbor diagnostics")

                neighbor_requests = neighbor_data.get("requests", 0)

                if (
                    type(locate_calls) is not int
                    or not 0 <= locate_calls <= 5
                    or type(neighbor_requests) is not int
                    or not 0 <= neighbor_requests <= 2
                ):
                    raise ValueError("Search budget out of range")

                report_items = search_report.get(
                    "selected_panoramas"
                )

                if (
                    not isinstance(report_items, list)
                    or len(report_items) != len(items)
                ):
                    raise ValueError("Search report selection mismatch")

                cad = search_report.get("cadastral_number")

                if cad is not None and (
                    not isinstance(cad, str) or len(cad) > 80
                ):
                    raise ValueError("Invalid cadastral number")

                for index, (item, reported) in enumerate(
                    zip(items, report_items), 1
                ):
                    if not isinstance(reported, dict):
                        raise ValueError("Invalid reported panorama")

                    if item.get("position") != reported.get("position"):
                        raise ValueError("Report position mismatch")

                    origin = item.get(
                        "discovery_source", "initial_locate"
                    )

                    if origin not in (
                        "initial_locate", "neighbor_graph"
                    ):
                        raise ValueError("Invalid discovery source")

                    parent = item.get("parent_position")

                    if parent is not None:
                        if (
                            not isinstance(parent, list)
                            or len(parent) < 2
                        ):
                            raise ValueError("Invalid parent position")

                        import math
                        if not all(
                            math.isfinite(float(v))
                            for v in parent[:2]
                        ):
                            raise ValueError("Invalid parent coordinates")

                    run_members.append((
                        index, origin,
                        json.dumps(parent) if parent is not None
                        else None
                    ))

            prepared = []
            seen = set()

            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("Invalid panorama object")

                position = item.get("position")
                if not isinstance(position, list) or len(position) < 2:
                    raise ValueError("Invalid panorama coordinates")

                lat = float(position[0])
                lon = float(position[1])
                distance = float(item["distance_m"])
                bearing = float(item["bearing_to_reference_deg"])
                score = float(item["selection_score"])

                if not all(map(math.isfinite, (
                    lat, lon, distance, bearing, score
                ))):
                    raise ValueError("Non-finite numeric value")

                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    raise ValueError("Coordinates out of range")

                if not (0 <= distance <= 100000):
                    raise ValueError("Distance out of range")

                if not (0 <= bearing < 360):
                    raise ValueError("Bearing out of range")

                if not (0 <= score <= 1):
                    raise ValueError("Selection score out of range")

                name = item.get("name") or ""
                if not isinstance(name, str) or len(name) > 500:
                    raise ValueError("Invalid panorama name")

                lat_db = Decimal(str(lat)).quantize(Decimal("0.00000001"))
                lon_db = Decimal(str(lon)).quantize(Decimal("0.00000001"))
                position_key = (lat_db, lon_db)

                if position_key in seen:
                    continue

                seen.add(position_key)

                prepared.append((
                    sid, lat_db, lon_db, name,
                    distance, bearing, score,
                    search_query, json.dumps(context, ensure_ascii=False)
                ))

            if (
                search_report is not None
                and len(prepared) != len(items)
            ):
                raise ValueError("Duplicate positions in search report")

            with connect(read_only=False) as db:
                with db.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM ecofusion.silver_publications "
                        "WHERE silver_id=%s",
                        (sid,)
                    )
                    if not cur.fetchone():
                        self.send_json({
                            "error": "Publication not found"
                        }, 404)
                        return

                    saved = []

                    for values in prepared:
                        cur.execute("""
                            INSERT INTO ecofusion.gold_panorama_candidates
                            (
                                silver_id,
                                panorama_latitude,
                                panorama_longitude,
                                panorama_name,
                                distance_m,
                                bearing_to_reference_deg,
                                selection_score,
                                search_query,
                                spatial_context
                            )
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                            ON CONFLICT (
                                silver_id,
                                panorama_latitude,
                                panorama_longitude
                            )
                            DO UPDATE SET
                                panorama_name=EXCLUDED.panorama_name,
                                distance_m=EXCLUDED.distance_m,
                                bearing_to_reference_deg=
                                    EXCLUDED.bearing_to_reference_deg,
                                selection_score=EXCLUDED.selection_score,
                                search_query=EXCLUDED.search_query,
                                spatial_context=EXCLUDED.spatial_context
                            RETURNING candidate_id
                        """, values)

                        saved.append(cur.fetchone()[0])

                    if search_report is not None:
                        cur.execute("""
                            INSERT INTO ecofusion.panorama_search_runs
                            (
                                silver_id, search_mode,
                                cadastral_number, locate_calls,
                                neighbor_requests, selected_count,
                                search_report
                            )
                            VALUES (
                                %s,%s,%s,%s,%s,%s,%s::jsonb
                            )
                            RETURNING search_run_id
                        """, (
                            sid,
                            search_report["search_mode"],
                            search_report.get("cadastral_number"),
                            search_report["search_points"],
                            search_report.get(
                                "neighbor_discovery", {}
                            ).get("requests", 0),
                            len(saved),
                            json.dumps(
                                search_report, ensure_ascii=False
                            )
                        ))

                        search_run_id = cur.fetchone()[0]

                        for candidate_id, member in zip(
                            saved, run_members
                        ):
                            ordinal, origin, parent_json = member

                            cur.execute("""
                                INSERT INTO
                                    ecofusion.panorama_search_run_candidates
                                (
                                    search_run_id,
                                    candidate_id,
                                    selection_order,
                                    discovery_source,
                                    parent_position
                                )
                                VALUES (%s,%s,%s,%s,%s::jsonb)
                            """, (
                                search_run_id,
                                candidate_id,
                                ordinal,
                                origin,
                                parent_json
                            ))

                db.commit()

            self.send_json({
                "status": "saved",
                "silver_id": sid,
                "candidate_count": len(saved),
                "candidate_ids": saved,
                "search_run_id": search_run_id,
                "visual_analysis": "not_performed",
                "verification_result": "not_determined"
            }, 200)

        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            self.send_json({"error": "Invalid panorama data"}, 400)
        except Exception as exc:
            self.send_json({
                "error": "Panorama save failed: " + type(exc).__name__
            }, 500)


    # ECOFUSION_PANORAMA_QUEUE_API_V1
    # ECOFUSION_YOLO_QUEUE_V1
    def enqueue_panorama_analysis(self, sid, query):
        """Queue YOLO analysis for an explicit panorama search run."""
        from pathlib import Path

        try:
            parameters = parse_qs(query)
            values = parameters.get("search_run_id", [])

            if len(values) != 1 or not values[0].isdigit():
                self.send_json({
                    "error": "Valid search_run_id is required"
                }, 400)
                return

            run_id = int(values[0])

            if sid <= 0 or run_id <= 0:
                self.send_json({
                    "error": "Invalid publication or search run"
                }, 400)
                return

            with connect(read_only=False) as db:
                with db.cursor(
                    cursor_factory=RealDictCursor
                ) as cur:

                    cur.execute("""
                        SELECT search_run_id, silver_id, selected_count
                        FROM ecofusion.panorama_search_runs
                        WHERE search_run_id = %s
                          AND silver_id = %s
                    """, (run_id, sid))

                    run = cur.fetchone()

                    if run is None:
                        self.send_json({
                            "error": "Search run does not belong to publication"
                        }, 404)
                        return

                    cur.execute("""
                        SELECT candidate_id
                        FROM ecofusion.panorama_search_run_candidates
                        WHERE search_run_id = %s
                        ORDER BY selection_order
                    """, (run_id,))

                    ids = [
                        int(r["candidate_id"])
                        for r in cur.fetchall()
                    ]

                    if not ids or len(ids) != int(run["selected_count"]):
                        self.send_json({
                            "error": "Search run candidate set is incomplete"
                        }, 409)
                        return

                    expected = 4 * len(ids)

                    cur.execute("""
                        SELECT count(*) AS n
                        FROM ecofusion.gold_yolo_run_detections
                        WHERE search_run_id = %s
                    """, (run_id,))

                    analyzed = int(cur.fetchone()["n"])

                    if analyzed == expected:
                        self.send_json({
                            "status": "already_completed",
                            "silver_id": sid,
                            "search_run_id": run_id,
                            "analyzed_fragments": analyzed
                        })
                        return

                    if analyzed != 0:
                        self.send_json({
                            "error": "Partial YOLO results require review",
                            "analyzed_fragments": analyzed,
                            "expected_fragments": expected
                        }, 409)
                        return

                    image_root = (
                        Path("/data/panorama_images") / str(sid)
                    )

                    missing = []

                    for cid in ids:
                        for part in range(4, 8):
                            filename = (
                                f"candidate_{cid}_part{part}.jpg"
                            )
                            if not (
                                image_root / filename
                            ).is_file():
                                missing.append(filename)

                    if missing:
                        self.send_json({
                            "error": "Source panorama fragments missing",
                            "silver_id": sid,
                            "search_run_id": run_id,
                            "missing_count": len(missing),
                            "missing_examples": missing[:5]
                        }, 409)
                        return

                    cur.execute("""
                        INSERT INTO ecofusion.panorama_analysis_jobs
                            (silver_id, search_run_id, status)
                        VALUES (%s, %s, 'queued')
                        ON CONFLICT (silver_id)
                            WHERE status IN ('queued', 'running')
                        DO NOTHING
                        RETURNING job_id
                    """, (sid, run_id))

                    inserted = cur.fetchone()

                    if inserted is None:
                        cur.execute("""
                            SELECT job_id, status, search_run_id
                            FROM ecofusion.panorama_analysis_jobs
                            WHERE silver_id = %s
                              AND status IN ('queued', 'running')
                            LIMIT 1
                        """, (sid,))

                        existing = cur.fetchone()

                        if (
                            existing is None or
                            existing["search_run_id"] != run_id
                        ):
                            self.send_json({
                                "error": (
                                    "Another search run already has "
                                    "an active analysis job"
                                )
                            }, 409)
                            return

                        job_id = int(existing["job_id"])
                        status = existing["status"]
                    else:
                        job_id = int(inserted["job_id"])
                        status = "queued"

                db.commit()

            self.send_json({
                "status": status,
                "job_id": job_id,
                "silver_id": sid,
                "search_run_id": run_id,
                "expected_fragments": expected,
                "visual_analysis": "awaiting_yolo_worker"
            }, 202)

        except Exception as exc:
            self.send_json({
                "error": "YOLO queue operation failed",
                "error_type": type(exc).__name__
            }, 500)

    def do_POST(self):
        queue_match = re.fullmatch(
            r'/api/panorama-analysis/(\d+)',
            urlsplit(self.path).path
        )
        if queue_match:
            self.enqueue_panorama_analysis(
                int(queue_match.group(1)),
                urlsplit(self.path).query
            )
            return


        panorama_match = re.fullmatch(
            r'/api/panorama-candidates/(\d+)',
            urlsplit(self.path).path
        )
        if panorama_match:
            self.save_panorama_candidates(int(panorama_match.group(1)))
            return

        verify_match = re.fullmatch(
            r'/api/verify/(\d+)', urlsplit(self.path).path
        )
        if verify_match:
            self.save_operator_verification(int(verify_match.group(1)))
            return

        match=re.fullmatch(r'/api/check/(\d+)',urlsplit(self.path).path)
        if not match:
            self.send_json({'error':'Не найдено'},404);return
        sid=int(match.group(1))

        # Search is initiated by the operator for ANY publication.
        import urllib.request
        import urllib.parse
        import urllib.error

        with connect() as db:
            with db.cursor() as cur:
                cur.execute("""
                    SELECT p.text,
                           c.region,
                           c.event_region
                    FROM ecofusion.silver_publications p
                    LEFT JOIN ecofusion.silver_classifications c
                      ON c.silver_id=p.silver_id
                    WHERE p.silver_id=%s
                """, (sid,))
                record=cur.fetchone()

        if not record:
            self.send_json({'error':'Публикация не найдена'},404)
            return

        text_value=record[0] or ''
        region=record[2] or record[1] or ''
        geo=GEO.get(sid) or {}
        address=geo.get('address_landmarks') or {}

        streets=address.get('street_candidates') or []
        houses=address.get('house_numbers') or []

        cities=lookup_city_mentions(text_value)
        city=''

        if cities:
            unique=[c for c in cities if not c.get('ambiguous')]
            if unique:
                city=unique[0].get('city') or ''

        # Fallback for inflected place names in VK texts.
        if not city:
            known_forms=(
                (r'\bХимок\b', 'Химки'),
                (r'\bКалуги\b', 'Калуга'),
                (r'\bНерюнгри\b', 'Нерюнгри'),
            )
            for pattern, name in known_forms:
                if re.search(pattern, text_value, re.I):
                    city=name
                    break

        street=streets[0] if streets else ''
        street=re.sub(
            r'Юбилейном\s+проспекте',
            'Юбилейный проспект',
            street,
            flags=re.I
        )

        parts=[x for x in (region, city, street) if x]

        if houses and street:
            parts.append('дом '+str(houses[0]))

        search_query=', '.join(parts)

        # No publication is blocked from initiating the search.
        if not search_query:
            search_query=text_value[:160].strip()


        # ECOFUSION_REGISTRY_GEOCODING_V1
        # Prefer verified cadastral data over a generic city lookup.
        registry_result = lookup_registry(text_value)
        registry_status = registry_result.get('status')
        registry_address = registry_result.get('address')

        if registry_status == 'registry_address_resolved':
            search_query = registry_address

        elif registry_status in (
            'multiple_cadastral_numbers',
            'registry_record_missing',
            'registry_record_invalid'
        ):
            self.send_json({
                'status': 'location_requires_review',
                'silver_id': sid,
                'cadastral_numbers':
                    registry_result.get('cadastral_numbers', []),
                'registry_status': registry_status,
                'search_query': None,
                'panorama_status': 'not_started',
                'message':
                    'Кадастровый номер обнаружен, но конкретный '
                    'адрес участка пока не установлен. '
                    'Необходимо уточнение реестровых сведений.'
            })
            return

        elif not street:
            # A city or region alone must never become the incident point.
            self.send_json({
                'status': 'location_requires_review',
                'silver_id': sid,
                'registry_status': registry_status,
                'search_query': None,
                'panorama_status': 'not_started',
                'message':
                    'Недостаточно точных адресных ориентиров. '
                    'Поиск панорам по центру города запрещён.'
            })
            return

        key=os.environ.get('YANDEX_GEOCODER_API_KEY','').strip()

        if not key:
            self.send_json({
                'status':'search_failed',
                'silver_id':sid,
                'search_query':search_query,
                'message':'Ключ Геокодера не настроен',
                'panorama_status':'not_started'
            })
            return

        if not search_query:
            self.send_json({
                'status':'search_failed',
                'silver_id':sid,
                'message':'Поиск инициирован, но текст публикации пуст',
                'panorama_status':'not_started'
            })
            return

        params=urllib.parse.urlencode({
            'apikey':key,
            'geocode':search_query,
            'format':'json',
            'results':1,
            'lang':'ru_RU'
        })

        url='https://geocode-maps.yandex.ru/v1/?'+params

        try:
            request=urllib.request.Request(
                url,
                headers={'User-Agent':'EcoFusion-Educational/1.0'}
            )
            with urllib.request.urlopen(request,timeout=15) as response:
                data=json.load(response)

            members=(
                data.get('response',{})
                    .get('GeoObjectCollection',{})
                    .get('featureMember',[])
            )

            if not members:
                self.send_json({
                    'status':'no_geocode_result',
                    'silver_id':sid,
                    'search_query':search_query,
                    'message':'Поиск выполнен, координаты не найдены',
                    'panorama_status':'not_started'
                })
                return

            found=members[0]['GeoObject']
            position=found['Point']['pos'].split()
            metadata=found.get('metaDataProperty',{}).get(
                'GeocoderMetaData',{}
            )


            # ECOFUSION_PARCEL_SEARCH_PLAN_V1
            panorama_search_plan = None

            if registry_status == 'registry_address_resolved':
                numbers = registry_result.get(
                    'cadastral_numbers', []
                )

                if len(numbers) == 1:
                    cad = numbers[0]

                    geometry_file = (
                        Path('/data/registry') /
                        ('nspd_' + cad.replace(':', '_')
                         + '_geometry.json')
                    )

                    if geometry_file.is_file():
                        try:
                            geometry = json.loads(
                                geometry_file.read_text(
                                    encoding='utf-8-sig'
                                )
                            )

                            if geometry.get('cadastral_number') != cad:
                                raise ValueError(
                                    'Cadastral geometry mismatch'
                                )

                            panorama_search_plan = plan_parcel_search(
                                geometry,
                                max_points=5
                            )

                        except (OSError, ValueError, TypeError) as exc:
                            self.send_json({
                                'status': 'location_requires_review',
                                'silver_id': sid,
                                'registry_status': registry_status,
                                'panorama_status': 'not_started',
                                'message':
                                    'Ошибка геометрии кадастрового участка: '
                                    + str(exc)
                            })
                            return

            self.send_json({
                'status':'geocoding_complete',
                'silver_id':sid,
                'spatial_context': {
                    'spatial_relations': [
                        {
                            'relation': item.get('relation'),
                            'phrase': item.get('phrase'),
                            'direction_strategy': item.get('direction_strategy'),
                            'status': item.get('status')
                        }
                        for item in geo.get('spatial_relations', [])
                        if isinstance(item, dict)
                    ],
                    'address_landmarks': geo.get('address_landmarks', {}),
                    'address_status': geo.get('address_status')
                },
                'search_query':search_query,
                'registry_status': registry_status,
                'panorama_search_plan': panorama_search_plan,
                'registry_source': (
                    registry_result.get('registry_source')
                    if registry_address else None
                ),
                'registry_source_mode': (
                    registry_result.get('source_mode')
                    if registry_address else None
                ),
                'cadastral_numbers':
                    registry_result.get('cadastral_numbers', []),
                'latitude':float(position[1]),
                'longitude':float(position[0]),
                'precision':metadata.get('precision','unknown'),
                'resolved_address':metadata.get('text',''),
                'panorama_status':'not_started',
                'message':'Координаты получены. Поиск панорамы ещё не подключён.'
            })

        except urllib.error.HTTPError as exc:
            self.send_json({
                'status':'search_failed',
                'silver_id':sid,
                'search_query':search_query,
                'message':'Геокодер вернул HTTP '+str(exc.code),
                'panorama_status':'not_started'
            })
        except Exception as exc:
            self.send_json({
                'status':'search_failed',
                'silver_id':sid,
                'search_query':search_query,
                'message':'Ошибка геокодирования: '+type(exc).__name__,
                'panorama_status':'not_started'
            })

    def log_message(self,fmt,*args):
        print('%s %s'%(self.address_string(),fmt%args), flush=True)

print(f'EcoFusion dashboard: http://localhost:{PORT}',flush=True)
ThreadingHTTPServer(('0.0.0.0',PORT),Handler).serve_forever()
