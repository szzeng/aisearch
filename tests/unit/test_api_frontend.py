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


# ---------------------------------------------------------------------------------------
# P1-05B: the front-end contract, one assertion per promise the page makes to the user.
# Each of these guards one specific regression; they are static on purpose (the repository
# deliberately ships no Node/npm chain, so there is no DOM test harness -- see
# docs/diagnostics/frontend-smoke-checklist.md).
# ---------------------------------------------------------------------------------------
def _web_documents() -> tuple[str, str, str]:
    shell = (read_static(INDEX_NAME) or b"").decode("utf-8")
    script = (read_static("app.js") or b"").decode("utf-8")
    style = (read_static("styles.css") or b"").decode("utf-8")
    assert shell and script and style
    return shell, script, style


def test_frontend_reaches_data_only_through_the_protected_api() -> None:
    """Every data request goes through ``/api/v1``; nothing bypasses the boundary."""
    _, script, _ = _web_documents()

    assert 'var API = "/api/v1";' in script
    # No request may be issued without the API prefix, so the count of call sites and the
    # count of prefixed call sites have to match exactly.
    assert script.count("fetch(") == script.count("fetch(API +")
    assert "fetch(" in script
    # The original bytes are reached by navigating to a controlled asset id, not a path.
    assert 'link.href = API + "/assets/"' in script
    # The page never opens the catalog or a source directory itself.
    assert "sqlite" not in script.lower()
    assert "catalog.db" not in script


def test_frontend_never_puts_a_credential_in_a_url_or_in_storage() -> None:
    """The bootstrap credential and the CSRF value never leave the cookie/header channel."""
    _, script, _ = _web_documents()

    assert "ps_csrf" in script
    assert "X-CSRF-Token" in script
    assert "token=" not in script
    assert "csrf_token" not in script
    assert "document.cookie =" not in script  # read-only cookie access
    assert "localStorage" not in script
    assert "sessionStorage" not in script
    assert "console." not in script


def test_frontend_never_renders_an_absolute_local_path() -> None:
    """Only display names and source-relative paths are rendered; root_path stays in the form."""
    _, script, _ = _web_documents()

    # The user's typed root path is sent to the API as a request field, but the list and the
    # detail renderer never read it back into the DOM.
    assert "root_path: root" in script
    assert "source.root_path" not in script
    # Server-supplied text is scrubbed before it is shown (P1-05 report §8.1).
    assert "PATH_LIKE" in script
    assert "错误详情包含本机路径" in script


def test_frontend_copy_stays_honest_about_time_and_offline_media() -> None:
    """The page states the browsing dimension and the offline reason explicitly."""
    shell, script, _ = _web_documents()

    assert "加入时间" in shell + script
    assert "不是 EXIF 拍摄时间" in shell
    assert "原文件不可用" in script
    assert "请使用启动器打印的一次性链接建立会话。" in script
    # A refused artifact shows the server-supplied code as visible text, never a broken image.
    assert "badge-code" in script
    assert "result.code" in script
