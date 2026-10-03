"""Synthetic geometry/validation probe; no weights, sessions, catalog or photos."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from photo_search.catalog.errors import CatalogError
from photo_search.face.engine import DetectedFace, analyze_image
from photo_search.face.processor import build_payload, validate_payload


class SyntheticEngine:
    def detect(self, blob):
        return []

    def recognize(self, aligned):
        return (1.0,) + (0.0,) * 511


@pytest.mark.parametrize("size", [(1280, 800), (2560, 1600), (4000, 2500)])
def test_source_geometry_and_payload(size):
    face = DetectedFace(
        0,
        (200.0, 200.0, 300.0, 300.0),
        ((220.0, 230.0), (280.0, 230.0), (250.0, 250.0), (225.0, 280.0), (275.0, 280.0)),
        0.9,
    )
    with patch("photo_search.face.engine.decode_detections", return_value=[face]):
        document = analyze_image(SyntheticEngine(), Image.new("RGB", size))
    payload = build_payload(
        input_revision="a" * 64, location_id=None, decode_format="PNG", document=document
    )
    error = None
    try:
        validate_payload(payload)
    except CatalogError as exc:
        error = {"code": exc.code, "message": str(exc)}
    inverse = size[0] / document.working_size[0]
    expected_bbox = [round(v * inverse, 2) for v in face.bbox]
    sample = {
        "input_size": size,
        "working_size": document.working_size,
        "actual_bbox": document.faces[0].bbox,
        "expected_source_bbox": expected_bbox,
        "actual_scale": document.scale,
        "expected_forward_scale": 1 / inverse,
        "validation_error": error,
        "synthetic_detector": True,
        "model_loaded": False,
    }
    out = Path("test-results/p4-person-ux-geometry")
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{size[0]}.json").write_text(
        json.dumps(sample, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    assert error is None and list(document.faces[0].bbox) == expected_bbox, sample
