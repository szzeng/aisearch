"""Pure, bounded evaluation helpers; no model, database, network or file access.

Unlabelled rank changes are observations, never evidence of precision or recall.
All model rankings must describe exactly the same frozen assets and queries.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import random
import re
from collections.abc import Mapping, Sequence
from numbers import Integral
from typing import Any

import numpy as np

AssetId = int | str
MAX_ASSETS = 1000


def _positive_integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return int(value)


def _asset_ids(values: Sequence[AssetId]) -> list[AssetId]:
    ids = list(values)
    if not ids:
        raise ValueError("asset IDs must not be empty")
    if any(type(value) not in (int, str) or value == "" for value in ids):
        raise ValueError("asset IDs must be integers or nonempty strings")
    if any(type(value) is not type(ids[0]) for value in ids):
        raise ValueError("asset IDs must have one consistent type")
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate asset ID")
    return ids


def normalize_prefix(vectors: Any, dimension: int) -> np.ndarray:
    """Take a floating-point matrix prefix, then L2-normalize to float32.

    Validate the entire source, including the discarded suffix. Scale before the
    norm so finite very small/large inputs do not underflow/overflow the norm.
    The input is never mutated; an all-zero prefix is always rejected.
    """
    dimension = _positive_integer(dimension, "dimension")
    matrix = np.asarray(vectors)
    if matrix.ndim != 2 or not matrix.shape[0] or not matrix.shape[1]:
        raise ValueError("vectors must be a nonempty two-dimensional matrix")
    if matrix.dtype.kind != "f" or not np.isfinite(matrix).all():
        raise ValueError("vectors must contain finite floating-point values")
    if dimension > matrix.shape[1]:
        raise ValueError("dimension exceeds the source vector width")
    prefix = matrix[:, :dimension].astype(np.float64)
    scale = np.max(np.abs(prefix), axis=1, keepdims=True)
    if np.any(scale == 0):
        raise ValueError("zero vector in the selected prefix")
    scaled = prefix / scale
    return (scaled / np.linalg.norm(scaled, axis=1, keepdims=True)).astype(np.float32)


def deterministic_rank(scores: Any, asset_ids: Sequence[AssetId]) -> list[AssetId]:
    """Rank by descending finite score, then ascending asset ID (numeric for ints)."""
    ids = _asset_ids(asset_ids)
    values = np.asarray(scores)
    if values.ndim != 1 or len(values) != len(ids):
        raise ValueError("one score is required per asset")
    if values.dtype.kind not in "fiu" or not np.isfinite(values).all():
        raise ValueError("scores must be finite real numbers")
    return [ids[index] for index in sorted(
        range(len(ids)), key=lambda i: (-values[i].item(), ids[i]),
    )]


def select_sample(
    rows: Sequence[Mapping[str, Any]],
    max_assets: int,
    seed: int,
    problem_ids: Sequence[AssetId] | Mapping[str, Sequence[AssetId]] = (),
) -> list[dict[str, Any]]:
    """Freeze a SHA-deduplicated sample, at least half uniformly random.

    Rows require ``asset_id`` and a 64-character ``sha256``. Duplicate bytes use
    the smallest asset ID as their representative. Random sampling happens first
    over all unique content, independent of problem strata; the remaining slots
    round-robin across shuffled problem strata, then use extra random assets.
    Input order cannot change the result. No status/decoding filter or replacement
    of failed assets happens here: the caller must retain all selected failures.
    """
    max_assets = _positive_integer(max_assets, "max_assets")
    if max_assets > MAX_ASSETS:
        raise ValueError(f"pilot sample is limited to {MAX_ASSETS} assets")
    if isinstance(seed, bool) or not isinstance(seed, Integral):
        raise ValueError("seed must be an integer")
    if not rows:
        return []
    ids = _asset_ids([row["asset_id"] for row in rows])
    by_sha: dict[str, dict[str, Any]] = {}
    id_sha = {}
    for asset_id, row in sorted(zip(ids, rows, strict=True), key=lambda item: item[0]):
        digest = row.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise ValueError("each sample row requires a SHA256 hex digest")
        digest = digest.lower()
        id_sha[asset_id] = digest
        by_sha.setdefault(digest, {**row, "sha256": digest})
    candidates = sorted(by_sha.values(), key=lambda row: row["asset_id"])
    groups = problem_ids if isinstance(problem_ids, Mapping) else {"selected": problem_ids}
    canonical_groups: dict[str, list[AssetId]] = {}
    for name, group in groups.items():
        if not isinstance(name, str) or not name:
            raise ValueError("problem strata need nonempty string names")
        canonical = set()
        for asset_id in group:
            if type(asset_id) is not type(ids[0]) or asset_id not in id_sha:
                raise ValueError("problem asset ID is outside the frozen input population")
            canonical.add(by_sha[id_sha[asset_id]]["asset_id"])
        canonical_groups[name] = sorted(canonical)

    rng = random.Random(int(seed))
    target = min(max_assets, len(candidates))
    chosen = rng.sample(candidates, (target + 1) // 2)
    output = [{**row, "selection_stratum": "random"} for row in chosen]
    selected_ids = {row["asset_id"] for row in chosen}
    by_id = {row["asset_id"]: row for row in candidates}
    pools = {}
    for name in sorted(canonical_groups):
        pool = [asset_id for asset_id in canonical_groups[name] if asset_id not in selected_ids]
        rng.shuffle(pool)
        pools[name] = pool
    while len(output) < target and any(pools.values()):
        for name, pool in pools.items():
            while pool and pool[-1] in selected_ids:
                pool.pop()
            if pool and len(output) < target:
                asset_id = pool.pop()
                selected_ids.add(asset_id)
                output.append({**by_id[asset_id], "selection_stratum": f"problem:{name}"})
    remaining = [row for row in candidates if row["asset_id"] not in selected_ids]
    for row in rng.sample(remaining, target - len(output)):
        output.append({**row, "selection_stratum": "random"})
    return output


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("cache identity mapping keys must be strings")
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if value is None or type(value) in (str, bool, int, float):
        return value
    raise ValueError("cache identity requires JSON values, not implicit string conversions")


def cache_identity(
    model_spec: Mapping[str, Any],
    input_identities: Sequence[Mapping[str, Any]],
    queries: Sequence[str | Mapping[str, Any]],
) -> str:
    """Hash the complete model spec and ordered input/query identities.

    Mapping order is irrelevant; row/query order is significant because it maps
    cached matrix rows. The caller must supply actual weight/code revisions,
    processor settings and dtype in model_spec, and source byte hashes in inputs.
    No identity is inferred from a path, mtime or filename.
    """
    if not model_spec or not input_identities or not queries:
        raise ValueError("cache identity needs model, input and query identities")
    payload = _json_value({
        "schema": "wemm-pilot-cache-v1",
        "model_spec": model_spec,
        "input_identities": list(input_identities),
        "queries": list(queries),
    })
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def compare_rankings(
    rankings: Mapping[str, Mapping[str, Sequence[AssetId]]],
    ks: Sequence[int] = (20, 50),
) -> dict[str, Any]:
    """Compare complete rankings over one frozen population, without relevance labels.

    ``rankings[model_name][query_id]`` contains every asset exactly once. Positive
    ``right_rank_minus_left`` means an asset moved down in the right-hand model.
    Top-K overlap divides by available K and explicitly reports a short gallery.
    Scores from different models are deliberately never compared here.
    """
    if len(rankings) < 2:
        raise ValueError("at least two model rankings are required")
    cutoff = sorted({_positive_integer(k, "k") for k in ks})
    if not cutoff:
        raise ValueError("at least one rank cutoff is required")
    models = sorted(rankings)
    query_ids = sorted(rankings[models[0]])
    if not query_ids:
        raise ValueError("rankings must contain queries")
    population = None
    canonical = {}
    for model in models:
        if set(rankings[model]) != set(query_ids):
            raise ValueError("all models must contain the same frozen queries")
        canonical[model] = {}
        for query_id in query_ids:
            ids = _asset_ids(rankings[model][query_id])
            current = set(ids)
            if population is None:
                population = current
            if current != population:
                raise ValueError("all rankings must contain the same frozen assets")
            canonical[model][query_id] = ids
    pairwise = []
    for left, right in itertools.combinations(models, 2):
        comparison = {"left": left, "right": right, "queries": {}}
        for query_id in query_ids:
            lhs, rhs = canonical[left][query_id], canonical[right][query_id]
            left_pos = {asset_id: i for i, asset_id in enumerate(lhs, 1)}
            right_pos = {asset_id: i for i, asset_id in enumerate(rhs, 1)}
            shifts = [{"asset_id": asset_id, "left_rank": left_pos[asset_id],
                       "right_rank": right_pos[asset_id],
                       "right_rank_minus_left": right_pos[asset_id] - left_pos[asset_id]}
                      for asset_id in sorted(left_pos)]
            topk = {}
            for k in cutoff:
                available = min(k, len(lhs))
                overlap = len(set(lhs[:k]) & set(rhs[:k]))
                topk[str(k)] = {"requested_k": k, "available_k": available,
                                "overlap_count": overlap,
                                "overlap_fraction": overlap / available}
            comparison["queries"][query_id] = {"topk": topk, "rank_shifts": shifts}
        pairwise.append(comparison)
    return {"status": "UNVERIFIED / RELATIVE RANKING ONLY", "models": models,
            "queries": query_ids, "asset_count": len(population), "pairwise": pairwise}


def precision_at_k(
    ranking: Sequence[AssetId], labels: Mapping[AssetId, bool | None], k: int,
) -> dict[str, Any]:
    """Emit precision only when every one of the requested Top-K is judged.

    True means relevant, False irrelevant; None or an absent entry is unknown.
    Partial labels report coverage only. This function makes no recall claim and
    cannot certify label provenance: human and weak labels must be kept separate.
    """
    k = _positive_integer(k, "k")
    ids = _asset_ids(ranking)
    if any(value is not None and type(value) is not bool for value in labels.values()):
        raise ValueError("labels must be True, False or None, never inferred relevance")
    selected = ids[:k]
    known = [asset_id for asset_id in selected if labels.get(asset_id) is not None]
    result = {"requested_k": k, "available_k": len(selected), "judged_count": len(known),
              "judgment_coverage": len(known) / k,
              "status": "INCOMPLETE_JUDGMENTS"}
    if len(selected) < k:
        result["status"] = "INSUFFICIENT_CANDIDATES"
    elif len(known) == k:
        result["status"] = "COMPLETE_JUDGMENTS"
        result["precision"] = sum(labels[asset_id] is True for asset_id in selected) / k
    return result


def annotated_metrics(
    ranking: Sequence[AssetId], labels: Mapping[AssetId, bool | None],
    ks: Sequence[int] = (20, 50), *, strata: Mapping[AssetId, str] | None = None,
) -> dict[str, Any]:
    """Recall/AP only for a fully judged frozen population, within annotation scope.

    An exhaustive public category annotation can define this scope. It does not
    certify all real-world relevance or recall in a different personal library.
    Partial/unknown labels suppress recall and AP entirely. Strata preserve the
    same relative ranking rather than extrapolating to the source population.
    """
    ids = _asset_ids(ranking)
    cutoffs = [_positive_integer(k, "k") for k in ks]
    precision_at_k(ids, labels, 1)  # validate the label domain, including unused entries
    judged = sum(labels.get(asset_id) is not None for asset_id in ids)
    if judged != len(ids):
        return {"status": "INCOMPLETE_POPULATION_JUDGMENTS", "asset_count": len(ids),
                "judged_count": judged}
    positives = sum(labels[asset_id] is True for asset_id in ids)
    hits, sum_precision = 0, 0.0
    for position, asset_id in enumerate(ids, 1):
        if labels[asset_id]:
            hits += 1
            sum_precision += hits / position
    result = {"status": "COMPLETE_ANNOTATED_POPULATION", "scope": "ANNOTATION_SCOPE_ONLY",
              "asset_count": len(ids), "annotated_positives": positives,
              "average_precision": sum_precision / positives if positives else None,
              "topk": {str(k): {
                  **precision_at_k(ids, labels, k),
                  "annotated_recall": (sum(labels[asset_id] is True for asset_id in ids[:k])
                                       / positives) if positives else None,
                  "maximum_possible_precision": min(positives, k) / k if len(ids) >= k else None,
              } for k in cutoffs}}
    if strata is not None:
        if set(strata) != set(ids) or any(not isinstance(value, str) or not value
                                         for value in strata.values()):
            raise ValueError("strata must cover the complete frozen population")
        result["strata"] = {name: annotated_metrics(
            [asset_id for asset_id in ids if strata[asset_id] == name], labels, cutoffs,
        ) for name in sorted(set(strata.values()))}
    return result
