"""CPU-only, partial-label-aware evaluation of the frozen 6GB search pilot.

All candidate generation and fusion happen before relevance labels are read.
Judged-only metrics describe the corresponding condensed, fully judged subset,
never precision in the complete gallery or a 300k personal library.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import numpy as np

from tools.evaluation.prepare_wemm_coco import controlled_output, write_json
from tools.evaluation.wemm_pilot_core import cache_identity, deterministic_rank, normalize_prefix

DETECTION_THRESHOLD = 0.5
FUSION_TOP_K = 100
CUTOFFS = (20, 50, 100)


def validate_ranking(ranking: list[str], ids: list[str]) -> None:
    if not ids or len(ids) != len(set(ids)) or len(ranking) != len(ids) or set(ranking) != set(ids):
        raise ValueError("ranking must contain exactly the complete unique frozen population")


def judged_metrics(ranking: list[str], labels: dict[str, bool | None]) -> dict[str, Any]:
    """Keep unknowns visible, then explicitly evaluate a separate judged subset."""
    validate_ranking(ranking, list(labels))
    if any(value is not None and type(value) is not bool for value in labels.values()):
        raise ValueError("labels must be true, false or unknown")
    judged = [asset_id for asset_id in ranking if labels[asset_id] is not None]
    positives = sum(value is True for value in labels.values())
    ranks = [rank for rank, asset_id in enumerate(judged, 1) if labels[asset_id] is True]
    full_ranks = [rank for rank, asset_id in enumerate(ranking, 1) if labels[asset_id] is True]
    output: dict[str, Any] = {
        "scope": "CONDENSED_JUDGED_SUBSET_ONLY",
        "full_population": len(ranking), "judged_population": len(judged),
        "unknown_population": len(ranking) - len(judged),
        "judged_fraction": len(judged) / len(ranking), "known_positives": positives,
        "AP": sum(index / rank for index, rank in enumerate(ranks, 1)) / positives if positives else None,
        "judged_ranking": judged,
        "positive_full_rank_p50": float(np.percentile(full_ranks, 50)) if positives else None,
        "positive_full_rank_p90": float(np.percentile(full_ranks, 90)) if positives else None,
        "positive_full_rank_max": max(full_ranks) if positives else None,
        "full_gallery": {},
    }
    for k in CUTOFFS:
        hits = sum(rank <= k for rank in ranks)
        output[f"P{k}"] = hits / k if len(judged) >= k else None
        output[f"Recall{k}"] = hits / positives if positives else None
        output[f"judged_available{k}"] = min(k, len(judged))
        head = ranking[:k]
        known = sum(labels[asset_id] is not None for asset_id in head)
        full_hits = sum(labels[asset_id] is True for asset_id in head)
        output["full_gallery"][str(k)] = {
            "available": len(head), "known_positive": full_hits,
            "known_negative": known - full_hits, "unknown": len(head) - known,
            "judged_fraction_of_available": known / len(head),
            "precision": full_hits / k if len(head) == k and known == k else None,
            "known_positive_recall": full_hits / positives if positives else None,
        }
    return output


def stable_detection_partition(ranking: list[str], detector_scores: dict[str, float],
                               top_k: int | None = None) -> list[str]:
    """Prioritize >=0.5 detections within a fixed candidate range, without deletions."""
    validate_ranking(ranking, list(detector_scores))
    if top_k is not None and (type(top_k) is not int or top_k < 1):
        raise ValueError("fusion candidate bound must be a positive integer")
    if any(not math.isfinite(score) or not 0 <= score <= 1 for score in detector_scores.values()):
        raise ValueError("detector scores must be finite probabilities")
    bound = len(ranking) if top_k is None else min(top_k, len(ranking))
    selected = ranking[:bound]
    positive = [asset_id for asset_id in selected if detector_scores[asset_id] >= DETECTION_THRESHOLD]
    remaining = [asset_id for asset_id in selected if detector_scores[asset_id] < DETECTION_THRESHOLD]
    return positive + remaining + ranking[bound:]


def box_rank_observations(ranking: list[str], labels: dict[str, bool | None],
                          boxes: dict[str, Any], query: str) -> dict[str, Any]:
    """Ground-truth boxes influence reporting only, never model input or ordering."""
    groups: dict[str, list[dict[str, Any]]] = {"small_le_5pct": [], "larger": [],
                                             "box_unknown": [], "occluded": [], "depiction": []}
    for rank, asset_id in enumerate(ranking, 1):
        if labels[asset_id] is not True:
            continue
        value = boxes.get(asset_id, {}).get(query)
        item = {"asset_id": asset_id, "full_rank": rank}
        if value is None:
            groups["box_unknown"].append(item)
            continue
        area = value["max_box_area_fraction"]
        if not isinstance(area, (float, int)) or not math.isfinite(area) or not 0 < area <= 1:
            raise ValueError("invalid evaluation-only box area")
        item["max_box_area_fraction"] = area
        groups["small_le_5pct" if area <= 0.05 else "larger"].append(item)
        if value["has_occluded"]:
            groups["occluded"].append(item)
        if value["has_depiction"]:
            groups["depiction"].append(item)
    return {name: {"count": len(items), "items": items,
                   "rank_p50": float(np.percentile([x["full_rank"] for x in items], 50)) if items else None,
                   "known_positive_recall100": sum(x["full_rank"] <= 100 for x in items) / len(items)
                   if items else None} for name, items in groups.items()}


def verified_box_evidence(document: dict[str, Any], labels_doc: dict[str, Any],
                          identities: list[dict[str, str]]) -> dict[str, Any]:
    expected = labels_doc.get("provenance", {}).get("source_identity", {}).get("boxes.csv", {}).get("sha256")
    if not expected or document.get("source_sha256") != expected:
        raise ValueError("evaluation boxes have different official source identity")
    assets = document.get("assets")
    if not isinstance(assets, dict) or not set(assets) <= {row["asset_id"] for row in identities}:
        raise ValueError("evaluation boxes contain IDs outside the frozen sample")
    if any(not isinstance(value, dict) or not set(value) <= {"cat", "dog"} for value in assets.values()):
        raise ValueError("evaluation boxes contain unknown category keys")
    return assets


def evaluate_rankings(rankings: dict[str, dict[str, list[str]]], labels_doc: dict[str, Any],
                      identities: list[dict[str, str]], queries: list[dict[str, str]],
                      boxes: dict[str, Any] | None = None) -> dict[str, Any]:
    if labels_doc["input_identities"] != identities or labels_doc["query_definitions"] != queries:
        raise ValueError("labels belong to different image bytes or queries")
    ids = [row["asset_id"] for row in identities]
    qids = [row["query_id"] for row in queries]
    if len(qids) != len(set(qids)) or not qids or not rankings:
        raise ValueError("empty or duplicate evaluation schemes/queries")
    label_maps = {}
    for query in qids:
        rows = labels_doc["queries"][query]
        validate_ranking([row["asset_id"] for row in rows], ids)
        label_maps[query] = {row["asset_id"]: row["relevant"] for row in rows}
    results = {}
    for scheme, by_query in rankings.items():
        if set(by_query) != set(qids):
            raise ValueError("scheme must cover every frozen query")
        per_query = {}
        for query, ranking in by_query.items():
            validate_ranking(ranking, ids)
            value = judged_metrics(ranking, label_maps[query])
            if boxes is not None:
                value["box_diagnostics"] = box_rank_observations(ranking, label_maps[query], boxes, query)
            per_query[query] = value
        results[scheme] = per_query
    return {"scope": "Exploratory public data; metrics apply separately to each query's judged subset",
            "limitations": labels_doc["provenance"]["limitations"], "results": results,
            "rankings": rankings, "input_identities": identities, "query_definitions": queries}


def vector_rankings(images: np.ndarray, query_vectors: np.ndarray, ids: list[str],
                    qids: list[str], dimensions: list[int]) -> dict[str, dict[str, list[str]]]:
    if (images.ndim != 2 or query_vectors.ndim != 2 or images.shape[0] != len(ids)
            or query_vectors.shape[0] != len(qids) or images.shape[1] != query_vectors.shape[1]
            or len(ids) != len(set(ids)) or len(qids) != len(set(qids))):
        raise ValueError("vector matrices differ from ordered input identity")
    output = {}
    for dimension in dimensions:
        scores = normalize_prefix(query_vectors, dimension) @ normalize_prefix(images, dimension).T
        output[str(dimension)] = {query: deterministic_rank(scores[index], ids)
                                  for index, query in enumerate(qids)}
    return output


def add_detector_schemes(rankings: dict[str, dict[str, list[str]]],
                         detector: dict[str, dict[str, float]], ids: list[str]) -> dict[str, Any]:
    """No relevance label is accepted by this candidate/fusion function."""
    if not rankings or not detector:
        raise ValueError("empty ranking/detection input")
    for query, scores in detector.items():
        validate_ranking(list(scores), ids)
        if any(not math.isfinite(score) or not 0 <= score <= 1 for score in scores.values()):
            raise ValueError("invalid detector probabilities")
    output = {name: dict(by_query) for name, by_query in rankings.items()}
    if "detector_only" in output:
        raise ValueError("duplicate scheme name")
    output["detector_only"] = {
        query: deterministic_rank(np.asarray([scores[asset_id] for asset_id in ids]), ids)
        for query, scores in detector.items()}
    for name, by_query in rankings.items():
        if set(by_query) != set(detector):
            raise ValueError("detector query coverage differs")
        for suffix, bound in (("detector_partition_all", None), ("detector_partition_top100", FUSION_TOP_K)):
            scheme = f"{name}+{suffix}"
            if scheme in output:
                raise ValueError("duplicate scheme name")
            output[scheme] = {query: stable_detection_partition(ranking, detector[query], bound)
                              for query, ranking in by_query.items()}
    return output


def embeddings_from_record(record: dict[str, Any], images: np.ndarray,
                           queries: np.ndarray) -> tuple[dict[str, Any], list[dict[str, str]]]:
    if record.get("schema") != "budget-embedding-run-v1" or record.get("status") != "COMPLETE":
        raise ValueError("incomplete or unknown embedding record")
    model, dimension = record["model"], record["native_dimension"]
    expected = {"qwen": 2048, "siglip-base": 768, "siglip-so400m": 1152, "fgclip-base": 768,
                "fgclip-base-adaptive": 768,
                "embeddinggemma2": 768}
    if model not in expected or dimension != expected[model]:
        raise ValueError("unknown model/dimension; no implicit MRL assumption")
    if not record.get("identity") or not record.get("model_lock") or not record.get("sample_sha256"):
        raise ValueError("missing embedding provenance")
    identities = [{"asset_id": row["asset_id"], "sha256": row["sha256"]} for row in record["assets"]]
    if cache_identity(
        {key: record[key] for key in ("model", "dtype", "settings", "model_lock", "packages",
                                      "decoder", "source_sha256", "native_dimension")},
        identities, record["queries"],
    ) != record["identity"]:
        raise ValueError("embedding cache identity differs from complete execution specification")
    for matrix, count in ((images, len(identities)), (queries, len(record["queries"]))):
        if (matrix.shape != (count, dimension) or matrix.dtype != np.float32
                or not np.isfinite(matrix).all()
                or not np.allclose(np.linalg.norm(matrix.astype(np.float64), axis=1), 1, atol=1e-5, rtol=0)):
            raise ValueError("invalid native embedding shape/dtype/finiteness/unit norm")
    dimensions = {"qwen": [512, 1024, 2048], "embeddinggemma2": [256, 512, 768]}.get(model, [dimension])
    values = vector_rankings(images, queries, [row["asset_id"] for row in identities],
                             [row["query_id"] for row in record["queries"]], dimensions)
    return {f"{model}-{record['dtype']}-{dim}": by_query for dim, by_query in values.items()}, identities


def detector_from_record(record: dict[str, Any], identities: list[dict[str, str]],
                         sample_sha: str, full_assets: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    if (record.get("schema") != "budget-object-probe-v1" or record.get("status") != "COMPLETE"
            or not record.get("cache_identity") or not record.get("spec")
            or record.get("inputs") != identities or record.get("sample_sha256") != sample_sha):
        raise ValueError("detector record incomplete or input identity differs")
    if [{"asset_id": row["asset_id"], "sha256": row["sha256"]} for row in full_assets] != identities:
        raise ValueError("full detector sample does not match input identities")
    if cache_identity({**record["spec"], "sample_sha256": sample_sha}, full_assets,
                      [{"operation": "fixed-coco80-object-detection"}]) != record["cache_identity"]:
        raise ValueError("detector cache identity differs from complete specification/input records")
    if [{"asset_id": row["asset_id"], "sha256": row["sha256"]} for row in record["images"]] != identities:
        raise ValueError("detector image record order/bytes differ")
    return {query: {row["asset_id"]: row["class_scores"][query] for row in record["images"]}
            for query in ("cat", "dog")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rankings", type=Path,
                        help="JSON with rankings, input_identities and query_definitions")
    parser.add_argument("--embedding-dir", type=Path, action="append", default=[])
    parser.add_argument("--detector", type=Path, help="Optional object-probe result.json")
    parser.add_argument("--sample", type=Path, help="Frozen sample manifest, required with embedding runs")
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--boxes", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--junit", type=Path, required=True)
    args = parser.parse_args()
    if bool(args.rankings) == bool(args.embedding_dir) or (args.detector and not args.embedding_dir):
        parser.error("provide either --rankings or --embedding-dir; detector requires embedding directories")
    if args.embedding_dir and not args.sample:
        parser.error("embedding evaluation requires --sample to verify the complete manifest identity")
    out, junit = controlled_output(args.out), controlled_output(args.junit)
    started = time.perf_counter()
    failure = None
    hashes = {}
    try:
        def read(path: Path) -> Any:
            raw = path.read_bytes()
            hashes[str(path.resolve())] = hashlib.sha256(raw).hexdigest()
            return json.loads(raw)

        if args.rankings:
            candidates = read(args.rankings)
        else:
            candidates = {"rankings": {}, "provenance": {}}
            sample = read(args.sample)
            sample_sha = hashes[str(args.sample.resolve())]
            for directory in args.embedding_dir:
                record = read(directory / "result.json")
                if (record.get("sample_sha256") != sample_sha or record.get("assets") != sample["assets"]
                        or not 1 <= len(sample["assets"]) <= 1000):
                    raise ValueError("embedding result differs from frozen complete sample")
                matrices = []
                for filename in ("images.npy", "queries.npy"):
                    path = directory / filename
                    if path.stat().st_size > 16 * 1024 * 1024:
                        raise ValueError("embedding matrix exceeds bounded pilot file size")
                    raw = path.read_bytes()
                    digest = hashlib.sha256(raw).hexdigest()
                    if digest != record["vectors_sha256"][filename]:
                        raise ValueError("embedding matrix checksum differs")
                    hashes[str(path.resolve())] = digest
                    matrices.append(np.load(io.BytesIO(raw), allow_pickle=False))
                schemes, identities = embeddings_from_record(record, *matrices)
                if "input_identities" not in candidates:
                    candidates.update(input_identities=identities, query_definitions=record["queries"])
                elif (sample_sha != record["sample_sha256"] or identities != candidates["input_identities"]
                      or record["queries"] != candidates["query_definitions"]):
                    raise ValueError("embedding runs have different source sample or query identities")
                if set(schemes) & set(candidates["rankings"]):
                    raise ValueError("duplicate embedding scheme")
                candidates["rankings"].update(schemes)
                candidates["provenance"][str(directory)] = {
                    "identity": record["identity"], "model_lock": record["model_lock"],
                    "source_sha256": record.get("source_sha256"), "sample_sha256": sample_sha}
            if args.detector:
                detector_record = read(args.detector)
                detector = detector_from_record(detector_record, candidates["input_identities"], sample_sha,
                                                sample["assets"])
                candidates["rankings"] = add_detector_schemes(
                    candidates["rankings"], detector, [r["asset_id"] for r in candidates["input_identities"]])
                candidates["provenance"]["detector"] = {
                    "cache_identity": detector_record["cache_identity"], "spec": detector_record["spec"],
                    "fusion": {"score_threshold": DETECTION_THRESHOLD, "top_k": FUSION_TOP_K,
                               "method": "stable positive-first partition; no deletions"}}
        labels = read(args.labels)
        boxes = verified_box_evidence(read(args.boxes), labels, candidates["input_identities"]) if args.boxes else None
        result = evaluate_rankings(candidates["rankings"], labels, candidates["input_identities"],
                                   candidates["query_definitions"], boxes)
        for path, expected in hashes.items():
            if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
                raise ValueError("evaluation input changed during read")
        result.update(status="COMPLETE", source_hashes=hashes,
                      candidate_provenance=candidates.get("provenance", {}))
        write_json(out, result)
        print(json.dumps({"status": "COMPLETE", "schemes": len(result["rankings"])}))
    except BaseException as exc:
        failure = exc
        raise
    finally:
        suite = ET.Element("testsuite", name="budget_search_metrics", tests="1",
                           failures=str(int(failure is not None)), errors="0", skipped="0",
                           time=str(time.perf_counter() - started))
        case = ET.SubElement(suite, "testcase", name="identity_and_partial_label_evaluation")
        if failure is not None:
            ET.SubElement(case, "failure", message=str(failure))
        junit.parent.mkdir(parents=True, exist_ok=True)
        ET.ElementTree(suite).write(junit, encoding="utf-8", xml_declaration=True)


if __name__ == "__main__":
    main()
