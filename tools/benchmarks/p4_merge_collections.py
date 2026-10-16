"""Compare upstream and merged collection reads on an isolated 300k synthetic catalog."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import statistics
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from photo_search.catalog.db import Catalog
from photo_search.catalog.schema import SUPPORTED_SCHEMA_VERSION
from photo_search.catalog.sqlite_io import open_readonly
from photo_search.media.cache import MediaCache
from photo_search.persons import collections
from tools.benchmarks.p4_perf_people_300k import Oracle


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(data_root: Path, baseline_path: Path, output: Path, samples: int) -> None:
    from photo_search.api import queries

    db = data_root / "catalog/catalog.db"
    assert not Path(str(db) + "-wal").exists(), "checkpoint before immutable measurement"
    predicate_path = baseline_path.with_name('upstream_collection_sql.py')
    source_paths = [*Path("src/photo_search").rglob("*.py"), baseline_path, predicate_path,
                    Path(__file__), Path('tools/benchmarks/p4_perf_people_300k.py')]
    hashes = {str(p): file_hash(p) for p in source_paths}
    db_hash = file_hash(db)
    spec = importlib.util.spec_from_file_location("photo_search.persons._upstream_bench", baseline_path)
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    predicate_spec = importlib.util.spec_from_file_location('upstream_collection_sql', predicate_path)
    old_predicate = importlib.util.module_from_spec(predicate_spec)
    predicate_spec.loader.exec_module(old_predicate)
    baseline.collection_condition = old_predicate.collection_condition
    connection = open_readonly(db, immutable=True)
    catalog = Catalog(connection, db_path=db, data_root=data_root)
    cache = MediaCache.open(data_root)
    records = []
    checks = {'sources_unchanged': False, 'database_unchanged': False, 'completed': False}
    try:
        assert catalog.schema_version == SUPPORTED_SCHEMA_VERSION
        oracle = Oracle(connection)
        available = {int(r[0]) for r in connection.execute(
            "SELECT asset_id FROM file_locations WHERE availability='PRESENT'"
        )}
        located = {int(r[0]) for r in connection.execute("SELECT asset_id FROM file_locations")}
        sha = {int(r[0]): r[1] for r in connection.execute("SELECT asset_id,sha256 FROM assets")}
        face_counts = {}
        archived = {}
        for cid, fid, aid, revision in connection.execute(
            "SELECT collection_id,source_face_id,asset_id,input_revision "
            "FROM person_collection_face_members"
        ):
            face_counts[cid] = face_counts.get(cid, 0) + 1
            archived.setdefault(cid, []).append(fid)
            if aid in oracle.assets and sha[aid] == revision:
                oracle.collection_assets.setdefault(cid, set()).add(aid)
        for cid in oracle.collections:
            oracle.collection_assets.setdefault(cid, set()).difference_update(located - available)
        selected = connection.execute(
            "SELECT * FROM person_collections WHERE name IN ('merge-static', 'merge-mixed') "
            "ORDER BY collection_id"
        ).fetchall()
        assert len(selected) == 2 and all(face_counts.get(r['collection_id'], 0) for r in selected)

        directory_offset = sorted(oracle.collections).index(selected[0]['collection_id']) - 1
        directory_cursor = collections._encode('pc1', ['collections1'], sorted(oracle.collections)[directory_offset - 1])

        def validate_directory(answer):
            expected = sorted(oracle.collections)[directory_offset:directory_offset + 40]
            assert [r['collection_id'] for r in answer['collections']] == expected
            assert {r['collection_id'] for r in selected} <= set(expected)
            for row in answer['collections']:
                cid = row['collection_id']
                assert row['member_count'] == len(oracle.collection_members[cid])
                assert row['face_member_count'] == face_counts.get(cid, 0)
                assert row['photo_count'] == len(oracle.collection_assets[cid])

        cases = [("directory_with_static_and_mixed", lambda module: module.list_collections(
            catalog, cursor=directory_cursor), validate_directory)]
        for row in selected:
            cid, token, name = row['collection_id'], row['instance_token'], row['name']
            expected = oracle.ordered_photos(oracle.collection_assets[cid])

            def validate_photos(answer, expected=expected):
                assert [r['asset_id'] for r in answer['assets']] == expected[:60]
                assert answer['collection']['photo_count'] == len(expected)
                assert answer['has_more'] == (len(expected) > 60)

            def validate_faces(answer, cid=cid):
                assert [r['face_id'] for r in answer['faces']] == sorted(archived[cid])[:40]
                assert answer['has_more'] == (len(archived[cid]) > 40)

            cases.extend([
                (name + '_photos', lambda module, cid=cid, token=token: module.photo_page(
                    catalog, cache, cid, instance_token=token), validate_photos),
                (name + '_faces', lambda module, cid=cid, token=token: module.face_members_page(
                    catalog, cid, instance_token=token), validate_faces),
            ])
        for name, invoke, validate in cases:
            record = {'name': name, 'before_ms': [], 'after_ms': [], 'responses_equal': True}
            for iteration in range(samples + 1):
                answers = {}
                phases = [('before', baseline), ('after', collections)]
                if iteration % 2:
                    phases.reverse()
                for phase, module in phases:
                    # The shared asset pager imports this predicate too. Switch both
                    # references so the upstream OR and merged UNION are really compared.
                    with patch.object(queries, 'collection_condition', module.collection_condition):
                        tick = time.perf_counter()
                        answer = invoke(module)
                        elapsed = (time.perf_counter() - tick) * 1000
                    validate(answer)
                    answers[phase] = digest(answer)
                    if iteration:
                        record[phase + '_ms'].append(elapsed)
                    else:
                        record[phase + '_first_ms'] = elapsed
                assert answers['before'] == answers['after'], (name, answers)
            for phase in ('before', 'after'):
                values = record[phase + '_ms']
                record[phase + '_median_ms'] = statistics.median(values)
                record[phase + '_p95_ms'] = sorted(values)[math.ceil(len(values) * .95) - 1]
            records.append(record)
            checks[name] = True
            print(json.dumps({k: v for k, v in record.items() if not isinstance(v, list)}), flush=True)
        checks['sources_unchanged'] = all(file_hash(p) == value for p, value in hashes.items())
        checks['database_unchanged'] = file_hash(db) == db_hash
        checks['completed'] = len(records) == 5
        assert all(checks.values())
    finally:
        catalog.close()
        report = {
            'layer': 'warm service calls; excludes HTTP, browser, inference and source images',
            'baseline': str(baseline_path), 'baseline_predicate': str(predicate_path),
            'shared_dependencies': 'current catalog, indexes, members and asset pager; collection module and membership predicate differ',
            'samples': samples, 'assets': 300000, 'database_sha256': db_hash,
            'source_sha256': hashes, 'results': records, 'checks': checks,
        }
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        suite = ET.Element('testsuite', name='merge_collection_measurement', tests=str(len(checks) + 1),
                           failures=str(int(len(records) != 5) + sum(not v for v in checks.values())),
                           errors='0', skipped='0')
        for name, ok in [*checks.items(), ('all_five_cases_complete', len(records) == 5)]:
            case = ET.SubElement(suite, 'testcase', classname='measurement', name=name)
            if not ok:
                ET.SubElement(case, 'failure', message='measurement incomplete or invariant failed')
        ET.ElementTree(suite).write(output.with_suffix('.xml'), encoding='utf-8', xml_declaration=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--samples', type=int, default=30)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    for path in [args.data_root, args.baseline, args.output]:
        if not path.resolve().is_relative_to(root / '.tmp'):
            parser.error('all paths must be within the project .tmp directory')
    if args.samples < 1:
        parser.error('--samples must be positive')
    run(args.data_root, args.baseline, args.output, args.samples)


if __name__ == '__main__':
    main()
