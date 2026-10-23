"""Behavioral probe for map photo hover and MapLibre marker reuse."""
import json
from pathlib import Path
import subprocess

import pytest


@pytest.fixture(scope="module")
def observed():
    result = subprocess.run(
        ["node", "tests/unit/fixtures/p4_ui2_map_photo_hover_probe.js"],
        check=True, capture_output=True, text=True, encoding="utf-8",
    )
    return json.loads(result.stdout)


def test_mouse_hover_tracks_exact_gps_and_reuses_one_marker(observed):
    assert observed["listenerCounts"] == {"pointerover": 1, "keydown": 1}
    assert observed["mouseA"] == {"coords": [0, 0], "visible": "block"}
    assert observed["sameCardStable"] is True
    assert observed["mouseB"] == {"coords": [-73.5, 40.7], "visible": "block"}
    assert observed["mouseLeft"] == "none"
    assert observed["invalidGps"] == "none"
    assert observed["mouseAfterTouch"] == "none"
    assert observed["singleMarker"] == 1


def test_keyboard_focus_and_mouse_priority_restore_correct_source(observed):
    assert observed["focusWithinCard"] is True
    assert observed["mousePriority"] == [-73.5, 40.7]
    assert observed["focusRestored"] == [0, 0]
    assert observed["touchCleared"] == "none"


def test_cluster_leaf_uses_member_coordinate_and_viewer_clears_marker(observed):
    assert observed["cluster"] == {
        "coords": [121.5, 31.2], "firstCoordinates": [121.5, 31.2],
        "visible": "block", "memberCount": 2,
    }
    assert observed["clusterOpened"] == [5, 6]
    assert observed["viewerCleared"] == "none"
    assert observed["mapErrorCleared"] == "none"
    assert observed["hoverRequests"] == 0
    assert observed["mapUnchanged"] == {"center": [120, 30], "zoom": 7}


def test_map_hover_marker_and_photo_actions_keep_local_styles():
    web = Path("src/photo_search/web")
    css = (web / "map.css").read_text(encoding="utf-8")
    js = (web / "map.js").read_text(encoding="utf-8")
    html = (web / "map.html").read_text(encoding="utf-8")

    marker = css.split(".map-photo-hover-marker {", 1)[1].split("}", 1)[0]
    assert "position: absolute" in marker
    assert "z-index: 2 !important" in marker
    assert "pointer-events: none" in marker
    assert ".tile button" not in css
    assert ".tile > .map-photo-action" in css
    assert ".tile > .map-photo-open" in css
    assert 'button.className = "map-photo-action"' in js
    assert "window.PhotoFavorites.button(a.asset_id)" in js
    assert "map.css?v=p4-ui2-map-minor-fix-a-1" in html
    assert "map.js?v=p4-ui2-map-minor-fix-a-2" in html
