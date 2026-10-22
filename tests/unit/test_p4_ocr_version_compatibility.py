"""Historical OCR bytes stay readable while only v4 may execute new work."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from photo_search.catalog.errors import ARTIFACT_INVALID, CatalogError
from photo_search.ocr import contract
from photo_search.ocr.engine import OcrDocument, OcrRegion
from photo_search.ocr.processor import (
    artifact_ref,
    build_payload,
    extract_for_asset,
    ocr_processor_spec,
    validate_payload,
)
from photo_search.scheduler.contracts import canonical_json

HISTORY = json.loads(
    (Path(__file__).parents[1] / "fixtures/ocr/historical-v1-v2.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("sample", HISTORY["artifacts"], ids=lambda sample: sample["processor_id"])
def test_historical_payload_retains_exact_canonical_bytes_and_profile(sample: dict) -> None:
    original = deepcopy(sample["payload"])
    validate_payload(original)
    document = OcrDocument(
        original_size=tuple(original["original_size"]),
        working_size=tuple(original["working_size"]),
        scale=original["scale"],
        slice_count=original["slice_count"],
        regions=tuple(
            OcrRegion(
                index=row["index"], polygon=tuple(tuple(p) for p in row["polygon"]),
                box=tuple(row["box"]), text=row["text"], confidence=row["confidence"],
            )
            for row in original["regions"]
        ),
        elapsed_ms=999,
    )
    rebuilt = build_payload(
        input_revision=original["input_revision"], location_id=original["location_id"],
        document=document, decode_format=original["decode_format"],
        processor_id=original["processor_id"],
    )
    assert rebuilt == original == sample["payload"]
    body = canonical_json(rebuilt).encode("utf-8")
    assert hashlib.sha256(body).hexdigest() == sample["canonical_payload_sha256"]
    assert len(body) == sample["canonical_payload_bytes"]
    assert artifact_ref(rebuilt)["artifact_id"] == sample["artifact_id"]


@pytest.mark.parametrize("device", ("cpu", "gpu"))
def test_v2_full_configuration_and_hash_are_frozen(device: str) -> None:
    expected = HISTORY["execution_configs"][device]
    assert contract.execution_config(device, version=2) == expected["payload"]
    assert contract.execution_config_hash(device, version=2) == expected["sha256"]
    assert "deduplication" not in contract.execution_config(device, version=2)["output"]


def test_v4_profiles_are_active_and_v3_profiles_remain_frozen() -> None:
    assert contract.OCR_ACTIVE_PROCESSOR_IDS == (
        "ocr-text-cpu-fp32@4", "ocr-text-cuda-fp32@4",
    )
    assert contract.OCR_PROCESSOR_IDS == contract.OCR_ACTIVE_PROCESSOR_IDS
    assert contract.OCR_RETIRED_PROCESSOR_IDS == (
        "ocr-text@1", "ocr-text-cpu-fp32@2", "ocr-text-cuda-fp32@2",
        "ocr-text-cpu-fp32@3", "ocr-text-cuda-fp32@3",
    )
    assert set(contract.OCR_READABLE_PROCESSOR_IDS) == {
        *contract.OCR_RETIRED_PROCESSOR_IDS, *contract.OCR_ACTIVE_PROCESSOR_IDS,
    }
    for device in ("cpu", "gpu"):
        spec = ocr_processor_spec(device)
        profile = contract.execution_config(device)
        assert spec.processor_id.endswith("@4")
        assert spec.spec_revision.endswith("-v4")
        assert profile["version"] == "ocr-execution-config.v4"
        assert profile["recognition_and_preprocess"]["text_score"] == 0.5
        assert contract.execution_config(device, version=3)["recognition_and_preprocess"]["text_score"] == 0.0
        assert profile["output"]["deduplication"] == contract.DEDUPLICATION_CONFIG
        assert profile["recognition_and_preprocess"]["orientation_retry"] == contract.ORIENTATION_RETRY_CONFIG
        assert spec.config_hash == hashlib.sha256(canonical_json(profile).encode()).hexdigest()
        assert spec.config_hash == contract.execution_config_hash(device, version=4)
        assert spec.config_hash == contract.execution_config_hash(device)
        assert contract.execution_config_hash(device, version=3) == (
            contract.OCR_V3_CPU_CONFIG_HASH if device == "cpu" else contract.OCR_V3_GPU_CONFIG_HASH
        )
    assert contract.OCR_CPU_CONFIG_HASH != contract.OCR_GPU_CONFIG_HASH
    assert contract.OCR_V3_CPU_CONFIG_HASH == "2336126e744954f1740ee65c26d9da0ab928558d07466ffa65805590423cb002"
    assert contract.OCR_V3_GPU_CONFIG_HASH == "c406619b57b03718f4b73806603b04a75916dae67a2b01a241041304400fa4ad"
    assert contract.OCR_CPU_CONFIG_HASH != contract.OCR_V3_CPU_CONFIG_HASH
    assert contract.OCR_GPU_CONFIG_HASH != contract.OCR_V3_GPU_CONFIG_HASH


def test_active_policy_changes_cannot_redefine_historical_configuration(monkeypatch) -> None:
    hashes = {device: contract.execution_config_hash(device, version=4) for device in ("cpu", "gpu")}
    monkeypatch.setitem(contract.ENGINE_CONFIG, "use_cls", False)
    monkeypatch.setitem(contract.ENGINE_CONFIG, "rec_image_shape", [3, 99, 99])
    monkeypatch.setitem(contract.DEDUPLICATION_CONFIG, "substring_coverage", 0.9)
    monkeypatch.setitem(contract.ORIENTATION_RETRY_CONFIG, "retry_min_gain", 0.2)
    for device in ("cpu", "gpu"):
        expected = HISTORY["execution_configs"][device]
        assert contract.execution_config(device, version=2) == expected["payload"]
        assert contract.execution_config_hash(device, version=2) == expected["sha256"]
        assert contract.execution_config_hash(device, version=4) != hashes[device]
        assert contract.execution_config_hash(device, version=3) == (
            contract.OCR_V3_CPU_CONFIG_HASH if device == "cpu" else contract.OCR_V3_GPU_CONFIG_HASH
        )
    for sample in HISTORY["artifacts"]:
        validate_payload(deepcopy(sample["payload"]))


@pytest.mark.parametrize("sample", HISTORY["artifacts"], ids=lambda sample: sample["processor_id"])
def test_historical_payload_cannot_be_relabelled_as_v4(sample: dict) -> None:
    for processor_id in contract.OCR_ACTIVE_PROCESSOR_IDS:
        payload = {**deepcopy(sample["payload"]), "processor_id": processor_id}
        with pytest.raises(CatalogError) as caught:
            validate_payload(payload)
        assert caught.value.code == ARTIFACT_INVALID


@pytest.mark.parametrize("processor_id", (*contract.OCR_RETIRED_PROCESSOR_IDS, "unknown-ocr@9"))
def test_retired_execution_is_refused_before_catalog_or_model_access(processor_id: str) -> None:
    class ForbiddenCatalog:
        def read_one(self, *args, **kwargs):
            raise AssertionError("retired execution must not read catalog or originals")

    with pytest.raises(CatalogError) as caught:
        extract_for_asset(
            ForbiddenCatalog(), asset_id=1, input_revision="c" * 64, processor_id=processor_id,
        )
    assert caught.value.code == ARTIFACT_INVALID


def test_execution_configuration_returns_independent_nested_values() -> None:
    before = contract.execution_config("cpu", version=2)
    changed = contract.execution_config("cpu", version=2)
    changed["recognition_and_preprocess"]["rec_image_shape"][1] = 999
    assert contract.execution_config("cpu", version=2) == before


@pytest.mark.parametrize("version", (1, 5, 0))
def test_unknown_execution_configuration_version_is_rejected(version: int) -> None:
    with pytest.raises(ValueError):
        contract.execution_config("cpu", version=version)
