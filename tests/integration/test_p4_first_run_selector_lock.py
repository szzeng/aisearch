from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name != "nt", reason="portable launcher lock uses Windows FileShare semantics")
@pytest.mark.parametrize("mode", ("Configure", "Start", "Download", "Setup"))
def test_selector_modes_refuse_while_another_process_holds_operation_lock(
    tmp_path: Path, mode: str
) -> None:
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell is not installed")

    packaging = Path(__file__).resolve().parents[2] / "tools" / "packaging"
    selector = packaging / "Select-PhotoSearchDataRoot.ps1"
    appdata = tmp_path / "isolated-appdata"
    ready = tmp_path / "holder-ready.txt"
    holder_script = tmp_path / "hold-operation-lock.ps1"
    holder_script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "$config = Join-Path $env:APPDATA 'PhotoSearch'\n"
        "[IO.Directory]::CreateDirectory($config) | Out-Null\n"
        "$path = Join-Path $config 'portable-operation.lock'\n"
        "$lock = [IO.File]::Open($path, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)\n"
        f"[IO.File]::WriteAllText('{ready}', 'ready')\n"
        "[Console]::ReadLine() | Out-Null\n"
        "$lock.Dispose()\n",
        encoding="utf-8",
        newline="\n",
    )
    env = os.environ.copy()
    env["APPDATA"] = str(appdata)
    holder = subprocess.Popen(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(holder_script)],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        text=True,
    )
    contender: subprocess.Popen[str] | None = None
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and holder.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.exists(), "lock-holder process did not acquire the operation lock"

        contender = subprocess.Popen(
            [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(selector), "-Mode", mode],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
        )
        # If the selector incorrectly acquires the lock, it blocks at Read-Host because
        # this pipe remains open. Correct behavior exits immediately with the lock error.
        assert contender.wait(timeout=10) == 1
        output = contender.stdout.read() if contender.stdout is not None else b""
        assert b"PhotoSearch.exe" not in output, "selector exited at the missing-executable check instead of rejecting the lock"
    finally:
        if contender is not None and contender.poll() is None:
            contender.kill()
            contender.wait(timeout=5)
        if holder.poll() is None:
            assert holder.stdin is not None
            holder.stdin.write("\n")
            holder.stdin.flush()
            holder.wait(timeout=10)
