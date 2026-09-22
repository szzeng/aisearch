"""Exception handlers that turn every failure into the documented JSON envelope."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from photo_search.api.errors import (
    INTERNAL_ERROR,
    REQUEST_ID_HEADER,
    VALIDATION_ERROR,
    ApiError,
    Redactor,
    error_response,
    request_id_from,
    status_for_code,
)
from photo_search.catalog.errors import CatalogError


def install_exception_handlers(app: FastAPI) -> None:
    """Register the handlers in one place so no route can return a bare traceback."""
    app.add_exception_handler(CatalogError, _catalog_error)
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _unexpected_error)


def _request_id(request: Request) -> str:
    existing = getattr(request.state, "request_id", None)
    if existing:
        return str(existing)
    return request_id_from(request.headers.get(REQUEST_ID_HEADER))


def _redactor(request: Request) -> Redactor:
    context = getattr(request.app.state, "api", None)
    if context is None:  # pragma: no cover - only reachable for a foreign ASGI app
        return Redactor()
    return context.redactor()


async def _catalog_error(request: Request, error: CatalogError) -> JSONResponse:
    """Map a frozen catalog code to its HTTP status and redact every local path."""
    redactor = _redactor(request)
    details = redactor.details(error.details)
    if not isinstance(details, dict):
        details = {"value": details}
    return error_response(
        code=error.code,
        message=redactor.text(error.message),
        request_id=_request_id(request),
        status=status_for_code(error.code),
        details=details,
    )


async def _api_error(request: Request, error: ApiError) -> JSONResponse:
    redactor = _redactor(request)
    details = redactor.details(error.details)
    if not isinstance(details, dict):
        details = {"value": details}
    return error_response(
        code=error.code,
        message=redactor.text(error.message),
        request_id=_request_id(request),
        status=error.status,
        details=details,
        headers=error.headers,
        # ADR-0010 §8.2: a refusal that owns a frozen closure renders its ``details`` exactly
        # as frozen - including the empty object of ``INVALID_CURSOR``.
        closure_details=error.closure_details,
    )


async def _validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
    """Report *which* field is wrong without echoing the whole request back."""
    problems: list[dict[str, Any]] = []
    for item in error.errors():
        location = item.get("loc") or ()
        problems.append(
            {
                "loc": [str(part) for part in location],
                "msg": str(item.get("msg") or "invalid value"),
                "type": str(item.get("type") or "value_error"),
            }
        )
    return error_response(
        code=VALIDATION_ERROR,
        message="the request parameters are not valid",
        request_id=_request_id(request),
        status=422,
        details={"errors": problems},
    )


async def _unexpected_error(request: Request, error: Exception) -> JSONResponse:
    """Last resort: a structured 500. The traceback stays in the server log."""
    return error_response(
        code=INTERNAL_ERROR,
        message="the local API failed to handle this request",
        request_id=_request_id(request),
        details={"type": type(error).__name__},
    )
