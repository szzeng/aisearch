from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[2] / "tools" / "packaging" / "release_windows.py"
_SPEC = importlib.util.spec_from_file_location("release_windows", _MODULE_PATH)
assert _SPEC and _SPEC.loader
release = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(release)


def make_bundle(root: Path) -> Path:
    bundle = root / "PhotoSearch"
    for name in (
        "PhotoSearch.exe",
        "Start-PhotoSearch.cmd",
        "Download-Qwen.cmd",
        "README-PORTABLE.txt",
        "NEW-PC-QUICK-START.txt",
    ):
        path = bundle / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    for directory in (
        "_internal/tools/prepare_models/manifests",
        "_internal/photo_search/catalog/migrations",
        "_internal/photo_search/web",
    ):
        (bundle / directory).mkdir(parents=True, exist_ok=True)
    manifest_dir = bundle / "_internal/tools/prepare_models/manifests"
    (manifest_dir / "qwen3-vl-embedding-2b-exif-v2.json").write_text("{}", encoding="utf-8")
    (manifest_dir / "qwen3-vl-embedding-2b-1024d-exif-v2.json").write_text("{}", encoding="utf-8")
    (bundle / "_internal/photo_search/web/app.js").write_text("fixture", encoding="utf-8")
    return bundle


def test_version_sources_must_match(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('version = "1.2.3"\n', encoding="utf-8")
    api = tmp_path / "src/photo_search/api/routes.py"
    api.parent.mkdir(parents=True)
    api.write_text('APP_VERSION = "1.2.3"\n', encoding="utf-8")
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "photo-search"\nversion = "1.2.3"\n', encoding="utf-8"
    )
    assert release.release_metadata(tmp_path) == {"version": "1.2.3"}
    api.write_text('APP_VERSION = "1.2.4"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="version mismatch"):
        release.release_metadata(tmp_path)


def test_bundle_required_paths_and_missing_executable(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    release.validate_bundle(bundle)
    (bundle / "PhotoSearch.exe").unlink()
    with pytest.raises(ValueError, match="PhotoSearch.exe"):
        release.validate_bundle(bundle)
    (bundle / "PhotoSearch.exe").write_bytes(b"fixture")
    (bundle / "_internal/tools/prepare_models/manifests/qwen3-vl-embedding-2b-1024d-exif-v2.json").unlink()
    with pytest.raises(ValueError, match="1024d-exif-v2.json"):
        release.validate_bundle(bundle)


def test_archive_is_zip64_readable_and_refuses_overwrite(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    part = tmp_path / "release.zip.part"
    details = release.create_zip(bundle, part)
    assert details["entries"] == 8
    assert len(details["sha256"]) == 64
    with zipfile.ZipFile(part) as archive:
        assert archive.testzip() is None
        assert all(name.startswith("PhotoSearch/") for name in archive.namelist())
    with pytest.raises(FileExistsError):
        release.create_zip(bundle, part)


def test_archive_refuses_private_files_and_unsafe_names(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    (bundle / "models" / "secret.safetensors").parent.mkdir(parents=True)
    (bundle / "models" / "secret.safetensors").write_bytes(b"private")
    with pytest.raises(ValueError, match="private configuration"):
        release.archive_entries(bundle)
    for name in ("/PhotoSearch/file", "C:/PhotoSearch/file", "PhotoSearch/../escape", "PhotoSearch\\file"):
        with pytest.raises(ValueError, match="path"):
            release._validate_zip_name(name)


def test_preflight_guards_for_space_outputs_and_build_exit(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="insufficient disk space"):
        release.require_free_space(10, 11)
    release.require_free_space(11, 11)
    existing = tmp_path / "existing.zip"
    existing.write_bytes(b"prior release")
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        release.require_new_outputs([existing, tmp_path / "new.zip"])
    release.require_new_outputs([tmp_path / "new.zip"])
    with pytest.raises(RuntimeError, match="build failed"):
        release.require_success_exit(1, "build")


def test_zip_integrity_check_rejects_corrupted_payload(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    part = tmp_path / "release.zip.part"
    release.create_zip(bundle, part)
    damaged = bytearray(part.read_bytes())
    damaged[50] ^= 0xFF
    part.write_bytes(damaged)
    with pytest.raises((ValueError, zipfile.BadZipFile, RuntimeError)):
        release.verify_zip(part)


def test_zip_integrity_check_rejects_duplicate_members(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.zip"
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(duplicate, "w") as archive:
            archive.writestr("PhotoSearch/entry.txt", b"first")
            archive.writestr("PhotoSearch/entry.txt", b"second")
    with pytest.raises(ValueError, match="duplicate entries"):
        release.verify_zip(duplicate)


def test_gpu_summary_requires_cuda_and_normalized_synthetic_vectors(tmp_path: Path) -> None:
    report_path = tmp_path / "gpu.json"
    report = {
        "status": "ok",
        "device": {
            "device": "cuda",
            "provider": "PyTorchCUDA",
            "policy": "gpu_required",
            "cpu_fallback_disabled": True,
        },
        "runtime": {"cuda_available": True, "runtime_backend": "pytorch"},
        "query_l2_norm": 1.0,
        "images": [{"id": name, "l2_norm": 1.0} for name in ("red", "green", "blue")],
        "top_k": [{"id": name, "cosine": 0.25} for name in ("red", "green", "blue")],
    }
    report_path.write_text(json.dumps(report), encoding="utf-8")
    summary = release.summarize_gpu_smoke(report_path)
    assert summary["status"] == "GPU_VERIFIED"
    assert summary["dimension"] == 512
    report["device"]["provider"] = "PyTorchCPU"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="CUDA/normalization"):
        release.summarize_gpu_smoke(report_path)
