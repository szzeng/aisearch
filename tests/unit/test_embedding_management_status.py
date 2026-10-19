from __future__ import annotations

from photo_search.models.embedding.management import embedding_management_status


def test_status_does_not_invent_an_active_model_or_coverage(tmp_path):
    result = embedding_management_status(tmp_path)

    assert result["status"] == "ok"
    assert result["active_profile"] is None
    assert result["coverage_state"] == "CHECK_REQUIRED"
    assert result["orphan_tables"] == []
    assert result["providers"]
    assert all(not item["is_active"] for item in result["providers"])
    assert all(item["coverage"]["required"] is None for item in result["providers"])


def test_gallery_model_panel_and_search_page_have_status_entry_points():
    from photo_search.api.web import read_static

    gallery = read_static("index.html").decode("utf-8")
    search = read_static("search.html").decode("utf-8")
    app_js = read_static("app.js").decode("utf-8")
    search_js = read_static("search.js").decode("utf-8")

    assert 'id="search-model-panel"' in gallery
    assert 'id="active-search-model"' in gallery
    assert 'id="current-search-model"' in search
    assert "/embedding-management/status" in app_js
    assert "/embedding-management/status" in search_js
