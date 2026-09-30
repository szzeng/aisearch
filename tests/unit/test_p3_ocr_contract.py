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
    base = build_payload(
        input_revision=overrides.pop("input_revision", SHA),
        location_id=overrides.pop("location_id", 7),
        document=overrides.pop("document", document(region(0, "发票 金额 1280"), region(1, "编号 20260914"))),
        decode_format=overrides.pop("decode_format", "JPEG"),
    )
    base.update(overrides)
    return base


# ------------------------------------------------------------------------- pinned facts
def test_ocr_config_is_pinned_to_the_shipped_file() -> None:
    from photo_search.ocr.weights import sha256_file
    path = Path(contract.__file__).with_name("rapidocr.yaml")
    assert sha256_file(path) == contract.CONFIG_HASH
    assert contract.ENGINE_VERSION == "3.9.2"
    assert contract.OCR_SOURCE == "RAPIDOCR_PPOCRV6_SMALL"
    assert {entry["role"] for entry in contract.PINNED_WEIGHTS} == {
        "detection", "recognition", "classification", "config"
    }


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
    spec = ocr_processor_spec()
    assert isinstance(spec, ProcessorSpec)
    envelope = spec.to_envelope()
    # Round-tripping proves the envelope is a legal processor contract, not a lookalike.
    assert ProcessorSpec.from_envelope(envelope).processor_id == contract.OCR_PROCESSOR_ID
    assert envelope["processor_kind"] == "structured_model"
    assert envelope["device_policy"] == "CPU_ALLOWED"
    assert envelope["supported_devices"] == ["gpu", "cpu"]
    assert envelope["model_id"] == contract.OCR_MODEL_ID
    assert envelope["model_revision"] == contract.OCR_MODEL_REVISION
    assert {item["name"] for item in envelope["weights"]} == {
        "detection", "classification", "recognition"
    }
    assert envelope["runtime_backend"] == "onnxruntime"
    assert envelope["cancellation_supported"] is True
    assert envelope["max_batch_size"] == 1
    assert envelope["config_hash"] == contract.CONFIG_HASH
    assert envelope["task_type"] == contract.OCR_TASK_TYPE
    assert set(envelope) == set(ProcessorSpec.from_envelope(envelope).to_envelope())
    # No vector-shaped field ever appears on an OCR processor.
    for key in ("space_id", "dimension", "metric", "normalizer"):
        assert key not in envelope


def test_the_processor_declares_gpu_and_cpu_without_vector_identity() -> None:
    spec = ocr_processor_spec()
    assert spec.supported_devices == ("gpu", "cpu")
    assert spec.envelope["device_policy"] == contract.OCR_DEVICE_POLICY
    assert ProcessorSpec.from_envelope(spec.envelope).processor_kind == "structured_model"
    for field in ("space_id", "dimension", "metric"):
        with pytest.raises(CatalogError):
            ProcessorSpec.from_envelope({**spec.envelope, field: "invalid"})


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


def test_low_confidence_regions_are_kept_and_counted() -> None:
    data = payload(
        document=document(region(0, "清晰", confidence=0.99), region(1, "模糊", confidence=0.4))
    )
    validate_payload(data)
    assert data["region_count"] == 2
    assert data["low_confidence_regions"] == 1
    assert data["regions"][1]["confidence"] == 0.4


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"processor_id": "ocr-text@obsolete"}, ARTIFACT_INVALID),
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
