from __future__ import annotations

from p3_persons_page_fixtures import add_link, add_person, insert_face, seed_library, set_control

from photo_search.face.contract import FACE_DETAIL_SPEC_REVISION, FACE_SPEC_REVISION
from photo_search.persons.albums import inbox_hidden_page, set_inbox_visibility
from photo_search.persons.quality_policy import DISPLAY_TIER, TIERS, qualifies
from tools.diagnostics.person_face_quality import collect_quality_report


def test_display_quality_boundaries_missing_values_and_read_only_diagnostic(api_env):
    assert qualifies(quality_state="OK", det_score=.70, face_pixels=56, blur_variance=40)
    assert not qualifies(quality_state="OK", det_score=.6999, face_pixels=56, blur_variance=40)
    assert not qualifies(quality_state="LOW", det_score=.9, face_pixels=100, blur_variance=200)
    assert not qualifies(quality_state="OK", det_score=.70, face_pixels=55.99, blur_variance=40)
    assert not qualifies(quality_state="OK", det_score=.70, face_pixels=56, blur_variance=39.99)
    assert qualifies(quality_state=None, det_score=.2, face_pixels=2, blur_variance=1)
    assert [tier.name for tier in TIERS] == ["loose", "balanced", "strict"]
    assert DISPLAY_TIER.name == "balanced"
    for tier in TIERS:
        assert qualifies(quality_state="OK", det_score=tier.det_score, face_pixels=tier.face_pixels, blur_variance=tier.blur_variance, tier=tier)
        assert not qualifies(quality_state="OK", det_score=tier.det_score - .0001, face_pixels=tier.face_pixels, blur_variance=tier.blur_variance, tier=tier)

    photos = seed_library(api_env, {
        "quality.jpg": [(10, 10, 90, 90)],
        "ordinary.jpg": [(20, 20, 100, 100)],
    })
    catalog = api_env.service.catalog
    person = add_person(catalog)
    face_id = photos["quality.jpg"]["faces"][0]
    ordinary_person = add_person(catalog)
    add_link(catalog, photos["ordinary.jpg"]["faces"][0], ordinary_person)
    add_link(catalog, face_id, person)
    with catalog.write() as connection:
        connection.execute(
            "UPDATE faces SET det_score=.69,face_pixels=100,blur_variance=100,spec_revision=? WHERE face_id=?",
            (FACE_DETAIL_SPEC_REVISION, face_id),
        )
    before = tuple(catalog.connection.iterdump())
    report = collect_quality_report(catalog.connection)
    after = tuple(catalog.connection.iterdump())
    assert before == after
    assert report["read_only"] is True
    assert report["visible_face_count"] >= 1
    assert "incomplete_metric_face_counts_by_spec_revision" in report
    assert report["tier_suppressed_face_counts"]["balanced"] >= 1
    assert report["unnamed_group_impact"]["balanced"]["fully_suppressed_unnamed_groups"] == 1
    percentiles = report["metric_percentiles_by_spec_revision"]
    assert percentiles[FACE_DETAIL_SPEC_REVISION]["processing_kind"] == "source_detail"
    assert percentiles[FACE_SPEC_REVISION]["processing_kind"] == "ordinary_current"
    assert set(percentiles[FACE_DETAIL_SPEC_REVISION]["det_score"]) == {"p10", "p25", "p50", "p75"}
    assert report["overlapping_failure_reasons"]["balanced"]


def test_quality_view_filters_unnamed_and_inbox_but_preserves_recovery_and_manual(api_env):
    photos = seed_library(api_env, {
        "合照.jpg": [(20, 20, 90, 90), (150, 30, 220, 100)],
        "爸爸.jpg": [(40, 40, 120, 120)],
        "待整理.jpg": [(30, 30, 90, 90), (130, 30, 190, 90)],
    })
    catalog = api_env.service.catalog
    low_face = insert_face(catalog, asset_id=photos["待整理.jpg"]["asset_id"], sha256=photos["待整理.jpg"]["sha256"], bbox=(5, 5, 45, 45), ordinal=2)
    mixed_low = insert_face(catalog, asset_id=photos["待整理.jpg"]["asset_id"], sha256=photos["待整理.jpg"]["sha256"], bbox=(50, 5, 90, 45), ordinal=3)
    mixed_clear = insert_face(catalog, asset_id=photos["待整理.jpg"]["asset_id"], sha256=photos["待整理.jpg"]["sha256"], bbox=(95, 5, 135, 45), ordinal=4)
    protected_face = insert_face(catalog, asset_id=photos["待整理.jpg"]["asset_id"], sha256=photos["待整理.jpg"]["sha256"], bbox=(140, 5, 180, 45), ordinal=5)
    single_suggestion = insert_face(catalog, asset_id=photos["待整理.jpg"]["asset_id"], sha256=photos["待整理.jpg"]["sha256"], bbox=(185, 5, 225, 45), ordinal=6)
    low_person = add_person(catalog, avatar_face_id=low_face)
    mixed_person = add_person(catalog, avatar_face_id=mixed_low)
    protected_person = add_person(catalog)
    add_link(catalog, low_face, low_person)
    add_link(catalog, mixed_low, mixed_person)
    add_link(catalog, mixed_clear, mixed_person)
    # A quality-filtered candidate group must be recalculated from eligible faces;
    # removing one photo leaves the other as an ordinary single-face card.
    add_link(catalog, photos["待整理.jpg"]["faces"][0], mixed_person, decision="SUGGESTED")
    add_link(catalog, photos["爸爸.jpg"]["faces"][0], mixed_person, decision="SUGGESTED")
    add_link(catalog, single_suggestion, low_person, decision="SUGGESTED")
    add_link(catalog, protected_face, protected_person, assignment_kind="MANUAL")
    set_control(catalog, protected_face, "MANUAL_ASSIGNED")
    set_control(catalog, photos["待整理.jpg"]["faces"][1], "MANUAL_UNASSIGNED")
    with catalog.write() as connection:
        connection.executemany(
            "UPDATE faces SET det_score=?,face_pixels=?,blur_variance=? WHERE face_id=?",
            [(.69, 100, 100, low_face), (.69, 100, 100, mixed_low), (.70, 56, 40, mixed_clear), (.60, 40, 20, protected_face)],
        )
        connection.execute(
            "UPDATE faces SET det_score=.69,face_pixels=100,blur_variance=100 WHERE face_id=?",
            (photos["待整理.jpg"]["faces"][0],),
        )

    async def scenario(client):
        await api_env.authenticate(client)
        old = (await client.get("/api/v1/persons/browse", params={"kind": "unnamed", "visibility": "visible", "sort": "photos_desc"})).json()
        clean = (await client.get("/api/v1/persons/browse", params={"kind": "unnamed", "visibility": "visible", "sort": "photos_desc", "quality_view": "clean"})).json()
        assert {p["person_id"] for p in old["persons"]} >= {low_person, mixed_person, protected_person}
        assert {p["person_id"] for p in clean["persons"]} == {mixed_person, protected_person}
        mixed = next(p for p in clean["persons"] if p["person_id"] == mixed_person)
        assert mixed["avatar_face_id"] == mixed_low  # Explicit user cover stays first.
        assert mixed["asset_count"] == 1

        groups_all = (await client.get("/api/v1/faces/inbox/groups", params={"kind": "suggested", "quality_view": "all", "limit": 1})).json()
        groups_clean = (await client.get("/api/v1/faces/inbox/groups", params={"kind": "suggested", "quality_view": "clean", "limit": 30})).json()
        assert groups_all["cards"][0]["type"] == "candidate_group"
        assert groups_all["cards"][0]["photo_count"] == 2
        assert all(card["type"] == "single_face" for card in groups_clean["cards"])
        group_faces = (await client.get("/api/v1/faces/inbox/groups/candidate:" + str(mixed_person), params={"kind": "suggested", "quality_view": "all", "limit": 30})).json()
        assert len(group_faces["faces"]) == 2
        assert (await client.get("/api/v1/faces/inbox/groups", params={"kind": "suggested", "quality_view": "clean", "cursor": groups_all["next_cursor"]})).status_code == 409

        inbox_all = (await client.get("/api/v1/faces/inbox", params={"quality_view": "all"})).json()
        inbox_legacy = (await client.get("/api/v1/faces/inbox")).json()
        assert {f["face_id"] for f in inbox_legacy["faces"]} == {f["face_id"] for f in inbox_all["faces"]}
        inbox_clean = (await client.get("/api/v1/faces/inbox", params={"quality_view": "clean"})).json()
        inbox_quality = (await client.get("/api/v1/faces/inbox", params={"quality_view": "clean", "kind": "quality"})).json()
        inbox_manual = (await client.get("/api/v1/faces/inbox", params={"quality_view": "clean", "kind": "manual"})).json()
        all_ids = {face["face_id"] for face in inbox_all["faces"]}
        clean_ids = {face["face_id"] for face in inbox_clean["faces"]}
        assert photos["待整理.jpg"]["faces"][0] in all_ids
        assert photos["待整理.jpg"]["faces"][0] not in clean_ids
        assert {f["face_id"] for f in inbox_quality["faces"]} >= {photos["待整理.jpg"]["faces"][0]}
        assert {f["face_id"] for f in inbox_manual["faces"]} >= {photos["待整理.jpg"]["faces"][1]}
        low_projection = next(f for f in inbox_quality["faces"] if f["face_id"] == photos["待整理.jpg"]["faces"][0])
        assert low_projection["organization_reason"] != "DISPLAY_LOW_QUALITY"
        assert low_projection["display_quality_reason"] == "DISPLAY_LOW_QUALITY"
        summary = (await client.get("/api/v1/faces/inbox/summary", params={"quality_view": "clean"})).json()
        assert summary["quality_hidden"] >= 1 and summary["quality_view"] == "clean"
        assert summary["total"] <= summary["all_total"]
        paged_all = (await client.get("/api/v1/faces/inbox", params={"quality_view": "all", "limit": 1})).json()
        assert paged_all["next_cursor"]
        assert (await client.get("/api/v1/faces/inbox", params={"quality_view": "all", "cursor": paged_all["next_cursor"]})).status_code == 200
        assert (await client.get("/api/v1/faces/inbox", params={"quality_view": "clean", "cursor": paged_all["next_cursor"]})).status_code == 409
        set_inbox_visibility(catalog, face_ids=[photos["待整理.jpg"]["faces"][0]], hidden=True)
        assert photos["待整理.jpg"]["faces"][0] not in {
            f["face_id"] for f in (await client.get("/api/v1/faces/inbox", params={"quality_view": "clean", "kind": "quality"})).json()["faces"]
        }
        hidden = inbox_hidden_page(catalog, limit=30, cursor=None)
        assert photos["待整理.jpg"]["faces"][0] in {f["face_id"] for f in hidden["faces"]}

    api_env.scenario(scenario)
