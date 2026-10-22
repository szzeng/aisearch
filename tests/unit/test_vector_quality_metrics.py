"""CPU metric conventions and refusal boundaries; no network or model execution."""
from __future__ import annotations

import json

import numpy as np
import pytest

from tools.evaluation import vector_quality_metrics as metrics


@pytest.fixture(autouse=True)
def clean_tracking():
    metrics.HASHES.clear()
    metrics.CHECKS = 0
    yield
    metrics.HASHES.clear()


def test_metrics_use_complete_positives_and_bounded_precision_denominator():
    result = metrics.metrics([1, 2, 3, 4], {1: True, 2: False, 3: True, 4: False})
    assert result['AP'] == pytest.approx((1 + 2 / 3) / 2)
    assert result['P20'] == .5
    assert result['Recall100'] == 1
    assert result['first_unannotated_rank'] == 2
    assert result['positive_rank_percentiles']['100'] == 3


def test_no_positive_truth_does_not_claim_recall_or_ap():
    result = metrics.metrics([1, 2], {1: False, 2: False})
    assert result['AP'] is None and result['Recall100'] is None
    assert result['P100'] == 0


@pytest.mark.parametrize('ranking', [[1, 1], [1], [1, 3]])
def test_incomplete_duplicate_or_foreign_ranking_refused(ranking):
    with pytest.raises(ValueError, match='complete unique'):
        metrics.validate_rankings({'cat': ranking}, [1, 2], ['cat'])


def test_missing_query_refused():
    with pytest.raises(ValueError, match='query IDs'):
        metrics.validate_rankings({'dog': [1, 2]}, [1, 2], ['cat'])


def test_area_diagnostic_uses_full_rank_without_turning_other_positives_negative():
    labels = {i: i in (1, 102) for i in range(1, 103)}
    assets = {1: {'category_max_area_fraction': {'cat': .5}},
              102: {'category_max_area_fraction': {'cat': .005}}}
    groups = [{'name': 'tiny', 'lower_exclusive': 0, 'upper_inclusive': .01},
              {'name': 'rest', 'lower_exclusive': .01, 'upper_inclusive': 1}]
    result = metrics.area_metrics(list(range(1, 103)), labels, assets, 'cat', groups)
    assert result['tiny']['relevant_count'] == 1
    assert result['tiny']['Recall100'] == 0 and result['tiny']['rank_max'] == 102
    assert result['rest']['Recall100'] == 1
    assert 'AP' not in result['tiny']


def test_exact_ties_use_numeric_asset_order():
    result = metrics.ranking(np.array([[.2, .4, .4]], dtype=np.float32), [1, 11, 2], ['cat'])
    assert result['cat'] == [2, 11, 1]


def test_source_content_change_is_rejected(tmp_path):
    path = tmp_path / 'input.json'
    path.write_text('{}')
    metrics.read(path)
    path.write_text('{"changed":true}')
    with pytest.raises(ValueError, match='input changed'):
        metrics.read(path)


def test_vector_hash_failure_precedes_loading(tmp_path):
    np.save(tmp_path / 'value.npy', np.ones((1, 2048), dtype=np.float32))
    with pytest.raises(ValueError, match='checksum mismatch'):
        metrics.read_vectors(tmp_path, {'0': {'file': 'value.npy', 'sha256': 'wrong'}}, 1)


def test_reuse_output_preserves_original_evidence(tmp_path):
    output = tmp_path / 'test-results' / 'evidence.json'
    output.parent.mkdir()
    output.write_text('{"original":true}')
    assert metrics.main(['analyze', '--root', str(tmp_path), '--out', str(output)]) == 1
    assert json.loads(output.read_text()) == {'original': True}
    assert not output.with_suffix('.xml').exists()


def test_missing_dataset_records_actual_failure_and_junit(tmp_path):
    output = tmp_path / 'test-results' / 'evidence.json'
    output.parent.mkdir()
    assert metrics.main(['analyze', '--root', str(tmp_path), '--out', str(output)]) == 1
    assert json.loads(output.read_text())['status'] == 'FAIL'
    assert 'failures="1"' in output.with_suffix('.xml').read_text()



def test_report_refuses_source_or_external_destination(tmp_path):
    for destination in (tmp_path / 'src' / 'result.json', tmp_path.parent / 'external.json'):
        with pytest.raises(ValueError, match='project-local'):
            metrics.checked_report_path(tmp_path, destination)
    assert metrics.checked_report_path(tmp_path, tmp_path / '.tmp' / 'result.json').is_relative_to(tmp_path)


@pytest.mark.parametrize('result', [
    {'status': 'BLOCKED', 'stability': {'passed': True}},
    {'status': 'COMPLETE', 'stability': {'passed': False}},
    {'status': 'COMPLETE'},
])
def test_derivation_refuses_incomplete_or_unstable_baseline(result):
    with pytest.raises(ValueError, match='incomplete or unstable'):
        metrics.validate_complete_run(result, 'baseline')


def test_query_record_text_must_match_frozen_definition():
    definitions = [{'query_id': 'cat', 'variant': variant, 'text': variant}
                   for variant in metrics.VARIANTS]
    records = [dict(row, file='unused.npy') for row in definitions]
    assert len(metrics.validate_query_records(records, definitions, ['cat'])) == 5
    records[0]['text'] = 'different words'
    with pytest.raises(ValueError, match='metadata'):
        metrics.validate_query_records(records, definitions, ['cat'])



def test_rerank_prefix_preserves_membership_tail_and_numeric_ties():
    assert metrics.rerank_prefix([4, 3, 2, 1], {4: 0., 3: 1., 2: 1.}, 3) == [2, 3, 4, 1]


@pytest.mark.parametrize('scores', [{1: .5}, {1: .5, 2: float('nan')}])
def test_rerank_missing_or_nonfinite_score_is_refused(scores):
    with pytest.raises(ValueError, match='missing|nonfinite'):
        metrics.rerank_prefix([1, 2, 3], scores, 2)



@pytest.mark.parametrize('field', ['model_revision', 'instruction', 'max_pixels', 'min_pixels',
                                  'dtype', 'attention', 'batch_size', 'ranking_score'])
def test_reranker_runtime_configuration_cannot_differ_from_plan(field):
    plan = dict.fromkeys(('model_revision', 'instruction', 'max_pixels', 'min_pixels',
                          'dtype', 'attention', 'batch_size'), 'fixed')
    plan['ranking_score'] = 'raw_yes_minus_no_logit'
    metrics.validate_reranker_identity(dict(plan), plan, 0.0)
    changed = dict(plan, **{field: 'changed'})
    with pytest.raises(ValueError, match='configuration'):
        metrics.validate_reranker_identity(changed, plan, 0.0)


@pytest.mark.parametrize('delta', [float('nan'), .021, -.01])
def test_unstable_repeated_reranker_pair_is_rejected(delta):
    plan = dict.fromkeys(('model_revision', 'instruction', 'max_pixels', 'min_pixels',
                          'dtype', 'attention', 'batch_size'), 'fixed')
    plan['ranking_score'] = 'raw_yes_minus_no_logit'
    with pytest.raises(ValueError, match='unstable'):
        metrics.validate_reranker_identity(dict(plan), plan, delta)



def test_missing_reranker_runtime_configuration_is_rejected():
    with pytest.raises(ValueError, match='configuration'):
        metrics.validate_reranker_identity({'ranking_score': 'raw_yes_minus_no_logit'},
                                          {'ranking_score': 'raw_yes_minus_no_logit'}, 0.0)
