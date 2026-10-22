"""Verify research/production Qwen512 compatibility and export an optional head package.

Both commands are explicit, local-only and refuse existing output directories. This
does not activate a provider, modify an index, or install a second visual model.
The compatibility run is a bounded numerical check, not a photo-format guarantee.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import time
from pathlib import Path

import numpy as np

from photo_search.models.embedding.base import identity_of, read_manifest
from photo_search.models.embedding.registry import EmbeddingRegistry
from tools.evaluation.album_linear_probe import ALIASES, CLASS_IDS, Reader, read_model
from tools.evaluation.wemm_pilot_core import deterministic_rank, normalize_prefix
from tools.evaluation.wemm_pilot_runtime import (
    ModelRuntime,
    configure_offline,
    decode_image,
    decoder_identity,
    package_versions,
    sha256,
    source_identity,
    verify_model,
)

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "tools/prepare_models/manifests/qwen3-vl-embedding-2b-exif-v2.json"
MAX_ERROR = 2e-6
HEAD_DIRECTORY = "qwen512-category-v1"


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            + "\n").encode("utf-8")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def local_path(path: Path, *, exists: bool = True) -> Path:
    path = Path(path).absolute()
    if not path.is_relative_to(ROOT) or not path.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("paths must stay in the current repository workspace")
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise ValueError("linked input/output paths are refused")
    if exists and not path.exists():
        raise ValueError("required local path does not exist")
    return path


def fresh_directory(path: Path) -> Path:
    path = local_path(path, exists=False)
    path.mkdir(parents=True, exist_ok=False)
    return path


def source_head(path: Path) -> tuple[dict, np.ndarray, np.ndarray, dict]:
    path = local_path(path)
    for name in ("model.json", "weights.npz"):
        local_path(path / name)
    reader = Reader()
    document, weights, biases = read_model(reader, path)
    if (weights.shape != (8, 512) or biases.shape != (8,)
            or not np.isfinite(weights).all() or not np.isfinite(biases).all()
            or not np.isfinite(np.linalg.norm(weights, axis=1)).all()
            or np.any(np.linalg.norm(weights, axis=1) <= 0)):
        raise ValueError("only finite, nonzero eight-class Qwen512 heads are supported")
    formal = read_manifest(MANIFEST)
    spec = document["space_spec"]
    native = copy.deepcopy(formal)
    native["provider"]["provider_id"] = "qwen-pilot-budget-native2048"
    native["embedding"]["dimension"] = 2048
    if (spec.get("model") != "qwen" or spec.get("dtype") != "fp16"
            or spec.get("dimension") != 512 or spec.get("native_dimension") != 2048
            or spec.get("normalization") != "unit-prefix-v1"
            or spec.get("model_lock", {}).get("manifest") != formal
            or spec.get("settings", {}).get("qwen_manifest") != native
            or spec.get("decoder") != decoder_identity()):
        raise ValueError("research head preprocessing/model space differs from formal Qwen EXIF v2")
    # Compare the actual adapter/base decoder and numerical-prefix implementation,
    # not the cosmetic research provider name or merely the vector width.
    current = source_identity()
    critical = ("src/photo_search/models/embedding/qwen3_vl.py",
                "src/photo_search/models/embedding/base.py",
                "tools/evaluation/wemm_pilot_core.py",
                "tools/evaluation/wemm_pilot_runtime.py")
    if any(spec["source_sha256"].get(name) != current[name] for name in critical):
        raise ValueError("research and current encoding source differ; revalidate/retrain explicitly")
    if spec.get("packages") != package_versions():
        raise ValueError("research and current runtime versions differ")
    reader.unchanged()
    provenance = {"research_identity": document["identity"],
                  "research_model_sha256": sha256(path / "model.json"),
                  "research_weights_sha256": sha256(path / "weights.npz"),
                  "space_spec": spec, "training_source_hashes": document["source_hashes"]}
    return document, weights, biases, provenance


def numerical_comparison(left: np.ndarray, right: np.ndarray,
                         weights: np.ndarray, ids: list[int]) -> dict:
    if (left.shape != right.shape or left.shape != (len(ids), 512)
            or not np.isfinite(left).all() or not np.isfinite(right).all()):
        raise ValueError("invalid comparison vectors")
    norms = np.linalg.norm(left, axis=1), np.linalg.norm(right, axis=1)
    if any(np.max(np.abs(v - 1.0)) > MAX_ERROR for v in norms):
        raise ValueError("comparison vectors are not unit normalized")
    error = float(np.max(np.abs(left - right)))
    cosine_error = float(np.max(np.abs(np.sum(left * right, axis=1) - 1.0)))
    rankings = {}
    for class_id, weight in zip(CLASS_IDS, weights, strict=True):
        before = deterministic_rank(left.astype(np.float64) @ weight, ids)
        after = deterministic_rank(right.astype(np.float64) @ weight, ids)
        rankings[class_id] = {"equal": before == after, "production": before, "research": after}
    return {"passed": error <= MAX_ERROR and cosine_error <= MAX_ERROR
            and all(row["equal"] for row in rankings.values()),
            "max_abs_error": error, "max_cosine_error": cosine_error,
            "tolerance": MAX_ERROR, "rankings": rankings}


def synthetic_inputs(directory: Path) -> list[dict]:
    """EXIF 1..8 JPEG plus HEIF EXIF-only/container 6/8, no external fixtures."""
    from PIL import Image

    from photo_search.media.heif import ensure_heif_support

    ensure_heif_support()
    directory.mkdir()
    image = Image.new("RGB", (320, 200))
    for colour, box in [((230, 20, 20), (0, 0, 160, 100)),
                        ((20, 230, 20), (160, 0, 320, 100)),
                        ((20, 20, 230), (0, 100, 160, 200)),
                        ((235, 235, 30), (160, 100, 320, 200))]:
        image.paste(colour, box)
    image.paste((0, 0, 0), (8, 8, 56, 40))
    rows = []
    for orientation in range(1, 9):
        exif = Image.Exif()
        exif[0x0112] = orientation
        path = directory / f"orientation-{orientation}.jpg"
        image.save(path, "JPEG", quality=94, exif=exif)
        rows.append({"path": str(path), "sha256": sha256(path), "kind": "synthetic-jpeg",
                     "orientation": orientation})
    for orientation in (6, 8):
        for container in (False, True):
            exif = Image.Exif()
            exif[0x0112] = orientation
            path = directory / f"orientation-{orientation}-container-{container}.heic"
            image.save(path, "HEIF", exif=exif.tobytes() if container else exif)
            rows.append({"path": str(path), "sha256": sha256(path), "kind": "synthetic-heif",
                         "orientation": orientation, "container_orientation": container})
    image.close()
    return rows


def compare(args: argparse.Namespace) -> dict:
    _, weights, _, provenance = source_head(args.head_dir)
    sample_path = local_path(args.sample)
    sample = json.loads(sample_path.read_bytes())
    if sample.get("schema") != "wemm-pilot-public-sample-v1" or not 8 <= args.limit <= 64:
        raise ValueError("requires explicit public sample and a limit of 8..64")
    rows = sample["assets"][:args.limit]
    if len(rows) != args.limit:
        raise ValueError("public sample shorter than requested fixed limit")
    for row in rows:
        if sha256(local_path(Path(row["path"]))) != row["sha256"]:
            raise ValueError("public sample bytes changed")
    model_dir = local_path(args.model_dir)
    verified = verify_model("qwen", model_dir)
    if verified != provenance["space_spec"]["model_lock"]:
        raise ValueError("installed model files differ from research files")
    directory = fresh_directory(args.out_dir)
    configure_offline(directory / "runtime-cache")
    formal = read_manifest(MANIFEST)
    identity = identity_of(formal)
    native = provenance["space_spec"]["settings"]["qwen_manifest"]
    native_path = directory / "research-native-manifest.json"
    native_path.write_bytes(json_bytes(native))
    rows = [dict(row, kind="public-jpeg") for row in rows] + synthetic_inputs(directory / "synthetic")
    result = {"schema": "category-head-compatibility-v1", "status": "RUNNING",
              "processor_id": str(identity.processor_id), "space_id": str(identity.space_id),
              "dimension": 512, "source_manifest_sha256": sha256(MANIFEST),
              "source": provenance, "model_verification": verified,
              "sample_sha256": sha256(sample_path), "inputs": rows,
              "source_code_sha256": {"export_category_heads.py": sha256(Path(__file__)),
                                      **source_identity()},
              "limitations": ["Small numerical compatibility check, not all-format or all-photo certification.",
                              "No GTX1660 or 300k end-to-end performance certification.",
                              "No retraining, threshold calibration, provider/index/default change."]}
    started = time.perf_counter()
    try:
        from photo_search.models.embedding.qwen3_vl import Qwen3VLEmbeddingAdapter

        production = Qwen3VLEmbeddingAdapter(model_dir, MANIFEST)
        try:
            production.load()
            result["production_runtime"] = production.health()
            vectors = np.asarray(production.encode_images([r["path"] for r in rows]), dtype=np.float32)
            direct = np.asarray(production.encode_image(rows[0]["path"]), dtype=np.float32)
            if np.max(np.abs(vectors[0] - direct)) > MAX_ERROR:
                raise ValueError("encode_images and encode_image differ")
            text = np.asarray(production.encode_queries(list(ALIASES)), dtype=np.float32)
            decoded = []
            for row in rows:
                with production._read_local_image(row["path"]) as image:
                    decoded.append({"size": list(image.size), "rgb_sha256": digest(image.tobytes())})
            result["production_runtime"] = production.health()
        finally:
            production.close()
        runtime = ModelRuntime("qwen", model_dir, "cuda:0", "fp16", native_path)
        try:
            result["research_runtime"] = runtime.load()
            research, decoder = [], []
            for row in rows:
                image, record = decode_image(Path(row["path"]), row["sha256"])
                try:
                    research.append(normalize_prefix(runtime.encode(image=image), 512)[0])
                    decoder.append(record)
                finally:
                    image.close()
            research_text = np.vstack([normalize_prefix(runtime.encode(text=t), 512) for t in ALIASES])
        finally:
            runtime.close()
        for before, after in zip(decoded, decoder, strict=True):
            if before != {"size": [after["width"], after["height"]], "rgb_sha256": after["rgb_sha256"]}:
                raise ValueError("production and research decoded RGB bytes differ")
        result["decoded_rgb_equal"] = True
        result["decode_records"] = decoder
        result["image_comparison"] = numerical_comparison(vectors, np.asarray(research), weights,
                                                         list(range(len(rows))))
        result["text_max_abs_error"] = float(np.max(np.abs(text - research_text)))
        result["vectors_sha256"] = {}
        for name, value in (("production-images.npy", vectors), ("research-images.npy", research),
                            ("production-queries.npy", text), ("research-queries.npy", research_text)):
            np.save(directory / name, np.asarray(value, dtype=np.float32), allow_pickle=False)
            result["vectors_sha256"][name] = sha256(directory / name)
        if (not result["image_comparison"]["passed"] or result["text_max_abs_error"] > MAX_ERROR):
            raise ValueError("production/research numerical compatibility failed")
        # No input may change while the GPU work is running.
        source_head(args.head_dir)
        if any(sha256(Path(row["path"])) != row["sha256"] for row in rows):
            raise ValueError("image bytes changed during compatibility check")
        result["status"] = "PASS"
    except Exception as error:
        result.update(status="FAIL", error=str(error), error_type=type(error).__name__)
        raise
    finally:
        result["elapsed_seconds"] = time.perf_counter() - started
        (directory / "compatibility.json").write_bytes(json_bytes(result))
    return result


def export(args: argparse.Namespace) -> dict:
    _, weights, _, provenance = source_head(args.head_dir)
    evidence_path = local_path(args.evidence)
    if evidence_path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("compatibility evidence exceeds bounded size")
    evidence_bytes = evidence_path.read_bytes()
    evidence = json.loads(evidence_bytes)
    identity = identity_of(read_manifest(MANIFEST))
    identity_fields = {"processor_id": str(identity.processor_id), "space_id": str(identity.space_id),
                       "dimension": 512, "source_manifest_sha256": sha256(MANIFEST)}
    if (evidence.get("schema") != "category-head-compatibility-v1" or evidence.get("status") != "PASS"
            or evidence.get("source") != provenance or not evidence.get("decoded_rgb_equal")
            or any(evidence.get(k) != v for k, v in identity_fields.items())
            or evidence.get("source_code_sha256", {}).get("export_category_heads.py") != sha256(Path(__file__))):
        raise ValueError("compatibility evidence is failed, stale or belongs to another model")
    comparison = evidence.get("image_comparison", {})
    errors = [comparison.get("max_abs_error"), comparison.get("max_cosine_error"),
              evidence.get("text_max_abs_error")]
    if (comparison.get("passed") is not True or comparison.get("tolerance") != MAX_ERROR
            or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= MAX_ERROR for v in errors)
            or set(comparison.get("rankings", {})) != set(CLASS_IDS)
            or not all(r.get("equal") is True and r.get("production") == r.get("research")
                       for r in comparison["rankings"].values())):
        raise ValueError("numerical/ranking compatibility evidence did not pass")
    for name, expected in evidence.get("vectors_sha256", {}).items():
        if Path(name).name != name or sha256(local_path(evidence_path.parent / name)) != expected:
            raise ValueError("compatibility vector artifact changed")
    if set(evidence.get("vectors_sha256", {})) != {
            "production-images.npy", "research-images.npy", "production-queries.npy", "research-queries.npy"}:
        raise ValueError("compatibility vector artifacts are incomplete")
    arrays = {}
    for name in evidence["vectors_sha256"]:
        path = evidence_path.parent / name
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("compatibility vectors exceed bounded size")
        arrays[name] = np.load(path, allow_pickle=False)
    count = len(evidence.get("inputs", []))
    if not 20 <= count <= 76:
        raise ValueError("compatibility image count differs from bounded protocol")
    actual = numerical_comparison(arrays["production-images.npy"], arrays["research-images.npy"],
                                  weights, list(range(count)))
    prod_text, research_text = arrays["production-queries.npy"], arrays["research-queries.npy"]
    if (actual != comparison or prod_text.shape != (8, 512) or research_text.shape != (8, 512)
            or not np.isfinite(prod_text).all() or not np.isfinite(research_text).all()
            or float(np.max(np.abs(prod_text - research_text))) != evidence["text_max_abs_error"]):
        raise ValueError("raw compatibility vectors do not reproduce reported result")
    root = local_path(args.data_root, exists=False)
    # prepare-local annotates installed_from and rewrites JSON formatting. This
    # changes the bytes, not the encoded space. Bind the installed bytes only after
    # explicitly verifying this narrow bridge; never ignore arbitrary local edits.
    selected = getattr(args, "manifest", None)
    if selected is None:
        selected = EmbeddingRegistry.discover(root).for_processor(str(identity.processor_id)).manifest_path
    selected = local_path(selected)
    deployed_manifest = read_manifest(selected)
    encoding_manifest = {k: v for k, v in deployed_manifest.items() if k != "installed_from"}
    if encoding_manifest != read_manifest(MANIFEST):
        raise ValueError("installed manifest has changed encoding semantics")
    deployment_evidence = json_bytes({
        "schema": "category-head-export-evidence-v1", "compatibility": evidence,
        "compatibility_sha256": digest(evidence_bytes),
        "deployed_manifest": deployed_manifest, "deployed_manifest_sha256": sha256(selected),
        "bridge": "identical formal manifest except optional installed_from and JSON byte formatting",
        "export_tool_sha256": sha256(Path(__file__)),
    })
    weight_bytes = json_bytes({"schema": "photo-search-category-weights-v1",
                               "weights": dict(zip(CLASS_IDS, weights.tolist(), strict=True))})
    manifest = {"schema": "photo-search-category-head-v1", "version": "album-qwen512-v1",
                **identity_fields, "source_manifest_sha256": sha256(selected),
                "weights_sha256": digest(weight_bytes),
                "export_evidence_sha256": digest(deployment_evidence), "classes": CLASS_IDS, "aliases": ALIASES}
    manifest_bytes = json_bytes(manifest)
    if len(weight_bytes) + len(manifest_bytes) > 262144:
        raise ValueError("runtime head package exceeds bounded size")
    destination = fresh_directory(root / "models/search_heads" / HEAD_DIRECTORY)
    (destination / "weights.json").write_bytes(weight_bytes)
    (destination / "export-evidence.json").write_bytes(deployment_evidence)
    # Manifest is the completion marker and is written last.
    (destination / "manifest.json").write_bytes(manifest_bytes)
    return {"status": "EXPORTED_NOT_ACTIVATED", "directory": str(destination),
            "runtime_bytes": len(manifest_bytes) + len(weight_bytes), "manifest": manifest}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("compare")
    check.add_argument("--head-dir", type=Path, required=True)
    check.add_argument("--model-dir", type=Path, required=True)
    check.add_argument("--sample", type=Path, required=True)
    check.add_argument("--limit", type=int, default=24)
    check.add_argument("--out-dir", type=Path, required=True)
    install = commands.add_parser("export")
    install.add_argument("--head-dir", type=Path, required=True)
    install.add_argument("--evidence", type=Path, required=True)
    install.add_argument("--data-root", type=Path, required=True)
    install.add_argument("--manifest", type=Path,
                         help="installed formal manifest; default resolves the target data-root registry")
    args = parser.parse_args()
    result = compare(args) if args.command == "compare" else export(args)
    print(json.dumps({k: v for k, v in result.items() if k in ("status", "directory", "runtime_bytes",
                                                            "elapsed_seconds")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
