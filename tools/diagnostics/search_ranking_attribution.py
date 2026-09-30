"""Read-only ranking attribution for the current real photo library.

PowerShell example::

    .\\.venv\\Scripts\\python.exe tools\\diagnostics\\search_ranking_attribution.py `
      --data-root D:\\SZ\\project\\PhotoSearchData `
      --query "海边日落"

Outputs full JSON plus a focused CSV. SQLite is opened read-only; this tool does not run
migrations, FTS catch-up, repairs or any product write path.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import time
from pathlib import Path
from typing import Any

from photo_search.catalog.db import Catalog
from photo_search.catalog.sqlite_io import open_readonly
from photo_search.config import catalog_db_path, vectors_dir
from photo_search.indexing.store import LanceVectorIndex
from photo_search.search.embedding import encode_query
from photo_search.search.fusion import FTS_CHANNEL, VECTOR_CHANNEL, reciprocal_rank_fusion
from photo_search.search.profile import active_profile, resolve_profile_binding
from photo_search.search.retrieval import (
    MAX_TOP_K,
    SearchScope,
    fts_channel,
    text_column_matches,
    vector_channel,
)
from photo_search.search.tokens import build_match
from photo_search.workers.query_encoder import build_worker_query_encoder, default_model_dir

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "src/photo_search/models/embedding/fg-clip2-base.json"
OUT_JSON = REPO_ROOT / "test-results/search-ranking-attribution.json"
OUT_CSV = REPO_ROOT / "test-results/search-ranking-attribution.csv"
CHUNK = 800


def chunks(values: list[int]) -> list[list[int]]:
    return [values[i : i + CHUNK] for i in range(0, len(values), CHUNK)]


def paths_for(catalog: Catalog, ids: list[int]) -> dict[int, dict[str, str]]:
    out: dict[int, dict[str, str]] = {}
    for batch in chunks(sorted(set(ids))):
        marks = ",".join("?" for _ in batch)
        rows = catalog.read_all(
            "SELECT f.asset_id,f.relative_path,f.availability,s.root_path,s.display_name "
            "FROM file_locations f JOIN source_roots s ON s.source_id=f.source_id "
            f"WHERE f.asset_id IN ({marks}) "
            "ORDER BY f.asset_id,CASE f.availability WHEN 'PRESENT' THEN 0 ELSE 1 END,f.location_id",
            tuple(batch),
        )
        for row in rows:
            asset_id = int(row["asset_id"])
            if asset_id in out:
                continue
            root, rel = str(row["root_path"]), str(row["relative_path"])
            out[asset_id] = {
                "source": str(row["display_name"]),
                "relative_path": rel,
                "path": str(Path(root) / rel),
                "availability": str(row["availability"]),
            }
    return out


def text_matches(catalog: Catalog, match: Any, ids: list[int]) -> dict[int, list[str]]:
    merged: dict[int, set[str]] = {}
    for batch in chunks(sorted(set(ids))):
        for asset_id, columns in text_column_matches(catalog, match, batch).items():
            merged.setdefault(int(asset_id), set()).update(columns)
    return {k: sorted(v) for k, v in merged.items()}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-root", type=Path, required=True)
    p.add_argument("--query", required=True)
    p.add_argument("--model-dir", type=Path)
    p.add_argument("--manifest", type=Path, default=MANIFEST)
    p.add_argument("--device", default="cuda")
    p.add_argument("--top-k", type=int, default=MAX_TOP_K)
    p.add_argument("--focus", type=int, default=200)
    p.add_argument("--gap", type=int, default=50)
    p.add_argument("--json", type=Path, default=OUT_JSON)
    p.add_argument("--csv", type=Path, default=OUT_CSV)
    p.add_argument("--allow-stale-fts", action="store_true")
    args = p.parse_args()

    root = args.data_root.resolve()
    db = catalog_db_path(root)
    if not db.is_file():
        raise SystemExit(f"catalog not found: {db}")
    if not 1 <= args.top_k <= MAX_TOP_K:
        raise SystemExit(f"--top-k must be 1..{MAX_TOP_K}")
    model_dir = args.model_dir.resolve() if args.model_dir else default_model_dir(root)
    if not model_dir.is_dir():
        raise SystemExit(f"model directory not found: {model_dir}")
    manifest = args.manifest.resolve()
    if not manifest.is_file():
        raise SystemExit(f"manifest not found: {manifest}")

    catalog = Catalog(open_readonly(db), db_path=db, data_root=root)
    encoder = None
    t0 = time.perf_counter()
    try:
        profile = active_profile(catalog)
        if profile is None:
            raise SystemExit("no active search profile")
        binding = resolve_profile_binding(catalog, profile)
        backlog = int(catalog.read_one("SELECT COUNT(*) FROM search_projection_queue")[0])
        if backlog and not args.allow_stale_fts:
            raise SystemExit(
                f"search projection backlog={backlog}; let normal product search/refresh catch up, then rerun"
            )

        encoder = build_worker_query_encoder(
            model_dir=model_dir, manifest_path=manifest, device=args.device
        )
        match = build_match(args.query)
        scope = SearchScope.build()

        started = time.perf_counter()
        query_vector = encode_query(
            encoder, args.query, space_id=binding.space_id, dimension=binding.dimension
        )
        encode_ms = (time.perf_counter() - started) * 1000

        started = time.perf_counter()
        fts = fts_channel(catalog, match, scope, top_k=args.top_k)
        fts_ms = (time.perf_counter() - started) * 1000

        started = time.perf_counter()
        vb = vector_channel(
            catalog,
            index=LanceVectorIndex(vectors_dir(root)),
            binding=binding,
            query_vector=query_vector,
            scope=scope,
            top_k=args.top_k,
        )
        vector_ms = (time.perf_counter() - started) * 1000
        vector = list(vb["hits"])

        started = time.perf_counter()
        fused = reciprocal_rank_fusion(
            {
                FTS_CHANNEL: [int(x["asset_id"]) for x in fts],
                VECTOR_CHANNEL: [int(x["asset_id"]) for x in vector],
            }
        )
        fusion_ms = (time.perf_counter() - started) * 1000
        final = fused[: args.top_k]

        fts_map = {
            int(x["asset_id"]): (rank, float(x["score"]))
            for rank, x in enumerate(fts, 1)
        }
        vec_map = {
            int(x["asset_id"]): (rank, float(x["distance"]))
            for rank, x in enumerate(vector, 1)
        }
        final_map = {
            int(x.asset_id): (rank, float(x.score), dict(x.matched_by))
            for rank, x in enumerate(final, 1)
        }
        ids = sorted(set(fts_map) | set(vec_map) | set(final_map))
        provenance = text_matches(catalog, match, list(fts_map))
        file_paths = paths_for(catalog, ids)

        rows: list[dict[str, Any]] = []
        focused: list[dict[str, Any]] = []
        for asset_id in ids:
            fr = final_map.get(asset_id)
            vr = vec_map.get(asset_id)
            tr = fts_map.get(asset_id)
            final_rank = fr[0] if fr else None
            vector_rank = vr[0] if vr else None
            fts_rank = tr[0] if tr else None
            distance = vr[1] if vr else None
            gap = final_rank - vector_rank if final_rank and vector_rank else None
            channels = sorted(fr[2]) if fr else []
            if channels == [FTS_CHANNEL]:
                reason = "TEXT"
            elif channels == [VECTOR_CHANNEL]:
                reason = "SEMANTIC"
            elif channels == [FTS_CHANNEL, VECTOR_CHANNEL]:
                reason = "TEXT+SEMANTIC"
            else:
                reason = ""
            loc = file_paths.get(asset_id, {})
            row = {
                "asset_id": asset_id,
                "final_rank": final_rank,
                "vector_rank": vector_rank,
                "rank_gap_final_minus_vector": gap,
                "vector_distance": distance,
                "vector_cosine_approx": None if distance is None else 1.0 - distance / 2.0,
                "fts_rank": fts_rank,
                "fts_score": tr[1] if tr else None,
                "rrf_score": fr[1] if fr else None,
                "hit_reason": reason,
                "matched_text": provenance.get(asset_id, []),
                "source": loc.get("source"),
                "availability": loc.get("availability"),
                "relative_path": loc.get("relative_path"),
                "path": loc.get("path"),
            }
            focus_reason = []
            if final_rank and final_rank <= args.focus:
                focus_reason.append("FINAL_TOP")
            if vector_rank and vector_rank <= args.focus:
                focus_reason.append("VECTOR_TOP")
            if fts_rank and fts_rank <= args.focus:
                focus_reason.append("FTS_TOP")
            if gap is not None and abs(gap) >= args.gap:
                focus_reason.append("RANK_GAP")
            row["focus_reason"] = focus_reason
            if gap is not None and gap >= args.gap:
                row["diagnosis_hint"] = "VECTOR_HIGH_FINAL_LOW"
            elif final_rank and not vector_rank:
                row["diagnosis_hint"] = "FTS_ONLY_IN_FINAL"
            elif vector_rank and not final_rank:
                row["diagnosis_hint"] = "VECTOR_NOT_IN_FINAL_TOPK"
            else:
                row["diagnosis_hint"] = ""
            rows.append(row)
            if focus_reason:
                focused.append(row)

        inf = math.inf
        rows.sort(
            key=lambda r: (
                r["final_rank"] if r["final_rank"] is not None else inf,
                r["vector_rank"] if r["vector_rank"] is not None else inf,
                r["fts_rank"] if r["fts_rank"] is not None else inf,
                r["asset_id"],
            )
        )
        payload = {
            "tool": "search_ranking_attribution",
            "read_only": True,
            "query": args.query,
            "profile": profile.to_dict(),
            "binding": binding.to_dict(),
            "top_k": args.top_k,
            "focus": args.focus,
            "rank_gap_threshold": args.gap,
            "search_projection_backlog": backlog,
            "counts": {
                "fts": len(fts),
                "vector": len(vector),
                "fused_union": len(fused),
                "final_visible": len(final),
                "focused_csv_rows": len(focused),
                "vector_high_final_low": sum(
                    1 for r in rows if r["diagnosis_hint"] == "VECTOR_HIGH_FINAL_LOW"
                ),
            },
            "vector_scan": {
                "blocks": int(vb["blocks"]),
                "scanned_assets": int(vb["scanned_assets"]),
                "truncated": bool(vb["truncated"]),
            },
            "timing_ms": {
                "encode": round(encode_ms, 1),
                "fts": round(fts_ms, 1),
                "vector": round(vector_ms, 1),
                "fusion": round(fusion_ms, 1),
                "total": round((time.perf_counter() - t0) * 1000, 1),
            },
            "rows": rows,
        }
    finally:
        if encoder is not None:
            encoder.close()
        catalog.close()

    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "focus_reason", "diagnosis_hint", "final_rank", "vector_rank",
        "rank_gap_final_minus_vector", "vector_distance", "vector_cosine_approx",
        "fts_rank", "fts_score", "rrf_score", "hit_reason", "matched_text",
        "asset_id", "source", "availability", "relative_path", "path",
    ]
    with args.csv.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in focused:
            out = dict(row)
            out["focus_reason"] = ";".join(row["focus_reason"])
            out["matched_text"] = ";".join(row["matched_text"])
            writer.writerow(out)

    print(json.dumps(payload["counts"], ensure_ascii=False))
    print(f"JSON: {args.json}")
    print(f"CSV : {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
