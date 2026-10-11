"""Photo-scoped OPEN reviews: real API boundary, privacy and asset-bound keysets."""
from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from p3_redetection_fixtures import RedetectionHarness, make_record, shifted_bbox, shifted_landmarks

from photo_search.api.errors import ApiError
from photo_search.api.settings import ApiSettings
from photo_search.app import create_app
from photo_search.persons import read


@pytest.fixture()
def review_env(tmp_path: Path):
    harness = RedetectionHarness(tmp_path)
    try:
        asset = harness.add_asset("synthetic-review.png")
        dad = harness.add_person(name="张三")
        other = harness.add_person(name="李四")
        first = harness.seed_anchor(asset, person_id=dad, ordinal=0)
        second = harness.seed_anchor(asset, mode="MANUAL_UNASSIGNED", ordinal=1, excluded=[dad])
        harness.write_face_document(asset, [make_record(ordinal=0, bbox=shifted_bbox(0), landmarks=shifted_landmarks(0))])
        live = harness.add_face(asset, ordinal=2)
        harness.add_link(live, other)
        empty = harness.add_asset("no-review.png")
        yield harness, asset, sorted([first, second]), empty, live
    finally:
        harness.close()


def test_photo_reviews_are_scoped_private_bounded_and_read_only(review_env) -> None:
    harness, asset, old_faces, empty, live = review_env
    before = harness.library_snapshot()
    changes = harness.catalog.connection.total_changes
    first = read.asset_mapping_page(harness.catalog, asset, limit=1)
    assert first["groups"][0]["old_face_id"] == old_faces[0]
    assert first["has_more"] and first["next_cursor"]
    second = read.asset_mapping_page(harness.catalog, asset, limit=1, cursor=first["next_cursor"])
    assert second["groups"][0]["old_face_id"] == old_faces[1]
    assert not second["has_more"]
    assert read.asset_mapping_page(harness.catalog, empty, limit=1)["groups"] == []
    assert any(group["has_active_exclusions"] for group in first["groups"] + second["groups"])
    assert live not in old_faces
    rendered = json.dumps(first) + json.dumps(second)
    for private in ("bbox", "landmarks", "path", "vector", "confidence", "payload_sha256"):
        assert f'"{private}"' not in rendered
    assert harness.catalog.connection.total_changes == changes
    assert harness.library_snapshot() == before
    plan = harness.catalog.read_all(
        "EXPLAIN QUERY PLAN SELECT g.old_face_id FROM faces AS f INDEXED BY idx_faces_asset "
        "JOIN face_redetection_groups AS g ON g.old_face_id=f.face_id WHERE f.asset_id=? "
        "AND g.asset_id=? AND g.state='OPEN' AND g.old_face_id>? ORDER BY g.old_face_id LIMIT 2",
        (asset, asset, ""),
    )
    assert any("idx_faces_asset" in str(row["detail"]) for row in plan)
    assert not any("SCAN g" in str(row["detail"]) for row in plan)


def test_photo_review_cursor_and_bound_refusals(review_env) -> None:
    harness, asset, _, empty, _ = review_env
    cursor = read.asset_mapping_page(harness.catalog, asset, limit=1)["next_cursor"]
    for bad in (cursor, "fm1:bad", "fa1:" + base64.urlsafe_b64encode(b'["x", "z"]').decode()):
        with pytest.raises(ApiError):
            read.asset_mapping_page(harness.catalog, empty, limit=1, cursor=bad)
    for limit in (0, 201, "bad"):
        with pytest.raises(ApiError):
            read.asset_mapping_page(harness.catalog, asset, limit=limit)


def test_photo_review_api_uses_session_and_real_safe_projection(review_env) -> None:
    harness, asset, old_faces, empty, live = review_env
    app = create_app(settings=ApiSettings(data_root=harness.data_root, session_token="synthetic-ux-review"))

    async def scenario():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            assert (await client.get(f"/api/v1/assets/{asset}/face-review")).status_code == 401
            assert (await client.get("/api/v1/session?token=synthetic-ux-review")).status_code == 200
            response = await client.get(f"/api/v1/assets/{asset}/face-review?limit=1")
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["groups"][0]["old_face_id"] == old_faces[0]
            assert "has_active_exclusions" in body["groups"][0]
            assert (await client.get(f"/api/v1/assets/{asset}/faces")).json()["faces"]  # Other live faces do not hide reviews.
            assert (await client.get(f"/api/v1/assets/{empty}/face-review")).json()["groups"] == []
            assert (await client.get(f"/api/v1/assets/{empty}/face-review", params={"cursor": body["next_cursor"]})).status_code == 422
            assert (await client.get(f"/api/v1/assets/{asset}/face-review?limit=201")).status_code == 422
            assert (await client.get("/api/v1/assets/999999/face-review")).status_code == 404
    try:
        asyncio.run(scenario())
    finally:
        app.state.api.close()
