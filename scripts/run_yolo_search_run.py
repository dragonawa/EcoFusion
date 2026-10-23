import argparse
import json
from pathlib import Path

IMAGE_ROOT = Path("/images")
RESULT_ROOT = Path("/results")
MODEL_PATH = Path("/models/ecofusion_yolo11n_best.pt")

WASTE_CLASSES = {0: "dump", 4: "rubbish"}
CONFIDENCE = 0.50


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    manifest = json.loads(
        Path(args.manifest).read_text(encoding="utf-8-sig")
    )

    if not isinstance(manifest, list):
        raise ValueError("Manifest must be an array")

    if not 1 <= len(manifest) <= 5:
        raise ValueError("Expected 1 to 5 candidates")

    run_ids = {int(x["search_run_id"]) for x in manifest}
    silver_ids = {int(x["silver_id"]) for x in manifest}

    if len(run_ids) != 1 or len(silver_ids) != 1:
        raise ValueError("Mixed search runs or publications")

    run_id = next(iter(run_ids))
    silver_id = next(iter(silver_ids))

    if run_id <= 0 or silver_id <= 0:
        raise ValueError("Invalid search run or publication ID")

    seen = set()
    planned = []
    missing = []

    for candidate in manifest:
        candidate_id = int(candidate["candidate_id"])

        if candidate_id <= 0 or candidate_id in seen:
            raise ValueError("Invalid or repeated candidate ID")

        seen.add(candidate_id)

        fragments = candidate["fragments"]

        if not isinstance(fragments, list):
            raise ValueError("Invalid fragments list")

        parts_seen = set()

        for fragment in fragments:
            part = int(fragment["part"])

            if part not in (4, 5, 6, 7):
                raise ValueError("Unexpected image part")

            if part in parts_seen:
                raise ValueError("Repeated image part")

            parts_seen.add(part)

            # Reconstruct paths from trusted numeric identifiers,
            # never use arbitrary paths supplied by the manifest.
            image_name = f"candidate_{candidate_id}_part{part}.jpg"
            image_path = (
                IMAGE_ROOT / str(silver_id) / image_name
            )

            entry = {
                "candidate_id": candidate_id,
                "image_part": part,
                "image_path": str(image_path),
            }

            if image_path.is_file():
                planned.append(entry)
            else:
                missing.append(entry)

        if parts_seen != {4, 5, 6, 7}:
            raise ValueError("Exactly four fragments required")

    print("SEARCH_RUN_ID:", run_id)
    print("SILVER_ID:", silver_id)
    print("CANDIDATES:", len(manifest))
    print("AVAILABLE_FRAGMENTS:", len(planned))
    print("MISSING_FRAGMENTS:", len(missing))
    print("CONFIDENCE:", CONFIDENCE)
    print("WASTE_CLASSES:", WASTE_CLASSES)

    if args.dry_run:
        print("DRY_RUN: no inference or file writes")
        print("STEP 36.7.392: VALIDATION COMPLETED")
        return

    if missing:
        raise RuntimeError(
            "Missing image fragments; inference not started"
        )

    if not MODEL_PATH.is_file():
        raise RuntimeError("YOLO11n model not found")

    from ultralytics import YOLO

    model = YOLO(str(MODEL_PATH))

    for cls_id, name in WASTE_CLASSES.items():
        if model.names.get(cls_id) != name:
            raise RuntimeError("Unexpected YOLO model classes")

    output_dir = RESULT_ROOT / f"run_{run_id}"

    if output_dir.exists():
        raise RuntimeError(
            "Output directory exists; refusing to overwrite"
        )

    # Only create outputs when every input is available.
    output_dir.mkdir(parents=True)

    for entry in planned:
        image_path = entry["image_path"]

        results = model.predict(
            source=image_path,
            conf=CONFIDENCE,
            classes=list(WASTE_CLASSES),
            device="cpu",
            imgsz=1280,
            verbose=False,
        )

        detections = []

        for result in results:
            if result.boxes is None:
                continue

            for box in result.boxes:
                cls_id = int(box.cls.item())

                detections.append({
                    "class": WASTE_CLASSES[cls_id],
                    "confidence": round(
                        float(box.conf.item()), 4
                    ),
                    "bbox_xyxy": [
                        round(float(v), 2)
                        for v in box.xyxy[0].tolist()
                    ],
                    "review_status": "requires_review"
                })

        filename = (
            f"candidate_{entry['candidate_id']}"
            f"_part{entry['image_part']}.json"
        )

        result_path = output_dir / filename

        result_path.write_text(
            json.dumps({
                "search_run_id": run_id,
                "silver_id": silver_id,
                "candidate_id": entry["candidate_id"],
                "image_part": entry["image_part"],
                "image_path": image_path,
                "model_name": "ecofusion_yolo11n_best.pt",
                "confidence_threshold": CONFIDENCE,
                "detections": detections
            }, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        print(
            f"Candidate {entry['candidate_id']}, "
            f"part {entry['image_part']}: "
            f"{len(detections)} detections"
        )

    print("YOLO11N ANALYSIS COMPLETED")


if __name__ == "__main__":
    main()
