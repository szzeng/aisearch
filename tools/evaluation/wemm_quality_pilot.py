"""Bounded, offline Qwen2048/Qwen512/WeMM512 experiment (not a provider).

Run with ``python -m tools.evaluation.wemm_quality_pilot --help``. All generated
files, including private previews, must stay inside this checkout's ignored data
directories. No command installs packages, downloads weights or changes a catalog.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import time
import uuid
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

import numpy as np

from tools.evaluation.wemm_pilot_core import (
    annotated_metrics,
    cache_identity,
    compare_rankings,
    deterministic_rank,
    normalize_prefix,
    precision_at_k,
)
from tools.evaluation.wemm_pilot_runtime import (
    QWEN_MANIFEST,
    ROOT,
    ModelRuntime,
    PilotError,
    configure_offline,
    decode_image,
    decoder_identity,
    package_versions,
    sha256,
    source_identity,
    verify_model,
)

_WINDOWS_REPLACE = os.name == "nt"
_REPLACE_DELAYS = (.03, .06, .12, .24, .48)


def _replace_atomic(source: Path, target: Path) -> None:
    """Tolerate a short Windows reader denying delete-share, never an unbounded retry."""
    for attempt in range(len(_REPLACE_DELAYS) + 1):
        try:
            os.replace(source, target)
            return
        except PermissionError as error:
            if (not _WINDOWS_REPLACE or getattr(error, "winerror", None) not in {5, 32, 33}
                    or attempt == len(_REPLACE_DELAYS)):
                raise
            time.sleep(_REPLACE_DELAYS[attempt])


def _cleanup_temporary(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass  # A cleanup failure must never replace the original write/replace error.


def _os_error_numbers(error: Exception) -> dict[str, int]:
    return {name: value for name in ("errno", "winerror")
            if isinstance(value := getattr(error, name, None), int)}


def read_json(path: Path) -> Any:
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise PilotError("DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique_object)


def checked_output(path: Path, input_roots: list[Path] = ()) -> Path:
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise PilotError("OUTPUT_LINK_REFUSED")
    resolved = path.resolve()
    if not resolved.is_relative_to(ROOT) or not any(
            resolved.is_relative_to((ROOT / name).resolve()) for name in (".tmp", "test-results")):
        raise PilotError("OUTPUT_MUST_BE_PROJECT_IGNORED_DIRECTORY")
    if any(resolved.is_relative_to(root.resolve()) for root in input_roots):
        raise PilotError("OUTPUT_OVERLAPS_INPUT_ROOT")
    return resolved


def atomic_json(path: Path, value: Any) -> None:
    path = checked_output(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = checked_output(path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp"))
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False),
                             encoding="utf-8")
        _replace_atomic(temporary, path)
    finally:
        _cleanup_temporary(temporary)


def save_vector(path: Path, vector: np.ndarray) -> str:
    path = checked_output(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = checked_output(path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp"))
    try:
        with temporary.open("wb") as stream:
            np.save(stream, vector, allow_pickle=False)
        _replace_atomic(temporary, path)
    finally:
        _cleanup_temporary(temporary)
    return sha256(path)


def _load_vector(directory: Path, record: dict) -> np.ndarray:
    name = record["file"]
    if Path(name).name != name or not name.endswith(".npy"):
        raise PilotError("CACHE_VECTOR_PATH_INVALID")
    path = directory / name
    if not path.is_file() or sha256(path) != record["sha256"]:
        raise PilotError("CACHE_VECTOR_HASH_MISMATCH")
    result = np.load(path, allow_pickle=False)
    if result.dtype != np.float32 or result.shape != (1, 2048):
        raise PilotError("CACHE_VECTOR_SHAPE_MISMATCH")
    if not np.isfinite(result).all() or not np.allclose(np.linalg.norm(result, axis=1), 1, atol=1e-5):
        raise PilotError("CACHE_VECTOR_INVALID")
    return result


def load_inputs(sample_file: Path, queries_file: Path, max_assets: int) -> tuple[dict, list[dict]]:
    sample = read_json(sample_file)
    assets = sample.get("assets", [])
    if type(max_assets) is not int or not 1 <= max_assets <= 1000 or not 1 <= len(assets) <= max_assets:
        raise PilotError("FROZEN_SAMPLE_SIZE_OUTSIDE_BUDGET")
    ids, hashes = [], set()
    for row in assets:
        asset_id = row.get("asset_id")
        digest = row.get("sha256", "")
        if (type(asset_id) not in (int, str) or asset_id == "" or
                not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest)):
            raise PilotError("SAMPLE_IDENTITY_INVALID")
        if digest in hashes:
            raise PilotError("DUPLICATE_SAMPLE_CONTENT")
        ids.append(asset_id)
        hashes.add(digest)
        if row.get("path") is not None and (
                "://" in str(row["path"]) or not Path(row["path"]).is_absolute()):
            raise PilotError("SAMPLE_REQUIRES_ABSOLUTE_LOCAL_PATHS")
    deterministic_rank(np.zeros(len(ids)), ids)
    raw_queries = read_json(queries_file)
    raw_queries = raw_queries.get("queries") if isinstance(raw_queries, dict) else raw_queries
    if not isinstance(raw_queries, list) or not 1 <= len(raw_queries) <= 20:
        raise PilotError("QUERIES_REQUIRE_ONE_TO_TWENTY_FROZEN_ITEMS")
    queries = [{"query_id": f"q{index:02d}", "text": item} if isinstance(item, str)
               else item for index, item in enumerate(raw_queries)]
    for item in queries:
        if (not isinstance(item, dict) or not isinstance(item.get("query_id"), str)
                or not item["query_id"] or not isinstance(item.get("text"), str)
                or not item["text"].strip() or len(item["text"]) > 2000):
            raise PilotError("QUERY_IDENTITY_INVALID")
    if len({row["query_id"] for row in queries}) != len(queries):
        raise PilotError("DUPLICATE_QUERY_ID")
    return sample, queries


def _input_roots(args: argparse.Namespace, sample: dict | None = None) -> list[Path]:
    roots = [path.resolve() for path in
             (args.data_root, args.qwen_model_dir, args.wemm_model_dir) if path is not None]
    if sample:
        roots += [Path(path).resolve() for path in sample.get("source_roots", [])]
        roots += [Path(row["path"]).resolve().parent for row in sample["assets"] if row.get("path")]
    return roots


def _check_catalog(args: argparse.Namespace, sample: dict) -> None:
    if sample.get("schema") != "wemm-pilot-sample-v1":
        return  # Public/synthetic frozen samples have no catalog binding.
    if args.data_root is None:
        raise PilotError("CATALOG_SAMPLE_REQUIRES_DATA_ROOT")
    from tools.evaluation.wemm_pilot_catalog import catalog_preflight
    current = catalog_preflight(args.data_root)
    if current["source_identity"] != sample["source_identity"]:
        raise PilotError("CATALOG_IDENTITY_CHANGED")


@contextmanager
def gpu_lock(input_roots: list[Path], runtime: Any = None):
    """An OS lock releases on a crash; independent pilot runs cannot share the GPU."""
    path = checked_output(ROOT / ".tmp/wemm-pilot-gpu.lock", input_roots)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise PilotError("ANOTHER_PILOT_PROCESS_OWNS_GPU") from None
        try:
            yield
        finally:
            try:
                if runtime is not None:
                    runtime.close()
            finally:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_UN)


def _vector_record(directory: Path, name: str, vector: np.ndarray, elapsed: float,
                   **metadata: Any) -> dict:
    vector = normalize_prefix(vector, 2048)
    filename = name + ".npy"
    return {"file": filename, "sha256": save_vector(directory / filename, vector),
            "encode_ms": elapsed, **metadata}


def encode_run(args: argparse.Namespace, *, runtime_factory=ModelRuntime) -> dict:
    if args.sample_file is None or args.queries is None or args.model is None:
        raise PilotError("ENCODE_REQUIRES_FROZEN_SAMPLE_QUERIES_AND_MODEL")
    sample, queries = load_inputs(args.sample_file, args.queries, args.max_assets)
    if args.command == "smoke" and not 30 <= len(sample["assets"]) <= 50:
        raise PilotError("SMOKE_REQUIRES_THIRTY_TO_FIFTY_FROZEN_ASSETS")
    roots = _input_roots(args, sample)
    checked_output(args.out_dir / args.model, roots)
    # Acquire before writing any cache/identity/progress. A competing process that
    # loses this lock must not overwrite the successful process's evidence.
    with gpu_lock(roots):
        return _encode_locked(args, sample, queries, roots, runtime_factory)


def _encode_locked(args: argparse.Namespace, sample: dict, queries: list[dict],
                   roots: list[Path], runtime_factory: Any) -> dict:
    directory = checked_output(args.out_dir / args.model, roots)
    checked_output(directory / "runtime-cache", roots)
    configure_offline(directory / "runtime-cache")
    _check_catalog(args, sample)
    model_dir = args.qwen_model_dir if args.model == "qwen" else args.wemm_model_dir
    if model_dir is None:
        raise PilotError("EXPLICIT_LOCAL_MODEL_DIRECTORY_REQUIRED")
    verification_started = time.perf_counter()
    lock = verify_model(args.model, model_dir)
    verification_ms = (time.perf_counter() - verification_started) * 1000
    dtype = args.dtype or ("fp16" if args.model == "qwen" else "bf16")
    spec = {"model": args.model, "model_lock": lock, "dtype": dtype, "device": args.device,
            "native_dimension": 2048, "source_files": source_identity(),
            "decoder": decoder_identity(), "packages": package_versions()}
    assets = sample["assets"]
    identities = [{key: row.get(key) for key in ("asset_id", "sha256", "selection_stratum")}
                  for row in assets]
    identity = cache_identity(spec, identities, queries)
    identity_path = directory / "identity.json"
    if identity_path.exists() and read_json(identity_path)["cache_identity"] != identity:
        raise PilotError("CACHE_IDENTITY_MISMATCH_USE_NEW_OUTPUT_DIRECTORY")
    if args.command == "encode":
        if args.smoke_run_dir is None:
            raise PilotError("ENCODE_REQUIRES_COMPLETED_SMOKE_RUN")
        smoke = read_json(args.smoke_run_dir / "result.json")
        if (smoke.get("status") != "COMPLETE" or smoke.get("purpose") != "smoke"
                or smoke.get("model_spec") != spec or not smoke.get("stability", {}).get("passed")):
            raise PilotError("SMOKE_IDENTITY_OR_ACCEPTANCE_MISMATCH")
    manifest_path = None
    if args.model == "qwen":
        native = read_json(QWEN_MANIFEST)
        native["provider"]["provider_id"] = "qwen3-vl-embedding-2b-pilot-native2048"
        native["embedding"]["dimension"] = 2048
        manifest_path = directory / "experimental-native-manifest.json"
        atomic_json(manifest_path, native)
    atomic_json(identity_path, {"cache_identity": identity, "model_spec": spec,
                                "inputs": identities, "queries": queries})
    progress_path = directory / "progress.json"
    progress = read_json(progress_path) if progress_path.exists() else {
        "cache_identity": identity, "images": {}, "queries": {}, "failures": []}
    if progress.get("cache_identity") != identity:
        raise PilotError("PROGRESS_IDENTITY_MISMATCH")
    previous_result_path = directory / "result.json"
    if previous_result_path.exists():
        previous = read_json(previous_result_path)
        if previous.get("cache_identity") != identity:
            raise PilotError("RESULT_IDENTITY_MISMATCH")
        if (previous.get("status") == "BLOCKED" and previous.get("failure_history_write_error")
                and previous.get("failure") not in progress["failures"]):
            progress["failures"].append(previous["failure"])
            atomic_json(progress_path, progress)
    runtime = runtime_factory(args.model, model_dir, args.device, dtype, manifest_path)
    result = {"schema": "wemm-pilot-run-v1", "status": "RUNNING", "purpose": args.command,
              "model_spec": spec, "cache_identity": identity, "inputs": identities,
              "queries": queries, "verification_ms": verification_ms,
              "input_roots": [str(root) for root in roots],
              "quality_status": "UNVERIFIED / RELATIVE RANKING ONLY"}
    phase = "input-check"
    try:
        # Fail missing or changed inputs before taking GPU memory, even on a cache resume.
        for index, row in enumerate(assets):
            phase = f"input:{index}"
            if row.get("path") is None or row.get("input_status", "READY_FOR_HASH_CHECK") not in {
                    "READY_FOR_HASH_CHECK", "READY"}:
                raise PilotError("FROZEN_INPUT_UNAVAILABLE")
            if sha256(Path(row["path"])) != row["sha256"]:
                raise PilotError("IMAGE_CONTENT_CHANGED")
        with closing(runtime):
            phase = "load"
            started = time.perf_counter()
            result["runtime"] = runtime.load()
            result["load_ms"] = (time.perf_counter() - started) * 1000
            # First text -> first image -> same text tests the official rope reset fix.
            phase = "stability-text-before"
            first_text, first_ms = runtime.measured_encode(text=queries[0]["text"])
            phase = "stability-image"
            first_image, decoded = decode_image(Path(assets[0]["path"]), assets[0]["sha256"])
            try:
                first_visual, image_ms = runtime.measured_encode(image=first_image)
            finally:
                first_image.close()
            phase = "stability-text-after"
            repeated, repeated_ms = runtime.measured_encode(text=queries[0]["text"])
            similarity = float(np.sum(first_text * repeated))
            delta = float(np.max(np.abs(first_text - repeated)))
            result["stability"] = {"cosine": similarity, "max_absolute_delta": delta,
                                   "before_ms": first_ms, "after_ms": repeated_ms,
                                   "minimum_cosine": .99999, "maximum_delta": .0001,
                                   "passed": similarity >= .99999 and delta <= .0001}
            if not result["stability"]["passed"]:
                raise PilotError("TEXT_IMAGE_TEXT_STABILITY_FAILED")
            first_values = {"queries": (first_text, first_ms, {}),
                            "images": (first_visual, image_ms, {"decoded": decoded})}
            for kind, rows in (("queries", queries), ("images", assets)):
                for index, row in enumerate(rows):
                    phase, key = f"{kind}:{index}", str(index)
                    if key in progress[kind]:
                        _load_vector(directory, progress[kind][key])
                        continue
                    metadata = {}
                    if index == 0:
                        value, elapsed, metadata = first_values[kind]
                    elif kind == "queries":
                        value, elapsed = runtime.measured_encode(text=row["text"])
                    else:
                        image, facts = decode_image(Path(row["path"]), row["sha256"])
                        try:
                            value, elapsed = runtime.measured_encode(image=image)
                        finally:
                            image.close()
                        metadata["decoded"] = facts
                    progress[kind][key] = _vector_record(
                        directory, f"{kind}-{index:04d}", value, elapsed, **metadata,
                    )
                    atomic_json(progress_path, progress)
            phase = "catalog-after"
            _check_catalog(args, sample)
            result.update(status="COMPLETE", images=progress["images"],
                          query_vectors=progress["queries"], resources=runtime.resources(),
                          historical_failure_count=len(progress["failures"]),
                          failure_history_file="progress.json",
                          load_timing_scope="single process load; OS file cache state uncontrolled")
    except Exception as error:
        failure = {"failure_id": uuid.uuid4().hex, "phase": phase, "type": type(error).__name__,
                   "code": str(error) if isinstance(error, PilotError) else "ENCODING_FAILED",
                   **_os_error_numbers(error)}
        progress["failures"].append(failure)
        result.update(status="BLOCKED", failure=failure, resources=runtime.resources())
        try:
            atomic_json(progress_path, progress)
        except OSError as recording_error:
            result["failure_history_write_error"] = {
                "type": type(recording_error).__name__, **_os_error_numbers(recording_error)}
        raise
    finally:
        runtime.close()
        try:
            atomic_json(directory / "result.json", result)
        except OSError:
            if result["status"] != "BLOCKED":
                raise
            # The CLI still exits with the original failure if its report is also locked.
    return result


def _read_complete_run(directory: Path) -> tuple[dict, np.ndarray, np.ndarray]:
    result = read_json(directory / "result.json")
    if result.get("status") != "COMPLETE" or not result.get("stability", {}).get("passed"):
        raise PilotError("COMPARISON_REQUIRES_COMPLETE_STABLE_RUNS")
    if cache_identity(result["model_spec"], result["inputs"], result["queries"]) != result["cache_identity"]:
        raise PilotError("RESULT_IDENTITY_MISMATCH")
    images = np.concatenate([_load_vector(directory, result["images"][str(i)])
                             for i in range(len(result["inputs"]))])
    queries = np.concatenate([_load_vector(directory, result["query_vectors"][str(i)])
                              for i in range(len(result["queries"]))])
    return result, images, queries


def compare_runs(args: argparse.Namespace) -> dict:
    qwen_dir = args.qwen_run_dir or args.out_dir / "qwen"
    wemm_dir = args.wemm_run_dir or args.out_dir / "wemm"
    qwen, q_images, q_queries = _read_complete_run(qwen_dir)
    wemm, w_images, w_queries = _read_complete_run(wemm_dir)
    roots = _input_roots(args) + [Path(path) for run in (qwen, wemm)
                                 for path in run.get("input_roots", [])]
    checked_output(args.out_dir, roots)
    if qwen["model_spec"]["model"] != "qwen" or wemm["model_spec"]["model"] != "wemm":
        raise PilotError("MODEL_ROLE_MISMATCH")
    if qwen["inputs"] != wemm["inputs"] or qwen["queries"] != wemm["queries"]:
        raise PilotError("FROZEN_COMPARISON_INPUT_MISMATCH")
    for key in ("decoder", "source_files"):
        if qwen["model_spec"][key] != wemm["model_spec"][key]:
            raise PilotError("COMPARISON_PIPELINE_MISMATCH")
    for index in range(len(qwen["inputs"])):
        if qwen["images"][str(index)]["decoded"] != wemm["images"][str(index)]["decoded"]:
            raise PilotError("DECODED_PIXELS_MISMATCH")
    ids = [row["asset_id"] for row in qwen["inputs"]]
    rankings, timing, distributions = {}, {}, {}
    for name, dimension, images, queries in (("qwen512", 512, q_images, q_queries),
                                             ("qwen2048", 2048, q_images, q_queries),
                                             ("wemm512", 512, w_images, w_queries)):
        start = time.perf_counter()
        scores = normalize_prefix(queries, dimension) @ normalize_prefix(images, dimension).T
        timing[name] = (time.perf_counter() - start) * 1000
        rankings[name] = {query["query_id"]: deterministic_rank(scores[i], ids)
                          for i, query in enumerate(qwen["queries"])}
        distributions[name] = {query["query_id"]: {
            "min": float(scores[i].min()), "median": float(np.median(scores[i])),
            "max": float(scores[i].max()), "cross_model_comparison_valid": False}
            for i, query in enumerate(qwen["queries"])}
    comparison = compare_rankings(rankings)
    result = {**comparison, "schema": "wemm-pilot-comparison-v1", "rankings": rankings,
              "query_definitions": qwen["queries"], "inputs": qwen["inputs"],
              "input_roots": [str(root) for root in roots],
              "matrix_ms": timing, "within_model_score_distributions": distributions,
              "run_identities": {"qwen": qwen["cache_identity"], "wemm": wemm["cache_identity"]},
              "historical_failures": {"qwen": qwen.get("historical_failure_count", 0),
                                      "wemm": wemm.get("historical_failure_count", 0)},
              "production_ranking_equivalence": "UNVERIFIED"}
    if args.labels:
        labels_document = read_json(args.labels)
        if not labels_document.get("provenance"):
            raise PilotError("LABEL_PROVENANCE_REQUIRED")
        labels = labels_document["queries"]
        expected_inputs = [{key: row[key] for key in ("asset_id", "sha256")}
                           for row in qwen["inputs"]]
        if (labels_document.get("input_identities") != expected_inputs or
                labels_document.get("query_definitions") != qwen["queries"]):
            raise PilotError("LABEL_DATASET_IDENTITY_MISMATCH")
        if not set(labels).issubset({query["query_id"] for query in qwen["queries"]}):
            raise PilotError("LABEL_QUERY_IDENTITY_INVALID")
        result["label_provenance"] = labels_document["provenance"]
        result["judged_precision"] = {}
        result["fully_annotated_metrics"] = {}
        for model, by_query in rankings.items():
            result["judged_precision"][model] = {}
            result["fully_annotated_metrics"][model] = {}
            for query_id, ranked_ids in by_query.items():
                rows = labels.get(query_id, [])
                by_id = {row["asset_id"]: row["relevant"] for row in rows}
                if (len(by_id) != len(rows) or not set(by_id).issubset(set(ids)) or
                        any(type(asset_id) is not type(ids[0]) for asset_id in by_id)):
                    raise PilotError("LABEL_ASSET_IDENTITY_INVALID")
                result["judged_precision"][model][query_id] = {
                    str(k): precision_at_k(ranked_ids, by_id, k) for k in (20, 50)}
                strata = {row["asset_id"]: row.get("selection_stratum") or "unspecified"
                          for row in qwen["inputs"]}
                result["fully_annotated_metrics"][model][query_id] = annotated_metrics(
                    ranked_ids, by_id, strata=strata,
                )
    atomic_json(checked_output(args.out_dir / "comparison.json", roots), result)
    return result


def preview_candidates(rankings: dict, maximum: int) -> list:
    """Share the preview budget across each query/model before taking deeper ranks."""
    if type(maximum) is not int or not 1 <= maximum <= 500:
        raise PilotError("PREVIEW_LIMIT_MUST_BE_ONE_TO_FIVE_HUNDRED")
    models = sorted(rankings)
    queries = sorted(rankings[models[0]])
    selected, seen = [], set()
    for rank in range(50):
        for query_id in queries:
            for model in models:
                ranking = rankings[model][query_id]
                if rank >= len(ranking) or ranking[rank] in seen:
                    continue
                seen.add(ranking[rank])
                selected.append(ranking[rank])
                if len(selected) == maximum:
                    return selected
    return selected


def report(args: argparse.Namespace) -> dict:
    comparison = read_json(args.comparison_file or args.out_dir / "comparison.json")
    sample = read_json(args.sample_file) if args.sample_file else None
    roots = _input_roots(args, sample) + [Path(path) for path in comparison.get("input_roots", [])]
    destination = checked_output(args.out_dir / "report.html", roots)
    previews, failures = {}, []
    wanted = preview_candidates(comparison["rankings"], args.max_previews)
    top50_union = {asset_id for model in comparison["rankings"].values()
                   for ranked in model.values() for asset_id in ranked[:50]}
    if sample is not None:
        expected = [{key: row.get(key) for key in ("asset_id", "sha256", "selection_stratum")}
                    for row in sample["assets"]]
        if expected != comparison["inputs"]:
            raise PilotError("REPORT_SAMPLE_IDENTITY_MISMATCH")
        by_id = {row["asset_id"]: row for row in sample["assets"]}
        for index, asset_id in enumerate(wanted):
            row = by_id[asset_id]
            try:
                image, _ = decode_image(Path(row["path"]), row["sha256"])
                try:
                    image.thumbnail((320, 320))
                    path = checked_output(args.out_dir / "previews" / f"{index:04d}.jpg", roots)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    image.save(path, format="JPEG", quality=75, exif=b"")
                    previews[asset_id] = f"previews/{index:04d}.jpg"
                finally:
                    image.close()
            except Exception as error:
                failures.append({"asset_id": asset_id, "type": type(error).__name__})
    esc = lambda value: html.escape(str(value), quote=True)
    parts = ['<!doctype html><html lang="zh"><meta charset="utf-8">',
             '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
             'img-src \'self\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">',
             '<title>WeMM 本地检索对照</title><style>body{font:16px sans-serif;margin:24px}'
             '.columns{display:flex;gap:18px}.model{flex:1;min-width:0}'
             'li{min-height:70px;margin-bottom:12px}img{max-width:180px;max-height:130px}'
             'small{display:block;color:#555}</style>',
             '<h1>WeMM / Qwen 本地检索对照</h1><p>仅排名观察；相似度不能跨模型直接比较。'
             '小样本排名不等于生产全库排名。无完整标签时不提供准确率或召回率。</p>',
             f'<p>冻结资产数：{esc(comparison["asset_count"])}；本地预览：{len(previews)}'
             f'（本轮上限{args.max_previews}张）；读取失败：{len(failures)}；'
             f'Top50并集中缺预览：{len(top50_union) - len(previews)}。'
             '文件只适合本地查看，请勿上传。</p>']
    for query in comparison["query_definitions"]:
        query_id = query["query_id"]
        parts.append(f'<h2>{esc(query["text"])}</h2><div class="columns">')
        positions = {model: {asset_id: i for i, asset_id in enumerate(ranks[query_id], 1)}
                     for model, ranks in comparison["rankings"].items()}
        for model, ranks in comparison["rankings"].items():
            parts.append(f'<section class="model"><h3>{esc(model)}</h3>')
            metrics = comparison.get("fully_annotated_metrics", {}).get(model, {}).get(query_id, {})
            if metrics.get("status") == "COMPLETE_ANNOTATED_POPULATION":
                parts.append(f'<p>仅按数据集标注：相关图 {metrics["annotated_positives"]} 张；'
                             f'AP {esc(metrics["average_precision"])}。少于50张相关图时，'
                             'Precision@50的理论上限也小于100%。</p>')
                parts.append('<small>' + esc(json.dumps(metrics["topk"], ensure_ascii=False))
                             + '</small>')
            parts.append('<ol>')
            for index, asset_id in enumerate(ranks[query_id][:50], 1):
                if index == 21:
                    parts.append('</ol><details><summary>第21至50名</summary><ol start="21">')
                thumbnail = f'<img loading="lazy" src="{previews[asset_id]}" alt="">' \
                    if asset_id in previews else "无本地预览"
                other = " / ".join(f"{name}: {ranks[asset_id]}" for name, ranks in positions.items())
                parts.append(f'<li>{thumbnail}<small>资产 {esc(asset_id)} · {esc(other)}</small></li>')
            parts.append('</ol>' + ('</details>' if len(ranks[query_id]) > 20 else '') + '</section>')
        parts.append('</div>')
    parts.append('</html>')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(parts), encoding="utf-8")
    evidence = {"status": "REPORT_WRITTEN", "preview_count": len(previews),
                "max_previews": args.max_previews,
                "unpreviewed_top50_count": len(top50_union) - len(previews),
                "preview_failures": failures, "quality_status": comparison["status"]}
    atomic_json(args.out_dir / "report-status.json", evidence)
    return evidence


def preflight(args: argparse.Namespace) -> dict:
    result = {"schema": "wemm-pilot-preflight-v1", "status": "PREFLIGHT_ONLY",
              "packages": package_versions(), "gpu_model_tested": False, "models": {}}
    checked_output(args.out_dir, _input_roots(args))
    if args.data_root is not None:
        from tools.evaluation.wemm_pilot_catalog import catalog_preflight, freeze_sample
        result["catalog"] = catalog_preflight(args.data_root)
        if args.freeze_sample:
            sample = freeze_sample(args.data_root, args.max_assets, args.seed)
            roots = _input_roots(args, sample)
            checked_output(args.out_dir, roots)
            path = checked_output(args.sample_file or args.out_dir / "sample.private.json", roots)
            if path.exists():
                raise PilotError("FROZEN_SAMPLE_ALREADY_EXISTS")
            atomic_json(path, sample)
    for model, directory in (("qwen", args.qwen_model_dir), ("wemm", args.wemm_model_dir)):
        if directory is None:
            result["models"][model] = {"status": "MODEL_DIRECTORY_NOT_SUPPLIED"}
            continue
        try:
            lock = verify_model(model, directory)
            result["models"][model] = {"status": "HASH_VERIFIED_NOT_LOADED", "lock": lock}
        except PilotError as error:
            result["models"][model] = {"status": "BLOCKED", "reason": str(error)}
    atomic_json(args.out_dir / "preflight.json", result)
    return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("preflight", "smoke", "encode", "compare", "report"):
        command = commands.add_parser(name)
        command.add_argument("--data-root", type=Path)
        command.add_argument("--out-dir", type=Path, required=True)
        command.add_argument("--max-assets", type=int, default=1000)
        command.add_argument("--max-previews", type=int, default=150)
        command.add_argument("--seed", type=int, default=20261020)
        command.add_argument("--device", default="cuda:0")
        command.add_argument("--qwen-model-dir", type=Path)
        command.add_argument("--wemm-model-dir", type=Path)
        command.add_argument("--sample-file", type=Path)
        command.add_argument("--queries", type=Path)
        command.add_argument("--model", choices=("qwen", "wemm"))
        command.add_argument("--dtype", choices=("bf16", "fp16"))
        command.add_argument("--smoke-run-dir", type=Path)
        command.add_argument("--qwen-run-dir", type=Path)
        command.add_argument("--wemm-run-dir", type=Path)
        command.add_argument("--comparison-file", type=Path)
        command.add_argument("--labels", type=Path)
        command.add_argument("--freeze-sample", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        checked_output(args.out_dir, _input_roots(args))
        if args.command in {"smoke", "encode"}:
            result = encode_run(args)
        else:
            result = {"preflight": preflight, "compare": compare_runs, "report": report}[
                args.command](args)
        print(json.dumps({"status": result["status"], "command": args.command}, ensure_ascii=False))
        return 0
    except Exception as error:
        code = str(error) if isinstance(error, PilotError) else getattr(error, "code", type(error).__name__)
        print(json.dumps({"status": "BLOCKED", "reason": code, **_os_error_numbers(error)},
                         ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
