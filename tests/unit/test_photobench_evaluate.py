"""Full-gallery metrics, bilingual split integrity and conservative metadata hints."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys

import numpy as np
import pytest

from tools.evaluation import photobench_evaluate as p
from tools.evaluation import prepare_wemm_coco
from tools.evaluation.wemm_pilot_core import cache_identity


@pytest.mark.parametrize(('text', 'expected'), [
    ('2014年4月 孩子们与卡通兔子互动', (2014, 4)), ('August 2023', (2023, 8)),
    ('2020 February family', (2020, 2)), ('photos in 2022-08', (2022, 8)),
    ('2022 National Day', None), ('before August 2023', None),
    ('2022年3月到2023年4月', None), ('2022年13月', None), ('2022-03-12', None),
    ('2022年3月12日', None), ('3 April 2020', None), ('April 3, 2020', None),
    ('April 2020 versus 2021', None), ('last April', None),
    ('不是2022年3月', None), ('猫', None),
    ('2023年8月和9月', None), ('2023 August 15', None),
    ('August and September 2023', None), ('写着2023年8月的截图', None),
    ('receipt with text August 2023', None), ('2023-08/09', None),
    ('2023年8月二十五日的照片', None), ('2023年8月初三号的照片', None),
])
def test_date_parser_uses_only_explicit_unambiguous_month(text, expected):
    assert p.explicit_month(text) == expected


def test_date_hints_are_stable_and_never_drop_unknown_dates_or_members():
    metadata = {'a': {'time': '2022-03-01T10:00:00'}, 'b': {'time': None},
                'c': {'time': '2022-04-01T10:00:00'}, 'd': {'time': '2022-03-02T10:00:00'}}
    original = ['b', 'd', 'c', 'a']
    assert p.date_rerank(original, '2022年3月', metadata) == ['d', 'a', 'b', 'c']
    assert p.date_rerank(original, 'last March', metadata) == original
    with pytest.raises(ValueError, match='complete'):
        p.date_rerank(original[:-1], '2022年3月', metadata)


def test_multitarget_ap_recall_and_reciprocal_rank_use_complete_gallery():
    ranked = list(range(60))
    metrics = p.retrieval_metrics(ranked, [1, 9, 29])
    assert metrics['AP'] == pytest.approx((1 / 2 + 2 / 10 + 3 / 30) / 3)
    assert metrics['MRR'] == .5
    assert metrics['Recall20'] == pytest.approx(2 / 3)
    assert metrics['Recall50'] == 1
    assert metrics['P20'] == .1
    assert metrics['P50'] == .06
    assert metrics['Hit20'] == 1


def test_no_match_queries_are_not_zero_ap_in_positive_retrieval_means():
    rows = [{'group_id': 'a', 'metrics': p.retrieval_metrics(['a', 'b'], ['a'])},
            {'group_id': 'b', 'metrics': p.retrieval_metrics(['a', 'b'], [])}]
    result = p.summarize(rows)
    assert result['AP'] == 1
    assert result['positive_queries'] == result['no_match_queries'] == 1
    assert p.summarize(rows[1:])['AP'] is None


def test_threshold_uses_complete_score_ties_and_lower_threshold_on_equal_quality():
    # Both all-accept and all-reject have BA=.5; conservative rule keeps recall.
    result = p.choose_threshold([.5, .5], [True, False])
    assert result['threshold'] == .5
    assert result['calibration']['with_target_false_reject'] == 0
    assert result['calibration']['no_match_false_accept'] == 1
    result = p.choose_threshold([.9, .8, .7, .2], [True, True, False, False])
    assert result['threshold'] == .8
    assert result['calibration']['balanced_accuracy'] == 1
    heldout = p.rejection_metrics([.79, .81, .1], [True, False, False], result['threshold'])
    assert heldout['with_target_false_reject'] == 1
    assert heldout['no_match_false_accept'] == 1
    assert heldout['no_match_correct_reject'] == 1


def test_one_class_calibration_does_not_invent_a_rejection_threshold():
    result = p.choose_threshold([.4, .5], [True, True])
    assert result['threshold'] is None
    assert p.rejection_metrics([.9], [False], result['threshold']) == {'status': 'UNAVAILABLE'}
    with pytest.raises(ValueError):
        p.choose_threshold([float('nan')], [True])


def json_file(path, value):
    path.write_text(json.dumps(value, sort_keys=True), encoding='utf8')


def dataset(tmp_path):
    directory = tmp_path / 'dataset'
    directory.mkdir()
    assets = [{'asset_id': f'a{i:04}', 'sha256': hashlib.sha256(str(i).encode()).hexdigest()}
              for i in range(1047)]
    queries = [{'query_id': f'q{i:03}-{lang}', 'text': f'query{i} {lang}'}
               for i in range(100) for lang in ('cn', 'en')]
    splits = [{'query_id': q['query_id'], 'group_id': q['query_id'].split('-')[0],
               'language': q['query_id'].split('-')[1],
               'split': 'calibration' if i // 2 < 30 else 'test'} for i, q in enumerate(queries)]
    values = {
        'sample.json': {'assets': assets}, 'queries.json': queries,
        'dataset-plan.json': {'schema': 'photobench-plan-v1', 'album': 3, 'splits': splits,
                              'rerank_subset': [f'q{i:03}' for i in range(30, 40)]},
        'metadata.json': {a['asset_id']: {'time': None} for a in assets},
        'evaluation.json': {'schema': 'photobench-evaluation-v1', 'queries': [
            {**r, 'source': 'V', 'ground_truth_asset_ids': ['a0000'] if (i // 2) % 6 else []}
            for i, r in enumerate(splits)]}}
    for name, value in values.items():
        json_file(directory / name, value)
    refresh_summary(directory)
    return directory, values


def refresh_summary(directory):
    files = ('sample.json', 'queries.json', 'dataset-plan.json', 'metadata.json', 'evaluation.json')
    json_file(directory / 'preparation-summary.json', {'status': 'COMPLETE', 'derived_sha256': {
        name: p.sha((directory / name).read_bytes()) for name in files}})


def test_dataset_requires_full_coverage_bilingual_binding_and_frozen_derived_content(tmp_path):
    directory, values = dataset(tmp_path)
    p.load_dataset(p.Reader(), directory)
    changed = copy.deepcopy(values['evaluation.json'])
    changed['queries'][0]['ground_truth_asset_ids'] = ['a0001']
    json_file(directory / 'evaluation.json', changed)
    with pytest.raises(ValueError, match='provenance'):
        p.load_dataset(p.Reader(), directory)
    json_file(directory / 'evaluation.json', values['evaluation.json'])
    changed = copy.deepcopy(values['dataset-plan.json'])
    changed['splits'][0]['split'] = 'test'
    json_file(directory / 'dataset-plan.json', changed)
    refresh_summary(directory)
    with pytest.raises(ValueError, match='leakage'):
        p.load_dataset(p.Reader(), directory)


def test_policy_also_binds_judgment_hash_not_only_ids(tmp_path):
    directory, values = dataset(tmp_path)
    reader = p.Reader()
    p.load_dataset(reader, directory)
    policy = directory.parent / 'policy.json'
    json_file(policy, p.policy_document(reader, directory))
    changed = copy.deepcopy(values['evaluation.json'])
    changed['queries'][0]['ground_truth_asset_ids'] = ['a0001']
    json_file(directory / 'evaluation.json', changed)
    refresh_summary(directory)
    args = argparse.Namespace(dataset=directory, policy=policy, run=[], extra_rankings=[])
    with pytest.raises(ValueError, match='policy'):
        p.evaluate(args)


def make_run(directory, sample, queries, sample_path):
    directory.mkdir()
    spec = {'model': 'qwen', 'dtype': 'fp16', 'settings': {}, 'model_lock': {'revision': 'fixed'},
            'packages': {}, 'decoder': {}, 'source_sha256': {}, 'native_dimension': 2048}
    identities = [{key: row[key] for key in ('asset_id', 'sha256')} for row in sample['assets']]
    record = {**spec, 'schema': 'budget-embedding-run-v1', 'status': 'COMPLETE',
              'assets': sample['assets'], 'queries': queries,
              'sample_sha256': p.sha(sample_path.read_bytes()), 'identity': cache_identity(spec, identities, queries)}
    image_vectors = np.zeros((len(identities), 2048), dtype=np.float32)
    image_vectors[:, 0] = 1
    query_vectors = np.zeros((len(queries), 2048), dtype=np.float32)
    query_vectors[:, 0] = 1
    for name, matrix in [('images.npy', image_vectors), ('queries.npy', query_vectors)]:
        np.save(directory / name, matrix, allow_pickle=False)
    record['vectors_sha256'] = {name: p.sha((directory / name).read_bytes()) for name in ('images.npy', 'queries.npy')}
    json_file(directory / 'result.json', record)
    return record


@pytest.mark.parametrize('failure', ['bytes', 'query_order', 'sample_identity', 'model_settings', 'none'])
def test_scores_refuse_changed_matrices_inputs_queries_and_model_spec(tmp_path, failure):
    sample = {'assets': [{'asset_id': f'a{i}', 'sha256': hashlib.sha256(str(i).encode()).hexdigest()} for i in range(3)]}
    queries = [{'query_id': 'q1', 'text': '猫'}, {'query_id': 'q2', 'text': 'cat'}]
    sample_path = tmp_path / 'sample.json'
    json_file(sample_path, sample)
    directory = tmp_path / 'run'
    record = make_run(directory, sample, queries, sample_path)
    if failure == 'bytes':
        with (directory / 'images.npy').open('ab') as handle:
            handle.write(b'changed')
    elif failure == 'query_order':
        record['queries'] = queries[::-1]
    elif failure == 'sample_identity':
        record['sample_sha256'] = '0' * 64
    elif failure == 'model_settings':
        record['settings'] = {'changed': True}
    json_file(directory / 'result.json', record)
    reader = p.Reader()
    reader.json(sample_path)
    if failure == 'none':
        name, scores, _ = p.load_scores(reader, directory, sample, queries, sample_path)
        assert name == 'qwen-1024'
        assert scores.shape == (2, 3)
        assert np.all(scores == 1)
    else:
        with pytest.raises(ValueError):
            p.load_scores(reader, directory, sample, queries, sample_path)


def test_full_evaluation_preserves_all_query_groups_and_separates_rejection(tmp_path):
    directory, values = dataset(tmp_path)
    reader = p.Reader()
    p.load_dataset(reader, directory)
    policy = tmp_path / 'policy.json'
    json_file(policy, p.policy_document(reader, directory))
    run = tmp_path / 'run'
    make_run(run, values['sample.json'], values['queries.json'], directory / 'sample.json')
    args = argparse.Namespace(dataset=directory, policy=policy, run=[run], extra_rankings=[])
    result = p.evaluate(args)
    assert result['status'] == 'COMPLETE'
    assert set(result['results']) == {'qwen-1024', 'qwen-1024+date'}
    all_cn = result['results']['qwen-1024']['groups']['test/cn/all']
    assert all_cn['queries'] == all_cn['groups'] == 70
    assert all_cn['AP'] == 1
    assert all_cn['no_match_queries'] > 0
    assert result['no_match_diagnostic']['qwen-1024']['en']['status'] == 'CALIBRATED'


@pytest.mark.parametrize('failure', ['identity', 'outside_subset', 'tail', 'missing_query', 'none'])
def test_external_reranker_can_change_only_same_run_planned_top50(tmp_path, failure):
    directory, values = dataset(tmp_path)
    reader = p.Reader()
    p.load_dataset(reader, directory)
    policy = tmp_path / 'policy.json'
    json_file(policy, p.policy_document(reader, directory))
    run = tmp_path / 'run'
    record = make_run(run, values['sample.json'], values['queries.json'], directory / 'sample.json')
    ids = [row['asset_id'] for row in values['sample.json']['assets']]
    qids = [row['query_id'] for row in values['queries.json']]
    subset = qids[60:80]
    rankings = {qid: ids[:50][::-1] + ids[50:] if qid in subset else ids[:] for qid in qids}
    external = {'status': 'COMPLETE', 'input_identities': values['sample.json']['assets'],
                'query_definitions': values['queries.json'], 'embedding_run_identity': record['identity'],
                'reranked_query_ids': subset, 'rankings': {'reranked': rankings}}
    if failure == 'identity':
        external['embedding_run_identity'] = 'different-run'
    elif failure == 'outside_subset':
        rankings[qids[0]][:2] = rankings[qids[0]][:2][::-1]
    elif failure == 'tail':
        rankings[subset[0]][50:52] = rankings[subset[0]][50:52][::-1]
    elif failure == 'missing_query':
        del rankings[qids[0]]
    extra = tmp_path / 'external.json'
    json_file(extra, external)
    args = argparse.Namespace(dataset=directory, policy=policy, run=[run], extra_rankings=[extra])
    if failure == 'none':
        result = p.evaluate(args)
        assert result['results']['reranked']['rerank_subset']['test/cn/all']['queries'] == 10
        assert result['results']['reranked']['groups']['test/cn/all']['AP'] < 1
    else:
        with pytest.raises(ValueError, match='external'):
            p.evaluate(args)


@pytest.mark.parametrize('existing', ['junit', 'output'])
def test_cli_refuses_to_overwrite_previous_evidence(tmp_path, monkeypatch, existing):
    monkeypatch.setattr(prepare_wemm_coco, 'ROOT', tmp_path)
    root = tmp_path / '.tmp'
    root.mkdir()
    junit, output = root / 'junit.xml', root / 'result.json'
    target = junit if existing == 'junit' else output
    target.write_bytes(b'original failure evidence')
    monkeypatch.setattr(sys, 'argv', ['evaluate', 'evaluate', '--dataset', str(root / 'dataset'),
                                    '--policy', str(root / 'policy.json'), '--output', str(output),
                                    '--junit', str(junit)])
    with pytest.raises(ValueError, match='historical evidence'):
        p.main()
    assert target.read_bytes() == b'original failure evidence'
