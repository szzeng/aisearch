"""Local HTTP application: process health, the business API and the access boundary.

``create_app`` performs no I/O at all. The catalog, the derived-media cache and the single
background scan executor are opened lazily by ``ApiContext``, so importing this module (or
building an application for a test) can never create, migrate or lock a database.

``GET /api/v1/health`` keeps its P0-01 contract: no session, no catalog, no user data.
Everything else under ``/api/v1/`` is protected by ``SecurityMiddleware``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from photo_search.api.context import ApiContext
from photo_search.api.handlers import install_exception_handlers
from photo_search.api.routes import APP_VERSION, router
from photo_search.api.security import SecurityMiddleware
from photo_search.api.settings import ApiSettings
from photo_search.api.web import router as web_router
from photo_search.runtime.data_root_lease import acquire_data_root_lease

__all__ = ["APP_VERSION", "app", "create_app"]


def create_app(
    *,
    settings: ApiSettings | None = None,
    context: ApiContext | None = None,
    service_factory: Callable[[], Any] | None = None,
    runner_executor: Callable[[Any, int], Any] | None = None,
    encoder_factory: Callable[[], Any] | None = None,
    reranker_factory: Callable[[], Any] | None = None,
) -> FastAPI:
    """Build one application instance over the given local settings."""
    resolved = settings or ApiSettings.create()
    api = context or ApiContext(
        resolved,
        service_factory=service_factory,
        runner_executor=runner_executor,
        encoder_factory=encoder_factory,
        reranker_factory=reranker_factory,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # The same OS lease is acquired by backup only after this service has stopped.
        lease = acquire_data_root_lease(api.settings.data_root)
        try:
            yield
        finally:
            api.close()
            lease.close()

    application = FastAPI(
        title="Photo Search",
        version=APP_VERSION,
        openapi_url="/api/v1/openapi.json",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    application.state.api = api
    application.add_middleware(
        SecurityMiddleware, settings=resolved, sessions=api.sessions
    )
    install_exception_handlers(application)
    application.include_router(router)
    # The front-end shell and its assets sit outside ``/api/v1``: they carry no user data
    # and need no session, while every data request still goes through the guarded API.
    application.include_router(web_router)
    return application


#: The application the packaged launcher and ``uvicorn --factory`` style callers import.
app = create_app()
