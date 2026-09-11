r"""Build the authorized evaluation corpus: attributes, face counts and OCR text.

All five source directories below were explicitly authorized by the user on 2026-09-11 for the
P0-06 evaluation (D:\SZ\朋友, D:\SZ\canon, D:\SZ\vsco, D:\SZ\网络, D:\SZ\pic). The tools only
read them: nothing is written next to the photos, and no photo is modified, moved or deleted.

    uv run python tools/evaluation/build_corpus.py               # attributes + summary
    uv run python tools/evaluation/build_corpus.py --faces       # add InsightFace counts (CUDA)
    uv run python tools/evaluation/build_corpus.py --ocr         # add RapidOCR text (CUDA)

The corpus file (absolute paths, OCR text) stays in the user data directory, outside the
repository; the repository only receives aggregate counts. Face/OCR passes append to JSONL
caches keyed by image id, so a long run can be resumed instead of restarted.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
from corpus import (  # noqa: E402
    CorpusError,
    default_evaluation_dir,
    image_attributes,
    read_json,
    scan_sources,
    write_json,
)
from PIL import Image, ImageOps  # noqa: E402
from runtime_common import (  # noqa: E402
    RAPIDOCR_MODEL_FILES,
    REPOSITORY_ROOT,
    default_model_root,
    face_model_dir,
    ocr_model_dir,
)

DEFAULT_SOURCES = [
    Path(r"D:\SZ\朋友"),
    Path(r"D:\SZ\canon"),
    Path(r"D:\SZ\vsco"),
    Path(r"D:\SZ\网络"),
    Path(r"D:\SZ\pic"),
]
DET_SIZE = (640, 640)
DET_THRESHOLD = 0.5
REPO_SUMMARY = REPOSITORY_ROOT / "docs/reports/P0-06-eval-corpus-summary.json"


def attributes_path(directory: Path) -> Path:
    return directory / "corpus-attributes.json"


def faces_cache(directory: Path) -> Path:
    return directory / "faces.jsonl"


def ocr_cache(directory: Path) -> Path:
    return directory / "ocr.jsonl"


def corpus_path(directory: Path) -> Path:
    return directory / "corpus.json"


def load_jsonl(path: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return records
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict) and "id" in record:
                records[str(record["id"])] = record
    return records


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def build_attributes(
    sources: Iterable[Path], *, limit: int | None, directory: Path
) -> list[dict[str, Any]]:
    images = scan_sources(sources, limit=limit)
    records: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    started = time.perf_counter()
    for index, image in enumerate(images, start=1):
        try:
            records.append(image_attributes(image))
        except (CorpusError, OSError, ValueError) as error:  # skip unreadable files, keep going
            failures.append({"id": image.id, "error": f"{type(error).__name__}: {error}"})
        if index % 250 == 0:
            elapsed = time.perf_counter() - started
            print(
                json.dumps(
                    {
                        "phase": "attributes",
                        "processed": index,
                        "total": len(images),
                        "images_per_second": round(index / elapsed, 2),
                    }
                ),
                flush=True,
            )
    payload = {
        "sources": [str(source) for source in sources],
        "count": len(records),
        "failures": failures,
        "images": records,
    }
    write_json(attributes_path(directory), payload)
    return records


def load_bgr(path: str) -> np.ndarray:
    """Unicode-safe loader: cv2.imread cannot open non-ASCII paths on Windows.

    Several authorized folders use Chinese file names, so the image is decoded with Pillow
    (which handles them) and converted to the BGR array InsightFace expects.
    """
    with Image.open(path) as source:
        rgb = ImageOps.exif_transpose(source).convert("RGB")
        array = np.asarray(rgb)
    return np.ascontiguousarray(array[:, :, ::-1])


def _is_complete(record: dict[str, Any] | None) -> bool:
    """A cached record counts only when it holds a real value and no error."""
    return bool(record) and record.get("error") is None and record.get("face_count") is not None


def run_faces(directory: Path, images: list[dict[str, Any]], root: Path) -> dict[str, dict[str, Any]]:
    from insightface.app import FaceAnalysis

    from photo_search.runtime.device import preload_runtime_dlls

    preload = preload_runtime_dlls()
    cache = faces_cache(directory)
    done = load_jsonl(cache)
    pending = [image for image in images if not _is_complete(done.get(image["id"]))]
    if not pending:
        return done
    face_dir = face_model_dir(root)
    app = FaceAnalysis(
        name=str(face_dir),
        allowed_modules=["detection", "recognition"],
        providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
    )
    app.prepare(ctx_id=0, det_thresh=DET_THRESHOLD, det_size=DET_SIZE)
    print(json.dumps({"phase": "faces", "preload": preload, "pending": len(pending)}), flush=True)
    started = time.perf_counter()
    for index, image in enumerate(pending, start=1):
        path = image["path"]
        record: dict[str, Any] = {"id": image["id"], "path": path}
        try:
            matrix = load_bgr(path)
            faces = app.get(matrix)
            record["face_count"] = len(faces)
            record["max_det_score"] = (
                round(float(max(face.det_score for face in faces)), 4) if faces else None
            )
            record["error"] = None
        except Exception as error:
            record.update({"face_count": None, "max_det_score": None, "error": f"{type(error).__name__}: {error}"})
        append_jsonl(cache, record)
        if index % 100 == 0:
            elapsed = time.perf_counter() - started
            print(
                json.dumps(
                    {
                        "phase": "faces",
                        "processed": index,
                        "pending": len(pending),
                        "images_per_second": round(index / elapsed, 3),
                    }
                ),
                flush=True,
            )
    return load_jsonl(cache)


def run_ocr(directory: Path, images: list[dict[str, Any]], root: Path) -> dict[str, dict[str, Any]]:
    from rapidocr_onnxruntime import RapidOCR

    from photo_search.runtime.device import preload_runtime_dlls

    preload = preload_runtime_dlls()
    cache = ocr_cache(directory)
    done = load_jsonl(cache)
    pending = [image for image in images if image["id"] not in done]
    if not pending:
        return done
    ocr_dir = ocr_model_dir(root)
    engine = RapidOCR(
        det_model_path=str(ocr_dir / RAPIDOCR_MODEL_FILES[0]),
        cls_model_path=str(ocr_dir / RAPIDOCR_MODEL_FILES[1]),
        rec_model_path=str(ocr_dir / RAPIDOCR_MODEL_FILES[2]),
        det_use_cuda=True,
        cls_use_cuda=True,
        rec_use_cuda=True,
    )
    print(json.dumps({"phase": "ocr", "preload": preload, "pending": len(pending)}), flush=True)
    started = time.perf_counter()
    for index, image in enumerate(pending, start=1):
        path = image["path"]
        record: dict[str, Any] = {"id": image["id"], "path": path}
        try:
            boxes, _ = engine(path)
            texts = [str(box[1]) for box in (boxes or [])]
            joined = " ".join(texts)
            record.update(
                {
                    "ocr_boxes": len(texts),
                    "ocr_chars": len(joined),
                    "ocr_has_digits": any(character.isdigit() for character in joined),
                    "ocr_has_latin": any(character.isascii() and character.isalpha() for character in joined),
                    "ocr_text": joined,
                    "error": None,
                }
            )
        except Exception as error:
            record.update({"ocr_boxes": None, "ocr_text": "", "error": f"{type(error).__name__}: {error}"})
        append_jsonl(cache, record)
        if index % 100 == 0:
            elapsed = time.perf_counter() - started
            print(
                json.dumps(
                    {
                        "phase": "ocr",
                        "processed": index,
                        "pending": len(pending),
                        "images_per_second": round(index / elapsed, 3),
                    }
                ),
                flush=True,
            )
    return load_jsonl(cache)


def merge(
    directory: Path,
    attributes: list[dict[str, Any]],
    faces: dict[str, dict[str, Any]],
    ocr: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    images: list[dict[str, Any]] = []
    for record in attributes:
        image_id = record["id"]
        face = faces.get(image_id, {})
        text = ocr.get(image_id, {})
        merged = dict(record)
        merged.update(
            {
                "face_count": face.get("face_count"),
                "max_det_score": face.get("max_det_score"),
                "ocr_boxes": text.get("ocr_boxes"),
                "ocr_chars": text.get("ocr_chars"),
                "ocr_has_digits": text.get("ocr_has_digits"),
                "ocr_has_latin": text.get("ocr_has_latin"),
            }
        )
        images.append(merged)
    payload = {
        "sources": sorted({record["source"] for record in images}),
        "count": len(images),
        "images": images,
    }
    write_json(corpus_path(directory), payload)
    return payload


def summarise(corpus: dict[str, Any], attributes: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate-only summary that is safe to commit to the repository."""
    def quantiles(values: list[float]) -> dict[str, float]:
        if not values:
            return {}
        ordered = sorted(values)
        def pick(ratio: float) -> float:
            return round(ordered[min(len(ordered) - 1, int(ratio * (len(ordered) - 1)))], 4)
        return {"p05": pick(0.05), "p50": pick(0.5), "p95": pick(0.95)}

    by_source: dict[str, int] = {}
    by_orientation: dict[str, int] = {}
    by_dominant_hue: dict[str, int] = {}
    by_year: dict[str, int] = {}
    for record in attributes:
        by_source[record["source"]] = by_source.get(record["source"], 0) + 1
        by_orientation[record["orientation"]] = by_orientation.get(record["orientation"], 0) + 1
        by_dominant_hue[record["dominant_hue"]] = by_dominant_hue.get(record["dominant_hue"], 0) + 1
        by_year[str(record["year"])] = by_year.get(str(record["year"]), 0) + 1
    faces = [record["face_count"] for record in corpus["images"] if record.get("face_count") is not None]
    ocr = [record for record in corpus["images"] if record.get("ocr_boxes") is not None]
    summary = {
        "status": "ok",
        "task": "P0-06",
        "stage": "B",
        "sources": corpus["sources"],
        "images": len(corpus["images"]),
        "by_source": dict(sorted(by_source.items())),
        "by_orientation": dict(sorted(by_orientation.items())),
        "by_dominant_hue": dict(sorted(by_dominant_hue.items())),
        "by_year": dict(sorted(by_year.items())),
        "brightness": quantiles([float(record["brightness"]) for record in attributes]),
        "saturation": quantiles([float(record["saturation"]) for record in attributes]),
        "face_coverage": len(faces),
        "faces_detected_total": int(sum(faces)) if faces else 0,
        "images_with_faces": int(sum(1 for count in faces if count > 0)),
        "images_with_multiple_faces": int(sum(1 for count in faces if count >= 2)),
        "ocr_coverage": len(ocr),
        "images_with_text": int(sum(1 for record in ocr if (record.get("ocr_chars") or 0) > 0)),
        "images_with_digits": int(sum(1 for record in ocr if record.get("ocr_has_digits"))),
        "note": (
            "aggregate counts only; absolute paths and OCR text stay in the user data directory"
        ),
    }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, action="append", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-images", type=int, default=None, help="debug guard for a trial run")
    parser.add_argument("--faces", action="store_true")
    parser.add_argument("--ocr", action="store_true")
    parser.add_argument("--merge-only", action="store_true")
    parser.add_argument(
        "--paths-only",
        action="store_true",
        help="re-attach absolute paths to an existing attribute file without re-reading images",
    )
    parser.add_argument(
        "--force-attributes",
        action="store_true",
        help="re-read every image instead of reusing the existing attribute file",
    )
    parser.add_argument("--directory", type=Path, default=default_evaluation_dir())
    args = parser.parse_args()

    sources = args.source or DEFAULT_SOURCES
    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    limit = args.limit
    if args.max_images is not None:
        limit = args.max_images if limit is None else min(limit, args.max_images)

    steps: list[str] = []
    started = time.perf_counter()
    try:
        if args.paths_only:
            payload = read_json(attributes_path(directory))
            attributes = payload["images"]
            scanned = scan_sources(sources, limit=limit)
            by_id = {image.id: image for image in scanned}
            for record in attributes:
                match = by_id.get(record["id"])
                if match is not None:
                    record["path"] = str(match.path)
            payload["images"] = attributes
            write_json(attributes_path(directory), payload)
        elif args.merge_only:
            payload = read_json(attributes_path(directory))
            attributes = payload["images"]
        elif attributes_path(directory).is_file() and not args.force_attributes:
            # Reuse the finished attribute pass and only add the missing GPU phases.
            attributes = read_json(attributes_path(directory))["images"]
        else:
            attributes = build_attributes(sources, limit=limit, directory=directory)
        steps.append("attributes")
        if args.max_images is not None:
            attributes = attributes[: args.max_images]

        faces = load_jsonl(faces_cache(directory))
        ocr = load_jsonl(ocr_cache(directory))
        root = default_model_root()
        if args.faces:
            faces = run_faces(directory, attributes, root)
            steps.append("faces")
        if args.ocr:
            ocr = run_ocr(directory, attributes, root)
            steps.append("ocr")

        corpus = merge(directory, attributes, faces, ocr)
        summary = summarise(corpus, attributes)
        summary["seconds"] = round(time.perf_counter() - started, 1)
        summary["completed_steps"] = steps
        write_json(REPO_SUMMARY, summary)
        print(
            json.dumps(
                {
                    "status": "ok",
                    "steps": [*steps, "merge", "summary"],
                    "corpus": str(corpus_path(directory)),
                    "summary": str(REPO_SUMMARY),
                    "images": summary["images"],
                    "by_source": summary["by_source"],
                    "images_with_faces": summary["images_with_faces"],
                    "images_with_text": summary["images_with_text"],
                    "seconds": summary["seconds"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "fail",
                    "stage": steps[-1] if steps else "setup",
                    "exception_type": type(error).__name__,
                    "message": str(error),
                    "completed_steps": steps,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
