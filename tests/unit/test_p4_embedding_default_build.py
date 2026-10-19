"""Focused isolated-gallery checks for the new-library semantic-model default."""

from pathlib import Path
from typing import Any

import pytest
from p2_search_fixtures import build_library

from photo_search.artifacts.store import generation_id_for
from photo_search.catalog.errors import (
    EMBEDDING_DEVICE_UNAVAILABLE,
    INDEX_REPAIR_REQUIRED,
    CatalogError,
)
from photo_search.config import vectors_dir
from photo_search.indexing import semantic
from photo_search.indexing.store import LanceVectorIndex
from photo_search.refresh.pipeline import _semantic_build_stage
from photo_search.refresh.state import STAGE_FAILED, RefreshStatus

REPO_ROOT = Path(__file__).resolve().parents[2]
QWEN_MANIFEST = (
    REPO_ROOT / "tools" / "prepare_models" / "manifests" / "qwen3-vl-embedding-2b.json"
)


def _status(source_id: int) -> RefreshStatus:
    return RefreshStatus(
        refresh_id="synthetic-first-build",
        source_ids=[source_id],
        batch_size=10,
        started_at_ms=1,
        mode="build",
    )


@pytest.mark.parametrize(
    "failure",
    [None, CatalogError(EMBEDDING_DEVICE_UNAVAILABLE, "Qwen requires CUDA"), "returns-none"],
)
def test_first_build_reports_missing_qwen_or_cuda_without_clip_fallback(
    tmp_path: Path, failure: Any
) -> None:
    library = build_library(tmp_path, ("photo.jpg",))
    status = _status(library.source_id)
    try:
        jobs_before = int(library.catalog.read_one("SELECT COUNT(*) AS n FROM jobs")["n"])
        outbox_before = int(
            library.catalog.read_one("SELECT COUNT(*) AS n FROM index_outbox")["n"]
        )
        marker_key = f"source_build:{library.source_id}"
        marker_before = library.catalog.read_one(
            "SELECT value FROM metadata WHERE key = ?", (marker_key,)
        )

        def unavailable_encoder():
            if failure == "returns-none":
                return None
            if failure is not None:
                raise failure
            raise AssertionError("None encoder factory must not be called")

        result = _semantic_build_stage(
            library.catalog,
            status,
            scopes=[library.source_id],
            content_delta=set(),
            delta_overflow=False,
            encoder_factory=None if failure is None else unavailable_encoder,
            manifest_path=QWEN_MANIFEST,
            device="auto",
            device_policy="gpu_required",
            cancel_check=None,
        )

        assert result["state"] == STAGE_FAILED, result
        assert "chinese-clip" not in str(result).lower()
        assert status.snapshot()["errors"]
        assert int(library.catalog.read_one("SELECT COUNT(*) AS n FROM jobs")["n"]) == jobs_before
        assert int(
            library.catalog.read_one("SELECT COUNT(*) AS n FROM index_outbox")["n"]
        ) == outbox_before
        marker_after = library.catalog.read_one(
            "SELECT value FROM metadata WHERE key = ?", (marker_key,)
        )
        assert marker_after == marker_before
        if failure is None:
            assert "qwen3-vl-embedding-2b" in status.snapshot()["errors"][0]["message"]
        elif isinstance(failure, CatalogError):
            assert status.snapshot()["errors"][0]["code"] == EMBEDDING_DEVICE_UNAVAILABLE
        else:
            assert status.snapshot()["errors"][0]["code"] != "EMBEDDING_MODEL_UNAVAILABLE"
            assert "did not load" in status.snapshot()["errors"][0]["message"]
    finally:
        library.close()


def test_first_build_orphan_precheck_runs_before_semantic_state_writes(tmp_path: Path) -> None:
    library = build_library(tmp_path, ("photo.jpg",))
    status = _status(library.source_id)
    try:
        spec = semantic.semantic_spec(QWEN_MANIFEST, {})
        generation_id = generation_id_for(str(spec.envelope["space_id"]), str(spec.processor_id))
        store = LanceVectorIndex(vectors_dir(library.catalog.data_root))
        store._table(
            generation_id=generation_id,
            dimension=int(spec.envelope["dimension"]),
            create=True,
        )
        jobs_before = int(library.catalog.read_one("SELECT COUNT(*) AS n FROM jobs")["n"])
        outbox_before = int(
            library.catalog.read_one("SELECT COUNT(*) AS n FROM index_outbox")["n"]
        )
        marker_key = f"source_build:{library.source_id}"
        marker_before = library.catalog.read_one(
            "SELECT value FROM metadata WHERE key = ?", (marker_key,)
        )

        result = _semantic_build_stage(
            library.catalog,
            status,
            scopes=[library.source_id],
            content_delta=set(),
            delta_overflow=False,
            encoder_factory=lambda: pytest.fail("orphan precheck must run before model loading"),
            manifest_path=QWEN_MANIFEST,
            device="auto",
            device_policy="gpu_required",
            cancel_check=None,
        )

        assert result["state"] == STAGE_FAILED
        assert result["code"] == INDEX_REPAIR_REQUIRED
        assert result["reason"] == "ORPHAN_TABLE"
        assert int(library.catalog.read_one("SELECT COUNT(*) AS n FROM jobs")["n"]) == jobs_before
        assert int(
            library.catalog.read_one("SELECT COUNT(*) AS n FROM index_outbox")["n"]
        ) == outbox_before
        marker_after = library.catalog.read_one(
            "SELECT value FROM metadata WHERE key = ?", (marker_key,)
        )
        assert marker_before is None and marker_after is None
    finally:
        library.close()
