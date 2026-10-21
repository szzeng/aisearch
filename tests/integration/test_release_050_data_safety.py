"""Current-schema release data safety: 41 -> 42 -> backup -> empty-root restore."""

from __future__ import annotations

import hashlib
import json
import shutil
from copy import deepcopy
from pathlib import Path

import pytest
from p2_search_fixtures import (
    DIMENSION,
    SPACE,
    FakeEncoder,
    assign_place,
    build_library,
    configured_scheduler,
    embedding_spec,
    index_vector,
    install_source_places,
)
from p3_persons_page_fixtures import add_link, add_person, insert_face
from p4_time_fixtures import migrations_upto

from photo_search import favorites, photo_collections, photo_smart_collections
from photo_search.backup import MANIFEST_NAME, create_backup, restore_backup
from photo_search.catalog import schema
from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import CatalogError
from photo_search.catalog.migrate import migrate_database
from photo_search.search.service import search_assets


def _rows(catalog: Catalog, sql: str, params: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    return [tuple(row) for row in catalog.read_all(sql, params)]


def _human_facts(catalog: Catalog, asset_id: int) -> dict[str, list[tuple[object, ...]]]:
    return {
        "persons": _rows(catalog, "SELECT * FROM persons ORDER BY person_id"),
        "faces": _rows(catalog, "SELECT * FROM faces WHERE asset_id=? ORDER BY face_id", (asset_id,)),
        "person_links": _rows(
            catalog,
            "SELECT * FROM face_person_links WHERE face_id IN "
            "(SELECT face_id FROM faces WHERE asset_id=?) ORDER BY face_id,person_id",
            (asset_id,),
        ),
        "places": _rows(catalog, "SELECT * FROM places ORDER BY place_id"),
        "place_assignments": _rows(
            catalog, "SELECT * FROM asset_place_assignments WHERE asset_id=? ORDER BY place_id", (asset_id,)
        ),
        "favorites": _rows(catalog, "SELECT * FROM asset_favorites WHERE asset_id=?", (asset_id,)),
        "ordinary_collections": _rows(
            catalog,
            "SELECT c.*,m.* "
            "FROM photo_collections c JOIN photo_collection_members m USING(collection_id) "
            "ORDER BY c.collection_id,m.position",
        ),
        "smart_rules": _rows(catalog, "SELECT * FROM photo_smart_collections ORDER BY smart_id"),
        "smart_cache_state": _rows(catalog, "SELECT * FROM photo_smart_cache_state ORDER BY smart_id"),
        "smart_cache_entries": _rows(
            catalog, "SELECT * FROM photo_smart_cache_entries ORDER BY smart_id,rank", ()
        ),
        "smart_exclusions": _rows(
            catalog, "SELECT * FROM photo_smart_exclusions ORDER BY smart_id,asset_id", ()
        ),
    }


def _vector_facts(catalog: Catalog, asset_id: int) -> dict[str, list[tuple[object, ...]]]:
    return {
        "index_entries": _rows(catalog, "SELECT * FROM index_entries WHERE asset_id=? ORDER BY space_id,generation_id", (asset_id,)),
        "artifacts": _rows(
            catalog,
            "SELECT a.* FROM artifacts a JOIN index_entries e USING(artifact_id) "
            "WHERE e.asset_id=? ORDER BY a.artifact_id",
            (asset_id,),
        ),
        "outbox": _rows(catalog, "SELECT * FROM index_outbox WHERE asset_id=? ORDER BY event_id", (asset_id,)),
    }


def _corrupt_restored_copy(_source: Path, target: Path) -> None:
    target.write_bytes(target.read_bytes() + b"injected restore validation corruption")


def test_schema43_backup_restore_preserves_human_facts_vectors_and_originals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Start from a real schema 41 database and scan only synthetic source photos.
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 41)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", migrations_upto(tmp_path, 41))
    library = build_library(tmp_path, ("release one.jpg", "release two.jpg"))
    data_root = library.data_root
    source_paths = sorted(library.root.glob("*.jpg"))
    source_hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths}
    asset_id = library.assets["release one.jpg"]
    second_asset_id = library.assets["release two.jpg"]
    assert library.catalog.schema_version == 41
    library.close()

    # Apply the production 0042 and 0043 migrations to the closed 41 catalog.
    monkeypatch.undo()
    migration = migrate_database(data_root=data_root)
    assert migration.previous_version == 41
    assert migration.schema_version == 43
    assert migration.applied == (42, 43)

    with Catalog.open(data_root=data_root) as catalog:
        assert catalog.schema_version == 43
        spec = embedding_spec()
        scheduler = configured_scheduler(catalog, [spec])
        index_vector(
            catalog, scheduler, spec, asset_id=asset_id,
            vector=[1.0, 0.0, 0.0], artifact_id="release-050-embedding",
        )

        face_id = insert_face(
            catalog, asset_id=asset_id,
            sha256=str(catalog.read_one("SELECT sha256 FROM assets WHERE asset_id=?", (asset_id,))[0]),
            bbox=(0.1, 0.1, 0.8, 0.8), ordinal=0,
        )
        person_id = add_person(catalog, name="发布测试人物", avatar_face_id=face_id)
        add_link(catalog, face_id, person_id, decision="CONFIRMED", assignment_kind="MANUAL")

        place_id = install_source_places(catalog)["hangzhou"]
        assign_place(catalog, asset_id=asset_id, place_id=place_id, request_id="release-050-place")
        favorites.set_favorite(catalog, asset_id, favorite=True)

        ordinary = photo_collections.create_collection(
            catalog, "发布普通合集", "release-050-ordinary", asset_ids=[asset_id, second_asset_id]
        )["collection"]
        rule = {
            "version": 1,
            "execution_kind": "FILTER",
            "request": {"retrieval_mode": "text", "text": "release"},
            "display_query": "发布测试",
            "content_query": None,
            "confirmed_filter_revision": 0,
            "confirmed_suggestion_revision": 0,
            "confirmed_favorites_revision": None,
        }
        smart = photo_smart_collections.create_collection(
            catalog, "发布智能合集", "release-050-smart", rule,
            request_hash="release-050-smart-request", auth_hash="release-050-auth", source_hash="release-050-source",
        )["collection"]
        signature, indexing, _ = photo_smart_collections.dependency_signature(catalog.connection, rule)
        photo_smart_collections.publish_candidates(
            catalog, int(smart["smart_id"]), [asset_id, second_asset_id], signature=signature, indexing=indexing,
        )
        photo_smart_collections.set_exclusion(
            catalog, int(smart["smart_id"]), second_asset_id,
            instance_token=str(smart["instance_token"]), expected_revision=int(smart["revision"]), excluded=True,
        )

        facts_before = _human_facts(catalog, asset_id)
        vector_before = _vector_facts(catalog, asset_id)
        assert facts_before["persons"] and facts_before["person_links"]
        assert facts_before["place_assignments"] and facts_before["favorites"]
        assert facts_before["ordinary_collections"] and facts_before["smart_rules"]
        assert facts_before["smart_cache_entries"] and facts_before["smart_exclusions"]
        assert vector_before["index_entries"] and vector_before["artifacts"]
        generation_id = str(catalog.read_one("SELECT generation_id FROM index_generations")[0])
        query_before = search_assets(
            catalog, text="release", space_id=SPACE, generation_id=generation_id,
            dimension=DIMENSION, encoder=FakeEncoder(spec, vectors={"release": [1.0, 0.0, 0.0]}), top_k=10,
        )
        before_hits = deepcopy(query_before["hits"])
        assert any(int(row["asset_id"]) == asset_id for row in query_before["hits"])

    bundle = tmp_path / "schema43-backup"
    create_backup(data_root=data_root, destination=bundle, stopped=True)

    # A copy failure after staging starts must leave no usable partial data root.
    failed_target = tmp_path / "failed-restore-root"
    failed_target.mkdir()
    with pytest.raises(CatalogError) as invalid_restore:
        restore_backup(bundle=bundle, data_root=failed_target, copy_hook=_corrupt_restored_copy)
    assert invalid_restore.value.code == "BACKUP_INVALID"
    assert list(failed_target.iterdir()) == []
    assert not list(tmp_path.glob(".failed-restore-root.restore-staging-*"))

    # Refusing a version mismatch must not run migration or create a target catalog.
    mismatched_bundle = tmp_path / "schema-mismatch-backup"
    shutil.copytree(bundle, mismatched_bundle)
    manifest_path = mismatched_bundle / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = 41
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    mismatch_target = tmp_path / "mismatch-restore-root"
    mismatch_target.mkdir()
    with pytest.raises(CatalogError) as rejected:
        restore_backup(bundle=mismatched_bundle, data_root=mismatch_target)
    assert rejected.value.code == "BACKUP_SCHEMA_MISMATCH"
    assert list(mismatch_target.iterdir()) == []
    assert not list(tmp_path.glob(".mismatch-restore-root.restore-staging-*"))
    assert int(json.loads((bundle / MANIFEST_NAME).read_text(encoding="utf-8"))["schema_version"]) == 43

    restored_root = tmp_path / "empty-restore-root"
    restored_root.mkdir()
    restored = restore_backup(bundle=bundle, data_root=restored_root)
    assert restored["status"] == "ok" and restored["schema_version"] == 43
    with Catalog.open(data_root=restored_root) as catalog:
        assert catalog.schema_version == 43
        assert _human_facts(catalog, asset_id) == facts_before
        assert _vector_facts(catalog, asset_id) == vector_before
        query_after = search_assets(
            catalog, text="release", space_id=SPACE, generation_id=generation_id,
            dimension=DIMENSION, encoder=FakeEncoder(spec, vectors={"release": [1.0, 0.0, 0.0]}), top_k=10,
        )
        assert query_after["hits"] == before_hits

    assert {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths} == source_hashes
    assert int(ordinary["member_count"]) == 2
    assert {row[0] for row in facts_before["ordinary_collections"]} == {ordinary["collection_id"]}
    assert {int(row[-3]) for row in facts_before["ordinary_collections"]} == {asset_id, second_asset_id}


def test_retention_does_not_delete_a_smart_collection_exclusion(api_env) -> None:
    from photo_search.library_retention import begin_asset_retention_cleanup

    from .test_p4_library_retention import _seed_retention_eligible

    source = api_env.tmp_path / "smart-exclusion-source"
    source.mkdir()
    source_id = api_env.service.register_source(display_name="synthetic", root_path=source)["source_id"]
    catalog = api_env.service.catalog
    now = int(catalog.now_ms())
    _seed_retention_eligible(catalog, 1, source_id, now_ms=now)
    rule = {
        "version": 1, "execution_kind": "FILTER", "request": {"retrieval_mode": "text"},
        "display_query": "", "content_query": None,
        "confirmed_filter_revision": 0, "confirmed_suggestion_revision": 0,
        "confirmed_favorites_revision": None,
    }
    smart = photo_smart_collections.create_collection(
        catalog, "人工排除保护", "release-exclusion-protection", rule,
        request_hash="release-exclusion-protection", auth_hash="synthetic", source_hash="synthetic",
    )["collection"]
    photo_smart_collections.set_exclusion(
        catalog, int(smart["smart_id"]), 1, instance_token=str(smart["instance_token"]),
        expected_revision=int(smart["revision"]), excluded=True,
    )
    before = _rows(catalog, "SELECT * FROM photo_smart_exclusions")
    with pytest.raises(CatalogError) as blocked:
        begin_asset_retention_cleanup(catalog, asset_id=1, now_ms=now, manual_early=True)
    assert blocked.value.code == "ASSET_RETENTION_BLOCKED"
    assert _rows(catalog, "SELECT * FROM photo_smart_exclusions") == before
    assert catalog.read_one("SELECT asset_id FROM assets WHERE asset_id=1") is not None
    assert catalog.read_one("SELECT COUNT(*) FROM asset_retention_barriers")[0] == 0
    assert catalog.read_one("SELECT COUNT(*) FROM index_outbox")[0] == 0
