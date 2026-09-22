"""The ``taken_time_extract`` processor: the frozen fallback chain and its persistence.

The processor is an ordinary P2-02 scheduler job: ``non_model`` / ``CPU_PREFERRED`` over the
pure-Python runtime package, exactly like the GPS extractor. It declares no weights, no
dimension and no vector space, and it is only ever handed a *controlled* position - the
asset id comes from the job, the path comes from the registered ``file_locations`` row (via
the accepted P1-04 boundary), and the file is observed before and after the header read, so
a file that changed mid-read can never publish a fact.

ADR-0011 §4.1 is the authority for the chain
``EXIF_ORIGINAL → EXIF_DIGITIZED → EXIF_ROOT → FILE_MTIME``: a bad value is dropped and the
chain continues, the tail is ``min(mtime_ns)`` over *all* positions, and there is no scan
time / current time / ``assets.created_at`` fallback anywhere.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from photo_search.catalog.errors import (
    ARTIFACT_INVALID,
    ASSET_NOT_FOUND,
    STALE_INPUT_REVISION,
    CatalogError,
)
from photo_search.places import revision as revision_module
from photo_search.scheduler.contracts import ProcessorSpec, canonical_json
from photo_search.taken_time import contract
from photo_search.taken_time import exif as exif_module

TAKEN_TIME_PROCESSOR_ID = "taken-time-exif@1"
TAKEN_TIME_TASK_TYPE = "taken_time_extract"
TAKEN_TIME_SPEC_REVISION = "taken-time-exif-v1"
TAKEN_TIME_ARTIFACT_REVISION = "taken-time.v1"
TAKEN_TIME_MEDIA_TYPE = "application/json"

#: The versioned CPU runtime package a P2-02 capability snapshot records for the pure Python
#: backend (the same package the GPS extractor uses).
PYTHON_RUNTIME_PACKAGE_ID = "photo-search-runtime@1"

#: ADR-0011 §6.6: the cause this processor records on the shared filter revision.
CAUSE_CAPTURE_TIME_CHANGED = "CAPTURE_TIME_CHANGED"

#: The closed artifact payload of one extracted asset.
PAYLOAD_KEYS = frozenset(
    {
        "contract_version",
        "processor_id",
        "asset_id",
        "location_id",
        "taken_at_raw",
        "taken_at_sort",
        "time_source",
        "time_confidence",
        "timezone_offset",
        "camera",
    }
)

_CONFIG_HASH = hashlib.sha256(
    canonical_json({"algorithm": "exif-taken-time", "version": contract.RULES_VERSION}).encode(
        "utf-8"
    )
).hexdigest()


def system_now_ms() -> int:
    return time.time_ns() // 1_000_000


def taken_time_processor_spec() -> ProcessorSpec:
    """The frozen ``ProcessorSpec``: CPU only, no model, no vector, one asset per job."""
    return ProcessorSpec.from_envelope(
        {
            "contract_version": "p2-contracts.v1",
            "spec_revision": TAKEN_TIME_SPEC_REVISION,
            "processor_id": TAKEN_TIME_PROCESSOR_ID,
            "processor_kind": "non_model",
            "task_type": TAKEN_TIME_TASK_TYPE,
            "config_hash": _CONFIG_HASH,
            "input_type": "asset_original",
            "output_type": "taken-time.v1",
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
    return f"taken-time:{TAKEN_TIME_PROCESSOR_ID}:{str(input_revision)}"


#: How many hex characters of the observation digest an input revision carries. The digest
#: is a full sha256; the revision keeps a bounded, readable prefix of it.
OBSERVATION_CHARS = 16


def observation_digest(locations: Iterable[Any]) -> str:
    """The digest of the catalog's *recorded observation* of every position of one asset.

    ADR-0011 §2 keeps the file path, availability and file-time observations in
    ``file_locations``, and ADR-0002 §5.4 makes ``(size, mtime_ns)`` the observation guard.
    A taken-time fact is therefore a function of the content hash **and** of that recorded
    observation: re-reading a file whose recorded ``mtime_ns`` moved is a different input, not
    a replay of the same one, and the extraction has to publish a new fact for it. Folding the
    digest into the job's ``input_revision`` is what makes that an ordinary new logical job
    (ADR-0003 §8) instead of a conflicting second artifact for the same one.
    """
    payload = [
        {
            "location_id": int(row["location_id"]),
            "availability": str(row["availability"]),
            "size": int(row["size"]),
            "mtime_ns": int(row["mtime_ns"]),
        }
        for row in locations
    ]
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def input_revision_for(content_sha256: str, observation: str) -> str:
    """``"<content sha256>:<observation prefix>"`` - the composite input of this processor."""
    return f"{str(content_sha256)}:{str(observation)[:OBSERVATION_CHARS]}"


def split_input_revision(input_revision: str) -> tuple[str, str | None]:
    """``(content sha256, observation prefix)``; a bare hash keeps its historical meaning."""
    content, _, observation = str(input_revision).partition(":")
    return content, (observation or None)


def observation_matches(locations: Iterable[Any], observation: str | None) -> bool:
    """Whether the catalog's current observation still is the one this input names."""
    if observation is None:
        return True
    return observation_digest(locations)[:OBSERVATION_CHARS] == str(observation)


def artifact_ref(payload: Mapping[str, Any], *, input_revision: str) -> dict[str, Any]:
    """The ``ArtifactRef`` of one fact; the executor never chooses a storage path."""
    data = canonical_json(dict(payload)).encode("utf-8")
    return {
        "artifact_id": artifact_id_for(str(input_revision)),
        "artifact_type": "structured",
        "artifact_revision": TAKEN_TIME_ARTIFACT_REVISION,
        "media_type": TAKEN_TIME_MEDIA_TYPE,
        "checksum_sha256": hashlib.sha256(data).hexdigest(),
        "byte_size": len(data),
        "storage_uri": None,
        "payload": dict(payload),
        "space_id": None,
        "dimension": None,
    }


# --------------------------------------------------------------------------- extraction
def extract_for_asset(catalog: Any, *, asset_id: int, input_revision: str) -> dict[str, Any] | None:
    """Read one asset's controlled positions and return its frozen taken-time payload.

    ``None`` means "this asset has no registered position at all", the one shape the frozen
    model does not produce; such an asset is left un-extracted instead of being given an
    invented file time.
    """
    row = catalog.read_one("SELECT sha256 FROM assets WHERE asset_id = ?", (int(asset_id),))
    if row is None:
        raise CatalogError(
            ASSET_NOT_FOUND,
            f"asset {int(asset_id)} does not exist",
            details={"asset_ids": [int(asset_id)]},
        )
    content, observation = split_input_revision(input_revision)
    if str(row["sha256"]) != content:
        raise CatalogError(
            STALE_INPUT_REVISION,
            "the job was created for a different content revision",
            details={"asset_ids": [int(asset_id)]},
        )
    locations = catalog.read_all(
        "SELECT location_id, availability, size, mtime_ns FROM file_locations "
        "WHERE asset_id = ? ORDER BY location_id",
        (int(asset_id),),
    )
    if not observation_matches(locations, observation):
        # The *catalog's* observation moved after this job was created: the recorded file
        # times are no longer the input this execution was dispatched for, so the asset is
        # left for the next extraction instead of being answered from a mixed input.
        raise CatalogError(
            STALE_INPUT_REVISION,
            "the registered observations changed after this job was created",
            details={"asset_ids": [int(asset_id)]},
        )
    if not locations:
        return None
    fact = _fact_from_positions(catalog, asset_id=int(asset_id), locations=locations)
    return build_payload(asset_id=int(asset_id), fact=fact)


@dataclass(frozen=True)
class _TimeFact:
    """One chosen source of the frozen chain, before it becomes the artifact payload."""

    location_id: int | None
    taken_at_raw: str
    taken_at_sort: int | None
    time_source: str
    time_confidence: str
    timezone_offset: int | None
    camera: str | None


#: Which parsed field supplies the internal ``taken_at_raw`` evidence of each source.
_RAW_BY_SOURCE: Mapping[str, str] = {
    "EXIF_ORIGINAL": "original",
    "EXIF_DIGITIZED": "digitized",
    "EXIF_ROOT": "root",
}


def _fact_from_positions(catalog: Any, *, asset_id: int, locations: list[Any]) -> _TimeFact:
    """Walk the controlled ``PRESENT`` positions, then fall back to the file time."""
    from photo_search.api import files as files_module

    camera: str | None = None
    for position in files_module.resolve_present_locations(catalog, int(asset_id)):
        fields = _read_fields(position)
        if fields is None:
            continue
        if camera is None:
            camera = fields.camera
        chosen = _choose_from_fields(fields)
        if chosen is not None:
            source, key, offset, confidence = chosen
            return _TimeFact(
                location_id=int(position.location_id),
                taken_at_raw=str(getattr(fields, _RAW_BY_SOURCE[source])),
                taken_at_sort=int(key),
                time_source=source,
                time_confidence=confidence,
                timezone_offset=offset,
                camera=camera,
            )
    # The chain tail: the smallest recorded mtime of *every* position, MISSING included, so
    # unplugging one source disk cannot change an asset's file time.
    mtime_ns = min(int(row["mtime_ns"]) for row in locations)
    return _TimeFact(
        location_id=None,
        taken_at_raw=_iso_from_ns(mtime_ns),
        taken_at_sort=None,
        time_source="FILE_MTIME",
        time_confidence="LOW",
        timezone_offset=None,
        camera=camera,
    )


def _read_fields(position: Any) -> exif_module.ExifTimeFields | None:
    """Read one position's header under the before/after observation guard.

    A position whose bytes cannot be opened, or whose ``(size, mtime_ns)`` moved while it
    was being read, simply contributes no EXIF evidence (ADR-0011 §4.3). It is never
    adopted and never raised as an error: the chain falls through, and a later forced
    extraction re-reads the asset.
    """
    observed = (int(position.size), int(position.mtime_ns))
    try:
        fields = exif_module.read_time_fields(position.absolute_path)
        info = position.absolute_path.stat()
    except OSError:
        return None
    except Exception:  # noqa: BLE001 - an undecodable container is "no EXIF evidence"
        return None
    if (int(info.st_size), int(info.st_mtime_ns)) != observed:
        return None
    return fields


def _choose_from_fields(
    fields: exif_module.ExifTimeFields,
) -> tuple[str, int, int | None, str] | None:
    """The frozen priority chain over one header's fields, or ``None`` if all three fail."""
    for source, raw, offset_raw, subsec_raw in (
        (
            "EXIF_ORIGINAL",
            fields.original,
            fields.original_offset,
            fields.original_subsec,
        ),
        (
            "EXIF_DIGITIZED",
            fields.digitized,
            fields.digitized_offset,
            fields.digitized_subsec,
        ),
        ("EXIF_ROOT", fields.root, fields.root_offset, None),
    ):
        key = exif_module.parse_exif_datetime(raw)
        if key is None:
            continue
        offset = exif_module.parse_offset_minutes(offset_raw)
        millis = 0 if subsec_raw is None else exif_module.parse_subsec_millis(subsec_raw)
        return (
            source,
            int(key) + int(millis),
            offset,
            exif_module.confidence_for(source, offset),
        )
    return None


def _iso_from_ns(value: int) -> str:
    """The UTC ISO evidence text of one ``mtime_ns`` (internal only, never published)."""
    moment = datetime.fromtimestamp(int(value) / 1_000_000_000, tz=timezone.utc)
    return moment.isoformat(timespec="microseconds")


def build_payload(*, asset_id: int, fact: _TimeFact) -> dict[str, Any]:
    """The deterministic structured fact one execution publishes for one asset."""
    return {
        "contract_version": contract.CONTRACT_VERSION,
        "processor_id": TAKEN_TIME_PROCESSOR_ID,
        "asset_id": int(asset_id),
        "location_id": None if fact.location_id is None else int(fact.location_id),
        "taken_at_raw": str(fact.taken_at_raw),
        "taken_at_sort": None if fact.taken_at_sort is None else int(fact.taken_at_sort),
        "time_source": str(fact.time_source),
        "time_confidence": str(fact.time_confidence),
        "timezone_offset": None if fact.timezone_offset is None else int(fact.timezone_offset),
        "camera": fact.camera,
    }


# ------------------------------------------------------------------------ the executor
def taken_time_executor(
    catalog: Any, *, clock: Callable[[], int] | None = None
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Build the scheduler executor for ``taken_time_extract`` jobs over one catalog."""
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
        payload = extract_for_asset(
            catalog,
            asset_id=int(request["subject_id"]),
            input_revision=str(request["input_revision"]),
        )
        if payload is not None:
            result["artifacts"] = [
                artifact_ref(payload, input_revision=str(request["input_revision"]))
            ]
        return result

    return run


# --------------------------------------------------------------------- payload checks
def validate_payload(payload: Mapping[str, Any]) -> None:
    """Enforce the frozen fact shape before anything reaches the catalog."""
    if not isinstance(payload, Mapping) or frozenset(payload) != PAYLOAD_KEYS:
        raise CatalogError(
            ARTIFACT_INVALID,
            "the structured taken-time fact does not match the frozen shape",
            details={"artifact": TAKEN_TIME_ARTIFACT_REVISION},
        )
    if str(payload["contract_version"]) != contract.CONTRACT_VERSION:
        raise CatalogError(
            ARTIFACT_INVALID, "the taken-time fact carries a foreign contract version"
        )
    if str(payload["processor_id"]) != TAKEN_TIME_PROCESSOR_ID:
        raise CatalogError(
            ARTIFACT_INVALID, "the taken-time fact was produced by another processor"
        )
    source = str(payload["time_source"])
    if source not in contract.TIME_SOURCES:
        raise CatalogError(
            ARTIFACT_INVALID,
            "the taken-time fact names an unknown source",
            details={"time_source": source},
        )
    confidence = str(payload["time_confidence"])
    if confidence not in contract.TIME_CONFIDENCES:
        raise CatalogError(
            ARTIFACT_INVALID,
            "the taken-time fact names an unknown confidence",
            details={"time_confidence": confidence},
        )
    sort_key = payload["taken_at_sort"]
    offset = payload["timezone_offset"]
    if source == "FILE_MTIME":
        if sort_key is not None or offset is not None:
            raise CatalogError(
                ARTIFACT_INVALID, "a file-time fact carries no civil key and no offset"
            )
        if confidence != "LOW":
            raise CatalogError(ARTIFACT_INVALID, "a file-time fact is never better than LOW")
    else:
        if (
            isinstance(sort_key, bool)
            or not isinstance(sort_key, int)
            or sort_key < 0
            or sort_key > contract.MAX_SORT_KEY
        ):
            raise CatalogError(
                ARTIFACT_INVALID, "an EXIF fact needs a civil key inside the frozen range"
            )
        if offset is not None and (
            isinstance(offset, bool)
            or not isinstance(offset, int)
            or offset < contract.MIN_OFFSET_MINUTES
            or offset > contract.MAX_OFFSET_MINUTES
        ):
            raise CatalogError(
                ARTIFACT_INVALID, "the recorded timezone offset is outside its range"
            )
        expected = str(contract.SOURCE_RULES[source]["capture_confidence"])
        allowed = {expected, "MEDIUM"} if source == "EXIF_ORIGINAL" else {expected}
        if confidence not in allowed:
            raise CatalogError(
                ARTIFACT_INVALID, "the confidence does not belong to that source"
            )
        if source == "EXIF_ORIGINAL" and confidence == "HIGH" and offset is None:
            raise CatalogError(
                ARTIFACT_INVALID, "HIGH confidence requires a recorded offset"
            )
    raw = payload["taken_at_raw"]
    if not isinstance(raw, str) or not raw:
        raise CatalogError(ARTIFACT_INVALID, "the raw evidence text must be non-empty")
    camera = payload["camera"]
    if camera is not None and (
        not isinstance(camera, str) or not 1 <= len(camera) <= 128
    ):
        raise CatalogError(ARTIFACT_INVALID, "the normalized camera does not fit its bound")


# ----------------------------------------------------------------------- persistence
_COLUMNS = (
    "taken_at_raw",
    "taken_at_sort",
    "time_source",
    "time_confidence",
    "timezone_offset",
    "camera",
)


def persist_taken_time_row(
    connection: sqlite3.Connection,
    *,
    job: Any,
    result: Any,
    prepared: Any,
    now_ms: int,
) -> bool:
    """Write the current taken-time row inside the caller's transaction.

    ADR-0011 §6.6: the row write and the ``CAPTURE_TIME_CHANGED`` increment of the one
    shared ``catalog_filter_revision`` happen in this one transaction, at most once. An
    identical row is a no-op replay: nothing is rewritten and the revision does not move,
    which is what keeps a restarted or forced extraction idempotent.
    """
    facts = [
        item
        for item in prepared
        if str(item.ref.artifact_type) == "structured"
        and str(item.ref.artifact_id).startswith("taken-time:")
    ]
    if not facts:
        return False
    item = facts[0]
    try:
        payload = json.loads(item.payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CatalogError(
            ARTIFACT_INVALID, "the taken-time fact payload is not valid JSON"
        ) from error
    validate_payload(payload)
    asset_id = int(job.subject_id)
    row = connection.execute(
        "SELECT sha256 FROM assets WHERE asset_id = ?", (asset_id,)
    ).fetchone()
    if row is None:
        raise CatalogError(
            ARTIFACT_INVALID,
            "the taken-time fact subject asset does not exist",
            details={"asset_ids": [asset_id]},
        )
    content, observation = split_input_revision(str(result.input_revision))
    if str(row["sha256"]) != content:
        raise CatalogError(
            STALE_INPUT_REVISION,
            "the asset content changed before the taken-time fact could be committed",
            details={"asset_ids": [asset_id]},
        )
    locations = connection.execute(
        "SELECT location_id, availability, size, mtime_ns FROM file_locations "
        "WHERE asset_id = ? ORDER BY location_id",
        (asset_id,),
    ).fetchall()
    if not observation_matches(locations, observation):
        raise CatalogError(
            STALE_INPUT_REVISION,
            "the registered observations changed before the taken-time fact could be committed",
            details={"asset_ids": [asset_id]},
        )
    values = tuple(
        None if payload[column] is None else payload[column] for column in _COLUMNS
    )
    current = connection.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM asset_metadata WHERE asset_id = ?", (asset_id,)
    ).fetchone()
    if current is not None and tuple(current) == values:
        return True
    connection.execute(
        "INSERT INTO asset_metadata(asset_id, taken_at_raw, taken_at_sort, time_source, "
        "time_confidence, timezone_offset, camera) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(asset_id) DO UPDATE SET taken_at_raw = excluded.taken_at_raw, "
        "taken_at_sort = excluded.taken_at_sort, time_source = excluded.time_source, "
        "time_confidence = excluded.time_confidence, "
        "timezone_offset = excluded.timezone_offset, camera = excluded.camera",
        (asset_id, *values),
    )
    revision_module.bump_filter_revision(
        connection,
        cause=CAUSE_CAPTURE_TIME_CHANGED,
        now_ms=int(now_ms),
        asset_ids=[asset_id],
    )
    return True


__all__ = [
    "CAUSE_CAPTURE_TIME_CHANGED",
    "PAYLOAD_KEYS",
    "OBSERVATION_CHARS",
    "PYTHON_RUNTIME_PACKAGE_ID",
    "TAKEN_TIME_ARTIFACT_REVISION",
    "TAKEN_TIME_PROCESSOR_ID",
    "TAKEN_TIME_SPEC_REVISION",
    "TAKEN_TIME_TASK_TYPE",
    "artifact_id_for",
    "artifact_ref",
    "build_payload",
    "extract_for_asset",
    "input_revision_for",
    "observation_digest",
    "observation_matches",
    "persist_taken_time_row",
    "split_input_revision",
    "system_now_ms",
    "taken_time_executor",
    "taken_time_processor_spec",
    "validate_payload",
]
