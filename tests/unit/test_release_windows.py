from __future__ import annotations

import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest
from p2_dataset_fixtures import build_package

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
        "Select-PhotoSearchDataRoot.ps1",
        "Set-PhotoSearch-Data.cmd",
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
    ocr_files = {
        "classification": ("ch_ppocr_mobile_v2.0_cls_infer.onnx", b"cls-ocr"),
        "config": ("config.yaml", b"ocr-config"),
        "detection": ("ch_PP-OCRv4_det_infer.onnx", b"det-ocr"),
        "recognition": ("ch_PP-OCRv4_rec_infer.onnx", b"rec-ocr"),
    }
    artifacts = {}
    for role, (filename, payload) in ocr_files.items():
        relative = (
            "_internal/rapidocr_onnxruntime/config.yaml"
            if role == "config"
            else f"_internal/rapidocr_onnxruntime/models/{filename}"
        )
        resource = bundle / relative
        resource.parent.mkdir(parents=True, exist_ok=True)
        resource.write_bytes(payload)
        artifacts[role] = {
            "path": f"ocr/ppocr-v4-mobile/{filename}",
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    manifest_text = json.dumps({"ocr": {"artifacts": artifacts}})
    (manifest_dir / "face-ocr.json").write_text(manifest_text, encoding="utf-8")
    trusted_manifest = root / "trusted-face-ocr.json"
    trusted_manifest.write_text(manifest_text, encoding="utf-8")
    release.SOURCE_OCR_MANIFEST = trusted_manifest
    (bundle / "_internal/photo_search/web/app.js").write_text("fixture", encoding="utf-8")
    place_root = bundle / "_internal/place-datasets"
    place_id = "geonames-asia-cities500-v1-cn-c3c03b41ffe4cbe3"
    package = place_root / place_id
    manifest = build_package(package, dataset_id=place_id, previous_dataset_id="geonames-asia-cities500-v1")
    pin = {
        "dataset_id": place_id,
        "content_sha256": manifest["content_sha256"],
        "file_count": len(manifest["files"]),
        "uncompressed_bytes": sum(int(item["byte_size"]) for item in manifest["files"]),
        "license_review": "SYNTHETIC TEST FIXTURE",
        "release_pin_status": "TEST ONLY",
    }
    (place_root / "release-resource.json").write_text(json.dumps(pin), encoding="utf-8")
    trusted_pin = root / "trusted-place-dataset.json"
    trusted_pin.write_text(json.dumps(pin), encoding="utf-8")
    release.PLACE_DATASET_RELEASE_MANIFEST = trusted_pin
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


def test_repository_place_dataset_pin_fails_closed_while_candidate_is_pending() -> None:
    from photo_search.places.bundled_dataset import validate_release_pin

    pending = json.loads(release.PLACE_DATASET_RELEASE_MANIFEST.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="release pin is incomplete"):
        validate_release_pin(pending)


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
    assert details["entries"] > 15
    assert len(details["sha256"]) == 64
    assert details["place_dataset"]["dataset_id"] == "geonames-asia-cities500-v1-cn-c3c03b41ffe4cbe3"
    assert details["place_dataset"]["zip_increment_bytes"] > 0
    with zipfile.ZipFile(part) as archive:
        assert archive.testzip() is None
        assert all(name.startswith("PhotoSearch/") for name in archive.namelist())
    with pytest.raises(FileExistsError):
        release.create_zip(bundle, part)


def test_bundle_rejects_a_changed_pinned_place_dataset(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    package_file = next((bundle / "_internal/place-datasets").glob("*/places.jsonl"))
    package_file.write_text("not the pinned source content", encoding="utf-8")
    with pytest.raises(ValueError, match="place dataset"):
        release.validate_bundle(bundle)


def test_zip_rejects_place_content_changed_after_archive_creation(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    part = tmp_path / "release.zip.part"
    release.create_zip(bundle, part)
    changed = tmp_path / "changed.zip"
    with zipfile.ZipFile(part) as source, zipfile.ZipFile(changed, "w", zipfile.ZIP_DEFLATED) as target:
        place_member = next(name for name in source.namelist() if name.endswith("/places.jsonl"))
        for name in source.namelist():
            payload = source.read(name)
            if name == place_member:
                payload += b"tampered\n"
            target.writestr(name, payload)
    with pytest.raises(ValueError, match="SHA-256"):
        release.verify_zip(changed)


def test_archive_refuses_private_files_and_unsafe_names(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    (bundle / "models" / "secret.safetensors").parent.mkdir(parents=True)
    (bundle / "models" / "secret.safetensors").write_bytes(b"private")
    with pytest.raises(ValueError, match="private configuration"):
        release.archive_entries(bundle)
    for name in ("/PhotoSearch/file", "C:/PhotoSearch/file", "PhotoSearch/../escape", "PhotoSearch\\file"):
        with pytest.raises(ValueError, match="path"):
            release._validate_zip_name(name)


def test_archive_allows_dependency_package_named_models(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    stub = bundle / "_internal/tokenizers/models/__init__.pyi"
    stub.parent.mkdir(parents=True)
    stub.write_text("class Model: ...\n", encoding="utf-8")
    entries = release.archive_entries(bundle)
    assert any(name.endswith("_internal/tokenizers/models/__init__.pyi") for _, name in entries)
    weight = stub.parent / "weights.safetensors"
    weight.write_bytes(b"private model weight")
    with pytest.raises(ValueError, match="private configuration"):
        release.archive_entries(bundle)


def test_archive_allows_only_manifest_pinned_ocr_resources(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    entries = release.archive_entries(bundle)
    assert sum(name.endswith(".onnx") for _, name in entries) == 3
    unexpected = bundle / "_internal/other/model.onnx"
    unexpected.parent.mkdir(parents=True)
    unexpected.write_bytes(b"unapproved weight")
    with pytest.raises(ValueError, match="private configuration"):
        release.archive_entries(bundle)


def test_archive_rejects_modified_pinned_ocr_resource(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    resource = bundle / "_internal/rapidocr_onnxruntime/models/ch_PP-OCRv4_det_infer.onnx"
    resource.write_bytes(b"tamper!")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        release.archive_entries(bundle)


def test_archive_rejects_bundle_manifest_and_ocr_weight_changed_together(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    resource = bundle / "_internal/rapidocr_onnxruntime/models/ch_PP-OCRv4_det_infer.onnx"
    replacement = b"another"
    resource.write_bytes(replacement)
    manifest_path = bundle / "_internal/tools/prepare_models/manifests/face-ocr.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["ocr"]["artifacts"]["detection"].update(
        bytes=len(replacement), sha256=hashlib.sha256(replacement).hexdigest()
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid pinned OCR metadata"):
        release.archive_entries(bundle)


def test_build_sanitizes_local_model_root_from_bundle_manifest(tmp_path: Path) -> None:
    build_path = Path(__file__).resolve().parents[2] / "tools" / "packaging" / "build_onedir.py"
    build_spec = importlib.util.spec_from_file_location("build_onedir_test", build_path)
    assert build_spec and build_spec.loader
    build = importlib.util.module_from_spec(build_spec)
    build_spec.loader.exec_module(build)
    bundle = tmp_path / "PhotoSearch"
    manifest = bundle / "_internal/tools/prepare_models/manifests/face-ocr.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps({"resolved_model_root": "C:/private/PhotoSearchData/models", "ocr": {"artifacts": {}}}),
        encoding="utf-8",
    )
    validation_manifest = manifest.parent / "face-ocr-validation.json"
    validation_manifest.write_text(
        json.dumps({"resolved_model_root": "C:/private/PhotoSearchData/models"}), encoding="utf-8"
    )
    build.sanitize_bundled_manifests(bundle)
    assert "resolved_model_root" not in json.loads(manifest.read_text(encoding="utf-8"))
    assert not manifest.with_suffix(".json.tmp").exists()
    assert not validation_manifest.exists()


def test_bundle_validation_rejects_validation_only_manifests(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    validation_manifest = bundle / "_internal/tools/prepare_models/manifests/face-ocr-validation.json"
    validation_manifest.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="validation-only manifests"):
        release.validate_bundle(bundle)


def test_archive_refuses_local_model_root_in_bundle_manifest(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    manifest_path = bundle / "_internal/tools/prepare_models/manifests/face-ocr.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["resolved_model_root"] = "C:/private/PhotoSearchData/models"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="private local model-root path"):
        release.archive_entries(bundle)


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


def test_portable_launchers_share_selected_root() -> None:
    packaging = Path(__file__).resolve().parents[2] / "tools" / "packaging"
    start = (packaging / "Start-PhotoSearch.cmd").read_text(encoding="utf-8")
    download = (packaging / "Download-Qwen.cmd").read_text(encoding="utf-8")
    setup = (packaging / "Setup-AI-Models.cmd").read_text(encoding="utf-8")
    selector = (packaging / "Select-PhotoSearchDataRoot.ps1").read_text(encoding="utf-8-sig")
    assert "Select-PhotoSearchDataRoot.ps1" in start
    assert "-Mode Start" in start
    assert "Select-PhotoSearchDataRoot.ps1" in download
    assert "-Mode Download" in download
    assert "Select-PhotoSearchDataRoot.ps1" in setup
    assert "-Mode Setup" in setup
    assert "Setup" in selector
    assert "portable-operation.lock" in selector
    assert "portable-data-root.txt" in selector
    assert "'--data-root', $selected" in selector
    assert "Move-Item -LiteralPath $temporary -Destination $script:ConfigPath -Force | Out-Null" in selector
    assert "Read-Host" in selector


def test_release_script_publishes_without_overwriting() -> None:
    packaging = Path(__file__).resolve().parents[2] / "tools" / "packaging"
    script = (packaging / "release_windows.ps1").read_text(encoding="utf-8")
    assert "[IO.File]::Move($shaPart, $shaOutput)" in script
    assert "[IO.File]::Move($reportPart, $reportOutput)" in script
    assert "[IO.File]::Move($zipPart, $zipOutput)" in script
    assert "-NoClobber" not in script
