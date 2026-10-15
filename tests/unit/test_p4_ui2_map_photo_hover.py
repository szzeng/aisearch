"""Behavioral probe for map photo hover and MapLibre marker reuse."""
import json
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
