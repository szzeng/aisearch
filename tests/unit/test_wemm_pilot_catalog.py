"""Synthetic-only catalog safety and frozen sample tests; no model or image decode."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from tools.evaluation import wemm_pilot_catalog as pilot


@pytest.fixture
def catalog(tmp_path):
    root = tmp_path / "private-data-root"
    database = root / "catalog" / "catalog.db"
    database.parent.mkdir(parents=True)
    photos = tmp_path / "private-photos"
    photos.mkdir()
    connection = sqlite3.connect(database)
    connection.executescript("""
        CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO metadata VALUES('schema_version','42');
        CREATE TABLE active_search_profile(state_id INTEGER PRIMARY KEY,profile_id TEXT,updated_at INT);
        INSERT INTO active_search_profile VALUES(1,'profile-qwen',17);
        CREATE TABLE search_profiles(profile_id TEXT PRIMARY KEY,embedding_processor_id TEXT,
            space_id TEXT,image_generation_id TEXT);
        INSERT INTO search_profiles VALUES('profile-qwen','qwen@abc','qwen:abc','generation-qwen');
        CREATE TABLE index_generations(generation_id TEXT PRIMARY KEY,space_id TEXT,
            processor_id TEXT,dimension INTEGER,state TEXT,is_active INT);
        INSERT INTO index_generations VALUES('generation-qwen','qwen:abc','qwen@abc',512,'READY',1);
        CREATE TABLE assets(asset_id INTEGER PRIMARY KEY,sha256 TEXT,byte_size INT,
            width INT,height INT,format TEXT,state TEXT);
        CREATE TABLE source_roots(source_id INTEGER PRIMARY KEY,root_path TEXT,status TEXT);
        CREATE TABLE file_locations(location_id INTEGER PRIMARY KEY,source_id INT,
            relative_path TEXT,asset_id INT,availability TEXT);
        CREATE INDEX idx_locations_asset ON file_locations(asset_id);
        CREATE TABLE index_entries(space_id TEXT,asset_id INT,generation_id TEXT,state TEXT);
    """)
    connection.execute("INSERT INTO source_roots VALUES(1,?,'ONLINE')", (str(photos),))
    connection.execute("INSERT INTO source_roots VALUES(2,?,'OFFLINE')", (str(photos),))
    for number in range(1, 11):
        content = f"synthetic, deliberately not image bytes {number}".encode()
        (photos / f"photo-{number}.jpg").write_bytes(content)
        connection.execute("INSERT INTO assets VALUES(?,?,?,?,?,?,?)", (
            number, hashlib.sha256(content).hexdigest(), len(content), 640, 480, "JPEG", "ACTIVE",
        ))
        connection.execute("INSERT INTO file_locations VALUES(?,?,?,?,?)", (
            number, 1, f"photo-{number}.jpg", number, "PRESENT",
        ))
        connection.execute("INSERT INTO index_entries VALUES(?,?,?,?)", (
            "qwen:abc", number, "generation-qwen", "CONFIRMED" if number < 9 else "STALE",
        ))
    connection.commit()
    connection.close()
    return root, database, photos


def _update(database, sql, parameters=()):
    with sqlite3.connect(database) as connection:
        connection.execute(sql, parameters)


def _digest(database):
    return hashlib.sha256(database.read_bytes()).hexdigest()


def test_preflight_reads_actual_binding_and_coverage_without_writes(catalog):
    root, database, _ = catalog
    before = _digest(database)
    result = pilot.catalog_preflight(root)
    assert result["status"] == "READY"
    assert result["source_identity"]["binding"]["processor_id"] == "qwen@abc"
    assert result["source_identity"]["binding"]["dimension"] == 512
    assert result["source_identity"]["catalog_sha256"] == before
    assert result["coverage"]["confirmed_eligible_assets"] == 8
    assert result["coverage"]["bound_entries_by_state"] == {"CONFIRMED": 8, "STALE": 2}
    assert _digest(database) == before
    assert list(database.parent.iterdir()) == [database]
    assert str(root) not in str(result)


@pytest.mark.parametrize("column,value", [
    ("space_id", "foreign-space"), ("processor_id", "foreign-processor"),
    ("dimension", 0),
])
def test_conflicting_profile_generation_binding_is_refused(catalog, column, value):
    root, database, _ = catalog
    _update(database, f"UPDATE index_generations SET {column}=?", (value,))
    with pytest.raises(pilot.PilotCatalogError, match="^CATALOG_BINDING_INVALID$"):
        pilot.catalog_preflight(root)


def test_no_active_profile_is_honest_and_does_not_prevent_independent_sampling(catalog):
    root, database, _ = catalog
    _update(database, "UPDATE active_search_profile SET profile_id=NULL")
    result = pilot.catalog_preflight(root)
    assert result["status"] == "NO_ACTIVE_PROFILE"
    assert result["source_identity"]["binding"] is None
    assert len(pilot.freeze_sample(root, 4, 1)["assets"]) == 4


@pytest.mark.parametrize("suffix,content,code", [
    ("-wal", b"", "ACTIVE_WAL_REQUIRES_OFFLINE_SNAPSHOT"),
    ("-wal", b"active", "ACTIVE_WAL_REQUIRES_OFFLINE_SNAPSHOT"),
    ("-shm", b"shared", "CATALOG_SIDECARS_REQUIRE_OFFLINE_SNAPSHOT"),
    ("-journal", b"journal", "CATALOG_SIDECARS_REQUIRE_OFFLINE_SNAPSHOT"),
])
def test_sidecars_are_refused_before_sqlite_connect(catalog, monkeypatch, suffix, content, code):
    root, database, _ = catalog
    sidecar = Path(str(database) + suffix)
    sidecar.write_bytes(content)
    before = _digest(database)
    monkeypatch.setattr(pilot.sqlite3, "connect", lambda *a, **k: pytest.fail("opened live SQLite"))
    with pytest.raises(pilot.PilotCatalogError, match=f"^{code}$"):
        pilot.catalog_preflight(root)
    assert sidecar.read_bytes() == content
    assert _digest(database) == before


def test_authorizer_refuses_writes_attachments_and_writable_pragmas(catalog):
    root, database, _ = catalog
    before = _digest(database)
    with pilot._snapshot(root) as (connection, _):
        assert connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 10
        for statement in (
            "UPDATE assets SET width=1", "CREATE TABLE accidental(x)",
            "PRAGMA query_only=OFF", "PRAGMA journal_mode=WAL", "ATTACH ':memory:' AS other",
        ):
            with pytest.raises(sqlite3.DatabaseError):
                connection.execute(statement)
    assert _digest(database) == before


def test_checkpointed_wal_mode_without_sidecars_remains_byte_identical(catalog):
    root, database, _ = catalog
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.close()
    assert not Path(str(database) + "-wal").exists()
    before = _digest(database)
    assert pilot.catalog_preflight(root)["status"] == "READY"
    assert _digest(database) == before
    assert list(database.parent.iterdir()) == [database]


def test_external_write_during_snapshot_invalidates_result(catalog, monkeypatch):
    root, database, _ = catalog
    original = pilot._preflight

    def write_during_read(connection, identity):
        result = original(connection, identity)
        _update(database, "UPDATE assets SET width=width+1 WHERE asset_id=1")
        return result

    monkeypatch.setattr(pilot, "_preflight", write_during_read)
    with pytest.raises(pilot.PilotCatalogError, match="^CATALOG_CHANGED_DURING_READ$"):
        pilot.catalog_preflight(root)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT width FROM assets WHERE asset_id=1").fetchone()[0] == 641


def test_wal_appearing_during_read_refuses_result_without_removing_it(catalog, monkeypatch):
    root, database, _ = catalog
    original = pilot._preflight

    def add_sidecar(connection, identity):
        result = original(connection, identity)
        Path(str(database) + "-wal").write_bytes(b"writer sidecar")
        return result

    monkeypatch.setattr(pilot, "_preflight", add_sidecar)
    with pytest.raises(pilot.PilotCatalogError, match="ACTIVE_WAL_REQUIRES_OFFLINE_SNAPSHOT"):
        pilot.catalog_preflight(root)
    assert Path(str(database) + "-wal").read_bytes() == b"writer sidecar"


def test_sample_is_deterministic_half_random_private_and_does_not_decode(catalog):
    root, database, photos = catalog
    before = _digest(database)
    first = pilot.freeze_sample(root, 7, 19, {"cat-disagreement": [8, 9, 10]})
    second = pilot.freeze_sample(root, 7, 19, {"cat-disagreement": [8, 9, 10]})
    assert first == second
    assert len(first["assets"]) == 7
    assert sum(row["selection_stratum"] == "random" for row in first["assets"]) >= 4
    assert all(Path(row["path"]).parent == photos for row in first["assets"])
    assert first["source_roots"] == [str(photos)]
    assert first["input_status_counts"] == {"READY_FOR_HASH_CHECK": 7}
    assert first["source_bytes_verified"] is False
    assert _digest(database) == before


def test_missing_and_changed_selected_files_are_kept_not_replaced(catalog):
    root, _, photos = catalog
    baseline = pilot.freeze_sample(root, 6, 2)
    missing, changed = baseline["assets"][:2]
    Path(missing["path"]).unlink()
    Path(changed["path"]).write_bytes(b"changed bytes")
    frozen = pilot.freeze_sample(root, 6, 2)
    assert [row["asset_id"] for row in frozen["assets"]] == [
        row["asset_id"] for row in baseline["assets"]
    ]
    assert frozen["assets"][0]["input_status"] == "MISSING_FILE"
    assert frozen["assets"][0]["path"] is None
    assert frozen["assets"][1]["input_status"] == "SIZE_CHANGED"
    assert len(list(photos.iterdir())) == 9


def test_online_present_location_is_chosen_and_missing_offline_counts_are_explicit(catalog):
    root, database, photos = catalog
    _update(database, "UPDATE file_locations SET source_id=2 WHERE asset_id=1")
    _update(database, "UPDATE file_locations SET availability='MISSING' WHERE asset_id=2")
    _update(database, "INSERT INTO file_locations VALUES(11,1,'photo-1.jpg',1,'PRESENT')")
    frozen = pilot.freeze_sample(root, 10, 2)
    assert frozen["population_assets"] == 9
    assert frozen["coverage"]["missing_locations"] == 1
    assert frozen["coverage"]["offline_or_error_locations"] == 1
    first = next(row for row in frozen["assets"] if row["asset_id"] == 1)
    assert first["location_id"] == 11
    assert first["path"] == str(photos / "photo-1.jpg")


@pytest.mark.parametrize("relative", ["../escape.jpg", r"..\escape.jpg", r"C:\escape.jpg",
                                      r"C:escape.jpg", r"\escape.jpg", "photo.jpg:stream"])
def test_path_traversal_and_windows_rooted_paths_are_refused(catalog, relative):
    root, database, _ = catalog
    _update(database, "UPDATE file_locations SET relative_path=? WHERE asset_id=1", (relative,))
    with pytest.raises(pilot.PilotCatalogError, match="^SAMPLE_PATH_UNSAFE$"):
        pilot.freeze_sample(root, 10, 1)


def test_resolved_link_outside_source_root_is_refused(catalog, monkeypatch):
    root, database, photos = catalog
    _update(database, "UPDATE file_locations SET relative_path='link.jpg' WHERE asset_id=1")
    resolve = Path.resolve

    def escaped(path, *args, **kwargs):
        return photos.parent / "outside.jpg" if path == photos / "link.jpg" else resolve(
            path, *args, **kwargs,
        )

    monkeypatch.setattr(Path, "resolve", escaped)
    with pytest.raises(pilot.PilotCatalogError, match="^SAMPLE_PATH_UNSAFE$"):
        pilot.freeze_sample(root, 10, 1)


def test_database_missing_error_contains_no_private_path(tmp_path):
    with pytest.raises(pilot.PilotCatalogError) as raised:
        pilot.catalog_preflight(tmp_path / "private-user-and-library")
    assert str(raised.value) == "CATALOG_UNREADABLE_OR_UNSUPPORTED"
    assert "private" not in str(raised.value)


@pytest.mark.parametrize("maximum", [0, -1, 1001, True, 2.0])
def test_sample_limit_is_checked_before_opening_a_catalog(tmp_path, maximum):
    with pytest.raises(pilot.PilotCatalogError, match="^INVALID_SAMPLE_SIZE$"):
        pilot.freeze_sample(tmp_path / "absent", maximum, 1)


def test_problem_strata_outside_eligible_population_are_rejected(catalog):
    with pytest.raises(pilot.PilotCatalogError, match="INVALID_SAMPLE_POPULATION_OR_STRATA"):
        pilot.freeze_sample(catalog[0], 4, 1, [999])
