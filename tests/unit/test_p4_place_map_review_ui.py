"""Focused counterexamples for the current map UI, with JUnit evidence."""
import json
import subprocess

import pytest


@pytest.fixture(scope="module")
def observed():
    result = subprocess.run(
        ["node", "tests/unit/fixtures/p4_place_map_review_probe.js"],
        check=True, capture_output=True, text=True, encoding="utf-8",
    )
    return json.loads(result.stdout)


def test_reject_mixed_member_versions(observed):
    # A second assets request or rejection/reload is required, rather than publishing 1 vs 2.
    assert observed["mixed"]["requests"] > 2, observed["mixed"]


def test_missing_map_component_still_fetches_grid(observed):
    assert observed["missing"]["requests"] > 0, observed["missing"]
    assert observed["missing"]["more_requests"] > 0, observed["missing"]


def test_identical_cluster_opens_members_before_cluster_disappears(observed):
    # The source's clusterMaxZoom=16; at expansion zoom 17 the two points overlap unclustered.
    assert observed["identical"]["leaves_calls"] > 0, observed["identical"]
    assert observed["identical"]["members"] == 2, observed["identical"]
    assert observed["identical"]["detailHrefs"] == ["/#/asset/17", "/#/asset/18"], observed["identical"]


def test_late_old_view_response_is_ignored(observed):
    assert observed["race"]["version"] == 2, observed["race"]
    assert observed["race"]["title"] == "当前范围 · 2 张照片", observed["race"]


"""The focus response must survive either normal map load order."""
@pytest.mark.parametrize("order", ["early", "late"])
def test_detail_link_focuses_selected_asset(order):
    result = subprocess.run(
        ["node", "tests/unit/fixtures/p4_place_map_focus_probe.js"],
        check=True, capture_output=True, text=True, encoding="utf-8",
    )
    observed = json.loads(result.stdout)[order]
    assert observed["jump_count"] == 1, observed
