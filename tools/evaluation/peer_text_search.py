"""Compare text policies on bounded synthetic data, without installing peer products.

Current/legacy policies use the real Catalog and FTS channel. The Immich-inspired
variant ports its BMP CJK bigrams, then approximates PostgreSQL strict_word_similarity
with word-boundary trigram sets in a SQLite Python UDF. This is NOT a PostgreSQL,
GIN, Immich end-to-end, OCR-recognition, or performance benchmark. Its numeric
scores are not calibrated probabilities. Freeze the plan before running it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import tempfile
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import CatalogError
from photo_search.search import retrieval, tokens
from photo_search.search.documents import TEXT_COLUMNS
from tools.evaluation import search_quality as quality

ROOT = Path(__file__).resolve().parents[2]
IMMICH_REVISION = "e5094d54e95fc5c7d6f39990485835b15c0707bf"
IMMICH_SOURCE = (
    f"https://github.com/immich-app/immich/blob/{IMMICH_REVISION}"
    "/server/src/utils/database.ts#L372"
)
PG_SOURCE = "https://www.postgresql.org/docs/18/pgtrgm.html"
THRESHOLD = 0.5  # PostgreSQL's documented default, fixed before measurement.
VARIANTS = ("current", "legacy_stopchar", "immich_approx_all_fields", "immich_approx_ocr_only")


def supplement() -> dict[str, Any]:
    """Independent fixed examples: target and plausible distractor for each query.

    Each pair is evaluated in the shared supplement gallery. Labels describe the
    intended text, not model judgements. All unlisted rows are irrelevant.
    """
    cases = [
        ("natural", "杭州的照片", {"place_text": "杭州", "user_text": "照片"},
         {"place_text": "杭州", "user_text": "账单"}),
        ("natural", "成都的夜景", {"place_text": "成都", "caption_text": "夜景"},
         {"place_text": "成都", "caption_text": "白天"}),
        ("natural", "爸爸和妈妈", {"user_text": "爸爸 妈妈"}, {"user_text": "爸爸 叔叔"}),
        ("natural", "孩子在海边", {"user_text": "孩子 海边"}, {"user_text": "孩子 山中"}),
        ("name", "我的世界", {"ocr_text": "我的世界 游戏海报"}, {"ocr_text": "世界 地图"}),
        ("name", "你的名字", {"ocr_text": "你的名字 电影海报"}, {"ocr_text": "名字 登记表"}),
        ("name", "这是我的战争", {"ocr_text": "这是我的战争 游戏截图"},
         {"ocr_text": "战争 新闻"}),
        ("name", "和田", {"place_text": "和田 玉石市场"}, {"place_text": "田园 风光"}),
        ("identifier", "ZX9012", {"ocr_text": "快递 ZX9012"}, {"ocr_text": "快递 ZX9013"}),
        ("identifier", '"PQ7788"', {"ocr_text": "票号 PQ7788"}, {"ocr_text": "票号 PQ77880"}),
        ("identifier", "IMG_5721", {"filename_text": "IMG_5721.jpg"},
         {"filename_text": "IMG_57210.jpg"}),
        ("identifier", "粤B66321", {"ocr_text": "粤B66321"}, {"ocr_text": "粤B66322"}),
        ("cross_field", "深圳 灯塔", {"place_text": "深圳", "filename_text": "灯塔.jpg"},
         {"place_text": "珠海", "filename_text": "灯塔.jpg"}),
        ("cross_field", "福田 发票", {"place_text": "福田", "ocr_text": "发票"},
         {"place_text": "福田", "ocr_text": "菜单"}),
        ("cross_field", "南山 毕业", {"place_text": "南山", "user_text": "毕业"},
         {"place_text": "南山", "user_text": "开学"}),
        ("cross_field", "盐田 招牌", {"place_text": "盐田", "ocr_text": "招牌"},
         {"place_text": "盐田", "ocr_text": "通知"}),
        ("ocr_error", "深圳图书馆", {"ocr_text": "深圳图书管 借阅"},
         {"ocr_text": "深圳体育馆 赛事"}),
        ("ocr_error", "市民服务中心", {"ocr_text": "市民服条中心 办事"},
         {"ocr_text": "市民文化中心 展览"}),
        ("ocr_error", "招商银行营业厅", {"ocr_text": "招商银行营业庁 柜台"},
         {"ocr_text": "招商物业营业部"}),
        ("ocr_error", "南山区行政服务大厅", {"ocr_text": "南山区行政服各大厅"},
         {"ocr_text": "南山区文化体育大厅"}),
    ]
    docs, queries = [], []
    for i, (category, query, target, distractor) in enumerate(cases):
        asset_id = 2 * i + 1
        docs.extend([{"asset_id": asset_id, **target}, {"asset_id": asset_id + 1, **distractor}])
        queries.append({
            "query_id": f"{category}-{i}", "category": category, "family": f"{category}-{i}",
            "split": "tuning" if i % 2 == 0 else "holdout", "text": query,
            "judgments_complete": True, "relevant": {str(asset_id): 3},
            "target_ids": [asset_id],
        })
    for i, query in enumerate(("ZX9019", "广州市天河区税务大厅")):
        queries.append({
            "query_id": f"absent-{i}", "category": "absent", "family": f"absent-{i}",
            "split": "tuning" if i == 0 else "holdout", "text": query,
            "judgments_complete": True, "relevant": {}, "no_relevant": True,
        })
    return {
        "schema_version": 1, "name": "peer-text-supplement-v1", "kind": "SYNTHETIC",
        "provenance": "Fixed hand-authored synthetic text; no photos, real library or model output.",
        "annotation_rules": "Complete intended-name/code/content judgements; OCR-error targets "
        "explicitly contain one recognition error. Unlisted documents are irrelevant. "
        "A spaced natural-language query requires all intended content words across fields.",
        "documents": docs, "queries": queries,
    }


def legacy_match(query: str) -> tokens.MatchQuery:
    """Replay repository b619252's stop-character handling, retaining its query bounds."""
    if len(query) > tokens.MAX_QUERY_CHARS:
        raise CatalogError(tokens.VALIDATION_ERROR, "query too long")
    terms = []
    for piece, quoted in tokens._pieces(query):
        if quoted:
            terms.extend(tokens._phrase_terms(piece))
            continue
        run = []

        def flush() -> None:
            if run:
                terms.append(tokens.Term(tuple(run), True))
                run.clear()

        for token in tokens.scan_tokens(piece):
            if len(token) == 1 and tokens._is_cjk(token):
                if token in tokens.CJK_STOPWORDS:
                    flush()
                else:
                    run.append(token)
                continue
            flush()
            if token not in tokens.LATIN_STOPWORDS:
                terms.append(tokens.Term((token,), False))
        flush()
    if not terms or len(terms) > tokens.MAX_QUERY_TERMS:
        raise CatalogError(tokens.VALIDATION_ERROR, "query has no terms or too many terms")
    return tokens.MatchQuery(query, tokens.normalize_text(query),
                             " AND ".join(term.render() for term in terms), tuple(terms))


def immich_tokens(text: str) -> list[str]:
    """Port the peer's tokenizer for our BMP fixtures; no FTS AND/OR is invented."""
    def cjk(char: str) -> bool:
        return any(low <= ord(char) <= high for low, high in (
            (0x4E00, 0x9FFF), (0xAC00, 0xD7AF), (0x3040, 0x309F),
            (0x30A0, 0x30FF), (0x3400, 0x4DBF),
        ))

    result, i = [], 0
    while i < len(text):
        if ord(text[i]) <= 32:
            i += 1
            continue
        start, is_cjk = i, cjk(text[i])
        while i < len(text) and ord(text[i]) > 32 and cjk(text[i]) == is_cjk:
            i += 1
        word = text[start:i]
        result.extend([word] if not is_cjk or len(word) == 1 else
                      [word[k:k + 2] for k in range(len(word) - 1)])
    return result


def word_trigrams(text: str) -> list[set[str]]:
    # Approximate default lowercasing/unaccent using Unicode NFD. PostgreSQL's
    # collation, unaccent.rules and multibyte trigram hashes are NOT reproduced.
    folded = "".join(char for char in unicodedata.normalize("NFD", text.lower())
                     if unicodedata.category(char) != "Mn")
    words = re.findall(r"[^\W_]+", folded, flags=re.UNICODE)
    return [{("  " + word + " ")[i:i + 3] for i in range(len(word) + 1)} for word in words]


def strict_word_similarity(query: str, target: str) -> float:
    """Maximum set Jaccard over continuous whole-word extents, per pg_trgm docs."""
    query_set = set().union(*word_trigrams(query))
    words = word_trigrams(target)
    if not query_set or not words:
        return 0.0
    best = 0.0
    for start in range(len(words)):
        extent: set[str] = set()
        for word in words[start:]:
            extent.update(word)
            best = max(best, len(query_set & extent) / len(query_set | extent))
            if best == 1.0:
                return best
    return best


def policy_plan(datasets: list[dict[str, Any]]) -> dict[str, Any]:
    # Capture every Python implementation under src, not merely this driver:
    # Catalog/schema, tokenisation, retrieval, documents, fusion and metrics all
    # participate in the result. The large manifest belongs only to ignored evidence.
    dependencies = sorted((ROOT / "src/photo_search").rglob("*.py"))
    dependencies.extend([Path(quality.__file__), ROOT / "pyproject.toml", ROOT / "uv.lock"])
    return {
        "schema_version": 1, "variants": list(VARIANTS), "threshold": THRESHOLD,
        "selected_by": "Fixed peer/documented defaults; no quality results used to tune policies.",
        "datasets": {data["name"]: quality.digest(data) for data in datasets},
        "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "dependency_sha256": {
            path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in dependencies
        },
        "runtime": {"python": sys.version, "sqlite": sqlite3.sqlite_version},
        "sources": [IMMICH_SOURCE, PG_SOURCE],
    }


def evaluate(data: dict[str, Any], work_dir: Path) -> dict[str, Any]:
    quality.validate_dataset(data)
    if len(data["documents"]) > 1000 or len(data["queries"]) > 200:
        raise ValueError("this quadratic exploratory approximation only accepts small fixtures")
    if work_dir.exists():
        raise ValueError("work directory must be new")
    catalog = Catalog.open(data_root=work_dir)
    db = sqlite3.connect(":memory:")
    results: dict[str, list[dict[str, Any]]] = {variant: [] for variant in VARIANTS}
    try:
        quality.seed_catalog(catalog, data)
        db.create_function("strict_word_similarity", 2, strict_word_similarity, deterministic=True)
        db.execute("CREATE TABLE docs(asset_id INTEGER PRIMARY KEY, all_text TEXT, ocr_text TEXT)")
        for row in data["documents"]:
            all_text = " ".join(" ".join(immich_tokens(row.get(column, "")))
                                for column in TEXT_COLUMNS)
            ocr_text = " ".join(immich_tokens(row.get("ocr_text", "")))
            if max(len(all_text), len(ocr_text)) > 4096:
                raise ValueError("synthetic document exceeds bounded approximation limit")
            db.execute("INSERT INTO docs VALUES(?,?,?)", (row["asset_id"], all_text, ocr_text))
        for query in data["queries"]:
            for variant in VARIANTS:
                extra: dict[str, Any] = {}
                try:
                    if variant in ("current", "legacy_stopchar"):
                        match = (tokens.build_match if variant == "current" else legacy_match)(
                            query["text"])
                        hits = retrieval.fts_channel(catalog, match, retrieval.SearchScope())
                        ranked = [hit["asset_id"] for hit in hits]
                        extra["match_expression"] = match.expression
                    else:
                        column = "all_text" if variant.endswith("all_fields") else "ocr_text"
                        peer_query = " ".join(immich_tokens(query["text"]))
                        # column comes only from the closed variant enum, values stay bound.
                        hits = db.execute(
                            f"SELECT asset_id, strict_word_similarity(?,{column}) AS score "
                            "FROM docs WHERE score >= ? ORDER BY score DESC, asset_id",
                            (peer_query, THRESHOLD),
                        ).fetchall()
                        ranked = [hit[0] for hit in hits]
                        extra.update(peer_query=peer_query, scores=hits)
                except CatalogError as error:
                    if error.code != tokens.VALIDATION_ERROR:
                        raise
                    ranked, extra = [], {"query_error": error.code}
                results[variant].append({
                    **quality.score_ranking(query, ranked), **extra,
                    "split": query["split"], "category": query.get("category", query["family"]),
                })
    finally:
        db.close()
        catalog.close()
    variants = {}
    for variant, rows in results.items():
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            groups[row["category"]].append(row)
        variants[variant] = {
            "metrics": quality.summarise(rows),
            "splits": {split: quality.summarise([r for r in rows if r["split"] == split])
                       for split in ("tuning", "holdout")},
            "categories": {key: quality.summarise(value) for key, value in groups.items()},
            "queries": rows,
        }
    return {"dataset_name": data["name"], "dataset_sha256": quality.digest(data),
            "documents": len(data["documents"]), "variants": variants}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--freeze-to", type=Path)
    action.add_argument("--plan", type=Path)
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()
    datasets = [quality.load_json(quality.DEFAULT_DATASET), supplement()]
    plan = policy_plan(datasets)
    for data in datasets:
        quality.validate_dataset(data)
    if args.freeze_to:
        quality.write_new(args.freeze_to, plan)
        return 0
    if not args.result or args.result.exists():
        parser.error("--result must name a new file")
    if quality.load_json(args.plan) != plan:
        parser.error("plan/data/tool changed after freezing; refuse silently altered experiment")
    temp_root = ROOT / ".tmp/peer-search-20261016/text"
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=temp_root) as temp:
        reports = [evaluate(data, Path(temp) / str(i)) for i, data in enumerate(datasets)]
    result = {
        "plan": plan, "results": reports,
        "limitations": [
            "Synthetic text policy experiment, not real image/OCR quality or a peer product benchmark.",
            "The primary 76-document set was previously inspected; it is reused regression evidence.",
            "Supplement policies and labels were frozen before this run; no parameter was selected.",
            "Immich uses OCR-only filtering; six-field concatenation is our adaptation, not its feature.",
            "Python UDF approximates pg_trgm; locale/unaccent/hash/GIN plan are not reproduced.",
            "Similarity ordering is experimental; Immich's OCR condition itself only filters assets.",
            "Quoted exact phrases and identifier prefixes are not interpreted by the fuzzy variant.",
        ],
    }
    quality.write_new(args.result, result)
    print(json.dumps({report["dataset_name"]: {
        variant: block["metrics"] for variant, block in report["variants"].items()
    } for report in reports}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
