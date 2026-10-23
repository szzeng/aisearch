"""Small, testable helpers for the Windows one-command portable release flow."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


def release_metadata(root: Path) -> dict[str, str]:
    project = (root / "pyproject.toml").read_text(encoding="utf-8")
    api = (root / "src/photo_search/api/routes.py").read_text(encoding="utf-8")
    lock = (root / "uv.lock").read_text(encoding="utf-8")
    version_match = re.search(r'^version\s*=\s*"([^"]+)"', project, re.MULTILINE)
    app_match = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', api, re.MULTILINE)
    lock_match = re.search(
        r'\[\[package\]\]\s*\nname = "photo-search"\s*\nversion = "([^"]+)"',
        lock,
    )
    if not version_match or not app_match or not lock_match:
        raise ValueError("could not read application versions from pyproject.toml, routes.py and uv.lock")
    versions = {version_match.group(1), app_match.group(1), lock_match.group(1)}
    if len(versions) != 1:
        raise ValueError(
            "version mismatch: "
            f"pyproject={version_match.group(1)}, APP_VERSION={app_match.group(1)}, "
            f"uv.lock={lock_match.group(1)}"
        )
    return {"version": version_match.group(1)}


def validate_bundle(bundle: Path) -> None:
    executable = bundle / "PhotoSearch.exe"
    required = (
        executable,
        bundle / "Start-PhotoSearch.cmd",
        bundle / "Download-Qwen.cmd",
        bundle / "README-PORTABLE.txt",
        bundle / "NEW-PC-QUICK-START.txt",
        bundle / "_internal" / "tools" / "prepare_models" / "manifests",
        bundle / "_internal" / "photo_search" / "catalog" / "migrations",
        bundle / "_internal" / "photo_search" / "web",
    )
    missing = [str(path.relative_to(bundle)) for path in required if not path.exists()]
    manifest_dir = bundle / "_internal" / "tools" / "prepare_models" / "manifests"
    required_manifests = (
        "qwen3-vl-embedding-2b-exif-v2.json",
        "qwen3-vl-embedding-2b-1024d-exif-v2.json",
    )
    missing.extend(
        f"_internal/tools/prepare_models/manifests/{name}"
        for name in required_manifests
        if not (manifest_dir / name).is_file()
    )
    if missing:
        raise ValueError("built onedir is missing required files/directories: " + ", ".join(missing))
    if not executable.is_file() or executable.stat().st_size <= 0:
        raise ValueError("PhotoSearch.exe is missing or empty")


def require_free_space(free_bytes: int, required_bytes: int) -> None:
    if int(free_bytes) < int(required_bytes):
        raise ValueError(f"insufficient disk space: available={free_bytes}, estimated_required={required_bytes}")


def require_new_outputs(paths: list[Path]) -> None:
    existing = [str(path) for path in paths if path.exists()]
    if existing:
        raise FileExistsError("refusing to overwrite release output: " + ", ".join(existing))


def require_success_exit(exit_code: int, phase: str) -> None:
    if int(exit_code) != 0:
        raise RuntimeError(f"{phase} failed with exit code {exit_code}")


def summarize_gpu_smoke(report_path: Path) -> dict[str, Any]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    device = report.get("device") or {}
    runtime = report.get("runtime") or {}
    images = report.get("images") or []
    norms = [float(item.get("l2_norm", math.nan)) for item in images]
    scores = [float(item.get("cosine", math.nan)) for item in report.get("top_k") or []]
    if (
        report.get("status") != "ok"
        or device.get("device") != "cuda"
        or device.get("provider") != "PyTorchCUDA"
        or device.get("policy") != "gpu_required"
        or device.get("cpu_fallback_disabled") is not True
        or runtime.get("cuda_available") is not True
        or runtime.get("runtime_backend") != "pytorch"
        or not math.isfinite(float(report.get("query_l2_norm", math.nan)))
        or abs(float(report.get("query_l2_norm", math.nan)) - 1.0) > 1e-5
        or len(images) != 3
        or any(not math.isfinite(norm) or abs(norm - 1.0) > 1e-5 for norm in norms)
        or len(scores) != 3
        or any(not math.isfinite(score) for score in scores)
    ):
        raise ValueError("frozen Qwen GPU smoke report did not meet the CUDA/normalization checks")
    return {
        "status": "GPU_VERIFIED",
        "provider": "PyTorchCUDA",
        "dimension": 512,
        "cpu_fallback_disabled": True,
        "synthetic_images": len(images),
        "query_l2_norm": float(report["query_l2_norm"]),
        "image_l2_norms": norms,
    }


def archive_entries(bundle: Path) -> list[tuple[Path, str]]:
    bundle = bundle.resolve()
    entries: list[tuple[Path, str]] = []
    names: set[str] = set()
    for path in sorted(bundle.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"symlinks are not allowed in release ZIP: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(bundle).as_posix()
        parts = {part.casefold() for part in Path(relative).parts}
        forbidden_suffixes = {
            ".db", ".sqlite", ".sqlite3", ".safetensors", ".onnx", ".pt", ".pth", ".bin"
        }
        if (
            parts.intersection({"models", "photosearchdata", "user-data", "private"})
            or path.suffix.casefold() in forbidden_suffixes
            or path.name.casefold() in {".env", ".env.local", "config.local.json", "settings.private.json"}
            or path.name.casefold().endswith((".db-wal", ".db-shm", ".db-journal", ".sqlite-wal", ".sqlite-shm"))
        ):
            raise ValueError(f"user data, model weights or private configuration in bundle: {relative}")
        name = f"PhotoSearch/{relative}"
        _validate_zip_name(name)
        if name in names:
            raise ValueError(f"duplicate ZIP entry: {name}")
        names.add(name)
        entries.append((path, name))
    if not entries:
        raise ValueError("cannot archive an empty onedir")
    return entries


def _validate_zip_name(name: str) -> None:
    normalized = PurePosixPath(name)
    if name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name):
        raise ValueError(f"absolute ZIP path is not allowed: {name}")
    if ".." in normalized.parts or "\\" in name or "\x00" in name:
        raise ValueError(f"unsafe ZIP path: {name}")


def create_zip(bundle: Path, destination_part: Path) -> dict[str, Any]:
    """Stream files into a ZIP64 archive and verify every member before returning."""
    validate_bundle(bundle)
    entries = archive_entries(bundle)
    destination_part.parent.mkdir(parents=True, exist_ok=True)
    if destination_part.exists():
        raise FileExistsError(destination_part)
    with zipfile.ZipFile(
        destination_part,
        mode="x",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=1,
        allowZip64=True,
    ) as archive:
        for path, name in entries:
            info = zipfile.ZipInfo.from_file(path, name)
            info.compress_type = zipfile.ZIP_DEFLATED
            with path.open("rb") as source, archive.open(info, "w", force_zip64=True) as target:
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
    verify_zip(destination_part)
    digest = hashlib.sha256()
    with destination_part.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return {
        "zip_bytes": destination_part.stat().st_size,
        "sha256": digest.hexdigest().upper(),
        "entries": len(entries),
    }


def verify_zip(path: Path) -> list[str]:
    with zipfile.ZipFile(path, "r") as archive:
        bad_member = archive.testzip()
        if bad_member is not None:
            raise ValueError(f"ZIP CRC check failed at {bad_member}")
        actual = archive.namelist()
        if len(actual) != len(set(actual)):
            raise ValueError("ZIP contains duplicate entries")
        for name in actual:
            _validate_zip_name(name)
        if not actual or any(not name.startswith("PhotoSearch/") for name in actual):
            raise ValueError("ZIP must contain files under one PhotoSearch/ top-level directory")
        return actual


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    metadata_parser = subparsers.add_parser("metadata")
    metadata_parser.add_argument("--root", type=Path, required=True)
    bundle_parser = subparsers.add_parser("validate-bundle")
    bundle_parser.add_argument("--bundle", type=Path, required=True)
    archive_parser = subparsers.add_parser("archive")
    archive_parser.add_argument("--bundle", type=Path, required=True)
    archive_parser.add_argument("--part", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify-zip")
    verify_parser.add_argument("--path", type=Path, required=True)
    space_parser = subparsers.add_parser("preflight-space")
    space_parser.add_argument("--free", type=int, required=True)
    space_parser.add_argument("--required", type=int, required=True)
    output_parser = subparsers.add_parser("preflight-output")
    output_parser.add_argument("paths", nargs="+", type=Path)
    exit_parser = subparsers.add_parser("preflight-exit")
    exit_parser.add_argument("--code", type=int, required=True)
    exit_parser.add_argument("--phase", required=True)
    gpu_parser = subparsers.add_parser("gpu-summary")
    gpu_parser.add_argument("--path", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "metadata":
            result = release_metadata(args.root.resolve())
        elif args.command == "validate-bundle":
            validate_bundle(args.bundle.resolve())
            result = {"valid": True}
        elif args.command == "archive":
            result = create_zip(args.bundle.resolve(), args.part.resolve())
        elif args.command == "verify-zip":
            result = {"valid": True, "entries": len(verify_zip(args.path.resolve()))}
        elif args.command == "preflight-space":
            require_free_space(args.free, args.required)
            result = {"valid": True}
        elif args.command == "preflight-output":
            require_new_outputs(args.paths)
            result = {"valid": True}
        elif args.command == "gpu-summary":
            result = summarize_gpu_smoke(args.path.resolve())
        else:
            require_success_exit(args.code, args.phase)
            result = {"valid": True}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f"release helper failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
