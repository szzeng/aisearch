"""Per-application state: settings, session store, catalog service and scan runner.

``create_app`` must not touch the disk, so every resource here is opened lazily and a
*failed* open is deliberately not cached: a database that is momentarily too new, locked
or unreadable has to be re-checked on the next request instead of poisoning the process.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from photo_search.api.errors import Redactor
from photo_search.api.refresher import LibraryRefresher
from photo_search.api.runner import ScanRunner
from photo_search.api.session import SessionManager
from photo_search.api.settings import ApiSettings, generate_session_token
from photo_search.catalog.errors import CatalogError
from photo_search.ingestion.service import CatalogService
from photo_search.search.embedding import QueryEncoder
from photo_search.search.sessions import SearchSessionStore

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
        encoder_factory: Callable[[], QueryEncoder] | None = None,
        reranker_factory: Callable[[], Any] | None = None,
        refresher_executor: Callable[[Any, Any, Any], Any] | None = None,
        refresh_manifest_path: str | None = None,
        refresh_device: str | None = None,
        refresh_device_policy: str = "cpu_allowed",
        refresh_batch_size: int | None = None,
        refresh_enrich: bool = True,
    ) -> None:
        self._adapter_lock = threading.RLock()
        self._search_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="photo-search")
        self._search_slots = threading.BoundedSemaphore(4)
        self._search_state = threading.local()
        self._closed = False
        self.settings = settings
        token = settings.session_token or generate_session_token()
        self.sessions = SessionManager(
            token, token_source="settings" if settings.session_token else "generated"
        )
        self._service_factory = service_factory or self._default_service_factory
        self._runner_executor = runner_executor
        self._worker_id = worker_id
        #: P2-06A: search sessions are process-local by design (architecture §9.5). A
        #: restart drops them, and a continuation token then answers SEARCH_SESSION_EXPIRED.
        self.search_sessions = SearchSessionStore()
        self._encoder_factory = encoder_factory
        self._encoder: QueryEncoder | None = None
        self._encoder_resolved = False
        #: P2-06B: the optional reranker adapter, injected exactly like the query encoder.
        #: This build ships none, and a request that asks for a rerank is then answered with
        #: the base page plus ``rerank_state=UNAVAILABLE`` instead of a silent no-op.
        self._reranker_factory = reranker_factory
        self._reranker: Any = None
        self._reranker_resolved = False
        self._boundary_worker = None
        self._service: CatalogService | None = None
        self._runner: ScanRunner | None = None
        #: P4-02Y: the single background library refresh. It reuses the *same* ADR-0003 §7
        #: adapter as the query path (never a second model instance), resolves its own
        #: connection inside its worker thread, and reports through ``photo_search.refresh``.
        self._refresher_executor = refresher_executor
        self._refresh_manifest_path = refresh_manifest_path
        self._refresh_device = refresh_device
        self._refresh_device_policy = str(refresh_device_policy)
        self._refresh_batch_size = refresh_batch_size
        self._refresh_enrich = bool(refresh_enrich)
        self._refresher: LibraryRefresher | None = None

    def _default_service_factory(self) -> CatalogService:
        return CatalogService.open(data_root=self.settings.data_root)

    # ------------------------------------------------------------------ resources
    def service(self) -> CatalogService:
        """The catalog service of the request-handling thread (opened once)."""
        if getattr(self._search_state, "active", False):
            if not hasattr(self._search_state, "service"):
                self._search_state.service = self._service_factory()
            return self._search_state.service
        if self._service is None:
            self._service = self._service_factory()
            catalog = getattr(self._service, "catalog", None)
            if catalog is not None and hasattr(catalog, "db_path"):
                from photo_search.geo.worker import BoundaryWorker
                self._boundary_worker = BoundaryWorker(data_root=catalog.data_root, db_path=catalog.db_path)
        return self._service

    def runner(self) -> ScanRunner:
        """The single background scan executor; it opens its own service/connection."""
        if self._runner is None:
            self._runner = ScanRunner(
                self._service_factory, executor=self._runner_executor, worker_id=self._worker_id
            )
        return self._runner

    def refresher(self) -> LibraryRefresher:
        """The single background library refresh; one active refresh per process."""
        if self._refresher is None:
            from photo_search.ingestion.scanner import DEFAULT_OBSERVATION_BATCH

            self._refresher = LibraryRefresher(
                self._service_factory,
                encoder_provider=self.query_encoder,
                manifest_path=self._refresh_manifest_path,
                device=self._refresh_device,
                device_policy=self._refresh_device_policy,
                batch_size=(
                    DEFAULT_OBSERVATION_BATCH
                    if self._refresh_batch_size is None
                    else int(self._refresh_batch_size)
                ),
                enrich=self._refresh_enrich,
                executor=self._refresher_executor,
            )
        return self._refresher

    def refresh_manifest_path(self) -> str | None:
        """The pinned manifest the refresh (and the source card's progress) is bound to."""
        return self._refresh_manifest_path

    def refresh_device_policy(self) -> str:
        """The device policy of the refresh; the source card renders the same binding."""
        return self._refresh_device_policy

    def redactor(self) -> Redactor:
        """Build the path redactor from whatever catalog state is currently readable."""
        try:
            service = self.service()
        except Exception:  # noqa: BLE001 - reporting must work even when the catalog is not
            return Redactor(data_root=self.settings.data_root)
        return Redactor.for_catalog(service.catalog, data_root=self.settings.data_root)

    async def run_search(self, operation: Callable[[], Any]) -> Any:
        """Bound queued work and keep model/SQLite waits off the event loop."""
        if self._closed or not self._search_slots.acquire(blocking=False):
            raise CatalogError("SEARCH_BUSY", "搜索正在处理，请稍后重试")
        try:
            future = self._search_executor.submit(self._execute_search, operation)
        except BaseException:
            self._search_slots.release()
            raise
        # Releasing on actual completion also covers a disconnected/cancelled HTTP caller.
        future.add_done_callback(lambda _: self._search_slots.release())
        return await asyncio.wrap_future(future)

    def _execute_search(self, operation: Callable[[], Any]) -> Any:
        self._search_state.active = True
        try:
            return operation()
        finally:
            service = getattr(self._search_state, "service", None)
            if service is not None:
                service.close()
                del self._search_state.service
            self._search_state.active = False

    def query_encoder(self) -> QueryEncoder | None:
        """Cache a successful initialization; temporary failures can be retried."""
        with self._adapter_lock:
            if self._closed:
                return None
            if not self._encoder_resolved and self._encoder_factory is not None:
                try:
                    self._encoder = self._encoder_factory()
                    self._encoder_resolved = self._encoder is not None
                except Exception:
                    logger.warning("building the query encoder failed", exc_info=True)
            return self._encoder

    def reranker(self) -> Any:
        with self._adapter_lock:
            if self._closed:
                return None
            if not self._reranker_resolved and self._reranker_factory is not None:
                try:
                    self._reranker = self._reranker_factory()
                    self._reranker_resolved = self._reranker is not None
                except Exception:
                    logger.warning("building the reranker failed", exc_info=True)
            return self._reranker

    def close(self) -> None:
        """Stop the worker, the query encoder and both connections; safe to call twice."""
        if self._closed:
            return
        self._closed = True
        if self._boundary_worker is not None:
            self._boundary_worker.close()
        self._search_executor.shutdown(wait=True, cancel_futures=True)
        refresher = self._refresher
        self._refresher = None
        if refresher is not None:
            try:
                refresher.close()
            except Exception:  # pragma: no cover - shutdown must not raise
                logger.debug("stopping the library refresher failed", exc_info=True)
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
        # P4-02N: an adapter that owns a worker process is stopped here as well. The seam is
        # duck-typed on purpose - the documented ADR-0003 adapter has ``close()`` and a test
        # stand-in may not, which must never turn a shutdown into a failure.
        encoder = self._encoder
        self._encoder = None
        stop = getattr(encoder, "close", None)
        if callable(stop):
            try:
                stop()
            except Exception:  # pragma: no cover - shutdown must not raise
                logger.debug("stopping the query encoder failed", exc_info=True)
        self.search_sessions.clear()
