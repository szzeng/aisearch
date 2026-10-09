"""Focused synthetic SQLite/API coverage for one-face unnamed-person creation."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from httpx import AsyncClient

from photo_search.catalog import schema
from photo_search.face.contract import FACE_SPEC_REVISION
from photo_search.face.detail_policy import POLICY_REVISION
from photo_search.persons import operations, retention
from photo_search.persons import read as person_read
from photo_search.persons.create_from_face import CONTRACT_VERSION
from photo_search.persons.service import PersonService


def seed_pending_faces(env: Any) -> dict[str, Any]:
    catalog = env.service.catalog
    if catalog.read_one("SELECT 1 FROM source_roots LIMIT 1") is None:
        with catalog.write() as connection:
            connection.execute(
                "INSERT INTO source_roots(display_name, root_path, volume_hint, status) "
                "VALUES ('library', ?, 'C:', 'ONLINE')",
                (str(env.library),),
            )
    with catalog.write() as connection:
        digest = hashlib.sha256(b"person-inbox-create-asset").hexdigest()
        connection.execute(
            "INSERT INTO assets(sha256, byte_size, created_at, state) "
            "VALUES (?, 2048, 1, 'ACTIVE')",
            (digest,),
        )
        asset_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        connection.execute(
            "INSERT INTO file_locations(source_id, relative_path, path_key, asset_id, size, "
            "mtime_ns, last_seen_scan_id, availability) "
            "VALUES (1, 'inbox/a.jpg', 'inbox-a', ?, 2048, 1, NULL, 'PRESENT')",
            (asset_id,),
        )
        connection.execute(
            "INSERT INTO persons(name, name_key, revision, created_at, updated_at) "
            "VALUES ('候选人', 'nfc1:候选人', 1, 1, 1)"
        )
        candidate = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        connection.execute(
            "INSERT INTO persons(name, name_key, revision, created_at, updated_at) "
            "VALUES ('同照片另一人', 'nfc1:同照片另一人', 1, 1, 1)"
        )
        confirmed_person = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        face_ids = [hashlib.sha256(f"inbox-create-{i}".encode()).hexdigest() for i in range(4)]
        for ordinal, face_id in enumerate(face_ids):
            low = ordinal == 2
            connection.execute(
                "INSERT INTO faces(face_id, asset_id, spec_revision, detector_id, ordinal, "
                "input_revision, location_id, bbox_json, landmarks_json, det_score, face_pixels, "
                "blur_variance, quality_state, quality_reason, artifact_id, face_revision, "
                "created_at, updated_at) VALUES (?, ?, ?, 'det', ?, ?, 1, '[0,0,5,5]', "
                "'[[1,1],[2,1],[3,2],[2,3],[1,3]]', 0.5, ?, 5.0, ?, ?, 'artifact', 1, 1, 1)",
                (
                    face_id,
                    asset_id,
                    str(FACE_SPEC_REVISION),
                    ordinal,
                    digest,
                    10.0 if low else 90.0,
                    "LOW" if low else "OK",
                    "SMALL" if low else None,
                ),
            )
        # The selected pending face and a second candidate-group face have machine facts;
        # neither suggestion may be changed by the one-face creation.
        for face_id in (face_ids[0], face_ids[1]):
            connection.execute(
                "INSERT INTO face_person_links(face_id, person_id, assignment_kind, decision, "
                "confidence, reason, revision, last_operation_id, created_at, updated_at) "
                "VALUES (?, ?, 'AUTO', 'SUGGESTED', 0.4, 'BORDERLINE', 1, NULL, 1, 1)",
                (face_id, candidate),
            )
        # Another person is confirmed on the same photo; it remains untouched.
        connection.execute(
            "INSERT INTO face_person_links(face_id, person_id, assignment_kind, decision, "
            "confidence, revision, created_at, updated_at) "
            "VALUES (?, ?, 'AUTO', 'CONFIRMED', 0.9, 1, 1, 1)",
            (face_ids[1], confirmed_person),
        )
        connection.execute(
            "INSERT INTO face_assignment_controls(face_id, mode, revision, updated_at) "
            "VALUES (?, 'MANUAL_UNASSIGNED', 1, 1)",
            (face_ids[3],),
        )
        old_digest = hashlib.sha256(b"inbox-create-old-asset").hexdigest()
        connection.execute(
            "INSERT INTO assets(sha256, byte_size, created_at, state) "
            "VALUES (?, 2048, 1, 'ACTIVE')",
            (old_digest,),
        )
        old_asset_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        connection.execute(
            "INSERT INTO file_locations(source_id, relative_path, path_key, asset_id, size, "
            "mtime_ns, last_seen_scan_id, availability) "
            "VALUES (1, 'inbox/old.jpg', 'inbox-old', ?, 2048, 1, NULL, 'PRESENT')",
            (old_asset_id,),
        )
        old_location_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        old_face_id = hashlib.sha256(b"inbox-create-old-face").hexdigest()
        connection.execute(
            "INSERT INTO faces(face_id, asset_id, spec_revision, detector_id, ordinal, "
            "input_revision, location_id, bbox_json, landmarks_json, det_score, face_pixels, "
            "blur_variance, quality_state, quality_reason, artifact_id, face_revision, "
            "created_at, updated_at) VALUES (?, ?, 'face-spec-v1', 'det-old', 0, ?, ?, "
            "'[0,0,5,5]', '[[1,1],[2,1],[3,2],[2,3],[1,3]]', 0.5, 90.0, 5.0, 'OK', "
            "NULL, 'artifact-old', 1, 1, 1)",
            (old_face_id, old_asset_id, old_digest, old_location_id),
        )
        connection.execute(
            "INSERT INTO face_detail_auto_assets(asset_id,input_revision,artifact_id,policy_revision,ready,updated_at) "
            "VALUES (?,?,?,?,1,1)",
            (old_asset_id, old_digest, "artifact-new", POLICY_REVISION),
        )
    return {"asset_id": asset_id, "candidate": candidate, "faces": face_ids, "old_face": old_face_id}


def body(response: Any) -> dict[str, Any]:
    try:
        return response.json()
    except ValueError:
        return {"text": response.text}


def test_create_is_one_face_idempotent_non_undoable_and_preserves_other_facts(api_env) -> None:
    seeded = seed_pending_faces(api_env)
    selected, other = seeded["faces"][:2]
    catalog = api_env.service.catalog
    before_links = [
        tuple(row)
        for row in catalog.read_all(
            "SELECT face_id, person_id, assignment_kind, decision, revision "
            "FROM face_person_links ORDER BY face_id, person_id, assignment_kind"
        )
    ]

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await api_env.authenticate(client)
        payload = {
            "contract_version": CONTRACT_VERSION,
            "request_id": "create-from-face-1",
            "expected_control_revision": 0,
        }
        created = await client.post(
            f"/api/v1/faces/{selected}/create-person", json=payload,
            headers=api_env.post_headers(csrf),
        )
        replay = await client.post(
            f"/api/v1/faces/{selected}/create-person", json=payload,
            headers=api_env.post_headers(csrf),
        )
        different_request = await client.post(
            f"/api/v1/faces/{selected}/create-person",
            json={**payload, "request_id": "create-from-face-2"},
            headers=api_env.post_headers(csrf),
        )
        undo = await client.post(
            f"/api/v1/person-operations/{body(created)['operation_id']}/undo",
            json={"request_id": "create-from-face-undo", "expected_operation_revision": 1},
            headers=api_env.post_headers(csrf),
        )
        return {
            "created": body(created), "status": created.status_code,
            "replay": body(replay), "replay_status": replay.status_code,
            "different": body(different_request), "different_status": different_request.status_code,
            "undo": body(undo), "undo_status": undo.status_code,
        }

    result = api_env.scenario(scenario)
    assert result["status"] == 200
    assert result["created"]["new_person_id"] > 0
    assert result["created"]["face_id"] == selected
    assert result["created"]["changed"] is True
    assert result["created"]["replayed"] is False
    assert result["replay_status"] == 200
    assert result["replay"]["new_person_id"] == result["created"]["new_person_id"]
    assert result["replay"]["operation_id"] == result["created"]["operation_id"]
    assert result["replay"]["replayed"] is True
    assert result["different_status"] == 409
    assert result["undo_status"] == 409
    assert result["undo"]["code"] == "PERSON_OPERATION_NOT_UNDOABLE"
    new_person = result["created"]["new_person_id"]
    assert catalog.read_one(
        "SELECT 1 FROM persons WHERE person_id=? AND name IS NULL AND name_key IS NULL "
        "AND avatar_face_id=? AND revision=1 AND merged_into IS NULL",
        (new_person, selected),
    )
    assert catalog.read_all(
        "SELECT face_id, person_id, assignment_kind, decision FROM face_person_links "
        "WHERE decision='CONFIRMED' AND face_id=?",
        (selected,),
    )[0]["person_id"] == new_person
    assert [tuple(row) for row in catalog.read_all(
        "SELECT face_id, person_id, assignment_kind, decision, revision "
        "FROM face_person_links WHERE face_id<>? ORDER BY face_id, person_id, assignment_kind",
        (selected,),
    )] == [row for row in before_links if row[0] != selected]
    op = catalog.read_one(
        "SELECT type, target_person_id FROM person_operations WHERE operation_id=?",
        (result["created"]["operation_id"],),
    )
    assert tuple(op) == ("CREATE_PERSON_FROM_FACE", new_person)
    assert catalog.read_one(
        "SELECT COUNT(*) AS n FROM person_operation_items WHERE operation_id=? "
        "AND entity_type='FACE_CONTROL' AND entity_key=?",
        (result["created"]["operation_id"], selected),
    )["n"] == 1


def test_manual_unassigned_low_quality_and_atomic_failure(api_env, monkeypatch) -> None:
    seeded = seed_pending_faces(api_env)
    manual_unassigned, low_quality, hidden = (
        seeded["faces"][3], seeded["faces"][2], seeded["faces"][0]
    )
    service = PersonService(api_env.service.catalog)
    result = service.create_person_from_face(
        face_id=manual_unassigned,
        contract_version=CONTRACT_VERSION,
        expected_control_revision=1,
        request_id="manual-unassigned-create",
    )
    assert result["new_person_id"] > 0

    low = service.create_person_from_face(
        face_id=low_quality,
        contract_version=CONTRACT_VERSION,
        expected_control_revision=0,
        request_id="low-quality-create",
    )
    assert low["new_person_id"] > 0

    # Hidden inbox, unknown face, and stale control revision reject without new operations.
    with service.catalog.write() as connection:
        connection.execute("INSERT INTO face_inbox_hidden(face_id) VALUES (?)", (hidden,))
    count_before = service.catalog.read_one("SELECT COUNT(*) AS n FROM person_operations")["n"]
    for face_id, revision, request_id in (
        (hidden, 0, "hidden-create"),
        (seeded["old_face"], 0, "superseded-create"),
        ("f" * 64, 0, "unknown-create"),
        (low_quality, 0, "stale-create"),
    ):
        try:
            service.create_person_from_face(
                face_id=face_id,
                contract_version=CONTRACT_VERSION,
                expected_control_revision=revision,
                request_id=request_id,
            )
        except Exception:
            pass
        else:
            raise AssertionError(f"expected create rejection for {request_id}")
    assert service.catalog.read_one("SELECT COUNT(*) AS n FROM person_operations")["n"] == count_before

    with service.catalog.write() as connection:
        connection.execute("DELETE FROM face_inbox_hidden WHERE face_id=?", (hidden,))
        connection.execute(
            "INSERT INTO face_redetection_groups(old_face_id, asset_id, old_spec_revision, "
            "candidate_state, state, revision, created_at, updated_at) "
            "VALUES (?, ?, ?, 'NO_CANDIDATE', 'OPEN', 1, 1, 1)",
            (hidden, seeded["asset_id"], str(FACE_SPEC_REVISION)),
        )
    before_hold = service.catalog.read_one("SELECT COUNT(*) AS n FROM person_operations")["n"]
    try:
        service.create_person_from_face(
            face_id=hidden,
            contract_version=CONTRACT_VERSION,
            expected_control_revision=0,
            request_id="open-hold-create",
        )
    except Exception:
        pass
    else:
        raise AssertionError("an OPEN redetection hold must block creation")
    assert service.catalog.read_one("SELECT COUNT(*) AS n FROM person_operations")["n"] == before_hold
    with service.catalog.write() as connection:
        connection.execute("DELETE FROM face_redetection_groups WHERE old_face_id=?", (hidden,))

    def fail_publish(*args, **kwargs):
        raise RuntimeError("injected derived publish failure")

    monkeypatch.setattr(operations, "publish_derived", fail_publish)
    person_count = service.catalog.read_one("SELECT COUNT(*) AS n FROM persons")["n"]
    face_id = hidden
    try:
        service.create_person_from_face(
            face_id=face_id,
            contract_version=CONTRACT_VERSION,
            expected_control_revision=0,
            request_id="rollback-create",
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected injected failure")
    assert service.catalog.read_one("SELECT COUNT(*) AS n FROM persons")["n"] == person_count
    assert service.catalog.read_one(
        "SELECT 1 FROM person_operations WHERE request_id='rollback-create'"
    ) is None
    assert service.catalog.read_one(
        "SELECT 1 FROM face_assignment_controls WHERE face_id=?", (face_id,)
    ) is None
    monkeypatch.undo()

    # A corrected create stays replayable by the original request id. The empty unnamed
    # identity remains stable in history but cannot appear as a ghost choice in kind=all.
    service.set_face_assignment(
        face_id=manual_unassigned,
        contract_version="p3-face-correction-contracts.v1",
        action="unassign",
        target_person_id=None,
        expected_revision=2,
        request_id="manual-unassigned-create-correction",
    )
    replay = service.create_person_from_face(
        face_id=manual_unassigned,
        contract_version=CONTRACT_VERSION,
        expected_control_revision=1,
        request_id="manual-unassigned-create",
    )
    assert replay["replayed"] is True
    assert replay["new_person_id"] == result["new_person_id"]

    # Retention can replace the large snapshot with its replay receipt without losing the
    # stable created-person identity or the one selected face.
    with service.catalog.write() as connection:
        operation = connection.execute(
            "SELECT * FROM person_operations WHERE operation_id=?", (result["operation_id"],)
        ).fetchone()
        summary, replay_items, snapshot_bytes, receipt_bytes = retention._receipt_for(
            connection, operation
        )
        assert snapshot_bytes > receipt_bytes
        connection.execute(
            "INSERT INTO person_operation_retention(operation_id, expired_at, summary_json, "
            "replay_json, item_count, snapshot_bytes, receipt_bytes) VALUES (?, 1, ?, ?, ?, ?, ?)",
            (
                result["operation_id"],
                json.dumps(summary, ensure_ascii=False),
                json.dumps(replay_items, ensure_ascii=False),
                len(replay_items),
                snapshot_bytes,
                receipt_bytes,
            ),
        )
        connection.execute(
            "DELETE FROM person_operation_items WHERE operation_id=?",
            (result["operation_id"],),
        )
    compact_replay = service.create_person_from_face(
        face_id=manual_unassigned,
        contract_version=CONTRACT_VERSION,
        expected_control_revision=1,
        request_id="manual-unassigned-create",
    )
    assert compact_replay["new_person_id"] == result["new_person_id"]
    assert compact_replay["face_id"] == manual_unassigned
    assert compact_replay["replayed"] is True
    page = person_read.person_page(service.catalog, limit=50, kind="all")
    assert result["new_person_id"] not in {person["person_id"] for person in page["persons"]}


def test_schema_27_migration_preserves_retention_receipt_alias_and_undo_refs() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        migrations = schema.migration_files()
        for version, path in migrations:
            if version >= 28:
                break
            connection.executescript(path.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO persons(name, name_key, revision, created_at, updated_at) "
            "VALUES ('迁移人物', 'nfc1:迁移人物', 1, 1, 1)"
        )
        person_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        operation_id = "a" * 64
        connection.execute(
            "INSERT INTO person_operations(operation_id, request_id, payload_sha256, type, "
            "source_person_ids_json, target_person_id, state, operation_revision, "
            "catalog_filter_revision, created_at, committed_at) "
            "VALUES (?, 'migration-request', ?, 'RENAME', '[]', ?, 'COMMITTED', 1, 4, 1, 1)",
            (operation_id, "b" * 64, person_id),
        )
        connection.execute(
            "INSERT INTO person_operation_items(operation_id,item_index,entity_type,entity_id, "
            "before_value_json,after_value_json,after_revision) "
            "VALUES (?,0,'PERSON_BUNDLE',?,'{}','{}',1)",
            (operation_id, person_id),
        )
        connection.execute(
            "INSERT INTO person_aliases(person_id,alias,normalized_alias,last_operation_id,created_at) "
            "VALUES (?,'家人','家人',?,1)",
            (person_id, operation_id),
        )
        connection.execute(
            "INSERT INTO person_operation_retention(operation_id,expired_at,summary_json,replay_json, "
            "item_count,snapshot_bytes,receipt_bytes) VALUES (?,2,'{}','[]',1,20,10)",
            (operation_id,),
        )
        mapping_operation_id = "e" * 64
        mapping_face_id = "d" * 64
        connection.execute(
            "INSERT INTO person_operations(operation_id, request_id, payload_sha256, type, "
            "source_person_ids_json, state, operation_revision, catalog_filter_revision, "
            "created_at, committed_at) VALUES (?, 'mapping-request', ?, 'FACE_MAPPING_REJECT', "
            "'[]', 'COMMITTED', 2, 4, 2, 2)",
            (mapping_operation_id, "f" * 64),
        )
        connection.execute(
            "INSERT INTO person_operation_items(operation_id,item_index,entity_type,entity_key, "
            "before_value_json,after_value_json,after_revision) "
            "VALUES (?,0,'FACE_MAPPING_GROUP',?,'{}','{}',0)",
            (mapping_operation_id, mapping_face_id),
        )
        undo_operation_id = "9" * 64
        connection.execute(
            "INSERT INTO person_operations(operation_id, request_id, payload_sha256, type, "
            "source_person_ids_json, target_person_id, state, operation_revision, "
            "undo_of_operation_id, catalog_filter_revision, created_at, committed_at) "
            "VALUES (?, 'undo-request', ?, 'UNDO', '[]', ?, 'COMMITTED', 3, ?, 4, 3, 3)",
            (undo_operation_id, "8" * 64, person_id, operation_id),
        )
        connection.execute(
            "UPDATE person_operations SET state='UNDONE', undone_by=? WHERE operation_id=?",
            (undo_operation_id, operation_id),
        )
        connection.commit()
        migration = next(path for version, path in migrations if version == 28)
        connection.executescript(migration.read_text(encoding="utf-8"))
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert tuple(
            connection.execute(
                "SELECT type,target_person_id,catalog_filter_revision,state,undone_by FROM person_operations "
                "WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
        ) == ("RENAME", person_id, 4, "UNDONE", undo_operation_id)
        assert tuple(
            connection.execute(
                "SELECT type,undo_of_operation_id,state FROM person_operations WHERE operation_id=?",
                (undo_operation_id,),
            ).fetchone()
        ) == ("UNDO", operation_id, "COMMITTED")
        assert connection.execute(
            "SELECT last_operation_id FROM person_aliases WHERE person_id=?", (person_id,)
        ).fetchone()[0] == operation_id
        assert connection.execute(
            "SELECT replay_json FROM person_operation_retention WHERE operation_id=?",
            (operation_id,),
        ).fetchone()[0] == "[]"
        assert tuple(
            connection.execute(
                "SELECT entity_type,entity_key FROM person_operation_items WHERE operation_id=?",
                (mapping_operation_id,),
            ).fetchone()
        ) == ("FACE_MAPPING_GROUP", mapping_face_id)
        new_id = "c" * 64
        connection.execute(
            "INSERT INTO person_operations(operation_id, request_id, payload_sha256, type, "
            "source_person_ids_json, target_person_id, state, operation_revision, "
            "catalog_filter_revision, created_at, committed_at) "
            "VALUES (?, 'new-operation', ?, 'CREATE_PERSON_FROM_FACE', '[]', ?, 'COMMITTED', 4, 4, 2, 2)",
            (new_id, "d" * 64, person_id),
        )
    finally:
        connection.close()
