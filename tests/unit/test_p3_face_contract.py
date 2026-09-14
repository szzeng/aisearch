"""P3-02: the frozen face processor contract, its payload and its identity rules."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from photo_search.catalog.errors import ARTIFACT_INVALID, FACE_RESULT_TOO_LARGE, CatalogError
from photo_search.face import contract
from photo_search.face.engine import FaceDocument, FaceRecord
from photo_search.face.processor import (
    PAYLOAD_KEYS,
    artifact_id_for,
    artifact_ref,
    build_payload,
    embedding_blob,
    event_id_for,
    face_id_for,
    face_processor_spec,
    generation_id_for,
    human_controlled_face_ids,
    validate_payload,
)
from photo_search.scheduler.contracts import ProcessorSpec

MANIFEST = json.loads(
    (
        Path(__file__).resolve().parents[2] / "tools/prepare_models/manifests/face-ocr.json"
    ).read_text(encoding="utf-8")
)
SHA = "c" * 64


def unit_vector(index: int) -> tuple[float, ...]:
    values = [0.0] * contract.FACE_DIMENSION
    values[index % contract.FACE_DIMENSION] = 1.0
    return tuple(values)


def face(index: int, *, quality: str = "OK", top: float | None = None) -> FaceRecord:
    top = 10.0 + index * 100.0 if top is None else top
    return FaceRecord(
        ordinal=index,
        bbox=(10.0, top, 110.0, top + 120.0),
        landmarks=tuple((20.0 + step * 8.0, top + 20.0 + step * 5.0) for step in range(5)),
        det_score=0.91,
        face_pixels=100.0,
        blur_variance=240.0,
        quality_state=quality,
        quality_reason=None if quality == "OK" else contract.QUALITY_BLURRY,
        embedding=unit_vector(index),
    )


def document(*faces: FaceRecord) -> FaceDocument:
    return FaceDocument(
        original_size=(1200, 900),
        working_size=(1200, 900),
        scale=1.0,
        faces=tuple(faces),
        elapsed_ms=25,
    )


def payload(**overrides: object) -> dict:
    base = build_payload(
        input_revision=overrides.pop("input_revision", SHA),
        location_id=overrides.pop("location_id", 7),
        document=overrides.pop("document", document(face(0), face(1))),
        decode_format=overrides.pop("decode_format", "JPEG"),
    )
    base.update(overrides)
    return base


# ------------------------------------------------------------------------- pinned facts
def test_the_pinned_table_mirrors_the_p0_04_manifest_exactly() -> None:
    manifest = {entry["path"]: entry for entry in MANIFEST["face"]["artifacts"].values()}
    assert len(contract.PINNED_WEIGHTS) == len(manifest) == 2
    for entry in contract.PINNED_WEIGHTS:
        recorded = manifest[str(entry["path"])]
        assert int(entry["bytes"]) == int(recorded["bytes"])
        assert str(entry["sha256"]) == str(recorded["sha256"])


def test_the_face_space_can_never_collide_with_the_image_space() -> None:
    # The picture space is ``chinese-clip-vit-b-16:<digest>``; the face space is pinned to the
    # recognition graph, so the two can never be compared or mixed by accident.
    assert contract.FACE_SPACE_ID.startswith("insightface-buffalo-l:")
    assert "chinese-clip" not in contract.FACE_SPACE_ID
    assert str(contract.PINNED_WEIGHTS[1]["sha256"])[:12] in contract.FACE_SPACE_ID
    assert contract.FACE_DIMENSION == 512
    assert contract.FACE_METRIC == "cosine"
    assert contract.FACE_NORMALIZATION == "L2"


def test_the_config_hash_covers_the_frozen_engine_configuration() -> None:
    expected = hashlib.sha256(
        json.dumps(
            contract.ENGINE_CONFIG, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()
    assert expected == contract.CONFIG_HASH


def test_the_processor_spec_is_a_valid_frozen_embedding_spec() -> None:
    spec = face_processor_spec()
    assert isinstance(spec, ProcessorSpec)
    assert spec.processor_kind == "embedding_model"
    assert spec.task_type == "image_face"
    # ADR-0001/ADR-0003 §2: the face processor is the CUDA-default model family.
    assert spec.device_policy == "CPU_ALLOWED"
    assert spec.supported_devices == ("gpu", "cpu")
    envelope = spec.to_envelope()
    assert envelope["space_id"] == contract.FACE_SPACE_ID
    assert envelope["dimension"] == contract.FACE_DIMENSION
    assert envelope["metric"] == "cosine"
    assert envelope["config_hash"] == contract.CONFIG_HASH
    assert [entry["sha256"] for entry in envelope["weights"]] == [
        str(entry["sha256"]) for entry in contract.PINNED_WEIGHTS
    ]
    # A round trip through the frozen protocol is loss-free.
    assert ProcessorSpec.from_envelope(envelope).to_envelope() == envelope


def test_the_thresholds_are_derived_from_the_recorded_calibration() -> None:
    measured = contract.CALIBRATION
    gap = float(measured["min_genuine"]) - float(measured["max_impostor"])
    assert gap == pytest.approx(float(measured["gap"]), abs=1e-4)
    # The derivation is part of the contract: 0.8 * gap for merging, 0.2 * gap for suggesting.
    assert contract.MERGE_THRESHOLD == pytest.approx(
        round(float(measured["max_impostor"]) + 0.8 * gap, 2), abs=0.005
    )
    assert contract.SUGGEST_THRESHOLD == pytest.approx(
        round(float(measured["max_impostor"]) + 0.2 * gap, 2), abs=0.005
    )
    # The architecture forbids a generic constant: the merge threshold is not 0.45, it is far
    # above the highest measured impostor similarity and below the weakest genuine pair.
    assert contract.MERGE_THRESHOLD != 0.45
    assert float(measured["max_impostor"]) < contract.SUGGEST_THRESHOLD < contract.MERGE_THRESHOLD
    assert contract.MERGE_THRESHOLD <= float(measured["min_genuine"])


# ------------------------------------------------------------------------- identity
def test_face_id_is_deterministic_and_geometry_sensitive() -> None:
    first = face_id_for(input_revision=SHA, ordinal=0, bbox=(10.0, 20.0, 110.0, 140.0))
    assert first == face_id_for(input_revision=SHA, ordinal=0, bbox=(10.0, 20.0, 110.0, 140.0))
    assert len(first) == 64
    assert first != face_id_for(input_revision=SHA, ordinal=1, bbox=(10.0, 20.0, 110.0, 140.0))
    assert first != face_id_for(input_revision="d" * 64, ordinal=0, bbox=(10.0, 20.0, 110.0, 140.0))
    assert first != face_id_for(input_revision=SHA, ordinal=0, bbox=(11.0, 20.0, 110.0, 140.0))


def test_the_candidate_generation_and_event_ids_are_face_scoped() -> None:
    generation = generation_id_for(contract.FACE_SPACE_ID, contract.FACE_PROCESSOR_ID)
    assert generation.startswith(contract.FACE_GENERATION_PREFIX)
    assert generation == generation_id_for(contract.FACE_SPACE_ID, contract.FACE_PROCESSOR_ID)
    assert generation != generation_id_for("chinese-clip-vit-b-16:0", contract.FACE_PROCESSOR_ID)
    event = event_id_for(face_id="a" * 64, generation_id=generation, revision=1)
    assert event == event_id_for(face_id="a" * 64, generation_id=generation, revision=1)
    assert event != event_id_for(face_id="b" * 64, generation_id=generation, revision=1)
    assert event != event_id_for(face_id="a" * 64, generation_id=generation, revision=2)


def test_an_embedding_blob_is_the_canonical_little_endian_float32_form() -> None:
    import struct

    vector = (0.5, -0.25) + tuple(0.0 for _ in range(contract.FACE_DIMENSION - 2))
    blob = embedding_blob(vector)
    assert len(blob) == 4 * contract.FACE_DIMENSION
    assert struct.unpack("<2f", blob[:8]) == (0.5, -0.25)


# -------------------------------------------------------------------------- payload
def test_the_payload_matches_the_frozen_shape_and_is_content_addressed() -> None:
    built = payload()
    assert frozenset(built) == PAYLOAD_KEYS
    assert built["face_count"] == 2
    assert built["low_quality_faces"] == 0
    validate_payload(built)
    ref = artifact_ref(built)
    assert ref["artifact_type"] == "structured"
    assert ref["space_id"] is None and ref["dimension"] is None
    assert ref["artifact_id"] == artifact_id_for(SHA)
    assert ref["artifact_revision"] == contract.FACE_ARTIFACT_REVISION
    # The same document always produces the same bytes and checksum.
    assert artifact_ref(payload())["checksum_sha256"] == ref["checksum_sha256"]
    assert [item["face_id"] for item in built["faces"]] == [
        face_id_for(input_revision=SHA, ordinal=index, bbox=item["bbox"])
        for index, item in enumerate(built["faces"])
    ]


def test_a_document_without_faces_is_a_success() -> None:
    empty = payload(document=document())
    assert empty["face_count"] == 0
    validate_payload(empty)


@pytest.mark.parametrize(
    "damage",
    [
        {"processor_id": "other@1"},
        {"source": "SOMETHING_ELSE"},
        {"space_id": "chinese-clip-vit-b-16:0"},
        {"dimension": 128},
        {"metric": "l2"},
        {"normalization": "none"},
        {"face_count": 3},
        {"low_quality_faces": 1},
        {"input_revision": "not-a-sha"},
        {"extra": 1},
    ],
)
def test_the_validator_refuses_a_damaged_document(damage: dict) -> None:
    with pytest.raises(CatalogError) as error:
        validate_payload(payload(**damage))
    assert error.value.code == ARTIFACT_INVALID


def test_the_validator_refuses_a_foreign_face_id() -> None:
    built = payload()
    built["faces"][0]["face_id"] = "a" * 64
    with pytest.raises(CatalogError) as error:
        validate_payload(built)
    assert error.value.code == ARTIFACT_INVALID


def test_the_validator_refuses_a_non_normalised_embedding() -> None:
    built = payload()
    built["faces"][1]["embedding"] = [0.5] * contract.FACE_DIMENSION
    with pytest.raises(CatalogError) as error:
        validate_payload(built)
    assert error.value.code == ARTIFACT_INVALID


def test_the_validator_refuses_a_quality_reason_without_a_low_verdict() -> None:
    built = payload()
    built["faces"][0]["quality_reason"] = contract.QUALITY_BLURRY
    with pytest.raises(CatalogError) as error:
        validate_payload(built)
    assert error.value.code == ARTIFACT_INVALID
    assert "only a low-quality face" in str(error.value.message)


def test_the_validator_requires_ordinals_in_order() -> None:
    built = payload()
    built["faces"][1]["ordinal"] = 5
    with pytest.raises(CatalogError) as error:
        validate_payload(built)
    assert error.value.code == ARTIFACT_INVALID


def test_too_many_faces_are_a_bounded_output_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(contract, "MAX_FACES", 1)
    with pytest.raises(CatalogError) as error:
        validate_payload(payload())
    assert error.value.code == FACE_RESULT_TOO_LARGE


# ------------------------------------------------------------------- human boundary
def test_the_human_control_read_is_empty_without_the_p3_03_tables() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE TABLE faces(face_id TEXT PRIMARY KEY, asset_id INTEGER)")
        connection.execute("INSERT INTO faces(face_id, asset_id) VALUES ('a', 1)")
        assert human_controlled_face_ids(connection, asset_id=1) == set()
    finally:
        connection.close()


def test_the_human_control_read_defers_to_the_p3_03_tables_when_they_exist() -> None:
    """The reserved read order: the moment the human tables exist, their rows win."""
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE TABLE faces(face_id TEXT PRIMARY KEY, asset_id INTEGER)")
        connection.execute(
            "CREATE TABLE face_assignment_controls(face_id TEXT PRIMARY KEY, mode TEXT)"
        )
        connection.execute(
            "CREATE TABLE face_exclusions(face_id TEXT, person_id INTEGER, active INTEGER)"
        )
        connection.executemany(
            "INSERT INTO faces(face_id, asset_id) VALUES (?, 1)",
            [("locked",), ("excluded",), ("free",)],
        )
        connection.execute(
            "INSERT INTO face_assignment_controls(face_id, mode) VALUES ('locked', "
            "'MANUAL_UNASSIGNED')"
        )
        connection.execute(
            "INSERT INTO face_exclusions(face_id, person_id, active) VALUES ('excluded', 3, 1)"
        )
        connection.execute(
            "INSERT INTO face_exclusions(face_id, person_id, active) VALUES ('free', 4, 0)"
        )
        assert human_controlled_face_ids(connection, asset_id=1) == {"locked", "excluded"}
    finally:
        connection.close()
