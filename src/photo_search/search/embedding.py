"""The query-encoding slice of the ADR-0003 §7 embedding adapter boundary.

ADR-0003 §7 freezes the adapter as ``spec()/load()/encode_queries()/encode_images()/
health()/close()`` and keeps the heavy frameworks behind it: "业务 API/服务层禁止顶层
import ONNX、PyTorch 等重型框架". A query only ever needs the immutable ProcessorSpec and
one text encoding, so this module declares exactly those two methods and validates the
answer. Model loading, the image tower and the health/close lifecycle stay in the worker
card that ships the real adapter.

Encoding a query text is done **once** per retrieval and reused for every candidate block,
so the number of encoding calls never depends on the size of the library.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from photo_search.catalog.errors import (
    PROCESSOR_MISMATCH,
    RUNTIME_RESOLUTION_MISMATCH,
    SPACE_MISMATCH,
    CatalogError,
)
from photo_search.scheduler.contracts import ProcessorSpec


@runtime_checkable
class QueryEncoder(Protocol):
    """The query half of an ADR-0003 ``EmbeddingAdapter``."""

    def spec(self) -> ProcessorSpec:
        """The immutable ProcessorSpec of the model that produces this space."""

    def encode_queries(self, texts: Sequence[str], cancellation: Any = None) -> list[list[float]]:
        """Encode query texts; the result must carry the spec's space and dimension."""


def check_encoder_binding(spec: ProcessorSpec, *, space_id: str, dimension: int) -> None:
    """Refuse an encoder that does not produce exactly the bound space and dimension."""
    if spec.processor_kind != "embedding_model":
        raise CatalogError(
            RUNTIME_RESOLUTION_MISMATCH,
            "the query encoder is not an embedding processor",
            details={"processor_id": spec.processor_id, "processor_kind": spec.processor_kind},
        )
    declared_space = str(spec.envelope.get("space_id") or "")
    declared_dimension = int(spec.envelope.get("dimension") or 0)
    if declared_space != str(space_id) or declared_dimension != int(dimension):
        raise CatalogError(
            SPACE_MISMATCH,
            "the query encoder does not produce the bound space",
            details={
                "processor_id": spec.processor_id,
                "declared_space_id": declared_space,
                "declared_dimension": declared_dimension,
            },
        )


def encode_query(
    encoder: QueryEncoder, text: str, *, space_id: str, dimension: int
) -> list[float]:
    """One validated query vector: exactly one finite vector of the bound dimension.

    A wrong shape, a non-finite value or a batch of several vectors is a structured
    ``RUNTIME_RESOLUTION_MISMATCH``; the retrieval never silently truncates or pads a
    vector, never re-normalises it and never swaps in another space.
    """
    check_encoder_binding(encoder.spec(), space_id=space_id, dimension=dimension)
    try:
        vectors = encoder.encode_queries([str(text)])
    except CatalogError:
        raise
    except Exception as error:
        raise CatalogError(
            RUNTIME_RESOLUTION_MISMATCH,
            "the query encoder failed",
            details={"reason": type(error).__name__},
        ) from error
    if not isinstance(vectors, (list, tuple)) or len(vectors) != 1:
        raise CatalogError(
            RUNTIME_RESOLUTION_MISMATCH,
            "one query text must produce exactly one query vector",
            details={"vectors": None if not isinstance(vectors, (list, tuple)) else len(vectors)},
        )
    raw = vectors[0]
    if not isinstance(raw, (list, tuple)) or len(raw) != int(dimension):
        raise CatalogError(
            RUNTIME_RESOLUTION_MISMATCH,
            "the query vector does not match the bound dimension",
            details={
                "expected_dimension": int(dimension),
                "actual_dimension": None if not isinstance(raw, (list, tuple)) else len(raw),
            },
        )
    values: list[float] = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CatalogError(
                RUNTIME_RESOLUTION_MISMATCH,
                "the query vector contains a value that is not a finite number",
            )
        number = float(value)
        if not math.isfinite(number):
            raise CatalogError(
                RUNTIME_RESOLUTION_MISMATCH,
                "the query vector contains a value that is not a finite number",
            )
        values.append(number)
    return values


def encoder_processor_id(encoder: QueryEncoder) -> str:
    """The adapter's own processor id, used only in structured detail and diagnostics."""
    try:
        return str(encoder.spec().processor_id)
    except CatalogError:
        raise
    except Exception as error:  # pragma: no cover - a broken adapter is a structured failure
        raise CatalogError(
            PROCESSOR_MISMATCH,
            "the query encoder did not report a processor spec",
            details={"reason": type(error).__name__},
        ) from error
