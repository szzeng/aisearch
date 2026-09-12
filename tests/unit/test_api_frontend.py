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
from photo_search.api.web import INDEX_NAME, media_type_for_name, read_static


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


@pytest.mark.parametrize("relative", ["index.html", "app.js", "styles.css"])
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


def test_the_shipped_shell_has_no_external_reference_or_credential() -> None:
    shell = (read_static(INDEX_NAME) or b"").decode("utf-8")
    script = (read_static("app.js") or b"").decode("utf-8")
    style = (read_static("styles.css") or b"").decode("utf-8")

    for document in (shell, script, style):
        assert "http://" not in document
        assert "https://" not in document
    assert 'lang="zh-CN"' in shell
    assert "/web/app.js" in shell
    assert "/web/styles.css" in shell
    # The token is only ever read from the cookie and sent in the CSRF header.
    assert "ps_csrf" in script
    assert "X-CSRF-Token" in script
    assert 'credentials: "same-origin"' in script
    assert "localStorage" not in script
    assert "sessionStorage" not in script
    assert "console." not in script
    # A media failure message can carry a repr-escaped absolute path that the server-side
    # redactor does not replace, so the page has to withhold such text itself.
    assert "错误详情包含本机路径" in script
    assert "PATH_LIKE" in script
    # The browsing dimension is honestly labelled and uses the documented ordering.
    assert "加入时间" in shell + script
    assert "time_desc" in script
