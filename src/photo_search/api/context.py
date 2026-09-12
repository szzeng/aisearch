"""Per-application state: settings, session store, catalog service and scan runner.

``create_app`` must not touch the disk, so every resource here is opened lazily and a
*failed* open is deliberately not cached: a database that is momentarily too new, locked
or unreadable has to be re-checked on the next request instead of poisoning the process.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from photo_search.api.errors import Redactor
from photo_search.api.runner import ScanRunner
from photo_search.api.session import SessionManager
from photo_search.api.settings import ApiSettings, generate_session_token
from photo_search.ingestion.service import CatalogService

logger = logging.getLogger("photo_search.api")


class ApiContext:
    """Everything one live application instance owns."""

    def __init__(
        self,
        settings: ApiSettings,
        *,
        service_factory: Callable[[], CatalogService] | None = None,
        runner_executor: Callable[[Any, int], Any] | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.settings = settings
        token = settings.session_token or generate_session_token()
        self.sessions = SessionManager(
            token, token_source="settings" if settings.session_token else "generated"
        )
        self._service_factory = service_factory or self._default_service_factory
        self._runner_executor = runner_executor
        self._worker_id = worker_id
        self._service: CatalogService | None = None
        self._runner: ScanRunner | None = None

    def _default_service_factory(self) -> CatalogService:
        return CatalogService.open(data_root=self.settings.data_root)

    # ------------------------------------------------------------------ resources
    def service(self) -> CatalogService:
        """The catalog service of the request-handling thread (opened once)."""
        if self._service is None:
            self._service = self._service_factory()
        return self._service

    def runner(self) -> ScanRunner:
        """The single background scan executor; it opens its own service/connection."""
        if self._runner is None:
            self._runner = ScanRunner(
                self._service_factory, executor=self._runner_executor, worker_id=self._worker_id
            )
        return self._runner

    def redactor(self) -> Redactor:
        """Build the path redactor from whatever catalog state is currently readable."""
        try:
            service = self.service()
        except Exception:  # noqa: BLE001 - reporting must work even when the catalog is not
            return Redactor(data_root=self.settings.data_root)
        return Redactor.for_catalog(service.catalog, data_root=self.settings.data_root)

    def close(self) -> None:
        """Stop the worker and release both connections; safe to call twice."""
        runner = self._runner
        self._runner = None
        if runner is not None:
            try:
                runner.close()
            except Exception:  # pragma: no cover - shutdown must not raise
                logger.debug("stopping the scan runner failed", exc_info=True)
        service = self._service
        self._service = None
        if service is not None:
            try:
                service.close()
            except Exception:  # pragma: no cover - shutdown must not raise
                logger.debug("closing the catalog service failed", exc_info=True)
