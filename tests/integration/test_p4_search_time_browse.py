"""Read-only time browsing over the existing bounded semantic candidate session."""

from __future__ import annotations

from photo_search.search.retrieval import SearchScope
from photo_search.search.sessions import SessionEntry


def test_semantic_time_browse_sorts_only_frozen_candidates_and_rejects_stale_revision(api_env) -> None:
    for index in range(4):
        api_env.add_photo(
            f"time-browse/{index}.jpg",
            size=(24, 16),
            color=(index * 40, index * 30, index * 20),
        )
    source = api_env.service.register_source(
        display_name="time-browse fixture", root_path=api_env.library
    )
    api_env.service.run_scan(source["source_id"])
    catalog = api_env.service.catalog
    ids = [int(row[0]) for row in catalog.read_all("SELECT asset_id FROM assets ORDER BY asset_id")]
    with catalog.write() as conn:
        for asset_id, sort_key in zip(ids[:3], (100, 300, 300), strict=True):
            conn.execute(
                "INSERT INTO asset_metadata(asset_id,taken_at_sort,time_source,time_confidence) "
                "VALUES(?,?,'EXIF_ORIGINAL','MEDIUM')",
                (asset_id, sort_key),
            )
        catalog_revision = int(conn.execute(
            "SELECT revision FROM catalog_filter_revision WHERE revision_id=1"
        ).fetchone()[0])
        suggestion_revision = int(conn.execute(
            "SELECT revision FROM place_suggestion_revision WHERE revision_id=1"
        ).fetchone()[0])

    session = api_env.context.search_sessions.create(
        query_hash="time-browse-query-hash",
        entries=[
            SessionEntry(asset_id, score=1.0 / (rank + 1), matched_by={"vector": rank},
                         hit_reason="SEMANTIC", semantic_ready=True)
            for rank, asset_id in enumerate(ids[:3])
        ],
        profile_id=None,
        image_generation_id=None,
        space_id=None,
        dimension=None,
        catalog_filter_revision=catalog_revision,
        suggestion_revision=suggestion_revision,
        fusion_config={},
        query={"text": "semantic fixture"},
        filters={"availability": "present"},
        channels={"vector": {"engaged": True}},
        pending_semantic=None,
        search_projection={},
        state="OK",
        extra={
            "search_scope": SearchScope.build(availability="present"),
            "requested_profile_id": None,
        },
    )

    async def scenario(client):
        await api_env.authenticate(client)
        first = await client.get(
            f"/api/v1/search/sessions/{session.session_id}/time", params={"limit": 2}
        )
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["candidate_scope"] == "current_semantic_candidate_snapshot"
        assert body["candidates_truncated"] is False
        assert [item["asset_id"] for item in body["assets"]] == [ids[2], ids[1]]
        assert [item["semantic_rank"] for item in body["assets"]] == [3, 2]
        second = await client.get(
            f"/api/v1/search/sessions/{session.session_id}/time",
            params={"limit": 2, "cursor": body["next_cursor"]},
        )
        assert second.status_code == 200, second.text
        assert second.json()["assets"][0]["asset_id"] == ids[0]
        with catalog.write() as conn:
            conn.execute(
                "UPDATE file_locations SET availability='MISSING' WHERE asset_id=?",
                (ids[0],),
            )
            unchanged_revision = int(conn.execute(
                "SELECT revision FROM catalog_filter_revision WHERE revision_id=1"
            ).fetchone()[0])
            assert unchanged_revision == catalog_revision
        ineligible = await client.get(
            f"/api/v1/search/sessions/{session.session_id}/time",
            params={"limit": 2, "cursor": body["next_cursor"]},
        )
        assert ineligible.status_code == 200, ineligible.text
        assert ineligible.json()["assets"] == []
        with catalog.write() as conn:
            conn.execute("UPDATE catalog_filter_revision SET revision=revision+1 WHERE revision_id=1")
        stale = await client.get(
            f"/api/v1/search/sessions/{session.session_id}/time",
            params={"limit": 2, "cursor": body["next_cursor"]},
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == "CATALOG_FILTER_REVISION_STALE"
        session.profile_id = "changed-profile"
        stale_binding = await client.get(
            f"/api/v1/search/sessions/{session.session_id}/time",
            params={"limit": 2},
        )
        assert stale_binding.status_code == 409
        assert stale_binding.json()["code"] == "PROFILE_GENERATION_MISMATCH"

    api_env.scenario(scenario)
