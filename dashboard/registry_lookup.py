"""EcoFusion educational prototype: cadastral lookup."""

import json
import re
from pathlib import Path

CADASTRAL_PATTERN = re.compile(
    r"(?<!\d)(\d{2}:\d{2}:\d{6,7}:\d+)(?!\d)"
)


def extract_cadastral_numbers(text):
    """Return unique cadastral identifiers in appearance order."""
    return list(dict.fromkeys(
        CADASTRAL_PATTERN.findall(text or "")
    ))


def lookup_registry(text, registry_dir="/data/registry"):
    """Resolve cadastral IDs using stored NSPD snapshots.

    This function never guesses an address or incident coordinates.
    """
    numbers = extract_cadastral_numbers(text)

    if not numbers:
        return {
            "status": "no_cadastral_number",
            "cadastral_numbers": [],
            "address": None,
        }

    if len(numbers) > 1:
        return {
            "status": "multiple_cadastral_numbers",
            "cadastral_numbers": numbers,
            "address": None,
            "message": "Требуется определить конкретный участок",
        }

    number = numbers[0]
    filename = "nspd_" + number.replace(":", "_") + ".json"
    path = Path(registry_dir) / filename

    if not path.is_file():
        return {
            "status": "registry_record_missing",
            "cadastral_numbers": numbers,
            "address": None,
            "message": "Нет сохранённых реестровых сведений",
        }

    try:
        record = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {
            "status": "registry_record_invalid",
            "cadastral_numbers": numbers,
            "address": None,
        }

    if not isinstance(record, dict):
        return {
            "status": "registry_record_invalid",
            "cadastral_numbers": numbers,
            "address": None,
        }

    address = record.get("readable_address")

    if (
        record.get("cadastral_number") != number
        or not isinstance(address, str)
        or not address.strip()
        or record.get("source_mode") != "browser_verified_snapshot"
    ):
        return {
            "status": "registry_record_invalid",
            "cadastral_numbers": numbers,
            "address": None,
        }

    return {
        "status": "registry_address_resolved",
        "cadastral_numbers": numbers,
        "address": address.strip(),
        "registry_object_id": record.get("registry_object_id"),
        "registry_source": record.get("source_name"),
        "source_mode": record.get("source_mode"),
        "registry_updated_at": record.get("registry_updated_at"),
        "incident_location_confirmed": False,
    }
