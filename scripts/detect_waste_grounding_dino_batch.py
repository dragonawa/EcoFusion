import argparse
import json
import os
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

MODEL_ID = "/models/grounding-dino-tiny"
PROMPT = (
    "pile of construction debris. "
    "heap of dumped garbage. "
    "rubbish pile."
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--images", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--silver-id", type=int, required=True)
    parser.add_argument(
        "--candidate-ids",
        type=int,
        nargs="+",
        required=True,
    )
    args = parser.parse_args()

    import torch
    from PIL import Image
    from torchvision.ops import nms
    from transformers import (
        AutoProcessor,
        AutoModelForZeroShotObjectDetection,
    )

    torch.set_num_threads(1)

    images_dir = Path(args.images)
    output_dir = Path(args.output)

    selected = set(args.candidate_ids)

    files = sorted(
        p
        for p in images_dir.glob("candidate_*_part*.jpg")
        if any(
            p.name.startswith(f"candidate_{candidate_id}_part")
            for candidate_id in selected
        )
    )

    if not files:
        raise RuntimeError("No panorama fragments found")

    pending = [
        p for p in files
        if not (
            output_dir /
            f"panorama_{args.silver_id}_{p.stem}" /
            "detections.json"
        ).is_file()
    ]

    print(
        "Images selected:", len(files),
        "| pending:", len(pending),
        flush=True,
    )

    if not pending:
        print(
            "All results already exist. Model loading skipped.",
            flush=True,
        )
        return

    print("Loading Grounding DINO once...", flush=True)

    processor = AutoProcessor.from_pretrained(
        MODEL_ID, local_files_only=True
    )

    model = AutoModelForZeroShotObjectDetection.from_pretrained(
        MODEL_ID, local_files_only=True
    ).to("cpu")

    model.eval()

    print("Model ready. Images:", len(files), flush=True)

    processed = 0
    reused = 0

    for image_path in files:
        name = image_path.stem

        result_dir = output_dir / (
            f"panorama_{args.silver_id}_{name}"
        )
        result_file = result_dir / "detections.json"

        if result_file.exists():
            reused += 1
            print("REUSED:", name, flush=True)
            continue

        with Image.open(image_path) as source:
            image = source.convert("RGB")

        inputs = processor(
            images=image,
            text=PROMPT,
            return_tensors="pt",
        ).to("cpu")

        with torch.no_grad():
            outputs = model(**inputs)

        result = processor.post_process_grounded_object_detection(
            outputs,
            inputs.input_ids,
            threshold=0.22,
            text_threshold=0.20,
            target_sizes=[(image.height, image.width)],
        )[0]

        boxes = result["boxes"].detach().cpu()
        scores = result["scores"].detach().cpu()

        indices = nms(
            boxes, scores, iou_threshold=0.45
        ).tolist() if len(boxes) else []

        detections = []

        for index, i in enumerate(indices, start=1):
            detections.append({
                "id": index,
                "class": "waste_pile_candidate",
                "confidence": round(float(scores[i]), 4),
                "bbox_xyxy": [
                    round(float(v), 1)
                    for v in boxes[i].tolist()
                ],
                "review_status": "requires_review",
            })

        data = {
            "system": "EcoFusion",
            "model": MODEL_ID,
            "source_image": str(image_path),
            "image_width": image.width,
            "image_height": image.height,
            "prompt": PROMPT,
            "detections": detections,
            "limitations": [
                "Detections are unverified candidates",
                "Image date must be checked separately",
                "Illegal dumping cannot be established from an image alone",
            ],
        }

        result_dir.mkdir(parents=True, exist_ok=True)

        result_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        processed += 1

        print(
            "DONE:", name,
            "| detections:", len(detections),
            flush=True,
        )

    print(
        "BATCH COMPLETED:",
        "processed =", processed,
        "reused =", reused,
        flush=True,
    )


if __name__ == "__main__":
    main()