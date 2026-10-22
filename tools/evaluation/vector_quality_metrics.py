"""Bounded CPU diagnostics for frozen pure-vector experiments; no model loading.

Use ``derive`` to reconstruct query/highres/crop schemes from raw probe vectors,
then ``analyze`` to compare complete rankings with the frozen COCO presence labels.
The previously inspected 500-image dataset is exploratory, never a held-out set.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

CATEGORIES = {'cat': 'cat', 'dog': 'dog', 'bird': 'bird', 'car': 'car', 'bicycle': 'bicycle',
              'person': 'person', 'chair': 'chair', 'teddy_bear': 'teddy bear'}
HASHES: dict[Path, str] = {}
CHECKS = 0


def check(condition, message):
    global CHECKS
    CHECKS += 1
    if not condition:
        raise ValueError(message)


def digest(path):
    value = hashlib.sha256(path.read_bytes()).hexdigest()
    if path in HASHES:
        check(HASHES[path] == value, f'input changed during evaluation: {path.name}')
    HASHES[path] = value
    return value


def read(path):
    digest(path)
    return json.loads(path.read_text(encoding='utf-8-sig'))


def read_vectors(directory, records, rows):
    check(set(records) == {str(i) for i in range(rows)}, 'missing or unexpected vector record')
    values = []
    for index in range(rows):
        record = records[str(index)]
        name = record['file']
        check(Path(name).name == name and name.endswith('.npy'), 'invalid vector filename')
        path = directory / name
        check(digest(path) == record['sha256'], 'vector checksum mismatch')
        value = np.load(path, allow_pickle=False)
        check(value.shape == (1, 2048) and value.dtype == np.float32, 'native vector shape/dtype mismatch')
        check(np.isfinite(value).all(), 'non-finite native vector')
        check(abs(np.linalg.norm(value.astype(np.float64)) - 1.0) < 1e-5, 'native vector not unit normalized')
        values.append(value[0])
    return np.stack(values)


def prefix(vectors, dimension):
    result = vectors[:, :dimension].astype(np.float64)
    scale = np.max(np.abs(result), axis=1, keepdims=True)
    check(np.all(scale > 0), 'zero vector prefix')
    result /= scale
    result /= np.linalg.norm(result, axis=1, keepdims=True)
    return result.astype(np.float32)


def metrics(ranking, labels):
    relevant = [rank for rank, aid in enumerate(ranking, 1) if labels[aid]]
    count = len(relevant)
    result = {'candidate_count': len(ranking), 'relevant_count': count,
              'AP': sum(i / rank for i, rank in enumerate(relevant, 1)) / count if count else None}
    for k in (20, 50, 100, 200):
        hits = sum(rank <= k for rank in relevant)
        result[f'hits{k}'] = hits
        result[f'P{k}'] = hits / min(k, len(ranking))
        result[f'Recall{k}'] = hits / count if count else None
    result['first_unannotated_rank'] = next((i for i, aid in enumerate(ranking, 1) if not labels[aid]), None)
    result['positive_rank_percentiles'] = {str(p): float(np.percentile(relevant, p, method='linear')) if count else None
                                           for p in (50, 90, 95, 100)}
    return result


def area_metrics(ranking, labels, assets, category, area_groups):
    rank_by_id = {aid: rank for rank, aid in enumerate(ranking, 1)}
    groups = {item['name']: [] for item in area_groups}
    groups['unknown_or_zero'] = []
    for aid in ranking:
        if not labels[aid]:
            continue
        area = assets[aid]['category_max_area_fraction'].get(category)
        if area is None or area <= 0:
            groups['unknown_or_zero'].append(aid)
            continue
        check(math.isfinite(area) and 0 < area <= 1.000001, 'invalid annotation area fraction')
        group = next((item['name'] for item in area_groups
                      if item['lower_exclusive'] < area <= item['upper_inclusive'] + 1e-12), None)
        check(group is not None, 'positive area outside frozen groups')
        groups[group].append(aid)
    output = {}
    for name, ids in groups.items():
        ranks = [rank_by_id[aid] for aid in ids]
        output[name] = {'relevant_count': len(ids), 'asset_ids': sorted(ids),
                        'rank_p50': float(np.percentile(ranks, 50)) if ranks else None,
                        'rank_p90': float(np.percentile(ranks, 90)) if ranks else None,
                        'rank_max': max(ranks) if ranks else None}
        for k in (20, 50, 100, 200):
            hits = sum(rank <= k for rank in ranks)
            output[name][f'hits{k}'] = hits
            output[name][f'Recall{k}'] = hits / len(ranks) if ranks else None
    return output


def validate_rankings(by_query, ids, qids):
    check(set(by_query) == set(qids), 'query IDs missing or unexpected')
    for query, ranking in by_query.items():
        check(len(ranking) == len(ids) and len(set(ranking)) == len(ids) and set(ranking) == set(ids),
              f'ranking is not the complete unique asset set: {query}')


def analyze_runs(args):
    root = args.root.resolve()
    plan = read(root / args.plan)
    sample = read(root / plan['sample'])
    labels_doc = read(root / plan['labels'])
    queries = read(root / plan['queries'])
    assets = {row['asset_id']: row for row in sample['assets']}
    ids = [row['asset_id'] for row in sample['assets']]
    qids = [row['query_id'] for row in queries]
    inputs = [{key: row[key] for key in ('asset_id', 'sha256', 'selection_stratum')} for row in sample['assets']]
    id_inputs = [{key: row[key] for key in ('asset_id', 'sha256')} for row in sample['assets']]
    check(len(ids) == len(set(ids)) == 500, 'unexpected sample cardinality')
    check(len({row['sha256'] for row in sample['assets']}) == 500, 'duplicate sample content')
    check(labels_doc['input_identities'] == id_inputs, 'label input identity mismatch')
    check(labels_doc['query_definitions'] == queries, 'label query definition mismatch')
    labels = {}
    for query in CATEGORIES:
        rows = labels_doc['queries'][query]
        check(len(rows) == 500 and {row['asset_id'] for row in rows} == set(ids), 'incomplete label universe')
        check(all(type(row['relevant']) is bool for row in rows), 'non-boolean relevance')
        labels[query] = {row['asset_id']: row['relevant'] for row in rows}
    reference = read(root / '.tmp/wemm-pilot/runs/full/comparison.json')
    check(reference['inputs'] == inputs and reference['query_definitions'] == queries, 'baseline comparison identity mismatch')
    rankings, provenance = {}, {}
    for model in ('qwen', 'wemm'):
        directory = root / '.tmp/wemm-pilot/runs/full' / model
        result = read(directory / 'result.json')
        check(result['status'] == 'COMPLETE' and result['stability']['passed'], 'run incomplete or unstable')
        check(result['inputs'] == inputs and result['queries'] == queries, 'run sample/query identity mismatch')
        check(result['cache_identity'] == reference['run_identities'][model], 'baseline run identity mismatch')
        image_vectors = read_vectors(directory, result['images'], len(ids))
        query_vectors = read_vectors(directory, result['query_vectors'], len(qids))
        dimensions = (512, 1024, 2048) if model == 'qwen' else (512,)
        for dimension in dimensions:
            name = f'{model}{dimension}'
            scores = prefix(query_vectors, dimension) @ prefix(image_vectors, dimension).T
            by_query = {qid: [ids[i] for i in sorted(range(len(ids)), key=lambda i: (-float(scores[q, i]), ids[i]))]
                        for q, qid in enumerate(qids)}
            validate_rankings(by_query, ids, qids)
            rankings[name] = by_query
            provenance[name] = {'source': str(directory.relative_to(root)), 'cache_identity': result['cache_identity'],
                                'native_dimension': 2048, 'dimension': dimension,
                                'score': 'float64 prefix L2 normalization -> float32 dot; score descending, asset_id ascending'}
            if name in reference['rankings']:
                check(by_query == reference['rankings'][name], f'independent rankings differ from historical baseline: {name}')
    for path in args.extra_rankings:
        candidate = read(path.resolve())
        check(candidate['schema'] == 'vector-quality-candidates-v1', 'unknown candidate schema')
        check(candidate['input_identities'] == id_inputs, 'candidate image identities mismatch')
        check(candidate['query_definitions'] == queries, 'candidate query identities mismatch')
        check(bool(candidate.get('provenance')), 'candidate has no provenance')
        for name, by_query in candidate['rankings'].items():
            check(name not in rankings, 'candidate would overwrite existing scheme')
            validate_rankings(by_query, ids, candidate.get('evaluated_query_ids', qids))
            check(set(CATEGORIES) <= set(by_query), 'candidate lacks complete category coverage')
            rankings[name] = by_query
            provenance[name] = candidate['provenance']
    random_ids = {row['asset_id'] for row in sample['assets'] if row['selection_stratum'] == 'random'}
    check(len(random_ids) == 250, 'random stratum size changed')
    subsets = {'all500': set(ids), 'random250': random_ids, 'targeted250': set(ids) - random_ids}
    results, deltas = {}, {}
    for subset, members in subsets.items():
        results[subset] = {}
        for name, by_query in rankings.items():
            per_category = {query: metrics([aid for aid in by_query[query] if aid in members], labels[query])
                            for query in CATEGORIES}
            area = {query: area_metrics([aid for aid in by_query[query] if aid in members], labels[query], assets,
                                       category, plan['area_groups']) for query, category in CATEGORIES.items()}
            macros = {f'macro_{key}_8': statistics.mean(value[key] for value in per_category.values())
                      for key in ('AP', 'P20', 'P50', 'P100', 'Recall20', 'Recall50', 'Recall100', 'Recall200')}
            results[subset][name] = {**macros, 'per_category': per_category, 'area_diagnostics': area}
        deltas[subset] = {}
        for name, values in results[subset].items():
            if name == 'qwen512':
                continue
            baseline = results[subset]['qwen512']
            delta = {query: {key: values['per_category'][query][key] - baseline['per_category'][query][key]
                             for key in ('AP', 'P20', 'P50', 'P100', 'Recall100', 'Recall200')}
                     for query in CATEGORIES}
            deltas[subset][name] = {
                'macro_AP_delta': values['macro_AP_8'] - baseline['macro_AP_8'],
                'per_category': delta,
                'noncat_AP_regressions': {query: metrics['AP'] for query, metrics in delta.items()
                                          if query != 'cat' and metrics['AP'] < 0},
                'minimum_noncat_AP_delta': min(delta[query]['AP'] for query in CATEGORIES if query != 'cat')}
    cat_ranks = {name: {aid: rank for rank, aid in enumerate(by_query['cat'], 1)} for name, by_query in rankings.items()}
    cat_positive_detail = [{
        'asset_id': aid, 'selection_stratum': assets[aid]['selection_stratum'],
        'area_fraction': assets[aid]['category_max_area_fraction'].get('cat'),
        'ranks': {name: positions[aid] for name, positions in cat_ranks.items()}}
        for aid in sorted(ids) if labels['cat'][aid]]
    # Independent tiny oracle verifies AP and complete-positive recall conventions.
    oracle = metrics([1, 2, 3], {1: True, 2: False, 3: True})
    check(math.isclose(oracle['AP'], 5 / 6) and oracle['P20'] == 2 / 3 and oracle['Recall100'] == 1,
          'metric oracle failure')
    for name in ('qwen512', 'qwen2048', 'wemm512'):
        for query in CATEGORIES:
            check(math.isclose(results['all500'][name]['per_category'][query]['AP'],
                               reference['fully_annotated_metrics'][name][query]['average_precision'], abs_tol=1e-12),
                  'independent AP differs from baseline')
    for path, expected in list(HASHES.items()):
        check(hashlib.sha256(path.read_bytes()).hexdigest() == expected, 'input changed by analysis')
    return {'status': 'COMPLETE', 'scope': 'CPU-only exploratory evaluation of the fixed public 500-image sample',
            'plan': str((root / args.plan).relative_to(root)),
            'limits': plan['limits'], 'provenance': provenance, 'results': results, 'deltas_vs_qwen512': deltas,
            'cat_positive_detail': cat_positive_detail, 'rankings': rankings,
            'input_sha256': {str(path.relative_to(root)) if path.is_relative_to(root) else str(path): digest
                             for path, digest in HASHES.items()}}



VARIANTS = ('zh', 'zh_photo', 'en', 'en_photo', 'zh_contains')


def identity(spec, inputs, queries):
    payload = {'schema': 'wemm-pilot-cache-v1', 'model_spec': spec,
               'input_identities': inputs, 'queries': queries}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def vector(directory, record):
    name = record['file']
    check(Path(name).name == name and name.endswith('.npy'), 'invalid probe filename')
    path = directory / name
    check(digest(path) == record['sha256'], 'probe vector hash mismatch')
    value = np.load(path, allow_pickle=False)
    check(value.shape == (1, 2048) and value.dtype == np.float32 and np.isfinite(value).all(), 'invalid probe vector')
    check(abs(float(np.linalg.norm(value.astype(np.float64))) - 1) < 1e-5, 'probe vector not normalized')
    return value[0]


def ranking(scores, ids, qids):
    check(scores.shape == (len(qids), len(ids)) and np.isfinite(scores).all(), 'invalid score matrix')
    result = {qid: [ids[i] for i in sorted(range(len(ids)), key=lambda i: (-float(scores[q, i]), ids[i]))]
              for q, qid in enumerate(qids)}
    validate_rankings(result, ids, qids)
    return result


def validate_complete_run(result, kind):
    check(result.get('status') == 'COMPLETE' and result.get('stability', {}).get('passed') is True,
          kind + ' incomplete or unstable')


def validate_query_records(records, definitions, qids):
    check(len(records) == len(qids) * len(VARIANTS) == len(definitions), 'missing query vector')
    metadata = [{key: record.get(key) for key in ('query_id', 'variant', 'text')} for record in records]
    check(metadata == definitions, 'query vector record metadata does not match frozen definitions')
    mapped = {(record['query_id'], record['variant']): record for record in records}
    check(set(mapped) == {(qid, variant) for qid in qids for variant in VARIANTS}, 'variant set mismatch')
    return mapped


def derive_candidates(args):
    root = args.root.resolve()
    sample = read(root / '.tmp/wemm-pilot/datasets/sample.json')
    queries = read(root / '.tmp/wemm-pilot/datasets/queries.json')
    baseline_dir = root / '.tmp/wemm-pilot/runs/full/qwen'
    baseline = read(baseline_dir / 'result.json')
    validate_complete_run(baseline, 'baseline')
    identities = [{key: row[key] for key in ('asset_id', 'sha256', 'selection_stratum')} for row in sample['assets']]
    ids = [row['asset_id'] for row in sample['assets']]
    qids = list(CATEGORIES)
    check(identities == baseline['inputs'] and queries == baseline['queries'], 'baseline inputs mismatch')
    check(baseline['cache_identity'] == identity(baseline['model_spec'], identities, queries), 'baseline identity mismatch')
    base_images = read_vectors(baseline_dir, baseline['images'], len(ids))
    all_base_queries = read_vectors(baseline_dir, baseline['query_vectors'], len(queries))
    base_queries = all_base_queries[[next(i for i, q in enumerate(queries) if q['query_id'] == qid) for qid in qids]]
    out_rankings, details = {}, {}
    for mode in ('query', 'highres', 'crop5'):
        directory = getattr(args, mode + '_dir')
        if directory is None:
            continue
        directory = directory.resolve()
        result = read(directory / 'result.json')
        spec = result['spec']
        check(result['schema'] == 'vector-quality-probe-v1', 'invalid probe schema')
        validate_complete_run(result, 'probe')
        check(spec['mode'] == mode and result['inputs'] == identities, 'probe mode/input mismatch')
        check(spec['baseline_identity'] == baseline['cache_identity'], 'probe baseline mismatch')
        check(result['cache_identity'] == identity(spec, result['inputs'], result['queries']), 'probe identity mismatch')
        check(spec['max_pixels'] == (524288 if mode == 'highres' else 262144), 'unexpected pixel budget')
        details[mode] = {'directory': str(directory.relative_to(root)), 'identity': result['cache_identity'],
                         'spec': spec, 'resources': result['resources'], 'load_ms': result['load_ms'],
                         'load_and_work_ms': result['load_and_work_ms']}
        if mode == 'query':
            records = validate_query_records(result['query_vectors'], result['queries'], qids)
            raw = {variant: np.stack([vector(directory, records[qid, variant]) for qid in qids]) for variant in VARIANTS}
            check(np.max(np.abs(raw['zh'] - base_queries)) <= 1e-4, 'baseline text output changed unexpectedly')
            details[mode]['baseline_zh_max_abs_delta'] = float(np.max(np.abs(raw['zh'] - base_queries)))
            details[mode]['variant_encode_ms'] = {variant: [records[qid, variant]['encode_ms'] for qid in qids]
                                                 for variant in VARIANTS}
            for dimension in (512, 1024, 2048):
                images = prefix(base_images, dimension)
                normalized = {variant: prefix(values, dimension) for variant, values in raw.items()}
                for variant, texts in normalized.items():
                    out_rankings[f'qwen{dimension}_query_{variant}'] = ranking(texts @ images.T, ids, qids)
                mean = prefix(np.stack([normalized[v] for v in VARIANTS[:4]]).astype(np.float64).mean(axis=0), dimension)
                out_rankings[f'qwen{dimension}_query_mean4'] = ranking(mean @ images.T, ids, qids)
                if dimension == 2048:
                    saved_ensemble = {record['query_id']: vector(directory, record) for record in result['ensembles']}
                    check(set(saved_ensemble) == set(qids), 'ensemble set mismatch')
                    saved = np.stack([saved_ensemble[qid] for qid in qids])
                    check(np.max(np.abs(saved - mean)) <= 1e-7, 'independent 2048D ensemble differs')
        else:
            check(len(result['images']) == 500 and [row['asset_id'] for row in result['images']] == ids,
                    'image result cardinality/order mismatch')
            view_count = 5 if mode == 'crop5' else 1
            raw = []
            timings = []
            for index, row in enumerate(result['images']):
                views = row['views']
                check(len(views) == view_count and [v['view'] for v in views] == list(range(view_count)), 'view set mismatch')
                width, height = row['decoded']['width'], row['decoded']['height']
                cw, ch = width * 7 // 10, height * 7 // 10
                expected_boxes = [[0, 0, width, height]]
                if mode == 'crop5':
                    expected_boxes.extend([[0, 0, cw, ch], [width-cw, 0, width, ch],
                                           [0, height-ch, cw, height], [width-cw, height-ch, width, height]])
                check([v['box'] for v in views] == expected_boxes, 'unexpected crop geometry')
                values = np.stack([vector(directory, v) for v in views])
                if mode == 'crop5':
                    check(np.array_equal(values[0], base_images[index]), 'whole-image baseline vector changed')
                    check(views[0]['reused_from'] == baseline['cache_identity'], 'whole-image reuse identity mismatch')
                raw.append(values)
                timings.append(sum(v['encode_ms'] for v in views))
            raw = np.stack(raw)
            details[mode]['image_encode_ms_sum_new_views'] = timings
            details[mode]['decode_ms'] = [row['decode_ms'] for row in result['images']]
            for dimension in (512, 1024, 2048):
                images = prefix(raw.reshape(-1, 2048), dimension).reshape(len(ids), view_count, dimension)
                texts = prefix(base_queries, dimension)
                if mode == 'highres':
                    out_rankings[f'qwen{dimension}_highres'] = ranking(texts @ images[:, 0, :].T, ids, qids)
                else:
                    max_scores = np.einsum('qd,ivd->qiv', texts, images).max(axis=2)
                    mean_views = prefix(images.astype(np.float64).mean(axis=1), dimension)
                    out_rankings[f'qwen{dimension}_crop5_max'] = ranking(max_scores, ids, qids)
                    out_rankings[f'qwen{dimension}_crop5_mean'] = ranking(texts @ mean_views.T, ids, qids)
    check(bool(out_rankings), 'no probe directories supplied')
    for path, expected in HASHES.items():
        check(hashlib.sha256(path.read_bytes()).hexdigest() == expected, 'input changed during evaluation')
    return {'schema': 'vector-quality-candidates-v1', 'input_identities': [{key: row[key] for key in ('asset_id','sha256')}
                                                                        for row in sample['assets']],
            'query_definitions': queries, 'evaluated_query_ids': qids,
            'provenance': {'method': 'Independent raw-vector reconstruction; per-dimension prefix normalization, mean4 excludes zh_contains; crop max cosine or normalized mean of normalized views.',
                           'experiments': details,
                           'input_sha256': {str(path.relative_to(root)): value for path, value in HASHES.items()}},
            'rankings': out_rankings}


def rerank_prefix(baseline, scored, k):
    """Keep candidate membership and untouched tail; scores only order the prefix."""
    check(type(k) is int and 1 <= k <= len(baseline), 'invalid rerank prefix')
    check(len(set(baseline)) == len(baseline), 'duplicate baseline asset')
    check(set(baseline[:k]) <= set(scored), 'missing candidate scores')
    check(all(math.isfinite(scored[aid]) for aid in baseline[:k]), 'nonfinite rerank score')
    head = sorted(baseline[:k], key=lambda aid: (-scored[aid], aid))
    return head + baseline[k:]


def validate_reranker_identity(identity_doc, plan, repeat_delta):
    fields = ('model_revision', 'instruction', 'max_pixels', 'min_pixels',
              'dtype', 'attention', 'batch_size', 'ranking_score')
    check(all(key in identity_doc and key in plan and identity_doc[key] == plan[key] for key in fields),
          'reranker runtime configuration differs from frozen plan')
    check(identity_doc.get('ranking_score') == 'raw_yes_minus_no_logit', 'unexpected reranker score semantics')
    check(type(repeat_delta) in (int, float) and math.isfinite(repeat_delta) and 0 <= repeat_delta <= .02,
          'reranker repeated pair was unstable')


def derive_reranker(args):
    root = args.root.resolve()
    check(args.reranker_dir is not None and args.reranker_plan is not None, 'reranker inputs required')
    directory, plan_path = args.reranker_dir.resolve(), args.reranker_plan.resolve()
    sensitivity = read(root / args.plan)['reranker_sensitivity']
    check(sensitivity['candidate_counts'] == [20, 50, 100], 'unexpected sensitivity plan')
    plan, result = read(plan_path), read(directory / 'result.json')
    identity_doc = read(directory / 'identity.json')
    check(result['status'] == 'COMPLETE' and result['pairs'] == 800, 'incomplete reranker run')
    check(identity_doc == result['identity'], 'reranker identity records disagree')
    validate_reranker_identity(identity_doc, plan, result['repeat_first_logit_delta'])
    check(identity_doc['plan_sha256'] == digest(plan_path), 'reranker plan identity mismatch')
    check(plan['baseline'] == 'qwen2048' and plan['top_k'] == 100 and plan['smoke'] is False,
          'this diagnostic requires the frozen Qwen2048 top100 experiment')
    documents = {}
    for name in ('sample', 'queries', 'comparison'):
        entry = plan['inputs'][name]
        path = root / entry['path']
        check(digest(path) == entry['sha256'], 'reranker frozen input changed')
        documents[name] = read(path)
    sample, queries, comparison = (documents[name] for name in ('sample', 'queries', 'comparison'))
    inputs = [{key: asset[key] for key in ('asset_id', 'sha256', 'selection_stratum')} for asset in sample['assets']]
    check(comparison['inputs'] == inputs and comparison['query_definitions'] == queries,
          'reranker baseline comparison inputs mismatch')
    assets = {asset['asset_id']: asset for asset in sample['assets']}
    qmap = {query['query_id']: query['text'] for query in queries}
    expected_pairs = []
    for qid in CATEGORIES:
        ordered = comparison['rankings']['qwen2048'][qid]
        validate_rankings({qid: ordered}, list(assets), [qid])
        for rank, aid in enumerate(ordered[:100], 1):
            asset = assets[aid]
            expected_pairs.append({'query_id': qid, 'query': qmap[qid], 'asset_id': aid,
                                   'path': str(Path(asset['path']).resolve().relative_to(root)).replace('\\', '/'),
                                   'image_sha256': asset['sha256'], 'baseline_rank': rank})
    check(plan['pairs'] == expected_pairs, 'reranker candidates differ from actual retrieval prefix')
    rows_path = directory / 'pairs.jsonl'
    digest(rows_path)
    rows = [json.loads(line) for line in rows_path.read_text(encoding='utf-8').splitlines()]
    check(len(rows) == 800 and [{key: row[key] for key in expected_pairs[0]} for row in rows] == expected_pairs,
          'reranker scored pairs missing, duplicated or reordered')
    for row in rows:
        logit, sigmoid = row['logit'], row['sigmoid']
        check(math.isfinite(logit) and math.isfinite(sigmoid), 'nonfinite reranker output')
        expected = 1 / (1 + math.exp(-logit)) if logit >= 0 else math.exp(logit) / (1 + math.exp(logit))
        check(abs(sigmoid - expected) <= 2e-7, 'reported sigmoid not consistent with raw score')
        check(math.isfinite(row['total_ms']) and row['total_ms'] >= 0, 'invalid pair timing')
    out_rankings, costs = {}, {}
    for k in sensitivity['candidate_counts']:
        name = f'qwen2048_rerank{k}'
        out_rankings[name], costs[name] = {}, {}
        for qid in CATEGORIES:
            selected = [row for row in rows if row['query_id'] == qid]
            scored = {row['asset_id']: row['logit'] for row in selected}
            baseline = comparison['rankings']['qwen2048'][qid]
            reordered = rerank_prefix(baseline, scored, k)
            check(set(reordered[:k]) == set(baseline[:k]) and reordered[k:] == baseline[k:],
                  'reranking changed membership or tail')
            out_rankings[name][qid] = reordered
            costs[name][qid] = {'pairs': k, 'component_total_ms': sum(row['total_ms'] for row in selected[:k]),
                               'cost_scope': sensitivity['cost']}
    for path, expected in HASHES.items():
        check(hashlib.sha256(path.read_bytes()).hexdigest() == expected, 'reranker input changed during analysis')
    return {'schema': 'vector-quality-candidates-v1',
            'input_identities': [{key: row[key] for key in ('asset_id','sha256')} for row in sample['assets']],
            'query_definitions': queries, 'evaluated_query_ids': list(CATEGORIES),
            'rankings': out_rankings,
            'provenance': {'method': sensitivity, 'runtime': result, 'costs': costs,
                           'input_sha256': {str(path.relative_to(root)): value for path,value in HASHES.items()}}}


def checked_report_path(root: Path, path: Path) -> Path:
    """Only create new evidence inside the workspace's ignored output roots."""
    root = root.resolve()
    resolved = path.resolve()
    if resolved.suffix != '.json' or not any(
        resolved.is_relative_to(root / name) for name in ('.tmp', 'test-results')
    ):
        raise ValueError('report path must be a project-local .tmp/test-results JSON file')
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or candidate.is_junction():
            raise ValueError('report path links are refused')
    return resolved


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('mode', choices=('analyze', 'derive', 'rerank'))
    result.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    result.add_argument('--plan', type=Path, default=Path('tools/evaluation/vector_quality_plan.json'))
    result.add_argument('--extra-rankings', type=Path, action='append', default=[])
    result.add_argument('--reranker-dir', type=Path)
    result.add_argument('--reranker-plan', type=Path)
    for mode in ('query', 'highres', 'crop5'):
        result.add_argument('--' + mode + '-dir', type=Path)
    result.add_argument('--out', type=Path, required=True)
    return result


def main(argv=None):
    global CHECKS
    HASHES.clear()
    CHECKS = 0
    args = parser().parse_args(argv)
    started, failure = time.perf_counter(), None
    try:
        args.out = checked_report_path(args.root, args.out)
    except ValueError as error:
        print(json.dumps({'status': 'FAIL', 'error': str(error)}))
        return 1
    try:
        check(not args.out.exists() and not args.out.with_suffix('.xml').exists(),
              'refuse overwriting prior evidence')
        action = {'analyze': analyze_runs, 'derive': derive_candidates, 'rerank': derive_reranker}[args.mode]
        output = action(args)
    except Exception as error:
        failure = traceback.format_exc()
        output = {'status': 'FAIL', 'error': str(error)}
    elapsed = time.perf_counter() - started
    output.update(checks=CHECKS, elapsed_seconds=elapsed,
                  script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    # Preserve existing evidence on accidental output reuse, including failure paths.
    if args.out.exists() or args.out.with_suffix('.xml').exists():
        print(json.dumps({'status': 'FAIL', 'error': 'refuse overwriting prior evidence'}))
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    suite = ET.Element('testsuite', name='pure-vector-' + args.mode, tests='1',
                       failures=str(int(failure is not None)), errors='0', skipped='0', time=str(elapsed))
    case = ET.SubElement(suite, 'testcase', name='complete_identities_rankings_and_metrics', time=str(elapsed))
    if failure:
        ET.SubElement(case, 'failure', message=output['error']).text = failure
    ET.ElementTree(suite).write(args.out.with_suffix('.xml'), encoding='utf-8', xml_declaration=True)
    if failure:
        print(failure)
        return 1
    print(json.dumps({'status': 'COMPLETE', 'mode': args.mode, 'checks': CHECKS,
                      'schemes': len(output['rankings']), 'elapsed_seconds': elapsed}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
