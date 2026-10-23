"""People album pages bind the shared timeline without changing curation APIs."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "src" / "photo_search" / "web"


def test_person_and_collection_albums_load_the_shared_timeline() -> None:
    document = (WEB / "persons.html").read_text(encoding="utf-8")
    script = (WEB / "persons-albums.js").read_text(encoding="utf-8")

    assert 'id="pa-timeline"' in document
    assert 'src="/web/photo-timeline.js?v=p4-ui2-copy-daily-ux-1"' in document
    assert 'href="/web/photo-timeline.css?v=p4-favorites-ux-b-1"' in document
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
    styles = (WEB / "persons.css").read_text(encoding="utf-8")

    assert 'id="pa-search-photos"' in document
    assert 'id="pa-photo-query"' in document
    assert 'id="pa-cover"' in document
    assert 'id="pa-merge"' in document
    assert 'id="pt-organize"' in document
    assert ".pa-library-heading > .pa-mode-switch { width: auto; }" in styles
    assert ".pa-library-heading > .pa-mode-switch button { flex: 0 0 auto; }" in styles
    assert 'person_ids: [state.person.person_id]' in script
    assert '"/person-operations/" + encodeURIComponent(operation.operation_id) + "/undo"' in script
    assert 'operation.operation_type !== "CREATE_PERSON_FROM_FACE"' in script
    assert "可切换到‘待整理’查看未命名人物" in script
    assert "当前筛选下没有找到未命名人物" in script
    assert "可切换到‘待整理’给熟悉的人取名" in script


def test_person_directory_album_uses_browser_history_and_restores_on_forward() -> None:
    script = (WEB / "persons-albums.js").read_text(encoding="utf-8")

    assert "openAlbum(card.latestPerson, { pushHistory: true })" in script
    assert "current.personAlbumHistoryEntryId = entryId" in script
    assert "current.personAlbumPersonId = Number(person.person_id)" in script
    assert "if (target && target.photoViewerKey) return" in script
    assert 'state.albumHistoryEntryId && !$("album").hidden' in script
    assert "openPersonById(Number(target.personAlbumPersonId), { historyEntryId: target.personAlbumHistoryEntryId })" in script
