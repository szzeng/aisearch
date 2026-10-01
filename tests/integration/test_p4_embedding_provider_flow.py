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
    CatalogError,
)
from photo_search.config import vectors_dir
from photo_search.indexing import selection as selection_module
from photo_search.indexing import semantic
from photo_search.ingestion.service import CatalogService
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

def build_space(service: CatalogService, *, provider: str, adapter: StubAdapter) -> dict[str, Any]:
    manifest = CLIP_MANIFEST if provider == "clip" else QWEN_MANIFEST
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


def test_a_pending_outbox_or_an_offline_asset_blocks_the_selection(tmp_path: Path) -> None:
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

        # An offline file is its own refusal: the selector never guesses that a missing file
        # "probably still matches".
        offline = FlowEnv.build(tmp_path / "offline")
        offline_service = offline.service()
        try:
            offline_spec = semantic.semantic_spec(CLIP_MANIFEST, {})
            offline_adapter = StubAdapter(offline_spec)
            offline_build = build_space(offline_service, provider="clip", adapter=offline_adapter)
            (offline.library / "photo-000.jpg").unlink()
            offline.rescan(offline_service)
            report, _ = selection_module.selection_report(
                offline_service.catalog,
                ArtifactStore.open(offline_service.catalog.data_root),
                generation_id=str(offline_build["build"]["generation"]["generation_id"]),
                space_id=str(offline_spec.envelope["space_id"]),
                processor_id=str(offline_spec.processor_id),
            )
            assert report.offline_assets == 1
            with pytest.raises(CatalogError) as refused:
                selection_module.require_selection_ready(report)
            assert refused.value.code == SELECTION_COVERAGE_INCOMPLETE
            assert refused.value.details["reason"] == "OFFLINE_ASSETS_PRESENT"
        finally:
            offline_service.close()
    finally:
        service.close()


def test_a_video_in_the_library_never_blocks_the_selection(tmp_path: Path) -> None:
    """The gate is about the content *this index is about*, not about every file in the source.

    A real library holds videos, archives and files the decoder cannot identify; none of them can
    ever carry an image-text vector, so requiring them would turn a permanent property of the
    library into a permanent refusal. The frozen media policy decides, and the decision is
    reported: every indexable image must still be covered, and a missing image still refuses.
    """
    env = FlowEnv.build(tmp_path)
    service = env.service()
    try:
        # A real ISO-BMFF (mp4) header next to the photos: out of scope for this index.
        (env.library / "movie.mp4").write_bytes(
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


def test_the_selected_provider_serves_search_and_hides_the_strong_match_statistic(
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
    assert model["strong_match_available"] is False
    assert capabilities["search"]["active_image_generation_id"] == str(
        candidate["build"]["generation"]["generation_id"]
    )
    vector = body["channels"]["vector"]
    assert vector["engaged"] is True
    assert vector["strong_match_available"] is False
    app.state.api.close()
