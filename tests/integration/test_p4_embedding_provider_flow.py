"""P4-Embedding-Arch: the two-provider build, the candidate rule and the controlled selection.

Everything runs on ``%TEMP%`` fixtures through the production scanner, scheduler, artifact/outbox
path, Lance generation and the real HTTP application. The only stand-ins are the deterministic
clock and one adapter object per space (no real model, GPU or network), exactly like the P4-02O /
P4-02Y suites. The real CUDA closure lives in
``tools/diagnostics/p4_embedding_provider_smoke.py``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import DEFAULT_SESSION_TOKEN, Clock, Usage, WallClock, write_jpeg
from httpx import ASGITransport, AsyncClient

from photo_search.api.settings import ApiSettings
from photo_search.app import create_app
from photo_search.artifacts.store import ArtifactStore
from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import (
    SELECTION_COVERAGE_INCOMPLETE,
    SELECTION_OUTBOX_PENDING,
    SELECTION_WRITER_ACTIVE,
    CatalogError,
)
from photo_search.config import vectors_dir
from photo_search.indexing import selection as selection_module
from photo_search.indexing import semantic
from photo_search.ingestion.service import CatalogService
from photo_search.models.embedding import cli as embedding_cli
from photo_search.runtime.data_root_lease import DataRootLeaseBusy
from photo_search.search import profile as profile_module

REPO_ROOT = Path(__file__).resolve().parents[2]
CLIP_MANIFEST = REPO_ROOT / "tools" / "prepare_models" / "manifests" / "chinese-clip-vit-b-16.json"
QWEN_MANIFEST = (
    REPO_ROOT / "tools" / "prepare_models" / "manifests" / "qwen3-vl-embedding-2b.json"
)
DIMENSION = 512
ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]", "testserver"})


class StubAdapter:
    """One deterministic adapter for both towers of one space."""

    def __init__(self, spec: Any, *, pytorch: bool = False) -> None:
        self._spec = spec
        self.pytorch = bool(pytorch)
        self.paths: list[str] = []

    def spec(self) -> Any:
        return self._spec

    def health(self) -> dict[str, Any]:
        if self.pytorch:
            return {
                "loaded": True,
                "spec": self._spec.envelope,
                "device": {
                    "device": "cuda",
                    "provider": "PyTorchCUDA",
                    "precision": "FP16",
                    "cpu_fallback_disabled": True,
                },
                "runtime": {
                    "runtime_backend": "pytorch",
                    "runtime_package": "torch",
                    "runtime_version": "2.8.0+cu128",
                    "providers": ["PyTorchCUDA"],
                    "cuda_available": True,
                },
            }
        return {
            "loaded": True,
            "spec": self._spec.envelope,
            "device": {
                "device": "cuda",
                "provider": "CUDAExecutionProvider",
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
        dimension = int(self._spec.envelope["dimension"])
        digest = hashlib.sha256(Path(path).read_bytes()).digest()
        vector = [0.0] * dimension
        vector[int.from_bytes(digest[:4], "big") % dimension] = 1.0
        return vector

    def encode_queries(self, texts: Sequence[str], cancellation: Any = None) -> list[list[float]]:
        dimension = int(self._spec.envelope["dimension"])
        return [[1.0] + [0.0] * (dimension - 1) for _ in texts]


@dataclass
class FlowEnv:
    """A scanned temp library plus the two candidate spaces built over it."""

    tmp_path: Path
    data_root: Path
    library: Path
    clock: Clock
    disk: Usage = field(default_factory=Usage)
    clip: StubAdapter | None = None
    qwen: StubAdapter | None = None

    @classmethod
    def build(cls, tmp_path: Path, *, photos: int = 3) -> FlowEnv:
        tmp_path = Path(tmp_path)
        data_root = tmp_path / "PhotoSearchData"
        library = tmp_path / "library"
        clock = Clock()
        wall = WallClock()
        disk = Usage()
        service = CatalogService.open(
            data_root=data_root,
            clock=clock,
            lease_seconds=60,
            disk_usage=disk,
            wall_clock_ms=wall,
            stability_sleep=wall.advance,
        )
        try:
            for index in range(photos):
                write_jpeg(library / f"photo-{index:03d}.jpg", noise=index + 1)
            service.register_source(display_name="library", root_path=library)
            service.run_scan(1)
        finally:
            service.close()
        return cls(tmp_path=tmp_path, data_root=data_root, library=library, clock=clock, disk=disk)

    # ----------------------------------------------------------------------- resources
    def service(self) -> CatalogService:
        return CatalogService.open(
            data_root=self.data_root,
            clock=self.clock,
            lease_seconds=60,
            disk_usage=self.disk,
        )

    def open_catalog(self) -> Catalog:
        return Catalog.open(data_root=self.data_root)

    def add_photo(self, name: str, *, noise: int = 99) -> Path:
        return write_jpeg(self.library / name, noise=noise)

    def rescan(self, service: CatalogService) -> None:
        row = service.catalog.read_one("SELECT MIN(source_id) AS value FROM source_roots")
        service.run_scan(int(row["value"]))

def build_space(
    service: CatalogService, *, provider: str, adapter: StubAdapter, manifest: Path | None = None
) -> dict[str, Any]:
    manifest = manifest or (CLIP_MANIFEST if provider == "clip" else QWEN_MANIFEST)
    spec = semantic.semantic_spec(manifest, {})
    payload = semantic.build_index(
        service.catalog,
        encoder=adapter,
        manifest_path=manifest,
        expected_space_id=str(spec.envelope["space_id"]),
        expected_processor_id=str(spec.processor_id),
        limit=100,
    )
    profile = semantic.ensure_search_profile(
        service.catalog,
        semantic.semantic_spec(manifest, semantic.adapter_runtime_facts(adapter)),
        space_id=str(spec.envelope["space_id"]),
        generation_id=str(payload["generation"]["generation_id"]),
    )
    return {"spec": spec, "build": payload, "profile": profile}


def activate_candidate(catalog: Catalog, profile_id: str, space_id: str) -> dict[str, Any]:
    """The production selection gate, without the CLI's lease and process handling."""
    stored = profile_module.load_profile(catalog, profile_id)
    assert stored is not None
    report, lance = prepare_selection(catalog, stored)
    selection_module.require_selection_ready(report, lance=lance)
    profile, changed, readiness = profile_module.activate_profile(
        catalog,
        profile_id=profile_id,
        precondition=selection_module.selection_precondition(report, lance=lance),
    )
    return {"profile": profile, "changed": changed, "readiness": readiness, "report": report}


def prepare_selection(catalog: Catalog, profile: Any) -> tuple[Any, dict[str, Any]]:
    """The outside-the-transaction half of the selection, exactly as the CLI prepares it."""
    return selection_module.selection_report(
        catalog,
        ArtifactStore.open(catalog.data_root),
        generation_id=str(profile.image_generation_id),
        space_id=str(profile.space_id),
        processor_id=str(profile.embedding_processor_id),
        vectors_root=vectors_dir(catalog.data_root),
    )


def test_select_profile_requires_stopped_attestation_before_touching_the_data_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_lease(_data_root: Path) -> Any:
        raise AssertionError("the lease must not be acquired without --stopped")

    monkeypatch.setattr(embedding_cli, "resolve_data_root", lambda _value: tmp_path)
    monkeypatch.setattr(embedding_cli, "acquire_data_root_lease", unexpected_lease)
    monkeypatch.setattr(
        embedding_cli.Catalog,
        "open",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("catalog must not open")),
    )

    with pytest.raises(CatalogError, match="--stopped") as caught:
        embedding_cli._select_profile(
            SimpleNamespace(data_root=str(tmp_path), stopped=False, profile="unused")
        )
    assert caught.value.code == "USAGE"


def test_select_profile_refuses_a_managed_writer_lease_before_opening_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def busy_lease(_data_root: Path) -> Any:
        raise DataRootLeaseBusy("synthetic-managed-writer")

    monkeypatch.setattr(embedding_cli, "resolve_data_root", lambda _value: tmp_path)
    monkeypatch.setattr(embedding_cli, "acquire_data_root_lease", busy_lease)
    monkeypatch.setattr(
        embedding_cli.Catalog,
        "open",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("catalog must not open")),
    )

    with pytest.raises(CatalogError) as caught:
        embedding_cli._select_profile(
            SimpleNamespace(data_root=str(tmp_path), stopped=True, profile="unused")
        )
    assert caught.value.code == SELECTION_WRITER_ACTIVE
    assert caught.value.details == {
        "reason": "DATA_ROOT_LEASE_BUSY",
        "lease": "synthetic-managed-writer",
    }


def test_a_candidate_build_never_moves_the_pointer_and_the_selection_does(tmp_path: Path) -> None:
    env = FlowEnv.build(tmp_path)
    service = env.service()
    try:
        clip_spec = semantic.semantic_spec(CLIP_MANIFEST, {})
        clip = StubAdapter(clip_spec)
        first = build_space(service, provider="clip", adapter=clip)
        assert first["profile"]["activated"] is True
        active_before = first["profile"]["profile_id"]

        qwen_spec = semantic.semantic_spec(QWEN_MANIFEST, {})
        assert qwen_spec.envelope["dimension"] == clip_spec.envelope["dimension"] == DIMENSION
        assert qwen_spec.envelope["space_id"] != clip_spec.envelope["space_id"]
        qwen = StubAdapter(qwen_spec, pytorch=True)
        candidate = build_space(service, provider="qwen", adapter=qwen)
        assert candidate["profile"]["created"] is True
        assert candidate["profile"]["activated"] is False
        assert candidate["profile"]["reason"] == "CANDIDATE_NOT_ACTIVATED"
        assert candidate["profile"]["active_profile_id"] == active_before
        assert candidate["build"]["generation"]["entries"] == {"CONFIRMED": 3}
        assert candidate["build"]["model"]["dimension"] == DIMENSION
        # The candidate's evidence is the PyTorch CUDA fact, not an ONNX provider name.
        assert candidate["build"]["evidence"]["resolved_provider"] == "PyTorchCUDA"
        assert candidate["build"]["evidence"]["resolved_precision"] == "FP16"

        selection = activate_candidate(
            service.catalog, str(candidate["profile"]["profile_id"]), str(qwen_spec.envelope["space_id"])
        )
        assert selection["changed"] is True
        assert selection["report"].to_dict()["covered"] == 3
        assert selection["report"].missing == 0
        assert selection["readiness"]["provider_name"] == "PyTorchCUDA"
    finally:
        service.close()

    # A fresh connection (a restart) reads the persisted pointer, not an in-memory value.
    with env.open_catalog() as reopened:
        active = profile_module.active_profile(reopened)
        assert active is not None
        assert active.space_id == str(qwen_spec.envelope["space_id"])


@pytest.mark.parametrize("manifest", [CLIP_MANIFEST, QWEN_MANIFEST])
def test_orientation_upgrade_is_a_separate_candidate_and_can_return_to_legacy(
    tmp_path: Path, manifest: Path
) -> None:
    env = FlowEnv.build(tmp_path)
    service = env.service()
    try:
        old_spec = semantic.semantic_spec(manifest, {})
        old = build_space(
            service, provider="unused", manifest=manifest,
            adapter=StubAdapter(old_spec, pytorch=manifest == QWEN_MANIFEST),
        )
        before = profile_module.active_profile(service.catalog)
        assert before is not None
        new_manifest = manifest.with_stem(manifest.stem + "-exif-v2")
        new_spec = semantic.semantic_spec(new_manifest, {})
        new = build_space(
            service, provider="unused", manifest=new_manifest,
            adapter=StubAdapter(new_spec, pytorch=manifest == QWEN_MANIFEST),
        )
        assert new["profile"]["activated"] is False
        assert profile_module.active_profile(service.catalog) == before
        assert old_spec.processor_id != new_spec.processor_id
        assert old["build"]["generation"]["generation_id"] != new["build"]["generation"]["generation_id"]
        assert new["build"]["generation"]["entries"] == {"CONFIRMED": 3}
        new_profile = str(new["profile"]["profile_id"])
        activate_candidate(service.catalog, new_profile, str(new_spec.envelope["space_id"]))
        assert profile_module.active_profile(service.catalog).profile_id == new_profile
        activate_candidate(
            service.catalog, str(old["profile"]["profile_id"]), str(old_spec.envelope["space_id"])
        )
        assert profile_module.active_profile(service.catalog).profile_id == before.profile_id
    finally:
        service.close()
    with env.open_catalog() as reopened:
        assert profile_module.active_profile(reopened).space_id == old_spec.envelope["space_id"]


def test_switching_back_requires_full_coverage_and_rejects_a_gap(tmp_path: Path) -> None:
    env = FlowEnv.build(tmp_path)
    service = env.service()
    try:
        clip_spec = semantic.semantic_spec(CLIP_MANIFEST, {})
        clip = StubAdapter(clip_spec)
        first = build_space(service, provider="clip", adapter=clip)
        qwen_spec = semantic.semantic_spec(QWEN_MANIFEST, {})
        qwen = StubAdapter(qwen_spec, pytorch=True)
        candidate = build_space(service, provider="qwen", adapter=qwen)
        activate_candidate(
            service.catalog, str(candidate["profile"]["profile_id"]), str(qwen_spec.envelope["space_id"])
        )

        # A photo added while Qwen is active: the Chinese-CLIP space no longer covers the library.
        env.add_photo("photo-003.jpg", noise=42)
        env.rescan(service)
        with pytest.raises(CatalogError) as refused:
            activate_candidate(
                service.catalog, str(first["profile"]["profile_id"]), str(clip_spec.envelope["space_id"])
            )
        assert refused.value.code == SELECTION_COVERAGE_INCOMPLETE
        assert refused.value.details["reason"] == "COVERAGE_MISSING"
        assert refused.value.details["missing"] == 1
        assert profile_module.active_profile(service.catalog).space_id == str(
            qwen_spec.envelope["space_id"]
        )

        # Building the delta in the Chinese-CLIP space makes the switch legal again.
        refreshed = build_space(service, provider="clip", adapter=clip)
        assert refreshed["profile"]["activated"] is False  # the candidate rule again
        switched = activate_candidate(
            service.catalog, str(refreshed["profile"]["profile_id"]), str(clip_spec.envelope["space_id"])
        )
        assert switched["changed"] is True
        assert profile_module.active_profile(service.catalog).space_id == str(
            clip_spec.envelope["space_id"]
        )
    finally:
        service.close()


def test_pending_outbox_blocks_but_offline_assets_do_not_block_selection(tmp_path: Path) -> None:
    from p2_search_fixtures import configured_scheduler, submit_vector

    env = FlowEnv.build(tmp_path)
    service = env.service()
    try:
        qwen_spec = semantic.semantic_spec(QWEN_MANIFEST, {})
        qwen = StubAdapter(qwen_spec, pytorch=True)
        candidate = build_space(service, provider="qwen", adapter=qwen)
        generation_id = str(candidate["build"]["generation"]["generation_id"])

        # One asset whose vector was published but not yet confirmed by the outbox replay.
        # The authoritative filter revision is bumped first, exactly as a place edit would: the
        # new projection revision is what makes this a *second*, still-unconfirmed event.
        from photo_search.places import revision as place_revision

        with service.catalog.write() as connection:
            place_revision.bump_filter_revision(
                connection,
                cause=place_revision.CAUSE_EFFECTIVE_ASSIGNMENT_CHANGED,
                now_ms=service.catalog.now_ms(),
            )
        scheduler = configured_scheduler(service.catalog, [qwen_spec])
        row = service.catalog.read_one("SELECT MIN(asset_id) AS value FROM assets")
        submit_vector(
            service.catalog,
            scheduler,
            qwen_spec,
            asset_id=int(row["value"]),
            vector=[1.0] + [0.0] * (DIMENSION - 1),
            artifact_id="image-embedding:qwen-unconfirmed",
            revision="unconfirmed-revision",
        )
        report, _ = selection_module.selection_report(
            service.catalog,
            ArtifactStore.open(service.catalog.data_root),
            generation_id=generation_id,
            space_id=str(qwen_spec.envelope["space_id"]),
            processor_id=str(qwen_spec.processor_id),
        )
        assert report.pending_outbox == 1
        assert report.confirmed_entries == 2
        with pytest.raises(CatalogError) as refused:
            selection_module.require_selection_ready(report)
        assert refused.value.code in (SELECTION_COVERAGE_INCOMPLETE, SELECTION_OUTBOX_PENDING)

        # A catalog-confirmed offline asset is a separate diagnostic, not a member of the
        # online image coverage denominator. A fully covered candidate can be activated while
        # the old asset, its metadata and its prior generation entries remain intact.
        offline = FlowEnv.build(tmp_path / "offline")
        offline_service = offline.service()
        try:
            active_spec = semantic.semantic_spec(CLIP_MANIFEST, {})
            active = build_space(
                offline_service, provider="clip", adapter=StubAdapter(active_spec)
            )
            offline_sha = hashlib.sha256(
                (offline.library / "photo-000.jpg").read_bytes()
            ).hexdigest()
            (offline.library / "photo-000.jpg").unlink()
            offline.rescan(offline_service)
            candidate_spec = semantic.semantic_spec(QWEN_MANIFEST, {})
            candidate = build_space(
                offline_service,
                provider="qwen",
                adapter=StubAdapter(candidate_spec, pytorch=True),
            )
            offline_asset = offline_service.catalog.read_one(
                "SELECT asset_id FROM assets WHERE sha256 = ?", (offline_sha,)
            )
            assert offline_asset is not None
            old_generation = str(active["build"]["generation"]["generation_id"])
            old_entries_before = int(
                offline_service.catalog.read_one(
                    "SELECT COUNT(*) AS value FROM index_entries WHERE generation_id = ? AND state = 'CONFIRMED'",
                    (old_generation,),
                )["value"]
            )
            report, _ = selection_module.selection_report(
                offline_service.catalog,
                ArtifactStore.open(offline_service.catalog.data_root),
                generation_id=str(candidate["build"]["generation"]["generation_id"]),
                space_id=str(candidate_spec.envelope["space_id"]),
                processor_id=str(candidate_spec.processor_id),
            )
            assert report.offline_assets == 1
            assert report.required == report.covered == 2
            assert report.missing == 0
            assert report.complete is True
            selection_module.require_selection_ready(report)
            assert profile_module.active_profile(offline_service.catalog).profile_id == str(
                active["profile"]["profile_id"]
            )

            activated = activate_candidate(
                offline_service.catalog,
                str(candidate["profile"]["profile_id"]),
                str(candidate_spec.envelope["space_id"]),
            )
            assert activated["changed"] is True
            assert profile_module.active_profile(offline_service.catalog).profile_id == str(
                candidate["profile"]["profile_id"]
            )
            assert offline_service.catalog.read_one(
                "SELECT state FROM assets WHERE asset_id = ?", (int(offline_asset["asset_id"]),)
            )["state"] == "ACTIVE"
            assert offline_service.catalog.read_one(
                "SELECT COUNT(*) AS value FROM file_locations "
                "WHERE asset_id = ? AND availability = 'MISSING'",
                (int(offline_asset["asset_id"]),),
            )["value"] == 1
            old_entries_after = int(
                offline_service.catalog.read_one(
                    "SELECT COUNT(*) AS value FROM index_entries WHERE generation_id = ? AND state = 'CONFIRMED'",
                    (old_generation,),
                )["value"]
            )
            assert old_entries_after == old_entries_before == 3

            # An online image with no target-space vector remains a hard failure even when a
            # separate asset is offline; offline history never reduces the required set.
            offline.add_photo("photo-new.jpg", noise=77)
            offline.rescan(offline_service)
            target_profile = profile_module.load_profile(
                offline_service.catalog, str(candidate["profile"]["profile_id"])
            )
            assert target_profile is not None
            gap_report, gap_lance = prepare_selection(offline_service.catalog, target_profile)
            assert gap_report.offline_assets == 1
            assert gap_report.required == 3 and gap_report.covered == 2 and gap_report.missing == 1
            with pytest.raises(CatalogError) as refused:
                selection_module.require_selection_ready(gap_report, lance=gap_lance)
            assert refused.value.code == SELECTION_COVERAGE_INCOMPLETE
            assert refused.value.details["reason"] == "COVERAGE_MISSING"
            assert profile_module.active_profile(offline_service.catalog).profile_id == str(
                candidate["profile"]["profile_id"]
            )
        finally:
            offline_service.close()
    finally:
        service.close()


def test_offline_count_is_unique_active_assets_not_missing_location_rows(tmp_path: Path) -> None:
    env = FlowEnv.build(tmp_path, photos=1)
    service = env.service()
    try:
        original = env.library / "photo-000.jpg"
        content = original.read_bytes()
        second_root = tmp_path / "second-source"
        second_root.mkdir()
        (second_root / original.name).write_bytes(content)
        second_source = service.register_source(display_name="second", root_path=second_root)
        second_source_id = int(second_source["source_id"])
        service.run_scan(second_source_id)
        original_source_id = int(service.list_sources()[0]["source_id"])

        spec = semantic.semantic_spec(CLIP_MANIFEST, {})
        built = build_space(service, provider="clip", adapter=StubAdapter(spec))
        profile = profile_module.load_profile(service.catalog, str(built["profile"]["profile_id"]))
        assert profile is not None
        asset = service.catalog.read_one(
            "SELECT asset_id FROM assets WHERE sha256 = ?", (hashlib.sha256(content).hexdigest(),)
        )
        assert asset is not None
        asset_id = int(asset["asset_id"])
        assert service.catalog.read_one(
            "SELECT COUNT(*) AS value FROM file_locations WHERE asset_id = ?", (asset_id,)
        )["value"] == 2

        # One source disappears while the other remains PRESENT: the asset is still searchable.
        original.unlink()
        service.run_scan(original_source_id)
        report, _ = prepare_selection(service.catalog, profile)
        assert report.offline_assets == 0
        assert report.required == report.covered == 1

        # Both location rows are MISSING, but the report counts the unique ACTIVE asset once.
        second_copy = second_root / original.name
        second_copy.unlink()
        service.run_scan(second_source_id)
        report, _ = prepare_selection(service.catalog, profile)
        assert service.catalog.read_one(
            "SELECT COUNT(*) AS value FROM file_locations "
            "WHERE asset_id = ? AND availability = 'MISSING'", (asset_id,)
        )["value"] == 2
        assert report.offline_assets == 1

        # Removing both source registrations leaves the legacy asset with no locations, still
        # counted once and preserved in the catalog.
        service.delete_source(second_source_id)
        service.delete_source(original_source_id)
        report, _ = prepare_selection(service.catalog, profile)
        assert service.catalog.read_one(
            "SELECT COUNT(*) AS value FROM file_locations WHERE asset_id = ?", (asset_id,)
        )["value"] == 0
        assert service.catalog.read_one(
            "SELECT COUNT(*) AS value FROM assets WHERE asset_id = ? AND state = 'ACTIVE'",
            (asset_id,),
        )["value"] == 1
        assert report.offline_assets == 1
    finally:
        service.close()


def test_present_but_unreadable_asset_is_not_treated_as_offline_history(tmp_path: Path) -> None:
    env = FlowEnv.build(tmp_path, photos=2)
    service = env.service()
    try:
        spec = semantic.semantic_spec(CLIP_MANIFEST, {})
        built = build_space(service, provider="clip", adapter=StubAdapter(spec))
        stored = profile_module.load_profile(service.catalog, str(built["profile"]["profile_id"]))
        assert stored is not None

        # Keep the catalog location PRESENT but remove its file, simulating an unreadable path
        # without allowing the scanner to rewrite the observation to MISSING.
        (env.library / "photo-000.jpg").unlink()
        report, lance = prepare_selection(service.catalog, stored)
        assert report.offline_assets == 0
        assert report.out_of_scope["UNREADABLE"] == 1
        assert report.required == report.covered == 1
        assert report.complete is False
        with pytest.raises(CatalogError) as refused:
            selection_module.require_selection_ready(report, lance=lance)
        assert refused.value.code == SELECTION_COVERAGE_INCOMPLETE
        assert refused.value.details["reason"] == "PRESENT_ASSET_UNREADABLE"
        assert refused.value.details["unreadable_present_assets"] == 1
    finally:
        service.close()


def test_offline_history_does_not_weaken_other_selection_gates(tmp_path: Path) -> None:
    from dataclasses import replace

    env = FlowEnv.build(tmp_path, photos=2)
    service = env.service()
    try:
        active_spec = semantic.semantic_spec(CLIP_MANIFEST, {})
        build_space(service, provider="clip", adapter=StubAdapter(active_spec))
        (env.library / "photo-000.jpg").unlink()
        env.rescan(service)
        candidate_spec = semantic.semantic_spec(QWEN_MANIFEST, {})
        candidate = build_space(
            service, provider="qwen", adapter=StubAdapter(candidate_spec, pytorch=True)
        )
        stored = profile_module.load_profile(service.catalog, str(candidate["profile"]["profile_id"]))
        assert stored is not None
        ready, lance = prepare_selection(service.catalog, stored)
        assert ready.offline_assets == 1
        selection_module.require_selection_ready(ready, lance=lance)

        cases = (
            (replace(ready, generation_state="BUILDING"), "SELECTION_INDEX_UNHEALTHY", "GENERATION_NOT_READY"),
            (replace(ready, covered=0, confirmed_entries=0), "SELECTION_EMPTY_GENERATION", "EMPTY_GENERATION"),
            (replace(ready, required=2, missing=1), SELECTION_COVERAGE_INCOMPLETE, "COVERAGE_MISSING"),
            (replace(ready, pending_outbox=1), SELECTION_OUTBOX_PENDING, "OUTBOX_NOT_CONFIRMED"),
            (replace(ready, pending_repair=1), SELECTION_OUTBOX_PENDING, "OUTBOX_NOT_CONFIRMED"),
            (replace(ready, stale_entries=1), SELECTION_OUTBOX_PENDING, "OUTBOX_NOT_CONFIRMED"),
            (replace(ready, unreadable_artifacts=("broken-artifact",)), "SELECTION_ARTIFACT_UNREADABLE", "ARTIFACT_UNREADABLE"),
            (replace(ready, out_of_scope={"UNREADABLE": 1}), SELECTION_COVERAGE_INCOMPLETE, "PRESENT_ASSET_UNREADABLE"),
        )
        for report, expected_code, expected_reason in cases:
            with pytest.raises(CatalogError) as refused:
                selection_module.require_selection_ready(report, lance=lance)
            assert refused.value.code == expected_code
            assert refused.value.details["reason"] == expected_reason

        with pytest.raises(CatalogError) as unhealthy:
            selection_module.require_selection_ready(
                ready, lance={"state": "unhealthy", "reason": "ROW_COUNT_MISMATCH"}
            )
        assert unhealthy.value.code == "SELECTION_INDEX_UNHEALTHY"
        assert unhealthy.value.details["reason"] == "ROW_COUNT_MISMATCH"
    finally:
        service.close()


def test_selection_precondition_rejects_offline_asset_restored_after_preparation(tmp_path: Path) -> None:
    env = FlowEnv.build(tmp_path, photos=2)
    service = env.service()
    try:
        active_spec = semantic.semantic_spec(CLIP_MANIFEST, {})
        active = build_space(service, provider="clip", adapter=StubAdapter(active_spec))
        (env.library / "photo-000.jpg").unlink()
        env.rescan(service)

        candidate_spec = semantic.semantic_spec(QWEN_MANIFEST, {})
        candidate = build_space(
            service, provider="qwen", adapter=StubAdapter(candidate_spec, pytorch=True)
        )
        candidate_profile_id = str(candidate["profile"]["profile_id"])
        stored = profile_module.load_profile(service.catalog, candidate_profile_id)
        assert stored is not None
        prepared, lance = prepare_selection(service.catalog, stored)
        assert prepared.offline_assets == 1
        assert prepared.required == prepared.covered == 1
        assert prepared.complete is True
        selection_module.require_selection_ready(prepared, lance=lance)

        # Restoring and scanning adds the asset back to the online required set after the
        # explicit check; the prepared in-transaction guard must reject that stale report.
        write_jpeg(env.library / "photo-000.jpg", noise=1)
        env.rescan(service)
        with pytest.raises(CatalogError) as refused:
            profile_module.activate_profile(
                service.catalog,
                profile_id=candidate_profile_id,
                precondition=selection_module.selection_precondition(prepared, lance=lance),
            )
        assert refused.value.details["reason"] == "CATALOG_CHANGED_IN_TRANSACTION"
        assert profile_module.active_profile(service.catalog).profile_id == str(
            active["profile"]["profile_id"]
        )

        fresh, fresh_lance = prepare_selection(service.catalog, stored)
        assert fresh.offline_assets == 0
        assert fresh.required == 2 and fresh.covered == 1 and fresh.missing == 1
        with pytest.raises(CatalogError) as still_missing:
            selection_module.require_selection_ready(fresh, lance=fresh_lance)
        assert still_missing.value.details["reason"] == "COVERAGE_MISSING"
    finally:
        service.close()


def test_stopped_cli_rechecks_and_activates_candidate_with_offline_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from photo_search.models.embedding.base import provider_id_of_processor

    env = FlowEnv.build(tmp_path, photos=2)
    service = env.service()
    try:
        active_spec = semantic.semantic_spec(CLIP_MANIFEST, {})
        active = build_space(service, provider="clip", adapter=StubAdapter(active_spec))
        (env.library / "photo-000.jpg").unlink()
        env.rescan(service)
        candidate_spec = semantic.semantic_spec(QWEN_MANIFEST, {})
        candidate = build_space(
            service, provider="qwen", adapter=StubAdapter(candidate_spec, pytorch=True)
        )
        candidate_profile_id = str(candidate["profile"]["profile_id"])
        assert profile_module.active_profile(service.catalog).profile_id == str(
            active["profile"]["profile_id"]
        )
    finally:
        service.close()

    provider_id = provider_id_of_processor(str(candidate_spec.processor_id))
    entry = SimpleNamespace(
        provider_id=provider_id,
        installed=True,
        identity=SimpleNamespace(
            space_id=str(candidate_spec.envelope["space_id"]),
            processor_id=str(candidate_spec.processor_id),
            dimension=int(candidate_spec.envelope["dimension"]),
        ),
        model_dir=tmp_path,
        manifest_path=QWEN_MANIFEST,
        device_policy="CPU_ALLOWED",
    )
    registry = SimpleNamespace(require_installed=lambda _provider_id: entry)
    monkeypatch.setattr(embedding_cli, "registry_for", lambda _root: registry)

    class StubRuntime:
        def health(self) -> dict[str, Any]:
            return {"loaded": True, "synthetic": True}

        def close(self) -> None:
            return None

    monkeypatch.setattr(embedding_cli, "build_worker_query_encoder", lambda **_kwargs: StubRuntime())
    monkeypatch.setattr(embedding_cli, "_candidate_health", lambda _encoder: {"synthetic": True})
    monkeypatch.setattr(
        embedding_cli,
        "_record_candidate_runtime",
        lambda _catalog, _entry, _health: (candidate_spec, {"synthetic": True}),
    )

    selected = embedding_cli._select_profile(
        SimpleNamespace(data_root=env.data_root, stopped=True, profile=candidate_profile_id)
    )
    assert selected["status"] == "ok"
    assert selected["changed"] is True
    assert selected["coverage"]["required"] == selected["coverage"]["covered"] == 1
    assert selected["coverage"]["offline_assets"] == 1
    with env.open_catalog() as reopened:
        assert profile_module.active_profile(reopened).profile_id == candidate_profile_id


def test_stopped_cli_refuses_candidate_when_offline_asset_is_restored_before_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from photo_search.models.embedding.base import provider_id_of_processor

    env = FlowEnv.build(tmp_path, photos=2)
    service = env.service()
    try:
        active_spec = semantic.semantic_spec(CLIP_MANIFEST, {})
        active = build_space(service, provider="clip", adapter=StubAdapter(active_spec))
        (env.library / "photo-000.jpg").unlink()
        env.rescan(service)
        candidate_spec = semantic.semantic_spec(QWEN_MANIFEST, {})
        candidate = build_space(
            service, provider="qwen", adapter=StubAdapter(candidate_spec, pytorch=True)
        )
        candidate_profile_id = str(candidate["profile"]["profile_id"])
        assert profile_module.active_profile(service.catalog).profile_id == str(
            active["profile"]["profile_id"]
        )
    finally:
        service.close()

    # The explicit check was valid while one asset was offline. The stopped CLI must take a
    # fresh snapshot after restoration/rescan and refuse the now-incomplete online generation.
    write_jpeg(env.library / "photo-000.jpg", noise=1)
    service = env.service()
    try:
        env.rescan(service)
        restored_profile = profile_module.load_profile(service.catalog, candidate_profile_id)
        assert restored_profile is not None
        restored, restored_lance = prepare_selection(service.catalog, restored_profile)
        assert restored.offline_assets == 0
        assert restored.required == 2 and restored.covered == 1 and restored.missing == 1
    finally:
        service.close()

    provider_id = provider_id_of_processor(str(candidate_spec.processor_id))
    entry = SimpleNamespace(
        provider_id=provider_id,
        installed=True,
        identity=SimpleNamespace(
            space_id=str(candidate_spec.envelope["space_id"]),
            processor_id=str(candidate_spec.processor_id),
            dimension=int(candidate_spec.envelope["dimension"]),
        ),
        model_dir=tmp_path,
        manifest_path=QWEN_MANIFEST,
        device_policy="CPU_ALLOWED",
    )
    registry = SimpleNamespace(require_installed=lambda _provider_id: entry)
    monkeypatch.setattr(embedding_cli, "registry_for", lambda _root: registry)

    class StubRuntime:
        def health(self) -> dict[str, Any]:
            return {"loaded": True, "synthetic": True}

        def close(self) -> None:
            return None

    monkeypatch.setattr(embedding_cli, "build_worker_query_encoder", lambda **_kwargs: StubRuntime())
    monkeypatch.setattr(embedding_cli, "_candidate_health", lambda _encoder: {"synthetic": True})
    monkeypatch.setattr(
        embedding_cli,
        "_record_candidate_runtime",
        lambda _catalog, _entry, _health: (candidate_spec, {"synthetic": True}),
    )

    with pytest.raises(CatalogError) as refused:
        embedding_cli._select_profile(
            SimpleNamespace(data_root=env.data_root, stopped=True, profile=candidate_profile_id)
        )
    assert refused.value.details["reason"] == "COVERAGE_MISSING"
    with env.open_catalog() as reopened:
        assert profile_module.active_profile(reopened).profile_id == str(
            active["profile"]["profile_id"]
        )


def test_check_and_switch_api_accept_complete_online_coverage_with_offline_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from httpx import ASGITransport, AsyncClient

    from photo_search.artifacts.store import generation_id_for
    from photo_search.models.embedding import registry as registry_module
    from photo_search.models.embedding.base import provider_id_of_processor

    env = FlowEnv.build(tmp_path, photos=2)
    service = env.service()
    try:
        active_spec = semantic.semantic_spec(CLIP_MANIFEST, {})
        active = build_space(service, provider="clip", adapter=StubAdapter(active_spec))
        (env.library / "photo-000.jpg").unlink()
        env.rescan(service)
        candidate_spec = semantic.semantic_spec(QWEN_MANIFEST, {})
        candidate = build_space(
            service, provider="qwen", adapter=StubAdapter(candidate_spec, pytorch=True)
        )
        candidate_profile_id = str(candidate["profile"]["profile_id"])
        generation_id = generation_id_for(
            str(candidate_spec.envelope["space_id"]), str(candidate_spec.processor_id)
        )
        assert profile_module.active_profile(service.catalog).profile_id == str(
            active["profile"]["profile_id"]
        )
    finally:
        service.close()

    entry = SimpleNamespace(
        provider_id=provider_id_of_processor(str(candidate_spec.processor_id)),
        installed=True,
        identity=SimpleNamespace(
            space_id=str(candidate_spec.envelope["space_id"]),
            processor_id=str(candidate_spec.processor_id),
        ),
    )
    registry = SimpleNamespace(require_installed=lambda provider_id: entry)
    monkeypatch.setattr(registry_module, "registry_for", lambda _root: registry)
    settings = ApiSettings(
        data_root=env.data_root,
        bind_host="127.0.0.1",
        bind_port=80,
        session_token=DEFAULT_SESSION_TOKEN,
        allowed_hosts=ALLOWED_HOSTS,
    )
    app = create_app(settings=settings, service_factory=env.service)

    async def scenario() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1",
            follow_redirects=False,
        ) as client:
            session = await client.get("/api/v1/session", params={"token": DEFAULT_SESSION_TOKEN})
            assert session.status_code == 200, session.text
            headers = {"X-CSRF-Token": session.json()["csrf_token"], "Origin": "http://127.0.0.1"}

            checked = await client.post(
                f"/api/v1/embedding-management/providers/{entry.provider_id}/check", headers=headers
            )
            assert checked.status_code == 200, checked.text
            body = checked.json()
            assert body["status"] == "ok"
            assert body["generation_id"] == generation_id
            assert body["profile_id"] == candidate_profile_id
            assert body["coverage"]["required"] == body["coverage"]["covered"] == 1
            assert body["coverage"]["offline_assets"] == 1
            assert body["switch_eligible"] is True
            assert body["selection_error"] is None

            scheduled = await client.post(
                f"/api/v1/embedding-management/providers/{entry.provider_id}/switch",
                headers=headers,
                json={"profile_id": candidate_profile_id, "request_id": "offline-selection", "confirmed": True},
            )
            assert scheduled.status_code == 200, scheduled.text
            assert scheduled.json()["status"] == "pending"
            assert scheduled.json()["target_profile"]["profile_id"] == candidate_profile_id

    try:
        asyncio.run(scenario())
    finally:
        app.state.api.close()


def test_check_and_switch_api_reject_profile_identity_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import sqlite3

    from httpx import ASGITransport, AsyncClient

    from photo_search.config import catalog_db_path
    from photo_search.models.embedding import registry as registry_module
    from photo_search.models.embedding.base import provider_id_of_processor
    from photo_search.models.embedding.offline_transition import transition_status

    env = FlowEnv.build(tmp_path, photos=1)
    service = env.service()
    try:
        candidate_spec = semantic.semantic_spec(QWEN_MANIFEST, {})
        candidate = build_space(
            service, provider="qwen", adapter=StubAdapter(candidate_spec, pytorch=True)
        )
        candidate_profile_id = str(candidate["profile"]["profile_id"])
    finally:
        service.close()

    # Publish a malformed immutable profile identity while leaving the generation and vectors
    # intact. Normal writes cannot mutate an existing frozen SearchProfile row.
    mismatched_profile_id = f"{candidate_profile_id}-mismatch"
    with sqlite3.connect(catalog_db_path(env.data_root)) as connection:
        profile_row = connection.execute(
            "SELECT space_id,image_generation_id,query_preprocess_version,query_parser_version,"
            "fts_tokenizer_version,fts_generation_id,reranker_processor_id,fusion_config_json,"
            "runtime_package_id,created_at FROM search_profiles WHERE profile_id=?",
            (candidate_profile_id,),
        ).fetchone()
        assert profile_row is not None
        connection.execute(
            "INSERT INTO search_profiles (profile_id,embedding_processor_id,space_id,"
            "image_generation_id,query_preprocess_version,query_parser_version,"
            "fts_tokenizer_version,fts_generation_id,reranker_processor_id,"
            "fusion_config_json,runtime_package_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (mismatched_profile_id, "foreign-processor", *profile_row[:-1], int(profile_row[-1]) + 1),
        )

    entry = SimpleNamespace(
        provider_id=provider_id_of_processor(str(candidate_spec.processor_id)),
        installed=True,
        identity=SimpleNamespace(
            space_id=str(candidate_spec.envelope["space_id"]),
            processor_id=str(candidate_spec.processor_id),
        ),
    )
    registry = SimpleNamespace(require_installed=lambda _provider_id: entry)
    monkeypatch.setattr(registry_module, "registry_for", lambda _root: registry)
    settings = ApiSettings(
        data_root=env.data_root,
        bind_host="127.0.0.1",
        bind_port=80,
        session_token=DEFAULT_SESSION_TOKEN,
        allowed_hosts=ALLOWED_HOSTS,
    )
    app = create_app(settings=settings, service_factory=env.service)

    async def scenario() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1",
            follow_redirects=False,
        ) as client:
            session = await client.get("/api/v1/session", params={"token": DEFAULT_SESSION_TOKEN})
            assert session.status_code == 200, session.text
            headers = {"X-CSRF-Token": session.json()["csrf_token"], "Origin": "http://127.0.0.1"}
            check_url = f"/api/v1/embedding-management/providers/{entry.provider_id}/check"
            switch_url = f"/api/v1/embedding-management/providers/{entry.provider_id}/switch"

            checked = await client.post(check_url, headers=headers)
            assert checked.status_code == 200, checked.text
            body = checked.json()
            assert body["switch_eligible"] is False
            assert body["selection_error"]["details"]["reason"] == (
                "CANDIDATE_PROFILE_BINDING_MISMATCH"
            )

            refused = await client.post(
                switch_url,
                headers=headers,
                json={"profile_id": candidate_profile_id, "request_id": "mismatch", "confirmed": True},
            )
            assert refused.status_code == 409, refused.text
            assert refused.json()["code"] == "PROFILE_GENERATION_MISMATCH"
            assert refused.json()["details"]["selection_error"]["details"]["reason"] == (
                "CANDIDATE_PROFILE_BINDING_MISMATCH"
            )
            assert transition_status(env.data_root)["pending"] is None

    try:
        asyncio.run(scenario())
    finally:
        app.state.api.close()


def test_non_image_content_with_an_image_extension_never_blocks_the_selection(tmp_path: Path) -> None:
    """The selection gate excludes non-image content even with an allowed image extension.

    The scanner excludes normal video extensions before registration. An image extension can
    still conceal video content, so the frozen media policy must exclude and report that
    content. Every indexable image must still be covered, and a missing image still refuses.
    """
    env = FlowEnv.build(tmp_path)
    service = env.service()
    try:
        # An allowed suffix reaches the content gate; the ISO-BMFF bytes are not an image.
        (env.library / "movie-disguised.jpg").write_bytes(
            b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2mp41"
        )
        env.rescan(service)
        assert service.catalog.read_one("SELECT COUNT(*) AS value FROM assets")["value"] == 4

        spec = semantic.semantic_spec(CLIP_MANIFEST, {})
        adapter = StubAdapter(spec)
        built = build_space(service, provider="clip", adapter=adapter)
        assert built["build"]["targets"] == 3
        assert built["build"]["out_of_scope"]["labels"] == {"ISO-BMFF": 1}

        stored = profile_module.load_profile(
            service.catalog, str(built["profile"]["profile_id"])
        )
        assert stored is not None
        report, lance = prepare_selection(service.catalog, stored)
        assert report.required == 3  # the video is not part of "what this index is about"
        assert report.covered == 3
        assert report.out_of_scope == {"ISO-BMFF": 1}
        assert report.missing == 0
        selection_module.require_selection_ready(report, lance=lance)

        # A photo added afterwards still refuses the switch until it is encoded.
        env.add_photo("photo-004.jpg", noise=7)
        env.rescan(service)
        report, lance = prepare_selection(service.catalog, stored)
        assert report.required == 4 and report.missing == 1
        with pytest.raises(CatalogError) as refused:
            selection_module.require_selection_ready(report, lance=lance)
        assert refused.value.code == SELECTION_COVERAGE_INCOMPLETE
        assert refused.value.details["reason"] == "COVERAGE_MISSING"
    finally:
        service.close()


def test_the_selected_provider_serves_search_and_reports_the_strong_match_statistic(
    tmp_path: Path,
) -> None:
    import asyncio

    env = FlowEnv.build(tmp_path)
    service = env.service()
    qwen_spec = semantic.semantic_spec(QWEN_MANIFEST, {})
    try:
        qwen = StubAdapter(qwen_spec, pytorch=True)
        candidate = build_space(service, provider="qwen", adapter=qwen)
        activate_candidate(
            service.catalog, str(candidate["profile"]["profile_id"]), str(qwen_spec.envelope["space_id"])
        )
    finally:
        service.close()

    settings = ApiSettings(
        data_root=env.data_root,
        bind_host="127.0.0.1",
        bind_port=80,
        session_token=DEFAULT_SESSION_TOKEN,
        allowed_hosts=ALLOWED_HOSTS,
    )
    adapter = StubAdapter(qwen_spec, pytorch=True)
    app = create_app(
        settings=settings,
        service_factory=env.service,
        encoder_factory=lambda: adapter,
    )

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            session = await client.get(
                "/api/v1/session", params={"token": DEFAULT_SESSION_TOKEN}
            )
            assert session.status_code == 200, session.text
            csrf = str(session.json()["csrf_token"])
            headers = {"X-CSRF-Token": csrf, "Origin": "http://127.0.0.1"}
            capabilities = await client.get("/api/v1/capabilities")
            assert capabilities.status_code == 200, capabilities.text
            search = await client.post(
                "/api/v1/search",
                json={"request_id": "p4-embedding-arch", "text": "海边日落", "page_size": 5},
                headers=headers,
            )
            assert search.status_code == 200, search.text
            return capabilities.json(), search.json()

    capabilities, body = asyncio.run(scenario())
    model = capabilities["search"]["active_embedding_model"]
    assert model["provider_id"] == "qwen3-vl-embedding-2b"
    assert model["adapter"] == "qwen3_vl"
    assert model["dimension"] == DIMENSION
    assert model["strong_match_available"] is True
    assert capabilities["search"]["active_image_generation_id"] == str(
        candidate["build"]["generation"]["generation_id"]
    )
    vector = body["channels"]["vector"]
    assert vector["engaged"] is True
    assert vector["strong_match_available"] is True
    assert 0 <= vector["strong_match_count"] <= vector["candidates"]
    app.state.api.close()
