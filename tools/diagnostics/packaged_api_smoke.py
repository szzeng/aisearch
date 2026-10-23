"""C-level smoke test for the packaged local API and front end (P1-04, extended by P1-05).

The launcher changed in P1-04 (explicit bind options, one-time session credential,
in-process ``uvicorn`` with an application object), so the packaged build is exercised for
the scenarios that only a real bundle can prove: a Chinese/space installation path, a
Chinese/space *source* root, an offline original, and the access-log credential filter.
P1-05 adds the packaged static front end: ``GET /`` must return the shell and
``GET /web/app.js`` the script, and the single-use link printed by the launcher must
redirect a real client onto that shell.

Everything is created under ``%TEMP%``; the user's real photo library is never touched.
The script only reads ``sys.argv`` and the bundle, writes its own fixtures, and prints one
JSON report on stdout.

Usage::

    uv run --no-sync python tools/diagnostics/packaged_api_smoke.py --bundle dist/PhotoSearch
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
from PIL import Image

DEFAULT_BUNDLE = Path("dist") / "PhotoSearch"
WORK_DIRNAME = "P1-05 打包页面验收"
START_TIMEOUT_SECONDS = 180.0


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def drain(stream: Any, sink: list[str]) -> None:
    for line in stream:
        sink.append(line.rstrip("\n"))


def wait_for(sink: list[str], predicate: Any, *, timeout: float) -> str | None:
    deadline = time.monotonic() + float(timeout)
    while time.monotonic() < deadline:
        for line in list(sink):
            if predicate(line):
                return line
        time.sleep(0.2)
    return None


def image_size_of(response: Any) -> list[int] | None:
    """The pixel size of a 200 image response, or ``None`` for any other answer."""
    if response is None or response.status_code != 200:
        return None
    with Image.open(io.BytesIO(response.content)) as image:
        return [int(image.width), int(image.height)]


def seed_library(root: Path) -> dict[str, Path]:
    """JPEG/HEIC controls, a same-content JPEG twin and one damaged HEIC.

    The second photo exists so the packaged search can page with a real cursor; the first
    one is the offline subject the media checks delete later. The HEIC fixture and its JPEG
    twin store the *same* portrait pixels with the same EXIF orientation 6, so the frozen
    process has to reach the same upright size for both formats (P4-00C-B §2 step 2).
    """
    target = root / "子 目录 中文" / "照片 一.jpg"
    target.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (640, 480), (180, 60, 20)).save(target, "JPEG", quality=90)
    sibling = root / "子 目录 中文" / "beach sunset.jpg"
    image = Image.new("RGB", (320, 240), (20, 60, 180))
    pixels = image.load()
    for x in range(image.size[0]):
        for y in range(image.size[1]):
            pixels[x, y] = ((20 + x) % 256, (60 + y) % 256, 180)
    image.save(sibling, "JPEG", quality=90)
    moment = (1_700_000_000_000 - 3_600_000) * 1_000_000
    os.utime(target, ns=(moment, moment))
    os.utime(sibling, ns=(moment, moment))
    # This diagnostic is deliberately the producer of its own tiny HEIC fixture.  It makes
    # the frozen executable prove its decoder, rather than merely finding a bundled DLL.
    import pillow_heif

    pillow_heif.register_heif_opener()
    orientation = Image.Exif()
    orientation[0x0112] = 6
    exif_bytes = orientation.tobytes()
    portrait = Image.new("RGB", (480, 640), (35, 145, 90))
    heic = root / "子 目录 中文" / "竖拍 HEIC.heic"
    portrait.save(heic, format="HEIF", exif=exif_bytes)
    os.utime(heic, ns=(moment, moment))
    twin = root / "子 目录 中文" / "竖拍 JPEG.jpg"
    portrait.save(twin, "JPEG", quality=92, exif=exif_bytes)
    os.utime(twin, ns=(moment, moment))
    damaged = root / "子 目录 中文" / "损坏 HEIC.heic"
    payload = heic.read_bytes()
    damaged.write_bytes(payload[: max(1, len(payload) // 2)])
    os.utime(damaged, ns=(moment, moment))
    return {"offline": target, "heic": heic, "heic_twin": twin, "damaged_heic": damaged}


def seed_index_generation(data_root: Path, *, space_id: str) -> str:
    """Insert one READY index generation into the packaged app's fixture catalog.

    P2-06A serves a *bound* search only against a generation that exists; no product API
    creates one yet (index generation management is a later task), so the smoke seeds the
    row the same way a model-upgrade run would. Nothing outside this fixture's own
    ``--data-root`` is touched.
    """
    db_path = data_root / "catalog" / "catalog.db"
    generation_id = "image-gen-smoke"
    connection = sqlite3.connect(str(db_path), timeout=30.0, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO index_generations(generation_id, space_id, processor_id, dimension, "
            "state, is_active, created_at, updated_at) VALUES (?, ?, 'smoke-embedding@1', 3, "
            "'READY', 0, 1000, 1000)",
            (generation_id, str(space_id)),
        )
        connection.execute("COMMIT")
    finally:
        connection.close()
    return generation_id


def packaged_person_schema(data_root: Path) -> dict[str, Any]:
    """The packaged catalog's person-operation vocabulary, read straight from SQLite.

    This is the C-level proof that migration ``0013`` is inside the bundle: the packaged data
    root has to carry the widened ``SPLIT``/``SPLIT_GROUP`` CHECK lists, next to the P3-04
    ``FACE_ASSIGN`` value it must not have lost. The exact schema version is checked by
    :func:`packaged_taken_time_schema`, which owns the current tip of the chain.
    """
    db_path = data_root / "catalog" / "catalog.db"
    connection = sqlite3.connect(str(db_path), timeout=30.0)
    try:
        version = connection.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        ).fetchone()[0]
        operations = str(
            connection.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'person_operations'"
            ).fetchone()[0]
        )
        items = str(
            connection.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'person_operation_items'"
            ).fetchone()[0]
        )
    finally:
        connection.close()
    return {
        "schema_version": int(version),
        "has_split": "SPLIT" in operations,
        "has_face_assign": "FACE_ASSIGN" in operations,
        "has_split_group": "SPLIT_GROUP" in items,
        "has_face_control": "FACE_CONTROL" in items,
    }


def packaged_taken_time_schema(data_root: Path) -> dict[str, Any]:
    """The packaged catalog's taken-time layer (P4-00B, ADR-0011 §2/§9).

    ``0015_taken_time.sql`` is a *packaged data resource*: a frozen build that shipped without
    it would still declare support for schema 15, so ``migration_files()`` would raise
    ``SCHEMA_INCONSISTENT`` at the first open. This check therefore proves three things about
    the packaged data root - the version, the derived table with its closed source vocabulary
    and the capture index, and the library-level rule-version key.
    """
    db_path = data_root / "catalog" / "catalog.db"
    connection = sqlite3.connect(str(db_path), timeout=30.0)
    try:
        version = connection.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        ).fetchone()[0]
        table_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'asset_metadata'"
        ).fetchone()
        rules_row = connection.execute(
            "SELECT value FROM metadata WHERE key = 'taken_time_rules_version'"
        ).fetchone()
        indexes = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = "
                "'asset_metadata'"
            )
        }
    finally:
        connection.close()
    table = "" if table_row is None else str(table_row[0])
    return {
        "schema_version": int(version),
        "has_asset_metadata": table_row is not None,
        "has_closed_sources": "EXIF_ORIGINAL" in table and "FILE_MTIME" in table,
        "has_capture_index": "idx_asset_metadata_capture" in indexes,
        "rules_version": None if rules_row is None else int(str(rules_row[0])),
    }


def seed_derived_facts(
    data_root: Path, *, offset_asset: int, plain_asset: int, offset_minutes: int = 480
) -> None:
    """Publish the taken-time/GPS facts an A-stage extraction would write (P4-00C-B §2.3).

    The frozen product has no user-facing entry point that drains the taken-time jobs
    (``python -m photo_search.taken_time.cli`` is a development entry the onedir executable
    cannot run), so the packaged claim is checked on the *serving* path: this diagnostic
    writes exactly the rows ADR-0011 §2/§4.1 and ADR-0005 §4.5 define - one with an offset
    and one without, for the same wall clock and the same coordinate - and the frozen build
    then has to publish them through its own detail endpoint and catalog.

    The civil key and the geo-fact id come from the frozen contracts themselves, so the
    fixture cannot drift from what the extractors write.
    """
    from photo_search.places.contracts import geo_fact_id_for
    from photo_search.taken_time import contract as time_contract

    civil_key = time_contract.civil_key(2022, 7, 5, 18, 31, 7, 0)
    rows = ((int(offset_asset), "HIGH", int(offset_minutes)), (int(plain_asset), "MEDIUM", None))
    connection = sqlite3.connect(str(data_root / "catalog" / "catalog.db"), timeout=30.0)
    try:
        now = 1_700_000_000_000
        for asset_id, confidence, offset in rows:
            sha = str(
                connection.execute(
                    "SELECT sha256 FROM assets WHERE asset_id = ?", (asset_id,)
                ).fetchone()[0]
            )
            location = int(
                connection.execute(
                    "SELECT location_id FROM file_locations WHERE asset_id = ? "
                    "ORDER BY location_id LIMIT 1",
                    (asset_id,),
                ).fetchone()[0]
            )
            connection.execute(
                "INSERT OR REPLACE INTO asset_metadata(asset_id, taken_at_raw, taken_at_sort, "
                "time_source, time_confidence, timezone_offset, camera) "
                "VALUES (?, ?, ?, 'EXIF_ORIGINAL', ?, ?, 'Smoke Cam')",
                (asset_id, "2022:07:05 18:31:07", int(civil_key), confidence, offset),
            )
            connection.execute(
                "INSERT OR REPLACE INTO asset_geo_facts(asset_id, geo_fact_id, location_id, "
                "input_revision, fact_revision, status, source, processor_id, "
                "raw_fields_checksum_sha256, raw_gps_json, latitude, longitude, datum_status, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, 1, 'VALID', 'EXIF_GPS', "
                "'geo-exif-gps@1', ?, ?, 30.2741, 120.1551, 'WGS84', ?, ?)",
                (
                    asset_id,
                    geo_fact_id_for(sha, 1),
                    location,
                    sha,
                    "b" * 64,
                    json.dumps({"latitude_dms": [30, 16, 26.76]}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        connection.commit()
    finally:
        connection.close()


def read_geo_facts(data_root: Path, asset_ids: list[int]) -> dict[str, dict[str, Any]]:
    """The packaged catalog's GPS facts for the given assets, keyed by asset id."""
    connection = sqlite3.connect(str(data_root / "catalog" / "catalog.db"), timeout=30.0)
    try:
        rows = connection.execute(
            "SELECT asset_id, status, datum_status, latitude, longitude FROM asset_geo_facts "
            "WHERE asset_id IN (" + ", ".join("?" for _ in asset_ids) + ")",
            tuple(int(value) for value in asset_ids),
        ).fetchall()
    finally:
        connection.close()
    return {
        str(int(row[0])): {
            "status": str(row[1]),
            "datum_status": str(row[2]),
            "latitude": None if row[3] is None else float(row[3]),
            "longitude": None if row[4] is None else float(row[4]),
        }
        for row in rows
    }


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bundled_migrations(installation: Path) -> dict[str, str]:
    """``file name -> sha256`` of every migration script inside the frozen bundle."""
    for candidate in (
        installation / "_internal" / "photo_search" / "catalog" / "migrations",
        installation / "photo_search" / "catalog" / "migrations",
    ):
        if candidate.is_dir():
            return {path.name: _digest(path) for path in sorted(candidate.glob("*.sql"))}
    return {}


def bundled_photo_search_data_resources(installation: Path) -> dict[str, str]:
    """Every non-Python source resource shipped beside the frozen package.

    A migration-only audit missed a stale ``web/app.js`` in P4-00B.  The package currently
    owns SQL and static web resources; making the comparison extension-agnostic prevents the
    next data resource from silently falling outside the audit.
    """
    for candidate in (
        installation / "_internal" / "photo_search",
        installation / "photo_search",
    ):
        if candidate.is_dir():
            return {
                path.relative_to(candidate).as_posix(): _digest(path)
                for path in sorted(candidate.rglob("*"))
                if path.is_file() and path.suffix not in {".py", ".pyc", ".pyo"}
            }
    return {}


def source_photo_search_data_resources() -> dict[str, str]:
    source = Path(__file__).resolve().parents[2] / "src" / "photo_search"
    return {
        path.relative_to(source).as_posix(): _digest(path)
        for path in sorted(source.rglob("*"))
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix not in {".py", ".pyc", ".pyo"}
    }


def heif_components(root: Path) -> dict[str, dict[str, int | str]]:
    """The compiled pillow-heif extension and its libheif DLL, keyed by file name."""
    components: dict[str, dict[str, int | str]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        name = path.name.lower()
        if name.startswith("_pillow_heif") or (
            name.startswith("libheif-") and name.endswith(".dll")
        ):
            components[path.name] = {"bytes": path.stat().st_size, "sha256": _digest(path)}
    return components


def heif_component_kinds(components: dict[str, dict[str, int | str]]) -> set[str]:
    """Which halves of the decoder are present: the extension and/or the libheif runtime."""
    kinds: set[str] = set()
    for name in components:
        lowered = name.lower()
        if lowered.startswith("_pillow_heif"):
            kinds.add("extension")
        elif lowered.startswith("libheif-"):
            kinds.add("libheif")
    return kinds


def heif_expected_kinds(
    heif_degraded: str | None, source_components: dict[str, dict[str, int | str]]
) -> set[str]:
    """The component kinds a complete build — or one named degradation — has to show."""
    complete = heif_component_kinds(source_components)
    if heif_degraded is None:
        return complete
    if heif_degraded == "both":
        return set()
    if heif_degraded == "libheif":
        return complete - {"libheif"}
    return complete - {"extension"}


def run_smoke(
    bundle: Path,
    work_dir: Path,
    *,
    timeout: float,
    heif_degraded: str | None = None,
    in_place: bool = False,
) -> dict[str, Any]:
    report: dict[str, Any] = {"bundle": str(bundle), "work_dir": str(work_dir), "checks": {}}
    checks: dict[str, Any] = report["checks"]
    # P4-00C-B degraded acceptance: the same smoke runs twice — once against the complete
    # bundle and once against a disposable copy with a pillow-heif half removed. Only the
    # *expectations* differ, so the degraded bundle is checked by the same code path.
    checks["heif_degraded_mode"] = heif_degraded

    work_dir.mkdir(parents=True, exist_ok=True)
    installation = bundle if in_place else work_dir / "PhotoSearch 中文 空格"
    if not in_place:
        if installation.exists():
            shutil.rmtree(installation)
        shutil.copytree(bundle, installation)
    executable = installation / "PhotoSearch.exe"
    checks["bundled_executable_bytes"] = executable.stat().st_size
    checks["installed_under_non_ascii_path"] = not in_place
    checks["in_place_frozen_run"] = in_place
    bundled_components = heif_components(installation / "_internal")
    import pillow_heif

    source_components = heif_components(Path(pillow_heif.__file__).resolve().parents[1])
    checks["bundled_heif_components"] = {
        "components": bundled_components,
        "source_components": source_components,
        "matches_source": bundled_components == source_components,
        "present_kinds": sorted(heif_component_kinds(bundled_components)),
        "expected_kinds": sorted(heif_expected_kinds(heif_degraded, source_components)),
    }
    # P4-00B: the migration scripts are packaged data. Every script from 0001 to the build's
    # supported version has to be inside the bundle, byte for byte identical to the source.
    bundled = bundled_migrations(installation)
    source_dir = Path(__file__).resolve().parents[2] / "src" / "photo_search" / "catalog" / "migrations"
    source = {path.name: _digest(path) for path in sorted(source_dir.glob("*.sql"))}
    checks["bundled_migrations"] = {
        "names": sorted(bundled),
        "expected_names": sorted(source),
        "matches_source": bundled == source,
    }
    bundled_resources = bundled_photo_search_data_resources(installation)
    source_resources = source_photo_search_data_resources()
    checks["bundled_photo_search_data_resources"] = {
        "paths": sorted(bundled_resources),
        "expected_paths": sorted(source_resources),
        "matches_source": bundled_resources == source_resources,
    }

    data_root = work_dir / "PhotoSearchData 数据 目录"
    library = work_dir / "照片 库 中文 空格"
    fixtures = seed_library(library)
    photo = fixtures["offline"]
    port = free_port()
    stdout: list[str] = []
    stderr: list[str] = []
    process = subprocess.Popen(
        [str(executable), "--port", str(port), "--data-root", str(data_root)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(installation),
    )
    readers = [
        threading.Thread(target=drain, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()

    started = time.monotonic()
    try:
        bootstrap = wait_for(stdout, lambda line: "token=" in line, timeout=timeout)
        if not bootstrap:
            raise SystemExit(f"packaged launcher never printed its session URL: {stdout} {stderr}")
        match = re.search(r"https?://\S+", bootstrap)
        if not match:
            raise SystemExit(f"the launcher did not print a usable session URL: {bootstrap}")
        bootstrap_url = urlsplit(match.group(0))
        query = parse_qs(bootstrap_url.query)
        token = (query.get("token") or [""])[0]
        if not token:
            raise SystemExit(f"the printed session URL carries no credential: {bootstrap}")
        checks["startup_seconds"] = round(time.monotonic() - started, 2)
        checks["bootstrap_line_present"] = True
        checks["bootstrap_next"] = (query.get("next") or [None])[0]

        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + timeout
        while True:
            try:
                health = httpx.get(f"{base}/api/v1/health", timeout=10.0)
                break
            except httpx.HTTPError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.3)
        checks["health"] = health.status_code
        checks["health_body"] = health.json()
        checks["credential_file"] = (data_root / "runtime" / "session.json").is_file()

        with httpx.Client(base_url=base, timeout=180.0) as client:
            shell = client.get("/")
            checks["shell_status"] = shell.status_code
            checks["shell_content_type"] = shell.headers.get("Content-Type")
            checks["shell_cache_control"] = shell.headers.get("Cache-Control")
            checks["shell_has_no_external_reference"] = (
                "http://" not in shell.text and "https://" not in shell.text
            )
            checks["shell_has_no_local_path"] = all(
                needle not in shell.text
                for needle in (str(work_dir), str(data_root), str(library))
            )
            script = client.get("/web/app.js")
            checks["web_script_status"] = script.status_code
            checks["web_script_content_type"] = script.headers.get("Content-Type")
            checks["web_script_bytes"] = len(script.content)
            checks["web_style_status"] = client.get("/web/styles.css").status_code
            # P2-03C: the place-curation page must be inside the frozen bundle too.
            places_page = client.get("/places")
            checks["places_page_status"] = places_page.status_code
            checks["places_page_content_type"] = places_page.headers.get("Content-Type")
            checks["places_page_cache_control"] = places_page.headers.get("Cache-Control")
            checks["places_page_has_no_external_reference"] = (
                "http://" not in places_page.text and "https://" not in places_page.text
            )
            checks["places_page_has_no_local_path"] = all(
                needle not in places_page.text
                for needle in (str(work_dir), str(data_root), str(library))
            )
            checks["places_page_links_back_to_shell"] = 'href="/"' in places_page.text
            places_script = client.get("/web/places.js")
            checks["places_script_status"] = places_script.status_code
            checks["places_script_content_type"] = places_script.headers.get("Content-Type")
            checks["places_script_bytes"] = len(places_script.content)
            checks["places_script_has_no_external_reference"] = (
                "http://" not in places_script.text and "https://" not in places_script.text
            )
            checks["places_style_status"] = client.get("/web/places.css").status_code
            checks["shell_links_to_places"] = 'href="/places"' in shell.text
            # P3-06: the person-curation page must be inside the frozen bundle as well, and the
            # navigation entry for it must ship (hidden by default, revealed from
            # /capabilities) on all three existing documents.
            persons_page = client.get("/persons")
            checks["persons_page_status"] = persons_page.status_code
            checks["persons_page_content_type"] = persons_page.headers.get("Content-Type")
            checks["persons_page_cache_control"] = persons_page.headers.get("Cache-Control")
            checks["persons_page_has_no_external_reference"] = (
                "http://" not in persons_page.text and "https://" not in persons_page.text
            )
            checks["persons_page_has_no_local_path"] = all(
                needle not in persons_page.text
                for needle in (str(work_dir), str(data_root), str(library))
            )
            checks["persons_page_links_back_to_shell"] = 'href="/"' in persons_page.text
            checks["persons_page_has_global_search_form"] = (
                'class="global-search-form" action="/search"' in persons_page.text
            )
            checks["persons_page_links_to_places"] = 'href="/places"' in persons_page.text
            persons_script = client.get("/web/persons.js")
            checks["persons_script_status"] = persons_script.status_code
            checks["persons_script_content_type"] = persons_script.headers.get("Content-Type")
            checks["persons_script_bytes"] = len(persons_script.content)
            checks["persons_script_has_no_external_reference"] = (
                "http://" not in persons_script.text
                and "https://" not in persons_script.text
            )
            checks["persons_style_status"] = client.get("/web/persons.css").status_code
            # P2-07: the search page must be inside the frozen bundle as well, and all three
            # documents have to navigate to each other.
            search_page = client.get("/search")
            checks["search_page_status"] = search_page.status_code
            checks["search_page_content_type"] = search_page.headers.get("Content-Type")
            checks["search_page_cache_control"] = search_page.headers.get("Cache-Control")
            checks["search_page_has_no_external_reference"] = (
                "http://" not in search_page.text and "https://" not in search_page.text
            )
            checks["search_page_has_no_local_path"] = all(
                needle not in search_page.text
                for needle in (str(work_dir), str(data_root), str(library))
            )
            checks["search_page_links_back_to_shell"] = 'href="/"' in search_page.text
            checks["search_page_links_to_places"] = 'href="/places"' in search_page.text
            search_script = client.get("/web/search.js")
            checks["search_script_status"] = search_script.status_code
            checks["search_script_content_type"] = search_script.headers.get("Content-Type")
            checks["search_script_bytes"] = len(search_script.content)
            checks["search_script_has_no_external_reference"] = (
                "http://" not in search_script.text and "https://" not in search_script.text
            )
            checks["search_style_status"] = client.get("/web/search.css").status_code
            checks["shell_has_global_search_form"] = (
                'class="global-search-form" action="/search"' in shell.text
            )
            checks["places_page_has_global_search_form"] = (
                'class="global-search-form" action="/search"' in places_page.text
            )
            # P3-06: all three documents offer the person page (hidden until
            # /capabilities proves the catalog carries the person read layer).
            checks["nav_offers_persons"] = all(
                'id="nav-persons" href="/persons"' in document.text
                for document in (shell, places_page, search_page)
            )
            checks["web_missing_status"] = client.get("/web/does-not-exist.js").status_code
            checks["stats_without_session"] = client.get("/api/v1/stats").status_code
            # Follow the launcher's own single-use link: it must set both cookies and
            # redirect (303) onto the shell.
            session = client.get(f"{bootstrap_url.path}?{bootstrap_url.query}")
            checks["session_exchange"] = session.status_code
            checks["session_redirect_location"] = session.headers.get("Location")
            cookie_header = " | ".join(
                value for value in session.headers.get_list("set-cookie")
            )
            checks["session_cookie_httponly"] = "HttpOnly" in cookie_header
            checks["session_cookie_path_root"] = "Path=/" in cookie_header
            checks["session_token_reused"] = client.get(
                "/api/v1/session", params={"token": token}
            ).status_code
            # The front end reads the double-submit value from the non-HttpOnly cookie.
            csrf = client.cookies.get("ps_csrf") or ""
            checks["csrf_cookie_readable"] = bool(csrf)
            headers = {"Origin": base, "X-CSRF-Token": csrf}

            created = client.post(
                "/api/v1/sources",
                json={"display_name": "中文 来源", "root_path": str(library)},
                headers=headers,
            )
            checks["create_source"] = created.status_code
            if created.status_code != 201:
                checks["create_source_body"] = created.text[:500]
                checks["stderr_tail"] = stderr[-25:]
                raise SystemExit(
                    f"packaged source registration failed: {created.text[:500]}\n"
                    + "\n".join(stderr[-25:])
                )
            source_id = created.json()["source_id"]
            scan = client.post(f"/api/v1/sources/{source_id}/scan", headers=headers)
            checks["scan_request"] = scan.status_code
            job_state = "UNKNOWN"
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                jobs = client.get("/api/v1/jobs", params={"source_id": source_id}).json()
                if jobs["jobs"]:
                    job_state = jobs["jobs"][0]["state"]
                    if job_state in {"SUCCEEDED", "FAILED", "BLOCKED", "CANCELLED"}:
                        break
                time.sleep(0.3)
            checks["scan_state"] = job_state

            assets = client.get("/api/v1/assets").json()
            checks["asset_count"] = len(assets["assets"])
            checks["asset_has_more"] = assets["has_more"]
            if not assets["assets"]:
                # Never crash with an IndexError here: the report is the only artifact a
                # C-level run keeps, so an empty catalogue has to say *why* it is empty.
                checks["asset_listing_body"] = assets
                checks["scan_jobs_body"] = jobs
                checks["seeded_files"] = sorted(
                    str(path.relative_to(library)) for path in library.rglob("*") if path.is_file()
                )
                checks["app_stderr_tail"] = stderr[-40:]
                raise SystemExit(
                    "packaged scan catalogued no asset: "
                    + json.dumps(checks, ensure_ascii=False, sort_keys=True)
                )
            asset_id = assets["assets"][0]["asset_id"]
            checks["availability"] = assets["assets"][0]["availability"]

            # P4-02Y: the frozen build has to carry the one-click "刷新图库" entry and answer
            # it with the documented shape: ``202`` without waiting, a process-local status
            # that keeps the same refresh id, and a terminal state inside the frozen
            # vocabulary. Nothing in the fixture changed since the full scan above, so the
            # fast scan is a no-op; what this check proves is the *entry point*, not a second
            # library pass. This data root deliberately has no pinned model, so the run must
            # *not* claim SEARCH_READY - it has to report the missing semantic channel
            # instead. The real SEARCH_READY path is exercised by the P4-02Y-T trial card
            # against the real data root, not here.
            refresh = client.post("/api/v1/library/refresh", json={}, headers=headers)
            checks["refresh_request_status"] = refresh.status_code
            requested = refresh.json() if refresh.status_code == 202 else {}
            checks["refresh_accepted"] = requested.get("accepted")
            checks["refresh_request_state"] = requested.get("state")
            refresh_id = requested.get("refresh_id")
            checks["refresh_id_present"] = bool(refresh_id)
            refresh_status: dict[str, Any] = {}
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                refresh_status = client.get("/api/v1/library/refresh").json()
                if refresh_status.get("refresh_id") == refresh_id and not refresh_status.get(
                    "running"
                ):
                    break
                time.sleep(0.3)
            checks["refresh_final_state"] = refresh_status.get("state")
            checks["refresh_search_ready"] = refresh_status.get("search_ready")
            checks["refresh_search_ready_basis"] = refresh_status.get("search_ready_basis")
            checks["refresh_status_shape_ok"] = (
                refresh_status.get("refresh_id") == refresh_id
                and isinstance(refresh_status.get("stages"), dict)
                and isinstance(refresh_status.get("counters"), dict)
            )
            checks["refresh_failed_stages"] = refresh_status.get("failed_stages")
            checks["refresh_counters"] = refresh_status.get("counters")
            checks["refresh_semantic"] = (refresh_status.get("stages") or {}).get("semantic")

            # The offline subject is picked by its *relative path*, never by row order: the
            # listing is ordered by ``asset_id`` and a second fixture photo must not be
            # mistaken for the file this check deletes.
            wanted = photo.relative_to(library).as_posix()
            offline_asset = next(
                (
                    item
                    for item in assets["assets"]
                    if str((item.get("current_location") or {}).get("relative_path") or "")
                    .replace("\\", "/")
                    == wanted
                ),
                None,
            )
            checks["offline_asset_id"] = None if offline_asset is None else offline_asset["asset_id"]
            heic_relative = fixtures["heic"].relative_to(library).as_posix()
            twin_relative = fixtures["heic_twin"].relative_to(library).as_posix()
            damaged_relative = fixtures["damaged_heic"].relative_to(library).as_posix()
            heic_asset = next(
                (item for item in assets["assets"] if str((item.get("current_location") or {}).get("relative_path") or "").replace("\\", "/") == heic_relative),
                None,
            )
            damaged_heic_asset = next(
                (item for item in assets["assets"] if str((item.get("current_location") or {}).get("relative_path") or "").replace("\\", "/") == damaged_relative),
                None,
            )
            checks["packaged_heic_asset_id"] = None if heic_asset is None else heic_asset["asset_id"]
            twin_asset = next(
                (
                    item
                    for item in assets["assets"]
                    if str((item.get("current_location") or {}).get("relative_path") or "").replace(
                        "\\", "/"
                    )
                    == twin_relative
                ),
                None,
            )
            checks["packaged_heic_twin_asset_id"] = (
                None if twin_asset is None else twin_asset["asset_id"]
            )
            checks["packaged_damaged_heic_asset_id"] = (
                None if damaged_heic_asset is None else damaged_heic_asset["asset_id"]
            )
            # P2-04: the derived FTS projection must exist inside the frozen bundle. The
            # scan queued this asset, so the request itself has to catch up and answer.
            text_hit = client.get("/api/v1/assets", params={"text": "照片"})
            checks["text_search_status"] = text_hit.status_code
            text_body = text_hit.json() if text_hit.status_code == 200 else {}
            checks["text_search_ids"] = [item["asset_id"] for item in text_body.get("assets", [])]
            checks["text_search_hit_asset"] = checks["offline_asset_id"]
            checks["text_search_summary"] = {
                key: text_body.get("search", {}).get(key)
                for key in ("state", "queued_assets", "tokenizer_version", "fts_generation_id", "documents")
            }
            checks["text_search_has_no_local_path"] = all(
                needle not in text_hit.text
                for needle in (str(work_dir), str(data_root), str(library))
            )

            # P2-06A: the frozen bundle must serve ``POST /api/v1/search`` with the
            # explicit P2-05 binding, page a continuation with the same session, and
            # refuse a request that would need a query encoder this build does not ship.
            generation_id = seed_index_generation(data_root, space_id="smoke-space:v1")
            binding = {
                "space_id": "smoke-space:v1",
                "image_generation_id": generation_id,
                "dimension": 3,
            }
            profile = client.get("/api/v1/search-profile")
            checks["search_profile_status"] = profile.status_code
            checks["search_profile_active"] = profile.json().get("profile_id")
            search_headers = {"Origin": base, "X-CSRF-Token": csrf}
            first_page = client.post(
                "/api/v1/search",
                json={"request_id": "packaged-1", "page_size": 1, **binding},
                headers=search_headers,
            )
            checks["search_status"] = first_page.status_code
            first_body = first_page.json() if first_page.status_code == 200 else {}
            checks["search_state"] = first_body.get("state")
            checks["search_generation"] = first_body.get("image_generation_id")
            checks["search_profile_id"] = first_body.get("profile_id")
            checks["search_hits"] = [row["asset_id"] for row in first_body.get("hits", [])]
            checks["search_has_more"] = first_body.get("has_more")
            checks["search_vector_engaged"] = (
                (first_body.get("channels") or {}).get("vector", {}).get("engaged")
            )
            checks["search_has_no_local_path"] = all(
                needle not in first_page.text
                for needle in (str(work_dir), str(data_root), str(library))
            )
            continuation = client.post(
                "/api/v1/search",
                json={
                    "request_id": "packaged-2",
                    "page_size": 1,
                    **binding,
                    "search_session_id": first_body.get("search_session_id"),
                    "cursor": first_body.get("next_cursor"),
                },
                headers=search_headers,
            )
            checks["search_continuation_status"] = continuation.status_code
            second_body = continuation.json() if continuation.status_code == 200 else {}
            checks["search_continuation_session_matches"] = (
                second_body.get("search_session_id") == first_body.get("search_session_id")
            )
            checks["search_continuation_hits"] = [
                row["asset_id"] for row in second_body.get("hits", [])
            ]
            checks["search_continuation_offset"] = second_body.get("page_offset")
            # A text query would need the semantic channel; this build has no query encoder
            # inside the API process, so it must refuse instead of answering text-only.
            no_encoder = client.post(
                "/api/v1/search",
                json={"request_id": "packaged-3", "text": "照片", **binding},
                headers=search_headers,
            )
            checks["search_text_without_encoder"] = no_encoder.status_code
            checks["search_text_without_encoder_code"] = (
                no_encoder.json().get("code") if no_encoder.status_code != 200 else None
            )
            checks["search_text_without_encoder_reason"] = (
                (no_encoder.json().get("details") or {}).get("reason")
                if no_encoder.status_code != 200
                else None
            )
            # P2-06B: explicit relevance feedback. It must be recorded, replay idempotently
            # and be readable, while the packaged build has no reranker adapter - so a
            # requested rerank answers ``UNAVAILABLE`` instead of silently ignoring it.
            feedback_headers = {"Origin": base, "X-CSRF-Token": csrf}
            feedback_body = {
                "contract_version": "p2-contracts.v1",
                "request_id": "packaged-fb-1",
                "search_session_id": first_body.get("search_session_id"),
                "asset_id": (checks.get("search_hits") or [asset_id])[0],
                "rank": 1,
                "relevance": "RELEVANT",
                "note": "打包 smoke",
            }
            feedback = client.post(
                "/api/v1/search/feedback", json=feedback_body, headers=feedback_headers
            )
            checks["feedback_status"] = feedback.status_code
            feedback_json = feedback.json() if feedback.status_code in (200, 201) else {}
            checks["feedback_replayed"] = feedback_json.get("replayed")
            checks["feedback_record"] = feedback_json.get("feedback")
            replay = client.post(
                "/api/v1/search/feedback", json=feedback_body, headers=feedback_headers
            )
            checks["feedback_replay_status"] = replay.status_code
            checks["feedback_replay_same_id"] = (
                replay.json().get("feedback") == checks.get("feedback_record")
                if replay.status_code == 200
                else False
            )
            conflict = client.post(
                "/api/v1/search/feedback",
                json={**feedback_body, "relevance": "NOT_RELEVANT"},
                headers=feedback_headers,
            )
            checks["feedback_conflict_status"] = conflict.status_code
            checks["feedback_conflict_code"] = (
                conflict.json().get("code") if conflict.status_code != 200 else None
            )
            listed_feedback = client.get("/api/v1/search/feedback", params={"limit": 10})
            checks["feedback_list_status"] = listed_feedback.status_code
            listed_body = listed_feedback.json() if listed_feedback.status_code == 200 else {}
            checks["feedback_list_count"] = len(listed_body.get("feedback") or [])
            checks["feedback_has_no_local_path"] = all(
                needle not in feedback.text + replay.text + listed_feedback.text
                for needle in (str(work_dir), str(data_root), str(library))
            )
            with httpx.Client(base_url=base, timeout=60.0) as anonymous:
                checks["feedback_without_session"] = anonymous.post(
                    "/api/v1/search/feedback", json=feedback_body
                ).status_code
            rerank_request = client.post(
                "/api/v1/search",
                json={
                    "request_id": "packaged-5",
                    "page_size": 1,
                    "rerank": True,
                    **binding,
                },
                headers=search_headers,
            )
            checks["rerank_status"] = rerank_request.status_code
            rerank_body = rerank_request.json() if rerank_request.status_code == 200 else {}
            checks["rerank_state"] = rerank_body.get("rerank_state")
            checks["rerank_reason"] = (rerank_body.get("reranked") or {}).get("reason")
            checks["rerank_rows"] = (rerank_body.get("reranked") or {}).get("rows")
            checks["rerank_keeps_base_hits"] = rerank_body.get("hits") == first_body.get("hits")
            # A fresh client carries no session cookie: the same body must answer 401.
            with httpx.Client(base_url=base, timeout=60.0) as anonymous:
                checks["search_without_session"] = anonymous.post(
                    "/api/v1/search", json={"request_id": "packaged-4", **binding}
                ).status_code

            # P2-07: the upload endpoint exists, is protected and answers honestly. This
            # build ships no image tower, so it must refuse structurally instead of
            # pretending the text channel found something - and it must leave the catalog
            # exactly as it was (no asset, no index row, no leftover temporary file).
            upload_headers = {"Origin": base, "X-CSRF-Token": csrf}
            upload_request = client.post(
                "/api/v1/search/image",
                data={"request_id": "packaged-img-1", **binding},
                files={"image": ("上传 图片.jpg", photo.read_bytes(), "image/jpeg")},
                headers=upload_headers,
            )
            checks["upload_status"] = upload_request.status_code
            upload_body = upload_request.json() if upload_request.status_code != 500 else {}
            checks["upload_error_body"] = (
                upload_request.text[:500] if upload_request.status_code == 500 else None
            )
            checks["upload_code"] = upload_body.get("code")
            checks["upload_reason"] = (upload_body.get("details") or {}).get("reason")
            checks["upload_refused_as_a_capability_block"] = (
                upload_request.status_code == 409
                and upload_body.get("code") == "RUNTIME_CAPABILITY_UNAVAILABLE"
            )
            checks["upload_has_no_local_path"] = all(
                needle not in upload_request.text
                for needle in (str(work_dir), str(data_root), str(library))
            )
            assets_after_upload = client.get("/api/v1/assets").json()
            checks["upload_added_no_asset"] = len(assets_after_upload["assets"]) == checks.get(
                "asset_count"
            )
            with httpx.Client(base_url=base, timeout=60.0) as anonymous:
                checks["upload_without_session"] = anonymous.post(
                    "/api/v1/search/image",
                    data={"request_id": "packaged-img-2", **binding},
                    files={"image": ("a.jpg", photo.read_bytes(), "image/jpeg")},
                ).status_code

            # P2-07: the per-category coverage view is a pure read of three existing
            # endpoints and must carry no path and no coordinate.
            coverage_capabilities = client.get("/api/v1/capabilities")
            coverage_places = client.get("/api/v1/places/coverage")
            coverage_stats = client.get("/api/v1/stats")
            checks["coverage_capabilities_status"] = coverage_capabilities.status_code
            checks["coverage_places_status"] = coverage_places.status_code
            checks["coverage_stats_status"] = coverage_stats.status_code
            checks["coverage_search_block"] = {
                key: (coverage_capabilities.json().get("search") or {}).get(key)
                for key in ("documents", "queued_assets", "state", "query_encoder_available")
            }
            # P3-01: the frozen bundle must be able to describe its OCR layer. This is the
            # behavioural proof that the new ``photo_search.ocr`` light modules really are
            # importable inside the package (a missing module would fail the request).
            checks["coverage_ocr_block"] = coverage_capabilities.json().get("ocr")
            # P3-02: the frozen bundle must be able to describe its face layer as well. This is
            # the behavioural proof that the light ``photo_search.face.contract``/``weights``
            # modules and migration 0010 really are inside the package (a missing module would
            # fail the request, and a missing migration would make the catalog unusable).
            checks["coverage_faces_block"] = coverage_capabilities.json().get("faces")
            checks["coverage_place_counts"] = {
                key: coverage_places.json().get(key)
                for key in (
                    "assets_total",
                    "gps_extracted",
                    "places_assigned",
                    "places_suggested",
                    "places_unclassified",
                )
            }
            checks["coverage_source_counts"] = {
                key: (coverage_stats.json().get("counts") or {}).get(key)
                for key in ("sources", "assets", "locations_present", "locations_missing")
            }
            checks["coverage_has_no_local_path"] = all(
                needle not in (
                    coverage_capabilities.text + coverage_places.text + coverage_stats.text
                )
                for needle in (str(work_dir), str(data_root), str(library))
            ) and "latitude" not in coverage_places.text

            # P3-05B: the split route must exist inside the frozen bundle, be guarded like
            # every other write, and answer the frozen closed code - and the packaged data
            # root must really carry the 0013 vocabulary (a missing migration would make the
            # catalog unusable, and a stale CHECK would refuse a legitimate split).
            split_probe = client.post(
                "/api/v1/persons/999999/split",
                json={
                    "contract_version": "p3-person-split-contracts.v1",
                    "request_id": "packaged-split-1",
                    "source_expected_revision": 1,
                    "faces": [
                        {
                            "face_id": "0" * 64,
                            "expected_control_revision": 0,
                            "expected_assignment_revision": 1,
                        }
                    ],
                },
                headers=headers,
            )
            checks["split_missing_person_status"] = split_probe.status_code
            split_probe_body = split_probe.json() if split_probe.status_code != 500 else {}
            checks["split_missing_person_code"] = split_probe_body.get("code")
            checks["split_has_no_local_path"] = all(
                needle not in split_probe.text
                for needle in (str(work_dir), str(data_root), str(library))
            )
            with httpx.Client(base_url=base, timeout=60.0) as anonymous:
                checks["split_without_session"] = anonymous.post(
                    "/api/v1/persons/999999/split",
                    json={
                        "contract_version": "p3-person-split-contracts.v1",
                        "request_id": "packaged-split-2",
                        "source_expected_revision": 1,
                        "faces": [],
                    },
                ).status_code

            # P3-06: the four read surfaces the person page needs must exist inside the frozen
            # bundle and stay session-protected. The catalog here carries no faces at all, so
            # the honest answers are an empty page, an empty log and a structured 404 - never a
            # 500 and never a leaked path.
            persons_list = client.get("/api/v1/persons", params={"limit": 5})
            checks["persons_list_status"] = persons_list.status_code
            persons_body = persons_list.json() if persons_list.status_code == 200 else {}
            checks["persons_list_shape"] = {
                "kind": persons_body.get("kind"),
                "persons": len(persons_body.get("persons") or []),
                "has_more": persons_body.get("has_more"),
            }
            faces_list = client.get("/api/v1/faces", params={"person_id": 1, "limit": 5})
            checks["faces_list_status"] = faces_list.status_code
            # This fixture library has no person at all, so naming one is an honest 404 rather
            # than an empty page that pretends the person exists.
            checks["faces_list_code"] = (
                (faces_list.json() or {}).get("code") if faces_list.status_code != 200 else None
            )
            manual_list = client.get(
                "/api/v1/faces", params={"assignment_state": "manual_unassigned", "limit": 5}
            )
            checks["manual_faces_status"] = manual_list.status_code
            manual_body = manual_list.json() if manual_list.status_code == 200 else {}
            checks["manual_faces_view"] = manual_body.get("view")
            checks["manual_faces_rows"] = len(manual_body.get("faces") or [])
            operations_list = client.get("/api/v1/person-operations", params={"limit": 5})
            checks["person_operations_status"] = operations_list.status_code
            operations_body = (
                operations_list.json() if operations_list.status_code == 200 else {}
            )
            checks["person_operations_shape"] = {
                "operations": len(operations_body.get("operations") or []),
                "has_more": operations_body.get("has_more"),
            }
            face_crop = client.get("/api/v1/faces/" + "0" * 64 + "/thumbnail")
            checks["face_crop_missing_status"] = face_crop.status_code
            checks["face_crop_missing_code"] = (
                face_crop.json().get("code") if face_crop.status_code != 200 else None
            )
            checks["person_read_has_no_private_fact"] = all(
                needle not in rendered
                for rendered in (
                    persons_list.text,
                    faces_list.text,
                    manual_list.text,
                    operations_list.text,
                    face_crop.text,
                )
                for needle in (
                    str(work_dir),
                    str(data_root),
                    str(library),
                    "bbox",
                    "landmark",
                    "name_key",
                    "payload_sha256",
                )
            )
            with httpx.Client(base_url=base, timeout=60.0) as anonymous:
                checks["persons_without_session"] = anonymous.get("/api/v1/persons").status_code
                checks["faces_without_session"] = anonymous.get(
                    "/api/v1/faces", params={"person_id": 1}
                ).status_code
                checks["person_operations_without_session"] = anonymous.get(
                    "/api/v1/person-operations"
                ).status_code
                checks["face_crop_without_session"] = anonymous.get(
                    "/api/v1/faces/" + "0" * 64 + "/thumbnail"
                ).status_code
            checks["packaged_person_schema"] = packaged_person_schema(data_root)
            checks["packaged_taken_time_schema"] = packaged_taken_time_schema(data_root)

            thumbnail = client.get(f"/api/v1/assets/{asset_id}/thumbnail")
            checks["thumbnail"] = thumbnail.status_code
            checks["thumbnail_type"] = thumbnail.headers.get("Content-Type")
            checks["thumbnail_bytes"] = len(thumbnail.content)
            checks["thumbnail_original_available"] = thumbnail.headers.get(
                "X-Original-Available"
            )
            with Image.open(io.BytesIO(thumbnail.content)) as image:
                checks["thumbnail_size"] = [int(image.width), int(image.height)]
            # P4-00C-B: both calls run inside the frozen executable.  The damaged control
            # proves a decoder error stays per-file instead of taking down the process.
            heic_id = checks["packaged_heic_asset_id"]
            twin_id = checks["packaged_heic_twin_asset_id"]
            damaged_heic_id = checks["packaged_damaged_heic_asset_id"]
            heic_thumbnail = client.get(f"/api/v1/assets/{heic_id}/thumbnail") if heic_id else None
            heic_preview = client.get(f"/api/v1/assets/{heic_id}/preview") if heic_id else None
            twin_thumbnail = client.get(f"/api/v1/assets/{twin_id}/thumbnail") if twin_id else None
            damaged_thumbnail = (
                client.get(f"/api/v1/assets/{damaged_heic_id}/thumbnail") if damaged_heic_id else None
            )
            checks["packaged_heic_thumbnail_status"] = None if heic_thumbnail is None else heic_thumbnail.status_code
            checks["packaged_heic_thumbnail_type"] = None if heic_thumbnail is None else heic_thumbnail.headers.get("Content-Type")
            checks["packaged_heic_thumbnail_code"] = (
                None if heic_thumbnail is None or heic_thumbnail.status_code == 200 else heic_thumbnail.json().get("code")
            )
            checks["packaged_heic_preview_status"] = None if heic_preview is None else heic_preview.status_code
            # The frozen process has to reach the *same* upright size for the HEIC and for a
            # JPEG that stores the same portrait pixels with the same EXIF orientation.
            checks["packaged_heic_thumbnail_size"] = image_size_of(heic_thumbnail)
            checks["packaged_heic_twin_thumbnail_size"] = image_size_of(twin_thumbnail)
            # P4-00C-B §2 step 3: the packaged build serves the taken-time and GPS facts of a
            # with-offset / no-offset pair with the frozen semantics and the same shape.
            if heic_id and twin_id:
                seed_derived_facts(data_root, offset_asset=heic_id, plain_asset=twin_id)
                checks["packaged_heic_taken_time"] = (
                    client.get(f"/api/v1/assets/{heic_id}").json().get("taken_time")
                )
                checks["packaged_heic_twin_taken_time"] = (
                    client.get(f"/api/v1/assets/{twin_id}").json().get("taken_time")
                )
                checks["packaged_geo_facts"] = read_geo_facts(data_root, [heic_id, twin_id])
            checks["packaged_damaged_heic_status"] = None if damaged_thumbnail is None else damaged_thumbnail.status_code
            checks["packaged_damaged_heic_code"] = (
                None if damaged_thumbnail is None or damaged_thumbnail.status_code == 200 else damaged_thumbnail.json().get("code")
            )
            capability_body = client.get("/api/v1/capabilities").json()
            checks["packaged_capability_advertises_heic"] = {"HEIC", "HEIF"}.issubset(
                set(capability_body.get("supported_input_formats") or [])
            )

            # The offline subject's thumbnail has to exist *before* its original disappears,
            # otherwise the later request legitimately answers SOURCE_FILE_MISSING instead of
            # proving that a published artifact survives an offline source.
            offline_id = checks["offline_asset_id"]
            prepared = client.get(f"/api/v1/assets/{offline_id}/thumbnail")
            checks["offline_thumbnail_prepared"] = prepared.status_code
            checks["offline_thumbnail_prepared_bytes"] = len(prepared.content)

            photo.unlink()
            offline_media = client.get(f"/api/v1/assets/{offline_id}/thumbnail")
            checks["offline_thumbnail"] = offline_media.status_code
            checks["offline_thumbnail_bytes_unchanged"] = (
                len(offline_media.content) == checks["offline_thumbnail_prepared_bytes"]
            )
            checks["offline_original_available"] = offline_media.headers.get(
                "X-Original-Available"
            )
            offline_original = client.get(f"/api/v1/assets/{offline_id}/original")
            checks["offline_original_status"] = offline_original.status_code
            checks["offline_original_code"] = offline_original.json().get("code")
            checks["detail"] = client.get(f"/api/v1/assets/{asset_id}").status_code
            checks["capabilities"] = client.get("/api/v1/capabilities").status_code
    finally:
        process.terminate()
        try:
            process.wait(timeout=60)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            process.kill()
        for reader in readers:
            reader.join(timeout=10.0)

    # The credential DACL is deliberately tightened after each launch.  Start the frozen
    # app again against exactly the same data root to prove that atomic credential rotation
    # can replace that protected target on Windows.
    restart_port = free_port()
    restart_stdout: list[str] = []
    restart_stderr: list[str] = []
    restart = subprocess.Popen(
        [str(executable), "--port", str(restart_port), "--data-root", str(data_root)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(installation),
    )
    restart_readers = [
        threading.Thread(target=drain, args=(restart.stdout, restart_stdout), daemon=True),
        threading.Thread(target=drain, args=(restart.stderr, restart_stderr), daemon=True),
    ]
    for reader in restart_readers:
        reader.start()
    try:
        restart_bootstrap = wait_for(restart_stdout, lambda line: "token=" in line, timeout=timeout)
        checks["credential_rotation_second_startup"] = restart_bootstrap is not None
        if restart_bootstrap is not None:
            restart_match = re.search(r"https?://\S+", restart_bootstrap)
            restart_query = parse_qs(urlsplit(restart_match.group(0)).query) if restart_match else {}
            restart_token = (restart_query.get("token") or [""])[0]
            checks["credential_rotation_uses_fresh_token"] = bool(restart_token) and restart_token != token
            checks["credential_rotation_health"] = httpx.get(
                f"http://127.0.0.1:{restart_port}/api/v1/health", timeout=timeout
            ).status_code
        else:
            checks["credential_rotation_uses_fresh_token"] = False
            checks["credential_rotation_health"] = None
    finally:
        restart.terminate()
        try:
            restart.wait(timeout=60)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            restart.kill()
        for reader in restart_readers:
            reader.join(timeout=10.0)
    checks["credential_rotation_stderr_free_of_token"] = all(
        token not in line for line in restart_stderr
    )

    access_lines = [line for line in stdout if "HTTP/1.1" in line]
    checks["access_log_lines"] = len(access_lines)
    checks["access_log_redacted"] = any("token=<redacted>" in line for line in access_lines)
    checks["access_log_free_of_token"] = all(token not in line for line in access_lines)
    checks["stderr_free_of_token"] = token not in "\n".join(stderr)
    checks["app_stderr_tail"] = stderr[-20:]
    return report


def supported_schema_version() -> int:
    """The tip of the migration chain of *this* source build.

    The frozen bundle must carry the same tip, so the check is pinned to the source of truth
    instead of a literal that every additive migration would have to remember to bump.
    """
    from photo_search.catalog import schema

    return int(schema.SUPPORTED_SCHEMA_VERSION)


def summarize(report: dict[str, Any]) -> list[str]:
    checks = report["checks"]
    heif_degraded = checks.get("heif_degraded_mode")
    problems: list[str] = []
    if checks.get("bootstrap_next") != "/":
        problems.append("launcher session link carries next=/")
    if checks.get("shell_status") != 200 or "text/html" not in str(
        checks.get("shell_content_type")
    ):
        problems.append("packaged shell")
    if not checks.get("shell_has_no_external_reference"):
        problems.append("shell external references")
    if not checks.get("shell_has_no_local_path"):
        problems.append("shell local paths")
    if checks.get("web_script_status") != 200 or "javascript" not in str(
        checks.get("web_script_content_type")
    ):
        problems.append("packaged script")
    if checks.get("web_style_status") != 200:
        problems.append("packaged stylesheet")
    if checks.get("places_page_status") != 200 or "text/html" not in str(
        checks.get("places_page_content_type")
    ):
        problems.append("packaged places page")
    if not checks.get("places_page_has_no_external_reference"):
        problems.append("places page external references")
    if not checks.get("places_page_has_no_local_path"):
        problems.append("places page local paths")
    if not checks.get("places_page_links_back_to_shell"):
        problems.append("places page navigation")
    if not checks.get("shell_links_to_places"):
        problems.append("shell link to the places page")
    if checks.get("search_page_status") != 200 or "text/html" not in str(
        checks.get("search_page_content_type")
    ):
        problems.append("packaged search page")
    if checks.get("search_page_cache_control") != "no-cache":
        problems.append("search page cache control")
    if not checks.get("search_page_has_no_external_reference"):
        problems.append("search page external references")
    if not checks.get("search_page_has_no_local_path"):
        problems.append("search page local paths")
    if not checks.get("search_page_links_back_to_shell") or not checks.get(
        "search_page_links_to_places"
    ):
        problems.append("search page navigation")
    if not all(
        checks.get(key)
        for key in (
            "shell_has_global_search_form",
            "places_page_has_global_search_form",
        )
    ):
        problems.append("shell/places global search form route")
    if checks.get("search_script_status") != 200 or "javascript" not in str(
        checks.get("search_script_content_type")
    ):
        problems.append("packaged search script")
    if not checks.get("search_script_has_no_external_reference"):
        problems.append("search script external references")
    if checks.get("search_style_status") != 200:
        problems.append("packaged search stylesheet")
    if checks.get("places_script_status") != 200 or "javascript" not in str(
        checks.get("places_script_content_type")
    ):
        problems.append("packaged places script")
    if not checks.get("places_script_has_no_external_reference"):
        problems.append("places script external references")
    if checks.get("places_style_status") != 200:
        problems.append("packaged places stylesheet")
    # P3-06: the person page, its assets and the navigation entry must all be in the bundle.
    if checks.get("persons_page_status") != 200 or "text/html" not in str(
        checks.get("persons_page_content_type")
    ):
        problems.append("packaged persons page")
    if checks.get("persons_page_cache_control") != "no-cache":
        problems.append("persons page cache control")
    if not checks.get("persons_page_has_no_external_reference"):
        problems.append("persons page external references")
    if not checks.get("persons_page_has_no_local_path"):
        problems.append("persons page local paths")
    if (
        not checks.get("persons_page_links_back_to_shell")
        or not checks.get("persons_page_has_global_search_form")
        or not checks.get("persons_page_links_to_places")
    ):
        problems.append("persons page navigation")
    if checks.get("persons_script_status") != 200 or "javascript" not in str(
        checks.get("persons_script_content_type")
    ):
        problems.append("packaged persons script")
    if not checks.get("persons_script_has_no_external_reference"):
        problems.append("persons script external references")
    if checks.get("persons_style_status") != 200:
        problems.append("packaged persons stylesheet")
    if not checks.get("nav_offers_persons"):
        problems.append("navigation entry for the persons page")
    if checks.get("web_missing_status") != 404:
        problems.append("unknown web asset")
    if checks.get("health") != 200:
        problems.append("health")
    if checks.get("stats_without_session") != 401:
        problems.append("unauthenticated read")
    if (
        checks.get("credential_rotation_second_startup") is not True
        or checks.get("credential_rotation_uses_fresh_token") is not True
        or checks.get("credential_rotation_health") != 200
        or checks.get("credential_rotation_stderr_free_of_token") is not True
    ):
        problems.append("credential rotation on a reused data root")
    if checks.get("session_exchange") != 303 or checks.get("session_redirect_location") != "/":
        problems.append("session redirect")
    if not checks.get("session_cookie_httponly") or not checks.get("csrf_cookie_readable"):
        problems.append("session exchange")
    if checks.get("session_token_reused") != 401:
        problems.append("single-use credential")
    if checks.get("create_source") != 201:
        problems.append("source registration")
    if checks.get("scan_request") != 202 or checks.get("scan_state") != "SUCCEEDED":
        problems.append("scan")
    if checks.get("asset_count") != 5:
        problems.append("asset listing")
    from photo_search.refresh.state import REFRESH_STATES

    terminal_refresh_states = set(REFRESH_STATES) - {"IDLE", "SCANNING", "INDEXING", "ENRICHING"}
    if (
        checks.get("refresh_request_status") != 202
        or checks.get("refresh_accepted") is not True
        or not checks.get("refresh_id_present")
        or checks.get("refresh_request_state") not in REFRESH_STATES
        or checks.get("refresh_final_state") not in terminal_refresh_states
        or not checks.get("refresh_status_shape_ok")
    ):
        problems.append("packaged library refresh entry")
    refresh_semantic = checks.get("refresh_semantic") or {}
    if checks.get("refresh_search_ready") is not False:
        # A fixture data root with no pinned model cannot serve the semantic channel, so a
        # frozen build that still claimed SEARCH_READY here would promise search it has not
        # got (P4-02Y §6).
        problems.append("packaged library refresh claimed search readiness")
    if (
        refresh_semantic.get("state") != "SKIPPED_UNAVAILABLE"
        or refresh_semantic.get("reason") != "MANIFEST_UNCONFIGURED"
        or checks.get("refresh_final_state") != "FAILED"
    ):
        # The run cannot bring the library up to date without the model, so it has to name
        # the missing semantic channel *and* end in the state that says so instead of a
        # COMPLETE that would hide the gap.
        problems.append("packaged library refresh semantic stage")
    text_summary = checks.get("text_search_summary") or {}
    if (
        checks.get("text_search_status") != 200
        or checks.get("offline_asset_id") is None
        or checks.get("text_search_ids") != [checks.get("offline_asset_id")]
    ):
        problems.append("packaged text search")
    if text_summary.get("state") != "SYNCED" or text_summary.get("fts_generation_id") != "fts-v1":
        problems.append("packaged search projection")
    if not checks.get("text_search_has_no_local_path"):
        problems.append("text search leaked a local path")
    if checks.get("search_profile_status") != 200 or checks.get("search_profile_active") is not None:
        problems.append("packaged search profile")
    if checks.get("search_status") != 200 or checks.get("search_state") != "OK":
        problems.append("packaged search")
    if checks.get("search_generation") != "image-gen-smoke" or checks.get("search_profile_id") is not None:
        problems.append("packaged search binding echo")
    if checks.get("search_hits") == [] or checks.get("search_has_more") is not True:
        problems.append("packaged search page")
    if checks.get("search_vector_engaged") is not False:
        problems.append("packaged search channel")
    if not checks.get("search_has_no_local_path"):
        problems.append("search leaked a local path")
    if (
        checks.get("search_continuation_status") != 200
        or not checks.get("search_continuation_session_matches")
        or checks.get("search_continuation_offset") != 1
    ):
        problems.append("packaged search continuation")
    if set(checks.get("search_hits") or []) & set(checks.get("search_continuation_hits") or []):
        problems.append("packaged search repeated an asset across pages")
    if (
        checks.get("search_text_without_encoder") != 409
        or checks.get("search_text_without_encoder_code") != "RUNTIME_CAPABILITY_UNAVAILABLE"
        or checks.get("search_text_without_encoder_reason") != "QUERY_ENCODER_UNAVAILABLE"
    ):
        problems.append("packaged search without a query encoder")
    # P2-06B: feedback is recorded, idempotent and readable; a requested rerank is reported
    # as UNAVAILABLE (this build ships no reranker) without changing the base page.
    if checks.get("feedback_status") != 201 or checks.get("feedback_replayed") is not False:
        problems.append("packaged feedback record")
    if checks.get("feedback_replay_status") != 200 or not checks.get("feedback_replay_same_id"):
        problems.append("packaged feedback idempotency")
    if (
        checks.get("feedback_conflict_status") != 409
        or checks.get("feedback_conflict_code") != "FEEDBACK_IDEMPOTENCY_CONFLICT"
    ):
        problems.append("packaged feedback conflict")
    if checks.get("feedback_list_status") != 200 or (checks.get("feedback_list_count") or 0) < 1:
        problems.append("packaged feedback listing")
    if not checks.get("feedback_has_no_local_path"):
        problems.append("feedback leaked a local path")
    if checks.get("feedback_without_session") != 401:
        problems.append("packaged feedback without a session")
    if checks.get("rerank_status") != 200 or checks.get("rerank_state") != "UNAVAILABLE":
        problems.append("packaged rerank state")
    if checks.get("rerank_rows") != [] or checks.get("rerank_reason") is None:
        problems.append("packaged rerank rows")
    if checks.get("rerank_keeps_base_hits") is not True:
        problems.append("packaged rerank changed the base page")
    if checks.get("search_without_session") != 401:
        problems.append("packaged search without a session")
    if not checks.get("upload_refused_as_a_capability_block"):
        problems.append("packaged upload did not answer a structured capability refusal")
    if checks.get("upload_reason") not in (None, "IMAGE_ENCODER_UNAVAILABLE"):
        problems.append("packaged upload reason")
    if not checks.get("upload_has_no_local_path"):
        problems.append("upload leaked a local path")
    if checks.get("upload_added_no_asset") is not True:
        problems.append("packaged upload added an asset")
    if checks.get("upload_without_session") != 401:
        problems.append("packaged upload without a session")
    if (
        checks.get("coverage_capabilities_status") != 200
        or checks.get("coverage_places_status") != 200
        or checks.get("coverage_stats_status") != 200
    ):
        problems.append("packaged coverage view")
    if not checks.get("coverage_has_no_local_path"):
        problems.append("coverage leaked a local path or a coordinate")
    if checks.get("split_missing_person_status") != 404 or checks.get(
        "split_missing_person_code"
    ) != "PERSON_NOT_FOUND":
        problems.append("packaged split route")
    if not checks.get("split_has_no_local_path"):
        problems.append("split refusal leaked a local path")
    if checks.get("split_without_session") != 401:
        problems.append("packaged split without a session")
    # P3-06 read surfaces: an empty catalog must answer with empty pages, not with a 500.
    if checks.get("persons_list_status") != 200:
        problems.append("packaged persons list")
    if (checks.get("persons_list_shape") or {}).get("kind") != "all":
        problems.append("packaged persons list shape")
    if checks.get("faces_list_status") != 404 or checks.get("faces_list_code") != "PERSON_NOT_FOUND":
        problems.append("packaged faces list")
    if checks.get("manual_faces_status") != 200:
        problems.append("packaged manual-unassigned list")
    if checks.get("manual_faces_view") != "manual_unassigned" or checks.get(
        "manual_faces_rows"
    ) != 0:
        problems.append("packaged manual-unassigned list shape")
    if checks.get("person_operations_status") != 200:
        problems.append("packaged person operation log")
    if "operations" not in (checks.get("person_operations_shape") or {}):
        problems.append("packaged person operation log shape")
    if checks.get("face_crop_missing_status") != 404 or checks.get(
        "face_crop_missing_code"
    ) != "FACE_NOT_FOUND":
        problems.append("packaged face crop route")
    if not checks.get("person_read_has_no_private_fact"):
        problems.append("person read surface leaked a private fact or a local path")
    for key in (
        "persons_without_session",
        "faces_without_session",
        "person_operations_without_session",
        "face_crop_without_session",
    ):
        if checks.get(key) != 401:
            problems.append(f"packaged {key}")
    person_schema = checks.get("packaged_person_schema") or {}
    if (
        int(person_schema.get("schema_version") or 0) < 13
        or not person_schema.get("has_split")
        or not person_schema.get("has_face_assign")
        or not person_schema.get("has_split_group")
        or not person_schema.get("has_face_control")
    ):
        problems.append("packaged person-operation schema")
    taken_time_schema = checks.get("packaged_taken_time_schema") or {}
    if (
        taken_time_schema.get("schema_version") != supported_schema_version()
        or not taken_time_schema.get("has_asset_metadata")
        or not taken_time_schema.get("has_closed_sources")
        or not taken_time_schema.get("has_capture_index")
        or taken_time_schema.get("rules_version") != 1
    ):
        problems.append("packaged taken-time schema")
    migrations = checks.get("bundled_migrations") or {}
    if not migrations.get("matches_source") or migrations.get("names") != migrations.get(
        "expected_names"
    ):
        problems.append("packaged migration data resources")
    package_resources = checks.get("bundled_photo_search_data_resources") or {}
    if not package_resources.get("matches_source") or package_resources.get("paths") != package_resources.get(
        "expected_paths"
    ):
        problems.append("packaged photo_search data resources")
    heif_components = checks.get("bundled_heif_components") or {}
    if heif_degraded is None:
        # Complete bundle: the extension *and* its libheif runtime travel with the build and
        # HEIC decodes inside the frozen process.
        if (
            not heif_components.get("matches_source")
            or len(heif_components.get("components") or {}) != 2
        ):
            problems.append("packaged pillow-heif components")
        if (
            checks.get("packaged_heic_thumbnail_status") != 200
            or checks.get("packaged_heic_thumbnail_type") != "image/jpeg"
            or checks.get("packaged_heic_preview_status") != 200
            or checks.get("packaged_heic_thumbnail_size") is None
            or checks.get("packaged_heic_thumbnail_size")
            != checks.get("packaged_heic_twin_thumbnail_size")
            or checks.get("packaged_damaged_heic_status") != 409
            or checks.get("packaged_damaged_heic_code") != "DECODE_FAILED"
            or checks.get("packaged_capability_advertises_heic") is not True
        ):
            problems.append("packaged HEIC decode isolation")
    else:
        # Degraded bundle (P4-00C-B): one half of the decoder was removed from a disposable
        # copy. HEIC/HEIF must be *refused as a format* — never described as a broken file,
        # never a startup failure — while every other format and the existing cache keep
        # working, and the capability list must stop advertising HEIC.
        if heif_components.get("present_kinds") != heif_components.get("expected_kinds"):
            problems.append("packaged pillow-heif components")
        if (
            checks.get("packaged_heic_thumbnail_status") != 409
            or checks.get("packaged_heic_thumbnail_code") != "FORMAT_UNSUPPORTED"
            or checks.get("packaged_heic_preview_status") != 409
            or checks.get("packaged_damaged_heic_status") != 409
            or checks.get("packaged_damaged_heic_code") != "FORMAT_UNSUPPORTED"
            or checks.get("packaged_capability_advertises_heic") is not False
        ):
            problems.append("packaged HEIC degraded fail-closed")
        # The JPEG twin of the same pixels must still decode while HEIC is refused.
        if checks.get("packaged_heic_twin_thumbnail_size") is None:
            problems.append("packaged HEIC degraded JPEG control")
    if (checks.get("coverage_search_block") or {}).get("query_encoder_available") is not False:
        problems.append("packaged coverage query encoder flag")
    heic_time = checks.get("packaged_heic_taken_time") or {}
    twin_time = checks.get("packaged_heic_twin_taken_time") or {}
    if (
        heic_time.get("time_source") != "EXIF_ORIGINAL"
        or heic_time.get("time_confidence") != "HIGH"
        or heic_time.get("timezone_offset_minutes") != 480
        or heic_time.get("label") != "capture"
        or heic_time.get("camera") != "Smoke Cam"
        or twin_time.get("time_confidence") != "MEDIUM"
        or twin_time.get("timezone_offset_minutes") is not None
        or heic_time.get("taken_at") != twin_time.get("taken_at")
        or heic_time.get("taken_at_sort") != twin_time.get("taken_at_sort")
    ):
        problems.append("packaged taken-time semantics")
    geo_facts = checks.get("packaged_geo_facts") or {}
    if len(geo_facts) != 2 or len(
        {json.dumps(value, sort_keys=True) for value in geo_facts.values()}
    ) != 1:
        problems.append("packaged GPS facts")
    ocr_block = checks.get("coverage_ocr_block") or {}
    if ocr_block.get("processor_id") != "ocr-text-cuda-fp32@4":
        problems.append("packaged capabilities has no current OCR block")
    if ocr_block.get("state") != "NOT_RUN":
        problems.append("packaged OCR block is not a fresh NOT_RUN state")
    if not isinstance(ocr_block.get("weights_present"), bool):
        problems.append("packaged OCR block does not report weight presence")
    # Counts and states only: a per-region polygon or box in this block would be a leak.
    rendered_ocr = json.dumps(ocr_block, ensure_ascii=False)
    if "polygon" in rendered_ocr or '"box"' in rendered_ocr:
        problems.append("packaged OCR block leaked region geometry")
    if checks.get("thumbnail") != 200 or checks.get("thumbnail_type") != "image/jpeg":
        problems.append("thumbnail")
    if checks.get("thumbnail_original_available") != "true":
        problems.append("thumbnail availability header")
    if checks.get("offline_thumbnail_prepared") != 200:
        problems.append("offline thumbnail preparation")
    if checks.get("offline_thumbnail") != 200 or not checks.get(
        "offline_thumbnail_bytes_unchanged"
    ):
        problems.append("offline thumbnail")
    if checks.get("offline_original_available") != "false":
        problems.append("offline thumbnail availability header")
    if checks.get("offline_original_code") != "SOURCE_FILE_MISSING":
        problems.append("offline original")
    if not checks.get("access_log_redacted") or not checks.get("access_log_free_of_token"):
        problems.append("access log redaction")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path(tempfile.gettempdir()) / WORK_DIRNAME,
        help="where the copied bundle and the fixtures live (%%TEMP%% by default)",
    )
    parser.add_argument("--timeout", type=float, default=START_TIMEOUT_SECONDS)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="run the frozen executable from its build directory without copying the onedir",
    )
    parser.add_argument(
        "--heif-degraded",
        choices=("libheif", "extension", "both"),
        default=None,
        help=(
            "run the P4-00C-B degraded acceptance against a disposable bundle copy with the "
            "named pillow-heif half removed: HEIC must answer FORMAT_UNSUPPORTED"
        ),
    )
    args = parser.parse_args()

    if not (args.bundle / "PhotoSearch.exe").is_file():
        print(f"error: no packaged executable under {args.bundle}", file=sys.stderr)
        return 2
    report = run_smoke(
        args.bundle.resolve(),
        # The packaged process is started with its *own* working directory, so every path
        # handed to it has to be absolute: with a relative ``--work-dir`` the registered
        # source root would point somewhere the bundle cannot see and the scan would answer
        # SOURCE_OFFLINE instead of cataloguing the fixtures.
        args.work_dir.resolve(),
        timeout=args.timeout,
        heif_degraded=args.heif_degraded,
        in_place=args.in_place,
    )
    problems = summarize(report)
    report["problems"] = problems
    report["status"] = "ok" if not problems else "failed"
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8", newline="\n")
    print(rendered)
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
