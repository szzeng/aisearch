"""Small synthetic acceptance checks for manual person-operation snapshot retention."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from p3_persons_page_fixtures import add_link, add_person, seed_library

from photo_search.catalog import schema
from photo_search.catalog.errors import (
    FACE_OPERATION_NOT_UNDOABLE,
    PERSON_IDEMPOTENCY_CONFLICT,
    PERSON_OPERATION_NOT_UNDOABLE,
    CatalogError,
)
from photo_search.persons import face_correction, retention
from photo_search.persons.contracts import FACE_IDEMPOTENCY_CONFLICT
from photo_search.persons.service import PersonService

_DAY_MS = 24 * 60 * 60 * 1000


def _person(env: Any) -> int:
    with env.service.catalog.write() as connection:
        cursor = connection.execute(
            "INSERT INTO persons(name, name_key, avatar_face_id, merged_into, revision, "
            "created_at, updated_at) VALUES (NULL, NULL, NULL, NULL, 1, 1, 1)"
        )
        return int(cursor.lastrowid)


def _fill_recent_window(env: Any, *, first_revision: int, count: int) -> None:
    with env.service.catalog.write() as connection:
        for revision in range(int(first_revision), int(first_revision) + int(count)):
            request_id = f"retention-fill-{revision}"
            operation_id = hashlib.sha256(request_id.encode()).hexdigest()
            connection.execute(
                "INSERT INTO person_operations(operation_id, request_id, payload_sha256, type, "
                "source_person_ids_json, target_person_id, state, operation_revision, "
                "undo_of_operation_id, undone_by, catalog_filter_revision, created_at, committed_at) "
                "VALUES (?, ?, ?, 'UNDO', '[]', NULL, 'COMMITTED', ?, NULL, NULL, 0, ?, ?)",
                (operation_id, request_id, hashlib.sha256(b"fixture").hexdigest(), revision, 1, 1),
            )


def _insert_header(env: Any, *, revision: int, operation_type: str, now_ms: int) -> None:
    request_id = f"retention-{operation_type.lower()}-{revision}"
    operation_id = hashlib.sha256(request_id.encode()).hexdigest()
    with env.service.catalog.write() as connection:
        connection.execute(
            "INSERT INTO person_operations(operation_id, request_id, payload_sha256, type, "
            "source_person_ids_json, target_person_id, state, operation_revision, "
            "undo_of_operation_id, undone_by, catalog_filter_revision, created_at, committed_at) "
            "VALUES (?, ?, ?, ?, '[]', NULL, 'COMMITTED', ?, NULL, NULL, 0, ?, ?)",
            (
                operation_id,
                request_id,
                hashlib.sha256(request_id.encode()).hexdigest(),
                operation_type,
                int(revision),
                now_ms - 31 * _DAY_MS,
                now_ms - 31 * _DAY_MS,
            ),
        )


def _write_operation_service(env: Any) -> PersonService:
    return PersonService(env.service.catalog, clock=env.clock)


def test_expiry_requires_both_age_and_recent_200_exclusion(api_env) -> None:
    service = _write_operation_service(api_env)
    person_id = _person(api_env)
    operations = []
    for index in range(4):
        operations.append(
            service.rename(
                person_id=person_id,
                    name=f"person-{index}",
                request_id=f"retention-boundary-{index}",
                expected_revision=index + 1,
            )
        )
    now_ms = api_env.clock()
    with api_env.service.catalog.write() as connection:
        for index, operation in enumerate(operations):
            committed = now_ms - (31 * _DAY_MS if index in (0, 1, 3) else 30 * _DAY_MS)
            connection.execute(
                "UPDATE person_operations SET committed_at = ? WHERE operation_id = ?",
                (committed, operation["operation_id"]),
            )
    # Old SPLIT/mapping samples are deliberately skipped; revision 7 is old but protected
    # by the latest-200 rule. Revisions 7..206 are exactly the protected recent window.
    _insert_header(api_env, revision=5, operation_type="SPLIT", now_ms=now_ms)
    _insert_header(api_env, revision=6, operation_type="FACE_MAPPING_CONFIRM", now_ms=now_ms)
    _insert_header(api_env, revision=7, operation_type="UNDO", now_ms=now_ms)
    _fill_recent_window(api_env, first_revision=8, count=199)

    result = retention.preview(api_env.service.catalog.connection, now_ms=now_ms)

    assert result["candidate_operation_count"] == 5
    assert result["safe_operation_count"] == 3, result.get("skipped_reasons")
    assert result["skipped_operation_count"] == 2
    assert result["skipped_reasons"] == {"operation_family_not_enabled_for_cleanup": 2}
    assert result["estimated_net_savings_bytes"] > 0


def test_cleanup_preserves_face_identity_history_replay_and_refuses_undo(api_env) -> None:
    seeded = seed_library(api_env, {"fixture.jpg": [(10, 10, 80, 80)]})
    face_id = seeded["fixture.jpg"]["faces"][0]
    person_id = add_person(api_env.service.catalog)
    add_link(api_env.service.catalog, face_id, person_id)
    service = _write_operation_service(api_env)
    original = service.set_face_assignment(
        face_id=face_id,
        contract_version=face_correction.CONTRACT_VERSION,
        action="unassign",
        target_person_id=None,
        expected_revision=0,
        request_id="retention-face-op",
    )
    now_ms = api_env.clock()
    with api_env.service.catalog.write() as connection:
        connection.execute(
            "UPDATE person_operations SET committed_at = ? WHERE operation_id = ?",
            (now_ms - 31 * _DAY_MS, original["operation_id"]),
        )
    _fill_recent_window(api_env, first_revision=2, count=200)
    before = (
        api_env.service.catalog.read_all(
            "SELECT face_id, person_id, assignment_kind, decision, revision, last_operation_id "
            "FROM face_person_links WHERE face_id = ? ORDER BY person_id, assignment_kind",
            (face_id,),
        ),
        api_env.service.catalog.read_one(
            "SELECT mode, revision, last_operation_id FROM face_assignment_controls WHERE face_id = ?",
            (face_id,),
        ),
    )
    preview = service.retention_preview()
    assert preview["safe_operation_count"] == 1, preview
    cleaned = service.retention_cleanup()
    assert cleaned["cleaned_operations"] == 1
    assert cleaned["failed_operations"] == 0
    assert api_env.service.catalog.read_one(
        "SELECT 1 FROM person_operation_items WHERE operation_id = ?",
        (original["operation_id"],),
    ) is None
    assert api_env.service.catalog.read_one(
        "SELECT 1 FROM person_operation_retention WHERE operation_id = ?",
        (original["operation_id"],),
    ) is not None

    page = service.catalog
    from photo_search.persons.read import operation_summary

    row = page.read_one(
        "SELECT operation_id, type, operation_revision, state, undo_of_operation_id, undone_by, "
        "target_person_id, catalog_filter_revision, created_at, committed_at "
        "FROM person_operations WHERE operation_id = ?",
        (original["operation_id"],),
    )
    expired = operation_summary(page.connection, row, [])
    assert expired["expired"] is True
    assert expired["face_ids"] == [face_id]
    replay = service.set_face_assignment(
        face_id=face_id,
        contract_version=face_correction.CONTRACT_VERSION,
        action="unassign",
        target_person_id=None,
        expected_revision=0,
        request_id="retention-face-op",
    )
    assert replay["replayed"] is True
    assert replay["items"] == original["items"]
    with pytest.raises(CatalogError) as payload_conflict:
        service.set_face_assignment(
            face_id=face_id,
            contract_version=face_correction.CONTRACT_VERSION,
            action="restore_auto",
            target_person_id=None,
            expected_revision=1,
            request_id="retention-face-op",
        )
    assert payload_conflict.value.code == FACE_IDEMPOTENCY_CONFLICT
    operation_count_before_undo = int(
        api_env.service.catalog.read_one("SELECT COUNT(*) AS n FROM person_operations")["n"]
    )
    with pytest.raises(CatalogError) as refused:
        service.undo(
            operation_id=original["operation_id"],
            request_id="retention-face-undo",
            expected_operation_revision=original["operation_revision"],
        )
    assert refused.value.code == FACE_OPERATION_NOT_UNDOABLE
    assert int(
        api_env.service.catalog.read_one("SELECT COUNT(*) AS n FROM person_operations")["n"]
    ) == operation_count_before_undo
    after = (
        api_env.service.catalog.read_all(
            "SELECT face_id, person_id, assignment_kind, decision, revision, last_operation_id "
            "FROM face_person_links WHERE face_id = ? ORDER BY person_id, assignment_kind",
            (face_id,),
        ),
        api_env.service.catalog.read_one(
            "SELECT mode, revision, last_operation_id FROM face_assignment_controls WHERE face_id = ?",
            (face_id,),
        ),
    )
    assert after == before


def test_cleanup_preserves_person_history_and_idempotent_replay(api_env) -> None:
    person_id = _person(api_env)
    service = _write_operation_service(api_env)
    original = service.rename(
        person_id=person_id,
        name="retained-person-name",
        request_id="retention-person-op",
        expected_revision=1,
    )
    now_ms = api_env.clock()
    with api_env.service.catalog.write() as connection:
        connection.execute(
            "UPDATE person_operations SET committed_at = ? WHERE operation_id = ?",
            (now_ms - 31 * _DAY_MS, original["operation_id"]),
        )
    _fill_recent_window(api_env, first_revision=2, count=200)

    assert service.retention_preview()["safe_operation_count"] == 1
    cleaned = service.retention_cleanup()
    assert cleaned["cleaned_operations"] == 1
    assert service.get_person(person_id)["name"] == "retained-person-name"
    replay = service.rename(
        person_id=person_id,
        name="retained-person-name",
        request_id="retention-person-op",
        expected_revision=1,
    )
    assert replay["replayed"] is True
    assert replay["items"] == original["items"]
    assert service.get_person(person_id)["name"] == "retained-person-name"
    with pytest.raises(CatalogError) as payload_conflict:
        service.rename(
            person_id=person_id,
            name="different-retention-name",
            request_id="retention-person-op",
            expected_revision=2,
        )
    assert payload_conflict.value.code == PERSON_IDEMPOTENCY_CONFLICT
    before_undo = int(
        api_env.service.catalog.read_one("SELECT COUNT(*) AS n FROM person_operations")["n"]
    )
    with pytest.raises(CatalogError) as refused:
        service.undo(
            operation_id=original["operation_id"],
            request_id="retention-person-undo",
            expected_operation_revision=original["operation_revision"],
        )
    assert refused.value.code == PERSON_OPERATION_NOT_UNDOABLE
    assert int(
        api_env.service.catalog.read_one("SELECT COUNT(*) AS n FROM person_operations")["n"]
    ) == before_undo


def test_cleanup_batch_checks_at_most_100_candidates_and_continues(api_env) -> None:
    now_ms = api_env.clock()
    for revision in range(1, 103):
        _insert_header(
            api_env,
            revision=revision,
            operation_type="UNDO",
            now_ms=now_ms,
        )
    _fill_recent_window(api_env, first_revision=103, count=200)

    with api_env.service.catalog.write() as connection:
        first = retention.cleanup_batch(connection, now_ms=now_ms)
    assert first["cleaned_operations"] == 0
    assert first["skipped_operations"] == 100
    assert first["has_more"] is True
    assert first["next_after_operation_revision"] == 100

    with api_env.service.catalog.write() as connection:
        second = retention.cleanup_batch(
            connection,
            now_ms=now_ms,
            after_operation_revision=first["next_after_operation_revision"],
        )
    assert second["skipped_operations"] == 2
    assert second["has_more"] is False
    assert second["next_after_operation_revision"] is None


def test_v26_upgrade_shape_batch_rollback_and_page_controls(api_env) -> None:
    objects_v26 = schema.expected_objects(26)
    objects_v27 = schema.expected_objects(27)
    assert ("table", "person_operation_retention") not in objects_v26
    assert ("table", "person_operation_retention") in objects_v27
    assert 27 in [version for version, _ in schema.migration_files()]
    upgraded = sqlite3.connect(":memory:")
    try:
        for version, migration_path in schema.migration_files():
            if version <= 26:
                upgraded.executescript(migration_path.read_text(encoding="utf-8"))
        migration_27 = next(path for version, path in schema.migration_files() if version == 27)
        upgraded.executescript(migration_27.read_text(encoding="utf-8"))
        assert upgraded.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("person_operation_retention",),
        ).fetchone() is not None
    finally:
        upgraded.close()

    async def protected_routes(client):
        anonymous = await client.get("/api/v1/person-operations/retention-preview")
        csrf = await api_env.authenticate(client)
        preview = await client.get("/api/v1/person-operations/retention-preview")
        denied = await client.post(
            "/api/v1/person-operations/retention-cleanup",
            json={},
            headers={"Origin": "http://127.0.0.1"},
        )
        allowed = await client.post(
            "/api/v1/person-operations/retention-cleanup",
            json={},
            headers=api_env.post_headers(csrf),
        )
        return anonymous.status_code, preview.status_code, denied.status_code, allowed.status_code

    assert api_env.scenario(protected_routes) == (401, 200, 403, 200)

    service = _write_operation_service(api_env)
    person_id = _person(api_env)
    original = service.rename(
        person_id=person_id,
        name="rollback-sample",
        request_id="retention-rollback-op",
        expected_revision=1,
    )
    now_ms = api_env.clock()
    with api_env.service.catalog.write() as connection:
        connection.execute(
            "UPDATE person_operations SET committed_at = ? WHERE operation_id = ?",
            (now_ms - 31 * _DAY_MS, original["operation_id"]),
        )
    _fill_recent_window(api_env, first_revision=2, count=200)
    with api_env.service.catalog.write() as connection:
        connection.execute(
            "CREATE TRIGGER fail_retention_delete BEFORE DELETE ON person_operation_items "
            "WHEN OLD.operation_id = '" + original["operation_id"] + "' "
            "BEGIN SELECT RAISE(ABORT, 'injected retention failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        with api_env.service.catalog.write() as connection:
            retention.cleanup_batch(connection, now_ms=now_ms)
    assert api_env.service.catalog.read_one(
        "SELECT 1 FROM person_operation_retention WHERE operation_id = ?",
        (original["operation_id"],),
    ) is None
    assert api_env.service.catalog.read_one(
        "SELECT 1 FROM person_operation_items WHERE operation_id = ?",
        (original["operation_id"],),
    ) is not None
    html = Path("src/photo_search/web/persons.html").read_text(encoding="utf-8")
    script = Path("src/photo_search/web/persons.js").read_text(encoding="utf-8")
    assert 'id="pt-retention-preview"' in html
    assert 'id="pt-retention-confirm"' in html
    assert "window.confirm(" in script
    assert "/person-operations/retention-preview" in script
    assert "/person-operations/retention-cleanup" in script
    assert "page.estimated_net_savings_bytes" in script
    assert "page.skipped_operation_count" in script
    assert "after_operation_revision: state.retentionAfter" in script
