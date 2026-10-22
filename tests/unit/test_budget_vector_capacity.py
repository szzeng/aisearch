"""Correctness gates for the isolated capacity experiment, without large fixtures."""
from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest

from photo_search.indexing import store
from tools.evaluation import budget_vector_capacity as capacity


def test_prefix_datasets_share_same_native_draw_and_real_heads():
    heads = np.random.default_rng(17).standard_normal((3, 2048), dtype=np.float32)
    native = np.concatenate([b for _, b in capacity.synthetic_batches(heads, 12, 2048, batch=5)])
    short = np.concatenate([b for _, b in capacity.synthetic_batches(heads, 12, 512, batch=5)])
    np.testing.assert_allclose(short, capacity.prefix(native, 512), atol=2e-7)
    np.testing.assert_allclose(native[:3], capacity.prefix(heads, 2048), atol=2e-7)
    np.testing.assert_allclose(np.linalg.norm(native, axis=1), 1.0, atol=2e-7)
    again = np.concatenate([b for _, b in capacity.synthetic_batches(heads, 12, 2048, batch=5)])
    np.testing.assert_array_equal(native, again)


@pytest.mark.parametrize("dimension,rows,batch", [(256, 12, 5), (512, 2, 5), (512, 12, 0)])
def test_invalid_synthetic_request_rejected(dimension, rows, batch):
    heads = np.ones((3, 2048), dtype=np.float32)
    with pytest.raises(ValueError):
        list(capacity.synthetic_batches(heads, rows, dimension, batch=batch))


def test_membership_identity_and_nonwidening(tmp_path):
    path = tmp_path / "shenzhen-ids.npy"
    np.save(path, np.array([1, 3, 7], dtype=np.int64))
    (tmp_path / "fixture.json").write_text(json.dumps({
        "rows": 10, "files": {path.name: capacity.digest(path)}
    }), encoding="utf-8")
    assert capacity.scope_ids(tmp_path, "shenzhen").tolist() == [1, 3, 7]
    np.save(path, np.array([1, 3, 8], dtype=np.int64))
    with pytest.raises(ValueError, match="changed"):
        capacity.scope_ids(tmp_path, "shenzhen")


def test_inspection_refuses_foreign_member_space_and_wrong_distances():
    matrix = np.eye(3, dtype=np.float32)
    query = matrix[0]
    good = {"asset_id": 1, "generation_id": capacity.GENERATION,
            "space_id": capacity.SPACE, "_distance": 0.0}
    assert capacity.inspect_rows([good], [1], matrix, query)[0] == [1]
    for change in ({"asset_id": 2}, {"generation_id": "old"}, {"space_id": "old"},
                   {"_distance": float("nan")}, {"_distance": 0.2}):
        with pytest.raises(ValueError):
            capacity.inspect_rows([{**good, **change}], [1], matrix, query)
    with pytest.raises(ValueError, match="duplicate"):
        capacity.inspect_rows([good, good], [1], matrix, query)


def test_exact_engine_tie_boundary_and_prefilter(tmp_path):
    import lancedb
    import pyarrow as pa

    schema = store.LanceVectorIndex._schema(pa, 4)
    schema = schema.append(pa.field("shenzhen", pa.bool_(), nullable=False))
    schema = schema.append(pa.field("sparse", pa.bool_(), nullable=False))
    rows = [{"record_id": str(i), "asset_id": i, "generation_id": capacity.GENERATION,
             "space_id": capacity.SPACE, "artifact_revision": "test", "projection_revision": 0,
             "vector": [1.0, 0.0, 0.0, 0.0], "source_ids": [], "visible": True,
             "shenzhen": i % 2 == 0, "sparse": False} for i in reversed(range(1, 12))]
    table = lancedb.connect(str(tmp_path / "vectors")).create_table("test", rows, schema=schema)
    found, fallback = capacity.exact_once(table, [0.0] * 4, "global", 3, 11)
    assert fallback and [r["asset_id"] for r in found] == [1, 2, 3]
    found, fallback = capacity.exact_once(table, [0.0] * 4, "shenzhen", 3, 5)
    assert fallback and [r["asset_id"] for r in found] == [2, 4, 6]
    assert capacity.exact_once(table, [0.0] * 4, "sparse", 3, 0) == ([], False)


def test_output_root_cannot_escape_project_temp():
    with pytest.raises(ValueError):
        capacity.local_root(capacity.REPO / "src")


def ann_reference(snapshot):
    return {"status": "PASS", "top_k": 5, "query_indices": [0, 3, 5],
            "config": dict(capacity.ANN_CONFIG), "snapshot": snapshot}


@pytest.mark.parametrize("field", ["fixture_sha256", "exact_sha256", "oracle_sha256",
                                    "table_version", "table_rows", "index"])
def test_ann_diagnostic_rejects_source_or_index_changes(field):
    snapshot = {key: f"original-{key}" for key in ["fixture_sha256", "exact_sha256",
                                                  "oracle_sha256", "table_version",
                                                  "table_rows", "index"]}
    prior = ann_reference(snapshot)
    capacity.validate_ann_origin(prior, dict(snapshot), top_k=5, query_indices=[0, 3, 5])
    with pytest.raises(ValueError, match="identity changed"):
        capacity.validate_ann_origin(prior, {**snapshot, field: "changed"}, top_k=5,
                                     query_indices=[0, 3, 5])


def test_ann_diagnostic_rejects_legacy_or_changed_plan():
    prior = ann_reference({"fixed": True})
    for changes in ({"status": "FAIL"}, {"top_k": 1}, {"query_indices": [1]},
                    {"config": {**capacity.ANN_CONFIG, "metric": "cosine"}}, {"snapshot": None}):
        with pytest.raises(ValueError):
            capacity.validate_ann_origin({**prior, **changes}, {"fixed": True}, top_k=5,
                                         query_indices=[0, 3, 5])


def test_ann_snapshot_checks_native_sdk_metadata_and_coverage(tmp_path):
    for name in ("fixture.json", "exact.json", "exact-oracle.npz"):
        (tmp_path / name).write_bytes(name.encode())
    index = SimpleNamespace(name="vector_idx", columns=["vector"], index_type="IvfPq",
                            index_uuid="unique", index_details={"compression": {"type": "pq"}})
    stats = SimpleNamespace(index_type="IVF_PQ", distance_type="l2", num_indexed_rows=100,
                            num_unindexed_rows=0)
    table = SimpleNamespace(version=4, list_indices=lambda: [index], count_rows=lambda: 100,
                            index_stats=lambda name: stats)
    snapshot = capacity.ann_snapshot(tmp_path, table, {"rows": 100})
    assert snapshot["index"]["uuid"] == "unique" and snapshot["table_version"] == 4
    assert snapshot["exact_sha256"] == capacity.digest(tmp_path / "exact.json")
    stats.num_unindexed_rows = 1
    with pytest.raises(ValueError, match="coverage"):
        capacity.ann_snapshot(tmp_path, table, {"rows": 100})
    stats.num_unindexed_rows = 0
    index.index_type = "IvfFlat"
    with pytest.raises(ValueError, match="type"):
        capacity.ann_snapshot(tmp_path, table, {"rows": 100})
    table.list_indices = lambda: []
    with pytest.raises(ValueError, match="missing"):
        capacity.ann_snapshot(tmp_path, table, {"rows": 100})
