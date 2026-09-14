"""P2-04: what the derived search document contains, and what it must never contain.

Every fixture is a ``%TEMP%`` catalog; no real photo library, no dataset package and no
model is involved. The rows are seeded directly so the assertions are about the derivation
rules of the projection (architecture §4.3/§4.5.3/§4.5.4), not about the API.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from photo_search.catalog.db import Catalog
from photo_search.search import documents

SOURCE_ROOT = "C:\\Users\\someone\\Pictures"


def open_catalog(tmp_path: Path) -> Catalog:
    return Catalog.open(tmp_path / "data" / "catalog" / "catalog.db", lease_seconds=60)


@pytest.fixture()
def catalog(tmp_path: Path) -> Catalog:
    instance = open_catalog(tmp_path)
    try:
        yield instance
    finally:
        instance.close()


def add_asset(catalog: Catalog, sha_seed: str) -> int:
    with catalog.write() as connection:
        cursor = connection.execute(
            "INSERT INTO assets(sha256, byte_size, created_at, state) VALUES (?, 4096, 1000, 'ACTIVE')",
            (hashlib.sha256(sha_seed.encode()).hexdigest(),),
        )
        return int(cursor.lastrowid)


def ensure_source(catalog: Catalog) -> int:
    with catalog.write() as connection:
        row = connection.execute("SELECT source_id FROM source_roots ORDER BY source_id LIMIT 1").fetchone()
        if row is not None:
            return int(row["source_id"])
        cursor = connection.execute(
            "INSERT INTO source_roots(display_name, root_path, volume_hint, status) "
            "VALUES ('照片 库', ?, 'C:', 'ONLINE')",
            (SOURCE_ROOT,),
        )
        return int(cursor.lastrowid)


def add_location(catalog: Catalog, asset_id: int, relative_path: str, availability: str = "PRESENT") -> int:
    source_id = ensure_source(catalog)
    with catalog.write() as connection:
        cursor = connection.execute(
            "INSERT INTO file_locations(source_id, relative_path, path_key, asset_id, size, "
            "mtime_ns, last_seen_scan_id, availability) "
            "VALUES (?, ?, ?, ?, 4096, 1, NULL, ?)",
            (source_id, relative_path, relative_path, int(asset_id), availability),
        )
        return int(cursor.lastrowid)


def set_availability(catalog: Catalog, location_id: int, availability: str) -> None:
    with catalog.write() as connection:
        connection.execute(
            "UPDATE file_locations SET availability = ? WHERE location_id = ?",
            (availability, int(location_id)),
        )


def add_place(
    catalog: Catalog,
    *,
    kind: str,
    name: str,
    parent_place_id: int | None = None,
    aliases: tuple[tuple[str, str | None], ...] = (),
) -> int:
    with catalog.write() as connection:
        cursor = connection.execute(
            "INSERT INTO places(kind, canonical_name, normalized_name, parent_place_id, origin, "
            "lifecycle, revision, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'USER', 'ACTIVE', 1, 5, 5)",
            (kind, name, name, parent_place_id),
        )
        place_id = int(cursor.lastrowid)
        for text, language in aliases:
            connection.execute(
                "INSERT INTO place_aliases(place_id, text, normalized, language, source, "
                "lifecycle, revision, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'USER', 'ACTIVE', 1, 5, 5)",
                (place_id, text, text, language),
            )
        return place_id


def add_operation(catalog: Catalog, operation_id: str) -> None:
    with catalog.write() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO place_operations(operation_id, request_id, payload_sha256, "
            "operation_type, operation_revision, actor, atomic, status, catalog_filter_revision, "
            "created_at, committed_at) VALUES (?, ?, ?, 'ASSIGN', 1, 'LOCAL_USER', 1, 'COMMITTED', 0, 5, 5)",
            (operation_id, f"req-{operation_id}", hashlib.sha256(operation_id.encode()).hexdigest()),
        )


def assign(
    catalog: Catalog,
    asset_id: int,
    place_id: int | None,
    *,
    mode: str = "MANUAL_ASSIGNED",
    operation_id: str = "op-1",
) -> None:
    add_operation(catalog, operation_id)
    origin = "NONE" if mode == "AUTO_ALLOWED" else "MANUAL"
    lock = 0 if mode == "AUTO_ALLOWED" else 1
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO asset_place_assignments(asset_id, assignment_id, place_id, mode, origin, "
            "manual_lock, resolution_id, revision, record_state, actor, operation_id, created_at, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?, NULL, 1, 'ACTIVE', 'LOCAL_USER', ?, 5, 5) "
            "ON CONFLICT(asset_id) DO UPDATE SET place_id = excluded.place_id, mode = excluded.mode, "
            "origin = excluded.origin, manual_lock = excluded.manual_lock, "
            "operation_id = excluded.operation_id, revision = asset_place_assignments.revision + 1",
            (int(asset_id), f"asg-{asset_id}-{place_id}", place_id, mode, origin, lock, operation_id),
        )


def exclude(catalog: Catalog, asset_id: int, place_id: int, *, operation_id: str = "op-x") -> None:
    add_operation(catalog, operation_id)
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO asset_place_exclusions(asset_id, place_id, active, revision, operation_id, "
            "created_at, updated_at) VALUES (?, ?, 1, 1, ?, 5, 5) "
            "ON CONFLICT(asset_id, place_id) DO UPDATE SET active = 1",
            (int(asset_id), int(place_id), operation_id),
        )


def add_suggestion(catalog: Catalog, asset_id: int, *, display_name: str) -> int:
    """One machine candidate that is *not* an assignment (needs a dataset row for its FK)."""
    digest = hashlib.sha256(f"ds-{asset_id}".encode()).hexdigest()
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO geo_datasets(dataset_id, package_id, package_version, contract_version, "
            "manifest_schema, provider, source_url, source_snapshot, license_id, license_url, "
            "attribution, builder_id, builder_version, parameters_sha256, content_sha256, "
            "coordinate_reference_system, coverage_json, compatible_schema_min, compatible_schema_max, "
            "relative_dir, previous_dataset_id, state, record_count, file_count, generated_at, "
            "validated_at, activated_at, retained_at, created_at, updated_at) "
            "VALUES ('ds-1', 'pkg-1', '1', 'p2-place-contracts.v1', "
            "'photo-search-place-dataset-manifest.v1', 'provider', 'https://example.invalid', "
            "'2026-09-14', 'CC0-1.0', 'https://example.invalid/license', 'attribution', 'builder', "
            "'1', ?, ?, 'WGS84', '{}', 1, 6, 'places/datasets/ds-1', NULL, 'ACTIVE', 1, 1, 5, 5, 5, "
            "NULL, 5, 5)",
            (digest, digest),
        )
        connection.execute(
            "UPDATE active_geo_dataset SET dataset_id = 'ds-1', revision = 1, updated_at = 5, "
            "cause = 'TEST' WHERE singleton = 1"
        )
        connection.execute(
            "INSERT INTO place_resolutions(resolution_id, asset_id, geo_fact_id, input_revision, "
            "control_revision_seen, processor_id, dataset_id, dataset_version, config_hash, status, "
            "suggestion_revision, created_at) "
            "VALUES (?, ?, ?, ?, 0, 'geo-nearby-city@1', 'ds-1', '1', ?, 'CANDIDATES', 0, 5)",
            (f"res-{asset_id}", int(asset_id), f"fact-{asset_id}", digest, digest),
        )
        connection.execute(
            "INSERT INTO place_resolution_candidates(resolution_id, rank, candidate_id, "
            "dataset_place_id, local_place_id, kind, display_name, ancestor_names_json, distance_m, "
            "match_basis, confidence_basis, disposition, created_at) "
            "VALUES (?, 1, ?, ?, NULL, 'city', ?, '[]', 12.5, 'NEARBY_CITY_POINT', "
            "'DISTANCE_ONLY', 'SUGGESTION_ONLY', 5)",
            (f"res-{asset_id}", f"cand-{asset_id}", f"ext-{asset_id}", display_name),
        )
    return 5


def text_of(catalog: Catalog, asset_id: int) -> str:
    row = documents.document_row(catalog.connection, int(asset_id))
    assert row is not None
    return str(row["filename_text"])


def place_text_of(catalog: Catalog, asset_id: int) -> str:
    row = documents.document_row(catalog.connection, int(asset_id))
    assert row is not None
    return str(row["place_text"])


def hits(catalog: Catalog, query: str) -> list[int]:
    from photo_search.search.query import text_condition
    from photo_search.search.tokens import build_match

    match = build_match(query)
    sql, parameters = text_condition(match)
    rows = catalog.read_all(
        f"SELECT a.asset_id FROM assets AS a WHERE {sql} ORDER BY a.asset_id", tuple(parameters)
    )
    return [int(row["asset_id"]) for row in rows]


def test_filename_text_is_the_present_basenames_and_never_a_directory_name(
    catalog: Catalog,
) -> None:
    asset_id = add_asset(catalog, "one")
    add_location(catalog, asset_id, "2024 年/家庭/杭州西湖 照片.jpg")
    add_location(catalog, asset_id, "旧目录/旧照片.jpg", availability="MISSING")
    documents.catch_up(catalog)

    assert text_of(catalog, asset_id) == "杭 州 西 湖 照 片 jpg"
    assert hits(catalog, "西湖") == [asset_id]
    assert hits(catalog, "照片 jpg") == [asset_id]
    # The directory components are not searchable text.
    assert hits(catalog, "家庭") == []
    assert hits(catalog, "2024") == []
    assert hits(catalog, "旧照片") == []


def test_a_rename_leaves_only_the_new_name_and_no_second_document(catalog: Catalog) -> None:
    asset_id = add_asset(catalog, "two")
    old = add_location(catalog, asset_id, "a/日落.jpg")
    documents.catch_up(catalog)
    assert hits(catalog, "日落") == [asset_id]

    # The scanner sees a renamed file as a new location plus the old one going MISSING.
    new = add_location(catalog, asset_id, "a/日出.jpg")
    set_availability(catalog, old, "MISSING")
    assert documents.catch_up(catalog)["state"] == "SYNCED"

    assert text_of(catalog, asset_id) == "日 出 jpg"
    assert hits(catalog, "日落") == []
    assert hits(catalog, "日出") == [asset_id]
    assert catalog.read_one(
        "SELECT COUNT(*) AS value FROM search_documents WHERE asset_id = ?", (asset_id,)
    )["value"] == 1
    assert catalog.read_one(
        "SELECT COUNT(*) AS value FROM search_documents_fts WHERE rowid = ?", (asset_id,)
    )["value"] == 1
    documents.verify_index(catalog.connection)
    assert docs_in(catalog, new) == 1
    assert documents.stale_document_asset_ids(catalog.connection) == []


def docs_in(catalog: Catalog, location_id: int) -> int:
    row = catalog.read_one(
        "SELECT l.asset_id AS value FROM file_locations AS l WHERE l.location_id = ?",
        (int(location_id),),
    )
    return int(catalog.read_one(
        "SELECT COUNT(*) AS value FROM search_documents WHERE asset_id = ?", (int(row["value"]),)
    )["value"])


def test_every_present_name_is_searchable_and_duplicates_are_folded(catalog: Catalog) -> None:
    asset_id = add_asset(catalog, "three")
    add_location(catalog, asset_id, "a/杭州.jpg")
    add_location(catalog, asset_id, "b/杭州.jpg")
    add_location(catalog, asset_id, "b/苏州.jpg")
    documents.catch_up(catalog)

    assert text_of(catalog, asset_id) == "杭 州 jpg 苏 州 jpg"
    assert hits(catalog, "杭州") == [asset_id]
    assert hits(catalog, "苏州") == [asset_id]
    assert hits(catalog, "杭州 苏州") == [asset_id]
    assert hits(catalog, "杭州 北京") == []


def test_place_text_is_only_the_current_effective_chain_with_its_aliases(
    catalog: Catalog,
) -> None:
    country = add_place(catalog, kind="country", name="中国", aliases=(("China", "en"),))
    city = add_place(catalog, kind="city", name="杭州", parent_place_id=country, aliases=(("Hangzhou", "en"),))
    asset_id = add_asset(catalog, "four")
    add_location(catalog, asset_id, "西湖.jpg")
    documents.catch_up(catalog)

    # No effective assignment yet: the place half is empty even though a place exists.
    assert place_text_of(catalog, asset_id) == ""
    assert hits(catalog, "西湖") == [asset_id]  # the file name still matches
    assert hits(catalog, "杭州") == []
    assert hits(catalog, "hangzhou") == []

    assign(catalog, asset_id, city)
    documents.catch_up(catalog)
    assert place_text_of(catalog, asset_id) == "中 国 china 杭 州 hangzhou"
    assert hits(catalog, "hangzhou") == [asset_id]
    assert hits(catalog, "中国") == [asset_id]
    assert hits(catalog, "china 杭州") == [asset_id]

    # An explicit unassign removes the effective place again.
    assign(catalog, asset_id, None, mode="MANUAL_UNASSIGNED", operation_id="op-2")
    documents.catch_up(catalog)
    assert place_text_of(catalog, asset_id) == ""
    assert hits(catalog, "hangzhou") == []


def test_a_candidate_only_asset_has_an_empty_place_text(catalog: Catalog) -> None:
    asset_id = add_asset(catalog, "five")
    add_location(catalog, asset_id, "无名.jpg")
    add_suggestion(catalog, asset_id, display_name="候选城市")
    assert documents.catch_up(catalog)["state"] == "SYNCED"

    assert place_text_of(catalog, asset_id) == ""
    assert hits(catalog, "候选城市") == []
    # The candidate is still fully visible as a suggestion - it is just not a fact.
    assert catalog.read_one(
        "SELECT COUNT(*) AS value FROM place_resolution_candidates WHERE resolution_id = ?",
        (f"res-{asset_id}",),
    )["value"] == 1


def test_an_excluded_or_retired_place_never_reaches_place_text(catalog: Catalog) -> None:
    first = add_place(catalog, kind="city", name="杭州")
    second = add_place(catalog, kind="city", name="苏州")
    asset_id = add_asset(catalog, "six")
    add_location(catalog, asset_id, "photo.jpg")
    assign(catalog, asset_id, first, operation_id="op-a")
    exclude(catalog, asset_id, first, operation_id="op-b")
    assign(catalog, asset_id, second, operation_id="op-c")
    documents.catch_up(catalog)

    assert place_text_of(catalog, asset_id) == "苏 州"
    assert hits(catalog, "苏州") == [asset_id]
    assert hits(catalog, "杭州") == []

    with catalog.write() as connection:
        connection.execute(
            "UPDATE places SET lifecycle = 'RETIRED', revision = revision + 1 WHERE place_id = ?",
            (int(second),),
        )
    documents.catch_up(catalog)
    # A retired directory entry is not part of any current classification, so it leaves the
    # text. (The write path refuses to retire a place that is still assigned; this is the
    # defensive half of the same rule.)
    assert place_text_of(catalog, asset_id) == ""
    assert hits(catalog, "苏州") == []


def test_the_search_text_never_carries_a_path_or_a_coordinate(catalog: Catalog) -> None:
    place = add_place(catalog, kind="city", name="杭州")
    asset_id = add_asset(catalog, "seven")
    add_location(catalog, asset_id, "2024 年/杭州西湖.jpg")
    assign(catalog, asset_id, place)
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO asset_geo_facts(asset_id, geo_fact_id, location_id, input_revision, "
            "fact_revision, status, source, processor_id, raw_fields_checksum_sha256, raw_gps_json, "
            "latitude, longitude, datum_status, created_at, updated_at) "
            "VALUES (?, 'fact-1', NULL, ?, 1, 'VALID', 'EXIF_GPS', 'gps-exif@1', ?, ?, 30.2741, "
            "120.1551, 'WGS84', 5, 5)",
            (
                asset_id,
                hashlib.sha256(b"x").hexdigest(),
                hashlib.sha256(b"y").hexdigest(),
                json.dumps({"latitude": 30.2741, "longitude": 120.1551}),
            ),
        )
    documents.catch_up(catalog)

    row = documents.document_row(catalog.connection, asset_id)
    body = " ".join(str(row[column]) for column in documents.TEXT_COLUMNS)
    for forbidden in ("C:", "\\\\", "Pictures", "2024", "30.2741", "120.1551", "latitude"):
        assert forbidden not in body


def test_deleting_an_asset_removes_its_document_and_index_row(catalog: Catalog) -> None:
    asset_id = add_asset(catalog, "eight")
    location_id = add_location(catalog, asset_id, "go away.jpg")
    documents.catch_up(catalog)
    assert hits(catalog, "away") == [asset_id]

    with catalog.write() as connection:
        connection.execute("DELETE FROM file_locations WHERE location_id = ?", (location_id,))
        connection.execute("DELETE FROM assets WHERE asset_id = ?", (asset_id,))
    documents.verify_index(catalog.connection)

    assert catalog.read_one(
        "SELECT COUNT(*) AS value FROM search_documents WHERE asset_id = ?", (asset_id,)
    )["value"] == 0
    assert catalog.read_one(
        "SELECT COUNT(*) AS value FROM search_documents_fts WHERE rowid = ?", (asset_id,)
    )["value"] == 0
    assert hits(catalog, "away") == []
