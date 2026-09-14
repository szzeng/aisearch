"""P3-02 real-fixture smoke and threshold evidence for the face pipeline.

It builds a throwaway library in ``%TEMP%`` (never a real photo library), runs the *product*
path end to end (register -> scan -> face jobs -> independent candidate index -> conservative
association -> read-only status) on real public-domain faces, and prints what happened. It
also re-measures the genuine/impostor similarity distributions the thresholds were derived
from, so the calibration in ``photo_search.face.contract`` stays traceable.

Fixtures (all shipped with libraries this project already depends on, none of them private):

* ``skimage.data.astronaut`` - a public-domain NASA portrait;
* ``insightface/data/images/t1.jpg`` - a six-person group photo used by InsightFace's own tests;
* ``insightface/data/images/Tom_Hanks_54745.png`` - a 112x112 tightly cropped portrait;
* synthetic identity-preserving variants of the astronaut (brightness, low resolution).

Exit code 0 means every expectation held. Any failed expectation is printed and exits 1.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from photo_search.catalog.db import Catalog
from photo_search.face import contract
from photo_search.face.engine import FaceEngineCache, analyze_image
from photo_search.face.index import FaceIndexCoordinator, cosine
from photo_search.face.processor import face_processor_spec
from photo_search.face.service import detect, face_targets, job_counts
from photo_search.face.weights import resolve as resolve_weights
from photo_search.ingestion.service import CatalogService
from photo_search.scheduler.projection import face_summary

DEFAULT_WORK_DIR = Path.home() / "AppData" / "Local" / "Temp" / "photo-search-face-smoke"


def _fixture_images() -> dict[str, Image.Image]:
    """The real fixture pictures, loaded from the packages that ship them."""
    import insightface
    from skimage import data as skimage_data

    images_dir = Path(insightface.__file__).resolve().parent / "data" / "images"
    return {
        "astronaut.png": Image.fromarray(skimage_data.astronaut()).convert("RGB"),
        "group.jpg": Image.open(images_dir / "t1.jpg").convert("RGB"),
        "portrait.png": Image.open(images_dir / "Tom_Hanks_54745.png").convert("RGB"),
    }


def _brighten(image: Image.Image) -> Image.Image:
    values = np.clip(np.asarray(image, dtype=np.float32) * 1.22 + 8.0, 0, 255)
    return Image.fromarray(values.astype(np.uint8))


def _lowres(image: Image.Image, *, factor: float = 0.42) -> Image.Image:
    size = (max(1, int(image.width * factor)), max(1, int(image.height * factor)))
    return image.resize(size, Image.BILINEAR)


def _write_library(root: Path) -> Path:
    library = root / "library"
    library.mkdir(parents=True, exist_ok=True)
    fixtures = _fixture_images()
    fixtures["astronaut.png"].save(library / "astronaut.png")
    fixtures["group.jpg"].save(library / "group.jpg", quality=95)
    fixtures["portrait.png"].save(library / "portrait.png")
    _brighten(fixtures["astronaut.png"]).save(library / "astronaut_bright.jpg", quality=92)
    _lowres(fixtures["astronaut.png"]).save(library / "astronaut_lowres.jpg", quality=70)
    # The composite case of architecture §8.3: the very same face twice in one picture. The
    # second one must become a recorded conflict suggestion, never a silent second member.
    astronaut = fixtures["astronaut.png"]
    composite = Image.new("RGB", (astronaut.width * 2, astronaut.height), (128, 128, 128))
    composite.paste(astronaut, (0, 0))
    composite.paste(astronaut, (astronaut.width, 0))
    composite.save(library / "composite.png")
    return library


def _install_weights(data_root: Path) -> list[dict[str, Any]]:
    """Copy the machine's pinned files into the throwaway model root."""
    resolved = resolve_weights()
    target_root = resolve_weights(data_root).root
    copied: list[dict[str, Any]] = []
    for entry in resolved.files:
        destination = target_root / entry.relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(entry.absolute_path, destination)
        copied.append(
            {
                "role": entry.role,
                "path": entry.relative_path,
                "bytes": entry.expected_bytes,
            }
        )
    return copied


def _percentiles(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    array = np.sort(np.asarray(values, dtype=np.float64))
    return {
        "n": int(len(values)),
        "min": float(array[0]),
        "p05": float(np.percentile(array, 5)),
        "p50": float(np.percentile(array, 50)),
        "p95": float(np.percentile(array, 95)),
        "max": float(array[-1]),
    }


def _augment(image: Image.Image, name: str) -> Image.Image | None:
    """Identity-preserving variants of one real photo (the calibration's genuine side)."""
    import io

    if name == "bright":
        array = np.clip(np.asarray(image, dtype=np.float32) * 1.25 + 8, 0, 255)
    elif name == "dark":
        array = np.clip(np.asarray(image, dtype=np.float32) * 0.78 - 6, 0, 255)
    elif name == "contrast":
        mean = float(np.asarray(image, dtype=np.float32).mean())
        array = np.clip((np.asarray(image, dtype=np.float32) - mean) * 1.35 + mean, 0, 255)
    elif name == "jpeg":
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=55)
        buffer.seek(0)
        array = np.asarray(Image.open(buffer).convert("RGB"), dtype=np.float32)
    elif name == "blur":
        array = np.asarray(
            image.resize((image.width // 3, image.height // 3), Image.BILINEAR).resize(
                (image.width, image.height), Image.BILINEAR
            )
        )
    else:
        return None
    return Image.fromarray(np.clip(array, 0, 255).astype(np.uint8))


def _align_variant(aligned: np.ndarray, name: str) -> np.ndarray:
    """Geometric variants of the already-aligned crop (the recogniser's own tolerance)."""
    crop = Image.fromarray(aligned)
    if name == "rotate":
        changed = crop.rotate(12.0, resample=Image.BILINEAR, fillcolor=(0, 0, 0))
    elif name == "mirror":
        changed = crop.transpose(Image.FLIP_LEFT_RIGHT)
    elif name == "pose":
        changed = crop.transform(
            crop.size,
            Image.AFFINE,
            (1.0, 0.20, -0.20 * crop.size[1] / 2, 0.0, 1.0, 0.0),
            resample=Image.BILINEAR,
            fillcolor=(0, 0, 0),
        )
    else:  # "small": a 28-pixel face upscaled to the alignment size
        changed = crop.resize((28, 28), Image.BILINEAR).resize((112, 112), Image.BILINEAR)
    return np.asarray(changed, dtype=np.uint8)


def _iou(first: Any, second: Any) -> float:
    left = max(float(first[0]), float(second[0]))
    top = max(float(first[1]), float(second[1]))
    right = min(float(first[2]), float(second[2]))
    bottom = min(float(first[3]), float(second[3]))
    inter = max(0.0, right - left) * max(0.0, bottom - top)
    area_a = max(0.0, float(first[2] - first[0])) * max(0.0, float(first[3] - first[1]))
    area_b = max(0.0, float(second[2] - second[0])) * max(0.0, float(second[3] - second[1]))
    union = area_a + area_b - inter
    return 0.0 if union <= 0 else inter / union


def _calibration_blocking(
    cache: FaceEngineCache, data_root: Path, device: str
) -> dict[str, Any]:
    """Re-measure the distributions ``contract.CALIBRATION`` records, from the same pipeline."""
    engine = cache.engine(data_root, device=device)
    fixtures = _fixture_images()
    real: dict[str, list[Any]] = {}
    for name, image in fixtures.items():
        real[name] = list(analyze_image(engine, image).faces)

    # Impostor side: every pair of the eight real faces, including two strangers who happen to
    # share one picture. That is the stricter reading - the association never compares two faces
    # of the same photo, but a threshold justified only by cross-picture pairs would sit lower.
    impostor: list[float] = []
    identities = [face for faces in real.values() for face in faces]
    for index, left in enumerate(identities):
        for right in identities[index + 1 :]:
            impostor.append(cosine(left.embedding, right.embedding))

    # Genuine side, part 1: the whole photo changes photometrically, then the product pipeline
    # detects, aligns and embeds it again; faces are matched back by bounding-box IoU.
    genuine_pipeline: list[float] = []
    for name, image in fixtures.items():
        for variant in ("bright", "dark", "contrast", "jpeg", "blur"):
            changed = _augment(image, variant)
            if changed is None:
                continue
            found = list(analyze_image(engine, changed).faces)
            for face in real[name]:
                if not found:
                    continue
                best = max(found, key=lambda item: _iou(face.bbox, item.bbox))
                if _iou(face.bbox, best.bbox) < 0.3:
                    continue
                genuine_pipeline.append(cosine(face.embedding, best.embedding))

    # Genuine side, part 2: the aligned crop itself changes geometrically.
    genuine_align: list[float] = []
    for name, image in fixtures.items():
        for face in real[name]:
            aligned = _aligned_crop(image, face)
            for variant in ("rotate", "mirror", "pose", "small"):
                changed = _align_variant(aligned, variant)
                vector = _embed_crop(engine, changed)
                genuine_align.append(cosine(face.embedding, vector))
    combined = genuine_pipeline + genuine_align
    det_scores = [float(face.det_score) for face in identities]
    pixels = [float(face.face_pixels) for face in identities]
    blurs = [float(face.blur_variance) for face in identities]
    measured = {
        "genuine_pipeline": _percentiles(genuine_pipeline),
        "genuine_align": _percentiles(genuine_align),
        "impostor": _percentiles(impostor),
        "merge_threshold": float(contract.MERGE_THRESHOLD),
        "suggest_threshold": float(contract.SUGGEST_THRESHOLD),
        "measured_gap_holds": bool(
            combined and impostor and min(combined) > max(impostor)
        ),
        "min_genuine": min(combined) if combined else None,
        "max_impostor": max(impostor) if impostor else None,
        "det_score_min": min(det_scores) if det_scores else None,
        "min_face_pixels": min(pixels) if pixels else None,
        "blur_variance_min": min(blurs) if blurs else None,
    }
    recorded = contract.CALIBRATION
    comparisons = {
        "max_impostor": measured["max_impostor"],
        "min_genuine": measured["min_genuine"],
        "det_score_min": measured["det_score_min"],
        "min_face_pixels": measured["min_face_pixels"],
        "blur_variance_min": measured["blur_variance_min"],
    }
    measured["recorded_calibration_matches"] = all(
        value is not None
        and abs(float(value) - float(recorded[key])) <= 0.02
        for key, value in comparisons.items()
    )
    measured["recorded_calibration"] = {
        key: recorded[key] for key in ("max_impostor", "min_genuine", "gap")
    }
    return measured


def _aligned_crop(image: Image.Image, face: Any) -> np.ndarray:
    """Re-derive the aligned crop of one already-detected face (product geometry)."""
    from photo_search.face.engine import warp_aligned

    working = image
    if max(image.size) > contract.WORKING_LONG_EDGE:
        scale = contract.WORKING_LONG_EDGE / float(max(image.size))
        working = image.resize(
            (max(1, int(round(image.width * scale))), max(1, int(round(image.height * scale)))),
            Image.LANCZOS,
        )
        factor = scale
    else:
        factor = 1.0
    landmarks = [(x * factor, y * factor) for x, y in face.landmarks]
    return warp_aligned(working, landmarks)


def _embed_crop(engine: Any, aligned: np.ndarray) -> tuple[float, ...]:
    vector = engine.recognize(aligned)
    return tuple(float(value) for value in vector)


def _library_report(catalog: Catalog) -> dict[str, Any]:
    assets = catalog.read_all(
        "SELECT a.asset_id AS asset_id, f.relative_path AS path FROM assets AS a "
        "JOIN file_locations AS f ON f.asset_id = a.asset_id ORDER BY a.asset_id"
    )
    by_asset: dict[int, str] = {int(row["asset_id"]): str(row["path"]) for row in assets}
    faces = catalog.read_all(
        "SELECT face_id, asset_id, ordinal, quality_state, quality_reason, face_pixels, "
        "blur_variance, det_score FROM faces ORDER BY asset_id, ordinal"
    )
    links = catalog.read_all(
        "SELECT l.face_id AS face_id, l.person_id AS person_id, l.decision AS decision, "
        "l.assignment_kind AS kind, l.confidence AS confidence, l.reason AS reason, "
        "f.asset_id AS asset_id FROM face_person_links AS l JOIN faces AS f ON f.face_id = l.face_id "
        "ORDER BY l.person_id, f.asset_id"
    )
    persons = catalog.read_all(
        "SELECT person_id, name, merged_into FROM persons ORDER BY person_id"
    )
    return {
        "assets": [
            {
                "asset_id": int(row["asset_id"]),
                "file": Path(str(row["path"])).name,
                "faces": sum(1 for face in faces if int(face["asset_id"]) == int(row["asset_id"])),
            }
            for row in assets
        ],
        "faces": [
            {
                "file": Path(by_asset[int(face["asset_id"])]).name,
                "ordinal": int(face["ordinal"]),
                "quality": str(face["quality_state"]),
                "quality_reason": face["quality_reason"],
                "face_pixels": float(face["face_pixels"]),
                "blur_variance": float(face["blur_variance"]),
                "det_score": float(face["det_score"]),
            }
            for face in faces
        ],
        "persons": [
            {
                "person_id": int(person["person_id"]),
                "name": person["name"],
                "members": [
                    {
                        "file": Path(by_asset[int(link["asset_id"])]).name,
                        "decision": str(link["decision"]),
                        "kind": str(link["kind"]),
                        "confidence": round(float(link["confidence"]), 4),
                        "reason": link["reason"],
                    }
                    for link in links
                    if int(link["person_id"]) == int(person["person_id"])
                ],
            }
            for person in persons
        ],
    }


def _expect(problems: list[str], condition: bool, message: str) -> None:
    if not condition:
        problems.append(message)


def run(work_dir: Path, *, device: str) -> dict[str, Any]:
    if work_dir.exists():
        shutil.rmtree(work_dir)
    library = _write_library(work_dir)
    data_root = work_dir / "PhotoSearchData"
    copied = _install_weights(data_root)
    problems: list[str] = []
    service = CatalogService.open(data_root=data_root)
    try:
        catalog = service.catalog
        source = service.register_source(display_name="face-smoke", root_path=library)
        scan = service.run_scan(int(source["source_id"]))
        _expect(problems, int(scan.get("errors", 0) or 0) == 0, "the scan reported errors")
        targets = face_targets(catalog)
        result = detect(catalog, data_root=data_root, device=device)
        report = _library_report(catalog)
        summary = face_summary(catalog.db_path, weights_present=True) or {}
        index = FaceIndexCoordinator(catalog).index_state()

        faces_by_file: dict[str, list[dict[str, Any]]] = {}
        for face in report["faces"]:
            faces_by_file.setdefault(str(face["file"]), []).append(face)
        person_of_file: dict[str, set[int]] = {}
        for person in report["persons"]:
            for member in person["members"]:
                if str(member["decision"]) == "CONFIRMED":
                    person_of_file.setdefault(str(member["file"]), set()).add(
                        int(person["person_id"])
                    )

        _expect(problems, len(targets) == 6, f"expected 6 assets, found {len(targets)}")
        _expect(
            problems,
            len(faces_by_file.get("group.jpg", [])) == 6,
            "the six-person group photo did not produce six faces",
        )
        _expect(
            problems,
            len(faces_by_file.get("astronaut.png", [])) == 1,
            "the astronaut portrait did not produce exactly one face",
        )
        _expect(
            problems,
            len(faces_by_file.get("portrait.png", [])) == 1,
            "the tightly cropped portrait was not detected on the padded canvas",
        )
        astronaut_person = person_of_file.get("astronaut.png", set())
        bright_person = person_of_file.get("astronaut_bright.jpg", set())
        _expect(
            problems,
            bool(astronaut_person) and astronaut_person == bright_person,
            "the bright variant of the same person did not join the same unnamed group",
        )
        _expect(
            problems,
            astronaut_person != person_of_file.get("portrait.png", set()),
            "two different people were grouped together",
        )
        group_persons = {
            person
            for face in faces_by_file.get("group.jpg", [])
            for person in person_of_file.get(str(face["file"]), set())
        }
        _expect(
            problems,
            len(group_persons) >= 5,
            "the group photo's distinct people were collapsed into too few groups",
        )
        _expect(
            problems,
            not (group_persons & astronaut_person),
            "a group-photo stranger was merged with the astronaut",
        )
        composite_faces = faces_by_file.get("composite.png", [])
        composite_states = sorted(
            (
                member["decision"]
                for person in report["persons"]
                for member in person["members"]
                if str(member["file"]) == "composite.png"
            )
        )
        _expect(
            problems,
            len(composite_faces) == 2,
            f"the composite picture produced {len(composite_faces)} faces instead of 2",
        )
        _expect(
            problems,
            composite_states == ["CONFIRMED", "SUGGESTED"],
            f"the composite picture was not recorded as one member plus one conflict: {composite_states}",
        )
        _expect(
            problems,
            int(summary.get("same_image_conflicts", 0)) == 1,
            "the same-image conflict was not recorded exactly once",
        )
        _expect(
            problems,
            int(summary.get("persons", 0)) == 8,
            f"expected 8 unnamed groups, found {summary.get('persons')}",
        )
        _expect(
            problems,
            all(str(face["quality"]) in ("OK", "LOW") for face in report["faces"]),
            "a face carried an unknown quality state",
        )
        _expect(
            problems,
            int(summary.get("stale_assets", 0)) == 0
            and int(summary.get("associations_queued", 0)) == 0,
            "the face facts or the association pass did not finish",
        )
        _expect(
            problems,
            index["state"] == "READY" and index["is_active"] is True,
            "the independent face candidate index is not ready and active",
        )
        _expect(
            problems,
            not [
                state
                for state in index["entries"]
                if state not in ("CONFIRMED",)
            ],
            "the face candidate index still has unconfirmed entries",
        )

        # A second, forced pass over the same content must not add a face or a group.
        persons_before = int(summary.get("persons", 0))
        faces_before = int(summary.get("faces", 0))
        again = detect(catalog, data_root=data_root, device=device, force=True)
        summary_again = face_summary(catalog.db_path, weights_present=True) or {}
        _expect(
            problems,
            int(summary_again.get("faces", 0)) == faces_before,
            "re-running the same content produced a second set of faces",
        )
        _expect(
            problems,
            int(summary_again.get("persons", 0)) == persons_before,
            "re-running the same content produced a second set of person groups",
        )

        calibration = _calibration_blocking(FaceEngineCache(), data_root, device)
        _expect(problems, calibration["measured_gap_holds"], "the measured similarity gap broke")
        _expect(
            problems,
            calibration["recorded_calibration_matches"],
            "the recorded calibration in photo_search.face.contract no longer matches the "
            "measured distribution",
        )

        return {
            "status": "ok" if not problems else "problems",
            "device_requested": str(device),
            "providers": result["run"]["providers"],
            "qualified_devices": result["run"]["qualified_devices"],
            "weights": copied,
            "processor": {
                "processor_id": face_processor_spec().processor_id,
                "space_id": str(contract.FACE_SPACE_ID),
                "model_fingerprint": str(contract.MODEL_FINGERPRINT),
                "config_hash": str(contract.CONFIG_HASH),
            },
            "scan": {"errors": int(scan.get("errors", 0) or 0)},
            "jobs": job_counts(catalog),
            "index": index,
            "summary": {
                key: summary.get(key)
                for key in (
                    "state",
                    "library_assets",
                    "analyzed_assets",
                    "assets_with_faces",
                    "faces",
                    "low_quality_faces",
                    "persons",
                    "confirmed_links",
                    "suggested_links",
                    "same_image_conflicts",
                    "degraded_jobs",
                )
            },
            "library": report,
            "idempotent_rerun": {
                "faces_before": faces_before,
                "faces_after": int(summary_again.get("faces", 0)),
                "persons_before": persons_before,
                "persons_after": int(summary_again.get("persons", 0)),
                "rerun_steps": again["run"]["steps"],
            },
            "calibration": calibration,
            "problems": problems,
        }
    finally:
        service.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="face-smoke-fixture", description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--device", choices=("auto", "cpu", "gpu"), default="auto")
    parser.add_argument("--json", action="store_true", help="emit one JSON document")
    parser.add_argument(
        "--keep", action="store_true", help="keep the temporary library after the run"
    )
    args = parser.parse_args(argv)
    report = run(Path(args.work_dir), device=str(args.device))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(f"status: {report['status']}")
        print(f"device: requested={report['device_requested']} qualified={report['qualified_devices']}")
        print(f"summary: {json.dumps(report['summary'], ensure_ascii=False, sort_keys=True)}")
        print(f"index: {json.dumps(report['index'], ensure_ascii=False, sort_keys=True)}")
        for person in report["library"]["persons"]:
            members = ", ".join(
                f"{member['file']}[{member['decision']}:{member['confidence']}]"
                for member in person["members"]
            )
            print(f"person {person['person_id']}: {members}")
        for face in report["library"]["faces"]:
            print(
                f"face {face['file']}#{face['ordinal']}: {face['quality']} "
                f"{face['quality_reason'] or ''} pixels={face['face_pixels']} "
                f"blur={face['blur_variance']} score={face['det_score']}"
            )
        print(f"calibration: {json.dumps(report['calibration'], ensure_ascii=False, sort_keys=True)}")
        for problem in report["problems"]:
            print(f"PROBLEM: {problem}")
    if not args.keep:
        shutil.rmtree(Path(args.work_dir), ignore_errors=True)
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
