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
    """One JPEG under a Chinese/space path, with a quiet ``mtime``."""
    target = root / "子 目录 中文" / "照片 一.jpg"
    target.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (640, 480), (180, 60, 20)).save(target, "JPEG", quality=90)
    moment = (1_700_000_000_000 - 3_600_000) * 1_000_000
    os.utime(target, ns=(moment, moment))
    return target


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

            thumbnail = client.get(f"/api/v1/assets/{asset_id}/thumbnail")
            checks["thumbnail"] = thumbnail.status_code
            checks["thumbnail_type"] = thumbnail.headers.get("Content-Type")
            checks["thumbnail_bytes"] = len(thumbnail.content)
            checks["thumbnail_original_available"] = thumbnail.headers.get(
                "X-Original-Available"
            )
            with Image.open(io.BytesIO(thumbnail.content)) as image:
                checks["thumbnail_size"] = [int(image.width), int(image.height)]

            photo.unlink()
            offline_media = client.get(f"/api/v1/assets/{asset_id}/thumbnail")
            checks["offline_thumbnail"] = offline_media.status_code
            checks["offline_original_available"] = offline_media.headers.get(
                "X-Original-Available"
            )
            offline_original = client.get(f"/api/v1/assets/{asset_id}/original")
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
    if checks.get("asset_count") != 1:
        problems.append("asset listing")
    if checks.get("thumbnail") != 200 or checks.get("thumbnail_type") != "image/jpeg":
        problems.append("thumbnail")
    if checks.get("thumbnail_original_available") != "true":
        problems.append("thumbnail availability header")
    if checks.get("offline_thumbnail") != 200 or checks.get(
        "offline_original_available"
    ) != "false":
        problems.append("offline thumbnail")
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
