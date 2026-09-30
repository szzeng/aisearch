"""The API process's ADR-0003 §7 adapter: the pinned model behind one managed AI worker.

ADR-0003 §7 keeps ONNX Runtime and PyTorch out of the business layer: an API process never
loads a model itself, it is handed an adapter. This module is that adapter for this build -
one :class:`~photo_search.workers.client.AiWorkerClient` subprocess plus the two narrow
seams the HTTP layer documents (``search.embedding.QueryEncoder`` and
``search.upload.ImageEncoder``) - together with the helper the real startup entry uses to
decide whether this machine can serve the semantic channel at all.

Three rules decide what is written here:

* **The space is not invented.** ``processor_id``, ``space_id`` and ``dimension`` come from
  the *pinned manifest* the indexing side published its artifacts under
  (:func:`pinned_embedding_spec` reads that JSON and nothing else - no heavy import), and
  every encoded query is still checked against the bound generation by
  :func:`photo_search.search.embedding.check_encoder_binding`.
* **Honest capability.** :func:`resolve_query_encoder` answers ``None`` when this machine
  has no model directory or no manifest, so the API process keeps exactly its previous
  behaviour (``query_encoder_available=false`` plus the structured 409) instead of starting
  a worker that cannot load anything.
* **One request at a time.** The worker client speaks a single request/reply channel, so
  the client serialises all calls: two concurrent searches can never swap replies.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from photo_search.catalog.errors import (
    RUNTIME_CAPABILITY_UNAVAILABLE,
    RUNTIME_RESOLUTION_MISMATCH,
    SPACE_MISMATCH,
    CatalogError,
)
from photo_search.models.embedding.manifest import (
    MODEL_SUBDIR,
    SPEC_REVISION,
    pinned_embedding_spec,
)
from photo_search.runtime.device import DevicePolicy
from photo_search.scheduler.contracts import ProcessorSpec
from photo_search.workers.client import AiWorkerClient

COLD_START_TIMEOUT_SECONDS = 900.0
_PROVIDER_DEVICES = {"torch-cuda": "gpu", "torch-mps": "gpu", "torch-cpu": "cpu"}


def default_model_dir(data_root: str | Path) -> Path:
    return Path(data_root).joinpath(*MODEL_SUBDIR)


class WorkerQueryEncoder:
    """The one adapter object the API process owns: the worker client plus its spec."""

    def __init__(self, client: Any, spec: ProcessorSpec) -> None:
        self._client = client
        self._spec = spec

    def spec(self) -> ProcessorSpec:
        return self._spec

    def health(self) -> dict[str, Any]:
        """The worker's own health document, in the shape the ADR-0003 adapter defines.

        The indexing side reads a loaded adapter's live facts (device, provider, runtime
        package) to record the capability snapshot and the qualification evidence of the
        generation it builds (P4-02O). The API adapter owns a worker *process* instead of the
        sessions, so it answers with the worker's health reply - the same keys, one hop away -
        which is what lets the refresh embed new content in exactly the space the query path
        already serves (P4-02Y §6C).
        """
        reply = self._client.health(timeout=COLD_START_TIMEOUT_SECONDS)
        if isinstance(reply, Mapping) and "spec" in reply:
            _check_worker_space(reply["spec"], self._spec)
        return reply if isinstance(reply, dict) else {}

    def encode_queries(
        self, texts: Sequence[str], cancellation: Any = None
    ) -> list[list[float]]:
        """One vector per query text, each checked against the spec's dimension."""
        dimension = self._dimension()
        return [self._vector(self._worker_text(str(text)), dimension, "query text") for text in texts]

    def encode_images(
        self, asset_inputs: Sequence[Any], cancellation: Any = None
    ) -> list[list[float]]:
        """One vector per query image; a decoded upload is staged as PNG for the worker."""
        dimension = self._dimension()
        return [
            self._vector(self._worker_image(item), dimension, "query image")
            for item in asset_inputs
        ]

    def encode_image(self, image: Any) -> list[float]:
        """One vector for one image *path*, the spelling the indexing side calls.

        ``photo_search.indexing.semantic`` hands the image tower a path of a catalogued
        original; the upload/search half of this adapter hands it a decoded image. Both are
        the same worker call, so the API process can embed the delta of a refresh without a
        second model instance (P4-02Y §6C).
        """
        return self._vector(self._worker_image(image), self._dimension(), "image")

    def close(self) -> None:
        """Stop the worker process. Shutdown is idempotent and never raises."""
        self._client.shutdown()

    # --------------------------------------------------------------------- internals
    def _dimension(self) -> int:
        return int(self._spec.envelope["dimension"])

    def _worker_text(self, text: str) -> Any:
        return self._client.encode_text(text)

    def _worker_image(self, image: Any) -> Any:
        # The worker protocol carries a *path*: a decoded upload is written to a private
        # temporary PNG of this process and removed before the vector is returned.
        save = getattr(image, "save", None)
        if not callable(save):
            return self._client.encode_image(Path(image))
        with tempfile.TemporaryDirectory(prefix="photo-search-query-") as raw:
            path = Path(raw) / "query.png"
            save(str(path), "PNG")
            return self._client.encode_image(path)

    def _vector(self, reply: Any, dimension: int, what: str) -> list[float]:
        if isinstance(reply, Mapping) and "spec" in reply:
            _check_worker_space(reply["spec"], self._spec)
        raw = reply.get("vector") if isinstance(reply, Mapping) else None
        if not isinstance(raw, (list, tuple)):
            raise CatalogError(
                RUNTIME_RESOLUTION_MISMATCH,
                f"the AI worker returned no {what} vector",
                details={"reason": "WORKER_REPLY_SHAPE"},
            )
        values: list[float] = []
        for value in raw:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise CatalogError(
                    RUNTIME_RESOLUTION_MISMATCH,
                    f"the {what} vector contains a value that is not a number",
                    details={"reason": "WORKER_REPLY_VALUE"},
                )
            values.append(float(value))
        if len(values) != int(dimension):
            raise CatalogError(
                RUNTIME_RESOLUTION_MISMATCH,
                f"the {what} vector does not match the bound dimension",
                details={"expected_dimension": int(dimension), "actual_dimension": len(values)},
            )
        return values


def _runtime_facts(health: Mapping[str, Any]) -> dict[str, Any]:
    """The live worker's runtime facts, translated into the frozen spec's spellings."""
    runtime = health.get("runtime")
    runtime = runtime if isinstance(runtime, Mapping) else {}
    device = health.get("device")
    device = device if isinstance(device, Mapping) else {}
    facts: dict[str, Any] = {}
    package, version = runtime.get("runtime_package"), runtime.get("runtime_version")
    if package and version:
        facts["runtime_package_id"] = f"{package}@{version}"
    if runtime.get("runtime_backend"):
        facts["runtime_backend"] = str(runtime["runtime_backend"])
    devices = [
        _PROVIDER_DEVICES[str(name)]
        for name in (runtime.get("providers") or ())
        if str(name) in _PROVIDER_DEVICES
    ]
    if devices:
        facts["supported_devices"] = tuple(dict.fromkeys(devices))
    if device.get("precision"):
        facts["supported_precisions"] = (str(device["precision"]),)
    return facts


def build_worker_query_encoder(
    *,
    model_dir: str | Path,
    manifest_path: str | Path,
    device: str | None = None,
    device_policy: str | DevicePolicy = DevicePolicy.CPU_ALLOWED,
    client_factory: Callable[..., Any] = AiWorkerClient,
) -> WorkerQueryEncoder:
    """Start the managed AI worker, load the pinned model and return the adapter.

    The model is loaded *now*: the worker's startup handshake means a missing artifact, an
    unusable device or a corrupt session fails here - as a structured refusal the caller can
    report - instead of on the user's first query. The space the worker reports must equal
    the manifest-derived one; a worker that disagrees is refused, because ranking a query in
    another space would silently return the wrong photos.
    """
    client = client_factory(
        Path(model_dir), Path(manifest_path), device=device, device_policy=device_policy
    )
    try:
        health = client.health(timeout=COLD_START_TIMEOUT_SECONDS)
        if not bool(health.get("loaded")):
            raise CatalogError(
                RUNTIME_CAPABILITY_UNAVAILABLE,
                "the AI worker did not load the pinned embedding model",
                details={"reason": "MODEL_NOT_LOADED"},
            )
        spec = pinned_embedding_spec(
            Path(manifest_path), device_policy=device_policy, **_runtime_facts(health)
        )
        _check_worker_space(health.get("spec"), spec)
    except Exception:
        client.shutdown()
        raise
    return WorkerQueryEncoder(client, spec)


def _check_worker_space(worker_spec: Any, expected: ProcessorSpec) -> None:
    """Refuse a worker whose loaded space/dimension differs from the pinned manifest."""
    worker_spec = worker_spec if isinstance(worker_spec, Mapping) else {}
    for key in ("processor_id", "space_id"):
        reported = worker_spec.get(key)
        wanted = (
            str(expected.processor_id)
            if key == "processor_id"
            else str(expected.envelope.get("space_id"))
        )
        if reported is None or str(reported) != wanted:
            raise CatalogError(
                SPACE_MISMATCH,
                "the AI worker loaded a different embedding space than the pinned manifest",
                details={"worker": {key: reported}, "manifest": {key: wanted}},
            )
    if int(worker_spec.get("dimension") or 0) != int(expected.envelope.get("dimension") or 0):
        raise CatalogError(
            SPACE_MISMATCH,
            "the AI worker loaded a different embedding dimension than the pinned manifest",
            details={
                "worker_dimension": worker_spec.get("dimension"),
                "manifest_dimension": expected.envelope.get("dimension"),
            },
        )


@dataclass(frozen=True)
class EncoderWiring:
    """Whether this process can serve the semantic channel, and why."""

    #: ``READY`` / ``MODEL_DIR_MISSING`` / ``MANIFEST_MISSING``.
    reason: str
    model_dir: str
    manifest: str
    factory: Callable[[], WorkerQueryEncoder] | None

    @property
    def available(self) -> bool:
        return self.factory is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": bool(self.available),
            "reason": str(self.reason),
            "model_dir": str(self.model_dir),
            "manifest": str(self.manifest),
        }


def resolve_query_encoder(
    *,
    model_dir: str | Path,
    manifest_path: str | Path,
    device: str | None = None,
    device_policy: str | DevicePolicy = DevicePolicy.CPU_ALLOWED,
    client_factory: Callable[..., Any] = AiWorkerClient,
) -> EncoderWiring:
    """The encoder factory one real startup should be built with, or ``None``.

    Only the two registered paths are inspected (the model directory and the manifest). When
    either is absent the answer is an explicit "this machine has no pinned model", so the
    application reports ``query_encoder_available=false`` and refuses text search exactly as
    it did before this wiring existed. When both exist, the factory loads the model on the
    first request and any load failure is reported the same honest way.
    """
    directory, manifest = Path(model_dir), Path(manifest_path)
    if not directory.is_dir():
        return EncoderWiring("MODEL_DIR_MISSING", str(directory), str(manifest), None)
    if not manifest.is_file():
        return EncoderWiring("MANIFEST_MISSING", str(directory), str(manifest), None)

    def factory() -> WorkerQueryEncoder:
        return build_worker_query_encoder(
            model_dir=directory,
            manifest_path=manifest,
            device=device,
            device_policy=device_policy,
            client_factory=client_factory,
        )

    return EncoderWiring("READY", str(directory), str(manifest), factory)


__all__ = [
    "COLD_START_TIMEOUT_SECONDS",
    "EncoderWiring",
    "MODEL_SUBDIR",
    "SPEC_REVISION",
    "WorkerQueryEncoder",
    "build_worker_query_encoder",
    "default_model_dir",
    "pinned_embedding_spec",
    "resolve_query_encoder",
]
