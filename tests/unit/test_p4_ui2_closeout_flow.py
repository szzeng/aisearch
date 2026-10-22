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


def test_search_history_restores_committed_form_and_snapshot_without_research() -> None:
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

    assert "activeSearchHistoryForm || current.searchForm" in history
    assert "saveSearchSnapshot(SEARCH_SNAPSHOT_KEY + \":\" + token, previousForm)" in history
    assert "JSON.stringify(current.selectedScope || null) !== JSON.stringify(nextScope)" in history
    assert "pendingSearchHistoryTransition" in history
    assert "Boolean(current.searchSnapshotToken && previous)" in history
    assert "window.history.pushState(next" in history
    assert "activeSearchHistoryForm = target.searchForm || null" in popstate
    assert "restoreSearchSnapshot();" in popstate
    assert "sendSearch(" not in popstate
    assert "历史搜索快照不可用；已保留搜索条件，请重新搜索恢复结果。" in source
    assert "function clearSearchSnapshot(preserveCurrentHistoryEntry)" in source


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
