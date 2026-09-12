"""Unit tests for the keyset cursor and the documented page bounds."""

from __future__ import annotations

import pytest

from photo_search.api.errors import ApiError
from photo_search.api.queries import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    clamp_limit,
    decode_cursor,
    encode_cursor,
)


@pytest.mark.parametrize("value", [0, 1, 42, 999_999])
def test_cursor_round_trip(value: int) -> None:
    assert decode_cursor(encode_cursor(value)) == value


def test_empty_cursor_starts_at_zero() -> None:
    assert decode_cursor(None) == 0
    assert decode_cursor("") == 0


@pytest.mark.parametrize(
    "cursor",
    ["not-base64!!", "Zm9v", "djE6", "djE6YWJj", "djI6MQ", "v2:3", "djE6LTE"],
)
def test_invalid_cursor_is_rejected(cursor: str) -> None:
    with pytest.raises(ApiError) as failure:
        decode_cursor(cursor)
    assert failure.value.code == "INVALID_CURSOR"
    assert failure.value.status == 422


def test_limit_defaults_clamps_and_rejects_zero() -> None:
    assert clamp_limit(None) == DEFAULT_PAGE_LIMIT
    assert clamp_limit(1) == 1
    assert clamp_limit(MAX_PAGE_LIMIT) == MAX_PAGE_LIMIT
    assert clamp_limit(10_000) == MAX_PAGE_LIMIT
    with pytest.raises(ApiError) as failure:
        clamp_limit(0)
    assert failure.value.status == 422
