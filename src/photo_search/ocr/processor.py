"""The OCR ``ProcessorSpec``, its structured artifact and its persistence hook.

The processor uses the additive ``structured_model`` branch: pinned model weights with a
structured document result and no vector of any kind - ``embedding``, ``space_id`` and
``dimension`` never appear in an OCR result.

The split between the two durable stores follows the P3-01 invariants exactly:

* the immutable **structured artifact** carries the original, uncorrected text and every
  region (polygon, box, text, confidence) in reading order, plus the processor/weight
  identity and the content revision it was produced for. It is deterministic: the same
  pixels produce the same bytes, so a re-execution reuses the existing artifact row;
* **SQLite** carries the normalised search copy (``asset_ocr_text.search_text``) and the
  region metadata (index, box, confidence) - no original text and no polygon.

Publication happens inside the scheduler's single transaction, so a job that lost its lease
or whose content changed before the commit can never leave a document behind.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from photo_search.catalog.errors import (
    ACCESS_DENIED,
    ARTIFACT_INVALID,
    ASSET_NOT_FOUND,
    FILE_READ_ERROR,
    INPUT_UNSTABLE,
    OCR_DEADLINE_EXCEEDED,
    OCR_RESULT_TOO_LARGE,
    OCR_WEIGHTS_HASH_MISMATCH,
    OCR_WEIGHTS_MISSING,
    RUNTIME_CAPABILITY_UNAVAILABLE,
    SOURCE_FILE_MISSING,
    SOURCE_OFFLINE,
    STALE_INPUT_REVISION,
    CatalogError,
)
from photo_search.ocr import contract
from photo_search.ocr.engine import (
    OcrCancelled,
    OcrDocument,
    OcrEngineCache,
    recognize_image,
)
from photo_search.scheduler.contracts import ProcessorSpec, canonical_json
from photo_search.search.tokens import TOKENIZER_VERSION, index_text

ARTIFACT_ID_PREFIX = "ocr-document:"

PAYLOAD_KEYS = frozenset(
    {
        "contract_version",
        "processor_id",
        "input_revision",
        "location_id",
        "source",
        "engine",
        "engine_version",
        "model_fingerprint",
        "config_hash",
        "engine_config",
        "decode_format",
        "original_size",
        "working_size",
        "scale",
        "slice_count",
        "region_count",
        "low_confidence_threshold",
        "low_confidence_regions",
        "original_text",
        "regions",
    }
)

#: Failures of the *environment* (a source that cannot be read, weights that are not
#: installed): the job blocks without burning an attempt, exactly like the P2-03A GPS
#: processor treats an unavailable source.
_BLOCKING_CODES = frozenset(
    {
        SOURCE_FILE_MISSING,
        SOURCE_OFFLINE,
        INPUT_UNSTABLE,
        FILE_READ_ERROR,
        ACCESS_DENIED,
        OCR_WEIGHTS_MISSING,
        OCR_WEIGHTS_HASH_MISMATCH,
    }
)


def system_now_ms() -> int:
    return time.time_ns() // 1_000_000


def onnx_runtime_package_id() -> str:
    """The runtime package this process actually resolved (P0-04 manifest: 1.30.0)."""
    import onnxruntime

    return f"onnxruntime-gpu@{onnxruntime.__version__}"


def ocr_processor_spec() -> ProcessorSpec:
    """The frozen ``ProcessorSpec`` of the OCR structured model: CUDA/CPU, no vector."""
    weights = [
        {
            "name": str(entry["role"]),
            "revision": contract.OCR_MODEL_REVISION,
            "sha256": str(entry["sha256"]),
        }
        for entry in contract.PINNED_WEIGHTS
        if str(entry["role"]) != "config"
    ]
    return ProcessorSpec.from_envelope(
        {
            "contract_version": contract.CONTRACT_VERSION,
            "spec_revision": contract.OCR_SPEC_REVISION,
            "processor_id": contract.OCR_PROCESSOR_ID,
            "processor_kind": contract.OCR_PROCESSOR_KIND,
            "task_type": contract.OCR_TASK_TYPE,
            "config_hash": contract.CONFIG_HASH,
            "input_type": contract.OCR_INPUT_TYPE,
            "output_type": contract.OCR_OUTPUT_TYPE,
            "device_policy": contract.OCR_DEVICE_POLICY,
            "runtime_backend": contract.OCR_RUNTIME_BACKEND,
            "runtime_package_id": onnx_runtime_package_id(),
            "supported_devices": list(contract.OCR_SUPPORTED_DEVICES),
            "supported_precisions": list(contract.OCR_SUPPORTED_PRECISIONS),
            "max_batch_size": 1,
            "cancellation_supported": True,
            "resource_measurements": [],
            "model_id": contract.OCR_MODEL_ID,
            "model_revision": contract.OCR_MODEL_REVISION,
            "weights": weights,
        }
    )


def artifact_id_for(input_revision: str) -> str:
    return f"{ARTIFACT_ID_PREFIX}{contract.OCR_PROCESSOR_ID}:{str(input_revision)}"


def ocr_document_id_for(input_revision: str, text_revision: int) -> str:
    return f"ocr:{contract.OCR_PROCESSOR_ID}:{str(input_revision)}:{int(text_revision)}"


def build_payload(
    *,
    input_revision: str,
    location_id: int | None,
    document: OcrDocument,
    decode_format: str,
) -> dict[str, Any]:
    """The deterministic document artifact of one content revision.

    Deliberately free of anything that varies between two runs (wall-clock timings, local
    paths, the job id): the artifact is content-addressed, so a deterministic repair pass
    has to reproduce the identical bytes.
    """
    regions = [region.as_dict() for region in document.regions]
    return {
        "contract_version": contract.CONTRACT_VERSION,
        "processor_id": contract.OCR_PROCESSOR_ID,
        "input_revision": str(input_revision),
        "location_id": None if location_id is None else int(location_id),
        "source": contract.OCR_SOURCE,
        "engine": contract.OCR_ENGINE,
        "engine_version": contract.ENGINE_VERSION,
        "model_fingerprint": contract.MODEL_FINGERPRINT,
        "config_hash": contract.CONFIG_HASH,
        "engine_config": dict(contract.ENGINE_CONFIG),
        "decode_format": str(decode_format),
        "original_size": [int(document.original_size[0]), int(document.original_size[1])],
        "working_size": [int(document.working_size[0]), int(document.working_size[1])],
        "scale": float(document.scale),
        "slice_count": int(document.slice_count),
        "region_count": len(regions),
        "low_confidence_threshold": float(contract.LOW_CONFIDENCE_THRESHOLD),
        "low_confidence_regions": int(document.low_confidence_count),
        "original_text": document.original_text,
        "regions": regions,
    }


def artifact_ref(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The ``ArtifactRef`` for one document; the executor never chooses a storage path."""
    data = canonical_json(dict(payload)).encode("utf-8")
    return {
        "artifact_id": artifact_id_for(str(payload["input_revision"])),
        "artifact_type": "structured",
        "artifact_revision": contract.OCR_ARTIFACT_REVISION,
        "media_type": contract.OCR_MEDIA_TYPE,
        "checksum_sha256": hashlib.sha256(data).hexdigest(),
        "byte_size": len(data),
        "storage_uri": None,
        "payload": dict(payload),
        "space_id": None,
        "dimension": None,
    }


# --------------------------------------------------------------------------- extraction
def extract_for_asset(
    catalog: Any,
    *,
    asset_id: int,
    input_revision: str,
    data_root: str | Path | None = None,
    engine_cache: OcrEngineCache | None = None,
    resolved_device: str = "cpu",
    runtime_provider: str | None = None,
    precision: str = "FP32",
    cancel: Callable[[], bool] | None = None,
    deadline_ms: int | None = None,
    clock: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """Read the registered original, recognise it and return the document payload."""
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
    cache = engine_cache or OcrEngineCache()
    engine = cache.engine(
        data_root if data_root is not None else catalog.data_root,
        resolved_device=resolved_device,
        runtime_provider=runtime_provider,
        precision=precision,
    )
    document = recognize_image(
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

    return load_normalized(path, long_edge=contract.MAX_LONG_EDGE)


def _safe_message(error: CatalogError) -> str:
    """A blocking reason that never carries a local path (P2-03A privacy boundary)."""
    if str(error.code) in _BLOCKING_CODES:
        return "the registered original could not be read for OCR"
    return str(error.message)[:200]


# ------------------------------------------------------------------------- the executor
def ocr_executor(
    catalog: Any,
    *,
    data_root: str | Path | None = None,
    engine_cache: OcrEngineCache | None = None,
    cancel: Callable[[], bool] | None = None,
    deadline_ms: int | None = None,
    clock: Callable[[], float] | None = None,
    wall_clock: Callable[[], int] | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Build the scheduler executor for ``image_ocr`` jobs over one catalog."""
    now = wall_clock or system_now_ms
    cache = engine_cache or OcrEngineCache()

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
                data_root=data_root,
                engine_cache=cache,
                resolved_device=str(request["resolved_device"]),
                runtime_provider=str(request["runtime_provider"]),
                precision=str(request["precision"]),
                cancel=cancel,
                deadline_ms=deadline_ms,
                clock=clock,
            )
        except OcrCancelled:
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
            if code == OCR_DEADLINE_EXCEEDED:
                # A bounded CPU budget that ran out is a transient condition: the job may
                # be retried (and consumes one attempt), it is never a silent success.
                state, retryable, blocking = "RETRY_WAIT", True, None
            elif code in _BLOCKING_CODES or code == RUNTIME_CAPABILITY_UNAVAILABLE:
                state, retryable, blocking = "BLOCKED", False, "SOURCE_UNAVAILABLE"
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
    """Enforce the frozen document shape before anything reaches the catalog."""
    if not isinstance(payload, Mapping) or frozenset(payload) != PAYLOAD_KEYS:
        raise CatalogError(
            ARTIFACT_INVALID,
            "the OCR document does not match the frozen shape",
            details={"artifact": contract.OCR_ARTIFACT_REVISION},
        )
    if str(payload["contract_version"]) != contract.CONTRACT_VERSION:
        raise CatalogError(ARTIFACT_INVALID, "the OCR document carries a foreign contract")
    if str(payload["processor_id"]) != contract.OCR_PROCESSOR_ID:
        raise CatalogError(ARTIFACT_INVALID, "the OCR document was produced by another processor")
    if str(payload["source"]) != contract.OCR_SOURCE:
        raise CatalogError(ARTIFACT_INVALID, "the OCR document source is not the pinned engine")
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
    slices = _positive_int(payload["slice_count"], where="slice_count")
    if slices > contract.MAX_SLICES:
        raise CatalogError(ARTIFACT_INVALID, "the document reports more slices than allowed")
    threshold = _number(
        payload["low_confidence_threshold"], where="low_confidence_threshold", low=0.0, high=1.0
    )
    regions = payload["regions"]
    if not isinstance(regions, (list, tuple)):
        raise CatalogError(ARTIFACT_INVALID, "regions must be a list")
    if int(payload["region_count"]) != len(regions):
        raise CatalogError(ARTIFACT_INVALID, "region_count does not match the region list")
    if len(regions) > contract.MAX_REGIONS:
        raise CatalogError(
            OCR_RESULT_TOO_LARGE,
            "the recognised document has more regions than one artifact may carry",
            details={"regions": len(regions), "max_regions": contract.MAX_REGIONS},
        )
    texts: list[str] = []
    low = 0
    for expected_index, region in enumerate(regions):
        if not isinstance(region, Mapping):
            raise CatalogError(ARTIFACT_INVALID, "a region must be an object")
        if int(region.get("index", -1)) != expected_index:
            raise CatalogError(ARTIFACT_INVALID, "region indices must be 0..n-1 in order")
        box = region.get("box")
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            raise CatalogError(ARTIFACT_INVALID, "a region box must have four numbers")
        left, top, right, bottom = (
            _number(value, where="box", low=-1e7, high=1e7) for value in box
        )
        if right <= left or bottom <= top:
            raise CatalogError(ARTIFACT_INVALID, "a region box must have a positive area")
        polygon = region.get("polygon")
        if not isinstance(polygon, (list, tuple)) or len(polygon) < 4:
            raise CatalogError(ARTIFACT_INVALID, "a region polygon needs at least four points")
        for point in polygon:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise CatalogError(ARTIFACT_INVALID, "a polygon point must be a pair")
            _number(point[0], where="polygon.x", low=-1e7, high=1e7)
            _number(point[1], where="polygon.y", low=-1e7, high=1e7)
        text = region.get("text")
        if not isinstance(text, str) or not text:
            raise CatalogError(ARTIFACT_INVALID, "a region must carry its recognised text")
        confidence = _number(region.get("confidence"), where="confidence", low=0.0, high=1.0)
        if confidence <= threshold:
            low += 1
        texts.append(text)
    if str(payload["original_text"]) != "\n".join(texts):
        raise CatalogError(
            ARTIFACT_INVALID, "original_text must be the region texts in reading order"
        )
    if int(payload["low_confidence_regions"]) != low:
        raise CatalogError(ARTIFACT_INVALID, "low_confidence_regions does not match the regions")


# ------------------------------------------------------------------------- persistence
def persist_ocr_document(
    connection: sqlite3.Connection,
    *,
    job: Any,
    result: Any,
    prepared: Any,
    now_ms: int,
) -> bool:
    """Write the current OCR document of one asset inside the caller's transaction."""
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
        raise CatalogError(ARTIFACT_INVALID, "the OCR document payload is not valid JSON") from error
    validate_payload(payload)
    asset_id = int(job.subject_id)
    row = connection.execute(
        "SELECT sha256 FROM assets WHERE asset_id = ?", (asset_id,)
    ).fetchone()
    if row is None:
        raise CatalogError(ARTIFACT_INVALID, "the OCR document subject asset does not exist")
    if str(row["sha256"]) != str(result.input_revision):
        raise CatalogError(
            STALE_INPUT_REVISION,
            "the asset content changed before the OCR document could be committed",
            details={"asset_id": asset_id},
        )
    current = connection.execute(
        "SELECT text_revision FROM asset_ocr_text WHERE asset_id = ?", (asset_id,)
    ).fetchone()
    text_revision = 1 if current is None else int(current["text_revision"]) + 1
    confidences = [float(region["confidence"]) for region in payload["regions"]]
    connection.execute(
        "INSERT INTO asset_ocr_text(asset_id, ocr_document_id, input_revision, location_id, "
        "text_revision, processor_id, spec_revision, model_fingerprint, artifact_id, "
        "region_count, min_confidence, mean_confidence, max_confidence, search_text, "
        "tokenizer_version, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(asset_id) DO UPDATE SET ocr_document_id = excluded.ocr_document_id, "
        "input_revision = excluded.input_revision, location_id = excluded.location_id, "
        "text_revision = excluded.text_revision, processor_id = excluded.processor_id, "
        "spec_revision = excluded.spec_revision, "
        "model_fingerprint = excluded.model_fingerprint, artifact_id = excluded.artifact_id, "
        "region_count = excluded.region_count, min_confidence = excluded.min_confidence, "
        "mean_confidence = excluded.mean_confidence, max_confidence = excluded.max_confidence, "
        "search_text = excluded.search_text, tokenizer_version = excluded.tokenizer_version, "
        "updated_at = excluded.updated_at",
        (
            asset_id,
            ocr_document_id_for(str(result.input_revision), text_revision),
            str(result.input_revision),
            payload["location_id"],
            int(text_revision),
            contract.OCR_PROCESSOR_ID,
            contract.OCR_SPEC_REVISION,
            str(payload["model_fingerprint"]),
            str(item.ref.artifact_id),
            len(confidences),
            None if not confidences else min(confidences),
            None if not confidences else sum(confidences) / len(confidences),
            None if not confidences else max(confidences),
            index_text(str(payload["original_text"])),
            int(TOKENIZER_VERSION),
            int(now_ms),
            int(now_ms),
        ),
    )
    connection.execute("DELETE FROM asset_ocr_regions WHERE asset_id = ?", (asset_id,))
    connection.executemany(
        "INSERT INTO asset_ocr_regions(asset_id, region_index, text_revision, box_json, "
        "confidence) VALUES (?, ?, ?, ?, ?)",
        [
            (
                asset_id,
                int(region["index"]),
                int(text_revision),
                canonical_json({"box": [float(value) for value in region["box"]]}),
                float(region["confidence"]),
            )
            for region in payload["regions"]
        ],
    )
    return True
