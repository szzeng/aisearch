"""Read-only, database-only inventory of registered non-image locations."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path

from photo_search.catalog.image_policy import IMAGE_EXTENSIONS, image_location_sql
from photo_search.catalog.video import VIDEO_EXTENSIONS


def probe(database: Path, *, examples: int = 4) -> dict[str, object]:
    uri = database.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        def video_sql(alias: str) -> str:
            return "(" + " OR ".join(
                f"LOWER({alias}.relative_path) GLOB '*{extension}'"
                for extension in sorted(VIDEO_EXTENSIONS)
            ) + ")"

        rows = connection.execute(
            f"SELECT s.source_id, s.display_name, "
            f"COUNT(l.location_id) AS all_locations, "
            f"SUM(CASE WHEN l.availability='PRESENT' THEN 1 ELSE 0 END) AS all_present, "
            f"SUM(CASE WHEN l.availability='MISSING' THEN 1 ELSE 0 END) AS all_missing, "
            f"SUM(CASE WHEN l.location_id IS NOT NULL AND NOT ({image_location_sql('l')}) "
            f"THEN 1 ELSE 0 END) AS non_image_locations, "
            f"SUM(CASE WHEN NOT ({image_location_sql('l')}) AND l.availability='PRESENT' "
            f"THEN 1 ELSE 0 END) AS non_image_present, "
            f"SUM(CASE WHEN NOT ({image_location_sql('l')}) AND l.availability='MISSING' "
            f"THEN 1 ELSE 0 END) AS non_image_missing, "
            f"COUNT(DISTINCT l.asset_id) AS assets, "
            f"COUNT(DISTINCT CASE WHEN NOT EXISTS (SELECT 1 FROM file_locations i "
            f"WHERE i.asset_id=l.asset_id AND {image_location_sql('i')}) "
            f"THEN l.asset_id END) AS non_image_only_assets, "
            f"COUNT(DISTINCT CASE WHEN EXISTS (SELECT 1 FROM file_locations i "
            f"WHERE i.asset_id=l.asset_id AND {image_location_sql('i')}) "
            f"THEN l.asset_id END) AS image_non_image_shared_assets, "
            f"SUM(CASE WHEN {video_sql('l')} THEN 1 ELSE 0 END) AS locations, "
            f"SUM(CASE WHEN {video_sql('l')} AND l.availability='PRESENT' THEN 1 ELSE 0 END) AS present, "
            f"SUM(CASE WHEN {video_sql('l')} AND l.availability='MISSING' THEN 1 ELSE 0 END) AS missing, "
            f"COUNT(DISTINCT CASE WHEN {video_sql('l')} AND NOT EXISTS (SELECT 1 FROM file_locations v "
            f"WHERE v.asset_id=l.asset_id AND NOT {video_sql('v')}) THEN l.asset_id END) AS video_only_assets, "
            f"COUNT(DISTINCT CASE WHEN {video_sql('l')} AND EXISTS (SELECT 1 FROM file_locations v "
            f"WHERE v.asset_id=l.asset_id AND NOT {video_sql('v')}) THEN l.asset_id END) AS shared_assets, "
            f"SUM(CASE WHEN {image_location_sql('l')} THEN 1 ELSE 0 END) AS image_locations, "
            f"COUNT(DISTINCT CASE WHEN {image_location_sql('l')} THEN l.asset_id END) AS image_assets "
            f"FROM source_roots s LEFT JOIN file_locations l ON l.source_id=s.source_id "
            f"GROUP BY s.source_id ORDER BY s.source_id"
        ).fetchall()
        output = []
        for row in rows:
            source_id = int(row["source_id"])
            samples = connection.execute(
                f"SELECT l.asset_id, l.relative_path, l.availability FROM file_locations l "
                f"WHERE l.source_id=? AND NOT ({image_location_sql('l')}) ORDER BY l.location_id LIMIT ?",
                (source_id, max(0, min(int(examples), 10))),
            ).fetchall()
            output.append({**dict(row), "examples": [dict(sample) for sample in samples]})
            extensions: Counter[str] = Counter()
            for location in connection.execute(
                f"SELECT relative_path FROM file_locations l WHERE l.source_id=? "
                f"AND NOT ({image_location_sql('l')})",
                (source_id,),
            ):
                suffix = Path(str(location["relative_path"])).suffix.casefold() or "[no extension]"
                extensions[suffix] += 1
            output[-1]["non_image_extensions_top"] = [
                {"extension": extension, "locations": count}
                for extension, count in extensions.most_common(10)
            ]
        return {"database": database.name, "image_extensions": sorted(IMAGE_EXTENSIONS), "sources": output}
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
