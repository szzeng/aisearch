import json
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from tools.evaluation import album_light_tagger as light


def test_prepared_geometry_keeps_wider_context_and_caps_panoramas():
    assert light.regions(600, 400) == [[100, 0, 500, 400], [33, 0, 566, 400]]
    assert light.regions(400, 600) == [[0, 100, 400, 500], [0, 33, 400, 566]]
    assert light.regions(300, 300) == [[0, 0, 300, 300]]
    assert light.regions(300, 100) == [[100, 0, 200, 100]]
    with pytest.raises(ValueError):
        light.regions(0, 1)


def test_tensor_color_normalization_layout_and_source_are_stable():
    image = Image.new("RGB", (224, 224), (255, 0, 128))
    before = image.tobytes()
    tensors, _ = light.tensors(image)
    assert len(tensors) == 1
    assert tensors[0].shape == (1, 3, 224, 224)
    assert tensors[0].dtype == np.float32 and tensors[0].flags.c_contiguous
    np.testing.assert_allclose(tensors[0][0, :, 0, 0],
                               (np.array([255, 0, 128]) - light.PREPROCESS["mean_255"])
                               / light.PREPROCESS["std_255"], rtol=1e-6)
    assert image.tobytes() == before


def test_pooled_scores_keep_both_views_and_are_not_a_second_softmax():
    logits = np.zeros((2, 1000), dtype=np.float32)
    logits[0, 0], logits[1, 1] = 1000, 1000
    pooled = light.pool_logits(logits)
    assert pooled[0] == pooled[1] == 1
    assert pooled.sum() == 2
    np.testing.assert_allclose(light.pool_logits(np.zeros((1, 1000))), .001)


@pytest.mark.parametrize("logits", [np.zeros((0, 1000)), np.zeros((3, 1000)),
                                    np.zeros((1, 999)), np.full((1, 1000), np.nan),
                                    np.full((1, 1000), np.inf)])
def test_invalid_outputs_fail_instead_of_silently_producing_ranks(logits):
    with pytest.raises(ValueError):
        light.pool_logits(logits)


def test_mapping_preserves_native_classes_and_defaults_without_top1_generalization():
    labels = "\n".join(["tabby cat", *[f"class{i}" for i in range(999)]])
    rules = ('\t"tabby cat": {\n\t\tLabel: "cat",\n\t\tThreshold: 0.150000,\n'
             '\t\tPriority: 5,\n\t\tCategories: []string{"animal"},\n\t},')
    classes, mapping = light.parse_mapping(labels, rules)
    assert classes[0] == "tabby cat"
    assert mapping[0]["label"] == "cat" and mapping[0]["categories"] == ["animal"]
    assert mapping[1]["label"] == "" and mapping[1]["source"] == "photoprism-default"
    assert len(mapping) == 1000
    duplicates = labels.replace("class0\n", "tabby cat\n")
    repeated, repeated_mapping = light.parse_mapping(duplicates, rules)
    assert repeated[:2] == ["tabby cat", "tabby cat"]
    assert [x["index"] for x in repeated_mapping[:2]] == [0, 1]
    with pytest.raises(ValueError, match="format"):
        light.parse_mapping(labels, rules.replace("Threshold:", "Unknown:"))
    with pytest.raises(ValueError, match="duplicate"):
        light.parse_mapping(labels, rules + "\n" + rules)
    with pytest.raises(ValueError, match="vocabulary"):
        light.parse_mapping("cat\ncat", rules)


def fake_session(*, logits="true", providers=None, shape=None):
    return SimpleNamespace(
        get_inputs=lambda: [SimpleNamespace(name="input", shape=[1, 3, 224, 224], type="tensor(float)")],
        get_outputs=lambda: [SimpleNamespace(name="logits", shape=shape or [1, 1000], type="tensor(float)")],
        get_modelmeta=lambda: SimpleNamespace(custom_metadata_map={"photoprism.logits": logits}),
        get_providers=lambda: providers or ["CPUExecutionProvider"],
    )


def test_actual_graph_contract_prevents_softmax_twice_and_gpu_fallback():
    assert light.validate_session(fake_session())["photoprism.logits"] == "true"
    for session in [fake_session(logits="false"), fake_session(shape=[1, 1001]),
                    fake_session(providers=["CUDAExecutionProvider", "CPUExecutionProvider"])]:
        with pytest.raises(ValueError):
            light.validate_session(session)


def test_manifest_retains_order_and_unknown_assets_and_binds_content(tmp_path, monkeypatch):
    monkeypatch.setattr(light, "ROOT", tmp_path)
    image = tmp_path / "photo.jpg"
    image.write_bytes(b"fixture")
    manifest = tmp_path / "manifest.json"
    assets = [{"asset_id": i, "path": str(image), "sha256": "a" * 64} for i in [9, 2, 7]]
    document = {"schema": "broad-album-data-v1", "dataset": "fixture", "assets": assets,
                "judgments": [{"asset_id": 9, "relevant": None}]}
    manifest.write_text(json.dumps(document), encoding="utf-8")
    _, actual, digest = light.read_manifest(manifest, 0)
    assert actual == assets and digest == light.sha256(manifest)
    assert light.read_manifest(manifest, 2)[1] == assets[:2]
    document["assets"] = [assets[0], assets[0]]
    manifest.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        light.read_manifest(manifest, 0)


def test_prepared_files_cannot_be_replaced_by_same_size_wrong_model(tmp_path, monkeypatch):
    monkeypatch.setattr(light, "ROOT", tmp_path)
    model = tmp_path / "model.onnx"
    model.write_bytes(b"good")
    expected = light.sha256(model)
    monkeypatch.setattr(light, "FILES", {model.name: ("https://example.test/model", expected, 4)})
    assert light.verify_files(tmp_path)["files"][model.name]["sha256"] == expected
    model.write_bytes(b"evil")
    with pytest.raises(ValueError, match="mismatch"):
        light.verify_files(tmp_path)
