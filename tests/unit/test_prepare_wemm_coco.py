"""Fair sample selection, honest public labels, and frozen source identity."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from tools.evaluation import prepare_wemm_coco as pilot


def population(count=100):
    return [{"asset_id": i, "coco_categories": ["cat", "dog"] if i % 2 else ["dog"],
             "coco": {"width": 640 if i % 2 else 480, "height": 480 if i % 2 else 640}}
            for i in range(count)]


def test_selection_is_order_independent_with_model_independent_random_half():
    rows = population()
    selected = pilot.freeze_selection(rows, seed=7, random_count=10, quotas={"cat": 5, "dog": 5})
    assert selected == pilot.freeze_selection(rows[::-1], seed=7, random_count=10,
                                            quotas={"cat": 5, "dog": 5})
    other = pilot.freeze_selection(rows, seed=7, random_count=10, quotas={"dog": 10})
    assert selected[:10] == other[:10]
    assert len({row["asset_id"] for row in selected}) == 20
    assert all(row["selection_stratum"] == "random" for row in selected[:10])
    assert all("cat" in row["coco_categories"] for row in selected[10:15])


@pytest.mark.parametrize(("random_count", "quotas"), [(5, {"cat": 6}), (501, {"dog": 500}),
                                                     (10, {"absent": 5}), (10, {"cat": 0})])
def test_selection_refuses_unfair_unbounded_or_impossible_samples(random_count, quotas):
    with pytest.raises(ValueError):
        pilot.freeze_selection(population(), random_count=random_count, quotas=quotas)


def test_population_uses_instance_annotations_and_keeps_image_license():
    source = {"categories": [{"id": 17, "name": "cat"}, {"id": 18, "name": "dog"}],
              "licenses": [{"id": 4, "name": "CC BY"}, {"id": 1, "name": "other"}],
              "images": [{"id": 1, "license": 4, "width": 100, "height": 100},
                         {"id": 2, "license": 4, "width": 100, "height": 100},
                         {"id": 3, "license": 1, "width": 100, "height": 100}],
              "annotations": [{"image_id": 1, "category_id": 17, "area": 200},
                              {"image_id": 1, "category_id": 17, "area": 300},
                              {"image_id": 3, "category_id": 18, "area": 100}]}
    rows = pilot.load_population(source)
    assert [row["asset_id"] for row in rows] == [1, 2]
    assert rows[0]["category_counts"] == {"cat": 2}
    assert rows[0]["category_max_area_fraction"] == {"cat": .03}
    assert rows[1]["coco_categories"] == []
    assert rows[0]["license"] == {"id": 4, "name": "CC BY"}


def test_labels_bind_content_ids_queries_and_leave_complex_queries_unjudged():
    rows = population(2)
    for row in rows:
        row["sha256"] = hashlib.sha256(str(row["asset_id"]).encode()).hexdigest()
    labels = pilot.bind_labels(pilot.make_labels(rows, "annotation-sha"), rows)
    assert labels["queries"]["cat"] == [{"asset_id": 0, "relevant": False},
                                          {"asset_id": 1, "relevant": True}]
    assert labels["queries"]["two_cats"] == [{"asset_id": 0, "relevant": None},
                                               {"asset_id": 1, "relevant": None}]
    assert labels["input_identities"] == [{"asset_id": row["asset_id"], "sha256": row["sha256"]}
                                            for row in rows]
    assert labels["query_definitions"] == pilot.QUERIES
    assert "Missing instance" in labels["provenance"]["limitations"][0]
    with pytest.raises(ValueError):
        pilot.bind_labels(labels, population(2))


def test_frozen_plan_cannot_be_changed_on_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(pilot, "ROOT", tmp_path)
    path = tmp_path / ".tmp" / "plan.json"
    pilot.write_frozen_json(path, {"ids": [1, 2], "query": "猫"})
    before = path.read_bytes()
    pilot.write_frozen_json(path, {"query": "猫", "ids": [1, 2]})
    assert path.read_bytes() == before
    with pytest.raises(ValueError, match="frozen"):
        pilot.write_frozen_json(path, {"ids": [1, 2], "query": "狗"})
    assert path.read_bytes() == before


@pytest.mark.parametrize("failure", ["short_http", "broken_jpeg", "wrong_dimensions", "none"])
def test_image_download_rejects_partial_or_wrong_bytes_before_cache_publish(monkeypatch, failure):
    from PIL import Image

    stream = io.BytesIO()
    Image.new("RGB", (8, 6)).save(stream, format="JPEG")
    raw = stream.getvalue()

    class Response(io.BytesIO):
        status = 200

        def __init__(self):
            value = raw[:-20] if failure in {"short_http", "broken_jpeg"} else raw
            super().__init__(value)
            self.headers = {"Content-Length": str(len(raw) if failure == "short_http" else len(value))}

    monkeypatch.setattr(pilot.urllib.request, "urlopen", lambda *a, **kw: Response())
    if failure == "none":
        assert pilot.fetch_complete_image("https://example.invalid/image", 8, 6) == raw
    else:
        with pytest.raises((OSError, ValueError)):
            pilot.fetch_complete_image("https://example.invalid/image", 9 if failure == "wrong_dimensions" else 8, 6)


def test_annotation_download_retries_identical_range_and_verifies_frozen_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(pilot, "ROOT", tmp_path)
    directory = tmp_path / ".tmp" / "annotations"
    raw = json.dumps({"images": [], "annotations": []}).encode()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(pilot.MEMBER, raw)
    zip_bytes = stream.getvalue()
    payload_offset = 30 + len(pilot.MEMBER.encode())
    reads = []

    class FakeRemote(io.BytesIO):
        def __init__(self, url):
            super().__init__(zip_bytes)
            self.size = len(zip_bytes)
            self.failed = False

        def read(self, size=-1):
            if self.tell() == payload_offset:
                reads.append((self.tell(), size))
                if not self.failed:
                    self.failed = True
                    raise ValueError("truncated remote ZIP range")
            return super().read(size)

    monkeypatch.setattr(pilot, "RemoteZip", FakeRemote)
    monkeypatch.setattr(pilot, "ANNOTATION_SHA256", hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(pilot.time, "sleep", lambda seconds: None)
    path, identity = pilot.download_annotations(directory)
    assert path.read_bytes() == raw
    assert reads[0] == reads[1]
    assert identity["sha256"] == hashlib.sha256(raw).hexdigest()
    assert pilot.download_annotations(directory) == (path, identity)
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="frozen identity"):
        pilot.download_annotations(directory)


@pytest.mark.parametrize(("linked_part", "kind"), [(".tmp", "is_junction"),
                                                    ("images", "is_symlink"),
                                                    ("sample.json", "is_symlink")])
def test_output_refuses_linked_ancestors_and_file_targets(tmp_path, monkeypatch, linked_part, kind):
    monkeypatch.setattr(pilot, "ROOT", tmp_path)
    path = tmp_path / ".tmp" / "pilot" / "images" / "sample.json"
    monkeypatch.setattr(pilot.Path, kind, lambda self: self.name == linked_part)
    with pytest.raises(ValueError, match="links"):
        pilot.controlled_output(path)


def test_output_must_be_inside_ignored_project_location(tmp_path, monkeypatch):
    monkeypatch.setattr(pilot, "ROOT", tmp_path)
    assert pilot.controlled_output(tmp_path / ".tmp" / "pilot") == tmp_path / ".tmp" / "pilot"
    for path in (tmp_path / "src" / "data.json", tmp_path.parent / "outside" / ".tmp"):
        with pytest.raises(ValueError):
            pilot.controlled_output(path)
