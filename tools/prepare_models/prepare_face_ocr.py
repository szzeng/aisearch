"""P0-04: pin RapidOCR PP-OCRv4 mobile weights and InsightFace buffalo_l weights.

The script copies the ONNX models bundled in the pinned RapidOCR wheel into the
user data directory, downloads the official InsightFace `model-zoo` archive once
and extracts the detection and recognition weights, then writes a
machine-readable manifest with sources, sizes, SHA-256 digests, measured ONNX
input/output metadata and license statements.

No weight file is written into the repository.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import platform
import shutil
import sys
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import onnxruntime
import rapidocr_onnxruntime
import yaml
from face_ocr_common import (
    FACE_MODEL_FILES,
    FACE_PACK_LEGACY_URL,
    FACE_PACK_NAME,
    FACE_PACK_URL,
    RAPIDOCR_CONFIG_FILE,
    RAPIDOCR_MODEL_FILES,
    RAPIDOCR_PACKAGE,
    artifact_record,
    default_model_root,
    download_cache_dir,
    face_model_dir,
    ocr_model_dir,
    write_json,
)

MANIFEST_PATH = Path("tools/prepare_models/manifests/face-ocr.json")
RAPIDOCR_SOURCE = "https://pypi.org/project/rapidocr-onnxruntime/"
INSIGHTFACE_SOURCE = "https://github.com/deepinsight/insightface/tree/master/model_zoo"
MODEL_ZOO_LICENSE_STATEMENT = (
    "official model_zoo README: 'ALL models are available for non-commercial "
    "research purposes only.' (quoted 2026-09-11)"
)


def describe_onnx(path: Path) -> dict[str, list[dict[str, object]]]:
    """Return the measured graph inputs/outputs of one ONNX file."""
    session = onnxruntime.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    return {
        "inputs": [
            {"name": item.name, "shape": item.shape, "type": item.type}
            for item in session.get_inputs()
        ],
        "outputs": [
            {"name": item.name, "shape": item.shape, "type": item.type}
            for item in session.get_outputs()
        ],
    }


def download(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    with urllib.request.urlopen(url, timeout=180) as response, partial.open("wb") as stream:
        shutil.copyfileobj(response, stream, length=1024 * 1024)
    partial.replace(target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-root", type=Path, default=default_model_root())
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument(
        "--face-pack",
        type=Path,
        default=None,
        help="reuse an already downloaded buffalo_l.zip instead of downloading it again",
    )
    parser.add_argument("--force-download", action="store_true")
    args = parser.parse_args()

    root = args.model_root.resolve()
    ocr_dir = ocr_model_dir(root)
    face_dir = face_model_dir(root)
    steps: list[str] = []
    try:
        ocr_dir.mkdir(parents=True, exist_ok=True)
        package_dir = Path(rapidocr_onnxruntime.__file__).resolve().parent
        for name in RAPIDOCR_MODEL_FILES:
            shutil.copyfile(package_dir / "models" / name, ocr_dir / name)
        shutil.copyfile(package_dir / RAPIDOCR_CONFIG_FILE, ocr_dir / RAPIDOCR_CONFIG_FILE)
        steps.append("rapidocr_copy")

        pack_path = args.face_pack or download_cache_dir(root) / f"{FACE_PACK_NAME}.zip"
        if not pack_path.is_file() or args.force_download:
            download(FACE_PACK_URL, pack_path)
        with zipfile.ZipFile(pack_path) as archive:
            members = {Path(name).name: name for name in archive.namelist()}
            for filename in FACE_MODEL_FILES.values():
                if filename not in members:
                    raise FileNotFoundError(f"{filename} missing from {pack_path.name}")
                face_dir.mkdir(parents=True, exist_ok=True)
                with archive.open(members[filename]) as source:
                    with (face_dir / filename).open("wb") as target:
                        shutil.copyfileobj(source, target)
        steps.append("face_extract")

        with (ocr_dir / RAPIDOCR_CONFIG_FILE).open(encoding="utf-8") as stream:
            ocr_config = yaml.safe_load(stream)
        ocr_artifacts = {
            "detection": artifact_record(ocr_dir / RAPIDOCR_MODEL_FILES[0], root, RAPIDOCR_SOURCE),
            "classification": artifact_record(
                ocr_dir / RAPIDOCR_MODEL_FILES[1], root, RAPIDOCR_SOURCE
            ),
            "recognition": artifact_record(ocr_dir / RAPIDOCR_MODEL_FILES[2], root, RAPIDOCR_SOURCE),
            "config": artifact_record(
                ocr_dir / RAPIDOCR_CONFIG_FILE, root, RAPIDOCR_SOURCE
            ),
        }
        face_artifacts = {
            task: artifact_record(face_dir / filename, root, f"{FACE_PACK_URL}")
            for task, filename in FACE_MODEL_FILES.items()
        }
        recognition_output = describe_onnx(face_dir / FACE_MODEL_FILES["recognition"])["outputs"][0]
        manifest = {
            "schema_version": 1,
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "generator": "tools/prepare_models/prepare_face_ocr.py",
            "artifact_root_note": (
                "All artifact paths are relative to the user-selected model root "
                "(PhotoSearchData/models)."
            ),
            "resolved_model_root": str(root),
            "runtime": {
                "backend": "onnxruntime",
                "device": "CPU",
                "precision": "FP32",
                "python": platform.python_version(),
                "platform": platform.platform(),
                "versions": {
                    name: importlib.metadata.version(name)
                    for name in (
                        "onnxruntime",
                        "Pillow",
                        "insightface",
                        "numpy",
                        "opencv-python",
                        RAPIDOCR_PACKAGE,
                    )
                },
            },
            "ocr": {
                "engine": RAPIDOCR_PACKAGE,
                "package_version": importlib.metadata.version(RAPIDOCR_PACKAGE),
                "source_url": RAPIDOCR_SOURCE,
                "artifacts": ocr_artifacts,
                "onnx_io": {
                    "detection": describe_onnx(ocr_dir / RAPIDOCR_MODEL_FILES[0]),
                    "classification": describe_onnx(ocr_dir / RAPIDOCR_MODEL_FILES[1]),
                    "recognition": describe_onnx(ocr_dir / RAPIDOCR_MODEL_FILES[2]),
                },
                "preprocessing": {
                    "detection": {
                        key: ocr_config["Det"][key]
                        for key in (
                            "limit_side_len",
                            "limit_type",
                            "mean",
                            "std",
                            "thresh",
                            "box_thresh",
                            "unclip_ratio",
                            "score_mode",
                            "use_dilation",
                        )
                    },
                    "classification": {"image_shape": ocr_config["Cls"]["cls_image_shape"]},
                    "recognition": {"image_shape": ocr_config["Rec"]["rec_img_shape"]},
                    "channel_order": "BGR",
                    "dtype": "float32",
                },
                "output_contract": {
                    "result_fields": ["box", "text", "score"],
                    "normalization": "none; raw recognized text plus polygon and confidence",
                },
                "license": {
                    "package": {
                        "spdx": "Apache-2.0",
                        "basis": "PyPI metadata info.license for rapidocr-onnxruntime (2026-09-11)",
                        "source_url": "https://github.com/RapidAI/RapidOCR/blob/main/LICENSE",
                    },
                    "weights": {
                        "spdx": "Apache-2.0",
                        "basis": (
                            "PP-OCRv4 mobile models shipped inside the official RapidOCR wheel "
                            "and published by PaddleOCR; not re-verified file by file"
                        ),
                        "source_url": "https://github.com/PaddlePaddle/PaddleOCR",
                        "verified": False,
                    },
                },
                "notes": [
                    "Weights come from the pinned wheel, so the package version pins them; the "
                    "copied files and their hashes are recorded here.",
                    "RapidOCR 1.4.x is the ONNX-only line; the successor package is 'rapidocr' "
                    "(not evaluated in this task).",
                ],
            },
            "face": {
                "package": "insightface",
                "package_version": importlib.metadata.version("insightface"),
                "pack": FACE_PACK_NAME,
                "source_url": INSIGHTFACE_SOURCE,
                "pack_release": {
                    "url": FACE_PACK_URL,
                    "legacy_url": FACE_PACK_LEGACY_URL,
                    "archive_bytes": pack_path.stat().st_size,
                    "archive_sha256": artifact_record(pack_path, pack_path.parent, FACE_PACK_URL)[
                        "sha256"
                    ],
                },
                "artifacts": face_artifacts,
                "onnx_io": {
                    task: describe_onnx(face_dir / filename)
                    for task, filename in FACE_MODEL_FILES.items()
                },
                "embedding": {
                    "dimension": recognition_output["shape"][-1],
                    "source": "onnx graph output shape of the recognition model",
                    "normalization": "L2",
                    "metric": "cosine",
                },
                "preprocessing": {
                    "detection": {
                        "input_size": [640, 640],
                        "channel_order": "RGB",
                        "scale": 0.0078125,
                        "mean": [127.5, 127.5, 127.5],
                        "note": "reference values from the InsightFace SCRFD implementation",
                    },
                    "recognition": {
                        "input_size": [112, 112],
                        "channel_order": "RGB",
                        "scale": 0.00784313725490196,
                        "mean": [127.5, 127.5, 127.5],
                        "alignment": "ArcFace 5-point similarity transform template",
                        "note": "reference values from the InsightFace ArcFace ONNX implementation",
                    },
                    "dtype": "float32",
                },
                "license": {
                    "weights": {
                        "spdx": None,
                        "basis": MODEL_ZOO_LICENSE_STATEMENT,
                        "source_url": "https://github.com/deepinsight/insightface/tree/master/model_zoo",
                        "allow_redistribution": False,
                        "verified": True,
                    },
                    "package": {
                        "spdx": None,
                        "basis": (
                            "GitHub API repo metadata reported no recognized license file "
                            "(2026-09-11); package license metadata is empty"
                        ),
                        "source_url": "https://github.com/deepinsight/insightface",
                        "verified": False,
                    },
                },
                "notes": [
                    "Only the detection and recognition weights are extracted; landmark, "
                    "gender/age and 3D models from the same pack are not used.",
                    "Weights are for non-commercial research use; distribution terms must be "
                    "re-checked before shipping the model pack to users.",
                ],
            },
        }
        write_json(args.manifest.resolve(), manifest)
        print(
            {
                "status": "pass",
                "completed_steps": [*steps, "manifest"],
                "model_root": str(root),
                "manifest": str(args.manifest.resolve()),
                "face_pack_bytes": manifest["face"]["pack_release"]["archive_bytes"],
                "ocr_artifacts": {k: v["bytes"] for k, v in ocr_artifacts.items()},
                "face_artifacts": {k: v["bytes"] for k, v in face_artifacts.items()},
            }
        )
        return 0
    except Exception as error:
        print(
            {
                "status": "fail",
                "stage": steps[-1] if steps else "setup",
                "exception_type": type(error).__name__,
                "message": str(error),
                "completed_steps": steps,
            },
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
