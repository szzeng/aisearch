"""People album pages bind the shared timeline without changing curation APIs."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "src" / "photo_search" / "web"


def test_person_and_collection_albums_load_the_shared_timeline() -> None:
    document = (WEB / "persons.html").read_text(encoding="utf-8")
    script = (WEB / "persons-albums.js").read_text(encoding="utf-8")

    assert 'id="pa-timeline"' in document
    assert 'src="/web/photo-timeline.js?v=p4-ui-photos-b-1"' in document
    assert 'href="/web/photo-timeline.css?v=p4-ui-photos-b-1"' in document
    assert 'kind: "person", person_id:' in script
    assert 'kind: "collection", collection_id:' in script
    assert '"/api/v1/timeline/assets?"' in script
    assert "state.timeline.setScope(scope)" in script
    assert 'body.code === "INVALID_CURSOR"' in script
    assert 'responseScope.collection_instance !== state.collection.instance_token' in script
    assert 'Number(responseScope.collection_revision) !== Number(state.collection.revision)' in script
    assert 'const current = state.collectionById.get(String(collectionId))' in script
    assert 'await reloadCollections()' in script
    assert 'state.photoCursor = body.next_cursor || null' in script
    assert 'else (body.assets || []).forEach(item => rememberTimelineAsset(item.asset_id))' in script
    assert 'item.date_state === "unknown"' in script
    assert 'item.date_state === "pending"' in script
    assert 'item.time_label === "capture" ? "可信拍摄时间"' in script
    assert 'item.time_label === "file_time" ? "文件时间"' in script
    assert '"collection-expired"' in script or 'collectionExpired = "true"' in script
    assert "state.photoIds.length > 400" in script


def test_people_search_and_curation_actions_keep_their_existing_paths() -> None:
    document = (WEB / "persons.html").read_text(encoding="utf-8")
    script = (WEB / "persons-albums.js").read_text(encoding="utf-8")

    assert 'id="pa-search-photos"' in document
    assert 'id="pa-photo-query"' in document
    assert 'id="pa-cover"' in document
    assert 'id="pa-merge"' in document
    assert 'id="pt-organize"' in document
    assert 'person_ids: [state.person.person_id]' in script
    assert '"/person-operations/" + encodeURIComponent(operation.operation_id) + "/undo"' in script
    assert 'operation.operation_type !== "CREATE_PERSON_FROM_FACE"' in script
