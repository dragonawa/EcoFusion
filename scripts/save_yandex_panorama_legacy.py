"""
EcoFusion: isolated panorama image saver.

Image acquisition and assembly adapted from diplom/back/yapan.py.
Does not change geocoding, ranking, dashboard or Gold.
Uses the legacy project's undocumented Yandex endpoints.
"""

import argparse
import json
import math
import re
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from PIL import Image


USER_AGENT = "EcoFusion educational panorama image processor"


def get_bytes(url, max_bytes=12_000_000):
    request = Request(
        url,
        headers={"User-Agent": USER_AGENT},
    )

    with urlopen(request, timeout=20) as response:
        content = response.read(max_bytes + 1)

    if len(content) > max_bytes:
        raise ValueError("Response exceeds maximum size")

    return content


def get_metadata(lat, lon):
    query = urlencode({
        "l": "stv",
        "lang": "ru_RU",
        "ll": f"{lon},{lat}",
        "origin": "userAction",
        "provider": "streetview",
    })

    url = (
        "https://api-maps.yandex.ru/"
        "services/panoramas/1.x/?" + query
    )

    return json.loads(get_bytes(url, 3_000_000))


def save_panorama(lat, lon, destination, prefix, zoom=0):
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Invalid coordinates")

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", prefix):
        raise ValueError("Unsafe output prefix")

    destination = Path(destination)
    metadata = get_metadata(lat, lon)

    images = metadata["data"]["Data"]["Images"]
    image_id = images["imageId"]

    if not isinstance(image_id, str):
        raise ValueError("Invalid panorama image ID")

    if not re.fullmatch(r"[A-Za-z0-9_/-]+", image_id):
        raise ValueError("Unexpected panorama image ID")

    tile_width = int(images["Tiles"]["width"])
    tile_height = int(images["Tiles"]["height"])

    # ECOFUSION_AUTO_ZOOM_FIX_V1
    if not (1 <= tile_width <= 4096 and 1 <= tile_height <= 4096):
        raise ValueError("Unexpected tile dimensions")

    available = []

    for level_index, level in enumerate(images["Zooms"]):
        w = int(level["width"])
        h = int(level["height"])

        if w <= 0 or h <= 0:
            continue

        cols = math.ceil(w / tile_width)
        rows_count = math.ceil(h / tile_height)

        if (
            w <= 16000
            and h <= 8000
            and cols * rows_count <= 160
        ):
            available.append((
                w * h, level_index, w, h, cols, rows_count
            ))

    if not available:
        levels = [
            (
                i,
                level.get("width"),
                level.get("height")
            )
            for i, level in enumerate(images["Zooms"])
        ]
        raise ValueError(
            "No suitable panorama resolution. "
            f"Available levels: {levels}"
        )

    # Select the highest available resolution within the limits.
    _, zoom, width, height, columns, rows = max(
        available,
        key=lambda item: item[0]
    )

    print(
        f"Selected panorama zoom={zoom}; "
        f"resolution={width}x{height}; "
        f"tiles={columns * rows}",
        flush=True
    )

    panorama = Image.new("RGB", (width, height))

    # Same tile arrangement used in diplom/back/yapan.py.
    for x in range(columns):
        for y in range(rows):
            tile_url = (
                "https://pano.maps.yandex.net/"
                f"{image_id}/{zoom}.{x}.{y}"
            )

            raw = get_bytes(tile_url)

            with Image.open(BytesIO(raw)) as tile:
                tile.load()
                panorama.paste(
                    tile.convert("RGB"),
                    (x * tile_width, y * tile_height),
                )

    destination.mkdir(parents=True, exist_ok=True)

    full_name = f"{prefix}_full.jpg"
    full_path = destination / full_name

    panorama.save(full_path, format="JPEG", quality=95)

    # Preserve the original project's four lower-half crops.
    crop_names = []

    for col in range(4):
        left = col * width // 4
        right = (col + 1) * width // 4
        upper = height // 2

        crop = panorama.crop((left, upper, right, height))
        crop_name = f"{prefix}_part{col + 4}.jpg"
        crop.save(
            destination / crop_name,
            format="JPEG",
            quality=95,
        )
        crop_names.append(crop_name)

    result = {
        "source": "yandex_panorama_legacy_method",
        "source_coordinates": {
            "latitude": lat,
            "longitude": lon,
        },
        "image_id": image_id,
        "zoom": zoom,
        "image_width": width,
        "image_height": height,
        "full_image": full_name,
        "crops": crop_names,
        "visual_analysis": "not_performed",
    }

    metadata_path = destination / f"{prefix}_metadata.json"
    metadata_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lon", type=float, required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--zoom", type=int, default=0)
    args = parser.parse_args()

    if args.zoom < 0 or args.zoom > 10:
        parser.error("Zoom must be between 0 and 10")

    result = save_panorama(
        args.lat,
        args.lon,
        args.output,
        args.prefix,
        args.zoom,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()