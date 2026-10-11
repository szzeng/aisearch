"""Read-only, database-only inventory of registered video locations."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from photo_search.catalog.video import VIDEO_EXTENSIONS, non_video_location_sql


def probe(database: Path, *, examples: int = 4) -> dict[str, object]:
    uri = database.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        def video(alias: str) -> str:
            return "NOT (" + " OR ".join(
                f"LOWER({alias}.relative_path) GLOB '*{extension}'"
                for extension in sorted(VIDEO_EXTENSIONS)
            ) + ")"

        photo = non_video_location_sql("p")
        rows = connection.execute(
            f"SELECT s.source_id, s.display_name, "
            f"COUNT(l.location_id) AS locations, "
            f"SUM(CASE WHEN l.availability='PRESENT' THEN 1 ELSE 0 END) AS present, "
            f"SUM(CASE WHEN l.availability='MISSING' THEN 1 ELSE 0 END) AS missing, "
            f"COUNT(DISTINCT l.asset_id) AS assets, "
            f"COUNT(DISTINCT CASE WHEN EXISTS (SELECT 1 FROM file_locations v "
            f"WHERE v.asset_id=l.asset_id AND NOT ({video('v')})) AND NOT EXISTS "
            f"(SELECT 1 FROM file_locations p WHERE p.asset_id=l.asset_id AND ({photo})) "
            f"THEN l.asset_id END) AS video_only_assets, "
            f"COUNT(DISTINCT CASE WHEN EXISTS (SELECT 1 FROM file_locations v "
            f"WHERE v.asset_id=l.asset_id AND NOT ({video('v')})) AND EXISTS "
            f"(SELECT 1 FROM file_locations p WHERE p.asset_id=l.asset_id AND ({photo})) "
            f"THEN l.asset_id END) AS shared_assets "
            f"FROM source_roots s LEFT JOIN file_locations l ON l.source_id=s.source_id "
            f"AND NOT ({video('l')}) GROUP BY s.source_id ORDER BY s.source_id"
        ).fetchall()
        output = []
        for row in rows:
            source_id = int(row["source_id"])
            samples = connection.execute(
                f"SELECT l.asset_id, l.relative_path, l.availability FROM file_locations l "
                f"WHERE l.source_id=? AND NOT ({video('l')}) ORDER BY l.location_id LIMIT ?",
                (source_id, max(0, min(int(examples), 10))),
            ).fetchall()
            output.append({**dict(row), "examples": [dict(sample) for sample in samples]})
        return {"database": database.name, "sources": output}
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path, help="Existing catalog SQLite database")
    parser.add_argument("--examples", type=int, default=4)
    args = parser.parse_args()
    print(json.dumps(probe(args.database, examples=args.examples), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
