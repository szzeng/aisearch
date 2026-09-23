"""The versioned non-model CPU GPS extractor and its persistence hook.

The extractor is a ``non_model`` / ``CPU_PREFERRED`` processor: it declares no weights, no
dimension and no vector space, and it is only ever handed a *controlled* position - the
asset id comes from the job, the path comes from the registered ``file_locations`` row and
the bytes are hashed between two stat checks, so a file that changed mid-read can never
publish a fact. Publication itself happens inside the scheduler's single transaction.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from photo_search.catalog.errors import (
    ACCESS_DENIED,
    ARTIFACT_INVALID,
    DECODE_FAILED,
    FILE_READ_ERROR,
    GEO_COORDINATE_INVALID,
    INPUT_UNSTABLE,
    PLACE_NOT_FOUND,
    SOURCE_FILE_MISSING,
    SOURCE_OFFLINE,
    STALE_INPUT_REVISION,
    CatalogError,
)
from photo_search.geo.exif import (
    DATUM_ABSENT,
    DATUM_ASSUMED_WGS84,
    DATUM_UNSUPPORTED,
    DATUM_WGS84,
    STATUS_ABSENT,
    STATUS_INVALID,
    STATUS_UNSUPPORTED_CRS,
    STATUS_VALID,
    GeoExtraction,
    analyze,
    read_gps_fields,
)
from photo_search.places.contracts import CONTRACT_VERSION, geo_fact_id_for
from photo_search.scheduler.contracts import ProcessorSpec, canonical_json

GPS_PROCESSOR_ID = "geo-exif-gps@1"
GPS_TASK_TYPE = "geo_extract"
GPS_SPEC_REVISION = "geo-exif-gps-v1"
GPS_ARTIFACT_REVISION = "geo-fact.v1"
GPS_MEDIA_TYPE = "application/json"

#: The versioned CPU runtime package a P2-02 capability snapshot records for the pure Python
#: backend. A real run must record a capability with this id and a qualification row for
#: this processor; without either the job stays BLOCKED instead of being run silently.
PYTHON_RUNTIME_PACKAGE_ID = "photo-search-runtime@1"

DATUM_STATUSES = (DATUM_WGS84, DATUM_ASSUMED_WGS84, DATUM_UNSUPPORTED, DATUM_ABSENT)

#: Statuses that may be written as a *current* fact. ``SOURCE_UNAVAILABLE`` is deliberately
#: absent: a blocked read must never replace the previous fact (ADR-0005 §3).
PERSISTABLE_STATUSES = (STATUS_VALID, STATUS_ABSENT, STATUS_INVALID, STATUS_UNSUPPORTED_CRS)

PAYLOAD_KEYS = frozenset(
    {
        "contract_version",
        "processor_id",
        "input_revision",
        "location_id",
        "status",
        "source",
        "datum_status",
        "raw_gps",
        "normalized_wgs84",
        "raw_fields_checksum_sha256",
    }
)

_BLOCKING_CODES = frozenset(
    {SOURCE_FILE_MISSING, SOURCE_OFFLINE, INPUT_UNSTABLE, FILE_READ_ERROR, ACCESS_DENIED}
)

_CONFIG_HASH = hashlib.sha256(
    canonical_json({"algorithm": "exif-gps", "version": 1}).encode("utf-8")
).hexdigest()


def system_now_ms() -> int:
    return time.time_ns() // 1_000_000


def gps_processor_spec() -> ProcessorSpec:
    """The frozen ``ProcessorSpec`` for GPS extraction: CPU only, no model, no vector."""
    return ProcessorSpec.from_envelope(
        {
            "contract_version": "p2-contracts.v1",
            "spec_revision": GPS_SPEC_REVISION,
            "processor_id": GPS_PROCESSOR_ID,
            "processor_kind": "non_model",
            "task_type": GPS_TASK_TYPE,
            "config_hash": _CONFIG_HASH,
            "input_type": "asset_original",
            "output_type": "geo_fact.v1",
            "device_policy": "CPU_PREFERRED",
            "runtime_backend": "python",
            "runtime_package_id": PYTHON_RUNTIME_PACKAGE_ID,
            "supported_devices": ["cpu"],
            "supported_precisions": ["not_applicable"],
            "max_batch_size": 1,
            "cancellation_supported": True,
            "resource_measurements": [],
        }
    )


def artifact_id_for(input_revision: str) -> str:
    return f"geo-fact:{GPS_PROCESSOR_ID}:{str(input_revision)}"


def build_payload(
    *,
    input_revision: str,
    location_id: int | None,
    extraction: GeoExtraction,
) -> dict[str, Any]:
    """The deterministic structured fact an execution publishes for one content hash."""
    return {
        "contract_version": CONTRACT_VERSION,
        "processor_id": GPS_PROCESSOR_ID,
        "input_revision": str(input_revision),
        "location_id": None if location_id is None else int(location_id),
        "status": extraction.status,
        "source": "EXIF_GPS",
        "datum_status": extraction.datum_status,
        "raw_gps": extraction.raw_gps,
        "normalized_wgs84": extraction.normalized_wgs84,
        "raw_fields_checksum_sha256": extraction.raw_fields_checksum_sha256,
    }


def artifact_ref(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The ``ArtifactRef`` for one fact; the executor never chooses a storage path."""
    data = canonical_json(dict(payload)).encode("utf-8")
    return {
        "artifact_id": artifact_id_for(str(payload["input_revision"])),
        "artifact_type": "structured",
        "artifact_revision": GPS_ARTIFACT_REVISION,
        "media_type": GPS_MEDIA_TYPE,
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
) -> dict[str, Any]:
    """Read the registered original exactly once and return the fact payload."""
    row = catalog.read_one("SELECT sha256 FROM assets WHERE asset_id = ?", (int(asset_id),))
    if row is None:
        raise CatalogError(
            PLACE_NOT_FOUND,
            f"asset {int(asset_id)} does not exist",
            details={"asset_ids": [int(asset_id)]},
        )
    if str(row["sha256"]) != str(input_revision):
        raise CatalogError(
            STALE_INPUT_REVISION,
            "the job was created for a different content revision",
        )
    location = _resolve_location(catalog, int(asset_id))
    observed = _stat_pair(location)
    digest = _hash_file(location["absolute_path"])
    exif = _read_exif(location["absolute_path"])
    _verify_unchanged(location, observed)
    if digest != str(input_revision):
        raise CatalogError(
            INPUT_UNSTABLE,
            "the file bytes no longer match the catalogued content",
        )
    extraction = analyze(exif)
    return build_payload(
        input_revision=str(input_revision),
        location_id=int(location["location_id"]),
        extraction=extraction,
    )


def _resolve_location(catalog: Any, asset_id: int) -> dict[str, Any]:
    """Reuse the accepted P1-04 controlled-file boundary instead of duplicating it.

    The task card authorises ``ingestion/**`` and ``media/decode.py`` for a *minimal
    extraction* of this boundary; that extraction belongs to the task that next changes
    those modules, so this build reuses the accepted implementation verbatim and only
    translates its structured error into a ``CatalogError`` here.
    """
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


def _read_exif(path: Path) -> dict[int, Any] | None:
    from PIL import Image, UnidentifiedImageError

    # HEIC/HEIF metadata is only reachable once the one pillow-heif registration point has
    # run; it is idempotent and fails closed by leaving HEIF undecodable (P4-00C-A).
    from photo_search.media import heif

    heif.ensure_heif_support()
    try:
        with Image.open(path) as image:
            return read_gps_fields(image.getexif())
    except UnidentifiedImageError as error:
        # The bytes are not an image this build can inspect. Reporting ABSENT would claim a
        # successful empty extraction, so the job blocks instead.
        raise CatalogError(
            DECODE_FAILED,
            "the source file is not a decodable image",
            details={"reason": "unidentified"},
        ) from error
    except OSError as error:
        raise CatalogError(
            FILE_READ_ERROR,
            "cannot read image metadata",
            details={"reason": type(error).__name__},
        ) from error


# ------------------------------------------------------------------------ the executor
def gps_executor(
    catalog: Any, *, clock: Callable[[], int] | None = None
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Build the scheduler executor for ``geo_extract`` jobs over one catalog."""
    now = clock or system_now_ms

    def run(request: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "contract_version": "p2-contracts.v1",
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
            )
        except CatalogError as error:
            result.update(
                state="BLOCKED",
                progress=0.0,
                artifacts=[],
                error={
                    "code": str(error.code),
                    "message": _safe_message(error),
                    "retryable": False,
                    "blocking_reason": "SOURCE_UNAVAILABLE",
                },
            )
            return result
        result["artifacts"] = [artifact_ref(payload)]
        return result

    return run


def _safe_message(error: CatalogError) -> str:
    """A blocked reason that never carries a local path (ADR-0005 §3)."""
    if str(error.code) in _BLOCKING_CODES:
        return "the registered position could not be read for GPS extraction"
    return str(error.message)[:200]


# --------------------------------------------------------------------- payload checks
def validate_payload(payload: Mapping[str, Any]) -> None:
    """Enforce the frozen fact shape before anything reaches the catalog."""
    if not isinstance(payload, Mapping) or frozenset(payload) != PAYLOAD_KEYS:
        raise CatalogError(
            ARTIFACT_INVALID,
            "the structured geo fact does not match the frozen shape",
            details={"artifact": "geo-fact.v1"},
        )
    if str(payload["contract_version"]) != CONTRACT_VERSION:
        raise CatalogError(ARTIFACT_INVALID, "the geo fact carries a foreign contract version")
    if str(payload["processor_id"]) != GPS_PROCESSOR_ID:
        raise CatalogError(ARTIFACT_INVALID, "the geo fact was produced by another processor")
    if str(payload["source"]) != "EXIF_GPS":
        raise CatalogError(ARTIFACT_INVALID, "the geo fact source is not EXIF_GPS")
    status = str(payload["status"])
    if status not in PERSISTABLE_STATUSES:
        raise CatalogError(
            ARTIFACT_INVALID,
            "this status may not become a current fact",
            details={"status": status},
        )
    checksum = str(payload["raw_fields_checksum_sha256"])
    if len(checksum) != 64 or any(char not in "0123456789abcdef" for char in checksum):
        raise CatalogError(ARTIFACT_INVALID, "the raw-field checksum is not a sha256")
    raw = payload["raw_gps"]
    normalized = payload["normalized_wgs84"]
    datum_status = str(payload["datum_status"])
    if datum_status not in DATUM_STATUSES:
        raise CatalogError(
            ARTIFACT_INVALID, "unknown datum status", details={"datum_status": datum_status}
        )
    if status == STATUS_ABSENT and (raw is not None or normalized is not None or datum_status != DATUM_ABSENT):
        raise CatalogError(ARTIFACT_INVALID, "an ABSENT fact must carry no coordinates")
    if status == STATUS_VALID and (
        raw is None or normalized is None or datum_status not in (DATUM_WGS84, DATUM_ASSUMED_WGS84)
    ):
        raise CatalogError(
            ARTIFACT_INVALID, "a VALID fact needs raw and normalised coordinates"
        )
    if status in (STATUS_INVALID, STATUS_UNSUPPORTED_CRS) and normalized is not None:
        raise CatalogError(
            ARTIFACT_INVALID, "only a VALID fact may expose a searchable coordinate"
        )
    if normalized is not None:
        latitude = normalized.get("latitude")
        longitude = normalized.get("longitude")
        for value in (latitude, longitude):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise CatalogError(
                    GEO_COORDINATE_INVALID, "a coordinate must be a finite number"
                )
        if not -90.0 <= float(latitude) <= 90.0 or not -180.0 <= float(longitude) <= 180.0:
            raise CatalogError(
                GEO_COORDINATE_INVALID, "a coordinate is outside its valid range"
            )


# ----------------------------------------------------------------------- persistence
def persist_geo_fact(
    connection: sqlite3.Connection,
    *,
    job: Any,
    result: Any,
    prepared: Any,
    now_ms: int,
) -> bool:
    """Write the current fact for one asset inside the caller's transaction."""
    facts = [
        item
        for item in prepared
        if str(item.ref.artifact_type) == "structured"
        and str(item.ref.artifact_id).startswith("geo-fact:")
    ]
    if not facts:
        return False
    item = facts[0]
    try:
        payload = json.loads(item.payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CatalogError(ARTIFACT_INVALID, "the geo fact payload is not valid JSON") from error
    validate_payload(payload)
    asset_id = int(job.subject_id)
    row = connection.execute(
        "SELECT sha256 FROM assets WHERE asset_id = ?", (asset_id,)
    ).fetchone()
    if row is None:
        raise CatalogError(
            ARTIFACT_INVALID,
            "the geo fact subject asset does not exist",
            details={"asset_ids": [asset_id]},
        )
    if str(row["sha256"]) != str(result.input_revision):
        raise CatalogError(
            STALE_INPUT_REVISION,
            "the asset content changed before the fact could be committed",
            details={"asset_ids": [asset_id]},
        )
    current = connection.execute(
        "SELECT fact_revision FROM asset_geo_facts WHERE asset_id = ?", (asset_id,)
    ).fetchone()
    fact_revision = 1 if current is None else int(current["fact_revision"]) + 1
    normalized = payload["normalized_wgs84"]
    raw_json = None if payload["raw_gps"] is None else canonical_json(payload["raw_gps"])
    connection.execute(
        "INSERT INTO asset_geo_facts(asset_id, geo_fact_id, location_id, input_revision, "
        "fact_revision, status, source, processor_id, raw_fields_checksum_sha256, raw_gps_json, "
        "latitude, longitude, datum_status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'EXIF_GPS', ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(asset_id) DO UPDATE SET geo_fact_id = excluded.geo_fact_id, "
        "location_id = excluded.location_id, input_revision = excluded.input_revision, "
        "fact_revision = excluded.fact_revision, status = excluded.status, "
        "processor_id = excluded.processor_id, "
        "raw_fields_checksum_sha256 = excluded.raw_fields_checksum_sha256, "
        "raw_gps_json = excluded.raw_gps_json, latitude = excluded.latitude, "
        "longitude = excluded.longitude, datum_status = excluded.datum_status, "
        "updated_at = excluded.updated_at",
        (
            asset_id,
            geo_fact_id_for(str(result.input_revision), fact_revision),
            payload["location_id"],
            str(result.input_revision),
            int(fact_revision),
            str(payload["status"]),
            GPS_PROCESSOR_ID,
            str(payload["raw_fields_checksum_sha256"]),
            raw_json,
            None if normalized is None else float(normalized["latitude"]),
            None if normalized is None else float(normalized["longitude"]),
            str(payload["datum_status"]),
            int(now_ms),
            int(now_ms),
        ),
    )
    return True
