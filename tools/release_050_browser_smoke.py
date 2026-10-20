"""Disposable synthetic retention UI fixture for the 0.5.0 source beta.

Run with the project development environment. No real source/model path is used.
The server uses the normal launcher/session/auth; injections only delay/fail a
synthetic cleanup item to make progress, restart and retry observable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / ".tmp" / "P4-RELEASE-050" / "browser-retention"
DATA = ROOT / "PhotoSearchData"


def audit() -> dict:
    with sqlite3.connect(f"file:{DATA / 'catalog/catalog.db'}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        return {
            "assets": [row[0] for row in db.execute("SELECT asset_id FROM assets ORDER BY asset_id")],
            "favorites": [tuple(row) for row in db.execute("SELECT * FROM asset_favorites")],
            "runs": [dict(row) for row in db.execute("SELECT * FROM library_retention_runs ORDER BY created_at_ms")],
            "items": [dict(row) for row in db.execute("SELECT * FROM library_retention_run_items")],
            "barriers": [dict(row) for row in db.execute("SELECT * FROM asset_retention_barriers")],
            "outbox": [dict(row) for row in db.execute("SELECT * FROM index_outbox")],
            "original_hash": hashlib.sha256((ROOT / "source/present.jpg").read_bytes()).hexdigest(),
        }


def seed() -> None:
    if ROOT.exists():
        raise SystemExit(f"refusing to overwrite existing fixture: {ROOT}")
    sys.path[:0] = [str(REPO / "tests"), str(REPO / "tests/integration")]
    from conftest import write_jpeg
    from test_p4_library_retention import _seed_asset, _seed_retention_eligible

    from photo_search.ingestion.service import CatalogService

    ROOT.mkdir(parents=True)
    source = ROOT / "source"
    source.mkdir()
    original = write_jpeg(source / "present.jpg")
    with CatalogService.open(data_root=DATA) as service:
        source_id = service.register_source(display_name="release-050-synthetic", root_path=source)["source_id"]
        catalog = service.catalog
        now = int(catalog.now_ms())
        for asset_id in range(1, 66):
            _seed_retention_eligible(catalog, asset_id, source_id, now_ms=now)
        _seed_asset(catalog, 66, locations=((source_id, "PRESENT"),))
        with catalog.write() as db:
            db.execute("INSERT INTO asset_favorites(asset_id,added_at_ms) VALUES(65,?)", (now,))
            db.execute("UPDATE file_locations SET relative_path='present.jpg',path_key='present.jpg' WHERE asset_id=66")
            db.execute("UPDATE assets SET sha256=?,byte_size=? WHERE asset_id=66", (hashlib.sha256(original.read_bytes()).hexdigest(), original.stat().st_size))
    (ROOT / "baseline.json").write_text(json.dumps(audit(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"fixture": str(ROOT), "assets": 66, "missing": 65, "favorite": 65, "present": 66}))


def serve(port: int) -> None:
    if not (ROOT / "baseline.json").is_file():
        raise SystemExit("run seed first")
    from photo_search import library_retention
    from photo_search.catalog.errors import CatalogError

    original = library_retention.begin_asset_retention_cleanup

    def injected(catalog, *, asset_id, now_ms, manual_early=False):
        if int(asset_id) == 21:
            deadline = time.monotonic() + 120
            while not (ROOT / "continue-batch").exists() and time.monotonic() < deadline:
                time.sleep(0.1)
        if int(asset_id) == 1 and not (ROOT / "fault-off").exists():
            raise CatalogError("SYNTHETIC_INJECTED_FAILURE", "release-050 synthetic retry failure")
        return original(catalog, asset_id=asset_id, now_ms=now_ms, manual_early=manual_early)

    library_retention.begin_asset_retention_cleanup = injected
    sys.argv = ["photo-search", "--host", "127.0.0.1", "--port", str(port), "--data-root", str(DATA)]
    from photo_search.__main__ import main

    main()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("seed", "audit", "serve"))
    parser.add_argument("--port", type=int, default=18770)
    args = parser.parse_args()
    if args.action == "seed":
        seed()
    elif args.action == "audit":
        print(json.dumps(audit(), ensure_ascii=False, indent=2))
    else:
        serve(args.port)
