"""Explicit, data-root scoped preparation for OCR, Face and Qwen first use."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from photo_search.config import resolve_data_root

FACE_URL = "https://github.com/deepinsight/insightface/releases/download/model-zoo/buffalo_l.zip"
FACE_LICENSE_URL = "https://github.com/deepinsight/insightface/tree/master/python-package"
QWEN_PROVIDER_ID = "qwen3-vl-embedding-2b-exif-v2"
FACE_MAX_EXPANDED_BYTES = 512 * 1024 * 1024
FACE_REQUIRED = {"det_10g.onnx", "w600k_r50.onnx"}
FACE_ARCHIVE_MAX_BYTES = 512 * 1024 * 1024
FACE_PUBLISH_RESERVE_BYTES = 128 * 1024 * 1024
OCR_FILES = {
    "detection": "ch_PP-OCRv4_det_infer.onnx",
    "classification": "ch_ppocr_mobile_v2.0_cls_infer.onnx",
    "recognition": "ch_PP-OCRv4_rec_infer.onnx",
    "config": "config.yaml",
}


def _marker_path(root: Path, name: str) -> Path:
    return root / "models" / ".setup-state" / f"{name}.json"


def _cheap_component(data_root: Path, kind: str) -> dict[str, Any]:
    from photo_search.face import weights as face_weights
    from photo_search.ocr import weights as ocr_weights

    resolver = ocr_weights.resolve if kind == "ocr" else face_weights.resolve
    inspector = ocr_weights.inspect if kind == "ocr" else face_weights.inspect
    entries = inspector(resolver(data_root))
    marker = _marker_path(data_root, kind)
    marker_matches = False
    try:
        saved = json.loads(marker.read_text(encoding="utf-8"))
        fingerprint = (
            ocr_weights.resolve(data_root).fingerprint
            if kind == "ocr"
            else face_weights.resolve(data_root).fingerprint
        )
        signature = [
            {"path": item["path"], "bytes": item["actual_bytes"],
             "mtime_ns": (resolver(data_root).root / item["path"]).stat().st_mtime_ns}
            for item in entries if item["present"]
        ]
        marker_matches = saved.get("fingerprint") == fingerprint and saved.get("files") == signature
    except (OSError, ValueError, TypeError):
        pass
    if not any(item["present"] for item in entries):
        state = "missing"
    elif not all(item["size_matches"] for item in entries):
        state = "present_unverified"
    elif marker_matches:
        state = "weights_validated_at_install"
    else:
        state = "present_unverified"
    return {"state": state, "files": entries}


def _qwen_snapshot(data_root: Path) -> tuple[str, list[dict[str, Any]], bool]:
    from photo_search.models.embedding.base import read_manifest, required_artifact_names
    from photo_search.models.embedding.registry import registry_for, shipped_manifest_dir

    entry = registry_for(data_root).find(QWEN_PROVIDER_ID)
    if entry is None:
        return "", [], False
    manifest_path = shipped_manifest_dir() / f"{QWEN_PROVIDER_ID}.json"
    document = read_manifest(manifest_path)
    names = required_artifact_names(document)
    files: list[dict[str, Any]] = []
    present = 0
    for name in names:
        path = entry.model_dir / name
        try:
            info = path.stat()
        except OSError:
            continue
        if path.is_file():
            present += 1
            files.append({"path": name, "bytes": info.st_size, "mtime_ns": info.st_mtime_ns})
    fingerprint = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    return fingerprint, files, bool(entry.installed or present)


def record_qwen_verified(data_root: str | Path) -> None:
    """Record cheap file signatures only after the existing pinned downloader verified Qwen."""
    root = resolve_data_root(data_root)
    fingerprint, files, present = _qwen_snapshot(root)
    from photo_search.models.embedding.registry import registry_for

    entry = registry_for(root).find(QWEN_PROVIDER_ID)
    if not present or entry is None or not entry.installed or not files:
        return
    required = len(files)
    from photo_search.models.embedding.base import read_manifest, required_artifact_names
    from photo_search.models.embedding.registry import shipped_manifest_dir

    pinned = read_manifest(shipped_manifest_dir() / f"{QWEN_PROVIDER_ID}.json")
    if required != len(required_artifact_names(pinned)):
        return
    marker = _marker_path(root, "qwen")
    marker.parent.mkdir(parents=True, exist_ok=True)
    tmp = marker.with_suffix(".tmp")
    tmp.write_text(json.dumps({"fingerprint": fingerprint, "files": files}, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, marker)


def status(data_root: str | Path | None = None) -> dict[str, Any]:
    """Cheap status only: file stat, installed manifests and small marker reads."""
    root = resolve_data_root(data_root)

    qwen_fingerprint, qwen_files, qwen_present = _qwen_snapshot(root)
    qwen_marker_matches = False
    try:
        qwen_marker = json.loads(_marker_path(root, "qwen").read_text(encoding="utf-8"))
        qwen_marker_matches = (
            qwen_marker.get("fingerprint") == qwen_fingerprint
            and qwen_marker.get("files") == qwen_files
            and bool(qwen_files)
        )
    except (OSError, ValueError, TypeError):
        pass
    return {
        "status": "ok",
        "data_root": str(root),
        "components": {
            "qwen": {"state": "weights_validated_at_install" if qwen_marker_matches else "present_unverified" if qwen_present else "missing"},
            "ocr": _cheap_component(root, "ocr"),
            "face": _cheap_component(root, "face"),
        },
    }


def _write_marker(data_root: Path, kind: str, fingerprint: str) -> None:
    marker = _marker_path(data_root, kind)
    marker.parent.mkdir(parents=True, exist_ok=True)
    tmp = marker.with_suffix(".tmp")
    from photo_search.face import weights as face_weights
    from photo_search.ocr import weights as ocr_weights

    resolved = ocr_weights.resolve(data_root) if kind == "ocr" else face_weights.resolve(data_root)
    files = [
        {"path": item.relative_path, "bytes": item.absolute_path.stat().st_size,
         "mtime_ns": item.absolute_path.stat().st_mtime_ns}
        for item in resolved.files
    ]
    tmp.write_text(json.dumps({"fingerprint": fingerprint, "files": files}, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, marker)


def prepare_ocr(data_root: str | Path | None = None, *, package_root: Path | None = None) -> dict[str, Any]:
    """Copy bundled RapidOCR 1.4.4 files through a verified sibling staging directory."""
    from photo_search.ocr import weights

    root = resolve_data_root(data_root)
    target = weights.resolve(root)
    try:
        weights.resolve_and_verify(root)
        _write_marker(root, "ocr", target.fingerprint)
        return {"status": "already_ready", "component": "ocr"}
    except Exception:
        if (target.root / "ocr" / "ppocr-v4-mobile").exists():
            raise ValueError("an unverified OCR model directory already exists; move it aside manually")
    if package_root is None:
        import rapidocr_onnxruntime

        package_root = Path(rapidocr_onnxruntime.__file__).resolve().parent
    source = package_root / "models"
    stage = target.root / ".ppocr-v4-mobile-staging"
    stage.parent.mkdir(parents=True, exist_ok=True)
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    try:
        for entry in target.files:
            filename = OCR_FILES[entry.role]
            candidate = source / filename if entry.role != "config" else package_root / filename
            if not candidate.is_file():
                raise FileNotFoundError(f"bundled OCR asset missing: {filename}")
            shutil.copyfile(candidate, stage / filename)
        # Build the exact data-root-relative layout expected by the canonical verifier.
        relative_dir = Path("ocr") / "ppocr-v4-mobile"
        install_stage = target.root / ".ppocr-v4-mobile-publish"
        if install_stage.exists():
            shutil.rmtree(install_stage)
        (install_stage / relative_dir).mkdir(parents=True)
        for entry in target.files:
            shutil.copyfile(stage / OCR_FILES[entry.role], install_stage / entry.relative_path)
        staged_root = install_stage
        staged_weights = type(target)(root=install_stage, files=tuple(
            type(entry)(entry.role, entry.relative_path, entry.expected_bytes, entry.expected_sha256,
                        install_stage / entry.relative_path)
            for entry in target.files
        ))
        weights.verify(staged_weights)
        final = target.root / "ocr" / "ppocr-v4-mobile"
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged_root / relative_dir, final)
        _write_marker(root, "ocr", target.fingerprint)
        return {"status": "ok", "component": "ocr", "files": len(target.files)}
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        shutil.rmtree(target.root / ".ppocr-v4-mobile-publish", ignore_errors=True)


def prepare_face(
    data_root: str | Path | None = None,
    *,
    source_zip: str | Path | None = None,
    download: bool = False,
    license_accepted: bool = False,
    urlopen: Any = urllib.request.urlopen,
) -> dict[str, Any]:
    """Install the two pinned buffalo_l files from an official ZIP or user-local ZIP."""
    from photo_search.face import weights

    if not license_accepted:
        raise ValueError("Face license acknowledgement is required")
    root = resolve_data_root(data_root)
    target = weights.resolve(root)
    try:
        weights.resolve_and_verify(root)
        _write_marker(root, "face", target.fingerprint)
        return {"status": "already_ready", "component": "face"}
    except Exception:
        pass
    if source_zip is None and not download:
        raise ValueError("provide --source-zip or explicitly allow the official download")
    expected_weight_bytes = sum(item.expected_bytes for item in target.files)
    archive_bytes = (
        Path(source_zip).stat().st_size if source_zip is not None else FACE_ARCHIVE_MAX_BYTES
    )
    if archive_bytes > FACE_ARCHIVE_MAX_BYTES:
        raise ValueError("Face ZIP exceeds the archive-size limit")
    required_free = archive_bytes + (expected_weight_bytes * 2) + FACE_PUBLISH_RESERVE_BYTES
    volume_probe = root
    while not volume_probe.exists() and volume_probe.parent != volume_probe:
        volume_probe = volume_probe.parent
    if int(shutil.disk_usage(volume_probe).free) < required_free:
        raise ValueError(f"not enough free space for Face staging and verified publish ({required_free} bytes required)")
    stage = target.root / ".buffalo-l-staging"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True, exist_ok=True)
    archive_path = stage / "source.zip"
    try:
        if source_zip is not None:
            _copy_face_archive(Path(source_zip), archive_path, expected_bytes=archive_bytes)
        else:
            request = urllib.request.Request(FACE_URL, headers={"User-Agent": "PhotoSearch/0.6"})
            opener = urllib.request.build_opener(_FaceRedirectHandler()) if urlopen is urllib.request.urlopen else None
            with (opener.open(request, timeout=30) if opener else urlopen(request, timeout=30)) as response:
                with archive_path.open("wb") as output:
                    copied = 0
                    while chunk := response.read(1024 * 1024):
                        copied += len(chunk)
                        if copied > FACE_ARCHIVE_MAX_BYTES:
                            raise ValueError("Face ZIP exceeds the archive-size limit")
                        output.write(chunk)
        expected_sizes = {Path(entry.relative_path).name: entry.expected_bytes for entry in target.files}
        _extract_face_zip(archive_path, stage / "unpacked", expected_sizes=expected_sizes)
        unpacked = stage / "unpacked"
        for entry in target.files:
            source = unpacked / Path(entry.relative_path).name
            if not source.is_file():
                raise ValueError(f"Face ZIP is missing {source.name}")
        publish = target.root / ".buffalo-l-publish"
        if publish.exists():
            shutil.rmtree(publish)
        final_relative = Path("face") / "buffalo_l"
        (publish / final_relative).mkdir(parents=True)
        for entry in target.files:
            shutil.copyfile(unpacked / Path(entry.relative_path).name, publish / entry.relative_path)
        staged = type(target)(root=publish, files=tuple(
            type(entry)(entry.role, entry.relative_path, entry.expected_bytes, entry.expected_sha256,
                        publish / entry.relative_path)
            for entry in target.files
        ))
        weights.verify(staged)
        final = target.root / final_relative
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():
            # Invalid existing files are retained for recovery; never replace user data implicitly.
            raise ValueError("an unverified Face model directory already exists; move it aside manually")
        os.replace(publish / final_relative, final)
        _write_marker(root, "face", target.fingerprint)
        return {"status": "ok", "component": "face", "source": "official" if source_zip is None else "local"}
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        shutil.rmtree(target.root / ".buffalo-l-publish", ignore_errors=True)


def _copy_face_archive(source: Path, destination: Path, *, expected_bytes: int) -> None:
    """Copy only the archive length used by the disk-space preflight."""
    copied = 0
    with source.open("rb") as input_stream, destination.open("xb") as output_stream:
        while chunk := input_stream.read(1024 * 1024):
            copied += len(chunk)
            if copied > expected_bytes or copied > FACE_ARCHIVE_MAX_BYTES:
                raise ValueError("Face ZIP grew beyond the disk-space preflight limit")
            output_stream.write(chunk)
    if copied != expected_bytes:
        raise ValueError("Face ZIP changed after the disk-space preflight")


def _extract_face_zip(
    archive: Path,
    destination: Path,
    *,
    expected_sizes: dict[str, int] | None = None,
) -> None:
    total = 0
    seen: set[str] = set()
    seen_paths: set[str] = set()
    member_layout: str | None = None
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = info.filename.replace("\\", "/")
                path = PurePosixPath(name)
                if path.is_absolute() or ".." in path.parts or not path.parts:
                    raise ValueError("Face ZIP contains an unsafe path")
                normalized = "/".join(path.parts).casefold()
                if normalized in seen_paths:
                    raise ValueError("Face ZIP contains duplicate file names")
                seen_paths.add(normalized)
                total += int(info.file_size)
                if total > FACE_MAX_EXPANDED_BYTES:
                    raise ValueError("Face ZIP exceeds the expanded-size limit")
                basename = path.name
                if basename not in FACE_REQUIRED:
                    continue
                if len(path.parts) == 1:
                    current_layout = "flat"
                elif path.parts[-2] == "buffalo_l":
                    current_layout = "buffalo_l_directory"
                else:
                    continue
                if member_layout is not None and member_layout != current_layout:
                    raise ValueError("Face ZIP mixes flat and buffalo_l directory layouts")
                member_layout = current_layout
                key = basename.casefold()
                if key in seen:
                    raise ValueError("Face ZIP contains duplicate pinned files")
                expected_size = expected_sizes.get(basename) if expected_sizes is not None else None
                if expected_sizes is not None and expected_size is None:
                    raise ValueError(f"Face ZIP contains an unpinned model file: {basename}")
                if expected_size is not None and int(info.file_size) != expected_size:
                    raise ValueError(f"Face ZIP pinned file has an unexpected size: {basename}")
                seen.add(key)
                target = destination / basename
                actual = 0
                with zf.open(info) as src, target.open("xb") as dst:
                    while chunk := src.read(1024 * 1024):
                        actual += len(chunk)
                        if actual > FACE_MAX_EXPANDED_BYTES or (
                            expected_size is not None and actual > expected_size
                        ):
                            raise ValueError("Face ZIP exceeds the expanded-size limit")
                        dst.write(chunk)
                if expected_size is not None and actual != expected_size:
                    raise ValueError(f"Face ZIP pinned file has an unexpected size: {basename}")
        if seen != FACE_REQUIRED:
            raise ValueError("Face ZIP is missing one or more pinned buffalo_l ONNX files")
    except (zipfile.BadZipFile, EOFError, OSError) as error:
        raise ValueError("Face ZIP is corrupt or unreadable") from error


class _FaceRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Allow only the fixed GitHub release and its official release-asset host."""

    allowed_hosts = {"github.com", "release-assets.githubusercontent.com"}

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        host = parsed.hostname
        if parsed.scheme != "https" or host is None or host.casefold() not in self.allowed_hosts:
            raise ValueError("official Face download redirected to an unexpected host")
        return super().redirect_request(req, fp, code, msg, headers, newurl)
