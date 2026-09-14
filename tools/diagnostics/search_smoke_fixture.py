"""Seed the ``%TEMP%`` fixture for the P2-07 search-page browser smoke checklist.

The checklist (``docs/diagnostics/search-smoke-checklist.md``) needs a deterministic
starting point: a synthetic library that was scanned by the production scanner, one READY
image generation with CONFIRMED vectors for *some* of its assets (so the sentence
"语义索引仍在追赶" has something real to report), one manually assigned place, and an
activated ``SearchProfile`` so a pure-filter query is bound to that generation.

It never starts the server, never opens a browser, never uses the network, and never reads,
moves or deletes a real photo library: everything is written under its own work directory
and all images are synthetic.

Usage::

    uv run --no-sync python tools/diagnostics/search_smoke_fixture.py --photos 8 --reset
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from PIL import Image

from photo_search.artifacts.store import generation_id_for
from photo_search.indexing.store import IndexCoordinator
from photo_search.ingestion.service import CatalogService
from photo_search.jobs import queue as jobs
from photo_search.places import assignments, directory, revision
from photo_search.scheduler import engine
from photo_search.scheduler.contracts import (
    CONTRACT_VERSION,
    ProcessorSpec,
    RuntimeCapabilities,
    canonical_json,
)
from photo_search.search.profile import DEFAULT_FUSION_CONFIG, activate_profile

DEFAULT_WORK_DIRNAME = "P2-07 搜索页验收"
LIBRARY_DIRNAME = "照片库 中文 空格"
DATA_ROOT_DIRNAME = "PhotoSearchData 数据"

#: The embedding space this fixture indexes. Three dimensions keep the vectors hand-checkable.
SPACE = "smoke-space:v1"
DIMENSION = 3
PROCESSOR = "smoke-embedding@1"
RUNTIME_PACKAGE = "photo-search-diagnostic-runtime"
PROVIDER = "CPUExecutionProvider"
PRECISION = "FP32"
PROFILE_ID = "search-smoke-profile"
PLACE_NAME = "杭州西湖"

#: Files are stamped this far in the past so a scan never waits for the quiet period.
AGE_MS = 3_600_000

#: How many of the seeded assets get a CONFIRMED vector. The rest stay without an entry, so
#: the response reports them as "semantic projection still pending" instead of hiding them.
INDEXED_ASSETS = 2


def _stamp(path: Path) -> None:
    moment = (int(time.time() * 1000) - AGE_MS) * 1_000_000
    os.utime(path, ns=(moment, moment))


def _write_jpeg(path: Path, *, index: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (96, 72), (index % 256, (index * 37) % 256, 90))
    pixels = image.load()
    for x in range(image.size[0]):
        for y in range(image.size[1]):
            pixels[x, y] = ((x + index) % 256, (y * 3) % 256, (90 + index) % 256)
    image.save(path, "JPEG", quality=85)
    _stamp(path)


def seed_library(library: Path, *, photos: int) -> list[str]:
    """One synthetic photo per index; the first two names are the ones a query can find."""
    names = [f"杭州西湖 样片/p{index:03d}.jpg" for index in range(int(photos))]
    if names:
        names[0] = "杭州西湖 样片/西湖 sunset.jpg"
    if len(names) > 1:
        names[1] = "杭州西湖 样片/beach sunset.jpg"
    library.mkdir(parents=True, exist_ok=True)
    for index, relative in enumerate(names):
        _write_jpeg(library / relative, index=index)
    return names


def embedding_spec() -> ProcessorSpec:
    """The diagnostic embedding processor: cosine space, no real model."""
    return ProcessorSpec.from_envelope(
        {
            "contract_version": CONTRACT_VERSION,
            "spec_revision": "smoke-embedding-v1",
            "processor_id": PROCESSOR,
            "processor_kind": "embedding_model",
            "task_type": "image_embedding",
            "config_hash": "a" * 64,
            "input_type": "asset",
            "output_type": "embedding",
            "device_policy": "CPU_ALLOWED",
            "runtime_backend": "onnxruntime",
            "runtime_package_id": RUNTIME_PACKAGE,
            "supported_devices": ["cpu"],
            "supported_precisions": [PRECISION],
            "max_batch_size": 1,
            "cancellation_supported": True,
            "resource_measurements": [],
            "model_id": "smoke/model",
            "model_revision": "r1",
            "weights": [{"name": "smoke.onnx", "revision": "r1", "sha256": "a" * 64}],
            "image_preprocess_version": "image-v1",
            "query_preprocess_version": "query-v1",
            "tokenizer_version": "tokenizer-v1",
            "instruction_template_version": "none-v1",
            "normalization_version": "L2-v1",
            "dimension": DIMENSION,
            "metric": "cosine",
            "space_id": SPACE,
            "max_pixels": 16,
            "max_tokens": 4,
        }
    )


def capability(now_ms: int = 1_000) -> RuntimeCapabilities:
    """A healthy CPU capability for the diagnostic runtime package (no real driver)."""
    return RuntimeCapabilities.seal(
        {
            "runtime_package_id": RUNTIME_PACKAGE,
            "runtime_backend": "onnxruntime",
            "runtime_package_name": RUNTIME_PACKAGE,
            "runtime_package_version": "1",
            "python_version": "3.12.10",
            "platform": "windows-x86_64",
            "driver_version": None,
            "providers": [
                {
                    "name": PROVIDER,
                    "device": "cpu",
                    "precisions": [PRECISION],
                    "available": True,
                    "health": "healthy",
                    "health_checked_at": int(now_ms),
                    "evidence_id": None,
                }
            ],
            "health_status": "healthy",
            "health_checked_at": int(now_ms),
            "measured_at": int(now_ms),
            "evidence_ids": [],
            "failure_code": None,
        }
    )


def embedding_artifact(artifact_id: str, vector: Sequence[float]) -> dict[str, Any]:
    """One embedding artifact exactly as the P2-03 submission path stores it."""
    payload = {"vector": [float(value) for value in vector]}
    content = canonical_json(payload).encode("utf-8")
    return {
        "artifact_id": str(artifact_id),
        "artifact_type": "embedding",
        "artifact_revision": "embedding.v1",
        "media_type": "application/json",
        "checksum_sha256": hashlib.sha256(content).hexdigest(),
        "byte_size": len(content),
        "storage_uri": None,
        "payload": payload,
        "space_id": SPACE,
        "dimension": DIMENSION,
    }


def embedding_executor(vectors: dict[int, list[float]]) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """A deterministic stand-in for the embedding worker (no model, no GPU)."""

    def run(request: dict[str, Any]) -> dict[str, Any]:
        asset_id = int(request["subject_id"])
        return {
            "contract_version": CONTRACT_VERSION,
            "job_id": int(request["job_id"]),
            "lease_token": str(request["lease_token"]),
            "input_revision": str(request["input_revision"]),
            "task_type": str(request["task_type"]),
            "processor_id": str(request["processor_id"]),
            "state": "SUCCEEDED",
            "resolved_device": str(request["resolved_device"]),
            "runtime_provider": str(request["runtime_provider"]),
            "precision": str(request["precision"]),
            "progress": 1.0,
            "artifacts": [embedding_artifact(f"smoke-embedding-{asset_id}", vectors[asset_id])],
            "error": None,
            "completed_at": 1_150,
        }

    return run


def index_vectors(service: CatalogService, *, assets: Sequence[int]) -> dict[str, Any]:
    """Publish and confirm one vector per asset, then make the generation READY."""
    catalog = service.catalog
    spec = embedding_spec()
    scheduler = engine.Scheduler(catalog, [spec], lease_seconds=60)
    scheduler.record_capability(capability(1_000), now_ms=1_000)
    scheduler.record_qualification(
        processor_id=spec.processor_id,
        spec_revision=spec.spec_revision,
        device="cpu",
        precision=PRECISION,
        provider_name=PROVIDER,
        runtime_package_id=RUNTIME_PACKAGE,
        now_ms=1_000,
    )
    vectors: dict[int, list[float]] = {}
    for position, asset_id in enumerate(assets):
        vector = [0.0, 0.0, 0.0]
        vector[position % DIMENSION] = 1.0
        vectors[int(asset_id)] = vector
        with catalog.write() as connection:
            jobs.enqueue(
                connection,
                subject_type="asset",
                subject_id=str(int(asset_id)),
                input_revision=f"vec-{int(asset_id)}",
                task_type=spec.task_type,
                processor_id=spec.processor_id,
                now_ms=1_000,
            )
    scheduler.run_until_idle(embedding_executor(vectors), worker_id="w1", now_ms=1_100)
    confirmed = IndexCoordinator(catalog).process_all(now_ms=2_000)
    generation_id = generation_id_for(SPACE, PROCESSOR)
    row = catalog.read_one(
        "SELECT state, is_active FROM index_generations WHERE generation_id = ?",
        (generation_id,),
    )
    return {
        "generation_id": generation_id,
        "confirmed_events": int(confirmed),
        "generation_state": None if row is None else str(row["state"]),
        "generation_is_active": None if row is None else bool(row["is_active"]),
        "indexed_assets": sorted(vectors),
    }


def seed_place(catalog: Any, *, asset_id: int) -> int:
    """Create one city and assign it to one asset through the production write path."""
    with catalog.write() as connection:
        place_id = int(
            directory.create_place(
                connection,
                kind="city",
                canonical_name=PLACE_NAME,
                aliases=[{"text": "西湖", "language": "zh"}],
                now_ms=1_400,
            )
        )
    with catalog.write() as connection:
        assignments.apply_request(
            connection,
            items=[
                {
                    "asset_id": int(asset_id),
                    "action": "ASSIGN",
                    "target_place_id": place_id,
                    "expected_revision": 0,
                }
            ],
            request_id="search-smoke-assign-1",
            now_ms=1_500,
            refresh=revision.projection_refresher(catalog),
        )
    return place_id


def create_profile(catalog: Any, *, generation_id: str) -> dict[str, Any]:
    """Publish and activate one immutable profile over the diagnostic generation."""
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO search_profiles(profile_id, embedding_processor_id, space_id, "
            "image_generation_id, query_preprocess_version, query_parser_version, "
            "fts_tokenizer_version, fts_generation_id, reranker_processor_id, "
            "fusion_config_json, runtime_package_id, created_at) "
            "VALUES (?, ?, ?, ?, 'query-v1', 'parser-v1', '1', 'fts-v1', NULL, ?, ?, ?)",
            (
                PROFILE_ID,
                PROCESSOR,
                SPACE,
                str(generation_id),
                json.dumps(DEFAULT_FUSION_CONFIG, sort_keys=True),
                RUNTIME_PACKAGE,
                1_700_000_000_000,
            ),
        )
    profile, changed, readiness = activate_profile(
        catalog, profile_id=PROFILE_ID, now_ms=1_700_000_001_000
    )
    return {
        "profile_id": profile.profile_id,
        "changed": bool(changed),
        "readiness": {
            "device": readiness.get("device"),
            "provider_name": readiness.get("provider_name"),
            "precision": readiness.get("precision"),
        },
    }


def checklist_steps() -> list[str]:
    """The observations the search-page checklist expects, in order."""
    return [
        "1 打开启动器链接 → 303 落到 /，导航出现「搜索图片」；点它或直接打开 /search",
        "2 /search 顶部提示当前 profile 与「本进程没有查询编码器」的说明",
        "3 输入中文查询文本 → 点「搜索」→ 结构化 409 QUERY_ENCODER_UNAVAILABLE 提示，"
        "文案说明「语义通道未运行」，不是空网格；查询文本保留且出现「按当前条件重搜」",
        "4 清空查询文本（纯筛选）→ 出网格：每行显示命中原因「仅筛选」、资产编号、"
        "「语义就绪：否」与缩略图；状态行给出候选总数与是否还有下一页",
        "5 结果区出现「语义索引仍在追赶」提示与待确认向量条目计数",
        "6 点「加载下一页」→ 出现下一页行，状态行「已显示 N 行」，无重复",
        "7 地点状态选「已归类」或填入提示的 place_id → 结果收窄到已归类资产",
        "8 上传一张本地图片 → 结构化 409 IMAGE_ENCODER_UNAVAILABLE（本构建没有图像编码器），"
        "不显示任何伪造结果",
        "9 分项覆盖率：文本投影覆盖 / 语义索引覆盖 / 地点覆盖 / 来源可用性四项分别列出并各有口径说明",
        "10 窄视口（约 640px）无横向滚动；Tab 可依次聚焦输入、按钮与「查看详情」链接",
    ]


def force_utf8_when_piped() -> None:
    """Write UTF-8 when stdout is not a console, so Chinese paths survive a redirect."""
    if sys.stdout.isatty():
        return
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - defensive
        pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path(tempfile.gettempdir()) / DEFAULT_WORK_DIRNAME,
        help="fixture root (defaults to %%TEMP%%\\%s)" % DEFAULT_WORK_DIRNAME,
    )
    parser.add_argument("--photos", type=int, default=8, help="number of synthetic photos")
    parser.add_argument(
        "--reset", action="store_true", help="remove the previous fixture directories first"
    )
    parser.add_argument("--json", action="store_true", help="print only the JSON report")
    return parser.parse_args(argv)


def _within(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:  # pragma: no cover - defensive
        return False


def main(argv: list[str] | None = None) -> int:
    force_utf8_when_piped()
    args = parse_args(argv)
    work_dir = Path(args.work_dir).resolve()
    library = work_dir / LIBRARY_DIRNAME
    data_root = work_dir / DATA_ROOT_DIRNAME
    work_dir.mkdir(parents=True, exist_ok=True)
    removed: list[str] = []
    if args.reset:
        for target in (library, data_root):
            if target.exists() and _within(target, work_dir) and target != work_dir:
                shutil.rmtree(target)
                removed.append(str(target))

    names = seed_library(library, photos=max(3, int(args.photos)))
    service = CatalogService.open(data_root=data_root)
    try:
        source = service.register_source(display_name="搜索样片", root_path=library)
        service.run_scan(int(source["source_id"]))
        rows = service.catalog.read_all(
            "SELECT a.asset_id AS asset_id, l.relative_path AS relative_path FROM assets AS a "
            "JOIN file_locations AS l ON l.asset_id = a.asset_id ORDER BY l.relative_path"
        )
        assets = {str(row["relative_path"]).replace("\\", "/"): int(row["asset_id"]) for row in rows}
        ordered = [assets[name] for name in names if name in assets]
        indexed = ordered[:INDEXED_ASSETS]
        generation = index_vectors(service, assets=indexed)
        place_id = seed_place(service.catalog, asset_id=indexed[0])
        profile = create_profile(service.catalog, generation_id=str(generation["generation_id"]))
        counts = service.counts()
    finally:
        service.close()

    report: dict[str, Any] = {
        "work_dir": str(work_dir),
        "library": str(library),
        "data_root": str(data_root),
        "removed": removed,
        "assets": assets,
        "indexed_assets": indexed,
        "not_indexed_assets": [value for value in ordered if value not in indexed],
        "generation": generation,
        "place": {"place_id": place_id, "canonical_name": PLACE_NAME, "assigned_asset": indexed[0]},
        "profile": profile,
        "counts": counts,
        "next_steps": {
            "start_server": (
                "uv run --no-sync python -m photo_search --port <free-port> "
                f'--data-root "{data_root}"'
            ),
            "search_page": "/search",
            "checklist": "docs/diagnostics/search-smoke-checklist.md",
            "note": "synthetic fixtures only; no real photo library is read",
            "expected_observations": checklist_steps(),
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if not args.json:
        print(
            "\nready: %s assets (%s indexed, %s pending), place #%s, profile %s under %s"
            % (
                len(assets),
                len(indexed),
                len(ordered) - len(indexed),
                place_id,
                profile["profile_id"],
                work_dir,
            )
        )
        print("next: %s" % report["next_steps"]["start_server"])
        print("page: %s/search" % report["next_steps"]["start_server"].split("--port")[0].strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
