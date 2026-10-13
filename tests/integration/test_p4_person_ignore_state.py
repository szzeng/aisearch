"""Temporary ignore and explicit exclusion keep their distinct person semantics."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from conftest import ApiEnv
from p3_persons_page_fixtures import add_link, add_person, seed_library

from integration.test_p3_face_correction_migration import (
    REAL_MIGRATIONS_DIR,
    build_v11,
    data_root,
    migrations_upto,
)
from photo_search.catalog import schema
from photo_search.catalog.errors import CatalogError
from photo_search.catalog.migrate import inspect_database, migrate_database
from photo_search.catalog.sqlite_io import connect_rw, open_readonly
from photo_search.persons.albums import _face_view, people_page
from photo_search.persons.service import PersonService


def test_legacy_hidden_rows_migrate_to_temporary_ignore() -> None:
    migrations = Path(__file__).parents[2] / "src/photo_search/catalog/migrations"
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE TABLE persons(person_id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO persons VALUES (1)")
        connection.executescript((migrations / "0022_person_hidden.sql").read_text(encoding="utf-8"))
        connection.execute("INSERT INTO person_hidden(person_id) VALUES (1)")
        connection.executescript((migrations / "0030_person_ignore_state.sql").read_text(encoding="utf-8"))
        assert connection.execute(
            "SELECT person_id, ignore_state FROM person_hidden"
        ).fetchall() == [(1, "TEMPORARY")]
        assert connection.execute("SELECT COUNT(*) FROM persons").fetchone() == (1,)
    finally:
        connection.close()


def test_schema_29_upgrade_preserves_hidden_rows_and_rolls_back_failure(tmp_path, monkeypatch) -> None:
    path = build_v11(tmp_path, monkeypatch)
    with monkeypatch.context() as old:
        old.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 29)
        old.setattr(schema, "MIGRATIONS_DIR", migrations_upto(tmp_path, 29))
        migrate_database(path, data_root=data_root(tmp_path))
    connection = connect_rw(path)
    try:
        connection.execute("INSERT INTO person_hidden(person_id) VALUES (7)")
    finally:
        connection.close()
    before = open_readonly(path)
    try:
        original = [tuple(row) for row in before.execute("SELECT * FROM person_hidden")]
    finally:
        before.close()

    broken = migrations_upto(tmp_path, 30)
    migration = next(broken.glob("0030_*.sql"))
    migration.write_text(
        migration.read_text(encoding="utf-8") + "\nSELECT missing_fault_column;\n",
        encoding="utf-8",
    )
    with monkeypatch.context() as fault:
        fault.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 30)
        fault.setattr(schema, "MIGRATIONS_DIR", broken)
        try:
            migrate_database(path, data_root=data_root(tmp_path))
        except CatalogError as error:
            assert error.code == "MIGRATION_FAILED"
        else:
            raise AssertionError("the injected migration fault must fail")
        assert inspect_database(path) == 29
        connection = open_readonly(path)
        try:
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(person_hidden)")}
            assert "ignore_state" not in columns
            assert [tuple(row) for row in connection.execute("SELECT * FROM person_hidden")] == original
        finally:
            connection.close()

    with monkeypatch.context() as current:
        current.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 30)
        current.setattr(schema, "MIGRATIONS_DIR", REAL_MIGRATIONS_DIR)
        result = migrate_database(path, data_root=data_root(tmp_path))
    assert result.applied == (30,)
    connection = open_readonly(path)
    try:
        assert [tuple(row) for row in connection.execute(
            "SELECT person_id, ignore_state FROM person_hidden"
        )] == [(7, "TEMPORARY")]
        assert list(connection.execute("PRAGMA foreign_key_check")) == []
    finally:
        connection.close()


def test_temporary_ignore_keeps_suggestions_and_exclusion_suppresses_them(api_env: ApiEnv) -> None:
    photos = seed_library(api_env, {
        "待整理.jpg": [(30, 30, 90, 90)], "确认成员.jpg": [(30, 30, 90, 90)],
        "待整理2.jpg": [(30, 30, 90, 90)],
    })
    catalog = api_env.service.catalog
    face_id = photos["待整理.jpg"]["faces"][0]
    person_id = add_person(catalog, name="张三")
    add_link(catalog, face_id, person_id, decision="SUGGESTED", confidence=0.85)
    add_link(catalog, photos["确认成员.jpg"]["faces"][0], person_id)
    second_suggestion = photos["待整理2.jpg"]["faces"][0]
    add_link(catalog, second_suggestion, person_id, decision="SUGGESTED", confidence=0.82)
    service = PersonService.open(catalog)

    service.set_visibility(person_ids=[person_id], hidden=True, ignore_state="TEMPORARY")
    face = catalog.read_one(
        "SELECT f.*, c.mode, c.revision AS control_revision, "
        "f.quality_reason AS display_quality_reason, f.quality_reason AS organization_reason "
        "FROM faces AS f LEFT JOIN face_assignment_controls AS c ON c.face_id=f.face_id "
        "WHERE f.face_id=?",
        (face_id,),
    )
    assert [item["person_id"] for item in _face_view(catalog, face)["suggestions"]] == [person_id]
    temporary = people_page(
        catalog, kind="all", q="", limit=10, cursor=None, visibility="hidden", ignore_state="TEMPORARY"
    )
    assert [item["person_id"] for item in temporary["persons"]] == [person_id]
    service.set_face_assignment(
        face_id=face_id,
        contract_version="p3-face-correction-contracts.v1",
        action="assign",
        target_person_id=person_id,
        expected_revision=0,
        request_id="temporary-ignore-confirm-candidate",
    )
    assert service.face_assignment(face_id)["assigned_person_id"] == person_id

    service.set_visibility(person_ids=[person_id], hidden=True, ignore_state="EXCLUDED")
    assert catalog.read_one(
        "SELECT ignore_state FROM person_hidden WHERE person_id=?", (person_id,)
    )["ignore_state"] == "EXCLUDED"
    second_face = catalog.read_one(
        "SELECT f.*, c.mode, c.revision AS control_revision, "
        "f.quality_reason AS display_quality_reason, f.quality_reason AS organization_reason "
        "FROM faces AS f LEFT JOIN face_assignment_controls AS c ON c.face_id=f.face_id "
        "WHERE f.face_id=?",
        (second_suggestion,),
    )
    assert _face_view(catalog, second_face)["suggestions"] == []
    assert service.face_assignment(second_suggestion)["suggestions_available"] is False
    excluded = people_page(
        catalog, kind="all", q="", limit=10, cursor=None, visibility="hidden", ignore_state="EXCLUDED"
    )
    assert [item["person_id"] for item in excluded["persons"]] == [person_id]

    service.set_visibility(person_ids=[person_id], hidden=False)
    assert catalog.read_one("SELECT 1 FROM person_hidden WHERE person_id=?", (person_id,)) is None
    assert catalog.read_one("SELECT 1 FROM persons WHERE person_id=?", (person_id,)) is not None
    assert catalog.read_one("SELECT 1 FROM face_person_links WHERE face_id=?", (face_id,)) is not None


def test_visibility_api_requires_explicit_state_and_lists_each_kind(api_env: ApiEnv) -> None:
    photos = seed_library(api_env, {"人物.jpg": [(20, 20, 90, 90)]})
    face_id = photos["人物.jpg"]["faces"][0]
    person_id = add_person(api_env.service.catalog, name="候选人物")
    add_link(api_env.service.catalog, face_id, person_id)

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        excluded = await client.post(
            "/api/v1/persons/visibility",
            headers=headers,
            json={"person_ids": [person_id], "hidden": True, "ignore_state": "EXCLUDED"},
        )
        assert excluded.status_code == 200
        temporary = await client.get(
            "/api/v1/persons/browse",
            params={"visibility": "hidden", "ignore_state": "TEMPORARY"},
        )
        excluded_page = await client.get(
            "/api/v1/persons/browse",
            params={"visibility": "hidden", "ignore_state": "EXCLUDED"},
        )
        assert temporary.json()["total"] == 0
        assert [item["person_id"] for item in excluded_page.json()["persons"]] == [person_id]
        invalid_scope = await client.get(
            "/api/v1/persons/browse", params={"visibility": "visible", "ignore_state": "EXCLUDED"}
        )
        assert invalid_scope.status_code == 422

    api_env.scenario(scenario)
