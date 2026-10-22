"""Dataset semantics cannot turn missing annotations into convenient negatives."""

import io

import pytest
from PIL import Image

from tools.evaluation import prepare_budget_search_data as prep


def test_hierarchy_follows_subcategories_and_merges_multiple_parents():
    tree = {"LabelName": "root", "Subcategory": [
        {"LabelName": "animal", "Subcategory": [{"LabelName": "cat"}]},
        {"LabelName": "pet", "Subcategory": [{"LabelName": "cat"}]}]}
    assert prep.ancestors_of(tree, "cat") == {"root", "animal", "pet"}
    with pytest.raises(ValueError, match="missing"):
        prep.ancestors_of(tree, "fish")
    with pytest.raises(ValueError, match="cycle"):
        prep.ancestors_of({"LabelName": "x", "Subcategory": [{"LabelName": "x"}]}, "x")


def test_missing_class_is_unknown_even_if_parent_or_other_species_positive():
    assert prep.resolve_label({"animal": 1, "dog": 1}, "cat", {"animal"}) == {
        "relevant": None, "basis": "unknown", "label_ids": []}


def test_negative_ancestor_is_evidence_and_direct_negative_keeps_its_source():
    assert prep.resolve_label({"animal": 0}, "cat", {"animal"}) == {
        "relevant": False, "basis": "negative_ancestor", "label_ids": ["animal"]}
    assert prep.resolve_label({"cat": 0, "animal": 0}, "cat", {"animal"}) == {
        "relevant": False, "basis": "direct_human_label", "label_ids": ["cat"]}
    assert prep.resolve_label({"cat": 1}, "cat", {"animal"})["relevant"] is True


@pytest.mark.parametrize("labels", [{"cat": 1, "animal": 0}, {"cat": 0.8}, {"cat": True}])
def test_conflicting_or_invalid_human_labels_are_rejected(labels):
    with pytest.raises(ValueError):
        prep.resolve_label(labels, "cat", {"animal"})


def population():
    rows = []
    for i in range(100):
        cat = i < 20
        dog = 20 <= i < 40
        rows.append({"asset_id": f"{i:016x}", "judgments": {
            "cat": {"relevant": cat, "basis": "direct_human_label"},
            "dog": {"relevant": dog, "basis": "direct_human_label"}}})
    return rows


def test_selection_is_seeded_unique_bounded_and_independent_of_input_order():
    quotas = {name: 5 for name in prep.QUOTAS}
    rows = prep.freeze_selection(population(), quotas=quotas)
    assert rows == prep.freeze_selection(list(reversed(population())), quotas=quotas)
    assert len(rows) == len({r["asset_id"] for r in rows}) == 25
    assert rows != prep.freeze_selection(population(), seed=1, quotas=quotas)
    assert all(r["judgments"]["cat"]["relevant"] is False
               and r["judgments"]["dog"]["relevant"] is False
               for r in rows if r["selection_stratum"] == "shared_background")


def test_selection_rejects_shortage_duplicates_unknown_strata_and_oversized_request():
    with pytest.raises(ValueError, match="insufficient"):
        prep.freeze_selection(population())
    with pytest.raises(ValueError, match="duplicate"):
        prep.freeze_selection(population() + population()[:1])
    for quotas in ({"cat_positive": 2}, {name: 100 for name in prep.QUOTAS},
                   {name: -1 for name in prep.QUOTAS}, {name: True for name in prep.QUOTAS}):
        with pytest.raises(ValueError, match="quotas"):
            prep.freeze_selection(population(), quotas=quotas)


def test_jpeg_must_fully_decode_and_stay_bounded():
    image = Image.new("RGB", (40, 30))
    stream = io.BytesIO()
    image.save(stream, format="JPEG")
    assert prep.image_info(stream.getvalue()) == {"width": 40, "height": 30, "exif_orientation": 1}
    with pytest.raises((ValueError, OSError)):
        prep.image_info(stream.getvalue()[:-30])
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    with pytest.raises(ValueError, match="format"):
        prep.image_info(stream.getvalue())
    with pytest.raises(ValueError, match="bound"):
        prep.image_info(b"x" * (prep.MAX_IMAGE_BYTES + 1))


class Response(io.BytesIO):
    def __init__(self, raw, length=None, status=200):
        super().__init__(raw)
        self.status = status
        self.headers = {} if length is None else {"Content-Length": str(length)}


@pytest.mark.parametrize("raw,length,status", [(b"1234", 4, 200), (b"12", 3, 200),
                                               (b"", 0, 200), (b"1", 1, 206)])
def test_http_fetch_refuses_oversize_truncation_empty_and_partial(monkeypatch, raw, length, status):
    monkeypatch.setattr(prep.urllib.request, "urlopen", lambda *a, **k: Response(raw, length, status))
    with pytest.raises(ValueError):
        prep.fetch_bytes("https://example.invalid", cap=3)


def test_http_fetch_bounds_missing_content_length(monkeypatch):
    monkeypatch.setattr(prep.urllib.request, "urlopen", lambda *a, **k: Response(b"1234"))
    with pytest.raises(ValueError, match="oversized"):
        prep.fetch_bytes("https://example.invalid", cap=3)
    monkeypatch.setattr(prep.urllib.request, "urlopen", lambda *a, **k: Response(b"123"))
    assert prep.fetch_bytes("https://example.invalid", cap=3) == b"123"
