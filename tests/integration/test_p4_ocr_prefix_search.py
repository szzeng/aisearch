"""Recorded OCR outputs reach the real scheduler, artifact store, projection and FTS."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from p3_ocr_fixtures import Harness, stamp
from PIL import Image

from photo_search.artifacts.store import ArtifactStore
from photo_search.ocr import contract, service
from photo_search.search.tokens import build_match
from tests.integration.test_p4_02y_l_identity import install_runtime_evidence


@pytest.mark.parametrize("device", ["cpu", "gpu"])
def test_recorded_dense_prefix_is_searchable_after_publication(tmp_path, monkeypatch, device):
    data = json.loads((Path(__file__).parents[1] / "fixtures/ocr_dense_recorded.json")
                      .read_text(encoding="utf-8"))
    install_runtime_evidence(monkeypatch)

    class RecordedCache:
        def engine(self, _root, *, resolved_device, orientation_retry, **_kwargs):
            assert orientation_retry is True
            assert resolved_device == device
            tiles = iter(data["tiles"])

            class RecordedEngine:
                runtime_provider = (
                    "CUDAExecutionProvider" if device == "gpu" else "CPUExecutionProvider"
                )

                def read(self, _bgr):
                    return [[row["polygon"], row["text"], row["confidence"]]
                            for row in next(tiles)["raw"]]

            return RecordedEngine()

    harness = Harness(tmp_path)
    try:
        path = harness.library / "dense.png"
        Image.new("RGB", tuple(data["size"]), "white").save(path)
        stamp(path)
        harness.scan()
        asset_id = harness.asset_id(path.name)
        service.catch_up_projection(harness.catalog)
        query = build_match("旅行记录").expression
        assert harness.text_hits(query) == []

        result = harness.recognize(requested_device=device, engine_cache=RecordedCache())
        assert result["run"]["jobs"] == {"SUCCEEDED": 1}
        assert harness.text_hits(query) == [asset_id]
        assert harness.text_hits(build_match("1280").expression) == [asset_id]
        expected_id = (
            contract.OCR_CPU_PROCESSOR_ID if device == "cpu" else contract.OCR_GPU_PROCESSOR_ID
        )
        current = harness.ocr_document(asset_id)
        artifact = harness.catalog.read_one(
            "SELECT * FROM artifacts WHERE processor_id = ?", (expected_id,)
        )
        payload = json.loads(ArtifactStore.open(harness.data_root).read(artifact))
        assert payload["processor_id"] == expected_id
        assert "旅行记录03日期2026-10-15金额1280" in payload["original_text"]
        assert len(harness.ocr_regions(asset_id)) == payload["region_count"]
        assert current["processor_id"] == expected_id
    finally:
        harness.close()
