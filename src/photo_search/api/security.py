"""The local access boundary as one ASGI middleware (architecture §10.3).

Applied in a single place so no route can accidentally opt out of it:

* ``Host`` must be a loopback spelling of this machine (or the explicitly allowed bind
  address), which defeats DNS rebinding;
* every ``/api/v1/*`` path except ``health`` and the one-time session exchange needs a
  live session cookie;
* a state-changing request additionally needs a matching ``Origin`` and a double-submit
  CSRF token, so a page in another origin cannot drive this API;
* every response carries ``X-Request-ID`` and every failure is a JSON envelope with that
  id, never a traceback.
"""

from __future__ import annotations

import hmac
import logging
from typing import Any
from urllib.parse import urlsplit

from starlette.datastructures import MutableHeaders
from starlette.requests import Request

from photo_search.api.errors import (
    CSRF_INVALID,
    HOST_NOT_ALLOWED,
    INTERNAL_ERROR,
    ORIGIN_NOT_ALLOWED,
    REQUEST_ID_HEADER,
    SESSION_REQUIRED,
    ApiError,
    error_response,
    request_id_from,
)
from photo_search.api.session import (
    CSRF_COOKIE,
    CSRF_HEADER,
    SESSION_COOKIE,
    Session,
    SessionManager,
)
from photo_search.api.settings import ApiSettings

logger = logging.getLogger("photo_search.api")

#: Paths that must work before a session exists. Everything else under ``/api/v1/`` is
#: session-protected, including the exported OpenAPI document.
UNPROTECTED_PATHS = frozenset({"/api/v1/health", "/api/v1/session"})
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
DEFAULT_PORTS = {"http": 80, "https": 443}


class SecurityMiddleware:
    """Pure ASGI middleware: uniform host, session, origin and CSRF enforcement."""

    def __init__(self, app: Any, *, settings: ApiSettings, sessions: SessionManager) -> None:
        self.app = app
        self.settings = settings
        self.sessions = sessions

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        request_id = request_id_from(request.headers.get(REQUEST_ID_HEADER))
        state = scope.setdefault("state", {})
        state["request_id"] = request_id

        failure = self._guard(request)
        if failure is not None:
            await error_response(
                code=failure.code,
                message=failure.message,
                request_id=request_id,
                status=failure.status,
                details=failure.details,
            )(scope, receive, send)
            return

        started = False

        async def tracked_send(message: dict[str, Any]) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                MutableHeaders(scope=message).setdefault(REQUEST_ID_HEADER, request_id)
            await send(message)

        try:
            await self.app(scope, receive, tracked_send)
        except Exception as error:  # noqa: BLE001 - one honest 500 envelope, never a traceback
            logger.exception(
                "unhandled API error request_id=%s type=%s", request_id, type(error).__name__
            )
            if started:
                raise
            await error_response(
                code=INTERNAL_ERROR,
                message="the local API failed to handle this request",
                request_id=request_id,
            )(scope, receive, send)

    # ------------------------------------------------------------------- guards
    def _guard(self, request: Request) -> ApiError | None:
        path = request.url.path
        if not path.startswith("/api/v1/") or path in UNPROTECTED_PATHS:
            return None
        host_error = self._check_host(request)
        if host_error is not None:
            return host_error
        session = self.sessions.lookup(request.cookies.get(SESSION_COOKIE))
        if session is None:
            return ApiError(
                SESSION_REQUIRED,
                "a local session cookie is required for this endpoint",
            )
        request.state.session = session
        if request.method.upper() not in MUTATING_METHODS:
            return None
        origin_error = self._check_origin(request)
        if origin_error is not None:
            return origin_error
        csrf_error = self._check_csrf(request, session)
        if csrf_error is not None:
            return csrf_error
        return None

    def _check_host(self, request: Request) -> ApiError | None:
        raw = request.headers.get("host")
        parsed = split_host_port(raw)
        if parsed is None:
            return ApiError(HOST_NOT_ALLOWED, "the Host header is missing or malformed")
        hostname, port = parsed
        if hostname not in self.settings.effective_allowed_hosts():
            return ApiError(
                HOST_NOT_ALLOWED,
                "this API only answers requests addressed to the local machine",
                details={"host": hostname},
            )
        actual_port = _server_port(request)
        host_port = port if port is not None else _default_port(request.url.scheme)
        if host_port != actual_port:
            return ApiError(
                HOST_NOT_ALLOWED,
                "the Host header port does not belong to this server",
                details={"port": port},
            )
        return None

    def _check_origin(self, request: Request) -> ApiError | None:
        origin = request.headers.get("origin")
        if not origin:
            return ApiError(
                ORIGIN_NOT_ALLOWED,
                "state-changing requests must send an Origin header",
            )
        parsed = urlsplit(str(origin))
        scheme = (parsed.scheme or "").lower()
        hostname = (parsed.hostname or "").lower()
        if scheme not in DEFAULT_PORTS or not hostname:
            return ApiError(
                ORIGIN_NOT_ALLOWED,
                "the Origin header is not a usable http(s) origin",
                details={"origin": str(origin)},
            )
        if hostname not in self.settings.effective_allowed_hosts():
            return ApiError(
                ORIGIN_NOT_ALLOWED,
                "cross-origin requests are not accepted",
                details={"origin": str(origin)},
            )
        origin_port = parsed.port if parsed.port is not None else _default_port(scheme)
        if origin_port != _server_port(request):
            return ApiError(
                ORIGIN_NOT_ALLOWED,
                "the Origin header port does not belong to this server",
                details={"origin": str(origin)},
            )
        return None

    def _check_csrf(self, request: Request, session: Session) -> ApiError | None:
        cookie = request.cookies.get(CSRF_COOKIE)
        header = request.headers.get(CSRF_HEADER)
        if not cookie or not header:
            return ApiError(
                CSRF_INVALID,
                "this request is missing its CSRF token",
            )
        if not hmac.compare_digest(str(cookie), session.csrf_token) or not hmac.compare_digest(
            str(header), session.csrf_token
        ):
            return ApiError(CSRF_INVALID, "the CSRF token does not match this session")
        return None


def split_host_port(raw: str | None) -> tuple[str, int | None] | None:
    """Split a ``Host`` header into a lower-case hostname and an optional port."""
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    host = text
    port: int | None = None
    if text.startswith("["):
        end = text.find("]")
        if end < 0:
            return None
        host = text[1:end]
        remainder = text[end + 1 :]
        if remainder.startswith(":"):
            port = _int_or_none(remainder[1:])
            if port is None:
                return None
        elif remainder:
            return None
    elif ":" in text:
        host, _, raw_port = text.rpartition(":")
        if not host:
            return None
        port = _int_or_none(raw_port)
        if port is None:
            return None
    if not host:
        return None
    return host.strip().strip("[]").lower(), port


def _int_or_none(text: str) -> int | None:
    try:
        value = int(str(text))
    except (TypeError, ValueError):
        return None
    return value if 0 <= value <= 65_535 else None


def _server_port(request: Request) -> int:
    server = request.scope.get("server") or ()
    if len(server) >= 2 and isinstance(server[1], int):
        return int(server[1])
    return int(DEFAULT_PORTS.get(str(request.url.scheme).lower(), 80))


def _default_port(scheme: str) -> int:
    """Return the effective port when an HTTP authority omits it."""
    return int(DEFAULT_PORTS.get(str(scheme).lower(), 80))
