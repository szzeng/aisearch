from __future__ import annotations

import pytest

from photo_search.workers.protocol import (
    MAX_MESSAGE_BYTES,
    ProtocolError,
    checked_message,
    error_message,
)


def test_messages_are_json_safe_and_bounded() -> None:
    assert checked_message({"action": "health"}) == {"action": "health"}
    with pytest.raises(ProtocolError, match="exceeds"):
        checked_message({"text": "x" * MAX_MESSAGE_BYTES})


def test_messages_reject_non_json_values() -> None:
    with pytest.raises(ProtocolError, match="JSON serialisable"):
        checked_message({"invalid": {1, 2}})


def test_error_envelope_carries_device_details() -> None:
    payload = error_message(
        stage="startup",
        error=RuntimeError("cuda unavailable"),
        completed_steps=["model_loaded"],
        details={"requested_device": "cuda", "cpu_fallback": "disabled"},
    )
    assert payload["status"] == "error"
    assert payload["stage"] == "startup"
    assert payload["exception_type"] == "RuntimeError"
    assert payload["details"] == {"requested_device": "cuda", "cpu_fallback": "disabled"}


def test_error_envelope_omits_empty_details() -> None:
    payload = error_message(stage="request", error=ValueError("bad"), completed_steps=[])
    assert "details" not in payload
