"""Exercise the real OSM area assembler on small synthetic public-format inputs."""

import gzip
import importlib.util
import json
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring

import pytest
from shapely.geometry import MultiPolygon, Point, box, mapping, shape

pytest.importorskip("osmium", reason="install the optional places-build group for OSM preparation")

SPEC = importlib.util.spec_from_file_location(
    "prepare_china_places",
    Path(__file__).resolve().parents[2] / "tools/places/prepare_china_places.py",
)
TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOL)


def write_osm(path, *, incomplete=False):
    root = Element("osm", version="0.6", generator="synthetic-cn-test")
    rings = [
        [(120, 30), (121, 30), (121, 31), (120, 31), (120, 30)],
        [(120.06, 30.05), (120.8, 30.05), (120.8, 30.8), (120.06, 30.8), (120.06, 30.05)],
        [(120.08, 30.2), (120.12, 30.2), (120.12, 30.35), (120.08, 30.35), (120.08, 30.2)],
        [(120.095, 30.28), (120.10, 30.28), (120.10, 30.30), (120.095, 30.30), (120.095, 30.28)],
    ]
    for number, ring in enumerate(rings, start=1):
        for index, (longitude, latitude) in enumerate(ring[:-1]):
            SubElement(
                root,
                "node",
                id=str(number * 10 + index),
                lon=str(longitude),
                lat=str(latitude),
                version="1",
            )
    for number in range(1, 5):
        way = SubElement(root, "way", id=str(number), version="1")
        for offset in [0, 1, 2, 3, 0]:
            reference = number * 10 + offset
            if incomplete and number == 3 and offset == 2:
                reference = 999999
            SubElement(way, "nd", ref=str(reference))
    for number, (level, name) in enumerate([(4, "浙江省"), (5, "杭州市"), (6, "西湖区")], 1):
        relation = SubElement(root, "relation", id=str(number), version="1")
        SubElement(relation, "member", type="way", ref=str(number), role="outer")
        if level == 6:
            SubElement(relation, "member", type="way", ref="4", role="inner")
        for key, value in [
            ("type", "boundary"),
            ("boundary", "administrative"),
            ("admin_level", str(level)),
            ("name:zh", name),
            ("name", "English"),
        ]:
            SubElement(relation, "tag", k=key, v=value)
        if level == 4:
            SubElement(relation, "tag", k="ISO3166-2", v="CN-ZJ")
    path.write_bytes(tostring(root, encoding="utf-8", xml_declaration=True))


def row(key, level, geometry, **fields):
    return {
        "id": key,
        "admin_level": level,
        "geometry": mapping(geometry),
        "iso3166_2": "CN-ZJ" if level == 4 else "",
        "kind": TOOL.LEVELS[level],
        "name": "浙江省",
        "aliases": [],
        **fields,
    }


def test_real_area_assembler_builds_holes_and_source_report(tmp_path):
    source = tmp_path / "synthetic.osm"
    write_osm(source)
    original = source.read_bytes()
    report = TOOL.build(source, tmp_path / "output", source_url="https://example.invalid/test")
    assert report["record_count"] == 3 and report["rejected"] == []
    assert report["source_sha256"] == TOOL.digest(source)
    assert source.read_bytes() == original
    with gzip.open(tmp_path / "output" / "cn-admin.jsonl.gz", "rt", encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream]
    district = next(item for item in rows if item["admin_level"] == 6)
    assert district["name"] == "西湖区"
    assert district["parent_province_id"] == "relation/1"
    assert district["parent_city_id"] == "relation/2"
    geometry = shape(district["geometry"])
    assert geometry.contains(Point(120.09, 30.273))
    assert not geometry.contains(Point(120.097, 30.29))
    assert report["samples"][0]["passed"]
    assert not all(sample["passed"] for sample in report["samples"])
    with pytest.raises(FileExistsError):
        TOOL.build(source, tmp_path / "output", source_url="https://example.invalid/test")


def test_incomplete_relation_is_rejected_instead_of_shrunk(tmp_path):
    source = tmp_path / "missing-node.osm"
    write_osm(source, incomplete=True)
    rows, rejected = TOOL.extract(source)
    assert not any(item["id"] == "relation/3" for item in rows)
    assert any(item["id"] == "relation/3" for item in rejected)


def test_city_gap_and_ambiguous_parent_are_explicit():
    province = row("p", 4, box(0, 0, 10, 10))
    district = row("d", 6, box(1, 1, 2, 2))
    accepted, rejected = TOOL.attach_hierarchy([province, district])
    assert not rejected
    assert accepted[1]["parent_province_id"] == "p"
    assert accepted[1]["parent_city_id"] is None
    overlapping = row("p2", 4, box(0, 0, 9, 9))
    accepted, rejected = TOOL.attach_hierarchy([province, overlapping, district])
    assert len(accepted) == 2
    assert rejected == [{"id": "d", "reason": "NO_UNIQUE_CN_PROVINCE"}]


def test_parent_requires_full_containment_not_just_centroid():
    province = row("p", 4, box(0, 0, 10, 10))
    crossing = row("d", 6, box(-1, 1, 2, 2))
    accepted, rejected = TOOL.attach_hierarchy([province, crossing])
    assert len(accepted) == 1 and rejected[0]["reason"] == "NO_UNIQUE_CN_PROVINCE"


def test_chinese_spelling_is_preserved():
    assert TOOL.chinese_names(
        {"name": "Hangzhou", "name:zh": "杭州", "name:zh-Hans": "杭州市", "short_name:zh": "杭州"}
    ) == ["杭州市", "杭州"]
    assert TOOL.chinese_names({"name": "Hangzhou"}) == []


def test_disjoint_multipolygon_and_overlapping_city_parents():
    province = row("p", 4, box(0, 0, 10, 10))
    district = row("d", 6, MultiPolygon([box(1, 1, 2, 2), box(7, 7, 8, 8)]))
    accepted, rejected = TOOL.attach_hierarchy([province, district])
    assert not rejected and accepted[1]["parent_province_id"] == "p"
    city_a = row("a", 5, box(0.5, 0.5, 9, 9))
    city_b = row("b", 5, box(0.5, 0.5, 9, 9))
    accepted, rejected = TOOL.attach_hierarchy([province, district, city_a, city_b])
    assert all(item["id"] != "d" for item in accepted)
    assert rejected == [{"id": "d", "reason": "AMBIGUOUS_CITY_PARENT"}]


def test_cli_returns_failure_until_all_four_source_samples_pass(tmp_path, monkeypatch, capsys):
    source = tmp_path / "synthetic.osm"
    write_osm(source)
    monkeypatch.setattr(
        "sys.argv",
        [str(SPEC.origin), "--source", str(source), "--output", str(tmp_path / "output")],
    )
    assert TOOL.main() == 2
    report = json.loads(capsys.readouterr().out)
    assert report["record_count"] == 3
    assert not all(sample["passed"] for sample in report["samples"])


def test_pbf_requires_upstream_integrity_before_assembly(tmp_path):
    source = tmp_path / "truncated.osm.pbf"
    source.write_bytes(b"incomplete")
    with pytest.raises(ValueError, match="published upstream MD5"):
        TOOL.build(source, tmp_path / "output", source_url=TOOL.SOURCE_URL)
    with pytest.raises(ValueError, match="does not match"):
        TOOL.build(source, tmp_path / "output", source_url=TOOL.SOURCE_URL, expected_md5="0" * 32)
    assert not (tmp_path / "output").exists()


def test_pinned_sha256_and_size_are_checked_before_assembly(tmp_path):
    source = tmp_path / "synthetic.osm"
    write_osm(source)
    with pytest.raises(ValueError, match="byte size"):
        TOOL.build(
            source,
            tmp_path / "size",
            source_url=TOOL.SOURCE_URL,
            expected_sha256=TOOL.digest(source),
            expected_byte_size=1,
        )
    with pytest.raises(ValueError, match="pinned SHA256"):
        TOOL.build(
            source,
            tmp_path / "sha",
            source_url=TOOL.SOURCE_URL,
            expected_sha256="0" * 64,
            expected_byte_size=source.stat().st_size,
        )
    assert not (tmp_path / "size").exists() and not (tmp_path / "sha").exists()


def test_pinned_pbf_records_upstream_verification_as_unavailable(tmp_path):
    source = tmp_path / "synthetic.osm"
    write_osm(source)
    pbf = tmp_path / "synthetic.osm.pbf"
    with TOOL.osmium.SimpleWriter(str(pbf)) as writer:
        for entity in TOOL.osmium.FileProcessor(str(source)):
            writer.add(entity)
    report = TOOL.build(
        pbf,
        tmp_path / "output",
        source_url=TOOL.SOURCE_URL,
        expected_sha256=TOOL.digest(pbf),
        expected_byte_size=pbf.stat().st_size,
    )
    assert report["record_count"] == 3
    assert report["upstream_md5_verified"] is False and report["upstream_md5"] is None
    assert report["pinned_sha256"] == TOOL.digest(pbf)
