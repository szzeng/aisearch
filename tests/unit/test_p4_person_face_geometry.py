"""Source-coordinate fix and preprocessing generation isolation, without model weights."""

from __future__ import annotations

import hashlib
from unittest.mock import patch

import pytest
from PIL import Image

from photo_search.face import contract
from photo_search.face.engine import DetectedFace, analyze_image
from photo_search.face.processor import build_payload, generation_id_for, validate_payload


class Engine:
    def detect(self, blob):
        return []

    def recognize(self, aligned):
        return (1.0,) + (0.0,) * 511


@pytest.mark.parametrize("size", [(640, 481), (4000, 2501), (2501, 4000)])
@pytest.mark.parametrize("with_face", [True, False])
def test_geometry_uses_each_actual_resize_axis_and_legal_forward_scale(size, with_face):
    face = DetectedFace(
        0,
        (200.0, 200.0, 300.0, 300.0),
        ((220.0, 230.0), (280.0, 230.0), (250.0, 250.0), (225.0, 280.0), (275.0, 280.0)),
        0.9,
    )
    with patch(
        "photo_search.face.engine.decode_detections", return_value=[face] if with_face else []
    ):
        document = analyze_image(Engine(), Image.new("RGB", size))
    payload = build_payload(
        input_revision="a" * 64, location_id=None, decode_format="PNG", document=document
    )
    validate_payload(payload)
    assert 0 < document.scale <= 1
    assert document.scale == pytest.approx(min(1.0, 1280 / max(size)))
    if with_face:
        sx, sy = size[0] / document.working_size[0], size[1] / document.working_size[1]
        assert document.faces[0].bbox == tuple(
            round(v * (sx if i % 2 == 0 else sy), 2) for i, v in enumerate(face.bbox)
        )
        assert document.faces[0].landmarks == tuple(
            (round(x * sx, 2), round(y * sy, 2)) for x, y in face.landmarks
        )
    else:
        assert payload["face_count"] == 0


def test_v1_generation_identity_is_preserved_and_v2_is_separate():
    legacy = "face-buffalo-l-scrfd-arcface-v1"
    identity = f"{contract.FACE_SPACE_ID}\0{contract.FACE_PROCESSOR_ID}"
    old = contract.FACE_GENERATION_PREFIX + hashlib.sha256(identity.encode()).hexdigest()[:24]
    assert (
        generation_id_for(contract.FACE_SPACE_ID, contract.FACE_PROCESSOR_ID, spec_revision=legacy)
        == old
    )
    current = generation_id_for(contract.FACE_SPACE_ID, contract.FACE_PROCESSOR_ID)
    assert current != old
    assert contract.FACE_IMAGE_PREPROCESS_VERSION.endswith("-v2")
    assert (
        contract.ENGINE_CONFIG["image_preprocess_version"] == contract.FACE_IMAGE_PREPROCESS_VERSION
    )
