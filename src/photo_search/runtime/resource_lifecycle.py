"""Lifecycle helpers for resources owned by a worker thread."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

CLOSE_RETRY_SECONDS = 0.1


def close_owned_resource(
    resource: Any,
    *,
    on_failure: Callable[[Exception], None],
    retry_seconds: float = CLOSE_RETRY_SECONDS,
) -> None:
    """Close one thread-owned resource until it succeeds.

    The caller is the owning worker thread, so retrying here preserves SQLite thread
    affinity. A permanent failure intentionally keeps that worker alive; callers must
    retain the data-root lease while the resource cannot be cleaned up.
    """
    close = getattr(resource, "close", None)
    if not callable(close):
        return
    while True:
        try:
            close()
        except Exception as error:  # noqa: BLE001 - failure is reported to the owner
            on_failure(error)
            time.sleep(max(0.0, float(retry_seconds)))
        else:
            return


__all__ = ["CLOSE_RETRY_SECONDS", "close_owned_resource"]
