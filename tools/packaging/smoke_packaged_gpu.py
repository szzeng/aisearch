"""P0-06: smoke the packaged GPU runtime from a Chinese/space path with no network.

Runs the frozen executable twice - once on CUDA and once with an explicit CPU request -
from a copied bundle whose directory name contains Chinese characters and spaces, with the
model directory passed explicitly and every proxy pointed at an unroutable address so an
accidental download would fail instead of silently reaching the network.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from photo_search.config import resolve_data_root
from photo_search.workers.query_encoder import default_model_dir as model_directory

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def default_model_dir() -> Path:
    return model_directory(resolve_data_root())


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


DEFAULT_PACKAGE = REPOSITORY_ROOT / "dist" / "PhotoSearch"
DEFAULT_RESULT = REPOSITORY_ROOT / "docs/reports/P0-06-packaged-gpu-smoke.json"
OFFLINE_ENVIRONMENT = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HTTP_PROXY": "http://127.0.0.1:9",
    "HTTPS_PROXY": "http://127.0.0.1:9",
    "ALL_PROXY": "http://127.0.0.1:9",
    "NO_PROXY": "",
    "PYTHONIOENCODING": "utf-8",
}


def run_packaged(
    executable: Path,
    manifest: Path,
    model_dir: Path,
    device: str,
    working_directory: Path,
    output_path: Path,
) -> dict[str, Any]:
    environment = {**os.environ, **OFFLINE_ENVIRONMENT}
    command = [
        str(executable),
        "demo-search",
        "--device",
        device,
        "--model-dir",
        str(model_dir),
        "--manifest",
        str(manifest),
        "--output",
        str(output_path),
    ]
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=str(working_directory),
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
        check=False,
    )
    elapsed = round(time.perf_counter() - started, 1)
    report = json.loads(output_path.read_text(encoding="utf-8")) if output_path.is_file() else None
    return {
        "device_requested": device,
        "command": command,
        "returncode": completed.returncode,
        "seconds": elapsed,
        "stdout_tail": completed.stdout[-1500:],
        "stderr_tail": completed.stderr[-2000:],
        "report": report,
    }


def check_run(run: dict[str, Any], device: str, label: str) -> list[str]:
    failures: list[str] = []
    report = run["report"]
    if run["returncode"] != 0:
        failures.append(f"{label}: exit code {run['returncode']}")
    if not report:
        failures.append(f"{label}: no JSON report was written")
        return failures
    if report["device"]["device"] != device:
        failures.append(f"{label}: resolved device {report['device']['device']} != {device}")
    if device == "cuda":
        if report["device"]["provider"] != "torch-cuda":
            failures.append(f"{label}: provider {report['device']['provider']}")
        if report["device"]["cpu_fallback_disabled"] is not True:
            failures.append(f"{label}: CPU fallback was not disabled")
        usage = report.get("provider_usage", {})
        if usage.get("backend") != "pytorch" or usage.get("device") != "cuda":
            failures.append(f"{label}: unexpected inference runtime {usage}")
    else:
        if report["device"].get("is_degradation") is not True:
            failures.append(f"{label}: CPU run was not reported as a degradation")
        if report["device"]["reason"] != "explicit CPU request":
            failures.append(f"{label}: unexpected degradation reason {report['device']['reason']}")
    if report["query"] != "红色的图片":
        failures.append(f"{label}: query text was mangled: {report['query']!r}")
    order = [row["id"] for row in report["top_k"]]
    if order[0] != "red":
        failures.append(f"{label}: unexpected Top-1 {order}")
    for row in report["top_k"]:
        cosine = row["cosine"]
        if not -1.0001 <= cosine <= 1.0001:
            failures.append(f"{label}: impossible cosine {cosine}")
    if abs(report["query_l2_norm"] - 1.0) > 1e-5:
        failures.append(f"{label}: query vector is not L2-normalised")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=DEFAULT_PACKAGE)
    parser.add_argument("--model-dir", type=Path, default=default_model_dir())
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--keep-copy", action="store_true")
    args = parser.parse_args()

    package = args.package.resolve()
    executable = package / "PhotoSearch.exe"
    relative_manifest = Path("_internal/photo_search/models/embedding/fg-clip2-base.json")
    result: dict[str, Any] = {
        "status": "running",
        "task": "P0-06",
        "stage": "A11",
        "package": str(package),
        "executable_exists": executable.is_file(),
        "model_dir": str(args.model_dir),
        "environment": OFFLINE_ENVIRONMENT,
    }
    if not executable.is_file():
        result.update({"status": "fail", "message": f"missing executable: {executable}"})
        write_json(args.result.resolve(), result)
        print(result, file=sys.stderr)
        return 1
    temporary_parent = REPOSITORY_ROOT / ".pytest-tmp" / "packaging"
    temporary_parent.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(tempfile.mkdtemp(prefix="P0-06 ", dir=temporary_parent))
    accept_dir = temporary_root / "P0-06 中文 空格 验收"
    accept_dir.mkdir(parents=True, exist_ok=True)
    copied = accept_dir / "PhotoSearch"
    try:
        shutil.copytree(package, copied)
        result["copied_package"] = str(copied)
        result["copied_bytes"] = sum(
            path.stat().st_size for path in copied.rglob("*") if path.is_file()
        )
        copied_executable = copied / "PhotoSearch.exe"
        # Use the copy's own bundled manifest so the smoke also proves the packaged
        # resources resolve from the relocated directory.
        manifest = copied / relative_manifest
        result["manifest"] = str(manifest)
        if not manifest.is_file():
            result.update(
                {"status": "fail", "message": f"missing bundled manifest: {manifest}"}
            )
            write_json(args.result.resolve(), result)
            print(result, file=sys.stderr)
            return 1
        output_directory = temporary_root / "output"
        output_directory.mkdir()
        runs = []
        for device in ("cuda", "cpu"):
            output_path = output_directory / f"demo-{device}.json"
            run = run_packaged(
                copied_executable,
                manifest,
                args.model_dir.resolve(),
                device,
                working_directory=output_directory,
                output_path=output_path,
            )
            run["failures"] = check_run(run, device, f"packaged-{device}")
            runs.append(run)
        result["runs"] = runs
        failures = [failure for run in runs for failure in run["failures"]]
        result["failures"] = failures
        result["status"] = "fail" if failures else "pass"
        write_json(args.result.resolve(), result)
        print(
            {
                "status": result["status"],
                "result": str(args.result.resolve()),
                "seconds": {run["device_requested"]: run["seconds"] for run in runs},
                "failures": failures,
                "devices": {
                    run["device_requested"]: (run["report"] or {}).get("device", {}).get("device")
                    for run in runs
                },
                "top_k": {
                    run["device_requested"]: [
                        row["id"] for row in (run["report"] or {}).get("top_k", [])
                    ]
                    for run in runs
                },
            }
        )
        return 1 if failures else 0
    finally:
        if not args.keep_copy:
            shutil.rmtree(temporary_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
