from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import numpy as np
import pytest

from photo_search.models.embedding.base import identity_of, read_manifest
from tools.prepare_models import export_category_heads as export


@pytest.fixture
def export_fixture(tmp_path, monkeypatch):
    local_manifest = tmp_path / "formal-manifest.json"
    local_manifest.write_bytes(export.MANIFEST.read_bytes())
    monkeypatch.setattr(export, "ROOT", tmp_path)
    monkeypatch.setattr(export, "MANIFEST", local_manifest)
    rng = np.random.default_rng(8)
    weights = rng.normal(size=(8, 512))
    images = export.normalize_prefix(rng.normal(size=(20, 512)), 512)
    queries = export.normalize_prefix(rng.normal(size=(8, 512)), 512)
    provenance = {"research_identity": "frozen", "research_weights_sha256": "a" * 64}
    monkeypatch.setattr(export, "source_head", lambda path: ({}, weights, np.zeros(8), provenance))
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    identity = identity_of(read_manifest(export.MANIFEST))
    evidence = {
        "schema": "category-head-compatibility-v1", "status": "PASS", "source": provenance,
        "processor_id": str(identity.processor_id), "space_id": str(identity.space_id),
        "dimension": 512, "source_manifest_sha256": export.sha256(export.MANIFEST),
        "decoded_rgb_equal": True, "source_code_sha256": {
            "export_category_heads.py": export.sha256(Path(export.__file__))},
        "image_comparison": export.numerical_comparison(images, images, weights, list(range(20))),
        "text_max_abs_error": 0.0, "inputs": [{"kind": "synthetic"}] * 20,
        "vectors_sha256": {},
    }
    for name, value in (("production-images.npy", images), ("research-images.npy", images),
                        ("production-queries.npy", queries), ("research-queries.npy", queries)):
        np.save(evidence_dir / name, value, allow_pickle=False)
        evidence["vectors_sha256"][name] = export.sha256(evidence_dir / name)
    path = evidence_dir / "compatibility.json"
    path.write_bytes(export.json_bytes(evidence))
    args = argparse.Namespace(head_dir=tmp_path / "source", evidence=path,
                              data_root=tmp_path / "data", manifest=local_manifest)
    return args, evidence, weights


def test_export_reproducible_strict_payload_preserves_provenance(export_fixture):
    args, evidence, weights = export_fixture
    result = export.export(args)
    directory = Path(result["directory"])
    manifest = json.loads((directory / "manifest.json").read_bytes())
    payload = json.loads((directory / "weights.json").read_bytes())
    assert result["status"] == "EXPORTED_NOT_ACTIVATED"
    assert result["runtime_bytes"] < 262144
    assert set(manifest) == {"schema", "version", "processor_id", "space_id", "dimension",
                             "source_manifest_sha256", "weights_sha256", "export_evidence_sha256",
                             "classes", "aliases"}
    assert set(payload) == {"schema", "weights"}
    assert manifest["weights_sha256"] == export.sha256(directory / "weights.json")
    assert manifest["export_evidence_sha256"] == export.sha256(directory / "export-evidence.json")
    exported_evidence = json.loads((directory / "export-evidence.json").read_bytes())
    assert exported_evidence["compatibility"] == evidence
    assert exported_evidence["compatibility_sha256"] == export.sha256(args.evidence)
    np.testing.assert_array_equal([payload["weights"][key] for key in export.CLASS_IDS], weights)
    with pytest.raises(FileExistsError):
        export.export(args)


@pytest.mark.parametrize("field,value", [
    ("schema", "other"), ("status", "FAIL"), ("dimension", 2048),
    ("processor_id", "old"), ("space_id", "legacy"), ("source_manifest_sha256", "b" * 64),
    ("decoded_rgb_equal", False), ("source", {}), ("source_code_sha256", {}),
    ("text_max_abs_error", float("nan")), ("text_max_abs_error", 0.01),
    ("inputs", [{}] * 100),
])
def test_export_refuses_mismatched_or_failed_evidence(export_fixture, field, value):
    args, evidence, _ = export_fixture
    evidence[field] = value
    args.evidence.write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(ValueError):
        export.export(args)
    assert not args.data_root.exists()


def test_export_refuses_changed_raw_vector_file(export_fixture):
    args, _, _ = export_fixture
    (args.evidence.parent / "production-images.npy").write_bytes(b"changed")
    with pytest.raises(ValueError, match="artifact changed"):
        export.export(args)


def test_export_recomputes_ranking_instead_of_trusting_summary(export_fixture):
    args, evidence, _ = export_fixture
    for category in evidence["image_comparison"]["rankings"].values():
        category["production"] = category["research"] = list(range(20))
    args.evidence.write_bytes(export.json_bytes(evidence))
    with pytest.raises(ValueError, match="reproduce"):
        export.export(args)


def test_export_refuses_incomplete_vector_evidence(export_fixture):
    args, evidence, _ = export_fixture
    del evidence["vectors_sha256"]["research-queries.npy"]
    args.evidence.write_bytes(export.json_bytes(evidence))
    with pytest.raises(ValueError, match="incomplete"):
        export.export(args)


def test_export_binds_real_installed_manifest_bytes(export_fixture):
    args, _, _ = export_fixture
    installed = read_manifest(export.MANIFEST)
    installed["installed_from"] = "former-install-source"
    path = args.evidence.parent / "installed.json"
    path.write_bytes(export.json_bytes(installed))
    args.manifest = path
    result = export.export(args)
    assert result["manifest"]["source_manifest_sha256"] == export.sha256(path)
    assert result["manifest"]["source_manifest_sha256"] != export.sha256(export.MANIFEST)


def test_export_refuses_semantic_edit_despite_deployment_metadata(export_fixture):
    args, _, _ = export_fixture
    installed = read_manifest(export.MANIFEST)
    installed["installed_from"] = "former-install-source"
    installed["preprocessing"]["max_pixels"] *= 2
    path = args.evidence.parent / "installed.json"
    path.write_bytes(export.json_bytes(installed))
    args.manifest = path
    with pytest.raises(ValueError, match="encoding semantics"):
        export.export(args)


def test_path_rejects_outside_and_parent_escape(tmp_path, monkeypatch):
    monkeypatch.setattr(export, "ROOT", tmp_path)
    for path in (tmp_path.parent / "outside", tmp_path / ".." / "outside"):
        with pytest.raises(ValueError, match="workspace"):
            export.local_path(path, exists=False)


def test_numerical_gate_checks_rankings_not_only_close_vectors():
    image = np.zeros((2, 512), dtype=np.float32)
    image[:, 0] = 1
    other = image.copy()
    other[1, 1] = 1e-7
    weights = np.zeros((8, 512))
    weights[:, 1] = 1
    result = export.numerical_comparison(image, other, weights, [1, 2])
    assert result["max_abs_error"] < export.MAX_ERROR
    assert not result["passed"]


def test_numerical_gate_refuses_unnormalized_and_nan():
    images = np.ones((2, 512))
    with pytest.raises(ValueError, match="normalized"):
        export.numerical_comparison(images, images, np.ones((8, 512)), [1, 2])
    images[0, 1] = np.nan
    with pytest.raises(ValueError, match="invalid comparison"):
        export.numerical_comparison(images, images, np.ones((8, 512)), [1, 2])


def test_source_model_contract_refuses_changed_preprocessing(tmp_path, monkeypatch):
    # Exercise the production/research contract using a real-shaped source document.
    monkeypatch.setattr(export, "ROOT", tmp_path)
    directory = tmp_path / "head"
    directory.mkdir()
    for name in ("model.json", "weights.npz"):
        (directory / name).write_bytes(b"stub")
    formal = read_manifest(export.MANIFEST)
    changed = copy.deepcopy(formal)
    changed["preprocessing"]["orientation"] = "wrong"
    document = {"space_spec": {"model": "qwen", "dtype": "fp16", "dimension": 512,
                               "native_dimension": 2048, "normalization": "unit-prefix-v1",
                               "model_lock": {"manifest": changed}}}
    monkeypatch.setattr(export, "read_model", lambda *_: (document, np.ones((8, 512)), np.zeros(8)))
    with pytest.raises(ValueError, match="preprocessing/model space"):
        export.source_head(directory)
