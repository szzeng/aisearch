"""Read-only preflight for the bounded WeMM quality pilot.

This command deliberately reads catalog identity through a SQLite mode=ro URI and does not
import application startup/catalog APIs. It never opens images, copies vectors, hashes the
catalog, downloads model files, or changes the active profile.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parents[2]
WEMM_WEIGHT_BYTES = 5_441_695_216
WEMM_REQUIRED_FILES = (
    "config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json",
    "processor_config.json", "chat_template.jinja",
    "embedding_chat_template.jinja", "modeling_wemm_embedding.py",
)


def sqlite_readonly_uri(path: Path, *, immutable: bool = False) -> str:
    """Build a SQLite URI that permits reading but cannot write."""
    resolved = path.resolve().as_posix()
    suffix = "&immutable=1" if immutable else ""
    return f"file:{quote(resolved, safe='/:')}?mode=ro{suffix}"


def sqlite_file_state(database: Path) -> dict[str, tuple[int, int] | None]:
    """Capture size/mtime for the DB and existing sidecars around a read snapshot."""
    state: dict[str, tuple[int, int] | None] = {}
    for suffix in ("", "-wal", "-shm"):
        path = Path(f"{database}{suffix}")
        try:
            stat = path.stat()
        except OSError:
            state[suffix or "database"] = None
        else:
            state[suffix or "database"] = (stat.st_size, stat.st_mtime_ns)
    return state


def read_catalog_snapshot(database: Path) -> dict[str, Any]:
    """Read the active profile/generation and bounded counts without migration side effects."""
    wal_exists = Path(f"{database}-wal").exists()
    shm_exists = Path(f"{database}-shm").exists()
    if wal_exists and not shm_exists:
        raise RuntimeError("refusing WAL read: the existing WAL has no SHM sidecar")
    before = sqlite_file_state(database)
    connection = sqlite3.connect(
        sqlite_readonly_uri(database, immutable=not wal_exists), uri=True, timeout=2.0
    )
    try:
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA query_only").fetchone() != (1,):
            raise RuntimeError("SQLite query_only could not be enabled")
        row = connection.execute(
            """SELECT p.embedding_processor_id, p.space_id, p.image_generation_id,
                      g.processor_id, g.dimension, g.state, g.is_active,
                      (SELECT count(*) FROM assets),
                      (SELECT count(*) FROM index_entries ie
                       WHERE ie.generation_id = g.generation_id)
               FROM active_search_profile a
               JOIN search_profiles p ON p.profile_id = a.profile_id
               JOIN index_generations g ON g.generation_id = p.image_generation_id
               WHERE a.state_id = 1"""
        ).fetchone()
        if row is None:
            result = {"available": False, "reason": "no active profile/generation"}
        else:
            result = {
                "available": True,
                "processor_id": row[0],
                "space_id": row[1],
                "generation_id": row[2],
                "generation_processor_id": row[3],
                "dimension": row[4],
                "generation_state": row[5],
                "generation_active": bool(row[6]),
                "asset_count": row[7],
                "generation_vector_count": row[8],
                "binding_consistent": row[0] == row[3] and row[5] == "READY" and bool(row[6]),
            }
    finally:
        connection.close()
    after = sqlite_file_state(database)
    if before != after:
        return {"available": False, "reason": "catalog or SQLite sidecar state changed during read"}
    result["sqlite_files_unchanged_during_read"] = True
    return result


def model_files_snapshot(model_dir: Path, required: tuple[str, ...]) -> dict[str, Any]:
    """Stat only named top-level files; do not recurse through model/cache directories."""
    files: dict[str, dict[str, int | bool]] = {}
    for name in required:
        path = model_dir / name
        try:
            stat = path.stat()
        except OSError:
            files[name] = {"present": False, "bytes": 0}
        else:
            files[name] = {"present": path.is_file(), "bytes": stat.st_size}
    return {
        "named_files_present": all(item["present"] for item in files.values()),
        "integrity_checked": False,
        "runtime_tested": False,
        "files": files,
    }


def gpu_snapshot() -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.free,driver_version", "--format=csv,noheader,nounits"],
            capture_output=True,
            check=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return {"available": False, "reason": type(error).__name__}
    devices = []
    for line in result.stdout.splitlines():
        fields = [part.strip() for part in line.split(",")]
        if len(fields) == 5:
            devices.append({"index": int(fields[0]), "name": fields[1],
                            "memory_total_mib": int(fields[2]), "memory_free_mib": int(fields[3]),
                            "driver": fields[4]})
    return {"available": bool(devices), "devices": devices}


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _has_reparse_component(path: Path) -> bool:
    candidate = path.absolute()
    while candidate != candidate.parent:
        if candidate.is_symlink() or (hasattr(candidate, "is_junction") and candidate.is_junction()):
            return True
        candidate = candidate.parent
    return False


def write_snapshot_exclusive(output_dir: Path, snapshot: dict[str, Any]) -> Path:
    """Write a new private result without following or overwriting an existing leaf."""
    allowed_root = REPO_ROOT / ".tmp/wemm-pilot"
    if _has_reparse_component(output_dir):
        raise ValueError("--out-dir must not contain a symlink or junction")
    if not _within(output_dir, allowed_root):
        raise ValueError("--out-dir must stay inside the ignored project .tmp/wemm-pilot directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    if _has_reparse_component(output_dir):
        raise ValueError("--out-dir must not contain a symlink or junction")
    output_path = output_dir / "preflight.json"
    if _has_reparse_component(output_path):
        raise ValueError("preflight output must not be a symlink or junction")
    payload = json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n"
    descriptor = os.open(output_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)
    return output_path


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    preflight = subparsers.add_parser("preflight", help="Read-only host, model, and catalog identity snapshot")
    preflight.add_argument("--data-root", type=Path, required=True)
    preflight.add_argument("--out-dir", type=Path, default=REPO_ROOT / ".tmp/wemm-pilot")
    preflight.add_argument("--max-assets", type=int, default=50, help="Planned smoke ceiling (1..1000); does not read assets")
    preflight.add_argument("--seed", type=int, default=20261020)
    preflight.add_argument("--device", default="cuda")
    preflight.add_argument("--qwen-model-dir", type=Path)
    preflight.add_argument("--wemm-model-dir", type=Path)
    return parser


def run_preflight(args: argparse.Namespace) -> int:
    output_dir = args.out_dir.absolute()
    if not 1 <= args.max_assets <= 1000:
        raise ValueError("--max-assets must be in 1..1000")
    data_root = args.data_root.resolve()
    qwen_dir = (args.qwen_model_dir or data_root / "models/qwen3_vl_embedding").resolve()
    wemm_dir = (args.wemm_model_dir or data_root / "models/wemm_embedding").resolve()
    database = data_root / "catalog/catalog.db"

    snapshot: dict[str, Any] = {
        "python": sys.version.split()[0],
        "device_requested": args.device,
        "planned_smoke_max_assets": args.max_assets,
        "seed": args.seed,
        "data_root_exists": data_root.is_dir(),
        "catalog_read": {"available": False, "reason": "catalog not found"},
        "qwen_model": model_files_snapshot(qwen_dir, ("config.json", "model.safetensors", "tokenizer.json")),
        "wemm_model": model_files_snapshot(wemm_dir, WEMM_REQUIRED_FILES),
        "gpu": gpu_snapshot(),
        "disk_free_bytes": shutil.disk_usage(output_dir.anchor if output_dir.anchor else REPO_ROOT).free,
        "download_required_bytes": WEMM_WEIGHT_BYTES,
        "download_allowed": False,
        "inference_started": False,
    }
    if database.is_file():
        try:
            snapshot["catalog_read"] = read_catalog_snapshot(database)
        except (sqlite3.Error, RuntimeError) as error:
            snapshot["catalog_read"] = {"available": False, "reason": type(error).__name__}
    requested_gpu = None
    if args.device.startswith("cuda:"):
        try:
            requested_gpu = int(args.device.split(":", 1)[1])
        except ValueError:
            pass
    selected_device = next((item for item in snapshot["gpu"].get("devices", [])
                            if item["index"] == requested_gpu), None)
    snapshot["device_bound_to_reported_gpu"] = selected_device is not None
    snapshot["current_free_memory_can_hold_weights_alone"] = bool(
        selected_device and selected_device["memory_free_mib"] * 1024 * 1024 >= WEMM_WEIGHT_BYTES
    )
    snapshot["integrity_checked"] = False
    snapshot["runtime_tested"] = False
    snapshot["smoke_started"] = False
    snapshot["status"] = "PRECHECK_ONLY"

    output_path = write_snapshot_exclusive(output_dir, snapshot)
    print(json.dumps({
        "result": snapshot["status"],
        "output": output_path.name,
        "python": snapshot["python"],
        "catalog_available": snapshot["catalog_read"]["available"],
        "qwen_named_files_present": snapshot["qwen_model"]["named_files_present"],
        "wemm_named_files_present": snapshot["wemm_model"]["named_files_present"],
        "gpu_available": snapshot["gpu"]["available"],
        "device_bound_to_reported_gpu": snapshot["device_bound_to_reported_gpu"],
        "current_free_memory_can_hold_weights_alone": snapshot["current_free_memory_can_hold_weights_alone"],
        "download_allowed": False,
        "integrity_checked": False,
        "runtime_tested": False,
        "inference_started": False,
    }, ensure_ascii=False))
    return 0


def main() -> int:
    args = make_parser().parse_args()
    if args.command == "preflight":
        return run_preflight(args)
    raise AssertionError("argparse accepted an unsupported command")


if __name__ == "__main__":
    raise SystemExit(main())
