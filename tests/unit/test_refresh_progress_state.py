"""P4-BUILD-STABILITY-A2-C: in-memory refresh activity is honest and monotone."""

from __future__ import annotations

from photo_search.refresh.state import RefreshStatus


def test_activity_tracks_real_counts_and_waiting_does_not_advance_progress_time() -> None:
    moments = iter((1000, 2000, 9000, 10000))
    status = RefreshStatus(
        refresh_id="activity",
        source_ids=[1],
        batch_size=128,
        started_at_ms=1000,
        now_ms=lambda: next(moments),
    )

    status.set_activity(
        phase="semantic",
        detail="encoding_images",
        completed=2,
        total=10,
    )
    status.set_activity(
        phase="semantic",
        detail="encoding_images",
        completed=7,
        total=10,
    )
    waiting = status.snapshot()
    assert waiting["activity"]["completed"] == 7
    assert waiting["activity"]["total"] == 10
    assert waiting["progress_updated_at_ms"] == 2000

    status.set_activity(
        phase="semantic",
        detail="waiting_model",
        completed=7,
        total=10,
        waiting_reason="MODEL_RESOURCE",
        mark_progress=False,
    )
    paused = status.snapshot()
    assert paused["activity"]["waiting_reason"] == "MODEL_RESOURCE"
    assert paused["progress_updated_at_ms"] == 2000
    assert paused["updated_at_ms"] == 9000


def test_activity_counts_never_decrease_or_exceed_known_total() -> None:
    moments = iter((1000, 2000, 3000))
    status = RefreshStatus(
        refresh_id="monotone",
        source_ids=[1],
        batch_size=128,
        started_at_ms=1000,
        now_ms=lambda: next(moments),
    )

    status.set_activity(
        phase="scanning",
        detail="hashing_files",
        completed=8,
        total=10,
        source_index=2,
        source_total=3,
    )
    status.set_activity(
        phase="scanning",
        detail="hashing_files",
        completed=4,
        total=10,
        source_index=2,
        source_total=3,
    )
    snapshot = status.snapshot()
    assert snapshot["activity"]["completed"] == 8
    assert snapshot["activity"]["source_index"] == 2
    assert snapshot["activity"]["source_total"] == 3


def test_each_source_starts_with_its_own_progress_cycle() -> None:
    moments = iter((1000, 2000, 3000))
    status = RefreshStatus(
        refresh_id="multi-source",
        source_ids=[1, 2],
        batch_size=128,
        started_at_ms=1000,
        now_ms=lambda: next(moments),
    )

    status.set_activity(
        phase="scanning",
        detail="hashing_files",
        completed=5,
        total=5,
        source_index=1,
        source_total=2,
        cycle="scan:1",
    )
    status.set_activity(
        phase="scanning",
        detail="hashing_files",
        completed=0,
        total=2,
        source_index=2,
        source_total=2,
        cycle="scan:2",
    )
    snapshot = status.snapshot()
    assert snapshot["activity"]["completed"] == 0
    assert snapshot["activity"]["total"] == 2
    assert snapshot["activity"]["source_index"] == 2
