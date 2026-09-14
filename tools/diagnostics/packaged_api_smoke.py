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


def seed_library(root: Path) -> Path:
    """Two JPEGs under a Chinese/space path, with a quiet ``mtime``.

    The second photo exists so the packaged search can page with a real cursor; the first
    one is the offline subject the media checks delete later.
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
    return target


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


def run_smoke(bundle: Path, work_dir: Path, *, timeout: float) -> dict[str, Any]:
    report: dict[str, Any] = {"bundle": str(bundle), "work_dir": str(work_dir), "checks": {}}
    checks: dict[str, Any] = report["checks"]

    installation = work_dir / "PhotoSearch 中文 空格"
    if installation.exists():
        shutil.rmtree(installation)
    work_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(bundle, installation)
    executable = installation / "PhotoSearch.exe"
    checks["bundled_executable_bytes"] = executable.stat().st_size
    checks["installed_under_non_ascii_path"] = True

    data_root = work_dir / "PhotoSearchData 数据 目录"
    library = work_dir / "照片 库 中文 空格"
    photo = seed_library(library)
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
            checks["shell_links_to_search"] = 'href="/search"' in shell.text
            checks["places_page_links_to_search"] = 'href="/search"' in places_page.text
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
            asset_id = assets["assets"][0]["asset_id"]
            checks["availability"] = assets["assets"][0]["availability"]
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
            upload_body = (
                upload_request.json() if upload_request.status_code != 500 else {}
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

            thumbnail = client.get(f"/api/v1/assets/{asset_id}/thumbnail")
            checks["thumbnail"] = thumbnail.status_code
            checks["thumbnail_type"] = thumbnail.headers.get("Content-Type")
            checks["thumbnail_bytes"] = len(thumbnail.content)
            checks["thumbnail_original_available"] = thumbnail.headers.get(
                "X-Original-Available"
            )
            with Image.open(io.BytesIO(thumbnail.content)) as image:
                checks["thumbnail_size"] = [int(image.width), int(image.height)]

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
    return report


def summarize(report: dict[str, Any]) -> list[str]:
    checks = report["checks"]
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
    if not checks.get("shell_links_to_search") or not checks.get("places_page_links_to_search"):
        problems.append("shell/places link to the search page")
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
    if checks.get("asset_count") != 2:
        problems.append("asset listing")
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
    if (checks.get("coverage_search_block") or {}).get("query_encoder_available") is not False:
        problems.append("packaged coverage query encoder flag")
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
    args = parser.parse_args()

    if not (args.bundle / "PhotoSearch.exe").is_file():
        print(f"error: no packaged executable under {args.bundle}", file=sys.stderr)
        return 2
    report = run_smoke(args.bundle.resolve(), args.work_dir, timeout=args.timeout)
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
