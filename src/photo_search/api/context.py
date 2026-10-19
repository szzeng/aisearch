"""Per-application state: settings, session store, catalog service and scan runner.

``create_app`` must not touch the disk, so every resource here is opened lazily and a
*failed* open is deliberately not cached: a database that is momentarily too new, locked
or unreadable has to be re-checked on the next request instead of poisoning the process.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import threading
import time
from collections.abc import Callable
from typing import Any

from photo_search.api.errors import Redactor
from photo_search.api.face_detail_runner import FaceDetailRunner
from photo_search.api.refresher import LibraryRefresher
from photo_search.api.runner import ScanRunner
from photo_search.api.session import SessionManager
from photo_search.api.settings import ApiSettings, generate_session_token
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
        self._photo_collection_sources: dict[str, dict[str, Any]] = {}
        self._photo_collection_sources_lock = threading.RLock()
        self._encoder_factory = encoder_factory
        self._encoder: QueryEncoder | None = None
        self._encoder_resolved = False
        self._adapter_lock = threading.RLock()
        #: P2-06B: the optional reranker adapter, injected exactly like the query encoder.
        #: This build ships none, and a request that asks for a rerank is then answered with
        #: the base page plus ``rerank_state=UNAVAILABLE`` instead of a silent no-op.
        self._reranker_factory = reranker_factory
        self._reranker: Any = None
        self._reranker_resolved = False
        self._service: CatalogService | None = None
        self._runner: ScanRunner | None = None
        self._place_cover_warmer: Any | None = None
        self._lifecycle_lock = threading.Lock()
        self._closed = False
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
        self._face_detail_runner: FaceDetailRunner | None = None
        self._face_detail_recovery_started = False
        self._photo_smart_refresh_runner: Any | None = None
        self._data_root_lease_acquired = False
        self._smart_refresh_recovery_complete = False
        self._smart_refresh_recovery_lock = threading.Lock()

    def _default_service_factory(self) -> CatalogService:
        return CatalogService.open(data_root=self.settings.data_root)

    def register_photo_collection_source(self, *, auth_session_id: str, payload: dict[str, Any],
                                         result: dict[str, Any], mode: str) -> str | None:
        if mode not in {"ranked_candidates", "all_filter_matches"}:
            return None
        if not result.get("search_session_id"):
            return None
        token = secrets.token_urlsafe(32)
        now = time.time_ns() // 1_000_000
        clean_payload = json.loads(json.dumps(payload, ensure_ascii=False))
        query_block = result.get("query") if isinstance(result.get("query"), dict) else {}
        value = {
            "auth_session_hash": hashlib.sha256(str(auth_session_id).encode()).hexdigest(),
            "expires_at_ms": now + 600_000,
            "mode": mode,
            "search_session_id": str(result["search_session_id"]),
            "catalog_filter_revision": result.get("catalog_filter_revision"),
            "suggestion_revision": result.get("suggestion_revision"),
            "favorites_revision": result.get("favorites_revision"),
            "search_binding": {
                "profile_id": result.get("profile_id"),
                "space_id": result.get("space_id"),
                "image_generation_id": result.get("image_generation_id"),
                "dimension": result.get("dimension"),
                "fts_generation_id": result.get("fts_generation_id") or query_block.get("fts_generation_id"),
            },
            "search_scope": result.get("search_scope"),
            "place_scope": result.get("place_scope"),
            "collection_scope": result.get("collection_scope"),
            # Keep the server-confirmed plan and execution path beside the submitted
            # request. Smart rules must not parse natural language again on refresh.
            "confirmed_execution": {
                "mode": mode,
                "retrieval_mode": clean_payload.get("retrieval_mode"),
                "content_query": result.get("content_query"),
                "intent_plan": result.get("intent_plan"),
                "filters": result.get("filters"),
            },
            "payload": clean_payload,
        }
        with self._photo_collection_sources_lock:
            for key, item in list(self._photo_collection_sources.items()):
                if int(item["expires_at_ms"]) <= now:
                    self._photo_collection_sources.pop(key, None)
            if len(self._photo_collection_sources) >= 256:
                oldest = min(self._photo_collection_sources, key=lambda key: self._photo_collection_sources[key]["expires_at_ms"])
                self._photo_collection_sources.pop(oldest, None)
            self._photo_collection_sources[token] = value
        return token

    def resolve_photo_collection_source(self, token: str, *, auth_session_id: str) -> dict[str, Any]:
        now = time.time_ns() // 1_000_000
        with self._photo_collection_sources_lock:
            value = self._photo_collection_sources.get(str(token))
            if value is None or int(value["expires_at_ms"]) <= now:
                self._photo_collection_sources.pop(str(token), None)
                raise ValueError("搜索保存凭据已过期，请重新搜索后保存")
            supplied = hashlib.sha256(str(auth_session_id).encode()).hexdigest()
            if not secrets.compare_digest(str(value["auth_session_hash"]), supplied):
                raise ValueError("搜索保存凭据不属于当前应用会话")
            return dict(value)

    # ------------------------------------------------------------------ resources
    def service(self) -> CatalogService:
        """The catalog service of the request-handling thread (opened once)."""
        if self._service is None:
            self._service = self._service_factory()
        if self._data_root_lease_acquired and not self._smart_refresh_recovery_complete:
            self._recover_smart_refreshes(self._service)
        if not self._face_detail_recovery_started:
            try:
                from photo_search.face.source_detail_batches import running_batches

                for batch_id in running_batches(self._service.catalog):
                    self.face_detail_runner().submit_batch(batch_id)
                self._face_detail_recovery_started = True
            except Exception:  # noqa: BLE001 - app startup remains available if batch recovery fails
                logger.exception("recovering source-detail face batches failed")
        return self._service

    def recover_smart_refreshes_after_lock(self) -> bool:
        """Confirm exclusive data-root ownership, then recover prior-process work."""
        self._data_root_lease_acquired = True
        try:
            return self._recover_smart_refreshes(self.service())
        except Exception:  # database startup remains delayed/retryable if it is unavailable
            logger.info("smart refresh startup recovery deferred", exc_info=True)
            return False

    def _recover_smart_refreshes(self, service: Any) -> bool:
        if self._smart_refresh_recovery_complete:
            return True
        if not self._smart_refresh_recovery_lock.acquire(blocking=False):
            return False
        try:
            from photo_search.photo_smart_collections import recover_interrupted_refreshes

            recover_interrupted_refreshes(service.catalog)
            self._smart_refresh_recovery_complete = True
            return True
        except Exception:  # retry on the next service request; do not fail app startup
            logger.info("smart refresh recovery deferred", exc_info=True)
            return False
        finally:
            self._smart_refresh_recovery_lock.release()

    def runner(self) -> ScanRunner:
        """The single background scan executor; it opens its own service/connection."""
        if self._runner is None:
            self._runner = ScanRunner(
                self._service_factory,
                executor=self._runner_executor,
                on_idle_after_success=self.start_place_cover_warming,
                worker_id=self._worker_id,
            )
        return self._runner

    def start_place_cover_warming(self) -> None:
        """Warm the current first-screen album covers without blocking API or scan work."""
        with self._lifecycle_lock:
            if self._closed:
                return
            if self._place_cover_warmer is None:
                from photo_search.api.place_cover_warmer import PlaceCoverWarmer

                self._place_cover_warmer = PlaceCoverWarmer(self._service_factory)
            self._place_cover_warmer.request()

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

    def face_detail_runner(self) -> FaceDetailRunner:
        """The serialized, on-demand worker for source-detail face jobs."""
        if self._face_detail_runner is None:
            self._face_detail_runner = FaceDetailRunner(self._service_factory)
        return self._face_detail_runner

    def photo_smart_refresh_runner(self) -> Any:
        """One bounded smart-search worker; its durable lease coordinates app instances."""
        if self._photo_smart_refresh_runner is None:
            from photo_search.photo_smart_collections import SmartRefreshRunner

            self._photo_smart_refresh_runner = SmartRefreshRunner(self)
        return self._photo_smart_refresh_runner

    def query_encoder(self) -> QueryEncoder | None:
        """The query half of the embedding adapter, or ``None`` when none is wired.

        ADR-0003 §7 keeps the heavy frameworks out of the service layer, so the encoder is
        always a *supplied* adapter: the real startup entry (``photo_search.__main__``)
        supplies the managed-AI-worker adapter of ``photo_search.workers.query_encoder``
        when this machine has the pinned model, while a test or a deployment supplies its
        own through ``encoder_factory``. ``None`` - and equally a factory that raises - is
        reported honestly by ``GET /capabilities`` and turns a request that needs the
        semantic channel into a structured refusal instead of a silently text-only page.
        """
        with self._adapter_lock:
            if self._closed:
                return None
            if self._encoder_resolved:
                return self._encoder
            if self._encoder_factory is None:
                self._encoder_resolved = True
                return None
            try:
                self._encoder = self._encoder_factory()
            except Exception:  # noqa: BLE001 - a broken adapter is reported, never raised here
                logger.debug("building the query encoder failed", exc_info=True)
                self._encoder = None
            self._encoder_resolved = True
            return self._encoder

    def reranker(self) -> Any:
        """The reranker half of the ADR-0003 §7 boundary, or ``None`` when none is wired.

        Same rule as :meth:`query_encoder`: the service layer never imports a heavy
        framework, a deployment supplies the adapter, and an absent or broken adapter is
        reported honestly (``rerank_state=UNAVAILABLE``) rather than faked.
        """
        with self._adapter_lock:
            if self._closed:
                return None
            if self._reranker_resolved:
                return self._reranker
            if self._reranker_factory is None:
                self._reranker_resolved = True
                return None
            try:
                self._reranker = self._reranker_factory()
            except Exception:  # noqa: BLE001 - a broken adapter is reported, never raised here
                logger.debug("building the reranker adapter failed", exc_info=True)
                self._reranker = None
            self._reranker_resolved = True
            return self._reranker

    def close(self) -> None:
        """Stop the worker, the query encoder and both connections; safe to call twice."""
        with self._lifecycle_lock:
            self._closed = True
            place_cover_warmer = self._place_cover_warmer
            self._place_cover_warmer = None
        refresher = self._refresher
        self._refresher = None
        if refresher is not None:
            try:
                refresher.close()
            except Exception:  # pragma: no cover - shutdown must not raise
                logger.debug("stopping the library refresher failed", exc_info=True)
        face_detail_runner = self._face_detail_runner
        self._face_detail_runner = None
        if face_detail_runner is not None:
            try:
                face_detail_runner.close()
            except Exception:  # pragma: no cover - shutdown must not raise
                logger.debug("stopping the source-detail face runner failed", exc_info=True)
        smart_runner = self._photo_smart_refresh_runner
        self._photo_smart_refresh_runner = None
        if smart_runner is not None:
            try:
                smart_runner.close()
            except Exception:  # pragma: no cover - shutdown must not raise
                logger.debug("stopping the smart collection refresh runner failed", exc_info=True)
        runner = self._runner
        self._runner = None
        if runner is not None:
            try:
                runner.close()
            except Exception:  # pragma: no cover - shutdown must not raise
                logger.debug("stopping the scan runner failed", exc_info=True)
        if place_cover_warmer is not None:
            try:
                place_cover_warmer.close()
            except Exception:  # pragma: no cover - shutdown must not raise
                logger.debug("stopping the place-cover warmer failed", exc_info=True)
        service = self._service
        self._service = None
        self._face_detail_recovery_started = False
        if service is not None:
            try:
                service.close()
            except Exception:  # pragma: no cover - shutdown must not raise
                logger.debug("closing the catalog service failed", exc_info=True)
        # P4-02N: an adapter that owns a worker process is stopped here as well. The seam is
        # duck-typed on purpose - the documented ADR-0003 adapter has ``close()`` and a test
        # stand-in may not, which must never turn a shutdown into a failure.
        with self._adapter_lock:
            encoder = self._encoder
            self._encoder = None
        stop = getattr(encoder, "close", None)
        if callable(stop):
            try:
                stop()
            except Exception:  # pragma: no cover - shutdown must not raise
                logger.debug("stopping the query encoder failed", exc_info=True)
        self.search_sessions.clear()
