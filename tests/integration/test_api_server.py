"""P1-04 scenario 10: the launcher on a real port and the non-loopback refusal."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

MODULE = [sys.executable, "-m", "photo_search"]
START_TIMEOUT_SECONDS = 60.0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _reader(stream, sink: list[str]) -> None:
    for line in stream:
        sink.append(line.rstrip("\n"))


def _wait_for(sink: list[str], predicate, *, timeout: float) -> str | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for line in list(sink):
            if predicate(line):
                return line
        time.sleep(0.1)
    return None


def test_launcher_serves_the_api_on_loopback_and_redacts_the_credential(tmp_path: Path) -> None:
    port = _free_port()
    data_root = tmp_path / "PhotoSearchData"
    stdout: list[str] = []
    stderr: list[str] = []
    process = subprocess.Popen(
        [*MODULE, "--port", str(port), "--data-root", str(data_root)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    readers = [
        threading.Thread(target=_reader, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=_reader, args=(process.stderr, stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()
    try:
        bootstrap = _wait_for(
            stdout, lambda line: "token=" in line, timeout=START_TIMEOUT_SECONDS
        )
        assert bootstrap, f"the launcher never printed its session URL: {stdout}\n{stderr}"
        token = bootstrap.split("token=", 1)[1].strip()
        assert token
        listening = _wait_for(
            stdout, lambda line: "listening on" in line, timeout=START_TIMEOUT_SECONDS
        )
        assert listening and "127.0.0.1" in listening

        credential = data_root / "runtime" / "session.json"
        assert credential.is_file()
        payload = json.loads(credential.read_text(encoding="utf-8"))
        assert payload["token"] == token
        assert payload["schema"] == 1

        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + START_TIMEOUT_SECONDS
        health = None
        while time.monotonic() < deadline:
            try:
                health = httpx.get(f"{base}/api/v1/health", timeout=5.0)
                break
            except httpx.HTTPError:
                time.sleep(0.2)
        assert health is not None and health.status_code == 200, (health, stderr)
        assert health.json() == {"status": "ok", "service": "photo-search"}
        assert health.headers["X-Request-ID"]

        with httpx.Client(base_url=base, timeout=10.0) as client:
            assert client.get("/api/v1/stats").status_code == 401

            session = client.get("/api/v1/session", params={"token": token})
            assert session.status_code == 200, session.text
            set_cookie = session.headers.get_list("set-cookie")
            session_cookie = next(value for value in set_cookie if value.startswith("ps_session="))
            csrf_cookie = next(value for value in set_cookie if value.startswith("ps_csrf="))
            assert "HttpOnly" in session_cookie
            assert "Path=/" in session_cookie
            assert "HttpOnly" not in csrf_cookie
            csrf = session.json()["csrf_token"]

            assert client.get("/api/v1/stats").status_code == 200
            assert client.get("/api/v1/session", params={"token": token}).status_code == 401
            created = client.post(
                "/api/v1/sources",
                json={"display_name": "smoke", "root_path": str(tmp_path / "library")},
                headers={"Origin": base, "X-CSRF-Token": csrf},
            )
            assert created.status_code == 201, created.text
            sources = client.get("/api/v1/sources")
            assert sources.status_code == 200
            assert [item["display_name"] for item in sources.json()["sources"]] == ["smoke"]
    finally:
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            process.kill()
        for reader in readers:
            reader.join(timeout=5.0)

    # uvicorn writes its access log to stdout; the console bootstrap line is the only place
    # the credential is allowed to appear.
    access_lines = [line for line in stdout if "HTTP/1.1" in line]
    bootstrap_lines = [line for line in stdout if "token=" in line and "HTTP/1.1" not in line]
    assert any("/api/v1/session?token=<redacted>" in line for line in access_lines), access_lines
    assert all(token not in line for line in access_lines), access_lines
    assert any(token in line for line in bootstrap_lines), bootstrap_lines
    assert token not in "\n".join(stderr)


def test_launcher_refuses_a_non_loopback_bind_by_default(tmp_path: Path) -> None:
    port = _free_port()
    data_root = tmp_path / "PhotoSearchData"
    completed = subprocess.run(
        [*MODULE, "--host", "0.0.0.0", "--port", str(port), "--data-root", str(data_root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    assert completed.returncode == 2, completed
    assert "BIND_NOT_ALLOWED" in completed.stderr
    assert not (data_root / "runtime" / "session.json").exists()
    assert not (data_root / "catalog" / "catalog.db").exists()
    assert "listening on" not in completed.stdout


def test_token_redaction_filter_covers_the_known_query_spellings() -> None:
    from photo_search.__main__ import TokenRedactingFilter, redact_token_query

    assert redact_token_query("GET /api/v1/session?token=abc HTTP/1.1") == (
        "GET /api/v1/session?token=<redacted> HTTP/1.1"
    )
    assert "abc" not in redact_token_query("/x?csrf_token=abc&other=1")
    assert redact_token_query("/api/v1/assets?cursor=abc") == "/api/v1/assets?cursor=abc"

    import logging

    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:1", "GET", "/api/v1/session?token=secret", "1.1", 200),
        exc_info=None,
    )
    assert TokenRedactingFilter().filter(record) is True
    assert "secret" not in record.getMessage()
    assert "<redacted>" in record.getMessage()
    assert os.name == "nt" or True
