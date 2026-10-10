"""Bounded person album projections stay equivalent while avoiding row-wise SQL."""

from __future__ import annotations

from p3_person_fixtures import PersonHarness

from photo_search.persons.albums import (
    _album_summary,
    _photo_page_facts,
    _photo_person_faces,
    people_page,
)
from photo_search.taken_time.read import asset_view


def test_people_page_batches_only_the_selected_forty_people(tmp_path) -> None:
    harness = PersonHarness(tmp_path)
    try:
        catalog = harness.catalog
        for index in range(42):
            person_id = harness.add_person(name=f"人物 {index}" if index % 2 else None)
            if index % 3:
                asset_id = harness.add_asset(f"person-{index}.jpg")
                face_id = harness.add_face(asset_id)
                harness.add_link(face_id, person_id)
                if index % 5 == 0:
                    with catalog.write() as connection:
                        connection.execute(
                            "UPDATE persons SET avatar_face_id = ? WHERE person_id = ?",
                            (face_id, person_id),
                        )

        selected = catalog.read_all(
            "SELECT p.* FROM persons AS p WHERE p.merged_into IS NULL "
            "ORDER BY p.person_id LIMIT 41"
        )
        statements: list[str] = []
        catalog.connection.set_trace_callback(statements.append)
        page = people_page(catalog, kind="all", q="", limit=40, cursor=None)
        catalog.connection.set_trace_callback(None)

        assert len(page["persons"]) == 40 and page["has_more"]
        assert page["next_cursor"]
        assert len(statements) == 8  # page/count/avatar plus bounded collection projections
        expected = [_album_summary(catalog.connection, row) for row in selected[:40]]
        for summary in expected:
            summary["collections"] = []
        assert page["persons"] == expected
    finally:
        harness.close()


def test_photo_page_facts_match_scalar_projections_for_sixty_one_photos(tmp_path) -> None:
    harness = PersonHarness(tmp_path)
    try:
        catalog = harness.catalog
        person_id = harness.add_person(name="同一个人")
        asset_ids: list[int] = []
        for index in range(61):
            asset_id = harness.add_asset(f"album-{index}.jpg")
            asset_ids.append(asset_id)
            faces = 9 if index == 0 else 1
            for ordinal in range(faces):
                face_id = harness.add_face(asset_id, ordinal=ordinal, face_pixels=100 + ordinal)
                harness.add_link(face_id, person_id)
            with catalog.write() as connection:
                connection.execute(
                    "INSERT INTO asset_metadata(asset_id, taken_at_raw, taken_at_sort, "
                    "time_source, time_confidence, timezone_offset, camera) "
                    "VALUES (?, ?, ?, 'EXIF_ORIGINAL', 'HIGH', 480, 'Fixture Camera')",
                    (asset_id, f"fixture-{index}", 63800000000000 + index),
                )

        statements: list[str] = []
        catalog.connection.set_trace_callback(statements.append)
        times, faces = _photo_page_facts(catalog.connection, asset_ids=asset_ids, person_id=person_id)
        catalog.connection.set_trace_callback(None)
        assert len(statements) == 3  # rule gate, batch time facts, batch face projections
        for asset_id in asset_ids:
            assert times[asset_id] == asset_view(catalog.connection, asset_id)
            assert faces.get(asset_id, {"person_faces": [], "person_faces_has_more": False}) == (
                _photo_person_faces(catalog.connection, asset_id=asset_id, person_id=person_id)
            )
        assert len(faces[asset_ids[0]]["person_faces"]) == 8
        assert faces[asset_ids[0]]["person_faces_has_more"] is True
    finally:
        harness.close()
