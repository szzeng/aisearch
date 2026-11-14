"""Process-scoped ownership tags for opaque lease tokens."""

from __future__ import annotations

import re
import secrets
import threading
from dataclasses import dataclass

_LOCK = threading.Lock()
_APP_OWNER_PREFIX: str | None = None
_TAG = re.compile(r"^app:[0-9a-f]{32}:")


@dataclass
class _ClockState:
    high_water_ms: int | None = None
    rollback_pending: bool = False
    rollback_observed_ms: int | None = None


_APP_CLOCKS: dict[str, _ClockState] = {}


def begin_app_lease_owner(scope: str) -> str:
    """Start one managed application incarnation and return its token prefix."""
    global _APP_OWNER_PREFIX
    prefix = f"app:{secrets.token_hex(16)}:"
    with _LOCK:
        _APP_OWNER_PREFIX = prefix
        _APP_CLOCKS.clear()
        _APP_CLOCKS[str(scope)] = _ClockState()
    return prefix


def current_app_lease_owner() -> str | None:
    with _LOCK:
        return _APP_OWNER_PREFIX


def observe_app_clock(scope: str, observed_ms: int) -> int | None:
    """Record one data root's wall time and return its process high-water."""
    with _LOCK:
        if _APP_OWNER_PREFIX is None:
            return None
        state = _APP_CLOCKS.setdefault(str(scope), _ClockState())
        observed = int(observed_ms)
        if state.high_water_ms is None or observed > state.high_water_ms:
            state.high_water_ms = observed
        elif observed < state.high_water_ms:
            state.rollback_pending = True
            state.rollback_observed_ms = observed
        return int(state.high_water_ms)


def app_clock_rollback_observed(scope: str) -> int | None:
    with _LOCK:
        state = _APP_CLOCKS.get(str(scope))
        return (
            int(state.rollback_observed_ms)
            if state is not None
            and state.rollback_pending
            and state.rollback_observed_ms is not None
            else None
        )


def mark_app_clock_rollback_recovered(scope: str) -> None:
    with _LOCK:
        state = _APP_CLOCKS.get(str(scope))
        if state is not None:
            state.rollback_pending = False
            state.rollback_observed_ms = None


def tag_lease_token(token: str) -> str:
    """Tag only tokens allocated while a managed application owns the data root."""
    owner = current_app_lease_owner()
    return f"{owner}{token}" if owner is not None else str(token)


def app_lease_token_owned_by(prefix: str, token: str | None) -> bool:
    """Return whether an opaque token belongs to the supplied application prefix."""
    return bool(prefix) and bool(token) and str(token).startswith(prefix)


def is_app_lease_token(token: str | None) -> bool:
    return bool(token) and _TAG.match(str(token)) is not None


def set_app_lease_owner_for_test(prefix: str | None) -> None:
    """Set or clear the owner tag for isolated tests."""
    global _APP_OWNER_PREFIX
    if prefix is not None and (not prefix.startswith("app:") or not prefix.endswith(":")):
        raise ValueError("the application lease owner prefix is invalid")
    with _LOCK:
        _APP_OWNER_PREFIX = prefix
        _APP_CLOCKS.clear()


__all__ = [
    "app_lease_token_owned_by",
    "app_clock_rollback_observed",
    "begin_app_lease_owner",
    "current_app_lease_owner",
    "is_app_lease_token",
    "mark_app_clock_rollback_recovered",
    "observe_app_clock",
    "set_app_lease_owner_for_test",
    "tag_lease_token",
]
