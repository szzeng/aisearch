"""Four fixed, post-hoc Qwen/FG-CLIP2 reciprocal-rank-fusion experiments.

Reuse original vector caches without loading a model. Each model first ranks the
complete population in its own cosine space. RRF consumes ranks, never raw scores
or ground-truth labels. This is exploratory after observing single-model results.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import numpy as np

from tools.evaluation.budget_search_metrics import (
    embeddings_from_record,
    evaluate_rankings,
    validate_ranking,
    vector_rankings,
)
from tools.evaluation.prepare_wemm_coco import controlled_output, write_json
from tools.evaluation.wemm_pilot_core import cache_identity, deterministic_rank

ROOT = Path(__file__).resolve().parents[2]
RRF_K = 60
SCHEMES = [{"qwen_dimension": d, "fg_dimension": 768, "weights": [w, 1]}
           for d in (1024, 2048) for w in (1, 2)]


def weighted_rrf(left: list[int | str], right: list[int | str],
                 left_weight: int = 1) -> list[int | str]:
    """Same 1/(60+rank) formula as peer_search_quality.add_fusions, with fixed weights.

    That older helper hard-codes its channel set and source-row tie order; accept
    already complete deterministic ranks here so numeric/string asset-ID ties are
    stable regardless of the sample's shuffled order.
    """
    if type(left_weight) is not int or left_weight not in (1, 2) or not 1 <= len(left) <= 1000:
        raise ValueError("only the bounded pre-frozen RRF weights are supported")
    validate_ranking(left, right)
    left_ranks = {asset_id: rank for rank, asset_id in enumerate(left, 1)}
    right_ranks = {asset_id: rank for rank, asset_id in enumerate(right, 1)}
    scores = np.asarray([left_weight / (RRF_K + left_ranks[asset_id])
                         + 1 / (RRF_K + right_ranks[asset_id]) for asset_id in left])
    return deterministic_rank(scores, left)


class Reader:
    """Track input bytes before/after CPU analysis, including every cached vector."""

    def __init__(self):
        self.hashes: dict[Path, str] = {}

    def raw(self, path: Path, *, max_bytes: int = 16 * 1024 * 1024) -> bytes:
        path = path.resolve()
        if (not 1 <= max_bytes <= 128 * 1024 * 1024 or not path.is_relative_to(ROOT)
                or path.stat().st_size > max_bytes):
            raise ValueError("input outside workspace or bounded pilot size")
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if path in self.hashes and self.hashes[path] != digest:
            raise ValueError("input changed during analysis")
        self.hashes[path] = digest
        return raw

    def json(self, path: Path) -> Any:
        return json.loads(self.raw(path))

    def matrix(self, path: Path, expected_sha: str) -> np.ndarray:
        raw = self.raw(path)
        if self.hashes[path.resolve()] != expected_sha:
            raise ValueError("cached vector checksum differs")
        return np.load(io.BytesIO(raw), allow_pickle=False)

    def verify_unchanged(self) -> dict[str, str]:
        for path, expected in self.hashes.items():
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError("input changed during analysis")
        return {path.relative_to(ROOT).as_posix(): digest for path, digest in self.hashes.items()}


def load_legacy_qwen(reader: Reader, directory: Path, sample: dict[str, Any],
                     queries: list[dict[str, str]]) -> tuple[dict[str, Any], dict[str, Any]]:
    record, identity = reader.json(directory / "result.json"), reader.json(directory / "identity.json")
    inputs = [{key: row[key] for key in ("asset_id", "sha256", "selection_stratum")}
              for row in sample["assets"]]
    if (record.get("schema") != "wemm-pilot-run-v1" or record.get("status") != "COMPLETE"
            or not record.get("stability", {}).get("passed") or record.get("inputs") != inputs
            or record.get("queries") != queries or record["model_spec"].get("model") != "qwen"
            or record["model_spec"].get("native_dimension") != 2048):
        raise ValueError("legacy Qwen run is incomplete, unstable or belongs to different inputs")
    calculated = cache_identity(record["model_spec"], inputs, queries)
    if calculated != record["cache_identity"] or identity != {
            "cache_identity": calculated, "model_spec": record["model_spec"],
            "inputs": inputs, "queries": queries}:
        raise ValueError("legacy Qwen cache identity mismatch")
    matrices = []
    for key, count in (("images", len(inputs)), ("query_vectors", len(queries))):
        records = record[key]
        if set(records) != {str(i) for i in range(count)}:
            raise ValueError("legacy vector records are incomplete or duplicated")
        rows = []
        for index in range(count):
            item = records[str(index)]
            if Path(item["file"]).name != item["file"] or not item["file"].endswith(".npy"):
                raise ValueError("invalid legacy vector filename")
            value = reader.matrix(directory / item["file"], item["sha256"])
            if (value.shape != (1, 2048) or value.dtype != np.float32 or not np.isfinite(value).all()
                    or not np.allclose(np.linalg.norm(value.astype(np.float64), axis=1), 1, atol=1e-5, rtol=0)):
                raise ValueError("invalid legacy native vector")
            rows.append(value)
        matrices.append(np.concatenate(rows))
    ranks = vector_rankings(*matrices, [row["asset_id"] for row in inputs],
                           [row["query_id"] for row in queries], [1024, 2048])
    return {f"qwen-fp16-{dim}": value for dim, value in ranks.items()}, {
        "kind": "original_native2048_cache", "cache_identity": calculated,
        "model_spec": record["model_spec"]}


def load_new_run(reader: Reader, directory: Path, sample_path: Path,
                 sample: dict[str, Any], queries: list[dict[str, str]], model: str
                 ) -> tuple[dict[str, Any], dict[str, Any]]:
    record = reader.json(directory / "result.json")
    if (record.get("model") != model or record.get("assets") != sample["assets"]
            or record.get("queries") != queries
            or record.get("sample_sha256") != reader.hashes[sample_path.resolve()]):
        raise ValueError("model run differs from complete frozen input")
    matrices = [reader.matrix(directory / name, record["vectors_sha256"][name])
                for name in ("images.npy", "queries.npy")]
    ranks, _ = embeddings_from_record(record, *matrices)
    return ranks, {"kind": "budget_embedding_run", "cache_identity": record["identity"],
                   "model_lock": record["model_lock"], "source_sha256": record["source_sha256"]}


def analyze(plan_path: Path) -> dict[str, Any]:
    reader = Reader()
    # These helpers determine identity validation, partial-label scoring, and ties;
    # pin their bytes alongside inputs so results remain attributable after edits.
    for filename in ("budget_rank_fusion.py", "budget_search_metrics.py", "wemm_pilot_core.py",
                     "prepare_wemm_coco.py"):
        reader.raw(Path(__file__).with_name(filename))
    plan = reader.json(plan_path)
    if (plan.get("schema") != "budget-rank-fusion-plan-v1" or plan.get("rrf_k") != RRF_K
            or plan.get("schemes") != SCHEMES
            or plan.get("study_kind") != "POSTHOC_EXPLORATORY_AFTER_SINGLE_MODEL_RESULTS_NOT_HOLDOUT"
            or not 1 <= len(plan.get("input_datasets", {})) <= 2):
        raise ValueError("plan differs from bounded pre-frozen experiment")
    outputs = {}
    for name, paths in plan["input_datasets"].items():
        sample_path = ROOT / paths["sample"]
        sample = reader.json(sample_path)
        if not 1 <= len(sample["assets"]) <= 1000:
            raise ValueError("dataset exceeds CPU experiment bounds")
        labels = reader.json(ROOT / paths["labels"])
        queries = labels["query_definitions"]
        identities = [{key: row[key] for key in ("asset_id", "sha256")} for row in sample["assets"]]
        if labels["input_identities"] != identities:
            raise ValueError("labels and sample identities differ")
        qwen_path, fg_path = ROOT / paths["qwen_run"], ROOT / paths["fg_run"]
        qwen_header = reader.json(qwen_path / "result.json")
        if qwen_header.get("schema") == "wemm-pilot-run-v1":
            qwen, qwen_source = load_legacy_qwen(reader, qwen_path, sample, queries)
        else:
            qwen, qwen_source = load_new_run(reader, qwen_path, sample_path, sample, queries, "qwen")
        fg, fg_source = load_new_run(reader, fg_path, sample_path, sample, queries, "fgclip-base")
        if len(fg) != 1:
            raise ValueError("FG must use exactly one native space")
        fg_name, fg_rankings = next(iter(fg.items()))
        rankings = {**qwen, **fg}
        for scheme in SCHEMES:
            dimension, weight = scheme["qwen_dimension"], scheme["weights"][0]
            baseline = qwen[f"qwen-fp16-{dimension}"]
            if set(baseline) != set(fg_rankings):
                raise ValueError("fusion inputs must share all frozen queries")
            rankings[f"rrf_qwen{dimension}_fg768_weight{weight}to1"] = {
                query: weighted_rrf(baseline[query], fg_rankings[query], weight) for query in baseline}
        result = evaluate_rankings(rankings, labels, identities, queries)
        summaries = {}
        for scheme, per_query in result["results"].items():
            judged = {query: value for query, value in per_query.items() if value["AP"] is not None}
            summaries[scheme] = {"mean_AP": float(np.mean([v["AP"] for v in judged.values()])),
                                 "judged_queries": list(judged),
                                 "AP_by_query": {query: value["AP"] for query, value in judged.items()}}
        outputs[name] = {**result, "summary": summaries,
                         "provenance": {"qwen": qwen_source, fg_name: fg_source}}
    return {"status": "COMPLETE", "study_kind": plan["study_kind"], "plan": plan,
            "datasets": outputs, "source_hashes": reader.verify_unchanged(),
            "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "limitations": [
                "Post-hoc exploration after seeing single-model outcomes; not independent confirmation.",
                "Ensembles need two encoders and two independent vector spaces; no free quality gain.",
                "Full-population ranks in this <=1000 sample are not an ANN/topK-union implementation or 300k cost measurement.",
                "Do not mix Qwen/FG raw cosine scores or put their vectors into one index space.",
            ]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--junit", type=Path, required=True)
    args = parser.parse_args()
    out, junit = controlled_output(args.out), controlled_output(args.junit)
    if out.exists() or junit.exists():
        raise ValueError("fresh result paths required; do not overwrite earlier evidence")
    failure, started = None, time.perf_counter()
    try:
        result = analyze(args.plan)
        write_json(out, result)
        print(json.dumps({name: data["summary"] for name, data in result["datasets"].items()},
                         ensure_ascii=False))
    except BaseException as exc:
        failure = exc
        raise
    finally:
        suite = ET.Element("testsuite", name="budget_rank_fusion", tests="1", errors="0", skipped="0",
                           failures=str(int(failure is not None)), time=str(time.perf_counter() - started))
        case = ET.SubElement(suite, "testcase", name="fixed_rank_fusion_identity_and_metrics")
        if failure is not None:
            ET.SubElement(case, "failure", message=str(failure))
        junit.parent.mkdir(parents=True, exist_ok=True)
        ET.ElementTree(suite).write(junit, encoding="utf-8", xml_declaration=True)


if __name__ == "__main__":
    main()
