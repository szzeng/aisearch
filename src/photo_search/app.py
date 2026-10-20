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
from photo_search.api.map_api import router as map_router
from photo_search.api.routes import APP_VERSION, router
from photo_search.api.security import SecurityMiddleware
from photo_search.api.settings import ApiSettings
from photo_search.api.web import router as web_router
from photo_search.runtime.data_root_lease import DataRootLease, acquire_data_root_lease

__all__ = ["APP_VERSION", "app", "create_app"]


def create_app(
    *,
    settings: ApiSettings | None = None,
    context: ApiContext | None = None,
    service_factory: Callable[[], Any] | None = None,
    runner_executor: Callable[[Any, int], Any] | None = None,
    encoder_factory: Callable[[], Any] | None = None,
    reranker_factory: Callable[[], Any] | None = None,
    refresher_executor: Callable[[Any, Any, Any], Any] | None = None,
    refresh_manifest_path: str | None = None,
    refresh_device: str | None = None,
    refresh_device_policy: str = "cpu_allowed",
    refresh_batch_size: int | None = None,
    refresh_enrich: bool = True,
    startup_lease: Any | None = None,
) -> FastAPI:
    """Build one application instance over the given local settings."""
    resolved = settings or ApiSettings.create()
    api = context or ApiContext(
        resolved,
        service_factory=service_factory,
        runner_executor=runner_executor,
        encoder_factory=encoder_factory,
        reranker_factory=reranker_factory,
        refresher_executor=refresher_executor,
        refresh_manifest_path=refresh_manifest_path,
        refresh_device=refresh_device,
        refresh_device_policy=refresh_device_policy,
        refresh_batch_size=refresh_batch_size,
        refresh_enrich=refresh_enrich,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Offline profile selection may transfer its actual lease into this lifespan so there
        # is no unlock/relock window between pointer activation and serving the new profile.
        owns_lease = startup_lease is None
        lease = acquire_data_root_lease(api.settings.data_root) if owns_lease else startup_lease
        if not owns_lease and (
            not isinstance(lease, DataRootLease) or getattr(lease, "_stream", None) is None
        ):
            raise RuntimeError("the startup data-root lease is not live")
        api.set_data_root_lease(lease)
        try:
            api.embedding_candidate_manager().recover_after_lock()
            # Once OS ownership proves the prior managed process is gone, old smart work
            # cannot still publish. Database/model work stays lazy and failures are retryable.
            api.recover_smart_refreshes_after_lock()
            api.resume_retention_cleanup_after_lock()
            api.start_automatic_retention_cleanup()
            # Warm only the first-screen covers in a separate daemon worker. The existing
            # thumbnail cache is reused and a cold cache never delays API startup.
            api.start_place_cover_warming()
            yield
        finally:
            api.close()
            api.set_data_root_lease(None)
            if owns_lease:
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
    application.add_middleware(SecurityMiddleware, settings=resolved, sessions=api.sessions)
    install_exception_handlers(application)
    application.include_router(router)
    application.include_router(map_router)
    # The front-end shell and its assets sit outside ``/api/v1``: they carry no user data
    # and need no session, while every data request still goes through the guarded API.
    application.include_router(web_router)
    return application


#: The application the packaged launcher and ``uvicorn --factory`` style callers import.
app = create_app()
