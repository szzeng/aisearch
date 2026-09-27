"""P4-02N no-model branch: the *real* startup entry without a pinned model.

Starts ``python -m photo_search --data-root <root>`` - the production entry, not the app
factory - against a catalog that already has one READY semantic generation, and records what
the process does when this machine has no model directory:

* the launcher prints the semantic-channel line with ``MODEL_DIR_MISSING``;
* ``GET /api/v1/capabilities`` answers ``query_encoder_available=false``;
* a text search is refused with the documented ``409`` / ``QUERY_ENCODER_UNAVAILABLE``;
* an uploaded-image search is refused with ``409`` / ``IMAGE_ENCODER_UNAVAILABLE``;
* the process creates no ``models/`` directory in the data root and logs no traceback.

Nothing is downloaded and no real photo library is touched: the catalog is the throw-away one
the real-model driver leaves behind, and the uploaded image is generated in memory.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

from httpx import Client  # noqa: E402

from photo_search.catalog.db import Catalog  # noqa: E402

TOKEN_PATTERN = re.compile(r"token=([A-Za-z0-9_\-]+)")
SEMANTIC_PATTERN = re.compile(r"photo-search: semantic channel (\S+) \(model_dir=([^,]+), manifest=([^)]+)\)")
def _png_bytes() -> bytes:
    """A tiny valid PNG, generated instead of pasted so the upload never fails to decode."""
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), (220, 40, 40)).save(buffer, "PNG")
    return buffer.getvalue()


def _ready_generation(catalog_db: Path) -> dict[str, Any]:
    catalog = Catalog.open(db_path=catalog_db)
    try:
        return dict(
            catalog.read_one(
                "SELECT generation_id, space_id, dimension FROM index_generations "
                "WHERE state = 'READY' ORDER BY created_at DESC LIMIT 1"
            )
        )
    finally:
        catalog.close()


def _wait_for_startup(process: Any, deadline: float) -> tuple[str, str, str]:
    """Read the launcher lines until the one-time credential appears."""
    lines: list[str] = []
    while time.monotonic() < deadline:
        line = process.stdout.readline()
        if not line:
            if process.poll() is not None:
                break
            time.sleep(0.05)
            continue
        lines.append(line.rstrip("\n"))
        found = TOKEN_PATTERN.search(line)
        if found:
            return found.group(1), "\n".join(lines), "\n".join(lines)
    raise RuntimeError(f"the launcher never printed a credential: {lines[-5:]}")


def run(args: argparse.Namespace) -> int:
    data_root = Path(args.data_root)
    catalog_db = data_root / "catalog" / "catalog.db"
    generation = _ready_generation(catalog_db)
    port = int(args.port)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "photo_search",
            "--data-root",
            str(data_root),
            "--port",
            str(port),
        ],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    report: dict[str, Any] = {"data_root": str(data_root), "generation": generation}
    try:
        token, startup, _ = _wait_for_startup(process, time.monotonic() + 90)
        report["startup_lines"] = startup.splitlines()
        match = SEMANTIC_PATTERN.search(startup)
        report["semantic_channel_line"] = match.group(0) if match else None
        report["semantic_channel_reason"] = match.group(1) if match else None
        base = f"http://127.0.0.1:{port}"
        # The middleware requires the Origin port to belong to this very server, so the
        # origin is built from the port the process really bound.
        with Client(base_url=base, timeout=60.0, follow_redirects=False) as client:
            session = client.get("/api/v1/session", params={"token": token})
            csrf = str(session.json()["csrf_token"])
            headers = {"X-CSRF-Token": csrf, "Origin": base}
            capabilities = client.get("/api/v1/capabilities")
            body = {
                "request_id": "p4-02n-no-model",
                "text": "红色的图片",
                "space_id": str(generation["space_id"]),
                "image_generation_id": str(generation["generation_id"]),
                "dimension": int(generation["dimension"]),
            }
            text_search = client.post("/api/v1/search", json=body, headers=headers)
            image_search = client.post(
                "/api/v1/search/image",
                files={"image": ("query.png", _png_bytes(), "image/png")},
                data={key: str(value) for key, value in body.items() if key != "text"},
                headers=headers,
            )
        search_capability = (
            capabilities.json().get("search") if capabilities.status_code == 200 else {}
        )
        text_error = text_search.json() if text_search.status_code != 200 else {}
        image_error = image_search.json() if image_search.status_code != 200 else {}
        report.update(
            {
                "session_status": int(session.status_code),
                "capabilities_status": int(capabilities.status_code),
                "query_encoder_available": (search_capability or {}).get(
                    "query_encoder_available"
                ),
                "text_search": {
                    "status": int(text_search.status_code),
                    "code": (text_error or {}).get("code"),
                    "reason": ((text_error or {}).get("details") or {}).get("reason"),
                },
                "image_search": {
                    "status": int(image_search.status_code),
                    "code": (image_error or {}).get("code"),
                    "reason": ((image_error or {}).get("details") or {}).get("reason"),
                },
                "raw_error_bodies": {
                    "text": text_search.text[:400],
                    "image": image_search.text[:400],
                },
                "models_dir_created": (data_root / "models").exists(),
            }
        )
    finally:
        process.terminate()
        try:
            stderr = process.stderr.read() if process.stderr else ""
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:  # pragma: no cover - a stuck server is reported
            process.kill()
            stderr = ""
    report["traceback_in_stderr"] = "Traceback (most recent call last)" in (stderr or "")
    checks = {
        "starts_without_a_model": bool(report.get("startup_lines")),
        "semantic_channel_is_missing": report.get("semantic_channel_reason")
        == "MODEL_DIR_MISSING",
        "capability_is_false": report.get("query_encoder_available") is False,
        "text_search_is_the_documented_refusal": report.get("text_search")
        == {
            "status": 409,
            "code": "RUNTIME_CAPABILITY_UNAVAILABLE",
            "reason": "QUERY_ENCODER_UNAVAILABLE",
        },
        "image_search_is_the_documented_refusal": report.get("image_search")
        == {
            "status": 409,
            "code": "RUNTIME_CAPABILITY_UNAVAILABLE",
            "reason": "IMAGE_ENCODER_UNAVAILABLE",
        },
        "no_models_directory_was_created": report.get("models_dir_created") is False,
        "no_traceback": report.get("traceback_in_stderr") is False,
    }
    report["checks"] = checks
    report["passed"] = all(checks.values())
    report["status"] = "pass" if report["passed"] else "fail"
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is not None:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)
    return 0 if report["passed"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8129)
    parser.add_argument("--output", type=Path, default=None)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
