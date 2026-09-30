"""Offline polygon classification. Geometry work precedes short catalog transactions.

Administrative packages form one hierarchy. Scenic packages are independent overlays:
a lake spanning two cities is one area, without claiming that either city contains it.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from shapely import STRtree, from_wkb, to_wkb
from shapely.errors import ShapelyError
from shapely.geometry import Point, shape

from photo_search.catalog.db import Catalog
from photo_search.places import directory, revision
from photo_search.places.contracts import fail

FORMAT = "photo-search-boundaries.v1"
MAX_PACKAGE_BYTES = 64 * 1024 * 1024
ADMIN_KINDS = ("country", "province", "city", "district")


def parse_package(raw: bytes) -> tuple[dict, list[tuple[dict, bytes | None]]]:
    if len(raw) > MAX_PACKAGE_BYTES:
        raise fail("VALIDATION_ERROR", "边界包超过 64 MB 上限")
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or not isinstance(data.get("metadata"), dict):
            raise ValueError("边界包及 metadata 必须为对象")
        meta = data["metadata"]
        if data["format"] != FORMAT or meta["crs"] != "EPSG:4326":
            raise ValueError("需要 WGS84 边界包")
        for key in ("namespace", "version", "source", "license", "coverage", "boundary_year"):
            if not isinstance(meta[key], str) or not 1 <= len(meta[key]) <= 1000:
                raise ValueError(f"缺少有效的 {key}")
        if urlsplit(meta["source"]).scheme not in ("https", "http"):
            raise ValueError("数据来源必须为 HTTP(S) 链接")
        if meta["layer"] not in ("administrative", "scenic"):
            raise ValueError("不支持的边界图层")
        features = data["features"]
        if not isinstance(features, list) or not 1 <= len(features) <= 100_000:
            raise ValueError("地点数量无效")
        known: dict[str, dict] = {}
        parsed = []
        for feature in features:
            if not isinstance(feature, dict):
                raise ValueError("地点必须为对象")
            key, kind, name = feature["id"], feature["kind"], feature["name"]
            if not isinstance(key, str) or not 1 <= len(key) <= 200 or key in known:
                raise ValueError("地点标识无效或重复")
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 200:
                raise ValueError("地点名称无效")
            allowed = ADMIN_KINDS if meta["layer"] == "administrative" else ("attraction",)
            if kind not in allowed:
                raise ValueError("地点类型与图层不一致")
            parent = feature.get("parent")
            if parent is not None:
                if parent not in known or known[parent]["kind"] not in directory.PARENT_KINDS[kind]:
                    raise ValueError("父地点必须先定义，且行政层级合法")
            if meta["layer"] == "scenic" and parent is not None:
                raise ValueError("景区是独立范围，不指定行政父级")
            geometry = feature.get("geometry")
            wkb = None
            if geometry is not None:
                if not isinstance(geometry, dict) or geometry.get("type") not in (
                    "Polygon", "MultiPolygon"
                ):
                    raise ValueError("geometry 必须为 GeoJSON Polygon/MultiPolygon 对象")
                geom = shape(geometry)
                if (
                    geom.geom_type not in ("Polygon", "MultiPolygon")
                    or geom.is_empty
                    or not geom.is_valid
                ):
                    raise ValueError("边界必须是有效、非空的 Polygon/MultiPolygon")
                if not all(math.isfinite(v) for v in geom.bounds):
                    raise ValueError("边界坐标无效")
                west, south, east, north = geom.bounds
                if west < -180 or east > 180 or south < -90 or north > 90:
                    raise ValueError("边界坐标超出 WGS84 范围")
                # RFC 7946 date-line polygons must be split, not drawn across the world.
                for part in [geom] if geom.geom_type == "Polygon" else geom.geoms:
                    if part.bounds[2] - part.bounds[0] > 180:
                        raise ValueError("跨日期变更线的边界须拆成 MultiPolygon")
                wkb = to_wkb(geom)
            known[key] = feature
            parsed.append((feature, wkb))
        if not any(wkb for _, wkb in parsed):
            raise ValueError("没有可用于定位的边界")
    except (KeyError, TypeError, ValueError, IndexError, ShapelyError) as exc:
        raise fail("VALIDATION_ERROR", f"无效边界包：{exc}") from exc
    return meta, parsed


def install(catalog: Catalog, raw: bytes) -> dict[str, Any]:
    meta, features = parse_package(raw)
    digest = hashlib.sha256(raw).hexdigest()
    package_id = "boundary:" + digest
    now = catalog.now_ms()
    with catalog.write() as db:
        if db.execute(
            "SELECT 1 FROM boundary_packages WHERE namespace=? AND layer<>?",
            (meta["namespace"], meta["layer"]),
        ).fetchone():
            raise fail("VALIDATION_ERROR", "同一边界数据源不能改变图层类型")
        existing = db.execute(
            "SELECT * FROM boundary_packages WHERE package_id=?", (package_id,)
        ).fetchone()
        if existing and existing["active"]:
            return status(db)
        previous = db.execute(
            "SELECT package_id FROM boundary_packages WHERE namespace=? AND active=1",
            (meta["namespace"],),
        ).fetchone()
        # Only one administrative hierarchy is active; independent scenic overlays may coexist.
        if (
            meta["layer"] == "administrative"
            and db.execute(
                "SELECT 1 FROM boundary_packages WHERE layer='administrative' AND active=1 AND namespace<>?",
                (meta["namespace"],),
            ).fetchone()
        ):
            raise fail("VALIDATION_ERROR", "请使用同一行政边界包的更新版本，避免重复行政体系")
        affected = (
            []
            if not previous
            else [
                r[0]
                for r in db.execute(
                    "SELECT DISTINCT asset_id FROM boundary_memberships WHERE package_id=?",
                    (previous[0],),
                )
            ]
        )
        db.execute("UPDATE boundary_packages SET active=0 WHERE namespace=?", (meta["namespace"],))
        if existing:
            db.execute("UPDATE boundary_packages SET active=1 WHERE package_id=?", (package_id,))
        else:
            db.execute(
                "INSERT INTO boundary_packages VALUES (?,?,?,?,?,?,1,?)",
                (
                    package_id,
                    meta["namespace"],
                    meta["version"],
                    digest,
                    meta["layer"],
                    json.dumps(meta, ensure_ascii=False),
                    now,
                ),
            )
            ids = {}
            provider = "boundary:" + meta["namespace"]
            for feature, wkb in features:
                external = feature["id"]
                parent = ids.get(feature.get("parent"))
                old = db.execute(
                    "SELECT place_id FROM place_external_refs WHERE provider=? AND external_id=?",
                    (provider, external),
                ).fetchone()
                if old:
                    place_id = int(old[0])
                    row = directory.get_place_row(db, place_id)
                    if row["kind"] != feature["kind"] or row["parent_place_id"] != parent:
                        raise fail(
                            "VALIDATION_ERROR", "区划层级变化须使用新的地点标识，不能改写已有身份"
                        )
                else:
                    # IDs, never names, identify imports. User places are never merged silently.
                    place_id = directory.create_place(
                        db,
                        kind=feature["kind"],
                        canonical_name=feature["name"],
                        parent_place_id=parent,
                        origin="DATASET",
                        now_ms=now,
                        external_refs=[{"provider": provider, "external_id": external}],
                    )
                ids[external] = place_id
                if wkb:
                    db.execute(
                        "INSERT INTO boundary_features VALUES (?,?,?)", (package_id, place_id, wkb)
                    )
        # Switching a package invalidates old automatic results immediately. Recalculation is
        # resumable in bounded batches; manual assignments and operations are untouched.
        affected = sorted(
            set(affected)
            | {
                r[0]
                for r in db.execute(
                    "SELECT DISTINCT asset_id FROM boundary_memberships WHERE package_id=?",
                    (package_id,),
                )
            }
        )
        _changed(catalog, db, affected, now, cause="PLACE_BOUNDARY_PACKAGE_CHANGED", always=True)
    return status(catalog.connection)


def status(db: sqlite3.Connection) -> dict[str, Any]:
    packages = []
    for row in db.execute(
        "SELECT * FROM boundary_packages WHERE active=1 ORDER BY layer,namespace"
    ):
        item = json.loads(row["metadata_json"])
        item["package_id"] = row["package_id"]
        item["feature_count"] = db.execute(
            "SELECT COUNT(*) FROM boundary_features WHERE package_id=?", (row["package_id"],)
        ).fetchone()[0]
        item["pending"] = db.execute(
            "SELECT COUNT(*) FROM asset_geo_facts g JOIN assets a USING(asset_id) "
            "WHERE NOT EXISTS(SELECT 1 FROM boundary_results r WHERE r.package_id=? "
            "AND r.asset_id=g.asset_id AND r.geo_fact_id=g.geo_fact_id AND r.input_revision=a.sha256)",
            (row["package_id"],),
        ).fetchone()[0]
        packages.append(item)
    return {"available": bool(packages), "packages": packages}


def _changed(catalog, db, assets, now, *, cause="PLACE_EFFECTIVE_ASSIGNMENT_CHANGED", always=False):
    if not assets and not always:
        return
    db.executemany(
        "INSERT INTO search_projection_queue VALUES (?,'PLACE_ASSIGNMENT_CHANGED',?) "
        "ON CONFLICT(asset_id) DO UPDATE SET cause=excluded.cause,queued_at=excluded.queued_at",
        [(asset, now) for asset in assets],
    )
    revision.bump_filter_revision(
        db,
        cause=cause,
        now_ms=now,
        asset_ids=assets,
        refresh=revision.projection_refresher(catalog),
    )
    revision.bump_suggestion_revision(db, cause=cause, now_ms=now)


@dataclass
class BoundaryIndex:
    tree: STRtree
    place_ids: list[int]
    chains: dict[int, list[int]]
    layer: str

    @classmethod
    def load(cls, db, package):
        rows = db.execute(
            "SELECT place_id,geometry_wkb FROM boundary_features WHERE package_id=? ORDER BY place_id",
            (package["package_id"],),
        ).fetchall()
        ids = [int(r[0]) for r in rows]
        return cls(
            STRtree([from_wkb(r[1]) for r in rows]),
            ids,
            {
                pid: [p["place_id"] for p in directory.ancestor_views(db, pid)] + [pid]
                for pid in ids
            },
            package["layer"],
        )

    def locate(self, longitude: float, latitude: float) -> tuple[str, list[int]]:
        # covered_by includes the exact boundary: two neighbours => common ancestor,
        # never whichever feature happened to be first in the file.
        hits = [
            self.place_ids[int(i)]
            for i in self.tree.query(Point(longitude, latitude), predicate="covered_by")
        ]
        if not hits:
            return "NO_MATCH", []
        if self.layer == "scenic":
            return "MATCHED", sorted(hits)
        chains = [self.chains[pid] for pid in hits]
        leaves = [pid for pid in hits if not any(pid in c[:-1] for c in chains)]
        if len(leaves) == 1:
            return "MATCHED", leaves
        common = set(self.chains[leaves[0]])
        for pid in leaves[1:]:
            common.intersection_update(self.chains[pid])
        chain = [pid for pid in self.chains[leaves[0]] if pid in common]
        return "AMBIGUOUS", chain[-1:]


def resolve_batch(catalog: Catalog, *, limit: int = 200, cache: dict | None = None) -> int:
    """Process only stored GPS facts. Never opens or scans photo files."""
    cache = cache if cache is not None else {}
    db = catalog.connection
    packages = db.execute(
        "SELECT * FROM boundary_packages WHERE active=1 ORDER BY package_id"
    ).fetchall()
    active = {p["package_id"] for p in packages}
    for key in list(cache):
        if key not in active:
            del cache[key]
    handled = 0
    for package in packages:
        package_id = package["package_id"]
        facts = db.execute(
            "SELECT g.*,a.sha256 FROM asset_geo_facts g JOIN assets a USING(asset_id) "
            "WHERE NOT EXISTS(SELECT 1 FROM boundary_results r WHERE r.package_id=? "
            "AND r.asset_id=g.asset_id AND r.geo_fact_id=g.geo_fact_id AND r.input_revision=a.sha256) "
            "ORDER BY g.asset_id LIMIT ?",
            (package_id, limit),
        ).fetchall()
        if not facts:
            continue
        index = cache.get(package_id)
        if index is None:
            index = cache[package_id] = BoundaryIndex.load(db, package)
        computed = []
        for fact in facts:
            # EXIF commonly omits datum. We use WGS84 as an explicit assumption and
            # expose that assumption in detail; known foreign CRSs are never accepted.
            valid = (
                fact["status"] == "VALID"
                and fact["datum_status"] in ("WGS84", "ASSUMED_WGS84")
                and fact["input_revision"] == fact["sha256"]
            )
            outcome, ids = (
                index.locate(fact["longitude"], fact["latitude"]) if valid else ("UNSUPPORTED", [])
            )
            computed.append((fact, outcome, ids))
        now = catalog.now_ms()
        with catalog.write() as connection:
            if not connection.execute(
                "SELECT 1 FROM boundary_packages WHERE package_id=? AND active=1", (package_id,)
            ).fetchone():
                continue
            affected = []
            for fact, outcome, ids in computed:
                asset = int(fact["asset_id"])
                current = connection.execute(
                    "SELECT g.geo_fact_id,a.sha256 FROM asset_geo_facts g "
                    "JOIN assets a USING(asset_id) WHERE g.asset_id=?",
                    (asset,),
                ).fetchone()
                if not current or tuple(current) != (fact["geo_fact_id"], fact["sha256"]):
                    continue
                before = effective_ids(connection, asset)
                connection.execute(
                    "DELETE FROM boundary_memberships WHERE package_id=? AND asset_id=?",
                    (package_id, asset),
                )
                connection.execute(
                    "INSERT INTO boundary_results VALUES (?,?,?,?,?,?) "
                    "ON CONFLICT(package_id,asset_id) DO UPDATE SET geo_fact_id=excluded.geo_fact_id,"
                    "input_revision=excluded.input_revision,status=excluded.status,updated_at=excluded.updated_at",
                    (package_id, asset, fact["geo_fact_id"], fact["sha256"], outcome, now),
                )
                connection.executemany(
                    "INSERT INTO boundary_memberships VALUES (?,?,?)",
                    [(package_id, asset, pid) for pid in ids],
                )
                if effective_ids(connection, asset) != before:
                    affected.append(asset)
                handled += 1
            _changed(catalog, connection, affected, now)
    return handled


def effective_ids(db, asset_id):
    return tuple(
        r[0]
        for r in db.execute(
            "SELECT DISTINCT place_id FROM effective_asset_places WHERE asset_id=? ORDER BY place_id",
            (asset_id,),
        )
    )


def automatic_view(db, asset_id):
    return [
        dict(r)
        for r in db.execute(
            "SELECT r.status,b.version,b.layer,g.datum_status FROM boundary_results r "
            "JOIN boundary_packages b USING(package_id) "
            "JOIN asset_geo_facts g ON g.asset_id=r.asset_id AND g.geo_fact_id=r.geo_fact_id "
            "JOIN assets a ON a.asset_id=r.asset_id AND a.sha256=r.input_revision "
            "WHERE r.asset_id=? AND b.active=1 ORDER BY b.layer",
            (asset_id,),
        )
    ]
