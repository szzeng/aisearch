"""Small real-model profile comparison using package fixtures, never a private library."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance
from skimage import data

from photo_search.face import contract
from photo_search.face.engine import FaceEngineCache, analyze_image
from photo_search.face.index import cosine
from photo_search.face.processor import (
    _decode,
    build_payload,
    face_processor_spec,
    validate_payload,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--work-dir", type=Path, default=Path(".tmp/p4-person-detail-b-compatibility")
    )
    args = parser.parse_args()
    import insightface

    images = Path(insightface.__file__).parent / "data" / "images"
    with Image.open(images / "t1.jpg") as photo:
        other = photo.convert("RGB")
    astronaut = Image.fromarray(data.astronaut())
    fixtures = {
        "astronaut": astronaut.resize((4096, 4096)),
        "astronaut-bright": ImageEnhance.Brightness(astronaut).enhance(1.12).resize((3072, 3072)),
        "group-person": other.resize((2048, round(2048 * other.height / other.width))),
    }
    engine = FaceEngineCache().engine(args.data_root, device="cpu")
    vectors = {}
    cases = []
    args.work_dir.mkdir(parents=True, exist_ok=True)
    for name, image in fixtures.items():
        path = args.work_dir / (name + ".jpg")
        image.save(path, quality=95)
        revision = hashlib.sha256(path.read_bytes()).hexdigest()
        for mode in ("lightweight", "source_detail"):
            started = time.perf_counter()
            decoded = _decode(path, preserve_source_detail=mode == "source_detail")
            try:
                document = analyze_image(
                    engine, decoded.image, preserve_source_detail=mode == "source_detail"
                )
                payload = build_payload(
                    input_revision=revision,
                    location_id=None,
                    document=document,
                    decode_format="JPEG",
                    source_detail=mode == "source_detail",
                )
                validate_payload(payload)
            finally:
                decoded.image.close()
            assert len(document.faces) >= 1, (name, mode, len(document.faces))
            face = max(document.faces, key=lambda item: item.face_pixels)
            vectors[name, mode] = face.embedding
            cases.append(
                {
                    "fixture": name,
                    "profile": mode,
                    "face_count": len(document.faces),
                    "original_size": document.original_size,
                    "working_size": document.working_size,
                    "dimension": len(face.embedding),
                    "norm": float(np.linalg.norm(face.embedding)),
                    "quality": face.quality_state,
                    "scale": document.scale,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000),
                    "analyze_ms": document.elapsed_ms,
                    "decode_included": True,
                }
            )
    pairs = []
    for left in fixtures:
        for right in fixtures:
            same = (left != "group-person") == (right != "group-person")
            similarity = cosine(vectors[left, "lightweight"], vectors[right, "source_detail"])
            passed = (
                similarity >= contract.MERGE_THRESHOLD
                if same
                else similarity < contract.SUGGEST_THRESHOLD
            )
            pairs.append(
                {
                    "old": left,
                    "new": right,
                    "same_person": same,
                    "similarity": round(similarity, 6),
                    "passed": passed,
                }
            )
    result = {
        "status": "PASS" if all(pair["passed"] for pair in pairs) else "FAIL",
        "scope": "Two public package fixture identities; resized and brightness variants. No real-library accuracy claim.",
        "sources": ["scikit-image astronaut", "insightface package t1.jpg"],
        "model_fingerprint": contract.MODEL_FINGERPRINT,
        "space_id": contract.FACE_SPACE_ID,
        "old_spec": contract.FACE_SPEC_REVISION,
        "new_spec": contract.FACE_DETAIL_SPEC_REVISION,
        "old_config_hash": face_processor_spec().config_hash,
        "new_config_hash": face_processor_spec(source_detail=True).config_hash,
        "provider": engine.provider,
        "device": engine.device,
        "merge_threshold": contract.MERGE_THRESHOLD,
        "suggest_threshold": contract.SUGGEST_THRESHOLD,
        "cases": cases,
        "pairs": pairs,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"status": result["status"], "pairs": pairs}, ensure_ascii=False))
    assert result["status"] == "PASS"


if __name__ == "__main__":
    main()
