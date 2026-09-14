"""The face ``ProcessorSpec``, its structured document, its executor and its persistence.

The processor is the frozen ADR-0003 shell with the P3-02 facts filled in: an
``embedding_model`` (512-d, cosine, L2, own space) whose *published* artifact is a
structured face document, plus one 512-d vector per face in the canonical
``face_embeddings`` table. The asset-level ``embedding`` artifact kind is deliberately not
used: it is asset-keyed and would create an ``index_entries`` row in the image space, which
is exactly the identity reuse the P3-02 card forbids. The independent face candidate index
is fed by its own outbox (see :mod:`photo_search.face.index`).

The split between the durable stores follows the P2-03/P3-01 invariants exactly:

* the immutable **structured artifact** carries every face with its source-frame bbox, its
  five landmarks, the detection score, the quality measures (the recognition-frame pixel
  count and the variance of the Laplacian), the quality verdict and the 512-d vector. It is
  deterministic, so a re-execution reproduces the identical bytes and reuses the artifact
  row instead of writing a second document;
* **SQLite** carries the current facts of every face (``faces``), the canonical vectors
  (``face_embeddings``) and the durable "these facts still need a conservative association
  pass" row (``face_association_queue``);
* nothing in this module writes a person, a link or a suggestion. The association runs in
  ``photo_search.face.index`` *after* the candidate index is confirmed, which is what keeps
  the numbering of this task's two halves (facts first, grouping second) honest.

One decision is worth naming explicitly: a re-run replaces this asset's rows for the *same*
detection generation only (``spec_revision``). A future generation therefore adds its own
rows instead of destroying the old ones, and the frozen
``UNIQUE(asset_id, spec_revision, ordinal)`` makes "a re-run cannot produce a second set of
faces" a database fact rather than a promise.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from photo_search.catalog.errors import (
    ACCESS_DENIED,
    ARTIFACT_INVALID,
    ASSET_NOT_FOUND,
    FACE_DEADLINE_EXCEEDED,
    FACE_DEVICE_UNAVAILABLE,
    FACE_RESULT_TOO_LARGE,
    FACE_WEIGHTS_HASH_MISMATCH,
    FACE_WEIGHTS_MISSING,
    FILE_READ_ERROR,
    INPUT_UNSTABLE,
    SOURCE_FILE_MISSING,
    SOURCE_OFFLINE,
    STALE_INPUT_REVISION,
    CatalogError,
)
from photo_search.face import contract
from photo_search.face.engine import (
    FaceCancelled,
    FaceDocument,
    FaceEngineCache,
    analyze_image,
)
from photo_search.scheduler.contracts import ProcessorSpec, canonical_json

ARTIFACT_ID_PREFIX = "face-document:"
EVENT_ID_PREFIX = "face-evt-"
#: Temporary ordinal offset used while a re-detection rewrites its own rows.
_ORDINAL_PARK_OFFSET = 1_000_000

PAYLOAD_KEYS = frozenset(
    {
        "contract_version",
        "processor_id",
        "input_revision",
        "location_id",
        "source",
        "model_id",
        "model_fingerprint",
        "config_hash",
        "engine_config",
        "space_id",
        "dimension",
        "metric",
        "normalization",
        "decode_format",
        "original_size",
        "working_size",
        "scale",
        "face_count",
        "low_quality_faces",
        "faces",
    }
)

FACE_KEYS = frozenset(
    {
        "face_id",
        "ordinal",
        "bbox",
        "landmarks",
        "det_score",
        "face_pixels",
        "blur_variance",
        "quality_state",
        "quality_reason",
        "embedding",
    }
)

#: Failures of the *environment* (a source that cannot be read, weights that are not
#: installed, a device the resolved session cannot open): the job blocks without burning an
#: attempt, exactly like the P3-01 OCR processor treats an unavailable source.
_BLOCKING_CODES = frozenset(
    {
        SOURCE_FILE_MISSING,
        SOURCE_OFFLINE,
        INPUT_UNSTABLE,
        FILE_READ_ERROR,
        ACCESS_DENIED,
        FACE_WEIGHTS_MISSING,
        FACE_WEIGHTS_HASH_MISMATCH,
        FACE_DEVICE_UNAVAILABLE,
    }
)

#: The blocking reason recorded for each blocking code: a source/weight problem is not a
#: device problem, and a device problem is never reported as a missing file.
_BLOCKING_REASON = {
    FACE_DEVICE_UNAVAILABLE: "RUNTIME_CAPABILITY_UNAVAILABLE",
}


def system_now_ms() -> int:
    return time.time_ns() // 1_000_000


def onnx_runtime_package_id() -> str:
    """The runtime package this process actually resolved (P0-04 manifest: 1.30.0)."""
    import onnxruntime

    return f"onnxruntime-gpu@{onnxruntime.__version__}"


def face_processor_spec() -> ProcessorSpec:
    """The frozen ``ProcessorSpec`` of the face processor (ADR-0003 §2/§4)."""
    weights = [
        {
            "name": Path(str(entry["path"])).name,
            "revision": f"{contract.FACE_MODEL_REVISION}#{str(entry['sha256'])[:12]}",
            "sha256": str(entry["sha256"]),
        }
        for entry in contract.PINNED_WEIGHTS
    ]
    return ProcessorSpec.from_envelope(
        {
            "contract_version": contract.CONTRACT_VERSION,
            "spec_revision": contract.FACE_SPEC_REVISION,
            "processor_id": contract.FACE_PROCESSOR_ID,
            "processor_kind": contract.FACE_PROCESSOR_KIND,
            "task_type": contract.FACE_TASK_TYPE,
            "config_hash": contract.CONFIG_HASH,
            "input_type": contract.FACE_INPUT_TYPE,
            "output_type": contract.FACE_OUTPUT_TYPE,
            "device_policy": contract.FACE_DEVICE_POLICY,
            "runtime_backend": contract.FACE_RUNTIME_BACKEND,
            "runtime_package_id": onnx_runtime_package_id(),
            "supported_devices": list(contract.FACE_SUPPORTED_DEVICES),
            "supported_precisions": list(contract.FACE_SUPPORTED_PRECISIONS),
            "max_batch_size": 1,
            "cancellation_supported": True,
            "resource_measurements": [],
            "model_id": contract.FACE_MODEL_ID,
            "model_revision": contract.FACE_MODEL_REVISION,
            "weights": weights,
            "image_preprocess_version": contract.FACE_IMAGE_PREPROCESS_VERSION,
            "query_preprocess_version": contract.FACE_QUERY_PREPROCESS_VERSION,
            "tokenizer_version": contract.FACE_TOKENIZER_VERSION,
            "instruction_template_version": contract.FACE_INSTRUCTION_TEMPLATE_VERSION,
            "normalization_version": contract.FACE_NORMALIZATION_VERSION,
            "dimension": contract.FACE_DIMENSION,
            "metric": contract.FACE_METRIC,
            "space_id": contract.FACE_SPACE_ID,
            "max_pixels": contract.DETECT_CANVAS * contract.DETECT_CANVAS,
            #: This processor consumes no tokens; the frozen embedding spec still requires a
            #: positive bound, so it declares the smallest one instead of inventing a
            #: misleading text limit.
            "max_tokens": 1,
        }
    )


def artifact_id_for(input_revision: str) -> str:
    return f"{ARTIFACT_ID_PREFIX}{contract.FACE_PROCESSOR_ID}:{str(input_revision)}"


def face_id_for(*, input_revision: str, ordinal: int, bbox: Sequence[float]) -> str:
    """The deterministic identity of one face of one content revision and generation.

    It depends on the content revision, the detection generation, the ordinal and the
    rounded bounding box - never on database state or on the order of two runs. Two runs
    over the same bytes therefore produce the same ids (so the grouping is idempotent), and
    a different picture produces different ids (so nothing is silently inherited).
    """
    payload = {
        "spec_revision": contract.FACE_SPEC_REVISION,
        "input_revision": str(input_revision),
        "ordinal": int(ordinal),
        "bbox": [round(float(value), contract.COORDINATE_DECIMALS) for value in bbox],
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def generation_id_for(space_id: str, processor_id: str) -> str:
    """The independent face-index generation: face-keyed, never the image generation."""
    suffix = hashlib.sha256(f"{space_id}\0{processor_id}".encode()).hexdigest()[:24]
    return f"{contract.FACE_GENERATION_PREFIX}{suffix}"


def event_id_for(*, face_id: str, generation_id: str, revision: int) -> str:
    value = f"UPSERT_FACE\0{face_id}\0{generation_id}\0{int(revision)}"
    return f"{EVENT_ID_PREFIX}{hashlib.sha256(value.encode()).hexdigest()}"


def build_payload(
    *,
    input_revision: str,
    location_id: int | None,
    document: FaceDocument,
    decode_format: str,
) -> dict[str, Any]:
    """The deterministic face document of one content revision."""
    faces: list[dict[str, Any]] = []
    for face in document.faces:
        faces.append(
            {
                "face_id": face_id_for(
                    input_revision=str(input_revision), ordinal=face.ordinal, bbox=face.bbox
                ),
                "ordinal": int(face.ordinal),
                "bbox": [float(value) for value in face.bbox],
                "landmarks": [[float(x), float(y)] for x, y in face.landmarks],
                "det_score": float(face.det_score),
                "face_pixels": float(face.face_pixels),
                "blur_variance": float(face.blur_variance),
                "quality_state": str(face.quality_state),
                "quality_reason": face.quality_reason,
                "embedding": [float(value) for value in face.embedding],
            }
        )
    return {
        "contract_version": contract.CONTRACT_VERSION,
        "processor_id": contract.FACE_PROCESSOR_ID,
        "input_revision": str(input_revision),
        "location_id": None if location_id is None else int(location_id),
        "source": contract.FACE_SOURCE,
        "model_id": contract.FACE_MODEL_ID,
        "model_fingerprint": contract.MODEL_FINGERPRINT,
        "config_hash": contract.CONFIG_HASH,
        "engine_config": dict(contract.ENGINE_CONFIG),
        "space_id": contract.FACE_SPACE_ID,
        "dimension": int(contract.FACE_DIMENSION),
        "metric": contract.FACE_METRIC,
        "normalization": contract.FACE_NORMALIZATION,
        "decode_format": str(decode_format),
        "original_size": [int(document.original_size[0]), int(document.original_size[1])],
        "working_size": [int(document.working_size[0]), int(document.working_size[1])],
        "scale": float(document.scale),
        "face_count": len(faces),
        "low_quality_faces": int(document.low_quality_count),
        "faces": faces,
    }


def artifact_ref(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The ``ArtifactRef`` of one face document; the executor never picks a storage path."""
    data = canonical_json(dict(payload)).encode("utf-8")
    return {
        "artifact_id": artifact_id_for(str(payload["input_revision"])),
        "artifact_type": "structured",
        "artifact_revision": contract.FACE_ARTIFACT_REVISION,
        "media_type": contract.FACE_MEDIA_TYPE,
        "checksum_sha256": hashlib.sha256(data).hexdigest(),
        "byte_size": len(data),
        "storage_uri": None,
        "payload": dict(payload),
        "space_id": None,
        "dimension": None,
    }


def embedding_blob(vector: Sequence[float]) -> bytes:
    """The canonical little-endian float32 encoding of one face vector."""
    import struct

    return b"".join(struct.pack("<f", float(value)) for value in vector)


# --------------------------------------------------------------------------- extraction
def extract_for_asset(
    catalog: Any,
    *,
    asset_id: int,
    input_revision: str,
    device: str = "auto",
    data_root: str | Path | None = None,
    engine_cache: FaceEngineCache | None = None,
    cancel: Callable[[], bool] | None = None,
    deadline_ms: int | None = None,
    clock: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """Read the registered original, find its faces and return the document payload."""
    row = catalog.read_one("SELECT sha256 FROM assets WHERE asset_id = ?", (int(asset_id),))
    if row is None:
        raise CatalogError(
            ASSET_NOT_FOUND,
            f"asset {int(asset_id)} does not exist",
            details={"asset_id": int(asset_id)},
        )
    if str(row["sha256"]) != str(input_revision):
        raise CatalogError(
            STALE_INPUT_REVISION,
            "the job was created for a different content revision",
        )
    location = _resolve_location(catalog, int(asset_id))
    observed = _stat_pair(location)
    digest = _hash_file(location["absolute_path"])
    if digest != str(input_revision):
        raise CatalogError(
            INPUT_UNSTABLE,
            "the file bytes no longer match the catalogued content",
        )
    decoded = _decode(location["absolute_path"])
    _verify_unchanged(location, observed)
    cache = engine_cache or FaceEngineCache()
    engine = cache.engine(data_root if data_root is not None else catalog.data_root, device=str(device))
    document = analyze_image(
        engine,
        decoded.image,
        cancel=cancel,
        deadline_ms=deadline_ms,
        clock=clock,
    )
    payload = build_payload(
        input_revision=str(input_revision),
        location_id=int(location["location_id"]),
        document=document,
        decode_format=str(decoded.source_format),
    )
    validate_payload(payload)
    return payload


def _resolve_location(catalog: Any, asset_id: int) -> dict[str, Any]:
    """Reuse the accepted P1-04 controlled-file boundary instead of duplicating it."""
    from photo_search.api.errors import ApiError
    from photo_search.api.files import resolve_present_location

    try:
        location = resolve_present_location(catalog, int(asset_id))
    except ApiError as error:
        details = {
            key: value
            for key, value in (error.details or {}).items()
            if key in ("location_id", "source_id", "positions")
        }
        raise CatalogError(str(error.code), str(error.message), details=details) from error
    return {
        "location_id": location.location_id,
        "absolute_path": location.absolute_path,
        "size": location.size,
        "mtime_ns": location.mtime_ns,
    }


def _stat_pair(location: Mapping[str, Any]) -> tuple[int, int]:
    path: Path = location["absolute_path"]
    try:
        info = path.stat()
    except OSError as error:
        raise CatalogError(
            FILE_READ_ERROR,
            "the registered position is not readable",
            details={"reason": type(error).__name__},
        ) from error
    observed = (int(info.st_size), int(info.st_mtime_ns))
    if observed != (int(location["size"]), int(location["mtime_ns"])):
        raise CatalogError(
            INPUT_UNSTABLE,
            "the source file changed after the catalog observation",
            details={"location_id": int(location["location_id"])},
        )
    return observed


def _verify_unchanged(location: Mapping[str, Any], observed: tuple[int, int]) -> None:
    try:
        info = location["absolute_path"].stat()
    except OSError as error:
        raise CatalogError(
            FILE_READ_ERROR,
            "the source file disappeared during the read",
            details={"reason": type(error).__name__},
        ) from error
    if (int(info.st_size), int(info.st_mtime_ns)) != observed:
        raise CatalogError(
            INPUT_UNSTABLE,
            "the source file changed while it was being read",
            details={"location_id": int(location["location_id"])},
        )


def _hash_file(path: Path, *, chunk_bytes: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(chunk_bytes)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError as error:
        raise CatalogError(
            FILE_READ_ERROR,
            "cannot read the source file",
            details={"reason": type(error).__name__},
        ) from error
    return digest.hexdigest()


def _decode(path: Path) -> Any:
    """The accepted P1-03 decode boundary: format gate, EXIF orientation, JPEG draft."""
    from photo_search.media.decode import load_normalized

    return load_normalized(path, long_edge=contract.WORKING_LONG_EDGE)


def _safe_message(error: CatalogError) -> str:
    """A blocking reason that never carries a local path (P2-03A privacy boundary)."""
    if str(error.code) in _BLOCKING_CODES:
        return "the registered original could not be read for face detection"
    return str(error.message)[:200]


# ------------------------------------------------------------------------- the executor
def face_executor(
    catalog: Any,
    *,
    data_root: str | Path | None = None,
    engine_cache: FaceEngineCache | None = None,
    cancel: Callable[[], bool] | None = None,
    deadline_ms: int | None = None,
    clock: Callable[[], float] | None = None,
    wall_clock: Callable[[], int] | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Build the scheduler executor for ``image_face`` jobs over one catalog.

    The engine is opened for the device the *scheduler* resolved (never chosen here), which
    is why the resolved device travels in the request and not in a local preference.
    """
    now = wall_clock or system_now_ms
    cache = engine_cache or FaceEngineCache()

    def run(request: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "contract_version": contract.CONTRACT_VERSION,
            "job_id": int(request["job_id"]),
            "lease_token": str(request["lease_token"]),
            "input_revision": str(request["input_revision"]),
            "task_type": str(request["task_type"]),
            "processor_id": str(request["processor_id"]),
            "state": "SUCCEEDED",
            "resolved_device": str(request["resolved_device"]),
            "runtime_provider": str(request["runtime_provider"]),
            "precision": str(request["precision"]),
            "progress": 1.0,
            "artifacts": [],
            "error": None,
            "completed_at": int(now()),
        }
        try:
            payload = extract_for_asset(
                catalog,
                asset_id=int(request["subject_id"]),
                input_revision=str(request["input_revision"]),
                device=str(request["resolved_device"]),
                data_root=data_root,
                engine_cache=cache,
                cancel=cancel,
                deadline_ms=deadline_ms,
                clock=clock,
            )
        except FaceCancelled:
            result.update(
                state="CANCELLED",
                progress=0.0,
                artifacts=[],
                error={
                    "code": "CANCELLED",
                    "message": "the execution was cancelled before it finished",
                    "retryable": False,
                    "blocking_reason": None,
                },
            )
            return result
        except CatalogError as error:
            code = str(error.code)
            if code == FACE_DEADLINE_EXCEEDED:
                # A bounded budget that ran out is transient: the job may be retried (and
                # consumes one attempt), it is never a silent success.
                state, retryable, blocking = "RETRY_WAIT", True, None
            elif code in _BLOCKING_CODES:
                state = "BLOCKED"
                retryable = False
                blocking = _BLOCKING_REASON.get(code, "SOURCE_UNAVAILABLE")
            else:
                state, retryable, blocking = "FAILED", False, None
            result.update(
                state=state,
                progress=0.0,
                artifacts=[],
                error={
                    "code": code,
                    "message": _safe_message(error),
                    "retryable": retryable,
                    "blocking_reason": blocking,
                },
            )
            return result
        result["artifacts"] = [artifact_ref(payload)]
        return result

    return run


# --------------------------------------------------------------------- payload checks
def _number(value: Any, *, where: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CatalogError(ARTIFACT_INVALID, f"{where} must be a number")
    number = float(value)
    if not (low <= number <= high):
        raise CatalogError(ARTIFACT_INVALID, f"{where} is outside [{low}, {high}]")
    return number


def _positive_int(value: Any, *, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or int(value) < 1:
        raise CatalogError(ARTIFACT_INVALID, f"{where} must be a positive integer")
    return int(value)


def validate_payload(payload: Mapping[str, Any]) -> None:
    """Enforce the frozen face-document shape before anything reaches the catalog."""
    import math

    if not isinstance(payload, Mapping) or frozenset(payload) != PAYLOAD_KEYS:
        raise CatalogError(
            ARTIFACT_INVALID,
            "the face document does not match the frozen shape",
            details={"artifact": contract.FACE_ARTIFACT_REVISION},
        )
    if str(payload["contract_version"]) != contract.CONTRACT_VERSION:
        raise CatalogError(ARTIFACT_INVALID, "the face document carries a foreign contract")
    if str(payload["processor_id"]) != contract.FACE_PROCESSOR_ID:
        raise CatalogError(ARTIFACT_INVALID, "the face document was produced by another processor")
    if str(payload["source"]) != contract.FACE_SOURCE:
        raise CatalogError(ARTIFACT_INVALID, "the face document source is not the pinned engine")
    if str(payload["space_id"]) != contract.FACE_SPACE_ID:
        raise CatalogError(
            ARTIFACT_INVALID,
            "the face document does not belong to this processor's face space",
        )
    if int(payload["dimension"]) != contract.FACE_DIMENSION:
        raise CatalogError(ARTIFACT_INVALID, "the face document dimension is not the pinned one")
    if str(payload["metric"]) != contract.FACE_METRIC:
        raise CatalogError(ARTIFACT_INVALID, "the face document metric is not cosine")
    if str(payload["normalization"]) != contract.FACE_NORMALIZATION:
        raise CatalogError(ARTIFACT_INVALID, "the face document normalization is not L2")
    revision = str(payload["input_revision"])
    if len(revision) != 64 or any(char not in "0123456789abcdef" for char in revision):
        raise CatalogError(ARTIFACT_INVALID, "the input revision is not a sha256")
    for key in ("model_fingerprint", "config_hash"):
        text = str(payload[key])
        if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
            raise CatalogError(ARTIFACT_INVALID, f"{key} is not a sha256")
    if str(payload["model_fingerprint"]) != contract.MODEL_FINGERPRINT:
        raise CatalogError(ARTIFACT_INVALID, "the document was produced by other weights")
    if str(payload["config_hash"]) != contract.CONFIG_HASH:
        raise CatalogError(ARTIFACT_INVALID, "the document was produced by another config")
    if not isinstance(payload["engine_config"], Mapping):
        raise CatalogError(ARTIFACT_INVALID, "engine_config must be an object")
    if str(payload["decode_format"]) != str(payload["decode_format"]).upper():
        raise CatalogError(ARTIFACT_INVALID, "the decode format label must be upper case")
    for key in ("original_size", "working_size"):
        size = payload[key]
        if not isinstance(size, (list, tuple)) or len(size) != 2:
            raise CatalogError(ARTIFACT_INVALID, f"{key} must be a width/height pair")
        _positive_int(size[0], where=f"{key}[0]")
        _positive_int(size[1], where=f"{key}[1]")
    _number(payload["scale"], where="scale", low=0.0, high=1.0)
    faces = payload["faces"]
    if not isinstance(faces, (list, tuple)):
        raise CatalogError(ARTIFACT_INVALID, "faces must be a list")
    if int(payload["face_count"]) != len(faces):
        raise CatalogError(ARTIFACT_INVALID, "face_count does not match the face list")
    if len(faces) > contract.MAX_FACES:
        raise CatalogError(
            FACE_RESULT_TOO_LARGE,
            "the face document has more faces than one artifact may carry",
            details={"faces": len(faces), "max_faces": contract.MAX_FACES},
        )
    low = 0
    seen: set[str] = set()
    for expected_ordinal, face in enumerate(faces):
        if not isinstance(face, Mapping) or frozenset(face) != FACE_KEYS:
            raise CatalogError(ARTIFACT_INVALID, "a face does not match the frozen shape")
        if int(face["ordinal"]) != expected_ordinal:
            raise CatalogError(ARTIFACT_INVALID, "face ordinals must be 0..n-1 in order")
        bbox = face["bbox"]
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            raise CatalogError(ARTIFACT_INVALID, "a face bbox must have four numbers")
        left, top, right, bottom = (_number(value, where="bbox", low=-1e7, high=1e7) for value in bbox)
        if right <= left or bottom <= top:
            raise CatalogError(ARTIFACT_INVALID, "a face bbox must have a positive area")
        landmarks = face["landmarks"]
        if not isinstance(landmarks, (list, tuple)) or len(landmarks) != 5:
            raise CatalogError(ARTIFACT_INVALID, "a face needs exactly five landmarks")
        for point in landmarks:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise CatalogError(ARTIFACT_INVALID, "a landmark must be a pair")
            _number(point[0], where="landmark.x", low=-1e7, high=1e7)
            _number(point[1], where="landmark.y", low=-1e7, high=1e7)
        _number(face["det_score"], where="det_score", low=0.0, high=1.0)
        _number(face["face_pixels"], where="face_pixels", low=0.0, high=1e7)
        _number(face["blur_variance"], where="blur_variance", low=0.0, high=1e12)
        state = str(face["quality_state"])
        reason = face["quality_reason"]
        if state not in ("OK", "LOW"):
            raise CatalogError(ARTIFACT_INVALID, "a face quality state must be OK or LOW")
        if (state == "LOW") != (reason is not None):
            raise CatalogError(
                ARTIFACT_INVALID, "only a low-quality face carries a quality reason"
            )
        if reason is not None and str(reason) not in (
            contract.QUALITY_SMALL,
            contract.QUALITY_BLURRY,
            contract.QUALITY_LOW_SCORE,
        ):
            raise CatalogError(ARTIFACT_INVALID, "a face quality reason is not a known one")
        if state == "LOW":
            low += 1
        vector = face["embedding"]
        if not isinstance(vector, (list, tuple)) or len(vector) != contract.FACE_DIMENSION:
            raise CatalogError(
                ARTIFACT_INVALID,
                "a face embedding must have the pinned dimension",
                details={"dimension": contract.FACE_DIMENSION},
            )
        norm = 0.0
        for value in vector:
            number = _number(value, where="embedding", low=-1.0, high=1.0)
            norm += number * number
        if not math.isfinite(norm) or abs(math.sqrt(norm) - 1.0) > 1e-3:
            raise CatalogError(ARTIFACT_INVALID, "a face embedding is not L2-normalised")
        expected_id = face_id_for(
            input_revision=revision, ordinal=expected_ordinal, bbox=bbox
        )
        if str(face["face_id"]) != expected_id:
            raise CatalogError(
                ARTIFACT_INVALID,
                "a face_id does not match its content revision, generation and geometry",
            )
        if expected_id in seen:
            raise CatalogError(ARTIFACT_INVALID, "a face_id is repeated in one document")
        seen.add(expected_id)
    if int(payload["low_quality_faces"]) != low:
        raise CatalogError(ARTIFACT_INVALID, "low_quality_faces does not match the faces")


# ------------------------------------------------------------------------- persistence
def human_controlled_face_ids(connection: sqlite3.Connection, *, asset_id: int) -> set[str]:
    """Faces of one asset that P3-03/P3-04 human tables already own.

    Those tables do not exist in this build, so the answer is empty today - but the *read*
    happens here, on every commit, so the moment they exist the automatic path cannot create,
    change or delete anything about such a face. The columns read are the ones architecture
    §4.2 fixes (``face_assignment_controls.face_id``/``mode``, ``face_exclusions.face_id``).
    """
    controlled: set[str] = set()
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if "face_assignment_controls" in tables:
        controlled.update(
            str(row[0])
            for row in connection.execute(
                "SELECT c.face_id FROM face_assignment_controls AS c "
                "JOIN faces AS f ON f.face_id = c.face_id WHERE f.asset_id = ? "
                "AND c.mode IN ('MANUAL_ASSIGNED', 'MANUAL_UNASSIGNED')",
                (int(asset_id),),
            )
        )
    if "face_exclusions" in tables:
        controlled.update(
            str(row[0])
            for row in connection.execute(
                "SELECT e.face_id FROM face_exclusions AS e "
                "JOIN faces AS f ON f.face_id = e.face_id WHERE f.asset_id = ? "
                "AND e.active = 1",
                (int(asset_id),),
            )
        )
    return controlled


def _ensure_generation(connection: sqlite3.Connection, *, now_ms: int) -> str:
    generation_id = generation_id_for(contract.FACE_SPACE_ID, contract.FACE_PROCESSOR_ID)
    connection.execute(
        "INSERT OR IGNORE INTO face_index_generations(generation_id, space_id, processor_id, "
        "spec_revision, dimension, metric, normalization, state, is_active, created_at, "
        "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', 0, ?, ?)",
        (
            generation_id,
            contract.FACE_SPACE_ID,
            contract.FACE_PROCESSOR_ID,
            contract.FACE_SPEC_REVISION,
            contract.FACE_DIMENSION,
            contract.FACE_METRIC,
            contract.FACE_NORMALIZATION,
            int(now_ms),
            int(now_ms),
        ),
    )
    row = connection.execute(
        "SELECT space_id, processor_id, spec_revision, dimension, state "
        "FROM face_index_generations WHERE generation_id = ?",
        (generation_id,),
    ).fetchone()
    if row is None or str(row["space_id"]) != contract.FACE_SPACE_ID:
        raise CatalogError(
            ARTIFACT_INVALID,
            "the face candidate generation does not belong to this processor's face space",
        )
    if str(row["spec_revision"]) != contract.FACE_SPEC_REVISION:
        raise CatalogError(
            ARTIFACT_INVALID,
            "the face candidate generation was created by another detection revision",
        )
    return generation_id


def persist_face_document(
    connection: sqlite3.Connection,
    *,
    job: Any,
    result: Any,
    prepared: Any,
    now_ms: int,
) -> bool:
    """Write the current faces of one asset inside the caller's transaction."""
    documents = [
        item
        for item in prepared
        if str(item.ref.artifact_type) == "structured"
        and str(item.ref.artifact_id).startswith(ARTIFACT_ID_PREFIX)
    ]
    if not documents:
        return False
    item = documents[0]
    try:
        payload = json.loads(item.payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CatalogError(ARTIFACT_INVALID, "the face document payload is not valid JSON") from error
    validate_payload(payload)
    asset_id = int(job.subject_id)
    row = connection.execute(
        "SELECT sha256 FROM assets WHERE asset_id = ?", (asset_id,)
    ).fetchone()
    if row is None:
        raise CatalogError(ARTIFACT_INVALID, "the face document subject asset does not exist")
    if str(row["sha256"]) != str(result.input_revision):
        raise CatalogError(
            STALE_INPUT_REVISION,
            "the asset content changed before the face document could be committed",
            details={"asset_id": asset_id},
        )
    revision_row = connection.execute(
        "SELECT COALESCE(MAX(face_revision), 0) AS revision FROM faces "
        "WHERE asset_id = ? AND spec_revision = ?",
        (asset_id, contract.FACE_SPEC_REVISION),
    ).fetchone()
    face_revision = int(revision_row["revision"]) + 1
    generation_id = _ensure_generation(connection, now_ms=int(now_ms))
    faces = list(payload["faces"])
    new_ids = {str(face["face_id"]) for face in faces}
    protected = human_controlled_face_ids(connection, asset_id=asset_id)
    _retire_stale_faces(
        connection,
        asset_id=asset_id,
        keep=new_ids,
        protected=protected,
        generation_id=generation_id,
        now_ms=int(now_ms),
    )
    _park_rewritten_ordinals(connection, asset_id=asset_id, keep=new_ids, now_ms=int(now_ms))
    ordinals = _free_ordinals(
        connection, asset_id=asset_id, keep=new_ids, wanted=len(faces)
    )
    for face, ordinal in zip(faces, ordinals, strict=True):
        _upsert_face(
            connection,
            face=face,
            asset_id=asset_id,
            ordinal=ordinal,
            input_revision=str(result.input_revision),
            location_id=payload["location_id"],
            artifact_id=str(item.ref.artifact_id),
            face_revision=face_revision,
            generation_id=generation_id,
            now_ms=int(now_ms),
        )
    connection.execute(
        "INSERT INTO face_association_queue(asset_id, spec_revision, face_revision, queued_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(asset_id) DO UPDATE SET spec_revision = excluded.spec_revision, "
        "face_revision = excluded.face_revision, queued_at = excluded.queued_at",
        (asset_id, contract.FACE_SPEC_REVISION, int(face_revision), int(now_ms)),
    )
    return True


def _retire_stale_faces(
    connection: sqlite3.Connection,
    *,
    asset_id: int,
    keep: set[str],
    protected: set[str],
    generation_id: str,
    now_ms: int,
) -> None:
    """Drop this asset's rows a new detection no longer contains, never a human-owned one."""
    rows = connection.execute(
        "SELECT face_id FROM faces WHERE asset_id = ? AND spec_revision = ?",
        (int(asset_id), contract.FACE_SPEC_REVISION),
    ).fetchall()
    stale = [
        str(row["face_id"])
        for row in rows
        if str(row["face_id"]) not in keep and str(row["face_id"]) not in protected
    ]
    for face_id in stale:
        connection.execute("DELETE FROM face_candidates WHERE face_id = ?", (face_id,))
        connection.execute("DELETE FROM face_index_entries WHERE face_id = ?", (face_id,))
        connection.execute("DELETE FROM face_index_outbox WHERE face_id = ?", (face_id,))
        connection.execute("DELETE FROM face_person_links WHERE face_id = ?", (face_id,))
        connection.execute("DELETE FROM face_embeddings WHERE face_id = ?", (face_id,))
        connection.execute("DELETE FROM faces WHERE face_id = ?", (face_id,))
    # A protected face keeps its row *and* its ordinal: the new detection has to avoid it
    # (see ``_free_ordinals``), so a human-owned anchor is never renumbered or displaced.


def _park_rewritten_ordinals(
    connection: sqlite3.Connection, *, asset_id: int, keep: set[str], now_ms: int
) -> None:
    """Move the rows this commit is about to rewrite out of the ordinal space.

    The frozen ``UNIQUE(asset_id, spec_revision, ordinal)`` is checked per statement, so a
    re-detection that reorders two faces would otherwise collide with itself while
    updating. The final ordinals are assigned immediately afterwards.
    """
    if not keep:
        return
    ordered = sorted(keep)
    placeholders = ", ".join("?" for _ in ordered)
    connection.execute(
        f"UPDATE faces SET ordinal = ordinal + ?, updated_at = ? "
        f"WHERE asset_id = ? AND spec_revision = ? AND ordinal < ? "
        f"AND face_id IN ({placeholders})",
        (
            _ORDINAL_PARK_OFFSET,
            int(now_ms),
            int(asset_id),
            contract.FACE_SPEC_REVISION,
            _ORDINAL_PARK_OFFSET,
            *ordered,
        ),
    )


def _free_ordinals(
    connection: sqlite3.Connection, *, asset_id: int, keep: set[str], wanted: int
) -> list[int]:
    """The ordinals the new detection may take, avoiding every surviving other face."""
    used = {
        int(row["ordinal"])
        for row in connection.execute(
            "SELECT ordinal, face_id FROM faces WHERE asset_id = ? AND spec_revision = ?",
            (int(asset_id), contract.FACE_SPEC_REVISION),
        ).fetchall()
        if str(row["face_id"]) not in keep
    }
    ordinals: list[int] = []
    candidate = 0
    while len(ordinals) < int(wanted):
        if candidate not in used:
            ordinals.append(candidate)
        candidate += 1
    return ordinals


def _upsert_face(
    connection: sqlite3.Connection,
    *,
    face: Mapping[str, Any],
    asset_id: int,
    ordinal: int,
    input_revision: str,
    location_id: int | None,
    artifact_id: str,
    face_revision: int,
    generation_id: str,
    now_ms: int,
) -> None:
    face_id = str(face["face_id"])
    blob = embedding_blob(face["embedding"])
    connection.execute(
        "INSERT INTO faces(face_id, asset_id, spec_revision, detector_id, ordinal, "
        "input_revision, location_id, bbox_json, landmarks_json, det_score, face_pixels, "
        "blur_variance, quality_state, quality_reason, artifact_id, face_revision, "
        "created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(face_id) DO UPDATE SET ordinal = excluded.ordinal, "
        "input_revision = excluded.input_revision, location_id = excluded.location_id, "
        "bbox_json = excluded.bbox_json, landmarks_json = excluded.landmarks_json, "
        "det_score = excluded.det_score, face_pixels = excluded.face_pixels, "
        "blur_variance = excluded.blur_variance, quality_state = excluded.quality_state, "
        "quality_reason = excluded.quality_reason, artifact_id = excluded.artifact_id, "
        "face_revision = excluded.face_revision, updated_at = excluded.updated_at",
        (
            face_id,
            int(asset_id),
            contract.FACE_SPEC_REVISION,
            contract.FACE_DETECTOR_ID,
            int(ordinal),
            str(input_revision),
            None if location_id is None else int(location_id),
            canonical_json({"bbox": [float(value) for value in face["bbox"]]}),
            canonical_json(
                {"landmarks": [[float(x), float(y)] for x, y in face["landmarks"]]}
            ),
            float(face["det_score"]),
            float(face["face_pixels"]),
            float(face["blur_variance"]),
            str(face["quality_state"]),
            face["quality_reason"],
            str(artifact_id),
            int(face_revision),
            int(now_ms),
            int(now_ms),
        ),
    )
    connection.execute(
        "INSERT INTO face_embeddings(face_id, processor_id, space_id, dimension, metric, "
        "normalization, vector_blob, vector_sha256, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(face_id, processor_id) DO UPDATE SET space_id = excluded.space_id, "
        "dimension = excluded.dimension, metric = excluded.metric, "
        "normalization = excluded.normalization, vector_blob = excluded.vector_blob, "
        "vector_sha256 = excluded.vector_sha256, updated_at = excluded.updated_at",
        (
            face_id,
            contract.FACE_PROCESSOR_ID,
            contract.FACE_SPACE_ID,
            contract.FACE_DIMENSION,
            contract.FACE_METRIC,
            contract.FACE_NORMALIZATION,
            blob,
            hashlib.sha256(blob).hexdigest(),
            int(now_ms),
            int(now_ms),
        ),
    )
    queue_candidate(
        connection,
        face_id=face_id,
        generation_id=generation_id,
        artifact_id=str(artifact_id),
        now_ms=int(now_ms),
    )


def queue_candidate(
    connection: sqlite3.Connection,
    *,
    face_id: str,
    generation_id: str,
    artifact_id: str,
    now_ms: int,
    projection_revision: int | None = None,
) -> None:
    """Put one face in the independent candidate index's outbox and entry table.

    The event is written in the same transaction as the face facts (so it cannot be lost)
    but the candidate row itself is published outside it by the coordinator and only then
    confirmed - the P2-03 discipline, in its own face-scoped namespace.
    """
    if projection_revision is None:
        projection_revision = int(
            connection.execute(
                "SELECT COALESCE(MAX(projection_revision), 0) + 1 FROM face_index_outbox "
                "WHERE space_id = ? AND face_id = ? AND generation_id = ?",
                (contract.FACE_SPACE_ID, str(face_id), str(generation_id)),
            ).fetchone()[0]
        )
    payload = {
        "face_id": str(face_id),
        "generation_id": str(generation_id),
        "projection_revision": int(projection_revision),
        "space_id": contract.FACE_SPACE_ID,
    }
    payload_json = canonical_json(payload)
    connection.execute(
        "INSERT INTO face_index_outbox(event_id, event_type, face_id, space_id, generation_id, "
        "projection_revision, payload_json, payload_checksum, state, attempts, last_error_code, "
        "created_at, updated_at) VALUES (?, 'UPSERT_FACE', ?, ?, ?, ?, ?, ?, 'PENDING', 0, NULL, "
        "?, ?) ON CONFLICT(event_id) DO NOTHING",
        (
            event_id_for(
                face_id=str(face_id),
                generation_id=str(generation_id),
                revision=int(projection_revision),
            ),
            str(face_id),
            contract.FACE_SPACE_ID,
            str(generation_id),
            int(projection_revision),
            payload_json,
            hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
            int(now_ms),
            int(now_ms),
        ),
    )
    connection.execute(
        "INSERT INTO face_index_entries(generation_id, face_id, artifact_id, "
        "confirmation_revision, confirmed_at, state) VALUES (?, ?, ?, ?, NULL, "
        "'PENDING_REPAIR') "
        "ON CONFLICT(generation_id, face_id) DO UPDATE SET artifact_id = excluded.artifact_id, "
        "confirmation_revision = excluded.confirmation_revision, confirmed_at = NULL, "
        "state = 'PENDING_REPAIR'",
        (str(generation_id), str(face_id), str(artifact_id), int(projection_revision)),
    )
