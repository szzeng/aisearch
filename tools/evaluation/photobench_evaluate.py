"""CPU-only complete-album comparison; freeze rules before reading held-out scores.

This is a local split of PhotoBench's public validation album, not its leaderboard.
Date hints preserve all results. Rejection remains a diagnostic, never a filter.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import time
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import numpy as np

from tools.evaluation.album_linear_probe import Reader, sha
from tools.evaluation.budget_search_metrics import embeddings_from_record, validate_ranking
from tools.evaluation.prepare_wemm_coco import controlled_output, write_frozen_json, write_json
from tools.evaluation.wemm_pilot_core import normalize_prefix

MONTHS = 'january february march april may june july august september october november december'.split()
POLICY = {
    'schema': 'photobench-evaluation-policy-v1',
    'models': {'qwen': 1024, 'fgclip-base-adaptive': 768},
    'ranking': 'full gallery cosine; ties retain frozen sample order',
    'date_hint': 'exactly one explicit year-month (Chinese, English full month name, ISO); no ranges, other numerals/months, relative dates, days, negation, screenshot or written-text cues; known matching dates stably first, all other members retain order',
    'threshold': 'per language: max image cosine >= threshold means accept; calibration balanced accuracy; candidates unique calibration scores plus nextafter(max,+inf); ties use lower threshold to reduce false rejection; require both classes',
    'threshold_scope': 'diagnostic only; never removes retrieval results; test does not choose parameters',
    'metrics': 'positive-GT AP over full gallery, MRR, Recall/P/Hit@20/50; no-GT excluded from retrieval mean and reported separately',
    'cost_scope': 'CPU matrix/rank only; model encoding and app HTTP latency excluded',
}


def explicit_month(text: str) -> tuple[int, int] | None:
    lower = text.lower()
    if re.search(r'\b(before|after|until|since|between|not|except|to)\b|之前|之后|以前|以后|至|到|不|除', lower):
        return None
    if re.search(r'写着|文字|标注|标有|上写|截图|屏幕|证件|收据|票据|发票|\b(screenshot|text|written|reads|labelled|labeled|receipt|ticket|certificate)\b', lower):
        return None
    matches = []
    patterns = [r'(?<!\d)((?:19|20)\d{2})年\s*(1[0-2]|0?[1-9])月(?!\s*\d+\s*[日号])',
                r'(?<![\d-])((?:19|20)\d{2})-(1[0-2]|0[1-9])(?![-\d])']
    for pattern in patterns:
        matches += [(match.start(), match.end(), (int(match[1]), int(match[2])))
                    for match in re.finditer(pattern, lower)]
    names = '|'.join(MONTHS)
    if re.search(rf'\b\d{{1,2}}\s+(?:{names})\s+(?:19|20)\d{{2}}\b', lower):
        return None
    for match in re.finditer(rf'\b({names})\s+((?:19|20)\d{{2}})\b', lower):
        matches.append((match.start(), match.end(), (int(match[2]), MONTHS.index(match[1]) + 1)))
    for match in re.finditer(rf'\b((?:19|20)\d{{2}})\s+({names})\b', lower):
        matches.append((match.start(), match.end(), (int(match[1]), MONTHS.index(match[2]) + 1)))
    # A second year is evidence of a range or comparison, not a single date.
    years = re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)', lower)
    if len(matches) != 1 or len(years) != 1:
        return None
    start, end, selected = matches[0]
    remaining = lower[:start] + lower[end:]
    if re.search(rf'\d|月|[零〇一二三四五六七八九十廿卅]+[日号]|\b(?:{names})\b', remaining):
        return None
    return selected


def date_rerank(ranking: list[str], text: str, metadata: dict[str, dict]) -> list[str]:
    validate_ranking(ranking, list(metadata))
    selected = explicit_month(text)
    if selected is None:
        return list(ranking)
    def matching(asset_id: str) -> bool:
        timestamp = metadata[asset_id]['time']
        if timestamp is None:
            return False
        parsed = datetime.fromisoformat(timestamp)
        return (parsed.year, parsed.month) == selected
    matched = {asset_id for asset_id in ranking if matching(asset_id)}
    return [asset_id for asset_id in ranking if asset_id in matched] + [
        asset_id for asset_id in ranking if asset_id not in matched]


def retrieval_metrics(ranking: list[str], relevant: list[str]) -> dict:
    if len(set(relevant)) != len(relevant) or not set(relevant) <= set(ranking):
        raise ValueError('invalid relevance members')
    if len(set(ranking)) != len(ranking) or not ranking:
        raise ValueError('ranking must be nonempty and unique')
    target = set(relevant)
    ranks = [i for i, asset_id in enumerate(ranking, 1) if asset_id in target]
    metrics = {'positive_count': len(target), 'AP': None, 'MRR': None}
    for k in (20, 50):
        hits = sum(rank <= k for rank in ranks)
        metrics.update({f'Recall{k}': hits / len(target) if target else None,
                        f'P{k}': hits / min(k, len(ranking)) if target else None,
                        f'Hit{k}': int(hits > 0) if target else None})
    if target:
        metrics.update(AP=sum(i / rank for i, rank in enumerate(ranks, 1)) / len(target),
                       MRR=1 / ranks[0])
    return metrics


def choose_threshold(scores: list[float], has_target: list[bool]) -> dict:
    values = np.asarray(scores, dtype=np.float64)
    if (values.ndim != 1 or len(values) != len(has_target) or not len(values)
            or not np.isfinite(values).all() or any(type(x) is not bool for x in has_target)):
        raise ValueError('invalid calibration scores/labels')
    if len(set(has_target)) < 2:
        return {'status': 'UNAVAILABLE_ONE_CLASS', 'threshold': None}
    target = np.asarray(has_target)
    candidates = np.append(np.unique(values), np.nextafter(values.max(), np.inf))
    winner, best = None, -1
    for threshold in candidates:
        accepted = values >= threshold
        correct_positive = int(np.sum(accepted & target))
        correct_negative = int(np.sum(~accepted & ~target))
        # Compare integer numerator to avoid floating point ties changing the rule.
        numerator = correct_positive * int(np.sum(~target)) + correct_negative * int(np.sum(target))
        if numerator > best:
            best, winner = numerator, float(threshold)
    return {'status': 'CALIBRATED', 'threshold': winner,
            'calibration': rejection_metrics(scores, has_target, winner)}


def rejection_metrics(scores: list[float], has_target: list[bool], threshold: float | None) -> dict:
    if threshold is None:
        return {'status': 'UNAVAILABLE'}
    if (not np.isfinite(threshold) or len(scores) != len(has_target)
            or not np.isfinite(scores).all() or any(type(x) is not bool for x in has_target)):
        raise ValueError('invalid rejection evaluation')
    pos, neg = sum(has_target), len(has_target) - sum(has_target)
    accepted = [score >= threshold for score in scores]
    false_accept = sum(a and not y for a, y in zip(accepted, has_target, strict=True))
    false_reject = sum(not a and y for a, y in zip(accepted, has_target, strict=True))
    return {'status': 'DIAGNOSTIC', 'queries': len(scores), 'with_targets': pos, 'no_match': neg,
            'no_match_false_accept': false_accept, 'no_match_correct_reject': neg - false_accept,
            'with_target_false_reject': false_reject, 'with_target_accept': pos - false_reject,
            'no_match_false_accept_rate': false_accept / neg if neg else None,
            'with_target_false_reject_rate': false_reject / pos if pos else None,
            'balanced_accuracy': ((pos - false_reject) / pos + (neg - false_accept) / neg) / 2
            if pos and neg else None}


def summarize(rows: list[dict]) -> dict:
    positive = [row for row in rows if row['metrics']['positive_count']]
    keys = ('AP', 'MRR', 'Recall20', 'Recall50', 'P20', 'P50', 'Hit20', 'Hit50')
    return {'queries': len(rows), 'groups': len({row['group_id'] for row in rows}),
            'positive_queries': len(positive), 'no_match_queries': len(rows) - len(positive),
            **{key: float(np.mean([row['metrics'][key] for row in positive])) if positive else None
               for key in keys}}


def summarize_groups(rows: list[dict]) -> dict:
    output = {}
    for split in ('calibration', 'test'):
        for language in ('cn', 'en'):
            subset = [r for r in rows if r['split'] == split and r['language'] == language]
            output[f'{split}/{language}/all'] = summarize(subset)
            for source in ('V', 'M', 'F', 'VM', 'VF', 'MF', 'VMF'):
                output[f'{split}/{language}/{source}'] = summarize([r for r in subset if r['source'] == source])
            output[f'{split}/{language}/requires_face'] = summarize([r for r in subset if 'F' in r['source']])
    return output


def load_dataset(reader: Reader, directory: Path) -> tuple[dict, list, dict, dict, dict]:
    summary = reader.json(directory / 'preparation-summary.json')
    required = {'sample.json', 'queries.json', 'dataset-plan.json', 'metadata.json', 'evaluation.json'}
    if summary.get('status') != 'COMPLETE' or set(summary.get('derived_sha256', {})) != required:
        raise ValueError('missing frozen derived-document provenance')
    for name, digest in summary['derived_sha256'].items():
        if sha(reader.raw(directory / name)) != digest:
            raise ValueError('derived dataset document differs from preparation provenance')
    sample = reader.json(directory / 'sample.json')
    queries = reader.json(directory / 'queries.json')
    plan = reader.json(directory / 'dataset-plan.json')
    metadata = reader.json(directory / 'metadata.json')
    ids = [row['asset_id'] for row in sample['assets']]
    qids = [row['query_id'] for row in queries]
    if (len(ids) != 1047 or ids != sorted(ids) or len(set(ids)) != len(ids) or len(qids) != 200 or len(set(qids)) != len(qids)
            or len({row['sha256'] for row in sample['assets']}) != len(ids)):
        raise ValueError('complete frozen album membership required')
    if (plan['schema'] != 'photobench-plan-v1' or plan['album'] != 3
            or set(metadata) != set(ids) or set(qids) != {r['query_id'] for r in plan['splits']}):
        raise ValueError('album plan/metadata/query membership differs')
    splits = {row['query_id']: row for row in plan['splits']}
    groups = sorted({row['group_id'] for row in splits.values()})
    if len(groups) != 100 or len(splits) != 200:
        raise ValueError('incorrect bilingual split')
    for group in groups:
        rows = [r for r in splits.values() if r['group_id'] == group]
        expected = 'calibration' if group in groups[:30] else 'test'
        if len(rows) != 2 or {r['language'] for r in rows} != {'cn', 'en'} or any(r['split'] != expected for r in rows):
            raise ValueError('bilingual/calibration leakage')
    if plan['rerank_subset'] != groups[30:40]:
        raise ValueError('rerank subset differs from frozen first ten test groups')
    return sample, queries, plan, metadata, splits


def policy_document(reader: Reader, directory: Path) -> dict:
    summary = reader.json(directory / 'preparation-summary.json')
    return {'policy': POLICY, 'dataset_artifacts_sha256': summary['derived_sha256']}


def load_scores(reader: Reader, directory: Path, sample: dict, queries: list, sample_path: Path) -> tuple[str, np.ndarray, dict]:
    record = reader.json(directory / 'result.json')
    if (record.get('assets') != sample['assets'] or record.get('queries') != queries
            or record.get('sample_sha256') != reader.hashes[sample_path.resolve()]):
        raise ValueError('embedding input/query identity differs')
    if record.get('model') not in POLICY['models']:
        raise ValueError('model outside frozen policy')
    matrices = []
    for name in ('images.npy', 'queries.npy'):
        raw = reader.raw(directory / name)
        if sha(raw) != record['vectors_sha256'][name]:
            raise ValueError('embedding matrix checksum differs')
        matrices.append(np.load(io.BytesIO(raw), allow_pickle=False))
    embeddings_from_record(record, *matrices)
    dimension = POLICY['models'][record['model']]
    scores = normalize_prefix(matrices[1], dimension) @ normalize_prefix(matrices[0], dimension).T
    return f"{record['model']}-{dimension}", scores, record


def evaluate(args: argparse.Namespace) -> dict:
    started = time.perf_counter()
    reader = Reader()
    sample, queries, plan, metadata, splits = load_dataset(reader, args.dataset)
    frozen_policy = reader.json(args.policy)
    if frozen_policy != policy_document(reader, args.dataset):
        raise ValueError('evaluation policy/plan changed after freezing')
    ids = [row['asset_id'] for row in sample['assets']]
    qids = [row['query_id'] for row in queries]
    rankings, tops, provenance = {}, {}, {}
    # No judgments have been loaded when ranking and date parsing are performed.
    for directory in args.run:
        name, scores, record = load_scores(reader, directory, sample, queries, args.dataset / 'sample.json')
        if name in tops:
            raise ValueError('duplicate model run')
        base = {qid: [ids[i] for i in np.argsort(-score, kind='stable')]
                for qid, score in zip(qids, scores, strict=True)}
        rankings[name] = base
        rankings[name + '+date'] = {q['query_id']: date_rerank(base[q['query_id']], q['text'], metadata) for q in queries}
        tops[name] = dict(zip(qids, map(float, scores.max(axis=1)), strict=True))
        provenance[name] = {'run_identity': record['identity'], 'runtime': record.get('resources'),
                            'image_count': len(ids), 'dimension': POLICY['models'][record['model']]}
    identities = [{key: row[key] for key in ('asset_id', 'sha256')} for row in sample['assets']]
    external_subset = []
    for path in args.extra_rankings:
        external = reader.json(path)
        if (external.get('status') != 'COMPLETE' or external.get('input_identities') != identities
                or external.get('query_definitions') != queries):
            raise ValueError('external ranking identity/incomplete result')
        subset = external.get('reranked_query_ids', [])
        expected = [r['query_id'] for r in plan['splits'] if r['group_id'] in plan['rerank_subset']]
        if subset != expected:
            raise ValueError('external rerank query subset differs')
        if ('qwen-1024' not in provenance or external.get('embedding_run_identity') !=
                provenance['qwen-1024']['run_identity']):
            raise ValueError('external reranker embedding run identity differs')
        external_subset = subset
        for name, ranked in external['rankings'].items():
            if name in rankings:
                raise ValueError('duplicate ranking scheme')
            if set(ranked) != set(qids):
                raise ValueError('external ranking query coverage differs')
            for qid in qids:
                baseline = rankings['qwen-1024'][qid]
                validate_ranking(ranked[qid], ids)
                if ((qid not in subset and ranked[qid] != baseline) or
                        (qid in subset and (set(ranked[qid][:50]) != set(baseline[:50]) or
                                            ranked[qid][50:] != baseline[50:]))):
                    raise ValueError('external reranker changed unplanned members or tail')
            rankings[name] = ranked
    judgments = reader.json(args.dataset / 'evaluation.json')
    if judgments.get('schema') != 'photobench-evaluation-v1' or len(judgments['queries']) != len(qids):
        raise ValueError('incorrect judgment schema/count')
    labels = {row['query_id']: row for row in judgments['queries']}
    if set(labels) != set(qids):
        raise ValueError('judgment query coverage differs')
    for qid, row in labels.items():
        if any(row.get(key) != splits[qid][key] for key in ('group_id', 'language', 'split')):
            raise ValueError('judgment split identity differs')
        if row.get('source') not in ('V', 'M', 'F', 'VM', 'VF', 'MF', 'VMF'):
            raise ValueError('unknown evaluation-only source group')
    for group in {row['group_id'] for row in labels.values()}:
        paired = [row for row in labels.values() if row['group_id'] == group]
        if (paired[0]['ground_truth_asset_ids'] != paired[1]['ground_truth_asset_ids'] or
                paired[0]['source'] != paired[1]['source']):
            raise ValueError('bilingual judgments must describe the same retrieval task')
    results = {}
    for name, ranked in rankings.items():
        if set(ranked) != set(qids):
            raise ValueError('ranking query coverage differs')
        rows = []
        for qid in qids:
            validate_ranking(ranked[qid], ids)
            row = labels[qid]
            rows.append({**{key: row[key] for key in ('query_id', 'group_id', 'language', 'split', 'source')},
                         'metrics': retrieval_metrics(ranked[qid], row['ground_truth_asset_ids'])})
        results[name] = {'groups': summarize_groups(rows), 'per_query': rows,
                         'rerank_subset': summarize_groups([r for r in rows if r['query_id'] in external_subset])
                         if external_subset else None}
    thresholds = {}
    for name, scores in tops.items():
        thresholds[name] = {}
        for language in ('cn', 'en'):
            cal = [qid for qid in qids if splits[qid]['language'] == language and splits[qid]['split'] == 'calibration']
            test = [qid for qid in qids if splits[qid]['language'] == language and splits[qid]['split'] == 'test']
            chosen = choose_threshold([scores[qid] for qid in cal], [bool(labels[qid]['ground_truth_asset_ids']) for qid in cal])
            thresholds[name][language] = {**chosen, 'test': rejection_metrics(
                [scores[qid] for qid in test], [bool(labels[qid]['ground_truth_asset_ids']) for qid in test], chosen['threshold'])}
    return {'schema': 'photobench-local-comparison-v1', 'status': 'COMPLETE', 'policy': POLICY,
            'scope': '1047-image complete album3; 30 calibration/70 test bilingual groups; local public-validation split',
            'parsed_dates': {q['query_id']: explicit_month(q['text']) for q in queries if explicit_month(q['text'])},
            'results': results, 'no_match_diagnostic': thresholds, 'provenance': provenance,
            'source_identity': reader.unchanged(), 'evaluator_sha256': sha(Path(__file__).read_bytes()),
            'elapsed_cpu_evaluation_seconds': time.perf_counter() - started,
            'limitations': ['One album, not 300k capacity or personal-query prevalence.',
                            'Chinese and English variants share groups, not independent observations.',
                            'Only 3 calibration/5 test no-match groups; thresholds are exploratory.',
                            'Pure vector/date methods do not learn private person names or reverse geocode locations.',
                            'Official cached vectors were not reused; this is not exact official baseline replication.']}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['freeze', 'evaluate'])
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--policy', type=Path, required=True)
    parser.add_argument('--run', type=Path, action='append', default=[])
    parser.add_argument('--extra-rankings', type=Path, action='append', default=[])
    parser.add_argument('--output', type=Path)
    parser.add_argument('--junit', type=Path, required=True)
    args = parser.parse_args()
    args.dataset, args.policy, args.junit = map(controlled_output, (args.dataset, args.policy, args.junit))
    args.run = [controlled_output(path) for path in args.run]
    args.extra_rankings = [controlled_output(path) for path in args.extra_rankings]
    if args.junit.exists() or (args.output is not None and controlled_output(args.output).exists()):
        raise ValueError('use fresh output/JUnit paths; preserve historical evidence')
    case = ET.Element('testcase', name=args.command, classname='photobench.evaluation')
    failed = False
    try:
        if args.command == 'freeze':
            reader = Reader()
            load_dataset(reader, args.dataset)
            write_frozen_json(args.policy, policy_document(reader, args.dataset))
            print(json.dumps({'status': 'FROZEN', 'policy': str(args.policy)}))
        else:
            if not args.run or args.output is None:
                raise ValueError('evaluate requires runs and output')
            output = controlled_output(args.output)
            if (output == args.policy or output in args.extra_rankings or
                    any(output == root or root in output.parents for root in [args.dataset, *args.run])):
                raise ValueError('report must not overwrite evaluation inputs')
            result = evaluate(args)
            write_json(output, result)
            print(json.dumps({'status': result['status'], 'schemes': list(result['results']), 'seconds': result['elapsed_cpu_evaluation_seconds']}))
        return 0
    except BaseException:
        failed = True
        ET.SubElement(case, 'failure', message='evaluation failed').text = traceback.format_exc()
        raise
    finally:
        suite = ET.Element('testsuite', name='photobench-evaluation', tests='1', failures=str(int(failed)), errors='0')
        suite.append(case)
        args.junit.parent.mkdir(parents=True, exist_ok=True)
        ET.ElementTree(suite).write(args.junit, encoding='utf8', xml_declaration=True)


if __name__ == '__main__':
    raise SystemExit(main())
