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
  the adapter serialises its own calls: two concurrent searches can never swap replies.
"""

from __future__ import annotations

import tempfile
import threading
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
from photo_search.models.embedding.base import (
    analyze_manifest,
    build_processor_spec,
    effective_device_policy,
    provider_block,
    read_manifest,
)
from photo_search.runtime.device import DevicePolicy
from photo_search.scheduler.contracts import ProcessorSpec
from photo_search.workers.client import AiWorkerClient

#: The query-side spec revision of the historical Chinese-CLIP adapter. The value now lives in
#: :data:`photo_search.models.embedding.base.SPEC_REVISIONS`; this alias keeps the imports of the
#: P4-02N wiring stable.
SPEC_REVISION = "chinese-clip-vit-b-16-query-v1"
#: The frozen job-protocol spellings this processor shares with the indexing side.
TASK_TYPE = "image_embedding"
INPUT_TYPE = "asset"
OUTPUT_TYPE = "embedding"
#: Version labels of the query pipeline, named so a reader can check them against the
#: manifest: its preprocessing (shortest edge 224, 224 center crop, bicubic, RGB, float32),
#: its ``BertTokenizerFast`` (max_length 52, right truncation, max-length padding), no
#: instruction template, and L2 normalisation before the vector leaves the adapter.
IMAGE_PREPROCESS_VERSION = "clip-224-shortest-bicubic-rgb-v1"
QUERY_PREPROCESS_VERSION = "bert-maxlen52-truncate-right-v1"
INSTRUCTION_TEMPLATE_VERSION = "none-v1"
NORMALIZATION_VERSION = "L2-v1"
#: The P0-04 model layout, kept *relative to the data root*: no absolute user path is ever
#: compiled into the source, and moving the data root moves the model with it.
MODEL_SUBDIR = ("models", "chinese_clip", "vit-b-16")
#: A cold CUDA start on a power-gated laptop GPU can take minutes; a slow start must never
#: be mistaken for a hung worker.
COLD_START_TIMEOUT_SECONDS = 900.0
#: ``DevicePolicy`` spellings as the frozen ``ProcessorSpec`` records them.
_DEVICE_POLICY_SPELLING = {
    DevicePolicy.GPU_REQUIRED: "GPU_REQUIRED",
    DevicePolicy.CPU_ALLOWED: "CPU_ALLOWED",
    DevicePolicy.CPU_PREFERRED: "CPU_PREFERRED",
}
#: ONNX Runtime provider names that back the frozen ``cpu``/``gpu`` device spellings.
_PROVIDER_DEVICES = {
    "CPUExecutionProvider": "cpu",
    "CUDAExecutionProvider": "gpu",
    "TensorrtExecutionProvider": "gpu",
    # P4-Embedding-Arch: the PyTorch CUDA path is a *different* execution fact from an ONNX
    # execution provider, and it is reported under its own honest name.
    "PyTorchCUDA": "gpu",
    "PyTorchCPU": "cpu",
}


def default_model_dir(data_root: str | Path) -> Path:
    """The pinned model directory under one application data root (P0-04 layout)."""
    return Path(data_root).joinpath(*MODEL_SUBDIR)


def _config_hash(manifest: Mapping[str, Any]) -> str:
    """Digest of the manifest facts that define this embedding configuration."""
    _, identity = analyze_manifest(manifest)
    return identity.config_hash


def pinned_embedding_spec(
    manifest_path: str | Path,
    *,
    device_policy: str | DevicePolicy = DevicePolicy.CPU_ALLOWED,
    runtime_package_id: str | None = None,
    runtime_backend: str | None = None,
    supported_devices: Sequence[str] | None = None,
    supported_precisions: Sequence[str] | None = None,
) -> ProcessorSpec:
    """The embedding ``ProcessorSpec`` of the pinned model, read from its manifest.

    The derivation lives in :mod:`photo_search.models.embedding.base` and is shared with the
    indexing side and with every other provider, so a query can never be encoded into a space the
    published vectors do not live in. For the historical Chinese-CLIP manifest this produces
    exactly the pre-P4 document. The runtime facts (package, backend, devices, precisions) are
    passed in from the live worker when they are known and otherwise fall back to the manifest.
    """
    manifest = read_manifest(manifest_path)
    _, identity = analyze_manifest(manifest)
    facts: dict[str, Any] = {}
    if runtime_package_id is not None:
        facts["runtime_package_id"] = str(runtime_package_id)
    if runtime_backend is not None:
        facts["runtime_backend"] = str(runtime_backend)
    if supported_devices is not None:
        facts["supported_devices"] = tuple(str(item) for item in supported_devices)
    if supported_precisions is not None:
        facts["supported_precisions"] = tuple(str(item) for item in supported_precisions)
    return build_processor_spec(
        manifest, identity, facts=facts, device_policy=device_policy
    )


class WorkerQueryEncoder:
    """The one adapter object the API process owns: the worker client plus its spec."""

    def __init__(self, client: Any, spec: ProcessorSpec) -> None:
        self._client = client
        self._spec = spec
        #: The worker client is a single request/reply channel; this lock is what keeps two
        #: concurrent searches from reading each other's reply.
        self._lock = threading.Lock()

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
        with self._lock:
            return self._client.encode_text(text)

    def _worker_image(self, image: Any) -> Any:
        # The worker protocol carries a *path*: a decoded upload is written to a private
        # temporary PNG of this process and removed before the vector is returned.
        save = getattr(image, "save", None)
        if not callable(save):
            with self._lock:
                return self._client.encode_image(Path(image))
        with tempfile.TemporaryDirectory(prefix="photo-search-query-") as raw:
            path = Path(raw) / "query.png"
            save(str(path), "PNG")
            with self._lock:
                return self._client.encode_image(path)

    def _vector(self, reply: Any, dimension: int, what: str) -> list[float]:
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
    resolved = str(device.get("device") or "")
    if resolved:
        # The device the adapter *really* resolved is authoritative; the provider list only says
        # what the runtime offers.
        devices.append("gpu" if resolved == "cuda" else resolved)
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
    manifest = read_manifest(manifest_path)
    provider = provider_block(manifest)
    # The manifest's own policy is combined with the caller's: a model that is only correct on a
    # strict CUDA path can never be softened into a CPU run by a caller's default.
    effective_policy = effective_device_policy(provider["device_policy"], device_policy)
    client = client_factory(
        Path(model_dir), Path(manifest_path), device=device, device_policy=effective_policy
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
            Path(manifest_path), device_policy=effective_policy, **_runtime_facts(health)
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

    #: ``READY`` / ``MODEL_DIR_MISSING`` / ``MANIFEST_MISSING`` / ``PROVIDER_UNKNOWN``.
    reason: str
    model_dir: str
    manifest: str
    factory: Callable[[], WorkerQueryEncoder] | None
    #: The provider this wiring resolves to (empty when nothing could be resolved).
    provider_id: str = ""

    @property
    def available(self) -> bool:
        return self.factory is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": bool(self.available),
            "reason": str(self.reason),
            "provider_id": str(self.provider_id),
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

    return EncoderWiring(
        "READY",
        str(directory),
        str(manifest),
        factory,
        provider_id=str(provider_block(read_manifest(manifest))["provider_id"]),
    )


def active_provider_id(catalog: Any) -> str | None:
    """The provider the *active* search profile binds, or ``None`` when none is activated.

    ADR-0003 §8: the served profile binds the encoder, the space and the generation, so the
    process that serves requests must load exactly the provider its ``embedding_processor_id``
    names. This reads one row, loads nothing and decides nothing else.
    """
    from photo_search.models.embedding.base import provider_id_of_processor
    from photo_search.search import profile as profile_module

    try:
        identifier = profile_module.active_profile_id(catalog.connection)
    except Exception:  # noqa: BLE001 - an unreadable catalog is "no profile", not a crash
        return None
    if identifier is None:
        return None
    profile = profile_module.load_profile(catalog, str(identifier))
    if profile is None:
        return None
    return provider_id_of_processor(str(profile.embedding_processor_id))


def resolve_provider_encoder(
    *,
    provider: Any,
    device: str | None = None,
    device_policy: str | DevicePolicy | None = None,
    client_factory: Callable[..., Any] = AiWorkerClient,
) -> EncoderWiring:
    """The encoder factory for one registry provider, or an honest "not installed".

    This is the production path: the provider comes from the registry (and, when a catalog has an
    activated profile, from that profile's ``processor_id``), so the query path can only ever load
    the model the served generation was built with.
    """
    directory = Path(str(provider.model_dir))
    manifest = Path(str(provider.manifest_path))
    policy = effective_device_policy(
        getattr(provider, "device_policy", None), device_policy
    )
    if not manifest.is_file():
        return EncoderWiring(
            "MANIFEST_MISSING", str(directory), str(manifest), None, str(provider.provider_id)
        )
    if not directory.is_dir() or getattr(provider, "missing_files", ()):
        return EncoderWiring(
            "MODEL_DIR_MISSING", str(directory), str(manifest), None, str(provider.provider_id)
        )

    def factory() -> WorkerQueryEncoder:
        return build_worker_query_encoder(
            model_dir=directory,
            manifest_path=manifest,
            device=device,
            device_policy=policy,
            client_factory=client_factory,
        )

    return EncoderWiring(
        "READY", str(directory), str(manifest), factory, str(provider.provider_id)
    )


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
