"""Unit tests for the packaged front end, the redirect target and the cursor spaces."""

from __future__ import annotations

import base64

import pytest

from photo_search.api.errors import ApiError
from photo_search.api.queries import (
    decode_cursor,
    decode_time_cursor,
    encode_cursor,
    encode_time_cursor,
)
from photo_search.api.session import safe_redirect_target
from photo_search.api.web import (
    INDEX_NAME,
    media_type_for_name,
    read_static,
)


def _blob(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("ascii")).decode("ascii").rstrip("=")


@pytest.mark.parametrize("target", ["/", "/sources", "/web/app.js", "/assets?order=time_desc"])
def test_redirect_target_accepts_plain_local_paths(target: str) -> None:
    assert safe_redirect_target(target) == target
    assert safe_redirect_target(None) is None


@pytest.mark.parametrize(
    "target",
    [
        "",
        "http://evil.example",
        "https://evil.example/",
        "//evil.example",
        "/\\evil.example",
        "\\evil.example",
        "relative/path",
        "/a\r\nb",
        "/a\nb",
        "/x\x00y",
        "javascript:alert(1)",
    ],
)
def test_redirect_target_refuses_everything_else(target: str) -> None:
    with pytest.raises(ApiError) as failure:
        safe_redirect_target(target)
    assert failure.value.code == "VALIDATION_ERROR"
    assert failure.value.status == 422
    assert failure.value.details == {"param": "next"}


def test_time_cursor_round_trip() -> None:
    assert decode_time_cursor(encode_time_cursor(1_700_000_000_000, 42)) == (
        1_700_000_000_000,
        42,
    )
    assert decode_time_cursor(None) is None
    assert decode_time_cursor("") is None


def test_the_two_cursor_spaces_never_mix() -> None:
    time_cursor = encode_time_cursor(1_700_000_000_000, 7)
    id_cursor = encode_cursor(7)
    assert time_cursor != id_cursor

    with pytest.raises(ApiError) as failure:
        decode_cursor(time_cursor)
    assert failure.value.code == "INVALID_CURSOR"

    with pytest.raises(ApiError) as failure:
        decode_time_cursor(id_cursor)
    assert failure.value.code == "INVALID_CURSOR"


@pytest.mark.parametrize(
    "cursor",
    [
        "!!!!",
        _blob("t1:1"),
        _blob("t1:1:2:3"),
        _blob("t1:-1:2"),
        _blob("t1:a:b"),
        _blob("t1:"),
        _blob("v1:1"),
    ],
)
def test_malformed_time_cursors_are_rejected(cursor: str) -> None:
    with pytest.raises(ApiError) as failure:
        decode_time_cursor(cursor)
    assert failure.value.code == "INVALID_CURSOR"
    assert failure.value.status == 422


@pytest.mark.parametrize("relative", [INDEX_NAME])
def test_packaged_assets_are_readable(relative: str) -> None:
    payload = read_static(relative)
    assert payload and len(payload) > 0


@pytest.mark.parametrize(
    "relative",
    [
        "",
        "/",
        "..",
        "../app.js",
        "..\\app.js",
        "app.js/../../index.html",
        "/etc/passwd",
        "%2e%2e/app.js",
        "index.html\x00.js",
    ],
)
def test_read_static_refuses_anything_outside_the_package(relative: str) -> None:
    assert read_static(relative) is None


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("index.html", "text/html"),
        ("app.js", "text/javascript"),
        ("styles.css", "text/css"),
        ("logo.svg", "image/svg+xml"),
        ("mystery.bin", "application/octet-stream"),
    ],
)
def test_web_media_types(name: str, expected: str) -> None:
    assert media_type_for_name(name) == expected


def test_compiled_shell_references_only_present_local_assets() -> None:
    import re

    shell = (read_static(INDEX_NAME) or b"").decode("utf-8")
    assert 'lang="zh-CN"' in shell
    assert 'id="app"' in shell
    assets = re.findall(r'(?:src|href)="(/web/[^"]+)"', shell)
    assert assets
    for asset in assets:
        assert read_static(asset.removeprefix("/web/"))
    assert "https://" not in shell and "http://" not in shell
