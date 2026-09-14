"""Seed the ``%TEMP%`` fixture for the P2-03C place-page browser smoke checklist.

The checklist (``docs/diagnostics/frontend-smoke-checklist.md`` §"地点页") needs the same
starting point every time it is executed by hand: a synthetic library full of GPS JPEGs, a
scanned catalog, the signed-in synthetic city package activated, real GPS facts and real
nearby-city suggestions, and a small directory with two same-name places under different
parents.

It creates only synthetic images inside its own work directory, never starts the server,
never opens a browser, never uses the network, and never reads, moves or deletes a real
photo library or a real place dataset.

Usage::

    uv run --no-sync python tools/diagnostics/places_smoke_fixture.py --photos 12 --reset
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from PIL import Image

from photo_search.geo import dataset as dataset_module
from photo_search.geo import extract as geo_extract
from photo_search.geo import resolver as resolver_module
from photo_search.ingestion.service import CatalogService
from photo_search.jobs import queue as jobs
from photo_search.places import datasets as datasets_module
from photo_search.places.service import PlaceService
from photo_search.scheduler import engine
from photo_search.scheduler.contracts import RuntimeCapabilities

DEFAULT_WORK_DIRNAME = "P2-03C 地点页验收"
LIBRARY_DIRNAME = "照片库 中文 空格"
DATA_ROOT_DIRNAME = "PhotoSearchData 数据"
PACKAGE_SOURCE_DIRNAME = "mini-cities-package"

#: Files are stamped this far in the past so a scan never waits for the quiet period.
AGE_MS = 3_600_000

#: Positions near Hangzhou; the distances are small enough for the default 200 km radius.
HANGZHOU_SAMPLES: tuple[tuple[float, float], ...] = tuple(
    (30.2741 + (index % 4) * 0.01, 120.1551 + (index // 4) * 0.01) for index in range(12)
)
#: A position in the East China Sea: a successful run with no candidate at all.
SEA_POSITION = (29.0, 125.5)

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_SPEC = REPO_ROOT / "tools" / "places" / "examples" / "mini-cities.spec.json"

PY_PROVIDER = "python-stdlib"


def _colour(index: int) -> tuple[int, int, int]:
    return (index % 256, (index // 256) % 256, 77)


def _stamp(path: Path) -> None:
    moment = (int(time.time() * 1000) - AGE_MS) * 1_000_000
    os.utime(path, ns=(moment, moment))


def _degrees_dms(value: float) -> tuple[float, float, float]:
    degrees = int(value)
    minutes_full = (value - degrees) * 60.0
    minutes = int(minutes_full)
    seconds = (minutes_full - minutes) * 60.0
    return (float(degrees), float(minutes), round(seconds, 6))


def write_gps_jpeg(path: Path, *, index: int, position: tuple[float, float] | None) -> None:
    """A real JPEG with a real EXIF GPS IFD, or a plain JPEG when ``position`` is ``None``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (96, 72), _colour(index))
    exif = Image.Exif()
    exif[0x0112] = 1
    if position is not None:
        latitude, longitude = position
        exif[0x8825] = {
            1: "N" if latitude >= 0 else "S",
            2: _degrees_dms(abs(latitude)),
            3: "E" if longitude >= 0 else "W",
            4: _degrees_dms(abs(longitude)),
            18: "WGS84",
        }
    image.save(path, "JPEG", quality=92, exif=exif)
    _stamp(path)


def seed_library(library: Path, *, photos: int, sea: bool, no_gps: bool) -> dict[str, Any]:
    library.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for index in range(int(photos)):
        relative = f"杭州 样片/p{index:03d}.jpg"
        write_gps_jpeg(
            library / relative, index=index, position=HANGZHOU_SAMPLES[index % len(HANGZHOU_SAMPLES)]
        )
        written.append(relative)
    if sea:
        write_gps_jpeg(library / "海上 样片/sea.jpg", index=900, position=SEA_POSITION)
        written.append("海上 样片/sea.jpg")
    if no_gps:
        write_gps_jpeg(library / "无 GPS/plain.jpg", index=901, position=None)
        written.append("无 GPS/plain.jpg")
    return {"photos": len(written), "files": written}


def python_capability(now_ms: int, *, runtime_package_id: str) -> RuntimeCapabilities:
    """The same CPU-only capability shape the P2-03B suites use, for this tool only.

    The runtime package id must be the one the processor specs declare, otherwise the
    scheduler honestly blocks the job with ``RUNTIME_CAPABILITY_UNAVAILABLE``.
    """
    return RuntimeCapabilities.seal(
        {
            "runtime_package_id": str(runtime_package_id),
            "runtime_backend": "python",
            "runtime_package_name": "photo-search-diagnostic-runtime",
            "runtime_package_version": "1",
            "python_version": "3.12.10",
            "platform": "windows-x86_64",
            "driver_version": None,
            "providers": [
                {
                    "name": PY_PROVIDER,
                    "device": "cpu",
                    "precisions": ["not_applicable"],
                    "available": True,
                    "health": "healthy",
                    "health_checked_at": now_ms,
                    "evidence_id": None,
                }
            ],
            "health_status": "healthy",
            "health_checked_at": now_ms,
            "measured_at": now_ms,
            "evidence_ids": [],
            "failure_code": None,
        }
    )


def build_scheduler(catalog: Any) -> engine.Scheduler:
    specs = [geo_extract.gps_processor_spec(), resolver_module.resolver_processor_spec()]
    scheduler = engine.Scheduler(catalog, specs, lease_seconds=60)
    scheduler.record_capability(
        python_capability(1_000, runtime_package_id=specs[0].runtime_package_id), now_ms=1_000
    )
    for spec in specs:
        scheduler.record_qualification(
            processor_id=spec.processor_id,
            spec_revision=spec.spec_revision,
            device="cpu",
            precision=spec.supported_precisions[0],
            provider_name=PY_PROVIDER,
            runtime_package_id=spec.runtime_package_id,
            now_ms=1_000,
        )
    return scheduler


def run_pipeline(service: CatalogService, *, data_root: Path) -> dict[str, int]:
    """Run the real GPS and nearby-city processors over every scanned asset."""
    catalog = service.catalog
    rows = catalog.connection.execute(
        "SELECT a.asset_id AS asset_id, a.sha256 AS sha256 FROM assets AS a "
        "JOIN file_locations AS l ON l.asset_id = a.asset_id ORDER BY l.relative_path"
    ).fetchall()
    assets = [(int(row["asset_id"]), str(row["sha256"])) for row in rows]
    scheduler = build_scheduler(catalog)

    def enqueue(task_type: str, processor_id: str, asset_id: int, sha: str) -> None:
        with catalog.write() as connection:
            jobs.enqueue(
                connection,
                subject_type="asset",
                subject_id=str(asset_id),
                input_revision=sha,
                task_type=task_type,
                processor_id=processor_id,
                now_ms=1_000,
            )

    # One task type at a time: a scheduler drains every runnable job it can see, so the GPS
    # execution must not be offered the resolver jobs (and the other way round).
    for asset_id, sha in assets:
        enqueue(geo_extract.GPS_TASK_TYPE, geo_extract.GPS_PROCESSOR_ID, asset_id, sha)
    scheduler.run_until_idle(
        geo_extract.gps_executor(catalog, clock=catalog.now_ms), worker_id="w1", now_ms=1_100
    )
    for asset_id, sha in assets:
        enqueue(
            resolver_module.RESOLVER_TASK_TYPE,
            resolver_module.RESOLVER_PROCESSOR_ID,
            asset_id,
            sha,
        )
    scheduler.run_until_idle(
        resolver_module.resolver_executor(
            catalog, data_root=data_root, clock=catalog.now_ms
        ),
        worker_id="w1",
        now_ms=1_200,
    )
    return {
        "assets": len(assets),
        "gps_facts": int(
            catalog.connection.execute("SELECT COUNT(*) FROM asset_geo_facts").fetchone()[0]
        ),
        "resolutions": int(
            catalog.connection.execute("SELECT COUNT(*) FROM place_resolutions").fetchone()[0]
        ),
    }


def install_dataset(catalog: Any, *, data_root: Path, work_dir: Path) -> dict[str, Any]:
    """Build the signed-in synthetic package from its spec and activate it."""
    spec = json.loads(EXAMPLE_SPEC.read_text(encoding="utf-8"))
    source = work_dir / PACKAGE_SOURCE_DIRNAME
    if source.exists():
        shutil.rmtree(source)
    dataset_module.build_package(spec, source)
    staged = datasets_module.stage_package(catalog, source)
    validated = datasets_module.validate_dataset(catalog, str(staged["dataset_id"]))
    datasets_module.activate_dataset(catalog, str(validated["dataset_id"]))
    status = datasets_module.dataset_status(catalog.connection, data_root=data_root)
    package_dir = datasets_module.dataset_dir(data_root, str(validated["dataset_id"]))
    return {
        "dataset_id": str(validated["dataset_id"]),
        "package_version": str(validated["package_version"]),
        "record_count": int(validated["record_count"]),
        "state": str(status["state"]),
        "resolver_available": bool(status["resolver_available"]),
        "package_dir": str(package_dir),
        "places_file": str(package_dir / "places.jsonl"),
    }


def seed_directory(service: CatalogService) -> dict[str, Any]:
    """A tiny directory with two same-name places under different parents."""
    places = PlaceService(service.catalog)
    country_row = service.catalog.connection.execute(
        "SELECT place_id FROM places WHERE kind = 'country' ORDER BY place_id LIMIT 1"
    ).fetchone()
    country_id = None if country_row is None else int(country_row["place_id"])
    first = places.create_place(kind="city", canonical_name="甲市", parent_place_id=country_id)
    second = places.create_place(kind="city", canonical_name="乙市", parent_place_id=country_id)
    left = places.create_place(
        kind="custom", canonical_name="同名地点", parent_place_id=int(first["place_id"])
    )
    right = places.create_place(
        kind="custom", canonical_name="同名地点", parent_place_id=int(second["place_id"])
    )
    target = places.create_place(
        kind="custom", canonical_name="我的标记", parent_place_id=int(left["place_id"])
    )
    return {
        "country_place_id": country_id,
        "city_甲市": int(first["place_id"]),
        "city_乙市": int(second["place_id"]),
        "same_name_under_甲市": int(left["place_id"]),
        "same_name_under_乙市": int(right["place_id"]),
        "custom_target": int(target["place_id"]),
    }


def checklist_steps() -> list[str]:
    return [
        "1 打开启动器链接 → 303 落到 /，点导航「地点整理」→ 打开 /places（无外链、无本机路径）",
        "2 新建 city 与 custom 各一个 → 目录出现；用「编辑此地点」改名（成功）后重试用同一旧"
        " revision 提交 → 提示当前 revision 并要求重新载入",
        "3 用「名称或别名」搜「同名地点」→ 两条同名记录靠祖先链与 #place_id 区分，无自动合并；"
        "点「只看它的后代」验证 ancestor 过滤",
        "4 建议面板加载 → 出现 12 条待确认建议（海上/无 GPS 的照片不在列表）；"
        "点某条「查看候选与当前归属」→ 候选标注 SUGGESTION_ONLY；点「确认第一条建议」→"
        " 该资产离开建议列表，控制行 origin=CONFIRMED_SUGGESTION",
        "5 勾选 2 条以上 →「批量确认归属」（先搜索或填入目标 place_id）→ 逐批结果显示"
        " operation 与 changed；把目标 place_id 留空再点一次 → 前端拒绝且不发请求",
        "6 每页条数选 10 →「加载 / 重新加载」拿到 revision；在另一个标签页确认一条建议后，"
        "回到本页点「加载更多（沿用已绑定 revision）」→ 提示「建议集合已变化，需重新加载」",
        "7 最近操作列表 →「撤销此操作」成功并在「撤销结果」显示新操作；对一条目标已被后续编辑的"
        "操作撤销 → 显示 PLACE_OPERATION_NOT_UNDOABLE 并刷新目录/建议",
        "8 删除活动包文件（报告里打印的 places.jsonl）后点「刷新地名包状态」→ state=missing、"
        "reason=PLACE_DATASET_MISSING；目录编辑与人工归属仍可用，建议列表为空而不是报错",
        "9 窄视口约 640×900 + Tab/Enter 走完 2、4、7 的主要动作；无横向滚动",
        "10 页面与网络响应中不出现盘符路径与精确坐标（坐标只在 /api/v1/assets/{id}/geo）",
    ]


def force_utf8_when_piped() -> None:
    if sys.stdout.isatty():
        return
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - defensive
        pass


def _within(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:  # pragma: no cover - defensive
        return False


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path(tempfile.gettempdir()) / DEFAULT_WORK_DIRNAME,
        help=f"fixture root (defaults to %%TEMP%%\\{DEFAULT_WORK_DIRNAME})",
    )
    parser.add_argument("--photos", type=int, default=12, help="number of GPS photos to seed")
    parser.add_argument("--no-sea", action="store_true", help="do not add the sea photo")
    parser.add_argument("--no-gps", action="store_true", help="do not add the GPS-free photo")
    parser.add_argument("--reset", action="store_true", help="remove the previous fixture first")
    parser.add_argument("--json", action="store_true", help="print only the JSON report")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    force_utf8_when_piped()
    args = parse_args(argv)
    work_dir = Path(args.work_dir).resolve()
    library = work_dir / LIBRARY_DIRNAME
    data_root = work_dir / DATA_ROOT_DIRNAME
    work_dir.mkdir(parents=True, exist_ok=True)

    removed: list[str] = []
    if args.reset:
        for target in (library, data_root, work_dir / PACKAGE_SOURCE_DIRNAME):
            if target.exists() and _within(target, work_dir):
                shutil.rmtree(target)
                removed.append(str(target))

    contents = seed_library(
        library, photos=args.photos, sea=not args.no_sea, no_gps=not args.no_gps
    )
    service = CatalogService.open(data_root=data_root)
    try:
        source = service.register_source(display_name="地点页 样片库", root_path=library)
        service.run_scan(int(source["source_id"]))
        dataset = install_dataset(service.catalog, data_root=data_root, work_dir=work_dir)
        counts = run_pipeline(service, data_root=data_root)
        directory = seed_directory(service)
        suggested = int(
            service.catalog.connection.execute(
                "SELECT COUNT(*) FROM assets AS a WHERE NOT EXISTS (SELECT 1 FROM "
                "asset_place_assignments AS ap WHERE ap.asset_id = a.asset_id AND ap.mode = "
                "'MANUAL_ASSIGNED') AND EXISTS (SELECT 1 FROM place_resolutions AS pr WHERE "
                "pr.asset_id = a.asset_id AND pr.status = 'CANDIDATES')"
            ).fetchone()[0]
        )
    finally:
        service.close()

    report: dict[str, Any] = {
        "work_dir": str(work_dir),
        "library": str(library),
        "data_root": str(data_root),
        "removed": removed,
        "library_contents": contents,
        "dataset": dataset,
        "counts": counts,
        "assets_with_candidates": suggested,
        "directory": directory,
        "next_steps": {
            "start_server": (
                f'uv run --no-sync python -m photo_search --port <free-port> --data-root "{data_root}"'
            ),
            "source_root_to_paste": str(library),
            "checklist": "docs/diagnostics/frontend-smoke-checklist.md",
            "expected_observations": checklist_steps(),
            "note": "synthetic fixtures only; never read a real photo library or a real dataset",
        },
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if not args.json:
        print(
            "\nready: %s photos, %s GPS facts, %s resolutions, %s suggested assets under %s"
            % (
                counts["assets"],
                counts["gps_facts"],
                counts["resolutions"],
                suggested,
                library,
            )
        )
        print("next: %s" % report["next_steps"]["start_server"])
        print("checklist: %s" % report["next_steps"]["checklist"])
        print("dataset dir (for the broken-package step): %s" % dataset["package_dir"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
