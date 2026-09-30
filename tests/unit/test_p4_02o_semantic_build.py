"""P4-02O: the semantic build entry, the scheduler executor and the profile bootstrap.

No test here loads the real model: the pinned adapter is a stand-in supplied through the same
call the CLI makes, and the files go through the production scanner, artifact/outbox path and
coordinator. The real-model, real-HTTP closure lives in
``tools/diagnostics/p4_02o_semantic_build.py``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from p2_search_fixtures import build_library, write_jpeg

from photo_search.artifacts.store import generation_id_for
from photo_search.indexing import semantic
from photo_search.scheduler.contracts import canonical_json

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "src" / "photo_search" / "models" / "embedding" / "fg-clip2-base.json"
DIMENSION = 768
PHOTOS = ("red.jpg", "green.jpg", "blue.jpg")


class StubPinnedAdapter:
    """A stand-in adapter with the pinned spec's identity and a deterministic image tower."""

    def __init__(self, *, device: str = "cuda", failure: Exception | None = None) -> None:
        self.device = str(device)
        self.failure = failure
        self.paths: list[str] = []

    def health(self) -> dict[str, Any]:
        return {
            "loaded": True,
            "spec": {"dimension": DIMENSION},
            "device": {
                "device": self.device,
                "provider": (
                    "torch-cuda" if self.device == "cuda" else "torch-cpu"
                ),
                "precision": "FP32",
                "cpu_fallback_disabled": True,
            },
            "runtime": {
                "runtime_backend": "pytorch",
                "runtime_package": "torch",
                "runtime_version": "1.30.0",
                "providers": ["torch-cuda", "torch-cpu"],
                "cuda_available": True,
            },
        }

    def encode_image(self, path: str) -> list[float]:
        self.paths.append(str(path))
        if self.failure is not None:
            raise self.failure
        digest = hashlib.sha256(Path(path).read_bytes()).digest()
        vector = [0.0] * (DIMENSION - 1) + [1.0]
        vector[int(digest[0]) % DIMENSION] = 1.0
        return vector


def pinned_spec() -> Any:
    return semantic.semantic_spec(MANIFEST, {})


def test_the_artifact_shape_is_the_frozen_embedding_contract() -> None:
    vector = [0.5] * DIMENSION
    artifact = semantic.embedding_artifact(
        semantic.artifact_id_for("chinese-clip-vit-b-16@36e679e65c2a", "a" * 64),
        vector,
        space_id="chinese-clip-vit-b-16:c8ac42d3907c57b0",
        dimension=DIMENSION,
    )
    payload = canonical_json({"vector": [float(value) for value in vector]}).encode("utf-8")
    assert artifact["artifact_type"] == "embedding"
    assert artifact["artifact_revision"] == "embedding.v1"
    assert artifact["media_type"] == "application/json"
    assert artifact["checksum_sha256"] == hashlib.sha256(payload).hexdigest()
    assert artifact["byte_size"] == len(payload)
    assert artifact["artifact_id"].startswith("image-embedding:")
    assert artifact["payload"] == {"vector": [float(value) for value in vector]}


def test_a_build_publishes_confirms_and_activates_one_profile(tmp_path: Path) -> None:
    library = build_library(tmp_path, PHOTOS)
    adapter = StubPinnedAdapter()
    try:
        spec = semantic.semantic_spec(MANIFEST, semantic.adapter_runtime_facts(adapter))
        report = semantic.build_index(
            library.catalog, encoder=adapter, manifest_path=MANIFEST, limit=10, device="auto"
        )
        assert report["targets"] == len(PHOTOS)
        assert report["enqueue"]["queued"] == len(PHOTOS)
        assert report["run"]["jobs"] == {"SUCCEEDED": len(PHOTOS)}
        assert report["drain"]["applied"] == len(PHOTOS)
        generation = report["generation"]
        assert generation["generation_id"] == generation_id_for(
            str(spec.envelope["space_id"]), str(spec.processor_id)
        )
        assert generation["state"] == "READY"
        assert generation["entries"] == {"CONFIRMED": len(PHOTOS)}
        assert report["model"]["space_id"] == str(spec.envelope["space_id"])
        assert report["evidence"]["resolved_device"] == "gpu"
        assert sorted(adapter.paths) == sorted(
            str(Path(library.root) / name) for name in PHOTOS
        )

        profile = semantic.ensure_search_profile(
            library.catalog,
            spec,
            space_id=str(spec.envelope["space_id"]),
            generation_id=str(generation["generation_id"]),
        )
        assert profile["created"] is True
        assert profile["active_profile_id"] == profile["profile_id"]
        again = semantic.ensure_search_profile(
            library.catalog,
            spec,
            space_id=str(spec.envelope["space_id"]),
            generation_id=str(generation["generation_id"]),
        )
        assert again["created"] is False and again["activated"] is False

        # Incremental: a second pass has nothing left to do for this content revision.
        assert (
            semantic.semantic_targets(
                library.catalog,
                space_id=str(spec.envelope["space_id"]),
                processor_id=str(spec.processor_id),
            )
            == []
        )
    finally:
        library.close()


def test_a_vanished_file_is_out_of_scope_and_the_rest_still_builds(tmp_path: Path) -> None:
    library = build_library(tmp_path, PHOTOS)
    adapter = StubPinnedAdapter()
    try:
        (Path(library.root) / PHOTOS[0]).unlink()
        report = semantic.build_index(
            library.catalog, encoder=adapter, manifest_path=MANIFEST, limit=10, device="auto"
        )
        assert report["targets"] == len(PHOTOS) - 1
        assert report["out_of_scope"]["count"] == 1
        assert report["out_of_scope"]["labels"] == {"UNREADABLE": 1}
        assert report["run"]["jobs"] == {"SUCCEEDED": len(PHOTOS) - 1}
        assert report["generation"]["entries"] == {"CONFIRMED": len(PHOTOS) - 1}
    finally:
        library.close()


def test_a_non_image_asset_is_out_of_scope_not_a_failure(tmp_path: Path) -> None:
    library = build_library(tmp_path, PHOTOS)
    adapter = StubPinnedAdapter()
    try:
        # A real ISO-BMFF (mp4) header: the media layer refuses it before any model work.
        (Path(library.root) / "movie.mp4").write_bytes(
            b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2mp41"
        )
        source_id = library.source_id
        library.service.run_scan(int(source_id))
        report = semantic.build_index(
            library.catalog, encoder=adapter, manifest_path=MANIFEST, limit=10, device="auto"
        )
        assert report["checked"] == len(PHOTOS) + 1
        assert report["targets"] == len(PHOTOS)
        assert report["out_of_scope"]["count"] == 1
        assert report["out_of_scope"]["labels"] == {"ISO-BMFF": 1}
        assert report["run"]["jobs"] == {"SUCCEEDED": len(PHOTOS)}
        assert "movie.mp4" not in " ".join(adapter.paths)
    finally:
        library.close()


def test_the_executor_reports_a_file_that_vanishes_after_selection(tmp_path: Path) -> None:
    library = build_library(tmp_path, PHOTOS)
    try:
        spec = pinned_spec()
        executor = semantic.semantic_executor(
            library.catalog,
            encoder=StubPinnedAdapter(),
            space_id=str(spec.envelope["space_id"]),
            dimension=DIMENSION,
            processor_id=str(spec.processor_id),
        )
        request = {
            "job_id": 1,
            "lease_token": "lease",
            "subject_id": str(library.assets[PHOTOS[0]]),
            "input_revision": "a" * 64,
            "task_type": semantic.SEMANTIC_TASK_TYPE,
            "processor_id": str(spec.processor_id),
            "resolved_device": "gpu",
            "runtime_provider": "CUDAExecutionProvider",
            "precision": "FP32",
        }
        ok = executor(request)
        assert ok["state"] == "SUCCEEDED" and len(ok["artifacts"]) == 1
        (Path(library.root) / PHOTOS[0]).unlink()
        failed = executor(request)
        assert failed["state"] == "FAILED"
        assert failed["artifacts"] == []
        assert failed["error"]["code"] == "SOURCE_FILE_MISSING"
    finally:
        library.close()


def test_a_source_scoped_build_selects_only_that_source(tmp_path: Path) -> None:
    """DS-H review F1: ``build --source-id`` must restrict the targets, not break the SQL."""
    library = build_library(tmp_path, PHOTOS)
    adapter = StubPinnedAdapter()
    try:
        other = tmp_path / "other"
        write_jpeg(other / "purple.jpg", index=9)
        source = library.service.register_source(display_name="other", root_path=other)
        library.service.run_scan(int(source["source_id"]))
        spec = pinned_spec()
        space_id = str(spec.envelope["space_id"])
        processor_id = str(spec.processor_id)

        everything = semantic.semantic_selection(
            library.catalog, space_id=space_id, processor_id=processor_id
        )
        assert everything["checked"] == len(PHOTOS) + 1

        first = semantic.semantic_selection(
            library.catalog,
            space_id=space_id,
            processor_id=processor_id,
            source_id=int(library.source_id),
        )
        assert first["checked"] == len(PHOTOS)
        assert len(first["targets"]) == len(PHOTOS)

        second = semantic.semantic_selection(
            library.catalog,
            space_id=space_id,
            processor_id=processor_id,
            source_id=int(source["source_id"]),
        )
        assert second["checked"] == 1 and len(second["targets"]) == 1

        absent = semantic.semantic_selection(
            library.catalog,
            space_id=space_id,
            processor_id=processor_id,
            source_id=int(source["source_id"]) + 999,
        )
        assert absent["checked"] == 0 and absent["targets"] == []

        report = semantic.build_index(
            library.catalog,
            encoder=adapter,
            manifest_path=MANIFEST,
            limit=10,
            source_id=int(library.source_id),
            device="auto",
        )
        assert report["targets"] == len(PHOTOS)
        assert report["generation"]["entries"] == {"CONFIRMED": len(PHOTOS)}
        assert all("purple" not in path for path in adapter.paths)
    finally:
        library.close()

def test_retry_failed_rewinds_the_same_logical_job(tmp_path: Path) -> None:
    library = build_library(tmp_path, PHOTOS)
    adapter = StubPinnedAdapter(failure=RuntimeError("boom"))
    try:
        report = semantic.build_index(
            library.catalog, encoder=adapter, manifest_path=MANIFEST, limit=10, device="auto"
        )
        assert report["run"]["jobs"] == {"FAILED": len(PHOTOS)}
        again = semantic.build_index(
            library.catalog,
            encoder=StubPinnedAdapter(),
            manifest_path=MANIFEST,
            limit=10,
            device="auto",
            retry_failed=True,
        )
        assert again["enqueue"]["rewound"] == len(PHOTOS)
        assert again["run"]["jobs"] == {"SUCCEEDED": len(PHOTOS)}
        assert again["generation"]["entries"] == {"CONFIRMED": len(PHOTOS)}
        third = semantic.build_index(
            library.catalog,
            encoder=StubPinnedAdapter(),
            manifest_path=MANIFEST,
            limit=10,
            device="auto",
            retry_failed=True,
        )
        assert third["targets"] == 0 and third["enqueue"]["rewound"] == 0
    finally:
        library.close()


def test_the_executor_refuses_a_vector_of_the_wrong_dimension(tmp_path: Path) -> None:
    library = build_library(tmp_path, PHOTOS)
    adapter = StubPinnedAdapter()
    adapter.encode_image = lambda path: [0.0, 1.0]  # type: ignore[method-assign]
    try:
        report = semantic.build_index(
            library.catalog, encoder=adapter, manifest_path=MANIFEST, limit=1, device="auto"
        )
        assert report["run"]["jobs"] == {"FAILED": 1}
        row = library.catalog.read_one(
            "SELECT last_error_code FROM jobs WHERE state = 'FAILED' LIMIT 1"
        )
        assert str(row["last_error_code"]) == "RUNTIME_RESOLUTION_MISMATCH"
    finally:
        library.close()


def test_the_dry_run_listing_needs_no_model_and_marks_targets(tmp_path: Path) -> None:
    library = build_library(tmp_path, PHOTOS)
    try:
        spec = pinned_spec()
        targets = semantic.semantic_targets(
            library.catalog,
            space_id=str(spec.envelope["space_id"]),
            processor_id=str(spec.processor_id),
            limit=2,
        )
        assert [item["asset_id"] for item in targets] == sorted(library.assets.values())[:2]
        assert all(len(str(item["input_revision"])) == 64 for item in targets)
        report = pytest.importorskip("photo_search.indexing.cli")
        assert "build" in report.COMMANDS
    finally:
        library.close()
