"""Source-coordinate fix and preprocessing generation isolation, without model weights."""

from __future__ import annotations

import hashlib
from unittest.mock import patch

import pytest
from PIL import Image

from photo_search.face import contract
from photo_search.face.engine import DetectedFace, analyze_image
from photo_search.face.processor import (
    artifact_ref,
    build_payload,
    generation_id_for,
    validate_payload,
)


class Engine:
    def detect(self, blob):
        self.detect_calls = getattr(self, "detect_calls", 0) + 1
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


def test_source_detail_mode_maps_working_landmarks_and_uses_source_crop():
    face = DetectedFace(
        0,
        (200.0, 100.0, 400.0, 300.0),
        ((230.0, 160.0), (370.0, 160.0), (300.0, 210.0), (250.0, 260.0), (350.0, 260.0)),
        0.9,
    )
    source = Image.new("RGB", (4000, 2000), "white")
    seen = {}

    def align(image, *, bbox, landmarks):
        seen["image_size"] = image.size
        seen["bbox"] = bbox
        seen["landmarks"] = landmarks
        return Image.new("RGB", (112, 112), "black")

    engine = Engine()
    with (
        patch("photo_search.face.engine.decode_detections", return_value=[face]),
        patch("photo_search.face.engine.warp_aligned_source_crop", side_effect=align),
        patch("photo_search.face.engine.laplacian_variance", return_value=100.0),
    ):
        document = analyze_image(engine, source, preserve_source_detail=True)
        assert document.scale == 1.0 and document.working_size == source.size

    assert seen["image_size"] == (4000, 2000)
    assert seen["bbox"] == (625.0, 312.5, 1250.0, 937.5)
    assert seen["landmarks"][0] == (718.75, 500.0)
    assert document.faces[0].face_pixels == 625.0
    assert engine.detect_calls == 1


def test_only_source_detail_decode_disables_jpeg_draft_and_sets_pixel_bound():
    from photo_search.face.processor import _decode

    with patch("photo_search.media.decode.load_normalized", return_value=object()) as decode:
        _decode("synthetic.jpg")
        _decode("synthetic.jpg", preserve_source_detail=True)
    assert decode.call_args_list[0].kwargs == {
        "long_edge": contract.WORKING_LONG_EDGE,
        "jpeg_draft": True,
        "max_pixels": None,
    }
    assert decode.call_args_list[1].kwargs == {
        "long_edge": contract.WORKING_LONG_EDGE,
        "jpeg_draft": False,
        "max_pixels": contract.MAX_DECODE_PIXELS,
    }


def test_source_detail_has_separate_payload_face_and_generation_identity():
    face = DetectedFace(
        0,
        (200.0, 100.0, 400.0, 300.0),
        ((230.0, 160.0), (370.0, 160.0), (300.0, 210.0), (250.0, 260.0), (350.0, 260.0)),
        0.9,
    )
    with patch("photo_search.face.engine.decode_detections", return_value=[face]):
        regular = analyze_image(Engine(), Image.new("RGB", (640, 480)))
        detailed = analyze_image(
            Engine(), Image.new("RGB", (640, 480)), preserve_source_detail=True
        )
    regular_payload = build_payload(
        input_revision="a" * 64, location_id=1, document=regular, decode_format="JPEG"
    )
    detail_payload = build_payload(
        input_revision="a" * 64,
        location_id=1,
        document=detailed,
        decode_format="JPEG",
        source_detail=True,
    )
    validate_payload(regular_payload)
    validate_payload(detail_payload)
    assert "spec_revision" not in regular_payload
    assert "artifact_revision" not in regular_payload
    assert regular_payload["faces"][0]["face_id"] != detail_payload["faces"][0]["face_id"]
    assert regular_payload["engine_config"] != detail_payload["engine_config"]
    assert artifact_ref(regular_payload)["artifact_id"] != artifact_ref(detail_payload)["artifact_id"]
    assert artifact_ref(regular_payload)["artifact_revision"] == contract.FACE_ARTIFACT_REVISION
    assert artifact_ref(detail_payload)["artifact_revision"] == contract.FACE_DETAIL_ARTIFACT_REVISION
    assert generation_id_for(contract.FACE_SPACE_ID, contract.FACE_PROCESSOR_ID) != generation_id_for(
        contract.FACE_SPACE_ID,
        contract.FACE_PROCESSOR_ID,
        spec_revision=contract.FACE_DETAIL_SPEC_REVISION,
    )


def test_detail_artifact_frame_requires_matching_detail_profile():
    import json
    from pathlib import Path

    from photo_search.catalog.errors import FACE_STATE_CONFLICT, CatalogError
    from photo_search.face.contract import FACE_PROCESSOR_ID
    from photo_search.media.face_crop import FaceBox, recorded_face_frame

    face_id = "f" * 64
    box = FaceBox(10.0, 20.0, 50.0, 60.0)
    artifact = {
        "lifecycle_state": "PUBLISHED",
        "artifact_type": "structured",
        "artifact_revision": contract.FACE_DETAIL_ARTIFACT_REVISION,
        "processor_id": FACE_PROCESSOR_ID,
        "input_revision": "a" * 64,
        "byte_size": 100,
    }
    payload = {
        "processor_id": FACE_PROCESSOR_ID,
        "input_revision": "a" * 64,
        "artifact_revision": contract.FACE_DETAIL_ARTIFACT_REVISION,
        "spec_revision": contract.FACE_DETAIL_SPEC_REVISION,
        "original_size": [4000, 2000],
        "faces": [{"face_id": face_id, "bbox": box.as_list()}],
    }
    face = {
        "face_id": face_id,
        "spec_revision": contract.FACE_DETAIL_SPEC_REVISION,
        "artifact_id": "face-document:detail",
        "input_revision": "a" * 64,
    }
    with patch(
        "photo_search.media.face_crop.ArtifactStore.read",
        return_value=json.dumps(payload).encode(),
    ):
        assert recorded_face_frame(Path("."), face, artifact, box=box, sha256="a" * 64) == (
            4000,
            2000,
        )
        malformed = {**payload, "artifact_revision": contract.FACE_ARTIFACT_REVISION}
        with patch(
            "photo_search.media.face_crop.ArtifactStore.read",
            return_value=json.dumps(malformed).encode(),
        ), pytest.raises(CatalogError) as error:
            recorded_face_frame(Path("."), face, artifact, box=box, sha256="a" * 64)
    assert error.value.code == FACE_STATE_CONFLICT


def test_source_crop_alignment_keeps_local_high_frequency_detail():
    import numpy as np

    from photo_search.face.engine import warp_aligned_source_crop

    source = Image.new("RGB", (4000, 2667), "gray")
    # The face is 600 px in the source and 192 px in the bounded working frame.
    for x in range(1250, 1850, 3):
        for y in range(780, 1380):
            source.putpixel((x, y), (255, 255, 255))
    landmarks = ((1370, 930), (1730, 930), (1550, 1080), (1415, 1245), (1685, 1245))
    bbox = (1250, 780, 1850, 1380)
    aligned = warp_aligned_source_crop(source, bbox=bbox, landmarks=landmarks)
    assert aligned.shape == (112, 112, 3)
    assert np.asarray(aligned).std() > 1.0
