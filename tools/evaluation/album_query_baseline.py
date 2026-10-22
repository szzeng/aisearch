"""Encode a frozen broad-category query list in the production Qwen512 space.

Images are reused from separately verified research caches. This command writes
only an isolated query-vector artifact and never changes the application's index.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from photo_search.models.embedding.base import identity_of, read_manifest
from tools.evaluation.prepare_wemm_coco import controlled_output, write_json
from tools.evaluation.wemm_pilot_runtime import (
    configure_offline,
    package_versions,
    sha256,
    verify_model,
)

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "tools/prepare_models/manifests/qwen3-vl-embedding-2b-exif-v2.json"
SOURCES = ("tools/evaluation/album_query_baseline.py", "tools/evaluation/wemm_pilot_runtime.py",
           "src/photo_search/models/embedding/base.py", "src/photo_search/models/embedding/qwen3_vl.py")


def local_file(path: Path, maximum: int) -> Path:
    if not path.resolve().is_relative_to(ROOT.resolve()) or not path.is_file():
        raise ValueError("input must be a workspace file")
    if path.stat().st_size > maximum:
        raise ValueError("input exceeds bounded size")
    return path


def read_queries(path: Path) -> list[dict]:
    document = json.loads(local_file(path, 1024 * 1024).read_bytes())
    rows = document.get("queries", [])
    if document.get("schema") != "broad-album-queries-v1" or not 1 <= len(rows) <= 512:
        raise ValueError("expected a frozen list of 1..512 queries")
    seen = set()
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("query_id"), str)
                or not row["query_id"] or row["query_id"] in seen
                or not isinstance(row.get("text"), str) or not row["text"].strip()
                or len(row["text"]) > 256):
            raise ValueError("invalid or duplicate query identity")
        seen.add(row["query_id"])
    return rows


def validate_vectors(values: np.ndarray, count: int) -> None:
    if (values.shape != (count, 512) or not np.isfinite(values).all()
            or np.max(np.abs(np.linalg.norm(values, axis=1) - 1)) > 2e-6):
        raise ValueError("formal query output is not finite unit Qwen512")


def run(queries_path: Path, model_dir: Path, output: Path) -> dict:
    query_hash = sha256(local_file(queries_path, 1024 * 1024))
    queries = read_queries(queries_path)
    if sha256(queries_path) != query_hash:
        raise ValueError("query file changed while reading")
    if not model_dir.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("model must stay in the workspace data directory")
    lock = verify_model("qwen", model_dir)
    hashes = {name: sha256(ROOT / name) for name in SOURCES}
    manifest_hash = sha256(MANIFEST)
    identity = identity_of(read_manifest(MANIFEST))
    output = controlled_output(output)
    output.mkdir(parents=True, exist_ok=False)
    configure_offline(output / "runtime-cache")
    result = {"schema": "album-query-baseline-v1", "status": "RUNNING", "queries": queries,
              "queries_sha256": query_hash, "dimension": 512,
              "processor_id": str(identity.processor_id), "space_id": str(identity.space_id),
              "manifest_sha256": manifest_hash, "model_lock": lock, "packages": package_versions(),
              "source_sha256": hashes, "query_ms": []}
    start = time.perf_counter()
    adapter = None
    try:
        from photo_search.models.embedding.qwen3_vl import Qwen3VLEmbeddingAdapter

        adapter = Qwen3VLEmbeddingAdapter(model_dir, MANIFEST)
        adapter.load()
        result["load_seconds"] = time.perf_counter() - start
        vectors = []
        for row in queries:
            before = time.perf_counter()
            vectors.append(adapter.encode_text(row["text"]))
            result["query_ms"].append((time.perf_counter() - before) * 1000)
            if len(vectors) % 50 == 0:
                print(json.dumps({"encoded": len(vectors), "total": len(queries)}), flush=True)
        values = np.asarray(vectors, dtype=np.float32)
        validate_vectors(values, len(queries))
        if (sha256(queries_path) != query_hash or sha256(MANIFEST) != manifest_hash
                or any(sha256(ROOT / name) != value for name, value in hashes.items())):
            raise ValueError("baseline inputs or code changed during encoding")
        if verify_model("qwen", model_dir) != lock:
            raise ValueError("model files changed during encoding")
        np.save(output / "queries.npy", values, allow_pickle=False)
        result.update(status="PASS", vectors_sha256=sha256(output / "queries.npy"),
                      runtime=adapter.health())
    except Exception as error:
        result.update(status="FAIL", error=str(error), error_type=type(error).__name__)
        raise
    finally:
        if adapter is not None:
            adapter.close()
        result["elapsed_seconds"] = time.perf_counter() - start
        write_json(output / "result.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.queries, args.model_dir, args.out_dir)
    print(json.dumps({"status": result["status"], "queries": len(result["queries"]),
                      "elapsed_seconds": result["elapsed_seconds"]}))


if __name__ == "__main__":
    main()
