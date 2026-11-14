"""Bounded, cancellable access to the existing background-model RLock."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager


class BackgroundSlotBusy(TimeoutError):
    """Another background model still owns the shared execution slot."""


class BackgroundSlotCancelled(InterruptedError):
    """The caller stopped before it acquired the shared execution slot."""


@contextmanager
def background_model_slot(
    *, cancel_check: Callable[[], bool] | None = None, timeout: float = 60.0,
) -> Iterator[None]:
    """Acquire the same reentrant lock as face work; never acquire a database lock here."""
    from photo_search.face.service import BACKGROUND_MODEL_SLOT

    deadline = time.monotonic() + max(0.0, float(timeout))
    acquired = False
    while not acquired:
        if cancel_check is not None and cancel_check():
            raise BackgroundSlotCancelled("background model wait was cancelled")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            acquired = BACKGROUND_MODEL_SLOT.acquire(blocking=False)
            if not acquired:
                raise BackgroundSlotBusy("background model slot is busy")
            break
        acquired = BACKGROUND_MODEL_SLOT.acquire(timeout=min(0.1, remaining))
    try:
        if cancel_check is not None and cancel_check():
            raise BackgroundSlotCancelled("background model wait was cancelled")
        yield
    finally:
        BACKGROUND_MODEL_SLOT.release()


def background_slot_owned_by_current_thread() -> bool:
    """Check ownership without initializing CUDA or waiting for the shared slot."""
    from photo_search.face.service import BACKGROUND_MODEL_SLOT

    owner_check = getattr(BACKGROUND_MODEL_SLOT, "owned_by_current_thread", None)
    if owner_check is None:
        owner_check = getattr(BACKGROUND_MODEL_SLOT, "_is_owned", None)
    return bool(owner_check()) if callable(owner_check) else False


_foreground_condition = threading.Condition()
_foreground_waiters = 0
_foreground_intent_local = threading.local()


def foreground_model_waiting() -> bool:
    """Check local waiters and cooperating Windows processes without blocking."""
    from photo_search.face.service import BACKGROUND_MODEL_SLOT

    with _foreground_condition:
        if _foreground_waiters > 0:
            return True
    marker_factory = getattr(BACKGROUND_MODEL_SLOT, "priority_marker", None)
    if marker_factory is None:
        return False
    marker = marker_factory()
    if not marker.acquire(blocking=False):
        return True
    marker.release()
    return False


@contextmanager
def foreground_model_slot(*, consume_intent: bool = False) -> Iterator[None]:
    """Wait until the current GPU operation yields; preserve reentrant ownership."""
    from photo_search.face.service import BACKGROUND_MODEL_SLOT

    global _foreground_waiters
    owner_check = getattr(BACKGROUND_MODEL_SLOT, "owned_by_current_thread", None)
    if owner_check is None:
        owner_check = getattr(BACKGROUND_MODEL_SLOT, "_is_owned", lambda: False)
    nested = bool(owner_check())
    marker_factory = getattr(BACKGROUND_MODEL_SLOT, "priority_marker", None)
    marker = marker_factory() if marker_factory is not None and not nested else None
    with _foreground_condition:
        if not nested:
            _foreground_waiters += 1
        _foreground_condition.notify_all()
    acquired = False
    marker_acquired = False
    try:
        if marker is not None:
            while not marker_acquired:
                marker_acquired = marker.acquire(timeout=0.1)
        while not acquired:
            acquired = BACKGROUND_MODEL_SLOT.acquire(timeout=0.1)
        with _foreground_condition:
            if not nested:
                _foreground_waiters -= 1
            _foreground_condition.notify_all()
        try:
            if consume_intent:
                intent = getattr(_foreground_intent_local, "active", None)
                if intent is not None:
                    intent.release()
            yield
        finally:
            BACKGROUND_MODEL_SLOT.release()
    finally:
        if not acquired and not nested:
            with _foreground_condition:
                _foreground_waiters -= 1
                _foreground_condition.notify_all()
        if marker_acquired:
            marker.release()


class _ForegroundIntent:
    def __init__(self, marker: object | None) -> None:
        self.marker = marker
        self.active = True

    def release(self) -> None:
        global _foreground_waiters
        if not self.active:
            return
        self.active = False
        try:
            if self.marker is not None:
                self.marker.release()
        finally:
            with _foreground_condition:
                _foreground_waiters -= 1
                _foreground_condition.notify_all()


@contextmanager
def foreground_model_intent() -> Iterator[None]:
    """Keep search priority from adapter resolution until the first model call."""
    from photo_search.face.service import BACKGROUND_MODEL_SLOT

    global _foreground_waiters
    existing = getattr(_foreground_intent_local, "active", None)
    if existing is not None and existing.active:
        yield
        return
    marker_factory = getattr(BACKGROUND_MODEL_SLOT, "priority_marker", None)
    marker = marker_factory() if marker_factory is not None else None
    with _foreground_condition:
        _foreground_waiters += 1
        _foreground_condition.notify_all()
    acquired = False
    intent = None
    try:
        if marker is not None:
            while not acquired:
                acquired = marker.acquire(timeout=0.1)
        intent = _ForegroundIntent(marker)
        _foreground_intent_local.active = intent
        yield
    finally:
        if intent is not None:
            intent.release()
            _foreground_intent_local.active = existing
        else:
            if acquired:
                marker.release()
            with _foreground_condition:
                _foreground_waiters -= 1
                _foreground_condition.notify_all()
