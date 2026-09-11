from __future__ import annotations

import pytest

from photo_search.workers.protocol import MAX_MESSAGE_BYTES, ProtocolError, checked_message


def test_messages_are_json_safe_and_bounded() -> None:
    assert checked_message({"action": "health"}) == {"action": "health"}
    with pytest.raises(ProtocolError, match="exceeds"):
        checked_message({"text": "x" * MAX_MESSAGE_BYTES})


def test_messages_reject_non_json_values() -> None:
    with pytest.raises(ProtocolError, match="JSON serialisable"):
        checked_message({"invalid": {1, 2}})