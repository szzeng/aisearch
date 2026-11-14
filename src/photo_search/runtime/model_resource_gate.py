"""Small per-process gate between foreground semantic encodes and model builds."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from contextlib import contextmanager, nullcontext
from typing import Any, Iterator

from photo_search.catalog.errors import RUNTIME_CAPABILITY_UNAVAILABLE, CatalogError


class ModelResourceWaitTimeout(TimeoutError):
    """A bounded exclusive wait expired before the candidate acquired the gate."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class ModelResourceWaitCancelled(InterruptedError):
    """A candidate wait was cancelled before it acquired the gate."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ModelResourceGate:
    """Block new query encodes while a candidate owns the model resource."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._readers = 0
        self._writer = False
        self._writer_waiting = False

    @property
    def reserved(self) -> bool:
        with self._condition:
            return self._writer or self._writer_waiting

    @contextmanager
    def query(self) -> Iterator[None]:
        with self._condition:
            if self._writer or self._writer_waiting:
                raise CatalogError(
                    RUNTIME_CAPABILITY_UNAVAILABLE,
                    "semantic model resources are reserved for candidate index work",
                    details={"reason": "MODEL_RESOURCE_BUSY", "retryable": True},
                )
            self._readers += 1
        try:
            yield
        finally:
            with self._condition:
                self._readers -= 1
                self._condition.notify_all()

    @contextmanager
    def exclusive(
        self,
        *,
        timeout: float = 0.0,
        cancel_check: Callable[[], str | None] | None = None,
        poll_interval: float = 0.1,
    ) -> Iterator[None]:
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            if self._writer or self._writer_waiting:
                raise ModelResourceWaitTimeout(
                    "SEMANTIC_RESOURCE_RESERVED",
                    "semantic model resources are already reserved by another task",
                )
            self._writer_waiting = True
            try:
                while self._readers:
                    cancelled = cancel_check() if cancel_check is not None else None
                    if cancelled:
                        raise ModelResourceWaitCancelled(cancelled)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ModelResourceWaitTimeout(
                            "SEMANTIC_QUERY_ACTIVE",
                            "foreground semantic queries are still using model resources",
                        )
                    self._condition.wait(min(remaining, max(0.01, float(poll_interval))))
                self._writer = True
            except BaseException:
                self._writer_waiting = False
                self._condition.notify_all()
                raise
            self._writer_waiting = False
        try:
            yield
        finally:
            with self._condition:
                self._writer = False
                self._condition.notify_all()


class GatedEncoder:
    """Query adapter proxy that gates only calls capable of using the model runtime."""

    def __init__(
        self, encoder: Any, gate: ModelResourceGate,
        is_current: Callable[[], bool] | None = None,
    ) -> None:
        self._encoder = encoder
        self._gate = gate
        self._is_current = is_current

    @contextmanager
    def _access(self) -> Iterator[None]:
        from photo_search.runtime.background_slot import foreground_model_slot

        resource = foreground_model_slot(consume_intent=True) if getattr(self._encoder, "requires_gpu_slot", True) else nullcontext()
        with resource, self._gate.query():
            if self._is_current is not None and not self._is_current():
                raise CatalogError(
                    RUNTIME_CAPABILITY_UNAVAILABLE, "semantic model adapter was replaced",
                    details={"reason": "MODEL_RESOURCE_BUSY", "retryable": True},
                )
            yield

    def spec(self) -> Any:
        return self._encoder.spec()

    @property
    def supports_images(self) -> bool:
        return callable(getattr(self._encoder, "encode_images", None))

    def encode_queries(self, texts: Any, cancellation: Any = None) -> Any:
        with self._access():
            return self._encoder.encode_queries(texts, cancellation=cancellation)

    def encode_images(self, images: Any, cancellation: Any = None) -> Any:
        with self._access():
            return self._encoder.encode_images(images, cancellation=cancellation)

    def encode_image(self, image: Any) -> Any:
        """Keep the singular indexing adapter call behind the same foreground gate."""
        with self._access():
            return self._encoder.encode_image(image)

    def health(self) -> Any:
        with self._access():
            return self._encoder.health()

    def close(self) -> None:
        self._encoder.close()


__all__ = ["GatedEncoder", "ModelResourceGate"]
