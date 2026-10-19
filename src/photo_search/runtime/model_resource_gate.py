"""Small per-process gate between foreground semantic encodes and model builds."""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

from photo_search.catalog.errors import RUNTIME_CAPABILITY_UNAVAILABLE, CatalogError


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
    def exclusive(self, *, timeout: float = 0.0) -> Iterator[None]:
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            if self._writer or self._writer_waiting:
                raise TimeoutError("model resource gate is already reserved")
            self._writer_waiting = True
            try:
                while self._readers:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("foreground semantic work is still active")
                    self._condition.wait(remaining)
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

    def __init__(self, encoder: Any, gate: ModelResourceGate) -> None:
        self._encoder = encoder
        self._gate = gate

    def spec(self) -> Any:
        return self._encoder.spec()

    @property
    def supports_images(self) -> bool:
        return callable(getattr(self._encoder, "encode_images", None))

    def encode_queries(self, texts: Any, cancellation: Any = None) -> Any:
        with self._gate.query():
            return self._encoder.encode_queries(texts, cancellation=cancellation)

    def encode_images(self, images: Any, cancellation: Any = None) -> Any:
        with self._gate.query():
            return self._encoder.encode_images(images, cancellation=cancellation)

    def health(self) -> Any:
        with self._gate.query():
            return self._encoder.health()

    def close(self) -> None:
        self._encoder.close()


__all__ = ["GatedEncoder", "ModelResourceGate"]
