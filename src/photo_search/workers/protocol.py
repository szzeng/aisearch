"""Bounded, JSON-serialisable messages for the AI worker boundary."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

MAX_MESSAGE_BYTES = 1024 * 1024


class ProtocolError(ValueError):
    """A request or response violates the worker transport contract."""


def checked_message(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe message after enforcing the explicit 1 MiB limit."""
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ProtocolError(f"message is not JSON serialisable: {error}") from error
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise ProtocolError(f"message exceeds {MAX_MESSAGE_BYTES} byte limit")
    return dict(value)


def error_message(
    *, stage: str,
    error: BaseException,
    completed_steps: list[str],
) -> dict[str, Any]:
    """Create the stable failure envelope returned to the parent process."""
    return checked_message(
        {
            "status": "error",
            "stage": stage,
            "exception_type": type(error).__name__,
            "message": str(error),
            "completed_steps": completed_steps,
        }
    )
