from __future__ import annotations

import zipfile
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

from photo_search.models import first_run, first_run_cli


def _zip(path: Path, entries: list[tuple[str, bytes]]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in entries:
            archive.writestr(name, payload)
    return path


def test_face_zip_extracts_only_pinned_model_files(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "face.zip", [
        ("buffalo_l/det_10g.onnx", b"det"),
        ("buffalo_l/w600k_r50.onnx", b"rec"),
        ("buffalo_l/genderage.onnx", b"ignored"),
    ])
    destination = tmp_path / "out"
    first_run._extract_face_zip(archive, destination)
    assert (destination / "det_10g.onnx").read_bytes() == b"det"
    assert (destination / "w600k_r50.onnx").read_bytes() == b"rec"
    assert not (destination / "genderage.onnx").exists()


def test_face_zip_extracts_flat_official_model_members(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "flat-face.zip", [
        ("det_10g.onnx", b"det"),
        ("w600k_r50.onnx", b"rec"),
        ("genderage.onnx", b"ignored"),
    ])
    destination = tmp_path / "flat-out"
    first_run._extract_face_zip(archive, destination)
    assert (destination / "det_10g.onnx").read_bytes() == b"det"
    assert (destination / "w600k_r50.onnx").read_bytes() == b"rec"
    assert not (destination / "genderage.onnx").exists()


@pytest.mark.parametrize("entries", [
    [("../buffalo_l/det_10g.onnx", b"x"), ("buffalo_l/w600k_r50.onnx", b"y")],
    [("det_10g.onnx", b"x"), ("buffalo_l/w600k_r50.onnx", b"y")],
    [("buffalo_l/det_10g.onnx", b"x"), ("buffalo_l/det_10g.onnx", b"y"),
     ("buffalo_l/w600k_r50.onnx", b"z")],
    [("buffalo_l/det_10g.onnx", b"x")],
])
def test_face_zip_refuses_unsafe_duplicate_or_incomplete_members(tmp_path: Path, entries) -> None:
    archive = _zip(tmp_path / "bad.zip", entries)
    with pytest.raises(ValueError):
        first_run._extract_face_zip(archive, tmp_path / "out")


def test_face_zip_expanded_size_limit_is_enforced(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(first_run, "FACE_MAX_EXPANDED_BYTES", 5)
    archive = _zip(tmp_path / "large.zip", [
        ("buffalo_l/det_10g.onnx", b"123"),
        ("buffalo_l/w600k_r50.onnx", b"456"),
    ])
    with pytest.raises(ValueError, match="expanded-size"):
        first_run._extract_face_zip(archive, tmp_path / "out")


def test_face_license_acknowledgement_precedes_any_filesystem_change(tmp_path: Path) -> None:
    root = tmp_path / "isolated"
    with pytest.raises(ValueError, match="license"):
        first_run.prepare_face(root, source_zip=tmp_path / "absent.zip")
    assert not root.exists()


def test_face_local_zip_publishes_only_after_pinned_verification(tmp_path: Path, monkeypatch) -> None:
    from photo_search.face import weights as face_weights

    root = tmp_path / "isolated-data"
    target_root = root / "models"
    expected = {
        "face/buffalo_l/det_10g.onnx": b"det synthetic",
        "face/buffalo_l/w600k_r50.onnx": b"rec synthetic",
    }
    from photo_search.face.weights import FaceWeights, PinnedFile

    entries = tuple(
        PinnedFile(role, relative, len(content), "synthetic-pin", target_root / relative)
        for role, relative, content in (
            ("detection", "face/buffalo_l/det_10g.onnx", b"det synthetic"),
            ("recognition", "face/buffalo_l/w600k_r50.onnx", b"rec synthetic"),
        )
    )
    pinned = FaceWeights(root=target_root, files=entries)
    monkeypatch.setattr(face_weights, "resolve", lambda _root: pinned)

    def verify(candidate):
        for entry in candidate.files:
            if entry.absolute_path.read_bytes() != expected[entry.relative_path]:
                raise ValueError("synthetic pin mismatch")
        return candidate

    monkeypatch.setattr(face_weights, "verify", verify)
    monkeypatch.setattr(face_weights, "resolve_and_verify", lambda _root: verify(pinned))
    archive = _zip(tmp_path / "face.zip", [
        ("buffalo_l/det_10g.onnx", expected["face/buffalo_l/det_10g.onnx"]),
        ("buffalo_l/w600k_r50.onnx", expected["face/buffalo_l/w600k_r50.onnx"]),
    ])
    result = first_run.prepare_face(root, source_zip=archive, license_accepted=True)
    assert result == {"status": "ok", "component": "face", "source": "local"}
    assert (target_root / "face/buffalo_l/det_10g.onnx").read_bytes() == b"det synthetic"
    assert (target_root / "face/buffalo_l/w600k_r50.onnx").read_bytes() == b"rec synthetic"
    assert (target_root / ".setup-state/face.json").is_file()


def test_face_insufficient_space_refuses_before_creating_data_root(tmp_path: Path, monkeypatch) -> None:
    from photo_search.face import weights as face_weights

    root = tmp_path / "selected-data-root"
    archive = _zip(tmp_path / "face.zip", [
        ("buffalo_l/det_10g.onnx", b"det"),
        ("buffalo_l/w600k_r50.onnx", b"rec"),
    ])
    monkeypatch.setattr(
        face_weights,
        "resolve_and_verify",
        lambda _root: (_ for _ in ()).throw(ValueError("models missing")),
    )
    monkeypatch.setattr(first_run.shutil, "disk_usage", lambda _path: SimpleNamespace(free=0))

    with pytest.raises(ValueError, match="not enough free space"):
        first_run.prepare_face(root, source_zip=archive, license_accepted=True)

    assert not root.exists()


def test_face_oversized_pinned_zip_member_is_rejected_before_publish_copy(
    tmp_path: Path, monkeypatch
) -> None:
    from photo_search.face import weights as face_weights
    from photo_search.face.weights import FaceWeights, PinnedFile

    root = tmp_path / "isolated-data"
    target_root = root / "models"
    entries = (
        PinnedFile("detection", "face/buffalo_l/det_10g.onnx", 3, "pin", target_root / "face/buffalo_l/det_10g.onnx"),
        PinnedFile("recognition", "face/buffalo_l/w600k_r50.onnx", 3, "pin", target_root / "face/buffalo_l/w600k_r50.onnx"),
    )
    pinned = FaceWeights(root=target_root, files=entries)
    monkeypatch.setattr(face_weights, "resolve", lambda _root: pinned)
    monkeypatch.setattr(
        face_weights,
        "resolve_and_verify",
        lambda _root: (_ for _ in ()).throw(ValueError("models missing")),
    )
    monkeypatch.setattr(face_weights, "verify", lambda _candidate: pytest.fail("oversized member reached publish verification"))
    archive = _zip(tmp_path / "oversized-face.zip", [
        ("buffalo_l/det_10g.onnx", b"oversized payload"),
        ("buffalo_l/w600k_r50.onnx", b"rec"),
    ])

    with pytest.raises(ValueError, match="unexpected size: det_10g.onnx"):
        first_run.prepare_face(root, source_zip=archive, license_accepted=True)

    assert not (target_root / "face/buffalo_l").exists()
    assert not (target_root / ".buffalo-l-publish").exists()
    assert not (target_root / ".setup-state/face.json").exists()


def test_face_local_zip_growth_after_preflight_is_bounded(tmp_path: Path, monkeypatch) -> None:
    from photo_search.face import weights as face_weights
    from photo_search.face.weights import FaceWeights, PinnedFile

    root = tmp_path / "isolated-data"
    target_root = root / "models"
    entries = (
        PinnedFile("detection", "face/buffalo_l/det_10g.onnx", 3, "pin", target_root / "face/buffalo_l/det_10g.onnx"),
        PinnedFile("recognition", "face/buffalo_l/w600k_r50.onnx", 3, "pin", target_root / "face/buffalo_l/w600k_r50.onnx"),
    )
    monkeypatch.setattr(face_weights, "resolve", lambda _root: FaceWeights(root=target_root, files=entries))
    monkeypatch.setattr(
        face_weights,
        "resolve_and_verify",
        lambda _root: (_ for _ in ()).throw(ValueError("models missing")),
    )
    archive = _zip(tmp_path / "face.zip", [
        ("buffalo_l/det_10g.onnx", b"det"),
        ("buffalo_l/w600k_r50.onnx", b"rec"),
    ])
    actual_stat = Path.stat

    def report_smaller_preflight_size(path: Path, *args, **kwargs):
        result = actual_stat(path, *args, **kwargs)
        if path == archive:
            return SimpleNamespace(st_size=result.st_size - 1)
        return result

    monkeypatch.setattr(Path, "stat", report_smaller_preflight_size)

    with pytest.raises(ValueError, match="grew beyond the disk-space preflight limit"):
        first_run.prepare_face(root, source_zip=archive, license_accepted=True)

    assert not (target_root / ".buffalo-l-staging").exists()
    assert not (target_root / ".buffalo-l-publish").exists()
    assert not (target_root / ".setup-state/face.json").exists()


@pytest.mark.parametrize("new_url", [
    "http://release-assets.githubusercontent.com/buffalo_l.zip",
    "https://evil.example/buffalo_l.zip",
])
def test_face_download_redirect_refuses_non_https_or_unpinned_hosts(new_url: str) -> None:
    request = first_run.urllib.request.Request(first_run.FACE_URL)
    handler = first_run._FaceRedirectHandler()

    with pytest.raises(ValueError, match="unexpected host"):
        handler.redirect_request(request, None, 302, "Found", {}, new_url)


def test_face_download_redirect_allows_pinned_https_asset_host() -> None:
    request = first_run.urllib.request.Request(first_run.FACE_URL)
    handler = first_run._FaceRedirectHandler()

    redirected = handler.redirect_request(
        request,
        None,
        302,
        "Found",
        {},
        "https://release-assets.githubusercontent.com/buffalo_l.zip",
    )

    assert redirected is not None
    assert redirected.full_url == "https://release-assets.githubusercontent.com/buffalo_l.zip"


def test_face_failed_pin_keeps_existing_files_and_retry_cleans_stale_staging(
    tmp_path: Path, monkeypatch
) -> None:
    from photo_search.face import weights as face_weights
    from photo_search.face.weights import FaceWeights, PinnedFile

    root = tmp_path / "isolated-data"
    target_root = root / "models"
    expected = {
        "face/buffalo_l/det_10g.onnx": b"det synthetic",
        "face/buffalo_l/w600k_r50.onnx": b"rec synthetic",
    }
    entries = tuple(
        PinnedFile(role, relative, len(content), "synthetic-pin", target_root / relative)
        for role, relative, content in (
            ("detection", "face/buffalo_l/det_10g.onnx", expected["face/buffalo_l/det_10g.onnx"]),
            ("recognition", "face/buffalo_l/w600k_r50.onnx", expected["face/buffalo_l/w600k_r50.onnx"]),
        )
    )
    pinned = FaceWeights(root=target_root, files=entries)
    monkeypatch.setattr(face_weights, "resolve", lambda _root: pinned)
    monkeypatch.setattr(
        face_weights,
        "resolve_and_verify",
        lambda _root: (_ for _ in ()).throw(ValueError("models missing")),
    )
    verify_calls = 0

    def verify(candidate):
        nonlocal verify_calls
        verify_calls += 1
        if verify_calls == 1:
            raise ValueError("synthetic pinned hash mismatch")
        for entry in candidate.files:
            if entry.absolute_path.read_bytes() != expected[entry.relative_path]:
                raise ValueError("synthetic pin mismatch")
        return candidate

    monkeypatch.setattr(face_weights, "verify", verify)
    archive = _zip(tmp_path / "face.zip", [
        ("buffalo_l/det_10g.onnx", expected["face/buffalo_l/det_10g.onnx"]),
        ("buffalo_l/w600k_r50.onnx", expected["face/buffalo_l/w600k_r50.onnx"]),
    ])

    with pytest.raises(ValueError, match="pinned hash mismatch"):
        first_run.prepare_face(root, source_zip=archive, license_accepted=True)
    assert not (target_root / "face/buffalo_l").exists()
    assert not (target_root / ".setup-state/face.json").exists()
    assert not (target_root / ".buffalo-l-staging").exists()
    assert not (target_root / ".buffalo-l-publish").exists()

    # Model a process that stopped while staging. The next explicit retry discards
    # stale temporary files and publishes only after the pin check succeeds.
    stale_stage = target_root / ".buffalo-l-staging"
    stale_publish = target_root / ".buffalo-l-publish"
    stale_stage.mkdir(parents=True)
    (stale_stage / "partial").write_bytes(b"interrupted")
    stale_publish.mkdir()
    (stale_publish / "partial").write_bytes(b"interrupted")
    assert first_run.prepare_face(root, source_zip=archive, license_accepted=True)["status"] == "ok"
    assert (target_root / "face/buffalo_l/det_10g.onnx").read_bytes() == expected["face/buffalo_l/det_10g.onnx"]
    assert (target_root / "face/buffalo_l/w600k_r50.onnx").read_bytes() == expected["face/buffalo_l/w600k_r50.onnx"]
    assert (target_root / ".setup-state/face.json").is_file()
    assert not stale_stage.exists()
    assert not stale_publish.exists()


def test_face_refuses_to_replace_existing_unverified_files(tmp_path: Path, monkeypatch) -> None:
    from photo_search.face import weights as face_weights
    from photo_search.face.weights import FaceWeights, PinnedFile

    root = tmp_path / "isolated-data"
    target_root = root / "models"
    old_files = {
        "det_10g.onnx": b"existing detection bytes",
        "w600k_r50.onnx": b"existing recognition bytes",
    }
    final = target_root / "face/buffalo_l"
    final.mkdir(parents=True)
    for name, content in old_files.items():
        (final / name).write_bytes(content)
    entries = (
        PinnedFile("detection", "face/buffalo_l/det_10g.onnx", 3, "pin", final / "det_10g.onnx"),
        PinnedFile("recognition", "face/buffalo_l/w600k_r50.onnx", 3, "pin", final / "w600k_r50.onnx"),
    )
    pinned = FaceWeights(root=target_root, files=entries)
    monkeypatch.setattr(face_weights, "resolve", lambda _root: pinned)
    monkeypatch.setattr(
        face_weights,
        "resolve_and_verify",
        lambda _root: (_ for _ in ()).throw(ValueError("existing files are unverified")),
    )
    monkeypatch.setattr(face_weights, "verify", lambda candidate: candidate)
    archive = _zip(tmp_path / "face.zip", [
        ("buffalo_l/det_10g.onnx", b"new"),
        ("buffalo_l/w600k_r50.onnx", b"new"),
    ])

    with pytest.raises(ValueError, match="unverified Face model directory"):
        first_run.prepare_face(root, source_zip=archive, license_accepted=True)

    assert {name: (final / name).read_bytes() for name in old_files} == old_files
    assert not (target_root / ".setup-state/face.json").exists()


def test_ocr_prepare_copies_and_verifies_the_locked_package_resources(tmp_path: Path) -> None:
    import rapidocr_onnxruntime

    root = tmp_path / "isolated-data"
    result = first_run.prepare_ocr(root, package_root=Path(rapidocr_onnxruntime.__file__).parent)
    assert result["status"] == "ok"
    assert first_run._cheap_component(root, "ocr")["state"] == "weights_validated_at_install"
    assert first_run.prepare_ocr(root, package_root=Path(rapidocr_onnxruntime.__file__).parent)["status"] == "already_ready"


def test_qwen_failure_does_not_block_ocr_or_optional_face(monkeypatch, tmp_path: Path, capsys) -> None:
    missing = {name: {"state": "missing"} for name in ("qwen", "ocr", "face")}
    monkeypatch.setattr(first_run, "status", lambda _root: {"components": missing})
    monkeypatch.setattr(first_run, "prepare_ocr", lambda _root: {"status": "ok"})
    monkeypatch.setattr(
        "photo_search.models.embedding.download.download_qwen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("offline")),
    )
    answers = iter(("y", "n"))
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    result = first_run_cli.run_models_command(
        Namespace(models_command="prepare", component="all", data_root=tmp_path)
    )
    output = capsys.readouterr().out
    assert result == 1
    assert "已准备并校验" in output
    assert "prepare-local --provider qwen3-vl-embedding-2b-exif-v2" in output
    assert '"face": "skipped_license_not_accepted"' in output


def test_qwen_status_requires_success_marker_and_matching_stat_signature(monkeypatch, tmp_path: Path) -> None:
    import json

    from photo_search.models.embedding import registry

    fingerprint = "fixed-qwen-manifest"
    files = [{"path": "model.safetensors", "bytes": 12, "mtime_ns": 34}]
    monkeypatch.setattr(first_run, "_qwen_snapshot", lambda _root: (fingerprint, files, True))
    monkeypatch.setattr(
        registry,
        "registry_for",
        lambda _root: SimpleNamespace(find=lambda _provider: SimpleNamespace(installed=True)),
    )
    marker = tmp_path / "models/.setup-state/qwen.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"fingerprint": fingerprint, "files": files}), encoding="utf-8")
    state = first_run.status(tmp_path)["components"]["qwen"]["state"]
    assert state == "weights_validated_at_install"
    marker.write_text(json.dumps({"fingerprint": fingerprint, "files": []}), encoding="utf-8")
    state = first_run.status(tmp_path)["components"]["qwen"]["state"]
    assert state == "present_unverified"


def test_qwen_status_on_empty_data_root_reads_without_installing(tmp_path: Path) -> None:
    result = first_run.status(tmp_path)
    assert result["components"]["qwen"]["state"] == "missing"
    assert not (tmp_path / "models").exists()


def test_setup_prompt_can_retry_only_one_component(monkeypatch, tmp_path: Path, capsys) -> None:
    missing = {name: {"state": "missing"} for name in ("qwen", "ocr", "face")}
    prepared: list[str] = []
    monkeypatch.setattr(first_run, "status", lambda _root: {"components": missing})
    monkeypatch.setattr(first_run, "prepare_ocr", lambda _root: prepared.append("ocr") or {"status": "ok"})
    monkeypatch.setattr("builtins.input", lambda _prompt: "3")
    result = first_run_cli.run_models_command(
        Namespace(models_command="prepare", component=None, data_root=tmp_path)
    )
    capsys.readouterr()
    assert result == 0
    assert prepared == ["ocr"]
