from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

from PyInstaller.depend.bindepend import resolve_library_path

from tools.diagnostics.packaged_api_smoke import (
    _redact_process_output,
    _stop_child,
    drain,
    wait_for,
)
from tools.packaging.build_onedir import pyinstaller_environment


def _start_child(
    source: str,
) -> tuple[subprocess.Popen[str], list[str], list[str], list[threading.Thread]]:
    process = subprocess.Popen(
        [sys.executable, "-c", source],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stdout: list[str] = []
    stderr: list[str] = []
    assert process.stdout is not None
    assert process.stderr is not None
    readers = [
        threading.Thread(target=drain, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()
    return process, stdout, stderr, readers


def test_pyinstaller_searches_system_runtime_before_inherited_path(monkeypatch) -> None:
    monkeypatch.setenv("WINDIR", r"C:\Windows")
    monkeypatch.setenv("PATH", r"D:\Vendor\bin;C:\Windows\System32")

    env = pyinstaller_environment()

    assert env["PATH"].split(os.pathsep) == [
        str(Path(r"C:\Windows") / "System32"),
        r"D:\Vendor\bin",
        r"C:\Windows\System32",
    ]
    monkeypatch.setenv("PATH", env["PATH"])
    resolved = resolve_library_path("msvcp140.dll")
    assert Path(resolved).resolve() == (Path(r"C:\Windows") / "System32" / "msvcp140.dll").resolve()


def test_wait_for_reports_early_exit_and_both_exit_code_forms() -> None:
    process, stdout, stderr, readers = _start_child(
        "import sys; print('token=secret-value', flush=True); "
        "print('native failure', file=sys.stderr, flush=True); sys.exit(-1073741819)"
    )

    try:
        try:
            wait_for(
                stdout,
                lambda line: "session URL" in line,
                timeout=3,
                process=process,
                stderr_sink=stderr,
                readers=readers,
            )
        except RuntimeError as error:
            message = str(error)
        else:
            raise AssertionError("early child exit should be reported")
    finally:
        _stop_child(process)

    assert "-1073741819" in message
    assert "0xC0000005" in message
    assert "listener_observed=False" in message
    assert "session_url_observed=False" in message
    assert "native failure" in message
    assert "secret-value" not in message


def test_wait_for_reports_clean_exit_without_a_session_url() -> None:
    process, stdout, stderr, readers = _start_child("pass")

    try:
        try:
            wait_for(
                stdout,
                lambda line: "token=" in line,
                timeout=3,
                process=process,
                stderr_sink=stderr,
                readers=readers,
            )
        except RuntimeError as error:
            message = str(error)
        else:
            raise AssertionError("child exit without a URL should be reported")
    finally:
        _stop_child(process)

    assert "exit=0 (0x00000000)" in message
    assert "smoke startup" in message


def test_wait_for_returns_a_successful_session_url() -> None:
    process, stdout, stderr, readers = _start_child(
        "import time; print('http://127.0.0.1/?token=synthetic', flush=True); time.sleep(5)"
    )

    try:
        line = wait_for(
            stdout,
            lambda value: "token=" in value,
            timeout=3,
            process=process,
            readers=readers,
        )
    finally:
        _stop_child(process)

    assert line == "http://127.0.0.1/?token=synthetic"
    assert process.poll() is not None


def test_wait_for_timeout_is_explicit_and_child_is_reaped() -> None:
    process, stdout, stderr, readers = _start_child("import time; time.sleep(30)")

    try:
        try:
            wait_for(
                stdout,
                lambda line: "token=" in line,
                timeout=0.25,
                process=process,
                readers=readers,
            )
        except TimeoutError as error:
            message = str(error)
        else:
            raise AssertionError("missing URL should time out")
    finally:
        _stop_child(process)

    assert "timed out" in message
    assert "stdout=<empty>" in message
    assert process.poll() is not None


def test_process_output_is_redacted_and_bounded() -> None:
    text = _redact_process_output(["url token=private csrf_token=also-private", "x" * 5000])

    assert "private" not in text
    assert "also-private" not in text
    assert "[truncated]" in text
    assert len(text) <= 1700


def test_unusable_bootstrap_diagnostic_redacts_token() -> None:
    from tools.diagnostics.packaged_api_smoke import _invalid_bootstrap_message

    message = _invalid_bootstrap_message("invalid URL", "token=private-value")

    assert "private-value" not in message
    assert "token=<redacted>" in message
