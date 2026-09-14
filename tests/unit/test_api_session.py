"""Unit tests for the session credential, host parsing, ranges and path presentation."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import photo_search.api.session as session_module
from photo_search.api.errors import (
    ACCESS_DENIED,
    ApiError,
    Redactor,
    error_payload,
    request_id_from,
    status_for_code,
)
from photo_search.api.files import (
    artifact_etag,
    content_disposition,
    media_type_for,
    original_etag,
    parse_range,
    safe_display_filename,
)
from photo_search.api.security import split_host_port
from photo_search.api.session import (
    CREDENTIAL_SCHEMA,
    SessionManager,
    bootstrap_session_credential,
    restrict_credential_file,
)
from photo_search.api.settings import ApiSettings, BindNotAllowed, is_loopback_host
from photo_search.config import session_credential_path

TOKEN = "bootstrap-token-value"


def test_exchange_burns_the_credential_and_issues_one_session() -> None:
    manager = SessionManager(TOKEN)

    assert manager.exchange("wrong-token") is None
    assert manager.exchange(None) is None
    assert manager.bootstrap_used is False

    session = manager.exchange(TOKEN)
    assert session is not None
    assert session.session_id != session.csrf_token
    assert manager.bootstrap_used is True
    assert manager.active_session_count == 1

    # Single use: the same credential can never open a second session.
    assert manager.exchange(TOKEN) is None
    assert manager.lookup(session.session_id) is session
    assert manager.revoke(session.session_id) is True
    assert manager.lookup(session.session_id) is None


def test_session_ids_are_unpredictable_and_distinct() -> None:
    manager = SessionManager(TOKEN)
    first = manager.exchange(TOKEN)
    assert first is not None
    second_manager = SessionManager(TOKEN)
    second = second_manager.exchange(TOKEN)
    assert second is not None
    assert first.session_id != second.session_id
    assert len(first.session_id) >= 32


def test_bootstrap_writes_a_credential_file_under_the_data_root(tmp_path: Path) -> None:
    data_root = tmp_path / "PhotoSearchData"
    bootstrap = bootstrap_session_credential(ApiSettings(data_root=data_root))

    assert bootstrap.source == "file"
    path = session_credential_path(data_root)
    assert path == bootstrap.path
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema"] == CREDENTIAL_SCHEMA
    assert payload["token"] == bootstrap.token
    assert payload["token"] != ""
    # The credential never lives inside a source directory or an install directory.
    assert path.parent.name == "runtime"
    assert bootstrap.permission


def test_bootstrap_rotates_the_credential_under_the_same_data_root(tmp_path: Path) -> None:
    """A restarted launcher replaces, rather than reuses, its bootstrap credential."""
    data_root = tmp_path / "PhotoSearchData"
    first = bootstrap_session_credential(ApiSettings(data_root=data_root))
    second = bootstrap_session_credential(ApiSettings(data_root=data_root))

    path = session_credential_path(data_root)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert first.token != second.token
    assert payload["token"] == second.token


def test_windows_credential_acl_retains_delete_for_atomic_rotation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The restricted DACL still permits replacing and later removing the credential."""
    calls: list[list[str]] = []

    def fake_run(arguments: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(arguments)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(
        session_module,
        "os",
        SimpleNamespace(name="nt", environ={"USERNAME": "photo-search-user"}),
    )
    monkeypatch.setattr(session_module.subprocess, "run", fake_run)

    assert restrict_credential_file(tmp_path / "session.json") == "windows-restricted-acl"
    assert calls == [
        [
            "icacls",
            str(tmp_path / "session.json"),
            "/inheritance:r",
            "/grant:r",
            "photo-search-user:(R,W,D)",
        ]
    ]


def test_explicit_token_override_does_not_write_a_file(tmp_path: Path) -> None:
    data_root = tmp_path / "PhotoSearchData"
    settings = ApiSettings(data_root=data_root, session_token="fixed-token")

    bootstrap = bootstrap_session_credential(settings)

    assert bootstrap.token == "fixed-token"
    assert bootstrap.source == "override"
    assert bootstrap.path is None
    assert not session_credential_path(data_root).exists()


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("localhost", True),
        ("::1", True),
        ("[::1]", True),
        ("192.168.1.10", False),
        ("0.0.0.0", False),
        ("example.com", False),
        ("", False),
    ],
)
def test_loopback_detection(host: str, expected: bool) -> None:
    assert is_loopback_host(host) is expected


def test_bind_policy_requires_an_explicit_non_loopback_switch() -> None:
    loopback = ApiSettings(data_root=Path("/tmp"), bind_host="127.0.0.1")
    loopback.validate_bind()
    assert loopback.loopback_only is True

    exposed = ApiSettings(data_root=Path("/tmp"), bind_host="0.0.0.0")
    with pytest.raises(BindNotAllowed):
        exposed.validate_bind()

    allowed = ApiSettings(
        data_root=Path("/tmp"), bind_host="0.0.0.0", allow_non_loopback=True
    )
    allowed.validate_bind()
    assert allowed.browser_host() == "127.0.0.1"


def test_allowed_hosts_are_loopback_only_by_default() -> None:
    settings = ApiSettings(data_root=Path("/tmp"))
    hosts = settings.effective_allowed_hosts()
    assert "127.0.0.1" in hosts
    assert "localhost" in hosts
    assert "evil.example" not in hosts


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("127.0.0.1", ("127.0.0.1", None)),
        ("127.0.0.1:8000", ("127.0.0.1", 8000)),
        ("[::1]:8000", ("::1", 8000)),
        ("[::1]", ("::1", None)),
        ("LOCALHOST:80", ("localhost", 80)),
        ("", None),
        ("127.0.0.1:not-a-port", None),
        ("[::1", None),
    ],
)
def test_split_host_port(raw: str, expected: tuple[str, int | None] | None) -> None:
    assert split_host_port(raw) == expected


def test_redactor_replaces_registered_roots_and_the_data_root() -> None:
    redactor = Redactor(
        source_roots=[r"D:\Photos", r"D:\Photos\Sub"],
        data_root=r"C:\Users\me\AppData\Local\PhotoSearchData",
    )

    text = redactor.text(
        r"cannot read D:\Photos\2024\a.jpg under C:\Users\me\AppData\Local\PhotoSearchData"
    )
    assert "D:\\Photos" not in text
    assert "C:\\Users\\me" not in text
    assert "<source_root>\\2024\\a.jpg" in text
    assert "<data_root>" in text
    assert redactor.details({"path": r"D:\Photos\b.jpg"}) == {"path": "<source_root>\\b.jpg"}


def test_error_payload_shape_and_status_mapping() -> None:
    payload = error_payload(
        code="SCHEMA_TOO_NEW", message="too new", request_id="abc123", details={"a": 1}
    )
    assert payload == {
        "code": "SCHEMA_TOO_NEW",
        "message": "too new",
        "request_id": "abc123",
        "details": {"a": 1},
    }
    assert status_for_code("ASSET_NOT_FOUND") == 404
    assert status_for_code("SCHEMA_TOO_NEW") == 409
    assert status_for_code("INVALID_PATH") == 422
    assert status_for_code("SESSION_REQUIRED") == 401
    assert status_for_code("NOT_A_REAL_CODE") == 500


def test_request_id_is_sanitised_or_generated() -> None:
    assert request_id_from("abc-123") == "abc-123"
    assert request_id_from("bad value") != "bad value"
    assert request_id_from("x" * 200) != "x" * 200
    assert request_id_from(None) and len(request_id_from(None)) == 16
    assert request_id_from("") and len(request_id_from("")) == 16


def test_parse_range_accepts_one_rfc_range_and_refuses_the_rest() -> None:
    assert parse_range(None, size=100) is None
    assert parse_range("items=0-1", size=100) is None

    assert parse_range("bytes=0-9", size=100).length == 10
    assert parse_range("bytes=90-", size=100).start == 90
    assert parse_range("bytes=90-500", size=100).end == 99
    assert parse_range("bytes=-10", size=100).start == 90
    assert parse_range("bytes=-1000", size=100).start == 0

    with pytest.raises(ApiError) as failure:
        parse_range("bytes=0-9,20-29", size=100)
    assert failure.value.code == "RANGE_NOT_SATISFIABLE"
    assert failure.value.status == 416

    with pytest.raises(ApiError):
        parse_range("bytes=100-200", size=100)
    with pytest.raises(ApiError):
        parse_range("bytes=9-1", size=100)
    with pytest.raises(ApiError):
        parse_range("bytes=abc-def", size=100)
    with pytest.raises(ApiError):
        parse_range("bytes=0-9", size=0)


def test_display_filename_never_carries_a_separator() -> None:
    assert safe_display_filename(r"a\b\photo.jpg", "fallback") == "photo.jpg"
    assert safe_display_filename("a/b/中文 名称.jpg", "fallback") == "中文 名称.jpg"
    assert safe_display_filename(r"..\..\secret.txt", "fallback") == "secret.txt"
    assert safe_display_filename("..", "fallback") == "fallback"
    assert safe_display_filename("", "fallback") == "fallback"
    assert safe_display_filename('we"ird.jpg', "fallback") == "we_ird.jpg"

    header = content_disposition("中文 名称.jpg")
    assert header.startswith("inline; filename=")
    assert 'filename="' in header
    assert "filename*=UTF-8''" in header
    assert "/" not in header and "\\" not in header


def test_media_type_and_etag_are_stable() -> None:
    assert media_type_for(r"D:\lib\a.JPG") == "image/jpeg"
    assert media_type_for(r"D:\lib\a.png") == "image/png"
    assert media_type_for(r"D:\lib\a.unknown") == "application/octet-stream"
    assert artifact_etag("catalog-thumbnail@1", "a" * 64) == f'"catalog-thumbnail@1:{"a" * 64}"'
    assert original_etag("a" * 64, 10, 20) == f'"{"a" * 64}:10:20"'


def test_access_denied_codes_map_to_403() -> None:
    assert status_for_code(ACCESS_DENIED) == 403
