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

import hashlib
import importlib.util
import json
import subprocess
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
    INDEX_SUBMISSION_FAILED,
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


def test_one_generation_with_two_artifacts_keeps_the_audit_bounded(tmp_path: Path) -> None:
    """P4-02C review F1: a content change leaves a second artifact on the same generation.

    A projection is per ``(space, asset, generation)``, so an audit must not allocate one event
    per artifact of that triple: with two published artifacts on one generation the delivered fix
    still grew the outbox on every pass (3 -> 5 -> 7). The decisive fact is the triple, not the
    artifact count.
    """
    fixture = build(tmp_path, events=2)
    try:
        asset_id = int(fixture.scale) + 1
        stale = json.loads(
            artifact_path(fixture, f"p4-02c-{asset_id}").read_text(encoding="utf-8")
        )["vector"]
        newer = benchmark.synthetic_vectors(1, seed=20260924, distribution="clustered")[0]
        benchmark.bump_filter_revision(fixture, cause="CONTENT_CHANGED")
        benchmark.publish_embedding(
            fixture,
            artifact_id=f"p4-02c-{asset_id}-changed",
            asset_id=asset_id,
            vector=newer,
            now=benchmark.now_ms(),
            input_revision=hashlib.sha256(b"p4-02c-content-changed").hexdigest(),
        )
        # The triple really owns two published artifacts and both events target one generation.
        artifacts = fixture.catalog.read_all(
            "SELECT a.lifecycle_state FROM artifacts AS a JOIN jobs AS j ON j.job_id = a.job_id "
            "WHERE j.subject_id = ? ORDER BY a.artifact_id",
            (str(asset_id),),
        )
        assert [str(row["lifecycle_state"]) for row in artifacts] == ["PUBLISHED", "PUBLISHED"]
        generations = {
            str(row["generation_id"])
            for row in fixture.catalog.read_all(
                "SELECT generation_id FROM index_outbox WHERE asset_id = ?", (asset_id,)
            )
        }
        assert generations == {benchmark.GENERATION_ID}
        before = outbox_rows(fixture)
        assert before == 3

        # Repeated audits over that unchanged state allocate nothing at all.
        for _ in range(3):
            audit = IndexCoordinator(fixture.catalog).audit_and_schedule_repairs(
                now_ms=benchmark.now_ms()
            )
            assert audit == {"scheduled": [], "requires_reexecution": []}
            assert outbox_rows(fixture) == before
            assert probe.outbox_states(fixture) == {"PENDING": before}

        # The drain lands the newest artifact's vector and nothing else.
        drained = maintenance.drain_semantic_outbox(fixture.catalog)
        assert drained["applied"] == before
        assert probe.outbox_states(fixture) == {"CONFIRMED": before}
        entry = probe._entry_revision(fixture, asset_id)
        row = probe._row_for(fixture, asset_id) or {}
        assert str(entry["state"]) == "CONFIRMED"
        assert int(entry["confirmation_revision"]) == int(row["projection_revision"]) == 1
        assert probe._asset_distance(fixture, query=newer, asset_id=asset_id) <= 1e-6
        # The stale artifact was superseded, so it never reached the engine.
        assert probe._asset_distance(fixture, query=stale, asset_id=asset_id) > 0.0
        assert str(
            fixture.catalog.read_one(
                "SELECT state FROM index_outbox WHERE artifact_id = ?", (f"p4-02c-{asset_id}",)
            )["state"]
        ) == "CONFIRMED"

        # A confirmation that really disappeared is still repaired on that same triple, once.
        with fixture.catalog.write() as connection:
            connection.execute(
                "DELETE FROM index_entries WHERE space_id = ? AND asset_id = ? AND generation_id = ?",
                (benchmark.SPACE_ID, asset_id, benchmark.GENERATION_ID),
            )
        repaired = maintenance.drain_semantic_outbox(fixture.catalog, repair=True)
        assert repaired["repair"]["scheduled"] == 1
        assert outbox_rows(fixture) == before + 1
        entry = probe._entry_revision(fixture, asset_id)
        assert str(entry["state"]) == "CONFIRMED"
        assert int(entry["confirmation_revision"]) == probe._max_event_revision(fixture, asset_id)
        assert probe._asset_distance(fixture, query=newer, asset_id=asset_id) <= 1e-6
    finally:
        fixture.catalog.close()


def test_a_stuck_drain_does_not_grow_through_repeated_repairs(tmp_path: Path) -> None:
    """P4-02C review F1 (stuck variant): the audit must not amplify a backlog that cannot drain.

    One event's payload no longer verifies, which is the fail-closed guard
    ``_validate_event_payload`` enforces and the repair audit does not repair: the drain refuses
    on every attempt, so the state is unchanged between passes except for what the audit itself
    writes. With a second artifact on the same generation that used to be two more rows per pass.
    """
    fixture = build(tmp_path, events=2)
    try:
        asset_id = int(fixture.scale) + 1
        benchmark.bump_filter_revision(fixture, cause="CONTENT_CHANGED")
        benchmark.publish_embedding(
            fixture,
            artifact_id=f"p4-02c-{asset_id}-changed",
            asset_id=asset_id,
            vector=benchmark.synthetic_vectors(1, seed=4242, distribution="clustered")[0],
            now=benchmark.now_ms(),
            input_revision=hashlib.sha256(b"p4-02c-stuck-changed").hexdigest(),
        )
        oldest = fixture.catalog.read_all(
            "SELECT event_id FROM index_outbox ORDER BY created_at, event_id LIMIT 1"
        )[0]
        with fixture.catalog.write() as connection:
            connection.execute(
                "UPDATE index_outbox SET payload_checksum = ? WHERE event_id = ?",
                ("0" * 64, str(oldest["event_id"])),
            )
        before = outbox_rows(fixture)
        assert before == 3

        for _ in range(3):
            with pytest.raises(CatalogError) as refused:
                maintenance.drain_semantic_outbox(fixture.catalog, repair=True)
            assert refused.value.code == INDEX_SUBMISSION_FAILED
            assert outbox_rows(fixture) == before
            assert probe.outbox_states(fixture) == {"PENDING": before}
    finally:
        fixture.catalog.close()


def test_a_reclaimed_newest_artifact_outranks_the_older_one(tmp_path: Path) -> None:
    """Fix-surface review F3: never project an older artifact over one waiting for re-execution.

    Two artifacts share one generation. When the *newest* one is the artifact awaiting
    re-execution, allocating a higher revision for the older one makes the artifact the operator
    just republished come back superseded: the stale vector stays reachable through search while
    the outbox, the entry and the audit all read "confirmed".
    """
    fixture = build(tmp_path, events=1)
    try:
        asset_id = int(fixture.scale) + 1
        stale_artifact = f"p4-02c-{asset_id}"
        newest_artifact = f"{stale_artifact}-changed"
        stale = json.loads(
            artifact_path(fixture, stale_artifact).read_text(encoding="utf-8")
        )["vector"]
        newer = benchmark.synthetic_vectors(1, seed=909, distribution="clustered")[0]
        benchmark.bump_filter_revision(fixture, cause="CONTENT_CHANGED")
        benchmark.publish_embedding(
            fixture,
            artifact_id=newest_artifact,
            asset_id=asset_id,
            vector=newer,
            now=benchmark.now_ms(),
            input_revision=hashlib.sha256(b"p4-02c-newest-reclaimed").hexdigest(),
        )
        path, replayed, published = corrupt_artifact(fixture, newest_artifact)
        with pytest.raises(CatalogError) as refused:
            maintenance.drain_semantic_outbox(fixture.catalog)
        assert refused.value.code == ARTIFACT_MISSING

        isolation = maintenance.drain_semantic_outbox(fixture.catalog, repair=True)
        # The audit refuses to schedule an event that would outrank the re-execution target.
        assert isolation["repair"] == {"scheduled": 0, "requires_reexecution": 1}
        assert isolation["awaiting_reexecution"]["count"] == 1
        assert isolation["applied"] == 1  # the older artifact's own outstanding event
        assert outbox_rows(fixture) == 2

        source = give_the_asset_a_real_source_file(fixture, tmp_path, asset_id)
        recovery = maintenance.reexecute_reclaimed_artifacts(
            fixture.catalog, encoder=StubImageEncoder({source.name: replayed})
        )
        assert recovery["republished"] == 1
        final = maintenance.drain_semantic_outbox(fixture.catalog)
        assert final["applied"] == 1
        assert final["awaiting_reexecution"]["count"] == 0
        assert probe.outbox_states(fixture) == {"CONFIRMED": 2}
        window = probe._entry_revision(fixture, asset_id)
        row = probe._row_for(fixture, asset_id) or {}
        assert str(window["state"]) == "CONFIRMED"
        assert int(window["confirmation_revision"]) == int(row["projection_revision"]) == (
            probe._max_event_revision(fixture, asset_id)
        )
        # The re-executed artifact is the one that survived; the pre-change vector is gone.
        assert probe._asset_distance(fixture, query=newer, asset_id=asset_id) <= 1e-6
        assert probe._asset_distance(fixture, query=stale, asset_id=asset_id) > 0.0
        # The re-executed artifact is byte-identical to what was published before the corruption.
        assert path.read_bytes() == published
    finally:
        fixture.catalog.close()


def test_a_same_millisecond_content_change_keeps_the_newest_vector(tmp_path: Path) -> None:
    """Fix round F4 (Sol focused review): order the triple by authority, not by millisecond.

    ``artifacts.created_at`` has millisecond granularity, so two legitimate content versions
    published inside one millisecond can put the *older* artifact first by artifact_id. The audit
    then allocated the higher projection revision for that stale artifact, the drain confirmed it,
    and search kept returning the pre-change vector while every report read "confirmed".
    """
    fixture = build(tmp_path, events=1)
    try:
        asset_id = int(fixture.scale) + 1
        stale_artifact = f"p4-02c-{asset_id}"
        published_at = int(
            fixture.catalog.read_one(
                "SELECT created_at FROM artifacts WHERE artifact_id = ?", (stale_artifact,)
            )["created_at"]
        )
        stale = json.loads(
            artifact_path(fixture, stale_artifact).read_text(encoding="utf-8")
        )["vector"]
        newest = benchmark.synthetic_vectors(1, seed=333, distribution="clustered")[0]
        benchmark.bump_filter_revision(fixture, cause="CONTENT_CHANGED")
        benchmark.publish_embedding(
            fixture,
            artifact_id="a-new-content",
            asset_id=asset_id,
            vector=newest,
            now=published_at,
            input_revision=hashlib.sha256(b"p4-02c-same-millisecond").hexdigest(),
        )
        # The two versions really share one millisecond, and the stale id sorts higher.
        assert stale_artifact > "a-new-content"
        assert {
            str(row["created_at"])
            for row in fixture.catalog.read_all(
                "SELECT created_at FROM artifacts WHERE artifact_id IN (?, ?)",
                (stale_artifact, "a-new-content"),
            )
        } == {str(published_at)}

        audit = IndexCoordinator(fixture.catalog).audit_and_schedule_repairs(now_ms=published_at)
        assert audit == {"scheduled": [], "requires_reexecution": []}
        assert outbox_rows(fixture) == 2

        drained = maintenance.drain_semantic_outbox(fixture.catalog)
        assert drained["applied"] == 2
        entry = probe._entry_revision(fixture, asset_id)
        row = probe._row_for(fixture, asset_id) or {}
        assert str(entry["state"]) == "CONFIRMED"
        assert int(entry["confirmation_revision"]) == int(row["projection_revision"]) == 1
        assert probe._asset_distance(fixture, query=newest, asset_id=asset_id) <= 1e-6
        assert probe._asset_distance(fixture, query=stale, asset_id=asset_id) > 0.0
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


def test_the_json_entry_point_survives_a_non_ascii_path(tmp_path: Path) -> None:
    """P4-02C review F2: ``--json`` is a machine-readable contract, not a console artifact.

    A renamed model or data directory is an ordinary Windows fact (the documented default model
    directory lives under the user profile), and a ``--json`` document carrying one must still be
    decodable as UTF-8 when its bytes go through a pipe.
    """
    fixture = build(tmp_path, events=2)
    try:
        root = str(fixture.data_root)
        non_ascii = tmp_path / "模型 目录"
        non_ascii.mkdir(parents=True, exist_ok=True)
        argv = [
            "reexecute",
            "--data-root",
            root,
            "--model-dir",
            str(non_ascii),
            "--manifest",
            str(non_ascii / "manifest.json"),
            "--device",
            "cpu",
            "--json",
        ]
        digest = probe.state_digest(fixture)["sha256"]
        completed = subprocess.run(
            [sys.executable, "-m", "photo_search.indexing.cli", *argv],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            timeout=600,
        )
        assert completed.returncode == 1
        assert completed.stdout.isascii()
        payload = json.loads(completed.stdout.decode("utf-8"))
        assert payload["status"] == "error"
        assert payload["error"]["code"] == RUNTIME_CAPABILITY_UNAVAILABLE
        assert payload["error"]["details"]["model_dir"] == str(non_ascii)
        # The refusal still happens before any catalog work.
        assert probe.state_digest(fixture)["sha256"] == digest
        # The delivered evidence tool reads the very same invocation without raising.
        helper = probe._run_cli(argv)
        assert helper["returncode"] == 1
        assert helper["payload"]["error"]["code"] == RUNTIME_CAPABILITY_UNAVAILABLE
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
