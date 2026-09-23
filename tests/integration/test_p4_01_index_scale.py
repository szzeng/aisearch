"""P4-01 scale-benchmark closure: fixture fidelity, maintenance injections, filters.

The benchmark tool (``tools/diagnostics/ann_scale_benchmark.py``) is imported as a module
so these tests drive the *same* fixture builder and the *same* injection scenarios the
reported numbers come from - a scale report whose fixture cannot be rebuilt is not
evidence. Everything here runs at a few hundred assets; the 100 000/300 000 measurement
itself is the tool's job and is recorded in ``docs/reports/P4-01-ann-scale-benchmark.md``.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from photo_search.indexing.store import IndexCoordinator, LanceVectorIndex
from photo_search.search import retrieval

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOL_PATH = PROJECT_ROOT / "tools" / "diagnostics" / "ann_scale_benchmark.py"


def load_tool() -> ModuleType:
    """Import the benchmark script as a module (it is a script, not a package)."""
    spec = importlib.util.spec_from_file_location("p4_01_benchmark", TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # ``dataclass`` resolves ``cls.__module__`` through ``sys.modules``.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


benchmark = load_tool()


def build(tmp_path: Path, scale: int, *, seed: int = 20260923) -> Any:
    return benchmark.build_fixture(
        tmp_path / f"scale-{scale}",
        scale=scale,
        seed=seed,
        distribution="clustered",
    )


def test_fixture_is_protocol_complete_and_reproducible(tmp_path: Path) -> None:
    fixture = build(tmp_path, 400)
    try:
        rows = benchmark._row_counts(fixture.catalog)
        assert rows["assets"] == 400
        assert rows["file_locations"] == 400
        assert rows["asset_metadata"] == 400
        assert rows["search_documents"] == 400
        assert rows["search_documents_fts"] == 400
        assert rows["index_entries"] == 400
        assert rows["index_outbox"] == 0
        assert rows["faces"] > 0 and rows["face_person_links"] == rows["faces"]

        # Every embedding artifact row is backed by a real payload file whose bytes match
        # the recorded checksum, so `ArtifactStore.read` accepts the fixture.
        artifact = fixture.catalog.read_one(
            "SELECT * FROM artifacts ORDER BY artifact_id LIMIT 1"
        )
        store = benchmark.ArtifactStore.open(fixture.data_root)
        payload = store.read(artifact)
        assert hashlib.sha256(payload).hexdigest() == str(artifact["checksum_sha256"])
        assert json.loads(payload.decode("utf-8"))["vector"]

        index = LanceVectorIndex(fixture.vectors_dir)
        assert index.count(generation_id=benchmark.GENERATION_ID, dimension=benchmark.DIM, space_id=benchmark.SPACE_ID) == 400
        coordinator = IndexCoordinator(fixture.catalog)
        assert coordinator.semantic_ready(
            space_id=benchmark.SPACE_ID, asset_id=1, generation_id=benchmark.GENERATION_ID
        )

        # The same seed rebuilds the same vectors, so a reported recall is reproducible.
        other = benchmark.synthetic_vectors(
            400, seed=20260923, distribution="clustered"
        )
        assert (other == fixture.matrix).all()
    finally:
        fixture.catalog.close()


def test_production_walk_is_exact_and_authority_bounded(tmp_path: Path) -> None:
    fixture = build(tmp_path, 400)
    try:
        scope = retrieval.SearchScope.build()
        ids = fixture.scope_ids(scope)
        queries = benchmark.synthetic_vectors(3, seed=11, distribution="clustered")
        index = LanceVectorIndex(fixture.vectors_dir)
        walk = benchmark.production_walk(
            fixture, scope=scope, queries=queries, limit=200, index=index
        )
        exact = benchmark.exact_top_k(fixture.matrix, ids, queries, 20)
        assert benchmark.walk_recall(exact, walk["ranked"], k=20)["recall"] == 1.0
        assert walk["blocks"] == [1]
        assert walk["scanned_assets"] == [400]
        for ranked in walk["ranked"]:
            assert set(ranked) <= set(ids)
    finally:
        fixture.catalog.close()


def test_filter_tiers_agree_with_the_sql_authority(tmp_path: Path) -> None:
    fixture = build(tmp_path, 600)
    try:
        index = LanceVectorIndex(fixture.vectors_dir)
        queries = benchmark.synthetic_vectors(1, seed=5, distribution="clustered")
        for tier in benchmark.TIERS:
            place_ids = fixture.city_ids[: int(tier["cities"])] if tier["cities"] else ()
            capture = (
                None
                if tier["share"] is None
                else benchmark._capture_range_for_share(tier["share"], scale=fixture.scale)
            )
            scope = retrieval.SearchScope.build(
                source_id=tier["source_id"],
                place_ids=place_ids,
                person_ids=tier["persons"],
                person_match=tier["match"],
                capture=capture,
            )
            ids = fixture.scope_ids(scope)
            assert ids, f"tier {tier['label']} must not be empty at this scale"
            authority = retrieval.authoritative_page(fixture.catalog, scope)
            assert int(authority["total"]) == len(ids)
            walk = benchmark.production_walk(
                fixture, scope=scope, queries=queries, limit=200, index=index
            )
            assert set(walk["ranked"][0]) <= set(ids)
            assert walk["blocks"] == [
                benchmark.math.ceil(len(ids) / int(retrieval.SCOPE_BLOCK_SIZE))
            ]

        combination = benchmark._combination_check(
            fixture,
            args=benchmark.parse_args([]),
            index=index,
            seed=7,
        )
        assert combination["sql_agrees"] is True
        assert combination["walk_hits_outside_authority"] == []
        assert combination["fts_hits_outside_authority"] == []
        assert combination["projection_lag"]["in_topup"] is True
        assert combination["projection_lag"]["returned_as_hit"] is False
    finally:
        fixture.catalog.close()


def test_maintenance_injections_hold_their_invariants(tmp_path: Path) -> None:
    args = benchmark.parse_args(["--injection-assets", "240", "--work-dir", str(tmp_path)])
    results = benchmark.maintenance_injections(tmp_path, args=args)

    restart = results["interrupt_restart"]
    assert restart["crashed"] is True
    assert restart["no_logical_duplicates"] is True
    assert restart["rows_after"] == restart["unique_assets"]
    assert restart["drained"] >= 1

    replay = results["unconfirmed_replay"]
    assert replay["first_replay"] == 1 and replay["second_replay"] == 1
    assert replay["rows_for_asset_3"] == 1
    assert replay["outbox"] == {"CONFIRMED": 3}

    changed = results["content_change"]
    assert changed["old_vector_hit_before"] is True
    assert changed["old_vector_hit_during"] is False
    assert changed["entry_state_during"] == "PENDING_REPAIR"
    assert changed["hit_after_replay"] is True

    offline = results["offline_location"]
    assert offline["asset_2_in_present_scope"] is False
    assert offline["asset_2_returned"] is False
    assert offline["vector_row_still_present"] is True

    audit = results["repair_audit"]
    assert audit["fixture_rows"] == 240
    assert audit["has_call_ms"] > 0
    # The audit walks every embedding artifact through ``rows()``; at fixture scale it
    # completes, and the quadratic shape is what makes it unusable at 300 000 rows.
    assert audit["audit_ms"] >= audit["rows_call_ms"]


def test_scale_mode_writes_machine_readable_json(tmp_path: Path) -> None:
    output = tmp_path / "scale.json"
    work = tmp_path / "work dir"
    exit_code = benchmark.main(
        [
            "--mode",
            "scale",
            "--scale",
            "400",
            "--work-dir",
            str(work),
            "--output",
            str(output),
            "--queries",
            "6",
            "--boundary-queries",
            "2",
            "--walk-queries",
            "1",
            "--filter-walk-queries",
            "1",
            "--upsert-samples",
            "1",
            "--maintenance-assets",
            "2",
            "--skip-ann-variants",
        ]
    )
    assert exit_code == 0
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["status"] == "ok"
    entry = record["scales"][0]
    assert entry["scale"] == 400
    assert entry["fixture"]["rows"]["assets"] == 400
    assert entry["retrieval"]["engine_scan_cross_check"]["all_equal"] is True
    assert (
        entry["retrieval"]["paths"]["production_walk_unindexed"]["recall_at_20"]["recall"]
        == 1.0
    )
    assert entry["retrieval"]["paths"]["engine_global_unindexed"]["timings"]["count"] > 0
    assert entry["maintenance"]["replay"]["semantic_ready"] == 2
    assert record["peak_working_set_bytes"] > 0


def test_ann_index_needs_a_trainable_fixture(tmp_path: Path) -> None:
    """A tiny fixture reports the ANN stage as skipped instead of failing the run."""
    fixture = build(tmp_path, 120)
    try:
        assert benchmark.build_ann_index(fixture, partitions=2, sub_vectors=64) is None
    finally:
        fixture.catalog.close()
    if benchmark.MIN_ANN_ROWS != 256:  # pragma: no cover - documents the pinned bound
        pytest.fail("the PQ training bound changed; re-check the pinned lancedb build")
