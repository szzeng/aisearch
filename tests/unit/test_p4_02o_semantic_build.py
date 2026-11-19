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
from p2_search_fixtures import build_library, stamp, write_jpeg

from photo_search.artifacts.store import generation_id_for
from photo_search.catalog.errors import INDEX_REPAIR_REQUIRED, CatalogError
from photo_search.config import vectors_dir
from photo_search.indexing import semantic
from photo_search.indexing.store import LanceVectorIndex, _table_name
from photo_search.runtime.model_resource_gate import GatedEncoder, ModelResourceGate
from photo_search.scheduler.contracts import canonical_json

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "tools" / "prepare_models" / "manifests" / "chinese-clip-vit-b-16.json"
DIMENSION = 512
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
            "runtime_observed_at": 1_700_000_000_000,
            "spec": {"dimension": DIMENSION},
            "device": {
                "device": self.device,
                "provider": (
                    "CUDAExecutionProvider" if self.device == "cuda" else "CPUExecutionProvider"
                ),
                "precision": "FP32",
                "cpu_fallback_disabled": True,
            },
            "runtime": {
                "runtime_backend": "onnxruntime",
                "runtime_package": "onnxruntime-gpu",
                "runtime_version": "1.30.0",
                "providers": ["CUDAExecutionProvider", "CPUExecutionProvider"],
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


def test_first_build_refuses_an_orphan_lance_table_before_catalog_writes(tmp_path: Path) -> None:
    library = build_library(tmp_path, PHOTOS)
    adapter = StubPinnedAdapter()
    try:
        spec = semantic.semantic_spec(MANIFEST, semantic.adapter_runtime_facts(adapter))
        generation_id = generation_id_for(str(spec.envelope["space_id"]), str(spec.processor_id))
        store = LanceVectorIndex(vectors_dir(library.catalog.data_root))
        store._table(generation_id=generation_id, dimension=DIMENSION, create=True)
        jobs_before = int(library.catalog.read_one("SELECT COUNT(*) AS count FROM jobs")["count"])

        with pytest.raises(CatalogError) as refused:
            semantic.build_index(library.catalog, encoder=adapter, manifest_path=MANIFEST, limit=10)

        assert refused.value.code == INDEX_REPAIR_REQUIRED
        assert refused.value.details["reason"] == "ORPHAN_TABLE"
        assert "maintenance flow" in str(refused.value.message)
        assert _table_name(generation_id) in set(store._dependencies()[0].connect(str(store.root)).list_tables().tables)
        assert library.catalog.read_one(
            "SELECT 1 FROM index_generations WHERE generation_id = ?", (generation_id,)
        ) is None
        assert library.catalog.read_one("SELECT COUNT(*) AS count FROM jobs")["count"] == jobs_before
    finally:
        library.close()


def test_a_vanished_present_file_is_reported_and_the_rest_still_builds(tmp_path: Path) -> None:
    library = build_library(tmp_path, PHOTOS)
    adapter = StubPinnedAdapter()
    try:
        missing_path = Path(library.root) / PHOTOS[0]
        original = missing_path.read_bytes()
        missing_path.unlink()
        report = semantic.build_index(
            library.catalog, encoder=adapter, manifest_path=MANIFEST, limit=10, device="auto"
        )
        assert report["targets"] == len(PHOTOS)
        assert report["out_of_scope"]["count"] == 0
        assert report["run"]["jobs"] == {"SUCCEEDED": len(PHOTOS) - 1, "FAILED": 1}
        failure = library.catalog.read_one(
            "SELECT job_id, last_error_code FROM jobs WHERE state='FAILED'"
        )
        assert failure["last_error_code"] == "SOURCE_FILE_MISSING"
        assert report["generation"]["entries"] == {"CONFIRMED": len(PHOTOS) - 1}
        absent = semantic.build_index(library.catalog, encoder=adapter, manifest_path=MANIFEST)
        assert absent["targets"] == absent["run"]["steps"] == 0

        library.service.delete_source(library.source_id)
        missing_path.write_bytes(original)
        stamp(missing_path)
        source = library.service.register_source(display_name="restored", root_path=library.root)
        library.service.run_scan(int(source["source_id"]))
        adapter.paths.clear()
        recovered = semantic.build_index(library.catalog, encoder=adapter, manifest_path=MANIFEST)
        assert recovered["targets"] == recovered["enqueue"]["rewound"] == 1
        assert adapter.paths == [str(missing_path)]
        assert recovered["generation"]["entries"] == {"CONFIRMED": len(PHOTOS)}
        assert library.catalog.read_one(
            "SELECT state FROM jobs WHERE job_id=?", (failure["job_id"],)
        )["state"] == "SUCCEEDED"
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
        # The current image-only scanner excludes the movie before semantic selection.
        assert report["checked"] == len(PHOTOS)
        assert report["targets"] == len(PHOTOS)
        assert report["out_of_scope"]["count"] == 0
        assert report["out_of_scope"]["labels"] == {}
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
            "input_revision": library.catalog.read_one(
                "SELECT sha256 FROM assets WHERE asset_id = ?", (library.assets[PHOTOS[0]],)
            )["sha256"],
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

@pytest.mark.parametrize(
    ("failure", "retry_failed"),
    [
        (RuntimeError("boom"), True),
        (CatalogError("RUNTIME_CAPABILITY_UNAVAILABLE", "semantic model adapter was replaced"), False),
    ],
)
def test_recovery_rewinds_the_same_logical_job(
    tmp_path: Path, failure: Exception, retry_failed: bool
) -> None:
    library = build_library(tmp_path, PHOTOS)
    adapter = StubPinnedAdapter(failure=failure)
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
            retry_failed=retry_failed,
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


def test_replaced_adapter_defers_job_until_fresh_adapter_runs(tmp_path: Path) -> None:
    library = build_library(tmp_path, PHOTOS[:2])
    stale = StubPinnedAdapter()
    gate = ModelResourceGate()
    current = True
    adapter = GatedEncoder(stale, gate, lambda: current)

    def replace_after_health() -> dict[str, Any]:
        nonlocal current
        facts = stale.health()
        current = False
        return facts

    adapter.health = replace_after_health  # type: ignore[method-assign]
    try:
        first = semantic.build_index(library.catalog, encoder=adapter, manifest_path=MANIFEST)
        assert first["run"] == {"steps": 1, "jobs": {"RETRY_WAIT": 1, "QUEUED": 1}}
        old = library.catalog.read_one(
            "SELECT job_id, attempts, last_error_code FROM jobs WHERE state='RETRY_WAIT'"
        )
        assert old["attempts"] == 1 and old["last_error_code"] == "RUNTIME_CAPABILITY_UNAVAILABLE"
        assert stale.paths == []

        fresh = GatedEncoder(StubPinnedAdapter(), gate)
        waiting = semantic.build_index(
            library.catalog, encoder=fresh, manifest_path=MANIFEST, retry_failed=True,
        )
        assert waiting["run"] == {"steps": 1, "jobs": {"RETRY_WAIT": 1, "SUCCEEDED": 1}}
        assert waiting["enqueue"]["rewound"] == 0
        library.clock.advance(60_000)
        resumed = semantic.build_index(library.catalog, encoder=fresh, manifest_path=MANIFEST)
        assert resumed["run"]["jobs"] == {"SUCCEEDED": 2}
        assert resumed["generation"]["entries"] == {"CONFIRMED": 2}
        assert library.catalog.read_one(
            "SELECT state FROM jobs WHERE job_id=?", (old["job_id"],)
        )["state"] == "SUCCEEDED"
    finally:
        library.close()


def test_adapter_retry_budget_is_not_reset_by_ordinary_build(tmp_path: Path) -> None:
    library = build_library(tmp_path, PHOTOS[:1])
    stale = StubPinnedAdapter()
    adapter = GatedEncoder(stale, ModelResourceGate(), lambda: False)
    adapter.health = stale.health  # type: ignore[method-assign]
    try:
        for expected in ({"RETRY_WAIT": 1}, {"RETRY_WAIT": 1}, {"FAILED": 1}):
            result = semantic.build_index(library.catalog, encoder=adapter, manifest_path=MANIFEST)
            assert result["run"] == {"steps": 1, "jobs": expected}
            library.clock.advance(60_000)
        result = semantic.build_index(library.catalog, encoder=stale, manifest_path=MANIFEST)
        assert result["run"] == {"steps": 0, "jobs": {"FAILED": 1}}
        assert stale.paths == []
    finally:
        library.close()


def test_unstable_input_waits_for_rescan_of_same_content(tmp_path: Path) -> None:
    library = build_library(tmp_path, PHOTOS[:1])
    path = library.root / PHOTOS[0]
    adapter = StubPinnedAdapter()
    try:
        stamp(path, age_ms=10_000)
        first = semantic.build_index(library.catalog, encoder=adapter, manifest_path=MANIFEST)
        assert first["run"]["jobs"] == {"FAILED": 1}
        missing = library.catalog.read_one(
            "SELECT job_id, last_error_code FROM jobs WHERE task_type = 'image_embedding'"
        )
        assert missing["last_error_code"] == "INPUT_UNSTABLE"
        repeated = semantic.build_index(library.catalog, encoder=adapter, manifest_path=MANIFEST)
        assert repeated["run"]["steps"] == 0 and adapter.paths == []

        library.service.run_scan(library.source_id)
        recovered = semantic.build_index(library.catalog, encoder=adapter, manifest_path=MANIFEST)
        assert recovered["run"]["jobs"] == {"SUCCEEDED": 1}
        assert recovered["generation"]["entries"] == {"CONFIRMED": 1}
        assert library.catalog.read_one(
            "SELECT state FROM jobs WHERE job_id=?", (missing["job_id"],)
        )["state"] == "SUCCEEDED"
    finally:
        library.close()


def test_only_small_job_id_groups_are_sent_to_scheduler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = build_library(tmp_path, PHOTOS)
    adapter = StubPinnedAdapter()
    run_once = semantic.scheduler_engine.Scheduler.run_once
    attempted: list[int] = []

    def observe(scheduler: Any, *args: Any, **kwargs: Any) -> bool:
        attempted.append(len(scheduler.job_ids))
        return run_once(scheduler, *args, **kwargs)

    monkeypatch.setattr(semantic, "DISPATCH_PAGE_SIZE", 2)
    monkeypatch.setattr(semantic.scheduler_engine.Scheduler, "run_once", observe)
    try:
        result = semantic.build_index(library.catalog, encoder=adapter, manifest_path=MANIFEST)
        assert attempted == [2, 2, 1]
        assert result["generation"]["entries"] == {"CONFIRMED": 3}
    finally:
        library.close()


def test_source_deleted_between_selection_and_enqueue_never_reaches_encoder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = build_library(tmp_path, PHOTOS)
    adapter = StubPinnedAdapter()
    select = semantic.semantic_selection

    def remove_source(*args: Any, **kwargs: Any) -> dict[str, Any]:
        selected = select(*args, **kwargs)
        library.service.delete_source(library.source_id)
        return selected

    monkeypatch.setattr(semantic, "semantic_selection", remove_source)
    try:
        report = semantic.build_index(
            library.catalog, encoder=adapter, manifest_path=MANIFEST, retry_failed=True,
        )
        assert report["targets"] == len(PHOTOS)
        assert report["run"] == {"steps": 0, "jobs": {}}
        assert report["generation"]["entries"] == {}
        assert adapter.paths == []
    finally:
        library.close()


def test_explicit_retry_confirms_pending_artifact_without_encoding_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = build_library(tmp_path, (PHOTOS[0],))
    adapter = StubPinnedAdapter()
    try:
        with monkeypatch.context() as fault:
            fault.setattr(semantic, "drain_semantic_outbox", lambda *_args, **_kwargs: {})
            interrupted = semantic.build_index(library.catalog, encoder=adapter, manifest_path=MANIFEST)
            assert interrupted["run"]["jobs"] == {"SUCCEEDED": 1}
            assert interrupted["generation"]["entries"] == {"PENDING_REPAIR": 1}
        resumed = semantic.build_index(
            library.catalog, encoder=adapter, manifest_path=MANIFEST, retry_failed=True,
        )
        assert resumed["run"]["steps"] == resumed["enqueue"]["rewound"] == 0
        assert resumed["generation"]["entries"] == {"CONFIRMED": 1}
        assert len(adapter.paths) == 1
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
