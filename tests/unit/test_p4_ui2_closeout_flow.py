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
