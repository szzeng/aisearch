"""Evaluate actual FTS/BM25 and RRF on an explicitly labelled, isolated text corpus.

No photos are opened, OCR is not run, and vectors are never invented. The bundled
SYNTHETIC fixture checks retrieval of already recognised text, not image-model quality.
Optional external rankings must identify the model/run and explicitly declare that
relevance labels were not used to produce them. That declaration is provenance, not
independent proof. Review the producer before making a model-quality claim.

Run tuning first with --freeze-to CONFIG, then holdout with --config CONFIG. Holdout
cannot override weights, candidate limits, the corpus or the external model run.
Do not select a new configuration after looking at holdout: start a new unseen set.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import CatalogError
from photo_search.evaluation.metrics import best_rank, ndcg_at_k, recall_at_k
from photo_search.search import fusion, retrieval, tokens
from photo_search.search.documents import TEXT_COLUMNS, fingerprint

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = ROOT / "tests/fixtures/search_quality/synthetic_metadata.json"


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON root must be an object")
    return value


def validate_dataset(data: dict[str, Any]) -> None:
    """Require complete judgments and disjoint query families, including negatives."""
    if data.get("schema_version") != 1:
        raise ValueError("unsupported dataset schema_version")
    if data.get("kind") not in {"SYNTHETIC", "HUMAN_LABELLED"}:
        raise ValueError("dataset kind must be SYNTHETIC or HUMAN_LABELLED")
    if not data.get("provenance") or not data.get("annotation_rules"):
        raise ValueError("dataset needs provenance and annotation_rules")
    docs = data.get("documents", [])
    ids = [row["asset_id"] for row in docs]
    if not ids or any(type(value) is not int or value <= 0 for value in ids):
        raise ValueError("document ids must be positive integers")
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate document id")
    queries = data.get("queries", [])
    if not queries:
        raise ValueError("dataset has no queries")
    seen: set[str] = set()
    text_splits: dict[str, str] = {}
    families: dict[str, str] = {}
    splits: set[str] = set()
    for row in queries:
        query_id, split = row["query_id"], row["split"]
        if query_id in seen or not str(query_id).strip():
            raise ValueError("duplicate or empty query id")
        seen.add(query_id)
        if split not in {"tuning", "holdout"}:
            raise ValueError("unknown split")
        splits.add(split)
        text = tokens.normalize_text(row["text"]).strip()
        family = str(row.get("family", "")).strip()
        if not text or not family:
            raise ValueError("query needs text and family")
        if text_splits.setdefault(text, split) != split or families.setdefault(family, split) != split:
            raise ValueError("query text/family leaks between tuning and holdout")
        if row.get("judgments_complete") is not True:
            raise ValueError("partial judgments cannot measure recall or no-result behavior")
        relevant = row.get("relevant", {})
        for asset_id, grade in relevant.items():
            if str(int(asset_id)) != str(asset_id) or int(asset_id) not in ids:
                raise ValueError("judgment references unknown/noncanonical document id")
            if type(grade) is not int or grade not in {1, 2, 3}:
                raise ValueError("relevance grades must be 1, 2 or 3")
        if bool(relevant) == bool(row.get("no_relevant", False)):
            raise ValueError("empty judgments require explicit no_relevant=true")
        if any(str(value) not in relevant for value in row.get("target_ids", [])):
            raise ValueError("known targets must have positive relevance labels")
    if splits != {"tuning", "holdout"}:
        raise ValueError("both tuning and holdout queries are required")


def validate_rankings(data: dict[str, Any], rankings: dict[str, Any] | None) -> None:
    if rankings is None:
        return
    if rankings.get("dataset_sha256") != digest(data):
        raise ValueError("external rankings belong to a different dataset")
    for key in ("model_id", "model_revision", "preprocessing", "producer"):
        if not rankings.get(key):
            raise ValueError(f"external rankings need {key}")
    if rankings.get("judgments_used") is not False:
        raise ValueError("external rankings must be produced without relevance labels")
    if rankings.get("kind") not in {"MODEL_RUN", "SYNTHETIC_REPLAY"}:
        raise ValueError("rankings kind must identify a model run or synthetic replay")
    ids = {row["asset_id"] for row in data["documents"]}
    expected = {row["query_id"] for row in data["queries"]}
    if set(rankings.get("rankings", {})) != expected:
        raise ValueError("external rankings must cover every query exactly once")
    for ranked in rankings["rankings"].values():
        if not isinstance(ranked, list) or any(type(value) is not int for value in ranked):
            raise ValueError("rankings must be lists of integer asset ids")
        if len(ranked) != len(set(ranked)) or not set(ranked) <= ids:
            raise ValueError("rankings contain duplicate or unknown document ids")


def score_ranking(query: Mapping[str, Any], ranked: Sequence[int]) -> dict[str, Any]:
    """P@20 has a fixed denominator; empty-result queries have separate metrics."""
    if len(ranked) != len(set(ranked)):
        raise ValueError("duplicate ranked ids would corrupt retrieval metrics")
    relevant = query["relevant"]
    ranked_ids = [str(value) for value in ranked]
    top = ranked_ids[:20]
    hits = len(set(top) & set(relevant))
    row: dict[str, Any] = {
        "query_id": query["query_id"], "family": query["family"],
        "text": query["text"], "ranked_asset_ids": list(ranked),
        "returned": len(ranked), "relevant_total": len(relevant),
    }
    if not relevant:
        return {**row, "no_relevant": True, "false_positive": bool(ranked)}
    targets = {str(value) for value in query.get("target_ids", [])}
    return {
        **row, "no_relevant": False, "precision_at_20": hits / 20,
        "returned_precision_at_20": hits / len(top) if top else 0.0,
        "ndcg_at_20": ndcg_at_k(ranked_ids, relevant, 20),
        "recall_at_50": recall_at_k(ranked_ids, relevant, 50),
        "hit_at_50": bool(set(ranked_ids[:50]) & set(relevant)),
        "target_hit_at_50": bool(set(ranked_ids[:50]) & targets) if targets else None,
        "best_rank": best_rank(ranked_ids, relevant),
    }


def summarise(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    positives = [row for row in rows if not row["no_relevant"]]
    negatives = [row for row in rows if row["no_relevant"]]
    accepted_negatives = [row for row in negatives if not row.get("query_error")]

    def mean(values: Sequence[float]) -> float | None:
        return round(sum(values) / len(values), 6) if values else None

    return {
        "queries": len(rows), "positive_queries": len(positives),
        "no_relevant_queries": len(negatives),
        "accepted_no_relevant_queries": len(accepted_negatives),
        "rejected_queries": sum(bool(row.get("query_error")) for row in rows),
        **{f"{key}_mean": mean([row[key] for row in positives]) for key in (
            "precision_at_20", "returned_precision_at_20", "ndcg_at_20", "recall_at_50",
            "hit_at_50",
        )},
        "target_hit_at_50_mean": mean([
            row["target_hit_at_50"] for row in positives if row["target_hit_at_50"] is not None
        ]),
        "false_positive_query_rate": mean([row["false_positive"] for row in accepted_negatives]),
        "no_relevant_returned_mean": mean([row["returned"] for row in accepted_negatives]),
    }


def seed_catalog(catalog: Catalog, data: dict[str, Any]) -> None:
    """Seed synthetic *derived* text, then let real schema triggers build the FTS index.

    This deliberately does not measure scanner/OCR/place-assignment correctness. No
    locations are registered: the official photo scope includes legacy no-location rows.
    """
    with catalog.write() as connection:
        for row in data["documents"]:
            asset_id = row["asset_id"]
            connection.execute(
                "INSERT INTO assets(asset_id,sha256,byte_size,created_at) VALUES (?,?,0,0)",
                (asset_id, digest(row)),
            )
            columns = {column: tokens.index_text(row.get(column, "")) for column in TEXT_COLUMNS}
            connection.execute(
                "INSERT INTO search_documents(asset_id,filename_text,user_text,ocr_text,"
                "caption_text,tag_text,place_text,source_fingerprint,tokenizer_version,"
                "fts_generation_id,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,0)",
                (asset_id, *(columns[column] for column in TEXT_COLUMNS), fingerprint(columns),
                 tokens.TOKENIZER_VERSION, tokens.FTS_GENERATION_ID),
            )


def make_config(
    data: dict[str, Any], rankings: dict[str, Any] | None = None, *,
    fts_weight: float = 1.0, vector_weight: float = 1.0, top_k: int = 5000,
) -> dict[str, Any]:
    if any(not math.isfinite(value) or value <= 0 for value in (fts_weight, vector_weight)):
        raise ValueError("evaluation weights must be finite and positive")
    if not 50 <= top_k <= retrieval.MAX_TOP_K:
        raise ValueError("top_k must cover Recall@50 and stay within product bounds")
    return {
        "schema_version": 1, "selected_on": "tuning", "dataset_sha256": digest(data),
        "rankings_sha256": digest(rankings) if rankings else None,
        "weights": {"fts": fts_weight, "vector": vector_weight}, "top_k": top_k,
    }


def evaluate(
    data: dict[str, Any], *, split: str, work_dir: Path,
    config: dict[str, Any], rankings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validate_dataset(data)
    validate_rankings(data, rankings)
    if split not in {"tuning", "holdout"}:
        raise ValueError("evaluate exactly one split at a time")
    expected = make_config(data, rankings, **{
        "fts_weight": config["weights"]["fts"],
        "vector_weight": config["weights"]["vector"], "top_k": config["top_k"],
    })
    if config != expected:
        raise ValueError("configuration does not match the frozen dataset/model run")
    if work_dir.exists():
        raise ValueError("work directory must be new; refusing to open an existing catalog")
    catalog = Catalog.open(data_root=work_dir)
    rows = []
    try:
        seed_catalog(catalog, data)
        for query in data["queries"]:
            if query["split"] != split:
                continue
            try:
                match = tokens.build_match(query["text"])
            except CatalogError as error:
                if error.code != tokens.VALIDATION_ERROR:
                    raise
                rows.append({**score_ranking(query, []), "query_error": error.code})
                continue
            fts = retrieval.fts_channel(
                catalog, match, retrieval.SearchScope(), top_k=config["top_k"]
            )
            vector = rankings["rankings"][query["query_id"]] if rankings else []
            channels = {"fts": [hit["asset_id"] for hit in fts],
                        "vector": vector[:config["top_k"]]}
            fused = fusion.reciprocal_rank_fusion(
                channels, weights=config["weights"], limit=config["top_k"]
            )
            rows.append({
                **score_ranking(query, [hit.asset_id for hit in fused]),
                "match_expression": match.expression, "channels": channels,
                "fts_scores": fts,
            })
        schema_version = catalog.schema_version
    finally:
        catalog.close()
    sources = (tokens, retrieval, fusion)
    return {
        "schema_version": 1, "dataset_name": data["name"], "dataset_kind": data["kind"],
        "split": split, "config": config, "metrics": summarise(rows), "queries": rows,
        "catalog_schema_version": schema_version, "python": sys.version,
        "implementation_sha256": {
            module.__name__: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
            for module in sources
        },
        "vector_rankings": {key: value for key, value in rankings.items() if key != "rankings"}
        if rankings else None,
        "limitations": [
            "Text fixture evaluates already recognised/assigned text, not OCR or image quality.",
            "Synthetic adversarial queries are not a representative user-accuracy estimate.",
            "No-result false positives measure returned candidates, not calibrated confidence.",
            "Shared corpus; disjoint query families. Never tune using holdout results.",
        ],
    }


def write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--rankings", type=Path)
    parser.add_argument("--split", choices=("tuning", "holdout"), required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--freeze-to", type=Path)
    parser.add_argument("--fts-weight", type=float)
    parser.add_argument("--vector-weight", type=float)
    parser.add_argument("--top-k", type=int)
    args = parser.parse_args()
    if args.split == "holdout" and (not args.config or args.freeze_to):
        parser.error("holdout requires --config frozen on tuning; cannot create a new config")
    if args.config and any(value is not None for value in (
        args.fts_weight, args.vector_weight, args.top_k,
    )):
        parser.error("frozen configuration cannot be overridden")
    if args.result.exists() or (args.freeze_to and args.freeze_to.exists()):
        parser.error("refusing to overwrite existing results/configuration")
    try:
        data = load_json(args.dataset)
        rankings = load_json(args.rankings) if args.rankings else None
        config = load_json(args.config) if args.config else make_config(
            data, rankings, fts_weight=args.fts_weight if args.fts_weight is not None else 1.0,
            vector_weight=args.vector_weight if args.vector_weight is not None else 1.0,
            top_k=args.top_k if args.top_k is not None else 5000,
        )
        temp_root = ROOT / ".tmp/search-quality"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="eval-", dir=temp_root) as temp:
            report = evaluate(data, split=args.split, work_dir=Path(temp) / "data",
                              config=config, rankings=rankings)
        write_new(args.result, report)
        if args.freeze_to:
            write_new(args.freeze_to, config)
    except (OSError, ValueError, KeyError, CatalogError) as error:
        parser.exit(1, f"evaluation failed: {error}\n")
    print(json.dumps({"split": args.split, **report["metrics"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
