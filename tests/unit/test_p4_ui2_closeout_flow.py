from __future__ import annotations

from pathlib import Path


def test_filter_only_return_requeries_authoritative_first_page() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "photo_search"
        / "web"
        / "search.js"
    ).read_text(encoding="utf-8")
    save = source.split("function saveSearchSnapshot(", 1)[1].split(
        "function restoreSearchSnapshot(", 1
    )[0]
    restore = source.split("function restoreSearchSnapshot(", 1)[1].split(
        "function restoreValidatedSearchSnapshot(", 1
    )[0]

    assert "!state.search.hits.length" not in save
    assert "lastRequestBody: state.search.lastRequestBody" in save
    assert "placeAware: state.search.placeAware" in save
    assert 'saved.search_session_id === "filter-only"' in restore
    assert 'request("/search/place-aware", { method: "POST", body: refreshBody })' in restore
    assert 'refreshBody.request_id = newRequestId("return")' in restore
    assert "delete refreshBody.search_session_id" in restore
    assert "delete refreshBody.cursor" in restore
    assert "renderPage(result.body, false);" in restore
    assert "restoreValidatedSearchSnapshot(saved);" in restore
    assert "restoreGeneration !== state.search.requestGeneration" in restore


def test_search_submissions_replace_the_current_history_entry_and_restore_without_research() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "photo_search"
        / "web"
        / "search.js"
    ).read_text(encoding="utf-8")
    history = source.split("function beginSearchHistoryEntry()", 1)[1].split(
        "function applySearchHistoryForm(", 1
    )[0]
    popstate = source.split("function handleSearchHistoryPopState(", 1)[1].split(
        "function submitSearch(", 1
    )[0]

    assert "current.searchForm = currentForm" in history
    assert "current.selectedScope = nextScope" in history
    assert "window.history.replaceState(current" in history
    assert "window.history.pushState" not in history
    assert 'window.sessionStorage.removeItem(SEARCH_SNAPSHOT_KEY + ":" + current.searchSnapshotToken)' in history
    assert "setSearchHistoryEntryId(current.searchHistoryEntryId)" in history
    assert "activeSearchHistoryForm = target.searchForm || null" in popstate
    assert "restoreSearchSnapshot();" in popstate
    assert "sendSearch(" not in popstate
    assert "历史搜索快照不可用；已保留搜索条件，请重新搜索恢复结果。" in source
    assert "function clearSearchSnapshot(preserveCurrentHistoryEntry)" in source
    assert "if (!continuation) beginSearchHistoryEntry();" in source


def test_clearing_search_snapshot_removes_the_current_history_reference() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "photo_search"
        / "web"
        / "search.js"
    ).read_text(encoding="utf-8")
    clear_snapshot = source.split("function clearSearchSnapshot(", 1)[1].split(
        "function consumeEntryQuery(", 1
    )[0]

    assert 'window.sessionStorage.removeItem(SEARCH_SNAPSHOT_KEY + ":" + current.searchSnapshotToken)' in clear_snapshot
    assert "delete next.searchSnapshotToken" in clear_snapshot
    assert 'window.history.replaceState(next, "", window.location.href)' in clear_snapshot
    assert "if (!preserveCurrentHistoryEntry" in clear_snapshot


def test_search_scroll_is_sampled_on_page_exit_without_resaving_the_result_snapshot() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "photo_search"
        / "web"
        / "search.js"
    ).read_text(encoding="utf-8")
    save_scroll = source.split("function saveSearchHistoryScrollPosition()", 1)[1].split(
        "function saveCurrentSearchHistoryEntry(", 1
    )[0]
    restore_scroll = source.split("function restoreValidatedSearchSnapshot(", 1)[1].split(
        "function prepareSearchSimilarReturn(", 1
    )[0]

    assert "current.searchHistoryEntryId !== activeSearchHistoryEntryId" in save_scroll
    assert "current.searchScrollY = Math.max(0, scrollY)" in save_scroll
    assert "saveSearchSnapshot(" not in save_scroll
    assert 'window.addEventListener("pagehide", saveSearchHistoryScrollPosition)' in source
    assert "currentHistoryState.searchScrollY" in restore_scroll
    assert "saved.scrollY = Math.max(0, Number(currentHistoryState.searchScrollY))" in restore_scroll


def test_invalid_filters_do_not_clear_the_current_search_snapshot() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "photo_search"
        / "web"
        / "search.js"
    ).read_text(encoding="utf-8")
    submit = source.split("function submitSearch() {", 1)[1].split(
        "function continueSearch()", 1
    )[0]

    assert submit.index("var filters = collectFilters();") < submit.index("if (filters.problem)")
    assert submit.index("if (filters.problem)") < submit.index("clearSearchSnapshot();")
    assert "return Promise.resolve(false);" in submit


def test_map_full_detail_return_saves_only_the_view_and_reloads_authoritative_members() -> None:
    web = Path(__file__).resolve().parents[2] / "src" / "photo_search" / "web"
    map_source = (web / "map.js").read_text(encoding="utf-8")
    app_source = (web / "app.js").read_text(encoding="utf-8")
    save = map_source.split("function saveMapReturnSnapshot()", 1)[1].split(
        "function drawGrid(", 1
    )[0]
    fetch_snapshot = map_source.split("function fetchSnapshot(", 1)[1].split(
        "function refresh(", 1
    )[0]
    draw_detail = app_source.split("function drawDetail(", 1)[1].split(
        "function buildDetail", 1
    )[0]
    map_init = map_source.split("function init() {", 1)[1].split(
        "function startMap()", 1
    )[0]

    assert "center: [Number(center.lng), Number(center.lat)]" in save
    assert "zoom: zoom" in save and "version: version" in save
    assert "assets:" not in save and "cursor:" not in save
    assert "saveMapReturnSnapshot();" in map_source
    assert "restoreState.viewOnly && restoreState.version === version" in fetch_snapshot
    assert "drawGrid(assets, true);" in fetch_snapshot
    assert "hasValidMapReturn" in draw_detail
    assert "mapReturn.version !== null && isFinite(Number(mapReturn.version))" in draw_detail
    assert 'try { sessionStorage.removeItem(MAP_RETURN_KEY); } catch (_) {}' in map_init
    assert "saved.version === s.member_version" in map_init
    assert "savedCenter[0] >= -180" in map_init and "savedCenter[1] >= -90" in map_init
    assert "Number(saved.zoom) >= 0" in map_init and "Number(saved.zoom) <= 19" in map_init


def test_legacy_default_snapshot_seeds_the_current_history_entry() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "photo_search"
        / "web"
        / "search.js"
    ).read_text(encoding="utf-8")
    seed = source.split("function seedSearchHistoryEntryFromSnapshot(", 1)[1].split(
        "function saveCurrentSearchHistoryEntry(", 1
    )[0]
    restore = source.split("function restoreSearchSnapshot(", 1)[1].split(
        "function restoreValidatedSearchSnapshot(", 1
    )[0]

    assert "restoreKey !== SEARCH_SNAPSHOT_KEY" in seed
    assert 'current.searchSnapshotToken = token' in seed
    assert "current.searchForm = saved.form" in seed
    assert "current.selectedScope = saved.selectedScope || null" in seed
    assert "seedSearchHistoryEntryFromSnapshot(saved, restoreKey)" in restore
    assert 'window.sessionStorage.setItem(SEARCH_SNAPSHOT_KEY + ":" + token, JSON.stringify(saved))' in seed


def test_search_response_keeps_the_form_captured_when_the_request_was_submitted() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "photo_search"
        / "web"
        / "search.js"
    ).read_text(encoding="utf-8")
    save = source.split("function saveCurrentSearchHistoryEntry(", 1)[1].split(
        "function beginSearchHistoryEntry(", 1
    )[0]
    send = source.split("function sendSearch(continuation)", 1)[1].split(
        "function sendTextSearch(", 1
    )[0]

    assert "formOverride || activeSearchHistoryForm || current.searchForm" in save
    assert 'saveSearchSnapshot(SEARCH_SNAPSHOT_KEY + ":" + token, committedForm, committedScope)' in save
    assert "current.selectedScope = committedScope" in save
    assert "var requestSearchForm = continuation ? null : searchFormSnapshot()" in send
    assert "var requestSearchScope = continuation ? undefined : (state.search.selectedScope || null)" in send
    assert "saveCurrentSearchHistoryEntry(requestSearchForm, responseSearchScope)" in send
