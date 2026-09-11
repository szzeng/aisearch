"""P0-04 face/OCR manifest and probe-record contract, independent of the weights."""

from __future__ import annotations

import json
from pathlib import Path

MANIFEST_DIR = Path(__file__).resolve().parents[2] / "tools/prepare_models/manifests"


def load(name: str) -> dict:
    return json.loads((MANIFEST_DIR / name).read_text(encoding="utf-8"))


def test_face_ocr_manifest_pins_weights_sources_and_licenses() -> None:
    manifest = load("face-ocr.json")
    assert manifest["schema_version"] == 1
    assert manifest["runtime"]["device"] == "CPU"

    ocr = manifest["ocr"]
    assert ocr["engine"] == "rapidocr-onnxruntime"
    assert set(ocr["artifacts"]) == {"detection", "classification", "recognition", "config"}
    assert ocr["license"]["package"]["spdx"] == "Apache-2.0"

    face = manifest["face"]
    assert face["pack"] == "buffalo_l"
    assert set(face["artifacts"]) == {"detection", "recognition"}
    assert face["embedding"] == {
        "dimension": 512,
        "metric": "cosine",
        "normalization": "L2",
        "source": "onnx graph output shape of the recognition model",
    }
    assert face["license"]["weights"]["allow_redistribution"] is False
    assert face["pack_release"]["archive_bytes"] > 0
    assert len(face["pack_release"]["archive_sha256"]) == 64

    artifacts = [*ocr["artifacts"].values(), *face["artifacts"].values()]
    for artifact in artifacts:
        assert artifact["bytes"] > 0
        assert len(artifact["sha256"]) == 64
        assert artifact["source_url"].startswith("http")


def test_face_ocr_validation_record_matches_manifest_contract() -> None:
    manifest = load("face-ocr.json")
    validation = load("face-ocr-validation.json")
    dimension = manifest["face"]["embedding"]["dimension"]

    assert validation["status"] == "pass"
    assert validation["providers_used"] == ["CPUExecutionProvider"]
    assert "CPUExecutionProvider" in validation["providers_available"]
    assert validation["face"]["recognition_smoke"]["output_shape"][-1] == dimension
    assert validation["face_positive_case"] is True

    detected = [face for record in validation["face"]["records"] for face in record["faces"]]
    assert detected
    for face in detected:
        assert face["embedding_shape"][-1] == dimension
        assert len(face["kps"]) == 5
        assert face["det_score"] > 0

    cjk = next(record for record in validation["ocr"]["records"] if record["fixture"] == "cjk")
    assert cjk["box_count"] > 0
    assert "20260911" in cjk["text"]
    blank = next(record for record in validation["ocr"]["records"] if record["fixture"] == "blank")
    assert blank["box_count"] == 0
