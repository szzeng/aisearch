"""P3-01: the frozen OCR processor contract, its payload and its artifact identity."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from photo_search.catalog.errors import (
    ARTIFACT_INVALID,
    OCR_RESULT_TOO_LARGE,
    CatalogError,
)
from photo_search.ocr import contract
from photo_search.ocr.engine import OcrDocument, OcrRegion
from photo_search.ocr.processor import (
    PAYLOAD_KEYS,
    artifact_id_for,
    artifact_ref,
    build_payload,
    ocr_document_id_for,
    ocr_processor_spec,
    validate_payload,
)
from photo_search.scheduler.contracts import ProcessorSpec
from photo_search.search.tokens import TOKENIZER_VERSION, index_text

MANIFEST = json.loads(
    (
        Path(__file__).resolve().parents[2] / "tools/prepare_models/manifests/face-ocr.json"
    ).read_text(encoding="utf-8")
)
SHA = "c" * 64


def region(index: int, text: str, *, confidence: float = 0.9, top: float = 10.0) -> OcrRegion:
    return OcrRegion(
        index=index,
        polygon=((1.0, top), (100.0, top), (100.0, top + 20.0), (1.0, top + 20.0)),
        box=(1.0, top, 100.0, top + 20.0),
        text=text,
        confidence=confidence,
    )


def document(*regions: OcrRegion) -> OcrDocument:
    ordered = tuple(
        OcrRegion(
            index=index,
            polygon=item.polygon,
            box=item.box,
            text=item.text,
            confidence=item.confidence,
        )
        for index, item in enumerate(regions)
    )
    return OcrDocument(
        original_size=(1200, 800),
        working_size=(1200, 800),
        scale=1.0,
        slice_count=1,
        regions=ordered,
        elapsed_ms=12,
    )


def payload(**overrides: object) -> dict:
    processor_id = overrides.pop("processor_id", contract.OCR_PROCESSOR_ID)
    build_processor_id = (
        processor_id
        if processor_id in (
            contract.OCR_LEGACY_PROCESSOR_ID,
            contract.OCR_CPU_PROCESSOR_ID,
            contract.OCR_GPU_PROCESSOR_ID,
        )
        else contract.OCR_PROCESSOR_ID
    )
    base = build_payload(
        input_revision=overrides.pop("input_revision", SHA),
        location_id=overrides.pop("location_id", 7),
        document=overrides.pop("document", document(region(0, "发票 金额 1280"), region(1, "编号 20260914"))),
        decode_format=overrides.pop("decode_format", "JPEG"),
        processor_id=build_processor_id,
    )
    base["processor_id"] = processor_id
    base.update(overrides)
    return base


# ------------------------------------------------------------------------- pinned facts
def test_the_pinned_table_mirrors_the_p0_04_manifest_exactly() -> None:
    manifest = MANIFEST["ocr"]
    expected = {
        role: (str(entry["path"]), int(entry["bytes"]), str(entry["sha256"]))
        for role, entry in manifest["artifacts"].items()
    }
    mirrors = {
        str(entry["role"]): (str(entry["path"]), int(entry["bytes"]), str(entry["sha256"]))
        for entry in contract.PINNED_WEIGHTS
    }
    assert mirrors == expected
    assert contract.CONFIG_HASH == manifest["artifacts"]["config"]["sha256"]
    assert contract.ENGINE_VERSION == manifest["package_version"]
    assert contract.OCR_ENGINE == manifest["engine"]
    # The recorded preprocessing is the frozen engine configuration this build claims.
    assert contract.ENGINE_CONFIG["det_limit_side_len"] == manifest["preprocessing"]["detection"][
        "limit_side_len"
    ]
    assert contract.ENGINE_CONFIG["det_box_thresh"] == manifest["preprocessing"]["detection"][
        "box_thresh"
    ]
    assert contract.ENGINE_CONFIG["rec_image_shape"] == manifest["preprocessing"]["recognition"][
        "image_shape"
    ]
    assert contract.MODEL_FINGERPRINT == contract.MODEL_FINGERPRINT.lower()
    assert len(contract.MODEL_FINGERPRINT) == 64


def test_the_fingerprint_is_derived_from_the_pinned_triples() -> None:
    import hashlib

    triples = [
        {"role": str(entry["role"]), "path": str(entry["path"]), "sha256": str(entry["sha256"])}
        for entry in contract.PINNED_WEIGHTS
    ]
    canonical = json.dumps(triples, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert contract.MODEL_FINGERPRINT == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    # A changed weight set cannot keep the old identity.
    changed = [dict(item) for item in triples]
    changed[0]["sha256"] = "0" * 64
    other = json.dumps(changed, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert hashlib.sha256(other.encode("utf-8")).hexdigest() != contract.MODEL_FINGERPRINT


# ------------------------------------------------------------------------------- spec
def test_the_processor_spec_is_the_frozen_structured_model_shell() -> None:
    spec = ocr_processor_spec("gpu")
    assert isinstance(spec, ProcessorSpec)
    envelope = spec.to_envelope()
    # Round-tripping proves the envelope is a legal processor contract, not a lookalike.
    assert ProcessorSpec.from_envelope(envelope).processor_id == contract.OCR_PROCESSOR_ID
    assert envelope["processor_kind"] == "structured_model"
    assert envelope["device_policy"] == "GPU_REQUIRED"
    assert envelope["supported_devices"] == ["gpu"]
    assert envelope["model_id"] == contract.OCR_MODEL_ID
    assert envelope["model_revision"] == contract.OCR_MODEL_REVISION
    assert {item["name"] for item in envelope["weights"]} == {
        "detection", "classification", "recognition"
    }
    assert envelope["runtime_backend"] == "onnxruntime"
    assert envelope["cancellation_supported"] is True
    assert envelope["max_batch_size"] == 1
    assert envelope["config_hash"] == contract.OCR_GPU_CONFIG_HASH
    assert envelope["task_type"] == contract.OCR_TASK_TYPE
    assert set(envelope) == set(ProcessorSpec.from_envelope(envelope).to_envelope())
    # No vector-shaped field ever appears on an OCR processor.
    for key in ("space_id", "dimension", "metric", "normalizer"):
        assert key not in envelope


def test_the_processor_declares_gpu_and_cpu_without_vector_identity() -> None:
    for device, processor_id, policy, supported in (
        ("gpu", contract.OCR_GPU_PROCESSOR_ID, "GPU_REQUIRED", ["gpu"]),
        ("cpu", contract.OCR_CPU_PROCESSOR_ID, "CPU_PREFERRED", ["cpu"]),
    ):
        spec = ocr_processor_spec(device)
        assert spec.processor_id == processor_id
        assert spec.supported_devices == tuple(supported)
        assert spec.envelope["device_policy"] == policy
        assert ProcessorSpec.from_envelope(spec.envelope).processor_kind == "structured_model"
        for field in ("space_id", "dimension", "metric"):
            with pytest.raises(CatalogError):
                ProcessorSpec.from_envelope({**spec.envelope, field: "invalid"})


def test_v2_execution_hashes_are_versioned_and_device_specific() -> None:
    cpu = contract.execution_config("cpu")
    gpu = contract.execution_config("gpu")
    assert cpu["version"] == gpu["version"] == "ocr-execution-config.v2"
    assert cpu["provider"] == "CPUExecutionProvider"
    assert gpu["provider"] == "CUDAExecutionProvider"
    assert cpu["precision"] == gpu["precision"] == "FP32"
    assert contract.OCR_CPU_CONFIG_HASH == contract.execution_config_hash("cpu")
    assert contract.OCR_GPU_CONFIG_HASH == contract.execution_config_hash("gpu")
    assert contract.OCR_CPU_CONFIG_HASH != contract.OCR_GPU_CONFIG_HASH
    assert contract.OCR_CPU_CONFIG_HASH == "b34753ca9d2bd6f2c824d792b9af8debe1ecd788258e7bafbb2c56b76fd478f3"
    assert contract.OCR_GPU_CONFIG_HASH == "6936ffae2b03479335a09ed69bb18d5d821ca4d2a1029c27d56f007c66aef5c6"


# ---------------------------------------------------------------------------- payload
def test_a_valid_document_payload_round_trips_and_is_deterministic() -> None:
    first = payload()
    second = payload(document=document(region(0, "发票 金额 1280"), region(1, "编号 20260914")))
    validate_payload(first)
    assert first == second
    assert set(first) == PAYLOAD_KEYS
    assert first["original_text"] == "发票 金额 1280\n编号 20260914"
    assert first["region_count"] == 2
    assert first["low_confidence_regions"] == 0
    assert first["engine_config"]["text_score"] == contract.MIN_TEXT_SCORE
    assert first["original_size"] == [1200, 800]
    # The search copy the SQLite side will hold is derived from the original text alone.
    assert index_text(str(first["original_text"])) == "发 票 金 额 1280 编 号 20260914"
    assert TOKENIZER_VERSION == 1


def test_legacy_v1_artifacts_remain_readable_with_their_original_identity() -> None:
    legacy = payload(processor_id=contract.OCR_LEGACY_PROCESSOR_ID)
    validate_payload(legacy)
    ref = artifact_ref(legacy)
    assert ref["artifact_id"] == f"ocr-document:{contract.OCR_LEGACY_PROCESSOR_ID}:{SHA}"
    assert legacy["config_hash"] == contract.CONFIG_HASH
    assert legacy["processor_id"] == contract.OCR_LEGACY_PROCESSOR_ID


def test_low_confidence_regions_are_kept_and_counted() -> None:
    data = payload(
        document=document(region(0, "清晰", confidence=0.99), region(1, "模糊", confidence=0.4))
    )
    validate_payload(data)
    assert data["region_count"] == 2
    assert data["low_confidence_regions"] == 1
    assert data["regions"][1]["confidence"] == 0.4


def test_low_confidence_boundary_preserves_raw_scores() -> None:
    data = payload(
        document=document(
            region(0, "边界", confidence=0.6),
            region(1, "高于边界", confidence=0.600001),
        )
    )
    validate_payload(data)
    assert data["regions"][0]["confidence"] == 0.6
    assert data["regions"][1]["confidence"] == 0.600001
    assert data["low_confidence_regions"] == 1


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"processor_id": "ocr-text@2"}, ARTIFACT_INVALID),
        ({"source": "OTHER"}, ARTIFACT_INVALID),
        ({"model_fingerprint": "d" * 64}, ARTIFACT_INVALID),
        ({"config_hash": "d" * 64}, ARTIFACT_INVALID),
        ({"decode_format": "jpeg"}, ARTIFACT_INVALID),
        ({"input_revision": "short"}, ARTIFACT_INVALID),
        ({"region_count": 3}, ARTIFACT_INVALID),
        ({"low_confidence_regions": 1}, ARTIFACT_INVALID),
        ({"original_text": "something else"}, ARTIFACT_INVALID),
        ({"slice_count": contract.MAX_SLICES + 1}, ARTIFACT_INVALID),
        ({"scale": 1.5}, ARTIFACT_INVALID),
    ],
)
def test_a_malformed_payload_is_refused(overrides: dict, code: str) -> None:
    with pytest.raises(CatalogError) as error:
        validate_payload(payload(**overrides))
    assert error.value.code == code


def test_a_payload_with_an_unknown_or_missing_key_is_refused() -> None:
    with pytest.raises(CatalogError):
        validate_payload({**payload(), "extra": 1})
    broken = payload()
    broken.pop("original_text")
    with pytest.raises(CatalogError):
        validate_payload(broken)


def test_region_level_violations_are_refused() -> None:
    base = payload()
    for mutate in (
        lambda item: item["regions"][1].update({"index": 5}),
        lambda item: item["regions"][0].update({"text": ""}),
        lambda item: item["regions"][0].update({"confidence": 1.5}),
        lambda item: item["regions"][0].update({"box": [10.0, 10.0, 5.0, 20.0]}),
        lambda item: item["regions"][0].update({"polygon": [[1.0, 1.0]]}),
        lambda item: item["regions"][0].update({"polygon": [[1.0, 1.0], [2.0, 2.0], [3.0]]}),
        lambda item: item["regions"].pop(),
    ):
        broken = json.loads(json.dumps(base))
        mutate(broken)
        with pytest.raises(CatalogError):
            validate_payload(broken)


def test_too_many_regions_are_refused_rather_than_truncated() -> None:
    base = payload()
    base["regions"] = [
        {**base["regions"][0], "index": index} for index in range(contract.MAX_REGIONS + 1)
    ]
    base["region_count"] = contract.MAX_REGIONS + 1
    base["original_text"] = "\n".join(region["text"] for region in base["regions"])
    with pytest.raises(CatalogError) as error:
        validate_payload(base)
    assert error.value.code == OCR_RESULT_TOO_LARGE


# ---------------------------------------------------------------------------- artifact
def test_the_artifact_is_a_structured_document_without_a_vector() -> None:
    reference = artifact_ref(payload())
    assert reference["artifact_type"] == "structured"
    assert reference["artifact_revision"] == contract.OCR_ARTIFACT_REVISION
    assert reference["media_type"] == "application/json"
    assert reference["space_id"] is None and reference["dimension"] is None
    assert reference["storage_uri"] is None
    assert reference["artifact_id"] == artifact_id_for(SHA)
    cpu_reference = artifact_ref(
        build_payload(
            input_revision=SHA,
            location_id=7,
            document=document(region(0, "发票 金额 1280")),
            decode_format="JPEG",
            processor_id=contract.OCR_CPU_PROCESSOR_ID,
        )
    )
    assert cpu_reference["artifact_id"] != reference["artifact_id"]
    assert cpu_reference["payload"]["config_hash"] == contract.OCR_CPU_CONFIG_HASH
    assert "embedding" not in json.dumps(reference)
    # The checksum is the canonical JSON of the payload, byte for byte.
    import hashlib

    from photo_search.scheduler.contracts import canonical_json

    body = canonical_json(reference["payload"]).encode("utf-8")
    assert reference["byte_size"] == len(body)
    assert reference["checksum_sha256"] == hashlib.sha256(body).hexdigest()


def test_the_artifact_id_and_document_id_are_deterministic() -> None:
    assert artifact_id_for(SHA) == artifact_id_for(SHA)
    assert artifact_id_for(SHA) != artifact_id_for("d" * 64)
    assert ocr_document_id_for(SHA, 1) != ocr_document_id_for(SHA, 2)
    assert ocr_document_id_for(SHA, 1).startswith("ocr:")


def test_no_payload_field_can_carry_a_local_path() -> None:
    text = json.dumps(payload(), ensure_ascii=False)
    assert ":" not in str(payload()["decode_format"])
    assert "\\\\" not in text
    assert payload()["location_id"] == 7


def test_a_payload_without_regions_is_a_legal_empty_document() -> None:
    data = payload(document=document())
    validate_payload(data)
    assert data["region_count"] == 0
    assert data["original_text"] == ""
    assert data["low_confidence_regions"] == 0
    assert index_text("") == ""
