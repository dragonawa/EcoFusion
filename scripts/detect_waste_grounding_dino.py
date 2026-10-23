import argparse
import json
from pathlib import Path


MODEL_ID = "/models/grounding-dino-tiny"

PROMPT = (
    "pile of construction debris. "
    "heap of dumped garbage. "
    "rubbish pile."
)


def detect(image_path, output_dir):
    import torch
    from PIL import Image
    from torchvision.ops import nms
    from transformers import (
        AutoProcessor,
        AutoModelForZeroShotObjectDetection,
    )

    image_path = Path(image_path).resolve()
    output_dir = Path(output_dir).resolve()

    if not image_path.is_file():
        raise FileNotFoundError(image_path)

    output_dir.mkdir(parents=True, exist_ok=True)

    device = "cuda" if torch.cuda.is_available() else "cpu"

    print("Model:", MODEL_ID)
    print("Device:", device)
    print("Image:", image_path)

    processor = AutoProcessor.from_pretrained(MODEL_ID, local_files_only=True)

    model = AutoModelForZeroShotObjectDetection.from_pretrained(
        MODEL_ID, local_files_only=True
    ).to(device)

    model.eval()

    image = Image.open(image_path).convert("RGB")

    inputs = processor(
        images=image,
        text=PROMPT,
        return_tensors="pt",
    ).to(device)

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

    if len(boxes):
        kept_indices = nms(
            boxes,
            scores,
            iou_threshold=0.45,
        ).tolist()
    else:
        kept_indices = []

    detections = []

    for index, i in enumerate(kept_indices, start=1):
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

    result_path = output_dir / "detections.json"

    result_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("Detections:", len(detections))
    print("JSON:", result_path)

    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="EcoFusion Grounding DINO detector"
    )

    parser.add_argument(
        "--image",
        required=True,
        help="Path to input image",
    )

    parser.add_argument(
        "--output",
        required=True,
        help="Directory for detection results",
    )

    args = parser.parse_args()

    detect(args.image, args.output)

