"""Collection directory counts stay exact and independent of unrelated face volume."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
from p3_person_fixtures import PersonHarness

from photo_search.face.detail_policy import POLICY_REVISION
from photo_search.persons import collections


def _collection(harness: PersonHarness, name: str, people: list[int]) -> dict[str, Any]:
    catalog = harness.catalog
    row = collections.create_collection(catalog, name)["collection"]
    if people:
        collections.edit_members(
            catalog, row["collection_id"], instance_token=row["instance_token"],
            action="add", person_ids=people,
        )
    return row


def test_collection_directory_counts_keep_controls_redirects_and_distinct_photos(tmp_path):
    harness = PersonHarness(tmp_path)
    try:
        catalog = harness.catalog
        first, second, empty, redirect, older = [harness.add_person() for _ in range(5)]
        collection = _collection(harness, "exact", [first, second, empty, redirect, older])
        empty_collection = _collection(harness, "empty", [])
        shared = harness.add_asset("shared.jpg")
        for ordinal, person in enumerate((first, second, first)):
            harness.add_link(harness.add_face(shared, ordinal=ordinal), person)

        def add(name, *, decision="CONFIRMED"):
            asset = harness.add_asset(name)
            face = harness.add_face(asset)
            harness.add_link(face, first, decision=decision)
            return asset, face

        add("normal.jpg")
        _, unassigned = add("unassigned.jpg")
        harness.set_control(unassigned, "MANUAL_UNASSIGNED")
        _, excluded = add("excluded.jpg")
        harness.set_exclusion(excluded, first)
        add("suggested.jpg", decision="SUGGESTED")
        superseded_asset, superseded = add("superseded.jpg")
        protected_asset, protected = add("protected.jpg")
        harness.set_control(protected, "MANUAL_ASSIGNED")
        add("video.mp4")
        legacy, _ = add("legacy.jpg")
        with catalog.write() as connection:
            connection.execute("DELETE FROM file_locations WHERE asset_id=?", (legacy,))
            connection.execute("INSERT INTO person_hidden(person_id) VALUES (?)", (first,))
            connection.execute("UPDATE persons SET merged_into=? WHERE person_id=?", (first, redirect))
            connection.execute("UPDATE persons SET merged_into=? WHERE person_id=?", (redirect, older))
            for asset, face in ((superseded_asset, superseded), (protected_asset, protected)):
                revision = connection.execute("SELECT input_revision FROM faces WHERE face_id=?", (face,)).fetchone()[0]
                connection.execute(
                    "INSERT INTO face_detail_auto_assets(asset_id,input_revision,artifact_id,policy_revision,ready,updated_at) "
                    "VALUES(?,?,'synthetic-detail',?,1,1)", (asset, revision, POLICY_REVISION),
                )

        # Four photos: shared (three faces), normal, human-protected and zero-location legacy.
        # Both redirect anchors resolve to first; hidden people remain collection members.
        page = collections.list_collections(catalog, limit=1)
        assert page["collections"][0]["photo_count"] == 4
        assert page["collections"][0]["member_count"] == 3
        assert page["has_more"]
        tail = collections.list_collections(catalog, limit=1, cursor=page["next_cursor"])
        assert tail["collections"][0]["collection_id"] == empty_collection["collection_id"]
        assert tail["collections"][0]["photo_count"] == 0
        assert tail["collections"][0]["member_count"] == 0
        assert not tail["has_more"] and tail["next_cursor"] is None
        assert collections._collection_photo_counts(catalog.connection, []) == {}

        # Counts are immediate authority reads, not a cached copy of the first page.
        with catalog.write() as connection:
            connection.execute("DELETE FROM face_assignment_controls WHERE face_id=?", (unassigned,))
            connection.execute("UPDATE face_exclusions SET active=0 WHERE face_id=?", (excluded,))
            connection.execute("UPDATE face_detail_auto_assets SET ready=0 WHERE asset_id=?", (superseded_asset,))
        refreshed = collections.list_collections(catalog, limit=1)["collections"][0]
        assert refreshed["collection_id"] == collection["collection_id"]
        assert refreshed["photo_count"] == 7
        assert refreshed["member_count"] == 3
    finally:
        harness.close()


def test_collection_directory_counts_follow_formal_merge_and_undo(tmp_path):
    harness = PersonHarness(tmp_path)
    try:
        source, target = harness.add_person(), harness.add_person()
        row = _collection(harness, "merge", [source, target])
        shared = harness.add_asset("shared.jpg")
        harness.add_link(harness.add_face(shared), source)
        harness.add_link(harness.add_face(shared, ordinal=1), target)
        harness.add_link(harness.add_face(harness.add_asset("source.jpg")), source)

        def summary():
            result = collections.list_collections(harness.catalog)["collections"]
            return next(item for item in result if item["collection_id"] == row["collection_id"])

        assert (summary()["member_count"], summary()["photo_count"]) == (2, 2)
        merged = harness.service.merge(
            target_person_id=target, target_expected_revision=1,
            sources=[{"person_id": source, "expected_revision": 1}], request_id="count-merge",
        )
        assert (summary()["member_count"], summary()["photo_count"]) == (1, 2)
        harness.service.undo(
            operation_id=merged["operation_id"], request_id="count-undo",
            expected_operation_revision=merged["operation_revision"],
        )
        assert (summary()["member_count"], summary()["photo_count"]) == (2, 2)
    finally:
        harness.close()


@pytest.mark.parametrize("analyze", [False, True])
def test_small_collection_count_work_does_not_grow_with_unrelated_faces(tmp_path, analyze):
    harness = PersonHarness(tmp_path)
    try:
        catalog = harness.catalog
        selected, unrelated = harness.add_person(), harness.add_person()
        chosen = _collection(harness, "chosen", [selected])
        noise = _collection(harness, "unrelated", [unrelated])
        harness.add_link(harness.add_face(harness.add_asset("chosen.jpg")), selected)
        noise_asset = harness.add_asset("noise.jpg")
        template_id = harness.add_face(noise_asset)
        harness.add_link(template_id, unrelated)

        def measure_steps():
            ticks = 0

            def step():
                nonlocal ticks
                ticks += 1
                return 0

            catalog.connection.set_progress_handler(step, 100)
            try:
                result = collections._collection_photo_counts(catalog.connection, [chosen["collection_id"]])
            finally:
                catalog.connection.set_progress_handler(None, 0)
            assert result == {chosen["collection_id"]: 1}
            return ticks * 100

        if analyze:
            catalog.connection.execute("ANALYZE")
        before = measure_steps()
        template = dict(catalog.read_one("SELECT * FROM faces WHERE face_id=?", (template_id,)))
        face_rows, link_rows, people_rows = [], [], []
        for number in range(1, 4001):
            face_id = hashlib.sha256(f"count-noise-{number}".encode()).hexdigest()
            face_rows.append(tuple({**template, "face_id": face_id, "ordinal": number}.values()))
            link_rows.append((face_id, unrelated))
            if number <= 1000:
                people_rows.append((number + 10,))
        with catalog.write() as connection:
            connection.executemany(
                "INSERT INTO faces(" + ",".join(template) + ") VALUES(" + ",".join("?" for _ in template) + ")", face_rows,
            )
            connection.executemany(
                "INSERT INTO face_person_links(face_id,person_id,assignment_kind,decision,confidence,reason,revision,created_at,updated_at) "
                "VALUES(?,?,'AUTO','CONFIRMED',0.9,NULL,1,1,1)", link_rows,
            )
            connection.executemany(
                "INSERT INTO persons(person_id,revision,created_at,updated_at) VALUES(?,1,1,1)", people_rows,
            )
            connection.executemany(
                "INSERT INTO person_collection_members(collection_id,person_id) VALUES(?,?)",
                [(noise["collection_id"], row[0]) for row in people_rows],
            )
            if analyze:
                connection.execute("ANALYZE")
        after = measure_steps()
        # SQLite VM work gives a deterministic complexity guard without a timing SLA.
        # Noise includes both unrelated faces and 1,000 unrelated collection anchors.
        assert after <= max(1000, before * 3), (before, after)
    finally:
        harness.close()
