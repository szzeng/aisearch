"""Frozen-feature category heads for the isolated album Pareto experiment.

Fit accepts only training/calibration inputs. Evaluate cannot change weights or
thresholds. Class-balanced logistic logits are *not* calibrated probabilities.
No head alters the complete result membership; confidence is a separate set.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit

from tools.evaluation.budget_search_metrics import embeddings_from_record, judged_metrics
from tools.evaluation.prepare_wemm_coco import QUERIES, controlled_output, write_json
from tools.evaluation.wemm_pilot_core import deterministic_rank, normalize_prefix

ROOT = Path(__file__).resolve().parents[2]
CLASS_QUERIES = QUERIES[:8]
CLASS_IDS = [query["query_id"] for query in CLASS_QUERIES]
ALIASES = {query["text"]: query["query_id"] for query in CLASS_QUERIES}
CONFIG = {
    "schema": "album-linear-probe-config-v1", "C": 1.0,
    "objective": "sum(n/(2*n_class) * binary_logistic_loss) + ||weight||^2/(2*C)",
    "bias_regularization": False, "features": "unit-normalized-prefix-no-standardization",
    "solver": "scipy-L-BFGS-B-float64-CPU", "maxiter": 300, "gtol": 1e-6, "ftol": 1e-12,
    "target_calibration_precision": 0.90, "minimum_calibration_predictions": 20,
    "threshold_rule": "maximum coverage complete tie-group satisfying precision/minimum; otherwise abstain",
    "confidence_baseline": "apply identical calibration-only threshold rule to original Chinese cosine scores",
    "route_aliases": ALIASES,
    "score_meaning": "class-balanced logistic logit; NOT a calibrated probability",
}


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class Reader:
    def __init__(self) -> None:
        self.hashes: dict[Path, str] = {}

    def raw(self, path: Path, bound: int = 32 * 1024 * 1024) -> bytes:
        if path.stat().st_size > bound:
            raise ValueError("input exceeds bounded offline probe size")
        data = path.read_bytes()
        digest = sha(data)
        resolved = path.resolve()
        if resolved in self.hashes and self.hashes[resolved] != digest:
            raise ValueError("input changed while evaluating")
        self.hashes[resolved] = digest
        return data

    def json(self, path: Path) -> Any:
        return json.loads(self.raw(path))

    def unchanged(self) -> dict[str, str]:
        for path, digest in self.hashes.items():
            if sha(path.read_bytes()) != digest:
                raise ValueError("input changed while evaluating")
        return {str(path): digest for path, digest in self.hashes.items()}


def flickr_id(asset: dict[str, Any]) -> str:
    direct = str(asset.get("flickr_photo_id") or "")
    candidates = [direct] if direct else []
    for url in (asset.get("coco", {}).get("flickr_url", ""),
                asset.get("attribution", {}).get("OriginalLandingURL", "")):
        # COCO uses staticflickr URLs; OpenImages uses a Flickr landing page.
        match = re.search(r"/([0-9]+)_[^/]+\.(?:jpg|png)", url, re.I)
        match = match or re.search(r"flickr\.com/photos/[^/]+/([0-9]+)", url)
        if match:
            candidates.append(match.group(1))
    if not candidates or any(not value.isdigit() for value in candidates) or len(set(candidates)) != 1:
        raise ValueError("missing or inconsistent Flickr source identity")
    return candidates[0]


def asset_keys(assets: list[dict[str, Any]]) -> dict[str, set[Any]]:
    if not assets or len(assets) > 2000:
        raise ValueError("empty or oversized sample")
    keys = {"asset_id": [row["asset_id"] for row in assets],
            "sha256": [row["sha256"] for row in assets],
            "flickr_photo_id": [flickr_id(row) for row in assets]}
    if any(len(set(values)) != len(values) for values in keys.values()):
        raise ValueError("duplicate sample ID, content hash, or Flickr source")
    if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
           for value in keys["sha256"]):
        raise ValueError("invalid image content hash")
    return {key: set(values) for key, values in keys.items()}


def disjoint_samples(*samples: list[dict[str, Any]]) -> None:
    seen: dict[str, set[Any]] = {key: set() for key in ("asset_id", "sha256", "flickr_photo_id")}
    for assets in samples:
        for key, values in asset_keys(assets).items():
            if seen[key] & values:
                raise ValueError(f"split leakage by {key}")
            seen[key].update(values)


def space_spec(record: dict[str, Any], dimension: int) -> dict[str, Any]:
    expected = {"qwen": {512, 1024}, "fgclip-base-adaptive": {768}}
    if record.get("model") not in expected or dimension not in expected[record["model"]]:
        raise ValueError("only Qwen512/1024 and FG-adaptive768 are planned")
    # limit describes a population, not how any given image is encoded.
    settings = {key: value for key, value in record["settings"].items() if key != "limit"}
    return {**{key: record[key] for key in ("model", "dtype", "model_lock", "packages",
                                            "decoder", "source_sha256", "native_dimension")},
            "settings": settings, "dimension": dimension,
            "normalization": "unit-prefix-v1"}


@dataclass
class Split:
    role: str
    assets: list[dict[str, Any]]
    queries: list[dict[str, str]]
    images: np.ndarray
    query_vectors: np.ndarray
    spec: dict[str, Any]
    provenance: dict[str, Any]

    @property
    def ids(self) -> list[Any]:
        return [row["asset_id"] for row in self.assets]


def load_split(reader: Reader, directory: Path, sample_path: Path,
               dimension: int, role: str) -> Split:
    if role not in {"train", "calibration", "test", "exploratory"}:
        raise ValueError("unknown data role")
    sample = reader.json(sample_path)
    assets = sample["assets"]
    asset_keys(assets)
    record = reader.json(directory / "result.json")
    if (record.get("assets") != assets or
            record.get("sample_sha256") != reader.hashes[sample_path.resolve()]):
        raise ValueError("run differs from frozen sample bytes/membership")
    matrices = []
    for name in ("images.npy", "queries.npy"):
        raw = reader.raw(directory / name)
        if sha(raw) != record["vectors_sha256"][name]:
            raise ValueError("embedding array checksum mismatch")
        matrices.append(np.load(io.BytesIO(raw), allow_pickle=False))
    embeddings_from_record(record, *matrices)
    if role != "exploratory":
        expected_source = "val2014" if role == "test" else "train2014"
        if any(asset.get("source_split") != expected_source for asset in assets):
            raise ValueError("data role differs from official COCO split")
        if record["queries"] != CLASS_QUERIES:
            raise ValueError("training/heldout query order differs from fixed eight Chinese categories")
    return Split(role, assets, record["queries"], normalize_prefix(matrices[0], dimension),
                 normalize_prefix(matrices[1], dimension), space_spec(record, dimension),
                 {"run_identity": record["identity"], "sample_sha256": record["sample_sha256"],
                  "role": role})


def load_labels(reader: Reader, path: Path, split: Split, *, training: bool) -> dict[str, dict[Any, Any]]:
    document = reader.json(path)
    identities = [{key: row[key] for key in ("asset_id", "sha256")} for row in split.assets]
    if document["input_identities"] != identities or document["query_definitions"] != split.queries:
        raise ValueError("label input bytes or ordered query definitions differ")
    if set(document["queries"]) != {row["query_id"] for row in split.queries}:
        raise ValueError("label query coverage differs")
    result = {}
    for query, rows in document["queries"].items():
        if len(rows) != len(split.ids) or {row["asset_id"] for row in rows} != set(split.ids):
            raise ValueError("label membership differs")
        result[query] = {row["asset_id"]: row["relevant"] for row in rows}
        if any(type(value) is not bool and (training or value is not None)
               for value in result[query].values()):
            raise ValueError("training requires complete boolean labels; evaluation permits unknown")
    return result


def logistic_objective(theta: np.ndarray, features: np.ndarray,
                       targets: np.ndarray, weights: np.ndarray) -> tuple[float, np.ndarray]:
    logits = features @ theta[:-1] + theta[-1]
    residual = weights * (expit(logits) - targets)
    loss = np.sum(weights * (np.logaddexp(0, logits) - targets * logits))
    loss += 0.5 * np.dot(theta[:-1], theta[:-1]) / CONFIG["C"]
    gradient = np.concatenate((features.T @ residual + theta[:-1] / CONFIG["C"], [residual.sum()]))
    return float(loss), gradient


def fit_logistic(features: np.ndarray, targets: np.ndarray) -> tuple[np.ndarray, float, dict[str, Any]]:
    features = np.asarray(features, dtype=np.float64)
    targets = np.asarray(targets)
    if (features.ndim != 2 or targets.shape != (len(features),) or len(features) < 2
            or features.shape[1] < 1 or not np.isfinite(features).all()
            or not np.isin(targets, [0, 1]).all()):
        raise ValueError("invalid training matrix/labels")
    positive = int(targets.sum())
    if not 0 < positive < len(features):
        raise ValueError("both positive and negative training examples are required")
    weights = np.where(targets == 1, len(features) / (2 * positive),
                       len(features) / (2 * (len(features) - positive)))
    started = time.perf_counter()
    result = minimize(logistic_objective, np.zeros(features.shape[1] + 1),
                      args=(features, targets.astype(np.float64), weights), method="L-BFGS-B", jac=True,
                      options={key: CONFIG[key] for key in ("maxiter", "gtol", "ftol")})
    elapsed = (time.perf_counter() - started) * 1000
    if not result.success or not np.isfinite(result.x).all():
        raise ValueError(f"logistic optimization did not converge: {result.message}")
    return result.x[:-1], float(result.x[-1]), {
        "training_ms": elapsed, "iterations": int(result.nit), "objective": float(result.fun),
        "gradient_inf_norm": float(np.max(np.abs(result.jac))), "solver_message": str(result.message),
        "positive_count": positive, "negative_count": len(features) - positive,
        "class_weights": {"positive": len(features) / (2 * positive),
                          "negative": len(features) / (2 * (len(features) - positive))}}


def select_threshold(scores: np.ndarray, labels: np.ndarray) -> dict[str, Any]:
    scores, labels = np.asarray(scores), np.asarray(labels)
    if (scores.ndim != 1 or labels.shape != scores.shape or len(scores) < 1
            or not np.isfinite(scores).all() or not np.isin(labels, [0, 1]).all()):
        raise ValueError("invalid calibration scores/labels")
    order = np.argsort(-scores, kind="stable")
    sorted_scores, positives = scores[order], np.cumsum(labels[order])
    result = {"enabled": False, "threshold": None, "selected": 0, "precision": None,
              "coverage": 0.0, "positive_recall": 0.0 if labels.sum() else None,
              "reason": "no complete score group meets calibration precision and minimum support"}
    for index in range(len(scores)):
        if index + 1 < len(scores) and sorted_scores[index] == sorted_scores[index + 1]:
            continue
        count = index + 1
        precision = float(positives[index] / count)
        if (count >= CONFIG["minimum_calibration_predictions"]
                and precision >= CONFIG["target_calibration_precision"]):
            result = {"enabled": True, "threshold": float(sorted_scores[index]), "selected": count,
                      "precision": precision, "coverage": count / len(scores),
                      "positive_recall": float(positives[index] / labels.sum()) if labels.sum() else None,
                      "reason": "maximum calibrated coverage; observed precision is not a guarantee"}
    return result


def fit_heads(train: Split, calibration: Split, train_labels: dict, cal_labels: dict) -> tuple[dict, np.ndarray, np.ndarray]:
    if train.role != "train" or calibration.role != "calibration":
        raise ValueError("fit only accepts train and calibration roles")
    if train.spec != calibration.spec or train.queries != calibration.queries or train.queries != CLASS_QUERIES:
        raise ValueError("training/calibration embedding space or query identity differs")
    disjoint_samples(train.assets, calibration.assets)
    for split, labels in ((train, train_labels), (calibration, cal_labels)):
        if (set(labels) != set(CLASS_IDS) or any(set(values) != set(split.ids) for values in labels.values())
                or any(type(value) is not bool for values in labels.values() for value in values.values())):
            raise ValueError("learning requires complete boolean labels for exactly the eight categories")
    weight_rows, biases, classes = [], [], {}
    calibration_cosine = calibration.query_vectors @ calibration.images.T
    for column, query in enumerate(CLASS_IDS):
        y_train = np.asarray([train_labels[query][aid] for aid in train.ids], dtype=np.float64)
        y_cal = np.asarray([cal_labels[query][aid] for aid in calibration.ids], dtype=np.float64)
        weights, bias, diagnostics = fit_logistic(train.images, y_train)
        threshold = select_threshold(score_heads(calibration.images, weights[None, :], np.array([bias]))[:, 0], y_cal)
        classes[query] = {"fit": diagnostics, "calibration": threshold,
                          "cosine_calibration": select_threshold(calibration_cosine[column], y_cal)}
        weight_rows.append(weights)
        biases.append(bias)
    manifest = {"schema": "album-linear-heads-v1", "status": "FROZEN", "config": CONFIG,
                "space_spec": train.spec, "classes": classes, "query_definitions": train.queries,
                "study_kind": ("POSTHOC_512_COMPATIBILITY_AFTER_1024_RESULTS_NO_HYPERPARAMETER_CHANGE"
                               if train.spec.get("model") == "qwen" and train.spec["dimension"] == 512
                               else "PREDECLARED_PRIMARY_DIMENSION"),
                "training": train.provenance, "calibration": calibration.provenance,
                "training_assets": train.assets, "calibration_assets": calibration.assets,
                "limitations": ["Class balancing changes the training prior; logits are not probabilities.",
                                "Calibration is a selected sample; its precision does not transfer as a guarantee."]}
    return manifest, np.stack(weight_rows), np.asarray(biases)


def score_heads(images: np.ndarray, weights: np.ndarray, biases: np.ndarray) -> np.ndarray:
    """Bound conversions to 8192 rows; a 300k index is never copied to float64."""
    if (images.ndim != 2 or weights.ndim != 2 or biases.shape != (len(weights),)
            or images.shape[1] != weights.shape[1] or not 1 <= len(weights) <= 8
            or not np.isfinite(weights).all() or not np.isfinite(biases).all()):
        raise ValueError("invalid scoring matrix/weights")
    output = np.empty((len(images), len(weights)), dtype=np.float64)
    for start in range(0, len(images), 8192):
        block = images[start:start + 8192].astype(np.float64)
        if not np.isfinite(block).all():
            raise ValueError("nonfinite input feature")
        output[start:start + len(block)] = block @ weights.T + biases
    return output


def route_query(text: str, base_ranking: list, head_rankings: dict, classes: dict) -> tuple[list, str]:
    # Exact aliases only: never turn 'two cats', a negation or a date into 'cat'.
    query = ALIASES.get(text.strip())
    if query is not None and classes[query]["calibration"]["enabled"]:
        head = head_rankings[query]
        if len(head) != len(base_ranking) or set(head) != set(base_ranking):
            raise ValueError("route must preserve every candidate")
        return list(head), "head"
    return list(base_ranking), "base"


def predict(manifest: dict, weights: np.ndarray, biases: np.ndarray, split: Split) -> dict:
    if manifest["space_spec"] != split.spec:
        raise ValueError("head and evaluation embedding space differ")
    disjoint_samples(manifest["training_assets"], manifest["calibration_assets"], split.assets)
    dimension = split.images.shape[1]
    if (weights.shape != (8, dimension) or biases.shape != (8,) or not np.isfinite(weights).all()
            or not np.isfinite(biases).all()):
        raise ValueError("head weights have invalid shape/values")
    started = time.perf_counter()
    scores = score_heads(split.images, weights, biases)
    head_scan_ms = (time.perf_counter() - started) * 1000
    base_scores = split.query_vectors @ split.images.T
    heads = {query: deterministic_rank(scores[:, i], split.ids) for i, query in enumerate(CLASS_IDS)}
    base, routed, all_heads, confident, routes, base_confident, route_confident = {}, {}, {}, {}, {}, {}, {}
    for index, query in enumerate(split.queries):
        qid = query["query_id"]
        base[qid] = deterministic_rank(base_scores[index], split.ids)
        routed[qid], routes[qid] = route_query(query["text"], base[qid], heads, manifest["classes"])
        # Non-category queries remain base even in the head-comparison scheme.
        class_id = ALIASES.get(query["text"].strip())
        all_heads[qid] = heads[class_id] if class_id is not None else base[qid]
        threshold = manifest["classes"][class_id]["calibration"] if class_id is not None else None
        selected = set()
        if threshold is not None and threshold["enabled"]:
            column = CLASS_IDS.index(class_id)
            selected = {aid for aid, score in zip(split.ids, scores[:, column], strict=True)
                        if score >= threshold["threshold"]}
        confident[qid] = [aid for aid in all_heads[qid] if aid in selected]
        base_threshold = manifest["classes"][class_id]["cosine_calibration"] if class_id is not None else None
        base_selected = set()
        if base_threshold is not None and base_threshold["enabled"]:
            base_selected = {aid for aid, score in zip(split.ids, base_scores[index], strict=True)
                             if score >= base_threshold["threshold"]}
        base_confident[qid] = [aid for aid in base[qid] if aid in base_selected]
        route_confident[qid] = confident[qid] if routes[qid] == "head" else base_confident[qid]
    return {"rankings": {"cosine": base, "linear_head": all_heads, "category_route": routed},
            "high_confidence": confident, "routes": routes, "head_scan_ms": head_scan_ms,
            "high_confidence_by_scheme": {"cosine": base_confident, "linear_head": confident,
                                          "category_route": route_confident},
            "weight_bytes": weights.nbytes + biases.nbytes,
            "score_semantics": CONFIG["score_meaning"]}


def indexed_query_equivalence(weights: np.ndarray, biases: np.ndarray, split: Split) -> dict:
    """Check learned query directions without changing any evaluated ranking.

    For unit images, w.x+b = ||w|| * cosine(w/||w||, x)+b. A real
    float32 vector index introduces rounding, so report observed rank changes.
    """
    logits = score_heads(split.images, weights, biases)
    results = {}
    for column, query in enumerate(CLASS_IDS):
        norm = float(np.linalg.norm(weights[column]))
        if norm == 0:
            results[query] = {"status": "ZERO_DIRECTION_NOT_REPRESENTABLE_AS_UNIT_QUERY"}
            continue
        direction64 = weights[column] / norm
        direction32 = direction64.astype(np.float32)
        cosine32 = split.images @ direction32
        reconstructed = cosine32.astype(np.float64) * norm + biases[column]
        reference = deterministic_rank(logits[:, column], split.ids)
        indexed = deterministic_rank(cosine32, split.ids)
        exact_identity_error = float(np.max(np.abs(
            (split.images.astype(np.float64) @ direction64) * norm + biases[column] - logits[:, column])))
        results[query] = {
            "status": "COMPARED", "weight_norm": norm,
            "float64_algebra_max_absolute_error": exact_identity_error,
            "float32_query_max_absolute_component_error": float(np.max(np.abs(direction64 - direction32))),
            "float32_reconstructed_logit_max_absolute_error": float(np.max(np.abs(reconstructed - logits[:, column]))),
            "same_complete_order": indexed == reference,
            "changed_rank_positions": sum(left != right for left, right in zip(indexed, reference, strict=True)),
            "top20_same_order": indexed[:20] == reference[:20],
            "top50_same_order": indexed[:50] == reference[:50],
            "top50_same_members": set(indexed[:50]) == set(reference[:50]),
            "float32_index_ranking": indexed,
        }
    return {"method": "CPU exact dot products using float32 unit query; no database/ANN invocation",
            "classes": results,
            "limitations": ["Small heldout vectors establish numerical comparison only, not pagination/filter/index contracts.",
                            "Zero learned directions require fallback; normalized query cannot represent a zero vector."]}


def confidence_metrics(selected: list, labels: dict) -> dict:
    known = [aid for aid in selected if labels[aid] is not None]
    positives = sum(labels[aid] is True for aid in known)
    total_positives = sum(value is True for value in labels.values())
    total_negatives = sum(value is False for value in labels.values())
    return {"selected": len(selected), "known_positive": positives,
            "known_negative": len(known) - positives, "unknown": len(selected) - len(known),
            "coverage": len(selected) / len(labels) if labels else 0.0,
            "judged_precision": positives / len(known) if known else None,
            "full_precision": positives / len(selected) if known and len(known) == len(selected) else None,
            "known_positive_recall": positives / total_positives if total_positives else None,
            "known_negative_population": total_negatives,
            "false_positive_rate_on_known_negatives": (len(known) - positives) / total_negatives if total_negatives else None,
            "no_hit_gallery_diagnostic": {
                "scope": "same heldout known-negative subset; not an independently collected absent-query benchmark",
                "population": total_negatives, "false_confident_results": len(known) - positives}}


def evaluate_predictions(prediction: dict, split: Split, labels: dict, boxes: dict | None = None) -> dict:
    groups = {"all": split.ids,
              "random": [row["asset_id"] for row in split.assets if row["selection_stratum"] == "random"],
              "enriched": [row["asset_id"] for row in split.assets if row["selection_stratum"] != "random"]}
    results = {}
    for name, ids in groups.items():
        if not ids:
            continue
        members = set(ids)
        by_query = {qid: {aid: values[aid] for aid in ids} for qid, values in labels.items()}
        results[name] = {
            "ranking_metrics": {scheme: {qid: judged_metrics([aid for aid in ranks if aid in members],
                                                               by_query[qid])
                                         for qid, ranks in queries.items()}
                                for scheme, queries in prediction["rankings"].items()},
            "confidence_metrics": {qid: confidence_metrics([aid for aid in ids_ if aid in members], by_query[qid])
                                   for qid, ids_ in prediction["high_confidence"].items()}}
        if "high_confidence_by_scheme" in prediction:
            results[name]["confidence_metrics_by_scheme"] = {
                scheme: {qid: confidence_metrics([aid for aid in selected if aid in members], by_query[qid])
                         for qid, selected in queries.items()}
                for scheme, queries in prediction["high_confidence_by_scheme"].items()}
    if boxes is not None:
        observations = {}
        for qid, query_labels in labels.items():
            small = [aid for aid in split.ids if query_labels[aid] is True and
                     0 < boxes.get(str(aid), {}).get(qid, {}).get("max_box_area_fraction", math.inf) <= 0.05]
            observations[qid] = {"count": len(small), "ids": small, "schemes": {}}
            for scheme, queries in prediction["rankings"].items():
                positions = {aid: i for i, aid in enumerate(queries[qid], 1)}
                ranks = [positions[aid] for aid in small]
                observations[qid]["schemes"][scheme] = {
                    "recall50": sum(rank <= 50 for rank in ranks) / len(ranks) if ranks else None,
                    "rank_median": float(np.median(ranks)) if ranks else None}
        results["small_subject_diagnostics"] = observations
    return results


def load_boxes(reader: Reader, path: Path, labels_path: Path, split: Split) -> dict:
    document, labels_doc = reader.json(path), reader.json(labels_path)
    labels = load_labels(reader, labels_path, split, training=False)
    identities = [{key: row[key] for key in ("asset_id", "sha256")} for row in split.assets]
    expected_source = labels_doc.get("provenance", {}).get("source_identity")
    if (document.get("input_identities") != identities or document.get("query_definitions") != split.queries
            or not expected_source or document.get("source_identity") != expected_source
            or not set(document.get("assets", {})) <= {str(aid) for aid in split.ids}):
        raise ValueError("evaluation boxes belong to different image/query/source identities")
    for asset in document["assets"].values():
        if not isinstance(asset, dict) or not set(asset) <= set(CLASS_IDS):
            raise ValueError("unknown box category")
        for value in asset.values():
            area = value.get("max_box_area_fraction")
            if type(area) not in (int, float) or not math.isfinite(area) or not 0 < area <= 1:
                raise ValueError("invalid ground truth box area")
    for aid in split.ids:
        per_asset = document["assets"].get(str(aid), {})
        for query in CLASS_IDS:
            value = labels.get(query, {}).get(aid)
            if value is True and query not in per_asset:
                raise ValueError("positive category lacks evaluation box evidence")
            if value is False and query in per_asset:
                raise ValueError("negative category contradicts evaluation box evidence")
    return document["assets"]


def benchmark_scoring(manifest: dict, weights: np.ndarray, biases: np.ndarray, matrix_path: Path) -> dict:
    """Shape/cost only. Synthetic Qwen capacity rows do not establish FG quality."""
    matrix = np.load(matrix_path, allow_pickle=False, mmap_mode="r")
    dimension = manifest["space_spec"]["dimension"]
    if (matrix.ndim != 2 or matrix.shape[0] != 300000 or matrix.dtype != np.float32
            or matrix.shape[1] < dimension):
        raise ValueError("capacity benchmark requires exactly 300000 float32 rows and sufficient width")
    def matrix_digest():
        digest = hashlib.sha256()
        with matrix_path.open("rb") as handle:
            while chunk := handle.read(8 * 1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    digest = matrix_digest()
    samples = {"one_category_ms": [], "all_eight_ms": [], "one_category_full_sort_ms": []}
    checksums = {}
    # Keep first-use samples, do not promise cold I/O after the integrity read.
    for repeat in range(3):
        for count, key in ((1, "one_category_ms"), (8, "all_eight_ms")):
            started = time.perf_counter()
            scores = score_heads(matrix[:, :dimension], weights[:count], biases[:count])
            samples[key].append((time.perf_counter() - started) * 1000)
            checksum = sha(scores.tobytes())
            if count in checksums and checksums[count] != checksum:
                raise ValueError("repeated CPU scoring changed")
            checksums[count] = checksum
            if count == 1:
                started = time.perf_counter()
                np.lexsort((np.arange(len(matrix)), -scores[:, 0]))
                samples["one_category_full_sort_ms"].append((time.perf_counter() - started) * 1000)
    if matrix_digest() != digest:
        raise ValueError("capacity input changed while measuring")
    return {"status": "COMPLETE", "schema": "album-head-scoring-cost-v1", "model_identity": manifest["identity"],
            "study_kind": manifest.get("study_kind", "PREDECLARED_PRIMARY_DIMENSION"),
            "matrix_sha256": digest, "matrix_shape": list(matrix.shape),
            "scored_shape": [300000, dimension], "samples": samples,
            "median_ms": {key: float(np.median(values)) for key, values in samples.items()},
            "score_checksums": checksums, "weight_bytes": weights.nbytes + biases.nbytes,
            "float64_conversion_block_rows": 8192,
            "limitations": ["Synthetic matrix shape/cost only; not image relevance or 1660 performance.",
                            "Three observations on current CPU; integrity read warms filesystem cache.",
                            "No API, filtering, pagination, concurrent indexing, or persistent per-class score cache."]}


def read_model(reader: Reader, directory: Path) -> tuple[dict, np.ndarray, np.ndarray]:
    manifest = reader.json(directory / "model.json")
    if (manifest.get("schema") != "album-linear-heads-v1" or manifest.get("status") != "FROZEN"
            or manifest.get("config") != CONFIG or manifest.get("query_definitions") != CLASS_QUERIES
            or set(manifest.get("classes", {})) != set(CLASS_IDS)):
        raise ValueError("model does not match frozen configuration")
    for category in manifest["classes"].values():
        for field in ("calibration", "cosine_calibration"):
            value = category[field]
            if type(value.get("enabled")) is not bool:
                raise ValueError("invalid calibration enabled state")
            if value["enabled"]:
                if (type(value.get("threshold")) not in (int, float) or not math.isfinite(value["threshold"])
                        or type(value.get("selected")) is not int
                        or value["selected"] < CONFIG["minimum_calibration_predictions"]
                        or not CONFIG["target_calibration_precision"] <= value.get("precision", -1) <= 1):
                    raise ValueError("invalid enabled calibration threshold")
            elif value.get("threshold") is not None or value.get("selected") != 0:
                raise ValueError("disabled category must abstain")
    raw = reader.raw(directory / "weights.npz")
    if sha(raw) != manifest["weights_sha256"]:
        raise ValueError("weight checksum mismatch")
    identity_spec = {key: value for key, value in manifest.items() if key != "identity"}
    if sha(json.dumps(identity_spec, sort_keys=True, ensure_ascii=False).encode()) != manifest.get("identity"):
        raise ValueError("model manifest identity mismatch")
    with np.load(io.BytesIO(raw), allow_pickle=False) as arrays:
        if set(arrays.files) != {"weights", "biases"}:
            raise ValueError("unexpected weight archive entries")
        weights, biases = arrays["weights"], arrays["biases"]
    return manifest, weights, biases


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fit = commands.add_parser("fit")
    for prefix in ("train", "cal"):
        for kind in ("run", "sample", "labels"):
            fit.add_argument(f"--{prefix}-{kind}", type=Path, required=True)
    fit.add_argument("--dimension", type=int, choices=(512, 768, 1024), required=True)
    fit.add_argument("--out-dir", type=Path, required=True)
    evaluate = commands.add_parser("evaluate")
    for field in ("model-dir", "run", "sample", "labels", "out"):
        evaluate.add_argument(f"--{field}", type=Path, required=True)
    evaluate.add_argument("--boxes", type=Path)
    evaluate.add_argument("--scope", choices=("test", "exploratory"), required=True)
    benchmark = commands.add_parser("benchmark")
    for field in ("model-dir", "matrix", "out"):
        benchmark.add_argument(f"--{field}", type=Path, required=True)
    for command in (fit, evaluate, benchmark):
        command.add_argument("--junit", type=Path, required=True)
    args = parser.parse_args()
    junit = controlled_output(args.junit)
    if junit.exists():
        raise ValueError("fresh JUnit path required")
    failure, started = None, time.perf_counter()
    reader = Reader()
    try:
        if any(os.environ.get(key) != "1" for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")):
            raise ValueError("set OPENBLAS_NUM_THREADS=OMP_NUM_THREADS=MKL_NUM_THREADS=1 before launching Python")
        for filename in ("album_linear_probe.py", "budget_search_metrics.py", "wemm_pilot_core.py",
                         "prepare_wemm_coco.py"):
            reader.raw(Path(__file__).with_name(filename))
        if args.command == "fit":
            destination = controlled_output(args.out_dir)
            if destination.exists():
                raise ValueError("fresh model directory required")
            train = load_split(reader, args.train_run, args.train_sample, args.dimension, "train")
            calibration = load_split(reader, args.cal_run, args.cal_sample, args.dimension, "calibration")
            if len(train.assets) != 800 or len(calibration.assets) != 320:
                raise ValueError("frozen experiment requires train800 and calibration320; no implicit role swap")
            train_labels = load_labels(reader, args.train_labels, train, training=True)
            cal_labels = load_labels(reader, args.cal_labels, calibration, training=True)
            manifest, weights, biases = fit_heads(train, calibration, train_labels, cal_labels)
            manifest["source_hashes"] = reader.unchanged()
            import scipy
            manifest["solver_environment"] = {"scipy": scipy.__version__, "numpy": np.__version__, "blas_threads": 1}
            destination.mkdir(parents=True)
            np.savez(destination / "weights.npz", weights=weights, biases=biases)
            manifest["weights_sha256"] = sha((destination / "weights.npz").read_bytes())
            manifest["identity"] = sha(json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode())
            write_json(destination / "model.json", manifest)
            print(json.dumps({"status": "FROZEN", "classes": manifest["classes"]}, ensure_ascii=False))
        elif args.command == "evaluate":
            out = controlled_output(args.out)
            if out.exists():
                raise ValueError("fresh evaluation path required")
            manifest, weights, biases = read_model(reader, args.model_dir)
            split = load_split(reader, args.run, args.sample, manifest["space_spec"]["dimension"], args.scope)
            if args.scope == "test" and len(split.assets) != 480:
                raise ValueError("frozen heldout experiment requires exactly test480")
            predictions = predict(manifest, weights, biases, split)
            # Evaluation labels/boxes are read only after every prediction is frozen.
            labels = load_labels(reader, args.labels, split, training=args.scope == "test")
            boxes = load_boxes(reader, args.boxes, args.labels, split) if args.boxes else None
            result = {"schema": "album-linear-evaluation-v1", "status": "COMPLETE", "scope": args.scope,
                      "model_identity": manifest["identity"], "split": split.provenance,
                      "study_kind": manifest.get("study_kind", "PREDECLARED_PRIMARY_DIMENSION"),
                      **predictions, "metrics": evaluate_predictions(predictions, split, labels, boxes),
                      "indexed_query_equivalence": indexed_query_equivalence(weights, biases, split),
                      "source_hashes": reader.unchanged(), "config": CONFIG,
                      "limitations": manifest["limitations"] + [
                          "No probability calibration or private-gallery precision guarantee.",
                          "COCO category labels are not exhaustive personal-album relevance judgments."]}
            write_json(out, result)
            summary = {name: {scheme: (float(np.mean(values)) if values else None)
                              for scheme, per_query in group["ranking_metrics"].items()
                              for values in [[v["AP"] for v in per_query.values() if v["AP"] is not None]]}
                       for name, group in result["metrics"].items() if "ranking_metrics" in group}
            print(json.dumps({"scope": args.scope, "mean_AP": summary, "routes": predictions["routes"]}))
        else:
            out = controlled_output(args.out)
            if out.exists():
                raise ValueError("fresh benchmark path required")
            manifest, weights, biases = read_model(reader, args.model_dir)
            result = benchmark_scoring(manifest, weights, biases, args.matrix)
            result["source_hashes"] = reader.unchanged()
            write_json(out, result)
            print(json.dumps(result["median_ms"]))
    except BaseException as exc:
        failure = exc
        raise
    finally:
        suite = ET.Element("testsuite", name="album_linear_probe", tests="1", errors="0", skipped="0",
                           failures=str(int(failure is not None)), time=str(time.perf_counter() - started))
        case = ET.SubElement(suite, "testcase", name=args.command)
        if failure is not None:
            ET.SubElement(case, "failure", message=str(failure))
        junit.parent.mkdir(parents=True, exist_ok=True)
        ET.ElementTree(suite).write(junit, encoding="utf-8", xml_declaration=True)


if __name__ == "__main__":
    main()
