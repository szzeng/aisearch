"""P4-02Y §5: the observable state of one library refresh.

The refresh is an *internal* maintenance run: the public API reports counts, stage names
and a state, never an asset-id list of the user's library (P4-02Y §4). This module is the
single place that decides which facts a client may see, so the HTTP layer, the diagnostics
tool and the tests all describe the same thing.
"""

from __future__ import annotations

import copy
import threading
from collections.abc import Mapping, Sequence
from typing import Any

#: ``IDLE`` is the state before the first refresh of a process; the rest are the stages of
#: one run. ``SEARCH_READY`` and ``COMPLETE`` are deliberately distinct: the first means
#: this round's new content is already reachable through the current search path, the second
#: that the available enrichment stages have also finished (P4-02Y §5).
REFRESH_STATES = (
    "IDLE",
    "SCANNING",
    "INDEXING",
    "SEARCH_READY",
    "ENRICHING",
    "COMPLETE",
    "PARTIAL_FAILED",
    "FAILED",
)

#: How a stage ended. ``SKIPPED_UNAVAILABLE`` is a reportable outcome, not a failure: this
#: machine has no weights/model/dataset for that processor (P4-02Y §6).
STAGE_OK = "OK"
STAGE_SKIPPED = "SKIPPED_UNAVAILABLE"
STAGE_SKIPPED_NO_TARGETS = "SKIPPED_NO_TARGETS"
STAGE_FAILED = "FAILED"


def empty_counters() -> dict[str, Any]:
    """The refresh-wide counters, all zero before the first source is scanned."""
    return {
        "sources_requested": 0,
        "sources_scanned": 0,
        "sources_failed": 0,
        "enumerated": 0,
        "unchanged_reused": 0,
        "hash_targets": 0,
        "hash_bytes": 0,
        "new_assets": 0,
        "rebound_assets": 0,
        "locations_added": 0,
        "locations_missing": 0,
        "content_delta_count": 0,
        "location_delta_count": 0,
        "delta_overflow": False,
        "scan_ms": 0,
        "search_ready_ms": None,
        "complete_ms": None,
    }


class RefreshStatus:
    """Thread-safe, JSON-ready snapshot of the refresh a process is running (or ran)."""

    def __init__(
        self,
        *,
        refresh_id: str,
        source_ids: Sequence[int] | None,
        batch_size: int,
        started_at_ms: int,
        mode: str = "refresh",
        face_profile: str = "source_detail",
        now_ms: int | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._now_ms = now_ms
        self._data: dict[str, Any] = {
            "refresh_id": str(refresh_id),
            "state": "SCANNING",
            "mode": str(mode),
            "face_profile": str(face_profile),
            "search_ready": False,
            "search_ready_basis": None,
            "source_ids": None if source_ids is None else [int(v) for v in source_ids],
            "batch_size": int(batch_size),
            "started_at_ms": int(started_at_ms),
            "updated_at_ms": int(started_at_ms),
            "finished_at_ms": None,
            "stages": {},
            "failed_stages": [],
            "sources": [],
            "counters": empty_counters(),
            "errors": [],
        }

    # ------------------------------------------------------------------ writes
    def _touch(self, data: dict[str, Any]) -> None:
        if self._now_ms is not None:
            data["updated_at_ms"] = int(self._now_ms())

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    def set_face_profile(self, profile: str) -> None:
        with self._lock:
            self._data["face_profile"] = str(profile)
            self._touch(self._data)

    def set_state(self, state: str, **fields: Any) -> None:
        if state not in REFRESH_STATES:
            raise ValueError(f"unknown refresh state {state!r}")
        with self._lock:
            self._data["state"] = state
            self._data.update(fields)
            self._touch(self._data)

    def set_counters(self, **values: Any) -> None:
        with self._lock:
            self._data["counters"].update(values)
            self._touch(self._data)

    def add_counters(self, **values: int) -> None:
        with self._lock:
            counters = self._data["counters"]
            for key, value in values.items():
                counters[key] = int(counters.get(key, 0)) + int(value)
            self._touch(self._data)

    def set_stage(self, name: str, payload: Mapping[str, Any]) -> None:
        with self._lock:
            self._data["stages"][str(name)] = dict(payload)
            self._touch(self._data)

    def set_failed_stages(self, names: Sequence[str]) -> None:
        """The stages (and stage-scoped reasons) that kept this run from being COMPLETE."""
        with self._lock:
            self._data["failed_stages"] = [str(name) for name in names]
            self._touch(self._data)

    def add_source(self, payload: Mapping[str, Any]) -> None:
        with self._lock:
            self._data["sources"].append(dict(payload))
            self._touch(self._data)

    def note_error(self, *, stage: str, code: str, message: str, source_id: int | None = None) -> None:
        entry: dict[str, Any] = {"stage": str(stage), "code": str(code), "message": str(message)}
        if source_id is not None:
            entry["source_id"] = int(source_id)
        with self._lock:
            self._data["errors"].append(entry)
            self._touch(self._data)

    def mark_search_ready(self, *, basis: str, elapsed_ms: int) -> None:
        with self._lock:
            self._data["search_ready"] = True
            self._data["search_ready_basis"] = str(basis)
            self._data["counters"]["search_ready_ms"] = int(elapsed_ms)
            self._data["state"] = "SEARCH_READY"
            self._touch(self._data)

    def note_search_gate(self, *, basis: str) -> None:
        """Record *why* the run is not search ready, without claiming readiness (P4-02Y-I).

        ``search_ready_basis`` is the honest half of the two-part answer: a build that stopped
        with a durable backlog left reports ``semantic-backlog`` here and keeps
        ``search_ready = false``, so the caller can word it as "not finished yet" instead of
        mistaking it for the same thing as a broken scan.
        """
        with self._lock:
            self._data["search_ready_basis"] = str(basis)
            self._touch(self._data)

    def finish(self, *, state: str, elapsed_ms: int) -> None:
        if state not in ("COMPLETE", "PARTIAL_FAILED", "FAILED"):
            raise ValueError(f"a refresh may only finish as COMPLETE/PARTIAL_FAILED/FAILED, not {state!r}")
        with self._lock:
            self._data["state"] = state
            self._data["counters"]["complete_ms"] = int(elapsed_ms)
            if self._now_ms is not None:
                self._data["finished_at_ms"] = int(self._now_ms())
            self._data["updated_at_ms"] = int(self._data["finished_at_ms"] or self._data["updated_at_ms"])


def idle_status(*, batch_size: int) -> dict[str, Any]:
    """The answer ``GET /api/v1/library/refresh`` gives before any refresh of this process."""
    return {
        "refresh_id": None,
        "state": "IDLE",
        "mode": None,
        "search_ready": False,
        "search_ready_basis": None,
        "source_ids": None,
        "batch_size": int(batch_size),
        "started_at_ms": None,
        "updated_at_ms": None,
        "finished_at_ms": None,
        "stages": {},
        "failed_stages": [],
        "sources": [],
        "counters": empty_counters(),
        "errors": [],
    }


__all__ = [
    "REFRESH_STATES",
    "STAGE_FAILED",
    "STAGE_OK",
    "STAGE_SKIPPED",
    "STAGE_SKIPPED_NO_TARGETS",
    "RefreshStatus",
    "empty_counters",
    "idle_status",
]
