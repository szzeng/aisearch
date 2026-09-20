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
    PERSONS_NAME,
    PLACES_NAME,
    SEARCH_NAME,
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


# ---------------------------------------------------------------------------------------
# P2-07: the search page. Same package-data rules as the two documents above, plus one
# assertion per promise the page makes about *how* it shows a search result.
# ---------------------------------------------------------------------------------------
SEARCH_DOCUMENTS = ("search.html", "search.js", "search.css")


def _search_documents() -> tuple[str, str, str]:
    shell = (read_static(SEARCH_NAME) or b"").decode("utf-8")
    script = (read_static("search.js") or b"").decode("utf-8")
    style = (read_static("search.css") or b"").decode("utf-8")
    assert shell and script and style
    return shell, script, style


@pytest.mark.parametrize("relative", SEARCH_DOCUMENTS)
def test_the_search_page_assets_are_packaged(relative: str) -> None:
    payload = read_static(relative)
    assert payload and len(payload) > 0


def test_the_search_page_route_never_shadows_the_search_api() -> None:
    """``/search`` is a document; the results still come from ``/api/v1/search``."""
    from photo_search.api.routes import router as api_router
    from photo_search.api.web import router as web_router

    api_paths = {route.path for route in api_router.routes}
    web_paths = {route.path for route in web_router.routes}
    assert SEARCH_NAME == "search.html"
    assert "/search" in web_paths
    assert "/api/v1/search" in api_paths
    assert "/api/v1/search/image" in api_paths
    assert "/search" not in api_paths


def test_the_search_page_has_no_external_reference_or_credential() -> None:
    shell, script, style = _search_documents()

    for document in (shell, script, style):
        assert "http://" not in document
        assert "https://" not in document
    assert 'lang="zh-CN"' in shell
    assert "/web/search.js" in shell
    assert "/web/styles.css" in shell
    assert "/web/search.css" in shell
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


def test_the_search_page_reaches_data_only_through_the_protected_api() -> None:
    _, script, _ = _search_documents()

    assert 'var API = "/api/v1";' in script
    assert script.count("fetch(") == script.count("fetch(API +")
    assert "sqlite" not in script.lower()
    assert "catalog.db" not in script
    # The exact endpoints this page is allowed to use.
    for path in ("/search", "/search/image", "/capabilities", "/places/coverage", "/stats", "/sources"):
        assert '"' + path in script
    # Exact coordinates stay on the session-protected per-asset /geo endpoint only.
    assert "/geo" not in script
    assert "latitude" not in script
    assert "normalized_wgs84" not in script


def test_the_search_page_keeps_the_frozen_hit_reason_vocabulary() -> None:
    shell, script, _ = _search_documents()

    # The four frozen reasons are shown verbatim, never re-worded into a score.
    for reason in ("TEXT:", "SEMANTIC:", '"TEXT+SEMANTIC"', "FILTER_ONLY"):
        assert reason in script
    assert "命中原因" in shell + script
    assert "matched_text" in script
    assert "semantic_ready" in script
    assert "hit_reason" in script
    # The page never reads a raw score, so it cannot turn one into an accuracy.
    assert ".score" not in script
    assert "reranker_score" not in script
    assert "toFixed" not in script
    assert "不把它当成准确率" in script
    assert "整体 AI 准确率" in script


def test_the_search_page_states_every_non_success_state_instead_of_downgrading_it() -> None:
    _, script, _ = _search_documents()

    # A missing encoder must be said out loud, in both of its two spellings.
    assert "语义通道未运行" in script
    assert "QUERY_ENCODER_UNAVAILABLE" in script
    assert "IMAGE_ENCODER_UNAVAILABLE" in script
    assert "RUNTIME_CAPABILITY_UNAVAILABLE" in script
    assert "没有用文本通道或「零命中」冒充结果" in script
    # Session/cursor/revision refusals keep the query and offer a re-run.
    for code in (
        "SEARCH_SESSION_EXPIRED",
        "INVALID_CURSOR",
        "PROFILE_GENERATION_MISMATCH",
        "CATALOG_FILTER_REVISION_STALE",
        "PLACE_SUGGESTION_REVISION_STALE",
    ):
        assert code in script
    assert "重新搜索" in script
    assert "query-retry" in script
    # Lagging projection and truncation are reported with their counts.
    assert "语义索引仍在追赶" in script
    assert "pending_semantic" in script
    assert "search_projection" in script
    assert "文本投影仍在追赶" in script
    assert "candidates_truncated" in script
    assert "truncated" in script


def test_the_search_page_keeps_the_p2_03_place_filter_semantics() -> None:
    shell, script, _ = _search_documents()

    for field in ("place_ids", "include_descendants", "place_state"):
        assert field in script
    # The page never mints a revision: the search session binds the authoritative ones.
    assert "suggestion_revision" not in script
    assert "catalog_filter_revision" not in script
    assert "suggested" in shell
    assert "人工未归类" in shell
    # A machine candidate is never presented as an effective place.
    assert "机器候选不参与有效地点过滤" in shell + script


def test_the_search_page_uploads_without_adding_anything_to_the_catalog() -> None:
    shell, script, _ = _search_documents()

    assert "FormData" in script
    assert 'form.append("image"' in script
    # The browser owns the multipart boundary; the page never sets Content-Type by hand.
    assert 'headers["Content-Type"] = "application/json";' in script
    assert "multipart/form-data" not in script
    assert "不新增资产" in shell
    assert "临时目录" in shell
    assert "UPLOAD_TOO_LARGE" in script
    assert "UPLOAD_TOO_MANY_PIXELS" in script
    assert "FORMAT_UNSUPPORTED" in script
    assert "DECODE_FAILED" in script
    # A refused upload is never reported as a success: the submit handler branches on the
    # outcome the search returned instead of assuming the request resolved.
    assert "promise.then(function (succeeded) {" in script
    assert "上传图查询没有成功" in script


def test_the_search_page_reports_coverage_by_category_and_never_as_one_accuracy() -> None:
    shell, script, _ = _search_documents()

    assert "分项覆盖率" in shell
    for title in (
        "文本投影覆盖",
        "语义索引覆盖",
        "地点覆盖",
        "来源可用性",
    ):
        assert title in script
    # Every card states its own basis instead of being folded into one number.
    assert "口径：" in script
    assert "本页不把任何一个换算成「整体 AI 准确率」" in script


def test_the_three_documents_navigate_to_each_other() -> None:
    shell, _, _ = _web_documents()
    places_shell = (read_static(PLACES_NAME) or b"").decode("utf-8")
    search_shell, _, _ = _search_documents()

    assert 'href="/search"' in shell
    assert 'href="/search"' in places_shell
    assert 'href="/"' in search_shell
    assert 'href="/places"' in search_shell


# ---------------------------------------------------------------------------------------
# P3-06: the person-curation page. Same package-data rules as the three documents above, plus
# one assertion per promise the page makes about the frozen person/face/split surface.
# ---------------------------------------------------------------------------------------
PERSONS_DOCUMENTS = ("persons.html", "persons.js", "persons.css")


def _persons_documents() -> tuple[str, str, str]:
    shell = (read_static(PERSONS_NAME) or b"").decode("utf-8")
    script = (read_static("persons.js") or b"").decode("utf-8")
    style = (read_static("persons.css") or b"").decode("utf-8")
    assert shell and script and style
    return shell, script, style


@pytest.mark.parametrize("relative", PERSONS_DOCUMENTS)
def test_the_persons_page_assets_are_packaged(relative: str) -> None:
    payload = read_static(relative)
    assert payload and len(payload) > 0


def test_the_persons_page_route_never_shadows_the_person_api() -> None:
    """``/persons`` is a document; the data still comes from ``/api/v1``."""
    from photo_search.api.routes import router as api_router
    from photo_search.api.web import router as web_router

    api_paths = {route.path for route in api_router.routes}
    web_paths = {route.path for route in web_router.routes}
    assert PERSONS_NAME == "persons.html"
    assert "/persons" in web_paths
    assert "/api/v1/persons" in api_paths
    assert "/api/v1/faces" in api_paths
    assert "/api/v1/faces/{face_id}/thumbnail" in api_paths
    assert "/api/v1/person-operations" in api_paths
    assert "/persons" not in api_paths


def test_the_persons_page_has_no_external_reference_credential_or_html_sink() -> None:
    shell, script, style = _persons_documents()

    for document in (shell, script, style):
        assert "http://" not in document
        assert "https://" not in document
    assert 'lang="zh-CN"' in shell
    assert "/web/persons.js" in shell
    assert "/web/styles.css" in shell
    assert "/web/persons.css" in shell
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
    # The page states what it will not do instead of hiding it.
    assert "本页不提供的动作" in shell
    assert "不触发任何模型推理" in shell


def test_the_persons_page_reaches_data_only_through_the_protected_api() -> None:
    _, script, _ = _persons_documents()

    assert 'var API = "/api/v1";' in script
    assert script.count("fetch(") == script.count("fetch(API +")
    assert "sqlite" not in script.lower()
    assert "catalog.db" not in script
    # Exactly the endpoints this page is allowed to use.
    for path in (
        "/persons",
        "/faces",
        "/face-assignments/batch",
        "/person-operations",
        "/capabilities",
    ):
        assert '"' + path in script, path
    # The avatar URL is always the one the server handed out, never one the page builds.
    assert "image.src = person.thumbnail_url;" in script
    assert "image.src = face.thumbnail_url;" in script
    # No coordinates, no stored root path, no private field name.
    for fragment in (
        "/geo",
        "latitude",
        "normalized_wgs84",
        "root_path",
        "bbox",
        "landmark",
        "vector",
        "similarity",
        "confidence",
        "name_key",
        "snapshot",
    ):
        assert fragment not in script, fragment


def test_the_persons_page_keeps_the_frozen_person_vocabulary() -> None:
    shell, script, _ = _persons_documents()

    # Every refusal the page can meet is shown with the server's own code.
    for code in (
        "PERSON_REVISION_CONFLICT",
        "PERSON_IDEMPOTENCY_CONFLICT",
        "PERSON_NOT_FOUND",
        "PERSON_REDIRECT_CYCLE",
        "PERSON_SPLIT_CONFLICT",
        "FACE_ASSIGNMENT_CONFLICT",
        "FACE_IDEMPOTENCY_CONFLICT",
        "FACE_STATE_CONFLICT",
        "FACE_NOT_FOUND",
        "FACE_OPERATION_NOT_UNDOABLE",
        "PERSON_OPERATION_NOT_UNDOABLE",
        "INVALID_CURSOR",
    ):
        assert code in script, code
    # The frozen contract versions and the write vocabulary are used as-is.
    assert "p3-person-split-contracts.v1" in script
    assert "p3-face-correction-contracts.v1" in script
    assert "MAX_BATCH = 500" in script
    assert "restore_auto" in script
    assert '"assign"' in script
    assert '"unassign"' in script
    assert "expected_revision" in script
    assert "source_expected_revision" in script
    assert "expected_control_revision" in script
    assert "expected_assignment_revision" in script
    assert "expected_operation_revision" in script
    assert "request_id" in script
    # The three views, the human lock vocabulary and the honest non-claims.
    assert "manual_unassigned" in script
    assert "MANUAL_UNASSIGNED" in shell + script
    assert "同一张照片的其他脸不会被自动带入" in shell
    assert "不会因为同一张照片的其它脸而联动" in shell
    assert "同名合并" in shell + script
    assert "能否撤销由服务器的冲突矩阵" in shell
    assert "缺模型" in shell + script or "缺少模型权重" in shell
    assert "服务端按权威人脸框裁剪" in shell


def test_the_navigation_offers_the_persons_page_only_when_the_read_layer_exists() -> None:
    """The entry is hidden by default and revealed from ``/capabilities``."""
    shell, script, _ = _web_documents()
    places_shell = (read_static(PLACES_NAME) or b"").decode("utf-8")
    search_shell = (read_static(SEARCH_NAME) or b"").decode("utf-8")
    places_script = (read_static("places.js") or b"").decode("utf-8")
    search_script = (read_static("search.js") or b"").decode("utf-8")
    persons_shell = (read_static(PERSONS_NAME) or b"").decode("utf-8")

    for document in (shell, places_shell, search_shell):
        assert 'id="nav-persons" href="/persons" hidden' in document
    for code in (script, places_script, search_script):
        assert "function revealPersonsNav(body)" in code
        assert 'link.hidden = !usable;' in code
        # The gate is the schema the person read layer needs, not the presence of a model.
        assert "version >= 13" in code
        assert 'schema_status' in code
    # The page itself navigates back to the other three documents.
    assert 'href="/"' in persons_shell
    assert 'href="/search"' in persons_shell
    assert 'href="/places"' in persons_shell
