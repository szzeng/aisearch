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

SOURCE_OCR_MANIFEST = Path(__file__).resolve().parents[2] / "tools" / "prepare_models" / "manifests" / "face-ocr.json"
PLACE_DATASET_RELEASE_MANIFEST = Path(__file__).resolve().parents[2] / "tools" / "places" / "bundled-dataset-release.json"


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


def validate_bundle(bundle: Path) -> dict[str, Any]:
    executable = bundle / "PhotoSearch.exe"
    required = (
        executable,
        bundle / "Start-PhotoSearch.cmd",
        bundle / "Download-Qwen.cmd",
        bundle / "Select-PhotoSearchDataRoot.ps1",
        bundle / "Set-PhotoSearch-Data.cmd",
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
        "face-ocr.json",
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
    validation_manifests = sorted(
        path.name
        for path in manifest_dir.glob("*-validation.json")
        if path.is_file()
    )
    if validation_manifests:
        raise ValueError("validation-only manifests must not be bundled: " + ", ".join(validation_manifests))
    pinned_ocr_bundle_resources(bundle)
    return _validate_place_dataset_bundle(bundle)


def _validate_place_dataset_bundle(bundle: Path) -> dict[str, Any]:
    root = bundle / "_internal" / "place-datasets"
    pin_path = root / "release-resource.json"
    try:
        pin = json.loads(pin_path.read_text(encoding="utf-8"))
        project_root = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(project_root / "src"))
        from photo_search.places.bundled_dataset import (
            validate_pinned_package,
            validate_release_pin,
        )

        validate_release_pin(pin)
        trusted = json.loads(PLACE_DATASET_RELEASE_MANIFEST.read_text(encoding="utf-8"))
        validate_release_pin(trusted)
        if pin != trusted:
            raise ValueError("bundled resource pin does not match the trusted release manifest")
        dataset_root = root / str(pin["dataset_id"])
        validate_pinned_package(dataset_root, pin)
    except Exception as exc:
        raise ValueError(f"bundled place dataset is missing, unpinned, or invalid: {exc}") from exc
    expected_names = {"release-resource.json", str(pin["dataset_id"])}
    if {path.name for path in root.iterdir()} != expected_names:
        raise ValueError("bundled place dataset directory contains unexpected files")
    return pin


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


def pinned_ocr_bundle_resources(bundle: Path) -> dict[str, tuple[int, str]]:
    """Return the only model resources allowed in the portable bundle, after hashing them."""
    manifest_path = bundle / "_internal/tools/prepare_models/manifests/face-ocr.json"
    try:
        trusted_artifacts = json.loads(SOURCE_OCR_MANIFEST.read_text(encoding="utf-8"))["ocr"]["artifacts"]
        bundled_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        bundled_artifacts = bundled_manifest["ocr"]["artifacts"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("pinned OCR resource manifest is missing or invalid") from exc
    if not isinstance(trusted_artifacts, dict) or not isinstance(bundled_artifacts, dict):
        raise ValueError("pinned OCR resource manifest is missing or invalid")
    if "resolved_model_root" in bundled_manifest:
        raise ValueError("private local model-root path is present in bundled OCR manifest")
    role_filenames = {
        "classification": "ch_ppocr_mobile_v2.0_cls_infer.onnx",
        "config": "config.yaml",
        "detection": "ch_PP-OCRv4_det_infer.onnx",
        "recognition": "ch_PP-OCRv4_rec_infer.onnx",
    }
    expected: dict[str, tuple[int, str]] = {}
    for role, filename in role_filenames.items():
        item = trusted_artifacts.get(role)
        bundled_item = bundled_artifacts.get(role)
        expected_source_path = f"ocr/ppocr-v4-mobile/{filename}"
        if (
            not isinstance(item, dict)
            or not isinstance(bundled_item, dict)
            or Path(str(item.get("path", ""))).as_posix() != expected_source_path
            or Path(str(bundled_item.get("path", ""))).as_posix() != expected_source_path
        ):
            raise ValueError(f"invalid pinned OCR path for {role}")
        relative = (
            "_internal/rapidocr_onnxruntime/config.yaml"
            if role == "config"
            else f"_internal/rapidocr_onnxruntime/models/{filename}"
        )
        try:
            size = int(item["bytes"])
            digest = str(item["sha256"]).lower()
            bundled_size = int(bundled_item["bytes"])
            bundled_digest = str(bundled_item["sha256"]).lower()
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid pinned OCR metadata for {role}") from exc
        if (
            size <= 0
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or bundled_size != size
            or bundled_digest != digest
        ):
            raise ValueError(f"invalid pinned OCR metadata for {role}")
        expected[relative] = (size, digest)
    if len(expected) != 4:
        raise ValueError("pinned OCR resource manifest must contain four artifacts")
    for relative, (size, expected_digest) in expected.items():
        path = bundle / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"pinned OCR resource is missing from bundle: {relative}")
        if path.stat().st_size != size:
            raise ValueError(f"pinned OCR resource size mismatch: {relative}")
        digest_state = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest_state.update(chunk)
        digest = digest_state.hexdigest()
        if digest != expected_digest:
            raise ValueError(f"pinned OCR resource SHA-256 mismatch: {relative}")
    return expected


def archive_entries(bundle: Path) -> list[tuple[Path, str]]:
    bundle = bundle.resolve()
    allowed_ocr_resources = pinned_ocr_bundle_resources(bundle)
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
        # ``models`` is also a normal Python package directory (for example,
        # tokenizers/models). Reject the bundle's top-level user model directory;
        # model payloads elsewhere remain blocked by their file extensions.
        user_model_directory = Path(relative).parts[0].casefold() == "models"
        is_pinned_ocr_resource = relative in allowed_ocr_resources
        if (
            user_model_directory
            or parts.intersection({"photosearchdata", "user-data", "private"})
            or (path.suffix.casefold() in forbidden_suffixes and not is_pinned_ocr_resource)
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
    place_pin = validate_bundle(bundle)
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
    place_prefix = f"PhotoSearch/_internal/place-datasets/{place_pin['dataset_id']}/"
    place_increment_bytes = 0
    with zipfile.ZipFile(destination_part, "r") as archive, destination_part.open("rb") as raw:
        for info in archive.infolist():
            if not info.filename.startswith(place_prefix) and info.filename != "PhotoSearch/_internal/place-datasets/release-resource.json":
                continue
            raw.seek(info.header_offset)
            local_header = raw.read(30)
            if len(local_header) != 30:
                raise ValueError("ZIP local header is truncated")
            local_name_bytes = int.from_bytes(local_header[26:28], "little")
            local_extra_bytes = int.from_bytes(local_header[28:30], "little")
            encoded_name_bytes = len(info.filename.encode("utf-8"))
            if local_name_bytes != encoded_name_bytes:
                raise ValueError("ZIP local filename length does not match its directory entry")
            place_increment_bytes += (
                info.compress_size
                + 30
                + local_name_bytes
                + local_extra_bytes
                + 46
                + encoded_name_bytes
                + len(info.extra)
                + len(info.comment)
            )
    digest = hashlib.sha256()
    with destination_part.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return {
        "zip_bytes": destination_part.stat().st_size,
        "sha256": digest.hexdigest().upper(),
        "entries": len(entries),
        "place_dataset": {
            "dataset_id": place_pin["dataset_id"],
            "content_sha256": place_pin["content_sha256"],
            "uncompressed_bytes": int(place_pin["uncompressed_bytes"]),
            "zip_increment_bytes": place_increment_bytes,
            "license_review": place_pin["license_review"],
            "release_pin_status": place_pin["release_pin_status"],
        },
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
        pin_name = "PhotoSearch/_internal/place-datasets/release-resource.json"
        if pin_name not in actual:
            raise ValueError("ZIP is missing the pinned offline place dataset")
        pin = json.loads(archive.read(pin_name).decode("utf-8"))
        project_root = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(project_root / "src"))
        from photo_search.catalog import schema as schema_module
        from photo_search.geo import dataset as dataset_module
        from photo_search.places.bundled_dataset import validate_release_pin

        validate_release_pin(pin)
        trusted = json.loads(PLACE_DATASET_RELEASE_MANIFEST.read_text(encoding="utf-8"))
        if pin != trusted:
            raise ValueError("ZIP resource pin does not match the trusted release manifest")
        dataset_id = str(pin["dataset_id"])
        base = f"PhotoSearch/_internal/place-datasets/{dataset_id}/"
        manifest_name = base + dataset_module.MANIFEST_FILENAME
        if manifest_name not in actual:
            raise ValueError("ZIP is missing the place dataset manifest")
        manifest = dataset_module.validate_manifest(
            json.loads(archive.read(manifest_name).decode("utf-8")),
            supported_schema_version=int(schema_module.SUPPORTED_SCHEMA_VERSION),
        )
        files = tuple(dataset_module.DatasetFile(**item) for item in manifest["files"])
        if (
            manifest["dataset_id"] != dataset_id
            or manifest["content_sha256"] != pin["content_sha256"]
            or len(files) != int(pin["file_count"])
            or sum(item.byte_size for item in files) != int(pin["uncompressed_bytes"])
            or dataset_module.declared_content_sha256(files) != pin["content_sha256"]
        ):
            raise ValueError("ZIP place dataset identity or content digest does not match its release pin")
        expected_files = {manifest_name}
        for item in files:
            name = base + item.relative_path
            expected_files.add(name)
            try:
                source = archive.open(name, "r")
            except KeyError as exc:
                raise ValueError(f"ZIP is missing place dataset content: {item.relative_path}") from exc
            digest = hashlib.sha256()
            size = 0
            with source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
            if size != item.byte_size or digest.hexdigest() != item.sha256:
                raise ValueError(f"ZIP place dataset content failed SHA-256: {item.relative_path}")
        actual_place = {name for name in actual if name.startswith(base)}
        if actual_place != expected_files:
            raise ValueError("ZIP place dataset contains unlisted or missing files")
        place_root = "PhotoSearch/_internal/place-datasets/"
        dataset_roots = {
            name[len(place_root) :].split("/", 1)[0]
            for name in actual
            if name.startswith(place_root)
        }
        if dataset_roots != {"release-resource.json", dataset_id}:
            raise ValueError("ZIP contains unexpected place dataset resources")
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
