import numpy as np
import pytest

from tools.evaluation.album_region_probe import pool_scores, select_regions, validate_plan_binding


def box(xyxy, score=.9):
    return {"xyxy": xyxy, "score": score}


def test_regions_are_bounded_deduplicated_padded_and_query_independent():
    rows = [box([0, 0, 100, 100], 1), box([10, 10, 40, 40]),
            box([11, 11, 39, 39], .89), box([60, 60, 90, 90], .8),
            box([0, 65, 25, 95], .7)]
    result = select_regions(rows, 100, 100)
    assert [r["detector_index"] for r in result] == [1, 3]
    assert result[0]["crop"] == [7, 7, 43, 43]


def test_no_detection_means_no_regions_not_whole_image_duplication():
    assert select_regions([box([0, 0, 80, 80], .49), box([0, 0, 10, 10])], 100, 100) == []


@pytest.mark.parametrize("row", [box([0, 0, 101, 80]), box([0, 0, float('nan'), 20]),
                                  box([20, 0, 10, 20]), box([0, 0, 40, 40], 2)])
def test_invalid_prediction_fails_instead_of_silent_clipping(row):
    with pytest.raises(ValueError):
        select_regions([row], 100, 100)


def test_pool_preserves_membership_and_original_when_no_local_improvement():
    whole = np.array([[.3, .9, .4], [.5, .1, .2]])
    crops = np.array([[.8, .6, .1], [.4, .9, .1]])
    result = pool_scores(whole, crops, [3, 1, 2], [3, 3, 2])
    np.testing.assert_array_equal(result, [[.8, .9, .4], [.9, .1, .2]])
    np.testing.assert_array_equal(whole, [[.3, .9, .4], [.5, .1, .2]])
    with pytest.raises(ValueError):
        pool_scores(whole, crops, [3, 1, 2], [3, 3, 4])


def test_zero_regions_and_nonfinite_scores():
    whole = np.array([[.1, .2]])
    np.testing.assert_array_equal(pool_scores(whole, np.empty((1, 0)), [1, 2], []), whole)
    with pytest.raises(ValueError):
        pool_scores(whole, np.array([[float("nan")]]), [1, 2], [1])


def test_plan_cannot_move_region_between_valid_parents():
    import copy
    plan = {"parents": [{"asset_id": 1}, {"asset_id": 2}], "regions": [{"parent_id": 1}]}
    sample = {"assets": ["crop"], "source_identity": {"region_plan": copy.deepcopy(plan)}}
    run = {"assets": ["crop"], "sample_sha256": "a"}
    validate_plan_binding(plan, sample, run, "a")
    plan["regions"][0]["parent_id"] = 2
    with pytest.raises(ValueError, match="bound"):
        validate_plan_binding(plan, sample, run, "a")
