"""P0-04: verify pinned face/OCR weights and run a CPU load and inference probe.

Every artifact recorded in the manifest is re-hashed, then the pinned RapidOCR
models read script-generated text fixtures and the pinned InsightFace
`buffalo_l` detection/recognition weights read script-generated face fixtures.
The probe fails loudly when a hash, a fixture assertion or an output contract
does not hold.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
import onnxruntime
from face_ocr_common import (
    FACE_MODEL_FILES,
    RAPIDOCR_MODEL_FILES,
    default_model_root,
    download_cache_dir,
    face_model_dir,
    load_json,
    ocr_model_dir,
    sha256_file,
    write_json,
)
from insightface.app import FaceAnalysis
from PIL import Image, ImageDraw, ImageFont
from rapidocr_onnxruntime import RapidOCR

MANIFEST_PATH = Path("tools/prepare_models/manifests/face-ocr.json")
RESULT_PATH = Path("tools/prepare_models/manifests/face-ocr-validation.json")
FONT_PATH = Path(r"C:\Windows\Fonts\msyh.ttc")
DET_SIZE = (640, 640)
DET_THRESHOLD = 0.5
PROVIDERS = ["CPUExecutionProvider"]
SAMPLE_FIXTURE = {
    "name": "sample-astronaut",
    "provider": "scikit-image sample data (skimage.data.astronaut)",
    "license": "public-domain sample data shipped with scikit-image; not from the user photo library",
    "source_url": "https://scikit-image.org/docs/stable/api/skimage.data.html",
}
OCR_FIXTURES = {
    "cjk": {
        "lines": ["照片搜索测试", "编号 20260911"],
        "required_substrings": ["20260911"],
        "required_any": ["照片", "搜索", "测试"],
    },
    "ascii": {
        "lines": ["Photo Search Test", "ID 20260911"],
        "required_substrings": ["20260911"],
        "required_any": ["photo", "search", "test"],
    },
}
FACE_FIXTURES = {
    "neutral": {"size": 512, "skin": (232, 190, 160), "hair": (62, 44, 34), "glasses": False},
    "warm": {"size": 420, "skin": (214, 160, 120), "hair": (28, 24, 28), "glasses": True},
    "cool": {"size": 600, "skin": (246, 214, 190), "hair": (120, 80, 40), "glasses": False},
}


def write_ocr_fixtures(directory: Path) -> dict[str, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    font = ImageFont.truetype(str(FONT_PATH), 56)
    for name, spec in OCR_FIXTURES.items():
        image = Image.new("RGB", (760, 320), (255, 255, 255))
        draw = ImageDraw.Draw(image)
        for index, line in enumerate(spec["lines"]):
            draw.text((40, 40 + index * 130), line, font=font, fill=(10, 10, 10))
        path = directory / f"ocr-{name}.png"
        image.save(path)
        paths[name] = path
    blank = Image.new("RGB", (760, 320), (255, 255, 255))
    path = directory / "ocr-blank.png"
    blank.save(path)
    paths["blank"] = path
    return paths


def draw_face(spec: dict[str, object]) -> Image.Image:
    """Draw a deterministic schematic face; it is a plumbing fixture, not a photo."""
    size = int(spec["size"])
    skin = spec["skin"]
    hair = spec["hair"]
    image = Image.new("RGB", (size, size), (238, 238, 240))
    draw = ImageDraw.Draw(image)
    unit = size / 100
    draw.ellipse(
        [20 * unit, 16 * unit, 80 * unit, 96 * unit],
        fill=skin,
        outline=(120, 90, 70),
        width=max(1, int(unit)),
    )
    draw.chord(
        [16 * unit, 8 * unit, 84 * unit, 56 * unit],
        start=180,
        end=360,
        fill=hair,
    )
    eye_y = 46 * unit
    for eye_x in (38 * unit, 62 * unit):
        draw.ellipse(
            [eye_x - 7 * unit, eye_y - 4 * unit, eye_x + 7 * unit, eye_y + 4 * unit],
            fill=(250, 250, 250),
            outline=(60, 60, 60),
        )
        draw.ellipse(
            [eye_x - 3 * unit, eye_y - 3 * unit, eye_x + 3 * unit, eye_y + 3 * unit],
            fill=(46, 70, 110),
        )
    for eye_x in (38 * unit, 62 * unit):
        draw.line(
            [eye_x - 8 * unit, eye_y - 9 * unit, eye_x + 8 * unit, eye_y - 11 * unit],
            fill=hair,
            width=max(1, int(2 * unit)),
        )
    draw.line(
        [50 * unit, 50 * unit, 50 * unit, 64 * unit],
        fill=(150, 110, 90),
        width=max(1, int(unit)),
    )
    draw.arc(
        [40 * unit, 60 * unit, 60 * unit, 78 * unit],
        start=20,
        end=160,
        fill=(150, 60, 60),
        width=max(1, int(2 * unit)),
    )
    if spec["glasses"]:
        for eye_x in (38 * unit, 62 * unit):
            draw.rectangle(
                [eye_x - 12 * unit, eye_y - 8 * unit, eye_x + 12 * unit, eye_y + 8 * unit],
                outline=(30, 30, 30),
                width=max(1, int(unit)),
            )
        draw.line(
            [50 * unit - 2 * unit, eye_y, 50 * unit + 2 * unit, eye_y],
            fill=(30, 30, 30),
            width=max(1, int(unit)),
        )
    return image


def write_face_fixtures(directory: Path) -> dict[str, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for name, spec in FACE_FIXTURES.items():
        path = directory / f"face-{name}.png"
        draw_face(spec).save(path)
        paths[name] = path
    return paths

def write_sample_face_fixture(directory: Path) -> tuple[dict[str, object] | None, Path | None]:
    """Export the public-domain scikit-image sample photo as a real-face fixture."""
    try:
        from skimage import data
    except Exception as error:  # pragma: no cover - optional dependency path
        return {"skipped": f"{type(error).__name__}: {error}"}, None
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"face-{SAMPLE_FIXTURE['name']}.png"
    Image.fromarray(data.astronaut()).save(path)
    record: dict[str, object] = {
        key: value for key, value in SAMPLE_FIXTURE.items() if key != "name"
    }
    record.update(
        {
            "name": SAMPLE_FIXTURE["name"],
            "path": str(path),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    )
    return record, path

def model_attributes(model: object) -> dict[str, object]:
    recorded = {}
    for key in ("input_mean", "input_std", "input_shape", "taskname"):
        if hasattr(model, key):
            value = getattr(model, key)
            recorded[key] = value.tolist() if isinstance(value, np.ndarray) else value
    return recorded


def ocr_probe(ocr_dir: Path, fixtures: dict[str, Path]) -> dict[str, object]:
    engine = RapidOCR(
        det_model_path=str(ocr_dir / RAPIDOCR_MODEL_FILES[0]),
        cls_model_path=str(ocr_dir / RAPIDOCR_MODEL_FILES[1]),
        rec_model_path=str(ocr_dir / RAPIDOCR_MODEL_FILES[2]),
    )
    records = []
    for name, path in fixtures.items():
        raw, elapsed = engine(str(path))
        boxes = []
        for item in raw or []:
            boxes.append(
                {
                    "box": [[float(point[0]), float(point[1])] for point in item[0]],
                    "text": item[1],
                    "score": float(item[2]),
                }
            )
        records.append(
            {
                "fixture": name,
                "path": str(path),
                "box_count": len(boxes),
                "text": " ".join(box["text"] for box in boxes),
                "boxes": boxes,
                "elapsed_seconds": float(np.sum(elapsed)) if elapsed is not None else None,
            }
        )
    return {
        "engine": "rapidocr-onnxruntime",
        "model_paths": {
            "detection": str(ocr_dir / RAPIDOCR_MODEL_FILES[0]),
            "classification": str(ocr_dir / RAPIDOCR_MODEL_FILES[1]),
            "recognition": str(ocr_dir / RAPIDOCR_MODEL_FILES[2]),
        },
        "records": records,
    }


def recognition_smoke(recognition_path: Path) -> dict[str, object]:
    """Run the recognition graph once on a deterministic zero patch."""
    session = onnxruntime.InferenceSession(str(recognition_path), providers=PROVIDERS)
    meta = session.get_inputs()[0]
    shape = [dim if isinstance(dim, int) else 1 for dim in meta.shape]
    blob = np.zeros(shape, dtype=np.float32)
    output = session.run(None, {meta.name: blob})[0]
    return {
        "input_name": meta.name,
        "input_shape": list(blob.shape),
        "output_shape": list(output.shape),
        "l2_norm": float(np.linalg.norm(output)),
        "note": "deterministic zero patch: graph plumbing only, not a face",
    }


def face_probe(face_dir: Path, fixtures: dict[str, Path]) -> dict[str, object]:
    app = FaceAnalysis(
        name=str(face_dir),
        allowed_modules=["detection", "recognition"],
        providers=PROVIDERS,
    )
    app.prepare(ctx_id=-1, det_thresh=DET_THRESHOLD, det_size=DET_SIZE)
    records = []
    for name, path in fixtures.items():
        image = cv2.imread(str(path))
        faces = app.get(image)
        records.append(
            {
                "fixture": name,
                "path": str(path),
                "face_count": len(faces),
                "faces": [
                    {
                        "bbox": [float(value) for value in face.bbox],
                        "det_score": float(face.det_score),
                        "kps": [[float(x), float(y)] for x, y in face.kps],
                        "embedding_shape": list(face.embedding.shape),
                        "embedding_l2_norm": float(np.linalg.norm(face.embedding)),
                    }
                    for face in faces
                ],
            }
        )
    return {
        "pack": face_dir.name,
        "directory": str(face_dir),
        "det_size": list(DET_SIZE),
        "det_threshold": DET_THRESHOLD,
        "models": {task: model_attributes(model) for task, model in app.models.items()},
        "records": records,
        "recognition_smoke": recognition_smoke(face_dir / FACE_MODEL_FILES["recognition"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-root", type=Path, default=default_model_root())
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--result", type=Path, default=RESULT_PATH)
    args = parser.parse_args()

    root = args.model_root.resolve()
    ocr_dir = ocr_model_dir(root)
    face_dir = face_model_dir(root)
    fixture_dir = download_cache_dir(root) / "p0-04-fixtures"
    steps: list[str] = []
    try:
        manifest = load_json(args.manifest.resolve())
        for artifact in [
            *manifest["ocr"]["artifacts"].values(),
            *manifest["face"]["artifacts"].values(),
        ]:
            path = root / artifact["path"]
            if (
                not path.is_file()
                or path.stat().st_size != artifact["bytes"]
                or sha256_file(path) != artifact["sha256"]
            ):
                raise ValueError(f"manifest hash or size mismatch: {path}")
        steps.append("manifest_hashes")

        ocr = ocr_probe(ocr_dir, write_ocr_fixtures(fixture_dir))
        steps.append("ocr_inference")
        ocr_by_fixture = {record["fixture"]: record for record in ocr["records"]}
        for name, spec in OCR_FIXTURES.items():
            record = ocr_by_fixture[name]
            if record["box_count"] == 0:
                raise ValueError(f"OCR detected no text on the {name} fixture")
            text = record["text"].lower()
            for expected in spec["required_substrings"]:
                if expected.lower() not in text:
                    raise ValueError(f"OCR {name} fixture missing {expected!r}: {record['text']!r}")
            if not any(candidate.lower() in text for candidate in spec["required_any"]):
                raise ValueError(
                    f"OCR {name} fixture hit none of {spec['required_any']}: {record['text']!r}"
                )
        if ocr_by_fixture["blank"]["box_count"] != 0:
            raise ValueError("OCR returned text for the blank fixture")

        face_fixtures = write_face_fixtures(fixture_dir)
        sample_record, sample_path = write_sample_face_fixture(fixture_dir)
        if sample_path is not None:
            face_fixtures[SAMPLE_FIXTURE["name"]] = sample_path
        face = face_probe(face_dir, face_fixtures)
        steps.append("face_inference")
        if set(face["models"]) != {"detection", "recognition"}:
            raise ValueError(f"unexpected face modules: {sorted(face['models'])}")
        expected_dimension = manifest["face"]["embedding"]["dimension"]
        smoke_shape = face["recognition_smoke"]["output_shape"]
        if smoke_shape[-1] != expected_dimension:
            raise ValueError(f"recognition output {smoke_shape} != manifest {expected_dimension}")
        for record in face["records"]:
            for detected in record["faces"]:
                if detected["embedding_shape"][-1] != expected_dimension:
                    raise ValueError(f"detected embedding shape {detected['embedding_shape']}")
        detected_fixtures = {record["fixture"]: record for record in face["records"]}
        if sample_path is not None and detected_fixtures[SAMPLE_FIXTURE["name"]]["face_count"] == 0:
            raise ValueError("no face detected in the public-domain sample photo")

        result = {
            "status": "pass",
            "task": "P0-04",
            "providers_available": onnxruntime.get_available_providers(),
            "providers_used": PROVIDERS,
            "fixture_directory": str(fixture_dir),
            "ocr": ocr,
            "face": face,
            "third_party_fixtures": [sample_record] if sample_record else [],
            "face_positive_case": any(record["face_count"] > 0 for record in face["records"]),
        }
        write_json(args.result.resolve(), result)
        print(
            {
                "status": "pass",
                "steps": [*steps, "result"],
                "result": str(args.result.resolve()),
                "ocr_text": {record["fixture"]: record["text"] for record in ocr["records"]},
                "face_counts": {
                    record["fixture"]: record["face_count"] for record in face["records"]
                },
                "recognition_output_shape": smoke_shape,
            }
        )
        return 0
    except Exception as error:
        print(
            {
                "status": "fail",
                "stage": steps[-1] if steps else "setup",
                "exception_type": type(error).__name__,
                "message": str(error),
                "completed_steps": steps,
            },
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
