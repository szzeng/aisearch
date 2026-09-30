from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest
from shapely.geometry import Polygon, box, mapping

from photo_search.catalog import schema
from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import CatalogError
from photo_search.geo.boundaries import (
    BoundaryIndex,
    effective_ids,
    install,
    parse_package,
    resolve_batch,
)
from photo_search.places.browse import browse
from photo_search.places.filter import asset_place_view, coverage, place_condition
from photo_search.places.service import PlaceService
from photo_search.search.documents import place_values


def package(*, version="1", scenic=False, geometry=None):
    meta = dict(
        namespace="scenery" if scenic else "admin",
        version=version,
        crs="EPSG:4326",
        source="https://example.org/boundaries",
        license="CC0",
        coverage="Synthetic test only",
        boundary_year="2026",
        layer="scenic" if scenic else "administrative",
    )
    features = [
        dict(id="cn", kind="country", name="国家"),
        dict(id="pr", kind="province", name="省", parent="cn"),
        dict(id="hz", kind="city", name="杭州", parent="pr"),
        dict(id="other", kind="city", name="另一城市", parent="pr"),
        dict(
            id="west", kind="district", name="西湖", parent="hz", geometry=mapping(box(0, 0, 2, 2))
        ),
        dict(
            id="east",
            kind="district",
            name="西湖",
            parent="other",
            geometry=mapping(box(2, 0, 4, 2)),
        ),
    ]
    if scenic:
        features = [
            dict(id="lake", kind="attraction", name="跨市景区", geometry=mapping(box(1, 0, 3, 2)))
        ]
    if geometry:
        features[-1]["geometry"] = geometry
    return json.dumps(
        dict(format="photo-search-boundaries.v1", metadata=meta, features=features)
    ).encode()


def seed(catalog, points):
    with catalog.write() as db:
        for i, (x, y, datum) in enumerate(points, 1):
            sha = hashlib.sha256(str(i).encode()).hexdigest()
            db.execute(
                "INSERT INTO assets(asset_id,sha256,byte_size,created_at,state) VALUES(?,?,1,1,'ACTIVE')",
                (i, sha),
            )
            db.execute(
                "INSERT INTO asset_geo_facts VALUES(?,?,NULL,?,1,'VALID','EXIF_GPS','test',?,'{}',?,?,?,1,1)",
                (i, f"g{i}", sha, sha, y, x, datum),
            )


def ids(db):
    return {
        r["external_id"]: r["place_id"] for r in db.execute("SELECT * FROM place_external_refs")
    }


@pytest.fixture
def catalog(tmp_path):
    c = Catalog.open(data_root=tmp_path)
    yield c
    c.close()


def test_gps_hierarchy_homonyms_cross_city_and_fts(catalog):
    seed(catalog, [(1.5, 1, "WGS84"), (2.5, 1, "ASSUMED_WGS84"), (9, 9, "WGS84")])
    install(catalog, package())
    install(catalog, package(scenic=True))
    assert resolve_batch(catalog) == 6
    assert resolve_batch(catalog) == 0
    db, p = catalog.connection, ids(catalog.connection)
    assert set(effective_ids(db, 1)) == {p["west"], p["lake"]}
    assert set(effective_ids(db, 2)) == {p["east"], p["lake"]}
    assert not effective_ids(db, 3)
    assert coverage(db)["places_assigned"] == 2
    assert [x["canonical_name"] for x in asset_place_view(db, 1)["place"]["ancestors"]] == [
        "国家",
        "省",
        "杭州",
    ]
    assert "跨市景区" in place_values(db, 1)
    for root, expected in [("hz", [1]), ("other", [2]), ("lake", [1, 2]), ("pr", [1, 2])]:
        sql, params = place_condition(db, place_ids=[p[root]])
        assert [
            r[0] for r in db.execute("SELECT a.asset_id FROM assets a WHERE " + sql, params)
        ] == expected
    root = browse(db)
    assert [(x["canonical_name"], x["asset_count"]) for x in root["places"]] == [("国家", 2)]
    city = browse(db, parent=p["hz"])
    assert [x["place_id"] for x in city["places"]] == [p["west"]]
    assert [x["place_id"] for x in city["areas"]] == [p["lake"]]
    assert browse(db, scenic=True)["places"][0]["asset_count"] == 2


def test_manual_cancel_restore_undo_and_package_update_preserve_knowledge(catalog):
    seed(catalog, [(1, 1, "WGS84")])
    install(catalog, package())
    resolve_batch(catalog)
    p, db = ids(catalog.connection), catalog.connection
    service = PlaceService(catalog)
    cancel = service.write_assignment(
        asset_id=1,
        action="UNASSIGN",
        target_place_id=None,
        expected_revision=0,
        request_id="cancel",
    )
    assert not effective_ids(db, 1)
    service.write_assignment(
        asset_id=1,
        action="RESTORE_AUTO",
        target_place_id=None,
        expected_revision=1,
        request_id="restore",
    )
    assert not effective_ids(db, 1), "restoring auto must retain the rejected boundary exclusion"
    service.write_assignment(
        asset_id=1,
        action="ASSIGN",
        target_place_id=p["other"],
        expected_revision=2,
        request_id="correct",
    )
    install(catalog, package(version="2"))
    resolve_batch(catalog)
    assert effective_ids(db, 1) == (p["other"],)
    assert ids(db) == p
    corrected = db.execute("SELECT * FROM place_operations WHERE request_id='correct'").fetchone()
    service.undo(
        operation_id=corrected["operation_id"],
        request_id="undo-correction",
        expected_operation_revision=corrected["operation_revision"],
    )
    assert not effective_ids(db, 1)
    assert cancel["operation_id"]
    assert db.execute("SELECT COUNT(*) FROM asset_place_assignment_history").fetchone()[0] == 4


def test_boundary_edges_overlap_and_holes_do_not_choose_arbitrary_city(catalog):
    install(catalog, package())
    db = catalog.connection
    index = BoundaryIndex.load(db, db.execute("SELECT * FROM boundary_packages").fetchone())
    p = ids(db)
    assert index.locate(2, 1) == ("AMBIGUOUS", [p["pr"]])
    assert index.locate(0, 0) == ("MATCHED", [p["west"]])
    hole = Polygon([(0, 0), (4, 0), (4, 4), (0, 4)], [[(1, 1), (3, 1), (3, 3), (1, 3)]])
    raw = json.loads(package(scenic=True))
    raw["features"][0]["geometry"] = mapping(hole)
    install(catalog, json.dumps(raw).encode())
    scenic = BoundaryIndex.load(
        db, db.execute("SELECT * FROM boundary_packages WHERE layer='scenic'").fetchone()
    )
    assert scenic.locate(2, 2) == ("NO_MATCH", [])


def test_package_rejection_rolls_back_active_and_ids(catalog):
    install(catalog, package())
    db = catalog.connection
    before = list(db.execute("SELECT package_id,active FROM boundary_packages"))
    raw = json.loads(package(version="2"))
    raw["features"][-1]["parent"] = "hz"
    with pytest.raises(CatalogError):
        install(catalog, json.dumps(raw).encode())
    assert [tuple(r) for r in db.execute("SELECT package_id,active FROM boundary_packages")] == [
        tuple(r) for r in before
    ]
    for change in ["GCJ-02", "EPSG:3857"]:
        raw["metadata"]["crs"] = change
        with pytest.raises(CatalogError):
            parse_package(json.dumps(raw).encode())
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_malformed_geometry_and_changed_layer_are_rejected(catalog):
    install(catalog, package())
    for geometry in ["Polygon", [], {"type": None}, {"type": "Polygon", "coordinates": [[[]]]}]:
        raw = json.loads(package())
        raw["features"][-1]["geometry"] = geometry
        with pytest.raises(CatalogError):
            parse_package(json.dumps(raw).encode())
    raw = json.loads(package(scenic=True))
    raw["metadata"]["namespace"] = "admin"
    with pytest.raises(CatalogError):
        install(catalog, json.dumps(raw).encode())
    assert catalog.connection.execute("SELECT COUNT(*) FROM boundary_packages").fetchone()[0] == 1


def test_superseded_gps_never_matches_and_recalculation_resumes(catalog):
    seed(catalog, [(1, 1, "WGS84")])
    install(catalog, package())
    resolve_batch(catalog)
    db = catalog.connection
    with catalog.write():
        db.execute("UPDATE asset_geo_facts SET geo_fact_id='new',longitude=3 WHERE asset_id=1")
    assert not effective_ids(db, 1)
    assert resolve_batch(catalog) == 1
    assert effective_ids(db, 1) == (ids(db)["east"],)


def test_v16_upgrade_preserves_ids_controls_aliases_and_foreign_keys(tmp_path):
    path = tmp_path / "catalog" / "catalog.db"
    path.parent.mkdir()
    db = sqlite3.connect(path)
    for version, file in schema.migration_files():
        if version < 17:
            db.executescript(file.read_text())
    db.execute("INSERT OR REPLACE INTO metadata VALUES('schema_version','16')")
    db.execute("INSERT INTO places VALUES (800,'city','杭州','杭州',NULL,'USER','ACTIVE',7,1,2)")
    db.execute(
        "INSERT INTO places VALUES (900,'attraction','西湖','西湖',800,'USER','ACTIVE',3,1,2)"
    )
    db.commit()
    db.close()
    c = Catalog.open(data_root=tmp_path)
    assert c.schema_version == schema.SUPPORTED_SCHEMA_VERSION
    assert tuple(
        c.connection.execute(
            "SELECT parent_place_id,revision FROM places WHERE place_id=900"
        ).fetchone()
    ) == (800, 3)
    assert c.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert not c.connection.execute("PRAGMA foreign_key_check").fetchall()
    assert list((tmp_path / "backups").rglob("*.db"))
    c.close()


def test_v17_failed_rebuild_rolls_back_and_restores_connection_guards(tmp_path):
    from photo_search.catalog.migrate import _apply_migration

    db = sqlite3.connect(":memory:", isolation_level=None)
    db.execute("PRAGMA foreign_keys=ON")
    for version, file in schema.migration_files():
        if version < 17:
            _apply_migration(db, version, file)
    db.execute("INSERT INTO places VALUES (800,'city','杭州','杭州',NULL,'USER','ACTIVE',7,1,2)")
    migration = next(file for version, file in schema.migration_files() if version == 17)
    broken = tmp_path / "0017_broken.sql"
    broken.write_text(migration.read_text() + "\nINSERT INTO nonexistent_table VALUES(1);")
    with pytest.raises(CatalogError, match="rolled back"):
        _apply_migration(db, 17, broken)
    assert db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0] == "16"
    assert db.execute("SELECT revision FROM places WHERE place_id=800").fetchone()[0] == 7
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db.execute("PRAGMA legacy_alter_table").fetchone()[0] == 0
    assert not db.execute(
        "SELECT name FROM sqlite_master WHERE name='boundary_packages'"
    ).fetchall()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM places WHERE place_id=800")
    db.close()


def test_v18_repairs_retired_manual_and_automatic_projection_queue(tmp_path):
    from photo_search.catalog.migrate import _apply_migration

    path = tmp_path / "catalog" / "catalog.db"
    path.parent.mkdir()
    db = sqlite3.connect(path, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    for version, file in schema.migration_files():
        if version <= 17:
            _apply_migration(db, version, file)
    with Catalog(db, db_path=path) as old:
        seed(old, [(1, 1, "WGS84"), (1, 1, "WGS84")])
        install(old, package())
        resolve_batch(old)
        place = ids(db)["west"]
        PlaceService(old).write_assignment(
            asset_id=2, action="ASSIGN", target_place_id=place,
            expected_revision=0, request_id="manual",
        )
        with old.write():
            db.execute("DELETE FROM search_projection_queue")
            db.execute("UPDATE places SET lifecycle='RETIRED' WHERE place_id=?", (place,))
        assert not db.execute("SELECT * FROM search_projection_queue").fetchall()
    with Catalog.open(data_root=tmp_path) as upgraded:
        assert upgraded.schema_version == 18
        assert {r[0] for r in upgraded.read_all("SELECT asset_id FROM search_projection_queue")} == {1, 2}
        assert not effective_ids(upgraded.connection, 1)
        assert not effective_ids(upgraded.connection, 2)
        assert not upgraded.read_all("PRAGMA foreign_key_check")
