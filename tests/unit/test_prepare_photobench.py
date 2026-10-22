"""Complete album extraction and judgment/input isolation."""
from __future__ import annotations

import hashlib
import io
import json
import stat
import sys
import xml.etree.ElementTree as ET
import zipfile

import pytest
from PIL import Image

from tools.evaluation import prepare_photobench as p
from tools.evaluation import prepare_wemm_coco


def query(index=0):
    return {'query_cn': f'猫{index}', 'query_en': f'cat{index}',
            'ground_truth': ['a.jpg'], 'ground_truth_count': 1, 'Source': 'V'}


def jpeg(color='red', orientation=1):
    stream = io.BytesIO()
    exif = Image.Exif()
    exif[274] = orientation
    Image.new('RGB', (8, 6), color).save(stream, format='JPEG', exif=exif)
    return stream.getvalue()


def test_split_is_order_independent_and_binds_languages_without_leaking_judgments():
    rows = [query(i) for i in range(10)]
    actual = p.prepare_queries(rows, ['a.jpg'], 3)
    assert actual == p.prepare_queries(rows[::-1], ['a.jpg'], 3)
    assert sum(row['split'] == 'calibration' for row in actual['splits']) == 6
    groups = {}
    for row in actual['splits']:
        groups.setdefault(row['group_id'], []).append(row)
    assert all({r['language'] for r in group} == {'cn', 'en'} for group in groups.values())
    assert all(len({r['split'] for r in group}) == 1 for group in groups.values())
    assert all(set(row) == {'query_id', 'text'} for row in actual['queries'])


@pytest.mark.parametrize('change', [
    {'ground_truth': ['missing.jpg']}, {'ground_truth': ['a.jpg', 'a.jpg']},
    {'ground_truth_count': True}, {'Source': 'unknown'}, {'query_en': ''},
])
def test_bad_ground_truth_and_query_refused(change):
    with pytest.raises(ValueError):
        p.prepare_queries([{**query(), **change}, query(1)], ['a.jpg'], 1)


def test_duplicate_query_refused():
    with pytest.raises(ValueError, match='duplicate'):
        p.prepare_queries([query(), query()], ['a.jpg'], 1)


@pytest.mark.parametrize('name', ['../a.jpg', 'AUX.jpg', 'foo:.jpg', 'a.jpg ', 'a\\b.jpg'])
def test_gallery_names_are_safe_on_windows(name):
    with pytest.raises(ValueError):
        p.validate_gallery({'filenames': [name]})


def test_gallery_collision_refused():
    with pytest.raises(ValueError, match='colliding'):
        p.validate_gallery({'filenames': ['A.jpg', 'a.jpg']})


def test_metadata_unknown_is_preserved_and_not_inferred():
    row = {'latitude': None, 'longitude': None, 'time': ''}
    assert p.validate_metadata({'a.jpg': row}, ['a.jpg']) == {
        p.asset_id('a.jpg'): {**row, 'time': None}}


@pytest.mark.parametrize('change', [
    {'latitude': 91, 'longitude': 1}, {'latitude': float('nan'), 'longitude': 1},
    {'latitude': 1}, {'time': '2023-02-30'}, {'time': 2023},
])
def test_metadata_refuses_bad_values(change):
    row = {'latitude': None, 'longitude': None, 'time': None, **change}
    with pytest.raises(ValueError):
        p.validate_metadata({'a.jpg': row}, ['a.jpg'])
    with pytest.raises(ValueError):
        p.validate_metadata({'a.jpg': row}, ['a.jpg', 'b.jpg'])


@pytest.mark.parametrize('member', ['../a.jpg', '/album3/a.jpg', 'album3/../a.jpg',
                                    'album3/a:b.jpg', 'album2/a.jpg'])
def test_archive_traversal_and_wrong_album_refused(member):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as z:
        z.writestr(member, b'data')
    with zipfile.ZipFile(stream) as z, pytest.raises(ValueError):
        p.archive_members(z, ['a.jpg'])


def test_archive_literal_backslash_refused_after_platform_normalization(monkeypatch):
    # ZipInfo on Windows normalizes OS separators at construction. Supply the
    # literal member here to also exercise archives read on other platforms.
    member = zipfile.ZipInfo('album3/a.jpg')
    member.filename = 'album3\\a.jpg'
    member.file_size = 4
    archive = zipfile.ZipFile(io.BytesIO(), 'w')
    monkeypatch.setattr(archive, 'infolist', lambda: [member])
    with archive, pytest.raises(ValueError, match='unsafe'):
        p.archive_members(archive, ['a.jpg'])


def test_archive_members_must_be_complete_unique_and_bounded(monkeypatch):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as z:
        z.writestr('album3/a.jpg', b'data')
    with zipfile.ZipFile(stream) as z:
        assert list(p.archive_members(z, ['a.jpg'])) == ['a.jpg']
        with pytest.raises(ValueError, match='complete'):
            p.archive_members(z, ['a.jpg', 'b.jpg'])
        monkeypatch.setattr(p, 'MAX_IMAGE_BYTES', 3)
        with pytest.raises(ValueError, match='oversized'):
            p.archive_members(z, ['a.jpg'])


def test_archive_symlink_refused():
    stream = io.BytesIO()
    member = zipfile.ZipInfo('album3/a.jpg')
    member.create_system = 3
    member.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(stream, 'w') as z:
        z.writestr(member, b'target')
    with zipfile.ZipFile(stream) as z, pytest.raises(ValueError, match='unsafe'):
        p.archive_members(z, ['a.jpg'])


def test_full_decode_preserves_unknown_exif_and_refuses_truncated_images():
    info = p.image_info(jpeg(orientation=0))
    assert info['exif_orientation'] == 0
    assert info['selected_frame'] == 0
    assert info['format'] == 'JPEG'
    with pytest.raises((ValueError, OSError)):
        p.image_info(jpeg()[:-25])
    with pytest.raises(ValueError, match='orientation'):
        p.image_info(jpeg(orientation=9))


def setup_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(prepare_wemm_coco, 'ROOT', tmp_path)
    root = tmp_path / '.tmp'
    root.mkdir()
    archive = root / 'album.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('album3/a.jpg', jpeg())
        z.writestr('album3/b.jpg', jpeg('blue'))
    monkeypatch.setattr(p, 'ARCHIVE_SHA256', hashlib.sha256(archive.read_bytes()).hexdigest())
    monkeypatch.setattr(p, 'IMAGE_COUNT', 2)
    values = {'gallery': {'filenames': ['b.jpg', 'a.jpg']},
              'metadata': {name: {'latitude': None, 'longitude': None, 'time': ''}
                           for name in ('a.jpg', 'b.jpg')},
              'validation': [query(i) for i in range(100)]}
    source_files = {}
    for key, value in values.items():
        file = root / f'{key}.json'
        file.write_text(json.dumps(value), encoding='utf8')
        source_files[key] = (file.name, hashlib.sha256(file.read_bytes()).hexdigest())
    monkeypatch.setattr(p, 'SOURCE_FILES', source_files)
    return archive, root, root / 'output'


def test_preparation_binds_every_image_and_refuses_changed_resume(tmp_path, monkeypatch):
    args = setup_sources(tmp_path, monkeypatch)
    summary = p.prepare(*args)
    assert summary['images'] == 2
    output = args[-1]
    sample = json.loads((output / 'sample.json').read_text())
    assert len(sample['assets']) == 2
    assert all('ground_truth' not in str(row) for row in sample['assets'])
    plan = json.loads((output / 'dataset-plan.json').read_text())
    assert len(plan['rerank_subset']) == 10
    assert p.prepare(*args)['sample_sha256'] == summary['sample_sha256']
    (output / 'images' / 'a.jpg').write_bytes(b'changed')
    with pytest.raises(ValueError, match='content changed'):
        p.prepare(*args)


def test_cli_interrupt_records_failure_in_junit(tmp_path, monkeypatch):
    monkeypatch.setattr(prepare_wemm_coco, 'ROOT', tmp_path)
    junit = tmp_path / '.tmp' / 'junit.xml'
    monkeypatch.setattr(sys, 'argv', ['prepare', '--archive', 'a', '--source-root', 's',
                                    '--output', 'o', '--junit', str(junit)])
    def interrupt(*args):
        raise KeyboardInterrupt('cancelled')
    monkeypatch.setattr(p, 'prepare', interrupt)
    with pytest.raises(KeyboardInterrupt):
        p.main()
    tree = ET.parse(junit)
    assert tree.getroot().attrib['failures'] == '1'
    assert 'KeyboardInterrupt' in tree.findtext('testcase/failure')


def test_preparation_does_not_overwrite_a_previous_junit_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(prepare_wemm_coco, 'ROOT', tmp_path)
    junit = tmp_path / '.tmp' / 'junit.xml'
    junit.parent.mkdir()
    junit.write_bytes(b'previous failure')
    monkeypatch.setattr(sys, 'argv', ['prepare', '--archive', 'a', '--source-root', 's',
                                    '--output', 'o', '--junit', str(junit)])
    with pytest.raises(ValueError, match='historical evidence'):
        p.main()
    assert junit.read_bytes() == b'previous failure'
