"""P4-02C acceptance: isolation of re-execution work, bounded audits, supported recovery.

The card's questions are not "does the outbox drain" (P4-02A answered that) but:

* may one artifact whose bytes no longer verify stop every other event? (it must not, once the
  repair audit has recorded ``RECLAIMED`` - and the event must survive that isolation)
* is there a *production* way back from ``RECLAIMED``, and does it refuse a re-execution that is
  not byte-identical to the published artifact? (``reexecute``)
* can repeated repair audits enlarge an outbox that cannot drain? (they must not - while a real
  lost confirmation must still be scheduled)

Every case drives the production entry (``photo_search.indexing.maintenance`` /
``python -m photo_search.indexing.cli``) and reads its facts back from SQLite and the Lance
generation. The fixture builder is the P4-02A one, so this file adds the recovery dimension to
the same real catalog instead of standing up a second harness.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from photo_search.artifacts.store import ArtifactStore
from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import (
    ARTIFACT_INVALID,
    ARTIFACT_MISSING,
    RUNTIME_CAPABILITY_UNAVAILABLE,
    RUNTIME_RESOLUTION_MISMATCH,
    CatalogError,
)
from photo_search.indexing import cli as index_cli
from photo_search.indexing import maintenance
from photo_search.indexing.store import IndexCoordinator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROBE_PATH = PROJECT_ROOT / "tools" / "diagnostics" / "index_maintenance_probe.py"


def load_probe() -> ModuleType:
    spec = importlib.util.spec_from_file_location("p4_02c_probe", PROBE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = load_probe()
benchmark = probe.benchmark


def build(tmp_path: Path, *, scale: int = 40, events: int = 3, seed: int = 20260924) -> Any:
    """A real fixture plus ``events`` real pending ``UPSERT_VECTOR`` events."""
    fixture = probe.fresh_fixture(
        tmp_path / "work", scale=scale, seed=seed, distribution="clustered"
    )
    probe.grow_library(fixture, events=events, seed=seed + 1, prefix="p4-02c")
    return fixture


def artifact_path(fixture: Any, artifact_id: str) -> Path:
    row = fixture.catalog.read_one(
        "SELECT * FROM artifacts WHERE artifact_id = ?", (str(artifact_id),)
    )
    assert row is not None
    return ArtifactStore.open(fixture.data_root).resolve_uri(str(row["storage_uri"]))


def corrupt_artifact(fixture: Any, artifact_id: str) -> tuple[Path, list[float], bytes]:
    """Break the published bytes; return the path, the re-run vector and the original bytes."""
    path = artifact_path(fixture, artifact_id)
    published = path.read_bytes()
    payload = json.loads(published.decode("utf-8"))
    path.write_bytes(b"these bytes no longer verify against the recorded checksum")
    return path, [float(value) for value in payload["vector"]], published


def lifecycle(fixture: Any, artifact_id: str) -> str:
    row = fixture.catalog.read_one(
        "SELECT lifecycle_state FROM artifacts WHERE artifact_id = ?", (str(artifact_id),)
    )
    assert row is not None
    return str(row["lifecycle_state"])


def outbox_rows(fixture: Any) -> int:
    return int(fixture.catalog.read_one("SELECT COUNT(*) AS total FROM index_outbox")["total"])


def give_the_asset_a_real_source_file(fixture: Any, tmp_path: Path, asset_id: int) -> Path:
    """Point the asset's source root at a real file inside the test's own temporary directory.

    The shared benchmark fixture records synthetic locations (``D:\\synthetic\\...``) with no
    files behind them, and the recovery entry deliberately reads only a ``PRESENT`` location.
    The stand-in encoder never decodes the file, so its content only has to exist.
    """
    location = fixture.catalog.read_one(
        "SELECT source_id, relative_path FROM file_locations WHERE asset_id = ? "
        "ORDER BY location_id LIMIT 1",
        (int(asset_id),),
    )
    assert location is not None
    root = tmp_path / "library"
    with fixture.catalog.write() as connection:
        connection.execute(
            "UPDATE source_roots SET root_path = ? WHERE source_id = ?",
            (str(root), int(location["source_id"])),
        )
    path = root / str(location["relative_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"synthetic source image (never decoded by the stand-in encoder)")
    return path


class StubImageEncoder:
    """The deterministic ADR-0003 image half: source file name -> the re-executed vector."""

    def __init__(self, vectors: dict[str, Any]) -> None:
        self.vectors = {
            str(name): [float(value) for value in vector] for name, vector in vectors.items()
        }
        self.calls: list[str] = []

    def encode_image(self, path: str) -> list[float]:
        name = Path(path).name
        self.calls.append(name)
        return list(self.vectors[name])


def open_like_a_second_process(fixture: Any) -> Catalog:
    return Catalog.open(data_root=fixture.data_root)


@contextmanager
def engine_delay(ms: int) -> Any:
    with probe.engine_delay(ms):
        yield


# ------------------------------------------------------------------ A2: isolation + recovery
def test_a_reclaimed_artifact_is_isolated_and_the_same_event_recovers(tmp_path: Path) -> None:
    fixture = build(tmp_path, events=3)
    try:
        target, other = "p4-02c-42", "p4-02c-41"
        path, vector, published = corrupt_artifact(fixture, target)

        # 1. The corrupt artifact is still fail-closed: the drain refuses and makes no progress.
        with pytest.raises(CatalogError) as refused:
            maintenance.drain_semantic_outbox(fixture.catalog)
        assert refused.value.code == ARTIFACT_MISSING
        assert probe.outbox_states(fixture) == {"PENDING": 3}

        # 2. The audit reclaims it, and from then on it is isolated - visible, not dropped.
        isolation = maintenance.drain_semantic_outbox(fixture.catalog, repair=True)
        assert isolation["repair"] == {"scheduled": 0, "requires_reexecution": 1}
        assert isolation["applied"] == 2
        assert isolation["confirmed_delta"] == 2
        assert isolation["index"]["after"]["pending"] == 1
        assert isolation["awaiting_reexecution"]["count"] == 1
        sample = isolation["awaiting_reexecution"]["sample"][0]
        assert sample["artifact_id"] == target
        assert sample["asset_id"] == 42
        assert sample["last_error_code"] == ARTIFACT_MISSING
        assert isolation["awaiting_reexecution"]["sample_truncated"] is False
        assert probe.outbox_states(fixture) == {"CONFIRMED": 2, "PENDING": 1}
        assert probe.semantic_ready(fixture) == 42
        assert probe._row_for(fixture, 42) is None
        assert str(probe._entry_revision(fixture, 42)["state"]) == "PENDING_REPAIR"
        assert not path.exists()

        # 3. The supported recovery republishes the *identical* bytes...
        source = give_the_asset_a_real_source_file(fixture, tmp_path, 42)
        encoder = StubImageEncoder({source.name: vector})
        digest_before = probe.state_digest(fixture)["sha256"]
        recovery = maintenance.reexecute_reclaimed_artifacts(fixture.catalog, encoder=encoder)
        assert recovery["targets"] == 1
        assert recovery["republished"] == 1
        assert recovery["refused"] == recovery["skipped"] == 0
        assert recovery["results"][0]["outcome"] == "republished"
        assert recovery["results"][0]["artifact_id"] == target
        assert encoder.calls == [source.name]
        assert path.read_bytes() == published
        assert lifecycle(fixture, target) == "PUBLISHED"
        # ...and republishing alone projects nothing: the confirmation is still the drain's job.
        assert probe.state_digest(fixture)["sha256"] == digest_before
        assert probe._row_for(fixture, 42) is None

        # 4. The same event confirms, so no second event had to be invented for it.
        final = maintenance.drain_semantic_outbox(fixture.catalog)
        assert final["applied"] == 1
        assert final["awaiting_reexecution"] == {"count": 0, "sample": [], "sample_truncated": False}
        assert probe.outbox_states(fixture) == {"CONFIRMED": 3}
        assert probe.semantic_ready(fixture) == 43
        row = probe._row_for(fixture, 42) or {}
        entry = probe._entry_revision(fixture, 42)
        assert str(entry["state"]) == "CONFIRMED"
        assert int(row["projection_revision"]) == int(entry["confirmation_revision"]) == 0
        assert probe._asset_distance(fixture, query=vector, asset_id=42) <= 1e-6
        assert int(
            fixture.catalog.read_one(
                "SELECT COUNT(*) AS total FROM index_outbox WHERE artifact_id = ?", (target,)
            )["total"]
        ) == 1
        assert str(probe._entry_revision(fixture, 41)["state"]) == "CONFIRMED"
        assert probe._row_for(fixture, 41) is not None
        assert other == "p4-02c-41"
    finally:
        fixture.catalog.close()


def test_a_reexecution_that_is_not_byte_identical_is_refused(tmp_path: Path) -> None:
    fixture = build(tmp_path, events=2)
    try:
        target = "p4-02c-42"
        path, vector, _ = corrupt_artifact(fixture, target)
        with pytest.raises(CatalogError):
            maintenance.drain_semantic_outbox(fixture.catalog)
        maintenance.drain_semantic_outbox(fixture.catalog, repair=True)
        source = give_the_asset_a_real_source_file(fixture, tmp_path, 42)
        before = probe.state_digest(fixture)["sha256"]

        drifting = maintenance.reexecute_reclaimed_artifacts(
            fixture.catalog,
            encoder=StubImageEncoder({source.name: [value + 0.01 for value in vector]}),
        )
        assert drifting["refused"] == 1 and drifting["republished"] == 0
        assert drifting["results"][0]["code"] == ARTIFACT_INVALID

        short = maintenance.reexecute_reclaimed_artifacts(
            fixture.catalog, encoder=StubImageEncoder({source.name: vector[:-1]})
        )
        assert short["results"][0]["code"] == RUNTIME_RESOLUTION_MISMATCH

        broken = maintenance.reexecute_reclaimed_artifacts(
            fixture.catalog,
            encoder=StubImageEncoder({source.name: [float("nan"), *vector[1:]]}),
        )
        assert broken["results"][0]["code"] == RUNTIME_RESOLUTION_MISMATCH

        # Nothing was republished and nothing reached the index: the artifact is still waiting.
        assert not path.exists()
        assert lifecycle(fixture, target) == "RECLAIMED"
        assert probe.state_digest(fixture)["sha256"] == before
        assert probe._row_for(fixture, 42) is None
        assert probe.outbox_states(fixture) == {"CONFIRMED": 1, "PENDING": 1}
        assert maintenance.drain_semantic_outbox(fixture.catalog)["applied"] == 0
    finally:
        fixture.catalog.close()


def test_a_target_without_a_usable_source_is_skipped_instead_of_guessed(tmp_path: Path) -> None:
    """The fixture's synthetic locations are exactly the "no real file" case."""
    fixture = build(tmp_path, events=2)
    try:
        target = "p4-02c-42"
        corrupt_artifact(fixture, target)
        with pytest.raises(CatalogError):
            maintenance.drain_semantic_outbox(fixture.catalog)
        maintenance.drain_semantic_outbox(fixture.catalog, repair=True)
        before = probe.state_digest(fixture)["sha256"]

        report = maintenance.reexecute_reclaimed_artifacts(
            fixture.catalog, encoder=StubImageEncoder({})
        )
        assert report["targets"] == 1
        assert report["skipped"] == 1 and report["republished"] == 0
        assert report["results"][0]["reason"] == "SOURCE_FILE_MISSING"
        assert report["results"][0]["asset_id"] == 42
        assert probe.state_digest(fixture)["sha256"] == before
        assert lifecycle(fixture, target) == "RECLAIMED"
    finally:
        fixture.catalog.close()


# ------------------------------------------------------------------------- A3: audit growth
def test_repeated_audits_do_not_grow_the_backlog_but_a_real_loss_still_schedules(
    tmp_path: Path,
) -> None:
    fixture = build(tmp_path, events=3)
    try:
        corrupt_artifact(fixture, "p4-02c-42")
        with pytest.raises(CatalogError):
            maintenance.drain_semantic_outbox(fixture.catalog)
        first = maintenance.drain_semantic_outbox(fixture.catalog, repair=True)
        assert first["repair"] == {"scheduled": 0, "requires_reexecution": 1}
        assert outbox_rows(fixture) == 3

        for _ in range(3):
            report = maintenance.drain_semantic_outbox(fixture.catalog, repair=True)
            assert report["applied"] == 0
            assert report["repair"] == {"scheduled": 0, "requires_reexecution": 1}
            assert report["index"]["after"]["pending"] == 1
            assert outbox_rows(fixture) == 3
            assert probe.outbox_states(fixture) == {"CONFIRMED": 2, "PENDING": 1}

        # Idempotent is not blind: a confirmation that really disappeared is scheduled again.
        with fixture.catalog.write() as connection:
            connection.execute(
                "DELETE FROM index_entries WHERE space_id = ? AND asset_id = ? AND generation_id = ?",
                (benchmark.SPACE_ID, 41, benchmark.GENERATION_ID),
            )
        repaired = maintenance.drain_semantic_outbox(fixture.catalog, repair=True)
        assert repaired["repair"] == {"scheduled": 1, "requires_reexecution": 1}
        assert repaired["applied"] == 1
        assert outbox_rows(fixture) == 4
        entry = probe._entry_revision(fixture, 41)
        assert str(entry["state"]) == "CONFIRMED"
        assert int(entry["confirmation_revision"]) == probe._max_event_revision(fixture, 41) == 1
        assert probe._row_for(fixture, 41) is not None
        # The stuck artifact is still exactly one unconfirmed event, not two.
        assert probe.outbox_states(fixture) == {"CONFIRMED": 3, "PENDING": 1}
    finally:
        fixture.catalog.close()


# ------------------------------------------------------------------------- the operator CLI
def test_the_reexecute_cli_lists_targets_cheaply_and_refuses_a_missing_model(
    tmp_path: Path, capsys: Any
) -> None:
    fixture = build(tmp_path, events=2)
    try:
        root = str(fixture.data_root)
        corrupt_artifact(fixture, "p4-02c-42")
        with pytest.raises(CatalogError):
            maintenance.drain_semantic_outbox(fixture.catalog)
        maintenance.drain_semantic_outbox(fixture.catalog, repair=True)
        give_the_asset_a_real_source_file(fixture, tmp_path, 42)
        digest = probe.state_digest(fixture)["sha256"]

        # A dry run needs no model at all and answers from the catalog alone.
        assert index_cli.main(["reexecute", "--data-root", root, "--dry-run", "--json"]) == 0
        dry = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert dry["status"] == "ok" and dry["dry_run"] is True
        assert dry["targets"] == 1 and dry["planned"] == 1 and dry["republished"] == 0
        assert dry["results"][0]["outcome"] == "planned"
        assert dry["results"][0]["artifact_id"] == "p4-02c-42"
        assert probe.state_digest(fixture)["sha256"] == digest

        # A real re-execution must name its model: no defaults, no silent CPU fallback.
        assert index_cli.main(["reexecute", "--data-root", root]) == 2
        capsys.readouterr()
        assert index_cli.main(["reexecute", "--data-root", root, "--device", "cpu"]) == 2
        capsys.readouterr()
        assert index_cli.main(
            ["reexecute", "--data-root", root, "--model-dir", str(tmp_path / "m"), "--device", "cpu"]
        ) == 2
        capsys.readouterr()

        missing = tmp_path / "no-model-here"
        assert index_cli.main(
            [
                "reexecute",
                "--data-root",
                root,
                "--model-dir",
                str(missing),
                "--manifest",
                str(missing / "manifest.json"),
                "--device",
                "cpu",
                "--json",
            ]
        ) == 1
        payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert payload["status"] == "error"
        assert payload["error"]["code"] == RUNTIME_CAPABILITY_UNAVAILABLE
        assert payload["error"]["details"]["model_dir"] == str(missing)
        # The refusal happened before any catalog work: the target is untouched.
        assert lifecycle(fixture, "p4-02c-42") == "RECLAIMED"
        assert probe.state_digest(fixture)["sha256"] == digest
    finally:
        fixture.catalog.close()


# ------------------------------------------------- P4-01C discipline under the new predicate
def test_the_new_claim_predicate_keeps_the_p4_01c_discipline(tmp_path: Path) -> None:
    """A newer revision still wins, and the crash window still replays through the entry."""
    fixture = build(tmp_path, events=3)
    try:
        assert maintenance.drain_semantic_outbox(fixture.catalog)["applied"] == 3
        asset_id = 1
        older = fixture.matrix[asset_id - 1]
        newer = benchmark.synthetic_vectors(1, seed=909, distribution="clustered")[0]
        assert probe._asset_distance(fixture, query=older, asset_id=asset_id) <= 1e-6

        benchmark.bump_filter_revision(fixture, cause="CONTENT_CHANGED")
        benchmark.publish_embedding(
            fixture,
            artifact_id="p4-02c-newest",
            asset_id=asset_id,
            vector=newer,
            now=benchmark.now_ms(),
            input_revision="c" * 64,
        )
        # The documented crash window: the engine write lands, the confirmation does not.
        with pytest.raises(RuntimeError):
            IndexCoordinator(fixture.catalog).process_next(
                now_ms=benchmark.now_ms(), failpoint="after_lance"
            )
        report = maintenance.drain_semantic_outbox(fixture.catalog)
        assert report["applied"] == 1
        assert report["awaiting_reexecution"]["count"] == 0

        row = probe._row_for(fixture, asset_id) or {}
        entry = probe._entry_revision(fixture, asset_id)
        newest_revision = probe._max_event_revision(fixture, asset_id)
        assert int(row["projection_revision"]) == newest_revision == 1
        assert int(entry["confirmation_revision"]) == newest_revision
        assert str(entry["state"]) == "CONFIRMED"
        assert probe._asset_distance(fixture, query=newer, asset_id=asset_id) <= 1e-6
        distance_old = probe._asset_distance(fixture, query=older, asset_id=asset_id)
        assert distance_old is not None and distance_old > 0.0
        assert probe.outbox_states(fixture) == {"CONFIRMED": 4}
    finally:
        fixture.catalog.close()


def test_two_maintainers_confirm_every_event_exactly_once_under_the_new_predicate(
    tmp_path: Path,
) -> None:
    events = 10
    fixture = build(tmp_path, events=events)
    try:
        results: list[dict[str, Any] | None] = [None, None]
        failures: list[str | None] = [None, None]

        def maintainer(index: int) -> None:
            handle = open_like_a_second_process(fixture)
            try:
                results[index] = maintenance.drain_semantic_outbox(
                    handle,
                    limit=events * 2,
                    batch_size=2,
                    retry_limit=5,
                    retry_backoff_ms=20,
                )
            except BaseException as error:  # noqa: BLE001 - reported, not swallowed
                failures[index] = f"{type(error).__name__}: {error}"
            finally:
                handle.close()

        with engine_delay(25):
            first = threading.Thread(target=maintainer, args=(0,))
            first.start()
            assert probe._wait_for_confirmed_events(fixture, minimum=1, timeout_s=30)
            second = threading.Thread(target=maintainer, args=(1,))
            second.start()
            for thread in (first, second):
                thread.join(timeout=300)
        assert failures == [None, None]

        leftover = maintenance.drain_semantic_outbox(fixture.catalog)
        applied = [int(item["applied"]) for item in results if item is not None]
        assert sum(applied) + int(leftover["applied"]) == events
        assert leftover["applied"] == 0
        contender = results[1]
        assert contender is not None
        # The same criterion the P4-02A review recommended: contended, or provably serialised.
        assert probe._contended(contender, interleave_delay_ms=25)
        state = probe.state_digest(fixture)
        assert state["outbox"] == {"CONFIRMED": events}
        assert state["lance_rows"] == state["lance_unique_assets"] == 40 + events
        assert state["entries_by_state"] == {"CONFIRMED": 40 + events}
        assert state["semantic_ready"] == 40 + events
    finally:
        fixture.catalog.close()
