"""SQLite authority for the independent CN facet projection (ADR-0016)."""

from __future__ import annotations

import json
from typing import Any

from photo_search.api.errors import ApiError
from photo_search.catalog.errors import CATALOG_FILTER_REVISION_STALE, PLACE_DATASET_MISSING
from photo_search.geo.cn_admin import CONTRACT, PROCESSOR
from photo_search.places import revision

COLS = {"province": "province_id", "city": "city_id", "district": "district_id"}


def materialize(connection: Any, content: Any, place_mapping: dict[str, int]) -> None:
    if content.cn_admin is None:
        return
    connection.executemany(
        "INSERT OR IGNORE INTO cn_base_china_places VALUES (?,?)",
        [
            (content.dataset_id, place_mapping[key])
            for key, place in content.places.items()
            if place.country_code == "CN"
        ],
    )
    existing = {
        str(row["external_id"]): int(row["admin_id"])
        for row in connection.execute("SELECT admin_id,external_id FROM cn_admin_ids")
    }
    for key in content.cn_admin.rows:
        if key not in existing:
            cursor = connection.execute("INSERT INTO cn_admin_ids(external_id) VALUES (?)", (key,))
            existing[key] = int(cursor.lastrowid)
    for key, row in content.cn_admin.rows.items():
        connection.execute(
            "INSERT OR IGNORE INTO cn_dataset_regions VALUES (?,?,?,?,?,?,?)",
            (
                content.dataset_id,
                existing[key],
                row["kind"],
                row["name"],
                json.dumps(row["aliases"], ensure_ascii=False),
                existing.get(row["parent_province_id"]),
                existing.get(row["parent_city_id"]),
            ),
        )
    for key, link in content.cn_legacy_links.items():
        connection.execute(
            "INSERT OR IGNORE INTO cn_legacy_links VALUES (?,?,?,?)",
            (
                content.dataset_id,
                place_mapping[key],
                existing[link["province"]],
                existing.get(link["city"]),
            ),
        )


def members_sql(kind: str, *, key: str | None = None) -> tuple[str, tuple[Any, ...]]:
    """One authoritative predicate for aggregation, pages and pre-ranking IDs.

    Any unresolved legacy exclusion conservatively blocks ALL CN auto membership.
    This remains live SQL, so manual edits never require recomputing polygons.
    """
    if kind not in COLS:
        raise ApiError("VALIDATION_ERROR", "unsupported administrative facet kind")
    column = COLS[kind]
    restrict = f" AND r.{column} = ?" if key is not None else f" AND r.{column} IS NOT NULL"
    if kind == "province":
        manual_admin = "CASE WHEN r.kind='province' THEN r.admin_id ELSE r.province_id END"
    elif kind == "city":
        manual_admin = "CASE WHEN r.kind='city' THEN r.admin_id ELSE r.city_id END"
    else:
        manual_admin = "CASE WHEN r.kind='district' THEN r.admin_id END"
    manual = f" AND ({manual_admin}) = ?" if key is not None else f" AND ({manual_admin}) IS NOT NULL"
    sql = f"""
        SELECT r.asset_id, r.{column} AS admin_id, 'GPS_ADMIN' AS origin,
               a.created_at AS added_at
          FROM cn_admin_resolutions r JOIN active_geo_dataset active
            ON active.singleton=1 AND active.dataset_id=r.dataset_id
          JOIN assets a ON a.asset_id=r.asset_id AND a.sha256=r.input_revision AND a.state='ACTIVE'
          JOIN asset_geo_facts gf ON gf.asset_id=r.asset_id AND gf.geo_fact_id=r.geo_fact_id
            AND gf.input_revision=r.input_revision AND gf.status='VALID'
            AND gf.datum_status IN ('WGS84','ASSUMED_WGS84')
         WHERE r.processor_id='{PROCESSOR}' {restrict}
           AND EXISTS(SELECT 1 FROM file_locations f WHERE f.asset_id=a.asset_id AND f.availability='PRESENT')
           AND NOT EXISTS(SELECT 1 FROM asset_place_assignments ap WHERE ap.asset_id=a.asset_id
               AND ap.record_state='ACTIVE' AND ap.mode<>'AUTO_ALLOWED')
           AND NOT EXISTS(SELECT 1 FROM asset_place_exclusions x WHERE x.asset_id=a.asset_id AND x.active=1)
    """
    params: tuple[Any, ...] = () if key is None else (admin_key(key),)
    sql += f""" UNION ALL
        SELECT ap.asset_id,({manual_admin}) AS admin_id,'MANUAL' AS origin,
               a.created_at AS added_at
          FROM asset_place_assignments ap JOIN assets a ON a.asset_id=ap.asset_id AND a.state='ACTIVE'
          JOIN places p ON p.place_id=ap.place_id AND p.lifecycle='ACTIVE'
          JOIN cn_admin_place_refs m ON m.place_id=ap.place_id
          JOIN active_geo_dataset active ON active.singleton=1
          JOIN cn_dataset_regions r ON r.dataset_id=active.dataset_id AND r.admin_id=m.admin_id
         WHERE ap.record_state='ACTIVE' AND ap.mode='MANUAL_ASSIGNED' {manual}
           AND EXISTS(SELECT 1 FROM file_locations f WHERE f.asset_id=ap.asset_id AND f.availability='PRESENT')
    """
    if key is not None:
        params += (admin_key(key),)
    if kind != "district":
        legacy_column = "province_id" if kind == "province" else "city_id"
        legacy_restrict = (
            f" AND l.{legacy_column} = ?" if key is not None
            else f" AND l.{legacy_column} IS NOT NULL"
        )
        sql += f""" UNION ALL
            SELECT ap.asset_id,l.{legacy_column} AS admin_id,'MANUAL' AS origin,
                   a.created_at AS added_at
              FROM asset_place_assignments ap JOIN assets a ON a.asset_id=ap.asset_id AND a.state='ACTIVE'
              JOIN places p ON p.place_id=ap.place_id AND p.lifecycle='ACTIVE'
              JOIN cn_legacy_links l ON l.place_id=ap.place_id
              JOIN active_geo_dataset active ON active.singleton=1 AND active.dataset_id=l.dataset_id
             WHERE ap.record_state='ACTIVE' AND ap.mode='MANUAL_ASSIGNED' {legacy_restrict}
               AND EXISTS(SELECT 1 FROM file_locations f WHERE f.asset_id=ap.asset_id AND f.availability='PRESENT')
        """
        if key is not None:
            params += (admin_key(key),)
    return sql, params


def admin_key(key: str) -> int:
    try:
        if not key.startswith("cn:") or not key[3:].isdigit() or int(key[3:]) < 1:
            raise ValueError
        return int(key[3:])
    except (ValueError, AttributeError) as error:
        raise ApiError("VALIDATION_ERROR", "invalid administrative facet key") from error


def revisions(catalog: Any) -> tuple[int, int, str | None]:
    active = catalog.read_one(
        "SELECT a.dataset_id,a.revision,g.content_sha256 FROM active_geo_dataset a LEFT JOIN geo_datasets g ON g.dataset_id=a.dataset_id WHERE singleton=1"
    )
    gps_revision = catalog.read_one("SELECT revision FROM map_member_revision WHERE singleton=1")[
        "revision"
    ]
    package = (
        None
        if active["dataset_id"] is None
        else f"{active['dataset_id']}:{active['content_sha256']}:{active['revision']}:{gps_revision}"
    )
    return (
        revision.read_filter_revision(catalog.connection),
        revision.read_suggestion_revision(catalog.connection),
        package,
    )


def choices(catalog: Any, *, kind: str | None = None) -> list[dict[str, Any]]:
    rows = catalog.read_all(
        """SELECT r.*,p.name AS province,c.name AS city
        FROM cn_dataset_regions r JOIN active_geo_dataset a ON a.singleton=1 AND a.dataset_id=r.dataset_id
        LEFT JOIN cn_dataset_regions p ON p.dataset_id=r.dataset_id AND p.admin_id=r.province_id
        LEFT JOIN cn_dataset_regions c ON c.dataset_id=r.dataset_id AND c.admin_id=r.city_id
        WHERE (? IS NULL OR r.kind=?) ORDER BY r.admin_id""",
        (kind, kind),
    )
    return [
        {
            "kind": row["kind"],
            "key": f"cn:{row['admin_id']}",
            "name": row["name"],
            "parent": " · ".join(str(row[k]) for k in ("province", "city") if row[k]),
            "aliases": json.loads(row["aliases_json"]),
            "contract_version": CONTRACT,
        }
        for row in rows
    ]


def collection(catalog: Any, *, kind: str, name: str | None = None) -> dict[str, Any]:
    from photo_search.places.contracts import normalize_name

    before = revisions(catalog)
    sql, params = members_sql(kind)
    counts = {
        int(row["admin_id"]): row
        for row in catalog.read_all(
            f"SELECT admin_id,COUNT(*) AS total,MIN(asset_id) AS cover,COUNT(DISTINCT origin) AS origins,MIN(origin) AS origin,MAX(added_at) AS latest_added_at FROM ({sql}) GROUP BY admin_id",
            params,
        )
    }
    albums = []
    for item in choices(catalog, kind=kind):
        count = counts.get(admin_key(item["key"]))
        if count is None:
            continue
        if name and normalize_name(name) not in normalize_name(
            " ".join([item["name"], item["parent"], *item["aliases"]])
        ):
            continue
        albums.append(
            dict(
                item,
                total=int(count["total"]),
                cover_asset_id=int(count["cover"]),
                _latest_added_at=int(count["latest_added_at"] or 0),
                origin="MIXED" if count["origins"] > 1 else count["origin"],
            )
        )
    if revisions(catalog) != before:
        raise ApiError(
            CATALOG_FILTER_REVISION_STALE, "the administrative albums changed", status=409
        )
    return {
        "kind": kind,
        "albums": albums,
        "catalog_filter_revision": before[0],
        "suggestion_revision": before[1],
        "package_revision": before[2],
        "contract_version": CONTRACT,
    }


def page(catalog: Any, *, kind: str, key: str, limit: int, cursor: str | None) -> dict[str, Any]:
    from photo_search.places.albums import _decode, _token

    before = revisions(catalog)
    bound = (*before, kind, key, CONTRACT)
    after = _decode(cursor, bound)
    sql, params = members_sql(kind, key=key)
    total = int(catalog.read_one(f"SELECT COUNT(*) AS n FROM ({sql})", params)["n"])
    rows = catalog.read_all(
        f"SELECT asset_id FROM ({sql}) WHERE asset_id>? ORDER BY asset_id LIMIT ?",
        (*params, after, max(1, min(limit, 100)) + 1),
    )
    selected = rows[: max(1, min(limit, 100))]
    if revisions(catalog) != before:
        raise ApiError(
            CATALOG_FILTER_REVISION_STALE, "the administrative album changed", status=409
        )
    token = _token(int(selected[-1]["asset_id"]), bound) if len(rows) > len(selected) else None
    return {
        "assets": [
            {
                "asset_id": int(row["asset_id"]),
                "thumbnail_url": f"/api/v1/assets/{row['asset_id']}/thumbnail",
                "detail_url": f"/api/v1/assets/{row['asset_id']}",
            }
            for row in selected
        ],
        "total": total,
        "next_cursor": token,
        "catalog_filter_revision": before[0],
        "suggestion_revision": before[1],
        "package_revision": before[2],
        "contract_version": CONTRACT,
    }


def asset_projection(catalog: Any, asset_id: int) -> dict[str, Any]:
    """Explain live membership without changing immutable machine or manual facts."""
    asset = catalog.read_one("SELECT state FROM assets WHERE asset_id=?", (asset_id,))
    if asset is None:
        raise ApiError("ASSET_NOT_FOUND", "unknown asset", status=404)
    before = revisions(catalog)
    facets = []
    origin = None
    for kind in COLS:
        sql, params = members_sql(kind)
        row = catalog.read_one(
            f"SELECT m.origin,r.admin_id,r.name FROM ({sql}) m "
            "JOIN cn_dataset_regions r ON r.admin_id=m.admin_id "
            "JOIN active_geo_dataset active ON active.singleton=1 AND active.dataset_id=r.dataset_id "
            "WHERE m.asset_id=?",
            (*params, asset_id),
        )
        if row is not None:
            origin = row["origin"]
            facets.append({"kind": kind, "key": f"cn:{row['admin_id']}", "name": row["name"]})
    control = catalog.read_one(
        "SELECT mode FROM asset_place_assignments WHERE asset_id=? AND record_state='ACTIVE'",
        (asset_id,),
    )
    mode = control["mode"] if control else "AUTO_ALLOWED"
    if facets:
        if origin == "MANUAL":
            reason = "MANUAL_SOURCE_LINK"
        else:
            matched = catalog.read_one(
                "SELECT r.reason FROM cn_admin_resolutions r "
                "JOIN active_geo_dataset active ON active.singleton=1 AND active.dataset_id=r.dataset_id "
                "JOIN asset_geo_facts gf ON gf.asset_id=r.asset_id AND gf.geo_fact_id=r.geo_fact_id "
                "AND gf.input_revision=r.input_revision WHERE r.asset_id=? AND r.processor_id=?",
                (asset_id, PROCESSOR),
            )
            reason = matched["reason"]
    elif mode == "MANUAL_UNASSIGNED":
        reason = "MANUAL_UNASSIGNED"
    elif mode == "MANUAL_ASSIGNED":
        reason = "NO_PROVEN_MANUAL_ADMIN_LINK"
    elif catalog.read_one(
        "SELECT 1 FROM asset_place_exclusions WHERE asset_id=? AND active=1", (asset_id,)
    ):
        reason = "ACTIVE_EXCLUSION_CONSERVATIVE"
    else:
        resolution = catalog.read_one(
            "SELECT r.reason FROM cn_admin_resolutions r "
            "JOIN active_geo_dataset active ON active.singleton=1 AND active.dataset_id=r.dataset_id "
            "JOIN assets a ON a.asset_id=r.asset_id AND a.sha256=r.input_revision AND a.state='ACTIVE' "
            "JOIN asset_geo_facts gf ON gf.asset_id=r.asset_id AND gf.geo_fact_id=r.geo_fact_id "
            "AND gf.input_revision=r.input_revision AND gf.status='VALID' "
            "AND gf.datum_status IN ('WGS84','ASSUMED_WGS84') "
            "WHERE r.asset_id=? AND r.processor_id=? "
            "AND EXISTS(SELECT 1 FROM file_locations f WHERE f.asset_id=a.asset_id AND f.availability='PRESENT')",
            (asset_id, PROCESSOR),
        )
        reason = resolution["reason"] if resolution else "NO_CURRENT_RESOLUTION"
    if revisions(catalog) != before:
        raise ApiError(CATALOG_FILTER_REVISION_STALE, "the asset places changed", status=409)
    return {
        "contract_version": CONTRACT,
        "asset_id": asset_id,
        "origin": origin,
        "reason": reason,
        "facets": facets,
        "package_revision": before[2],
    }


def start(catalog: Any, dataset_id: str) -> None:
    upper = int(catalog.read_one("SELECT COALESCE(MAX(asset_id),0) AS n FROM assets")["n"])
    with catalog.write() as connection:
        active = connection.execute(
            "SELECT dataset_id FROM active_geo_dataset WHERE singleton=1"
        ).fetchone()
        if active["dataset_id"] != dataset_id:
            raise ApiError(CATALOG_FILTER_REVISION_STALE, "the place package changed", status=409)
        connection.execute(
            "INSERT INTO cn_update_runs VALUES (?,0,?,0,0,0,1) ON CONFLICT(dataset_id) DO UPDATE SET after_asset_id=0,upper_asset_id=excluded.upper_asset_id,scanned=0,processed=0,complete=0,generation=generation+1",
            (dataset_id, upper),
        )


def inputs(
    catalog: Any, content: Any, *, restart: bool = False
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if content.cn_admin is None:
        raise ApiError(
            PLACE_DATASET_MISSING, "the active package has no Chinese boundaries", status=409
        )
    if restart:
        start(catalog, content.dataset_id)
    run = catalog.read_one("SELECT * FROM cn_update_runs WHERE dataset_id=?", (content.dataset_id,))
    if run is None:
        start(catalog, content.dataset_id)
        run = catalog.read_one(
            "SELECT * FROM cn_update_runs WHERE dataset_id=?", (content.dataset_id,)
        )
    rows = catalog.read_all(
        """SELECT gf.*,r.asset_id AS already_resolved
        FROM asset_geo_facts gf JOIN assets a ON a.asset_id=gf.asset_id AND a.sha256=gf.input_revision
        LEFT JOIN cn_admin_resolutions r ON r.asset_id=gf.asset_id AND r.dataset_id=?
            AND r.geo_fact_id=gf.geo_fact_id AND r.input_revision=gf.input_revision AND r.processor_id=?
        WHERE gf.asset_id>? AND gf.asset_id<=? AND gf.status='VALID'
            AND gf.datum_status IN ('WGS84','ASSUMED_WGS84')
        ORDER BY gf.asset_id LIMIT 100""",
        (content.dataset_id, PROCESSOR, run["after_asset_id"], run["upper_asset_id"]),
    )
    return dict(run), [dict(row) for row in rows]


def calculate(
    content: Any, rows: list[dict[str, Any]]
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    return [
        (row, content.cn_admin.resolve(float(row["latitude"]), float(row["longitude"])))
        for row in rows
        if row["already_resolved"] is None
    ]


def publish(
    catalog: Any, content: Any, run: dict[str, Any], rows: list[dict[str, Any]], results: list[Any]
) -> dict[str, Any]:
    added = 0
    with catalog.write() as connection:
        active = connection.execute(
            "SELECT dataset_id FROM active_geo_dataset WHERE singleton=1"
        ).fetchone()
        current = connection.execute(
            "SELECT * FROM cn_update_runs WHERE dataset_id=?", (content.dataset_id,)
        ).fetchone()
        if active["dataset_id"] != content.dataset_id or dict(current) != run:
            raise ApiError(
                CATALOG_FILTER_REVISION_STALE,
                "the Chinese place update changed; resume",
                status=409,
            )
        mapping = {
            row["external_id"]: int(row["admin_id"])
            for row in connection.execute("SELECT * FROM cn_admin_ids")
        }
        for row, result in results:
            fact = connection.execute(
                "SELECT gf.geo_fact_id,gf.input_revision FROM asset_geo_facts gf JOIN assets a ON a.asset_id=gf.asset_id AND a.sha256=gf.input_revision WHERE gf.asset_id=? AND gf.status='VALID' AND gf.datum_status IN ('WGS84','ASSUMED_WGS84')",
                (row["asset_id"],),
            ).fetchone()
            if fact is None or tuple(fact) != (row["geo_fact_id"], row["input_revision"]):
                continue  # a changed input cannot receive a late result
            cursor = connection.execute(
                "INSERT OR IGNORE INTO cn_admin_resolutions VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    row["asset_id"],
                    content.dataset_id,
                    row["geo_fact_id"],
                    row["input_revision"],
                    PROCESSOR,
                    mapping.get(result["province"]),
                    mapping.get(result["city"]),
                    mapping.get(result["district"]),
                    result["reason"],
                    catalog.now_ms(),
                ),
            )
            added += cursor.rowcount
        complete = len(rows) < 100
        after = int(rows[-1]["asset_id"]) if rows else int(run["upper_asset_id"])
        connection.execute(
            "UPDATE cn_update_runs SET after_asset_id=?,scanned=scanned+?,processed=processed+?,complete=? WHERE dataset_id=?",
            (after, len(rows), added, int(complete), content.dataset_id),
        )
        if added:
            revision.bump_suggestion_revision(
                connection, cause="CN_ADMIN_CHANGED", now_ms=catalog.now_ms()
            )
    return {
        "contract_version": CONTRACT,
        "dataset_id": content.dataset_id,
        "processed": added,
        "scanned": len(rows),
        "total_processed": run["processed"] + added,
        "total_scanned": run["scanned"] + len(rows),
        "complete": complete,
    }
