"""Seed the ``%TEMP%`` fixtures for the P1-05 front-end smoke checklist (P1-05B).

The checklist (``docs/diagnostics/frontend-smoke-checklist.md``) needs the same fixture set
every time it is executed by hand: a Chinese/space source library, a few hundred ordinary
photos, a large ``bulk/`` group that makes a scan slow enough to observe ``RUNNING``
progress, one undecodable file, and optionally a higher-schema database. This script
creates exactly those, prints the command that starts the real local server, and prints the
expected observations for the checklist steps.

It never starts the server or a browser, never touches anything outside its own work
directory, and only ever writes synthetic images -- no real photo library is read, moved or
deleted.

Usage::

    uv run python tools/diagnostics/frontend_smoke_fixture.py --photos 60 --bulk 2000 --corrupt
    uv run python tools/diagnostics/frontend_smoke_fixture.py --offline bulk/b0007.jpg --offline p003.jpg
    uv run python tools/diagnostics/frontend_smoke_fixture.py --too-new
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image

from photo_search.catalog import schema

DEFAULT_WORK_DIRNAME = "P1-05B 前端验收"
LIBRARY_DIRNAME = "照片库 中文 空格"
DATA_ROOT_DIRNAME = "PhotoSearchData 数据"
TOO_NEW_DIRNAME = "PhotoSearchData 过新"
SUBDIR_CHINESE = "子目录 中文"
SUBDIR_DEEP = "nested/deep"
BULK_DIRNAME = "bulk"
CORRUPT_NAME = "corrupt.jpg"

#: Fixture images are stamped this far in the past so the scan never waits for the
#: quiet period; an old ``mtime`` is the normal case for a real photo library.
AGE_MS = 3_600_000

SCHEMA_VERSION_KEY = "schema_version"
#: One past what this build can write, derived instead of hard coded so the fixture cannot
#: go stale when a new migration lands (P2-02 moved the supported version to 2).
TOO_NEW_SCHEMA_VERSION = int(schema.SUPPORTED_SCHEMA_VERSION) + 1


def _photo_colour(index: int) -> tuple[int, int, int]:
    """A deterministic colour that is unique for ``index < 65536``.

    Distinct content is what makes each fixture file its own asset (the catalog keys assets
    by full SHA-256), so one photo per expected asset keeps the checklist numbers simple.
    """
    return (index % 256, (index // 256) % 256, 90)


def _write_jpeg(path: Path, *, index: int, size: tuple[int, int] = (96, 72)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, _photo_colour(index)).save(path, "JPEG", quality=85)
    moment = (int(time.time() * 1000) - AGE_MS) * 1_000_000
    os.utime(path, ns=(moment, moment))


def seed_library(library: Path, *, photos: int, bulk: int, corrupt: bool) -> dict[str, int]:
    """Create the synthetic source tree and report what it contains."""
    library.mkdir(parents=True, exist_ok=True)
    for index in range(int(photos)):
        if index % 3 == 0:
            relative = f"{SUBDIR_CHINESE}/p{index:03d}.jpg"
        elif index % 5 == 0:
            relative = f"{SUBDIR_DEEP}/p{index:03d}.jpg"
        else:
            relative = f"p{index:03d}.jpg"
        _write_jpeg(library / relative, index=index)

    bulk_root = library / BULK_DIRNAME
    for index in range(int(bulk)):
        _write_jpeg(bulk_root / f"b{index:04d}.jpg", index=1000 + index)

    if corrupt:
        broken = library / CORRUPT_NAME
        broken.write_bytes(b"x")
        moment = (int(time.time() * 1000) - AGE_MS) * 1_000_000
        os.utime(broken, ns=(moment, moment))

    files = [path for path in library.rglob("*") if path.is_file()]
    return {
        "photos": int(photos),
        "bulk": int(bulk),
        "corrupt": 1 if corrupt else 0,
        "files_total": len(files),
    }


def build_too_new_database(data_root: Path, *, version: int = TOO_NEW_SCHEMA_VERSION) -> Path:
    """A catalog whose ``schema_version`` is newer than this build supports."""
    catalog_dir = data_root / "catalog"
    catalog_dir.mkdir(parents=True, exist_ok=True)
    path = catalog_dir / "catalog.db"
    if path.exists():
        path.unlink()
    connection = sqlite3.connect(str(path))
    try:
        connection.executescript(
            "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
            f"INSERT INTO metadata(key, value) VALUES('{SCHEMA_VERSION_KEY}', '{int(version)}');"
            "CREATE TABLE future_only (id INTEGER PRIMARY KEY);"
        )
        connection.commit()
    finally:
        connection.close()
    return path


def _within(path: Path, root: Path) -> bool:
    """True when ``path`` is ``root`` or lives below it (never delete anything else)."""
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:  # pragma: no cover - defensive: an unresolvable path is not ours to touch
        return False


def reset_work_dir(work_dir: Path, targets: list[Path]) -> list[str]:
    """Remove only the fixture directories this script owns."""
    removed: list[str] = []
    for target in targets:
        if not target.exists():
            continue
        if not _within(target, work_dir) or target.resolve() == work_dir.resolve():
            print(f"refusing to remove {target}: outside {work_dir}", file=sys.stderr)
            continue
        shutil.rmtree(target)
        removed.append(str(target))
    return removed


def offline_paths(library: Path, relatives: list[str]) -> dict[str, object]:
    """Delete named originals so the offline hints can be observed without a re-scan."""
    deleted: list[str] = []
    missing: list[str] = []
    for relative in relatives:
        candidate = (library / relative).resolve()
        if not _within(candidate, library) or candidate == library.resolve():
            print(f"refusing to delete {relative}: outside the library", file=sys.stderr)
            missing.append(relative)
            continue
        if candidate.is_file():
            candidate.unlink()
            deleted.append(relative)
        else:
            missing.append(relative)
    return {"deleted": deleted, "not_found": missing}


def checklist_steps() -> list[str]:
    """The ten observations the checklist expects, in order."""
    return [
        "1 打开启动器打印的链接 → 303 落到 /，页面标题「本机图片搜索」，来源页有引导文案",
        "2 填写显示名 + 粘贴来源根目录 → 201，卡片只显示显示名与计数（不显示绝对路径）",
        "3 点「扫描」→ 先「扫描已排队」，再 RUNNING 且进度递增，最后 SUCCEEDED 100%",
        "4 切「按加入时间浏览」→ 首批 50 张、缩略图按需加载；点「加载更多」→ 100 张",
        "5 打开首图详情 → 预览、加入时间（入库时间）、全部位置、产物状态、「打开原图」链接",
        "6 用 --offline 删掉某个原图后点「刷新」→ 该图块仍显示缩略图 +「原文件不可用」",
        "7 点「打开原图」→ 409 SOURCE_FILE_MISSING（新标签显示结构化 JSON）",
        "8 打开 corrupt.jpg 详情 → 可见 DECODE_FAILED，无破图，正文无绝对路径",
        "9 窄视口（约 640px）无横向滚动；Tab/Enter 可聚焦并打开详情；图片有 alt",
        "10 用 --too-new 的数据目录启动 → 页面显示 SCHEMA_TOO_NEW 结构化提示，不是空图库",
    ]


def force_utf8_when_piped() -> None:
    """Write UTF-8 when stdout is not a console, so Chinese paths survive a redirect.

    An interactive console keeps Python's own console encoding (Chinese displays fine
    there), but a pipe or a file would otherwise be encoded with the locale code page and
    turn every copied path into mojibake.
    """
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
        # argparse re-applies ``%``-formatting to every help string, so the already
        # rendered literal has to be escaped again; without this ``--help`` raised
        # "unsupported format character 'T'".
        help=f"fixture root (defaults to %TEMP%\\{DEFAULT_WORK_DIRNAME})".replace("%", "%%"),
    )
    parser.add_argument("--photos", type=int, default=60, help="number of ordinary photos")
    parser.add_argument(
        "--bulk",
        type=int,
        default=2000,
        help="number of extra bulk/*.jpg photos (makes a scan slow enough to watch)",
    )
    parser.add_argument("--corrupt", action="store_true", help="add a 1-byte corrupt.jpg")
    parser.add_argument(
        "--too-new",
        action="store_true",
        help=(
            f"also create a schema_version={TOO_NEW_SCHEMA_VERSION} data root for the "
            "SCHEMA_TOO_NEW step"
        ),
    )
    parser.add_argument(
        "--offline",
        action="append",
        default=[],
        metavar="RELATIVE_PATH",
        help="delete this source-relative original (repeatable); relative to the library root",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="remove the previously created library/data-root directories first",
    )
    parser.add_argument("--json", action="store_true", help="print only the JSON report")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    force_utf8_when_piped()
    args = parse_args(argv)
    work_dir = Path(args.work_dir).resolve()
    library = work_dir / LIBRARY_DIRNAME
    data_root = work_dir / DATA_ROOT_DIRNAME
    too_new_root = work_dir / TOO_NEW_DIRNAME
    work_dir.mkdir(parents=True, exist_ok=True)

    removed = (
        reset_work_dir(work_dir, [library, data_root, too_new_root]) if args.reset else []
    )
    report: dict[str, object] = {
        "work_dir": str(work_dir),
        "library": str(library),
        "data_root": str(data_root),
        "removed": removed,
        "library_contents": seed_library(
            library, photos=args.photos, bulk=args.bulk, corrupt=args.corrupt
        ),
        "offline": offline_paths(library, list(args.offline)),
    }
    if args.too_new:
        report["too_new_data_root"] = str(too_new_root)
        report["too_new_catalog"] = str(build_too_new_database(too_new_root))

    steps: dict[str, object] = {
        "start_server": (
            "uv run python -m photo_search --port <free-port> "
            f'--data-root "{data_root}"'
        ),
        "source_root_to_paste": str(library),
        "checklist": "docs/diagnostics/frontend-smoke-checklist.md",
        "note": "fixtures are synthetic and live under %TEMP%; never read a real photo library",
    }
    if args.too_new:
        steps["too_new_server"] = (
            "uv run python -m photo_search --port <free-port> "
            f'--data-root "{too_new_root}"'
        )
    else:
        steps["too_new_hint"] = "pass --too-new to create the SCHEMA_TOO_NEW data root"
    steps["expected_observations"] = checklist_steps()
    report["next_steps"] = steps

    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if not args.json:
        contents = report["library_contents"]
        print(
            "\nready: %s photos + %s bulk + %s corrupt file(s) under %s"
            % (contents["photos"], contents["bulk"], contents["corrupt"], library)
        )
        print("next: %s" % report["next_steps"]["start_server"])
        print("checklist: %s" % report["next_steps"]["checklist"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
