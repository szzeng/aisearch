from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from photo_search.api.models import FavoriteBatchRequest


def test_favorite_batch_request_limits_and_requires_distinct_strict_ids() -> None:
    assert len(FavoriteBatchRequest.model_validate({"asset_ids": list(range(1, 501)), "is_favorite": True}).asset_ids) == 500
    for payload in (
        {"asset_ids": list(range(1, 502)), "is_favorite": True},
        {"asset_ids": [1, 1], "is_favorite": True},
        {"asset_ids": [True], "is_favorite": True},
        {"asset_ids": ["1"], "is_favorite": True},
        {"asset_ids": [1], "is_favorite": 1},
    ):
        with pytest.raises(ValidationError):
            FavoriteBatchRequest.model_validate(payload)


def test_favorite_controls_are_loaded_only_on_photo_pages_and_status_reads_are_batched() -> None:
    web = Path(__file__).resolve().parents[2] / "src" / "photo_search" / "web"
    for page in ("index.html", "search.html", "persons.html", "places.html", "map.html"):
        page_path = web / page
        source = page_path.read_text(encoding="utf-8")
        assert re.search(r'favorites-ui\.js\?v=[^"\'&#]+', source), f"{page} must load a versioned favorites script"
        assert re.search(r'styles\.css\?v=[^"\'&#]+', source), f"{page} must load versioned styles"
    index = (web / "index.html").read_text(encoding="utf-8")
    search = (web / "search.html").read_text(encoding="utf-8")
    timeline = (web / "photo-timeline.js").read_text(encoding="utf-8")
    helper = (web / "favorites-ui.js").read_text(encoding="utf-8")
    styles = (web / "styles.css").read_text(encoding="utf-8")
    assert 'class="favorite-star__icon"' in helper
    assert '.favorite-star[aria-pressed="true"]' in styles
    assert '.favorite-star[aria-pressed="true"]::after' in styles
    assert 'id="favorites-order"' in index and 'value="capture_desc"' in index
    assert 'id="photo-select-toggle"' in index and 'id="photo-select-add"' in index
    assert 'id="search-photo-select-toggle"' in search and 'id="search-photo-select-remove"' in search
    assert 'presentation.append(window.PhotoFavorites.button(item.asset_id))' in timeline
    assert 'while (pending.length) chunks.push(pending.splice(0, 100))' in helper
    assert 'var cached = state.values.get(button.dataset.favoriteId)' in helper
    assert 'method: "POST"' in helper and '"/favorites/batch"' in helper


def test_viewer_favorite_changes_refresh_visible_thumbnail_states() -> None:
    web = Path(__file__).resolve().parents[2] / "src" / "photo_search" / "web"
    app = (web / "app.js").read_text(encoding="utf-8")
    search = (web / "search.js").read_text(encoding="utf-8")
    assert "window.PhotoFavorites.invalidate(els.assetGrid)" in app
    assert "window.PhotoFavorites.invalidate(els.browseTimeline)" in app
    assert "window.PhotoFavorites.invalidate(els.resultGrid)" in search
    helper = (web / "favorites-ui.js").read_text(encoding="utf-8")
    assert "state.values.delete(id)" in helper
    assert "if (!visibleIds.has(id)) return" in helper


def test_favorites_selection_limit_and_recycled_late_state_behavior() -> None:
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["node", str(root / "tests" / "unit" / "fixtures" / "favorites_ux_b_probe.js")],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "behavior probes passed" in result.stdout
