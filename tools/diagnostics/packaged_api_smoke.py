"""C-level smoke test for the packaged local API (P1-04).

The launcher changed in P1-04 (explicit bind options, one-time session credential,
in-process ``uvicorn`` with an application object), so the packaged build is exercised for
the scenarios that only a real bundle can prove: a Chinese/space installation path, a
Chinese/space *source* root, an offline original, and the access-log credential filter.

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
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

DEFAULT_BUNDLE = Path("dist") / "PhotoSearch"
WORK_DIRNAME = "P1-04 打包 API 验收"
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
        token = bootstrap.split("token=", 1)[1].strip()
        checks["startup_seconds"] = round(time.monotonic() - started, 2)
        checks["bootstrap_line_present"] = True

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
            checks["stats_without_session"] = client.get("/api/v1/stats").status_code
            session = client.get("/api/v1/session", params={"token": token})
            checks["session_exchange"] = session.status_code
            cookie_header = " | ".join(
                value for value in session.headers.get_list("set-cookie")
            )
            checks["session_cookie_httponly"] = "HttpOnly" in cookie_header
            checks["session_cookie_path_root"] = "Path=/" in cookie_header
            checks["session_token_reused"] = client.get(
                "/api/v1/session", params={"token": token}
            ).status_code
            csrf = session.json()["csrf_token"]
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
    if checks.get("health") != 200:
        problems.append("health")
    if checks.get("stats_without_session") != 401:
        problems.append("unauthenticated read")
    if checks.get("session_exchange") != 200 or not checks.get("session_cookie_httponly"):
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
