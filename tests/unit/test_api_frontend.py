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
from photo_search.api.web import INDEX_NAME, PLACES_NAME, media_type_for_name, read_static


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


# ---------------------------------------------------------------------------------------
# P2-03C: the place-curation page. It is a separate document in the same package-data
# directory, so it keeps the P1-05 rules and adds the frozen ADR-0005 vocabulary verbatim.
# ---------------------------------------------------------------------------------------
PLACES_DOCUMENTS = ("places.html", "places.js", "places.css")


def _places_documents() -> tuple[str, str, str]:
    shell = (read_static(PLACES_NAME) or b"").decode("utf-8")
    script = (read_static("places.js") or b"").decode("utf-8")
    style = (read_static("places.css") or b"").decode("utf-8")
    assert shell and script and style
    return shell, script, style


@pytest.mark.parametrize("relative", PLACES_DOCUMENTS)
def test_the_places_page_assets_are_packaged(relative: str) -> None:
    payload = read_static(relative)
    assert payload and len(payload) > 0


def test_the_places_page_route_never_shadows_the_place_api() -> None:
    """``/places`` is a document; every place record still comes from ``/api/v1/places``."""
    from photo_search.api.routes import router as api_router
    from photo_search.api.web import router as web_router

    api_paths = {route.path for route in api_router.routes}
    web_paths = {route.path for route in web_router.routes}
    assert PLACES_NAME == "places.html"
    assert "/places" in web_paths
    assert "/api/v1/places" in api_paths
    assert "/places" not in api_paths


def test_the_places_page_has_no_external_reference_credential_or_html_sink() -> None:
    shell, script, style = _places_documents()

    for document in (shell, script, style):
        assert "http://" not in document
        assert "https://" not in document
    assert 'lang="zh-CN"' in shell
    assert "/web/places.js" in shell
    assert "/web/styles.css" in shell
    assert "/web/places.css" in shell
    # The page can go back to the shell, which links here in turn.
    assert 'href="/"' in shell
    assert "ps_csrf" in script
    assert "X-CSRF-Token" in script
    assert 'credentials: "same-origin"' in script
    assert "localStorage" not in script
    assert "sessionStorage" not in script
    assert "console." not in script
    assert "innerHTML" not in script
    assert "token=" not in script
    assert "csrf_token" not in script
    assert "错误详情包含本机路径" in script
    assert "PATH_LIKE" in script


def test_the_places_page_reaches_data_only_through_the_protected_api() -> None:
    _, script, _ = _places_documents()

    assert 'var API = "/api/v1";' in script
    assert script.count("fetch(") == script.count("fetch(API +")
    assert "sqlite" not in script.lower()
    assert "catalog.db" not in script


def test_the_places_page_never_asks_for_coordinates_or_a_stored_root_path() -> None:
    """Exact coordinates stay on the session-protected ``/geo`` endpoint only."""
    _, script, _ = _places_documents()

    assert "/geo" not in script
    assert "root_path" not in script
    assert "normalized_wgs84" not in script
    assert "latitude" not in script


def test_the_places_page_keeps_the_frozen_place_vocabulary() -> None:
    shell, script, _ = _places_documents()

    # Revision binding and the two "reload instead of mixing versions" prompts.
    assert "CATALOG_FILTER_REVISION_STALE" in script
    assert "PLACE_SUGGESTION_REVISION_STALE" in script
    assert "目录已变化，需重新加载" in script
    assert "建议集合已变化，需重新加载" in script
    # The frozen write vocabulary is used as-is, including the origin of a confirmation.
    assert "CONFIRMED_SUGGESTION" in script
    assert 'origin: "CONFIRMED_SUGGESTION",' in script
    assert "restore_auto" in script
    assert "PLACE_OPERATION_NOT_UNDOABLE" in script
    assert "PLACE_IDEMPOTENCY_CONFLICT" in script
    assert "PLACE_ASSIGNMENT_CONFLICT" in script
    assert "PLACE_BATCH_TOO_LARGE" in script
    assert "expected_revision: revision," in script
    assert "expected_operation_revision" in script
    assert "MAX_BATCH = 500" in script
    # A candidate is never shown as a conclusion.
    assert "SUGGESTION_ONLY" in script
    assert "冻结全部结果快照" in shell
    assert "asset_id 快照" in shell + script
    # Batch provenance is stated honestly instead of being silently upgraded.
    assert "批量确认记录的 origin 是" in shell
    assert "单条确认" in shell
    # The page admits that re-parenting is refused by the server instead of hiding it.
    assert "不提供改父级" in shell
