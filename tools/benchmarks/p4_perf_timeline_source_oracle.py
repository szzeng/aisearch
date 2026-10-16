"""Independent full source timeline order oracle for a synthetic catalog."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from collections import Counter
from pathlib import Path

from photo_search.api.timeline import timeline_page, timeline_periods
from photo_search.catalog.db import Catalog
from photo_search.catalog.image_policy import is_image_path
from photo_search.catalog.sqlite_io import open_readonly
from photo_search.taken_time import contract, read


def verify(root: Path, source_id: int, output: Path) -> dict:
    data = root / "data"
    path = data / "catalog/catalog.db"
    with Catalog(open_readonly(path), db_path=path, data_root=data) as catalog:
        assert read.rules_version(catalog.connection) == contract.RULES_VERSION
        # Membership is independently computed from source locations and the
        # Python filename policy. It does not reuse optimized photo_asset_sql.
        catalog.connection.create_function("oracle_image", 1, is_image_path)
        trusted = "m.time_source IN ('EXIF_ORIGINAL','EXIF_DIGITIZED')"
        oracle = catalog.read_all(
            f"SELECT a.asset_id,CASE WHEN {trusted} THEN 0 "
            "WHEN m.time_source IN ('EXIF_ROOT','FILE_MTIME') THEN 1 ELSE 2 END AS rank,"
            f"CASE WHEN {trusted} THEN m.taken_at_sort ELSE a.created_at END AS sort_key "
            "FROM assets a LEFT JOIN asset_metadata m USING(asset_id) WHERE a.state='ACTIVE' "
            "AND a.asset_id IN (SELECT asset_id FROM file_locations "
            "WHERE source_id=? AND oracle_image(relative_path)) "
            "ORDER BY rank,sort_key DESC,a.asset_id DESC", (source_id,),
        )
        expected = [(int(row[0]), int(row[1]), int(row[2])) for row in oracle]
        actual, cursor, pages, observed_revision = [], None, 0, None
        while True:
            page = timeline_page(catalog, kind="source", source_id=source_id,
                                 limit=60, cursor=cursor)
            if observed_revision is None:
                observed_revision = page["catalog_filter_revision"]
            assert page["catalog_filter_revision"] == observed_revision
            for asset in page["assets"]:
                token = asset["anchor"]
                rank, sort_key, asset_id = json.loads(
                    base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
                )[3]
                assert asset_id == asset["asset_id"]
                actual.append((asset_id, rank, sort_key))
            pages += 1
            cursor = page["next_cursor"]
            if pages % 100 == 0:
                print(f"source_oracle_pages={pages} rows={len(actual)}", flush=True)
            if cursor is None:
                break
        assert actual == expected
        assert len(actual) == len({row[0] for row in actual})
        counts = Counter(row[1] for row in expected)
        summary = timeline_periods(catalog, kind="source", source_id=source_id)
        assert summary["counts"] == {
            "effective_total": len(expected), "trusted": counts[0],
            "unknown": counts[1], "pending": counts[2],
        }
        result = {
            "source_id": source_id, "schema_version": catalog.schema_version,
            "rows": len(actual), "pages": pages, "exact_order_and_membership": True,
            "duplicate_ids": 0, "counts": summary["counts"],
            "sha256_id_rank_sort": hashlib.sha256(
                json.dumps(actual, separators=(",", ":")).encode()).hexdigest(),
            "oracle": "complete independent SQL membership, Python image suffix predicate, "
                      "full CASE sort; compared every item and decoded cursor anchor across all pages",
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source-id", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.root, args.source_id, args.output), ensure_ascii=False))
