"""The versioned non-model CPU nearby-city resolver and its persistence hook.

ADR-0005 §4: a resolution is an immutable *machine product*. It declares no model, no
weights, no dimension and no vector space; it only turns the current ``VALID`` GeoFact into
``SUGGESTION_ONLY`` city candidates, or into the successful empty result ``NO_CANDIDATE``.
It never writes ``asset_place_assignments``. The publication itself happens inside the
scheduler's single transaction, after the lease/input-revision qualification, so a late
result can be stored without ever touching a human lock.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from collections.abc import Callable
from typing import Any, Mapping

from photo_search.catalog.errors import (
    ARTIFACT_CONFLICT,
    ARTIFACT_INVALID,
    GEO_FACT_MISSING,
    INPUT_UNSTABLE,
    PLACE_DATASET_INVALID,
    PLACE_DATASET_MISSING,
    PLACE_NOT_FOUND,
    STALE_INPUT_REVISION,
    CatalogError,
)
from photo_search.geo import dataset as dataset_module
from photo_search.places import datasets as dataset_registry
from photo_search.places import revision as revision_module
from photo_search.places import suggestions as suggestion_module
from photo_search.places.contracts import CONTRACT_VERSION
from photo_search.scheduler.contracts import ProcessorSpec, canonical_json

RESOLVER_PROCESSOR_ID = "geo-nearby-city@1"
RESOLVER_TASK_TYPE = "place_resolve"
RESOLVER_SPEC_REVISION = "geo-nearby-city-v1"
RESOLVER_ARTIFACT_REVISION = "place-resolution.v1"
RESOLVER_MEDIA_TYPE = "application/json"

PYTHON_RUNTIME_PACKAGE_ID = "photo-search-runtime@1"

#: The v1 resolver configuration. ``max_distance_m`` is what makes a sea position resolve to
#: an honest empty result instead of the nearest point on a far-away coast.
DEFAULT_CONFIG: dict[str, Any] = {
    "algorithm": "nearest-city-point",
    "version": 1,
    "max_candidates": 3,
    "max_distance_m": 200000.0,
}

DATUM_STATUSES = ("WGS84", "ASSUMED_WGS84", "UNSUPPORTED", "ABSENT")

PAYLOAD_KEYS = frozenset(
    {
        "contract_version",
        "processor_id",
        "input_revision",
        "geo_fact_id",
        "datum_status",
        "dataset_id",
        "dataset_version",
        "config_hash",
        "status",
        "candidates",
    }
)

CANDIDATE_KEYS = frozenset(
    {
        "candidate_id",
        "dataset_place_id",
        "kind",
        "display_name",
        "ancestor_names",
        "rank",
        "distance_m",
        "match_basis",
        "confidence_basis",
        "disposition",
    }
)

#: Blocking conditions: they stop this execution only, and the manual place paths stay usable.
BLOCKING_CODES = frozenset({PLACE_DATASET_MISSING, PLACE_DATASET_INVALID, GEO_FACT_MISSING})

_DETERMINISTIC_FAILURES = frozenset({STALE_INPUT_REVISION, INPUT_UNSTABLE, ARTIFACT_INVALID})

#: A verified parse of the active package, keyed by ``dataset_id:content_sha256``. The
#: executor warms it, so a normal publication does no extra file I/O inside the transaction;
#: a cold process (or a recovered job) verifies the package again on first use.
_CONTENT_CACHE: dict[str, dataset_module.DatasetContent] = {}


def clear_content_cache() -> None:
    """Drop the verified-package cache (tests and diagnostics model a fresh process)."""
    _CONTENT_CACHE.clear()


def system_now_ms() -> int:
    return time.time_ns() // 1_000_000


def config_hash_for(config: Mapping[str, Any] | None = None) -> str:
    payload = dict(DEFAULT_CONFIG) if config is None else dict(config)
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def resolver_processor_spec() -> ProcessorSpec:
    """The frozen ``ProcessorSpec`` of the resolver: CPU only, no model, no vector space."""
    return ProcessorSpec.from_envelope(
        {
            "contract_version": "p2-contracts.v1",
            "spec_revision": RESOLVER_SPEC_REVISION,
            "processor_id": RESOLVER_PROCESSOR_ID,
            "processor_kind": "non_model",
            "task_type": RESOLVER_TASK_TYPE,
            "config_hash": config_hash_for(),
            "input_type": "asset_original",
            "output_type": "place_resolution.v1",
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
    return f"place-resolution:{RESOLVER_PROCESSOR_ID}:{str(input_revision)}"


def resolution_id_for(
    *,
    asset_id: int,
    geo_fact_id: str,
    processor_id: str,
    dataset_id: str,
    config_hash: str,
) -> str:
    """Deterministic identity of one logical resolution input (ADR-0005 §7)."""
    value = "\0".join(
        [
            str(int(asset_id)),
            str(geo_fact_id),
            str(processor_id),
            str(dataset_id),
            str(config_hash),
        ]
    )
    return f"res-{int(asset_id)}-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]}"


def load_active_content(
    connection: sqlite3.Connection,
    *,
    data_root: str | Any = None,
    cache: dict[str, dataset_module.DatasetContent] | None = None,
) -> dataset_module.DatasetContent:
    """Verify (or reuse a verified parse of) the active package."""
    active = dataset_registry.active_row(connection)
    if active is None:
        raise CatalogError(
            PLACE_DATASET_MISSING, "no active name dataset is installed", details={"dataset_id": None}
        )
    store = _CONTENT_CACHE if cache is None else cache
    key = f"{active['dataset_id']}:{active['content_sha256']}"
    cached = store.get(key)
    if cached is not None:
        return cached
    from photo_search.catalog import schema as schema_module

    content = dataset_module.load_content(
        dataset_registry.dataset_dir(data_root, str(active["dataset_id"])),
        supported_schema_version=int(schema_module.SUPPORTED_SCHEMA_VERSION),
    )
    store[key] = content
    return content


def evaluate(
    catalog: Any,
    *,
    asset_id: int,
    input_revision: str,
    data_root: str | Any = None,
    config: Mapping[str, Any] | None = None,
    cache: dict[str, dataset_module.DatasetContent] | None = None,
) -> dict[str, Any]:
    """The deterministic resolution payload of one asset (no write of any kind)."""
    effective_config = dict(DEFAULT_CONFIG) if config is None else dict(config)
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
            details={"asset_ids": [int(asset_id)]},
        )
    content = load_active_content(
        catalog.connection, data_root=data_root, cache=cache
    )
    fact = catalog.read_one("SELECT * FROM asset_geo_facts WHERE asset_id = ?", (int(asset_id),))
    if fact is None:
        raise CatalogError(
            GEO_FACT_MISSING,
            "this asset has no current GPS fact to resolve yet",
            details={"asset_ids": [int(asset_id)]},
        )
    if str(fact["input_revision"]) != str(input_revision):
        raise CatalogError(
            STALE_INPUT_REVISION,
            "the stored GPS fact belongs to different content",
            details={"asset_ids": [int(asset_id)]},
        )
    candidates: list[dict[str, Any]] = []
    if str(fact["status"]) == "VALID" and fact["latitude"] is not None:
        candidates = dataset_module.nearby_cities(
            content,
            float(fact["latitude"]),
            float(fact["longitude"]),
            max_candidates=int(effective_config["max_candidates"]),
            max_distance_m=float(effective_config["max_distance_m"]),
        )
    return {
        "contract_version": CONTRACT_VERSION,
        "processor_id": RESOLVER_PROCESSOR_ID,
        "input_revision": str(input_revision),
        "geo_fact_id": str(fact["geo_fact_id"]),
        "datum_status": str(fact["datum_status"]),
        "dataset_id": content.dataset_id,
        "dataset_version": content.package_version,
        "config_hash": config_hash_for(effective_config),
        "status": "CANDIDATES" if candidates else "NO_CANDIDATE",
        "candidates": candidates,
    }


def artifact_ref(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The ``ArtifactRef`` that carries one resolution payload into the transaction."""
    data = canonical_json(dict(payload)).encode("utf-8")
    return {
        "artifact_id": artifact_id_for(str(payload["input_revision"])),
        "artifact_type": "structured",
        "artifact_revision": RESOLVER_ARTIFACT_REVISION,
        "media_type": RESOLVER_MEDIA_TYPE,
        "checksum_sha256": hashlib.sha256(data).hexdigest(),
        "byte_size": len(data),
        "storage_uri": None,
        "payload": dict(payload),
        "space_id": None,
        "dimension": None,
    }


# ------------------------------------------------------------------------ the executor
def resolver_executor(
    catalog: Any,
    *,
    data_root: str | Any = None,
    config: Mapping[str, Any] | None = None,
    cache: dict[str, dataset_module.DatasetContent] | None = None,
    clock: Callable[[], int] | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Build the scheduler executor for ``place_resolve`` jobs over one catalog."""
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
            payload = evaluate(
                catalog,
                asset_id=int(request["subject_id"]),
                input_revision=str(request["input_revision"]),
                data_root=data_root,
                config=config,
                cache=cache,
            )
        except CatalogError as error:
            blocked = str(error.code) in BLOCKING_CODES
            result.update(
                state="BLOCKED" if blocked else "FAILED",
                progress=0.0,
                artifacts=[],
                error={
                    "code": str(error.code),
                    "message": _safe_message(error, blocked=blocked),
                    "retryable": bool(blocked),
                    "blocking_reason": str(error.code) if blocked else None,
                },
            )
            return result
        result["artifacts"] = [artifact_ref(payload)]
        return result

    return run


def _safe_message(error: CatalogError, *, blocked: bool) -> str:
    """A blocking reason that never carries a local path (ADR-0005 §3/§8)."""
    if blocked:
        return "the current name dataset could not resolve this asset"
    return str(error.message)[:200]


# --------------------------------------------------------------------- payload checks
def validate_payload(payload: Mapping[str, Any]) -> None:
    """Enforce the deterministic resolution shape before anything reaches the catalog."""
    if not isinstance(payload, Mapping) or frozenset(payload) != PAYLOAD_KEYS:
        raise CatalogError(
            ARTIFACT_INVALID,
            "the structured place resolution does not match the frozen shape",
            details={"artifact": RESOLVER_ARTIFACT_REVISION},
        )
    if str(payload["contract_version"]) != CONTRACT_VERSION:
        raise CatalogError(ARTIFACT_INVALID, "the resolution carries a foreign contract version")
    if str(payload["processor_id"]) != RESOLVER_PROCESSOR_ID:
        raise CatalogError(ARTIFACT_INVALID, "the resolution was produced by another processor")
    input_revision = str(payload["input_revision"])
    if len(input_revision) != 64 or any(char not in "0123456789abcdef" for char in input_revision):
        raise CatalogError(ARTIFACT_INVALID, "the resolution input revision is not a sha256")
    config_hash = str(payload["config_hash"])
    if len(config_hash) != 64 or any(char not in "0123456789abcdef" for char in config_hash):
        raise CatalogError(ARTIFACT_INVALID, "the resolution config hash is not a sha256")
    for key in ("geo_fact_id", "dataset_id", "dataset_version"):
        if not str(payload[key]):
            raise CatalogError(ARTIFACT_INVALID, f"the resolution {key} must not be empty")
    if str(payload["datum_status"]) not in DATUM_STATUSES:
        raise CatalogError(ARTIFACT_INVALID, "unknown datum status")
    status = str(payload["status"])
    if status not in ("CANDIDATES", "NO_CANDIDATE"):
        raise CatalogError(ARTIFACT_INVALID, "unknown resolution status")
    candidates = payload["candidates"]
    if not isinstance(candidates, list):
        raise CatalogError(ARTIFACT_INVALID, "the resolution candidates must be a list")
    if status == "NO_CANDIDATE" and candidates:
        raise CatalogError(ARTIFACT_INVALID, "a NO_CANDIDATE resolution must be empty")
    if status == "CANDIDATES" and not candidates:
        raise CatalogError(ARTIFACT_INVALID, "a CANDIDATES resolution must not be empty")
    if len(candidates) > 10:
        raise CatalogError(ARTIFACT_INVALID, "a resolution carries at most 10 candidates")
    ranks: set[int] = set()
    ids: set[str] = set()
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, Mapping) or frozenset(candidate) != CANDIDATE_KEYS:
            raise CatalogError(
                ARTIFACT_INVALID,
                "a place candidate does not match the frozen shape",
                details={"index": index},
            )
        if str(candidate["kind"]) != "city":
            raise CatalogError(ARTIFACT_INVALID, "a v1 candidate is always a city")
        if str(candidate["match_basis"]) != "NEARBY_CITY_POINT":
            raise CatalogError(ARTIFACT_INVALID, "unknown candidate match basis")
        if str(candidate["confidence_basis"]) != "DISTANCE_ONLY":
            raise CatalogError(ARTIFACT_INVALID, "unknown candidate confidence basis")
        if str(candidate["disposition"]) != "SUGGESTION_ONLY":
            raise CatalogError(ARTIFACT_INVALID, "a candidate may never be an assignment")
        if not str(candidate["candidate_id"]) or not str(candidate["display_name"]):
            raise CatalogError(ARTIFACT_INVALID, "a candidate needs an id and a display name")
        if not isinstance(candidate["ancestor_names"], list):
            raise CatalogError(ARTIFACT_INVALID, "candidate ancestor_names must be a list")
        rank = candidate["rank"]
        if isinstance(rank, bool) or not isinstance(rank, int) or rank < 1 or rank in ranks:
            raise CatalogError(ARTIFACT_INVALID, "candidate ranks must be unique positive integers")
        ranks.add(int(rank))
        distance = candidate["distance_m"]
        if (
            isinstance(distance, bool)
            or not isinstance(distance, (int, float))
            or not math.isfinite(float(distance))
            or float(distance) < 0.0
        ):
            raise CatalogError(ARTIFACT_INVALID, "candidate distance must be a finite number")
        candidate_id = str(candidate["candidate_id"])
        if candidate_id in ids:
            raise CatalogError(ARTIFACT_INVALID, "candidate ids must be unique")
        ids.add(candidate_id)


# ----------------------------------------------------------------------- persistence
def _local_place_ids(
    connection: sqlite3.Connection, *, dataset_id: str, candidates: list[Mapping[str, Any]]
) -> dict[str, int]:
    """Map dataset place ids to local places through the imported external references."""
    row = connection.execute(
        "SELECT provider FROM geo_datasets WHERE dataset_id = ?", (str(dataset_id),)
    ).fetchone()
    if row is None:
        raise CatalogError(
            PLACE_DATASET_MISSING,
            "the resolution names a dataset that is not registered",
            details={"dataset_id": str(dataset_id)},
        )
    provider = str(row["provider"])
    mapping: dict[str, int] = {}
    for candidate in candidates:
        dataset_place_id = str(candidate["dataset_place_id"])
        reference = connection.execute(
            "SELECT place_id FROM place_external_refs WHERE provider = ? AND external_id = ?",
            (provider, dataset_place_id),
        ).fetchone()
        if reference is not None:
            mapping[dataset_place_id] = int(reference["place_id"])
    return mapping


def _predicted_signature(
    *,
    candidates: list[Mapping[str, Any]],
    local_place_ids: Mapping[str, int],
    excluded: set[int],
    locked: bool,
) -> tuple[Any, ...]:
    """What :func:`photo_search.places.suggestions.visible_signature` will report."""
    if locked:
        return ()
    visible = tuple(
        (int(candidate["rank"]), str(candidate["candidate_id"]))
        for candidate in candidates
        if local_place_ids.get(str(candidate["dataset_place_id"])) not in excluded
    )
    return visible


def persist_resolution(
    connection: sqlite3.Connection,
    *,
    job: Any,
    result: Any,
    prepared: Any,
    now_ms: int,
) -> bool:
    """Write the machine resolution inside the caller's transaction.

    ADR-0005 §4: the row and its candidates are insert-once, the suggestion revision moves
    only when the visible candidate set actually changed, and ``asset_place_assignments`` is
    never touched - a late result cannot re-add a place the user locked out.
    """
    items = [
        item
        for item in prepared
        if str(item.ref.artifact_type) == "structured"
        and str(item.ref.artifact_id).startswith("place-resolution:")
    ]
    if not items:
        return False
    item = items[0]
    try:
        payload = json.loads(item.payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CatalogError(ARTIFACT_INVALID, "the resolution payload is not valid JSON") from error
    validate_payload(payload)
    asset_id = int(job.subject_id)
    row = connection.execute("SELECT sha256 FROM assets WHERE asset_id = ?", (asset_id,)).fetchone()
    if row is None:
        raise CatalogError(
            ARTIFACT_INVALID,
            "the resolution subject asset does not exist",
            details={"asset_ids": [asset_id]},
        )
    if str(row["sha256"]) != str(result.input_revision):
        raise CatalogError(
            STALE_INPUT_REVISION,
            "the asset content changed before the resolution could be committed",
            details={"asset_ids": [asset_id]},
        )
    resolution_id = resolution_id_for(
        asset_id=asset_id,
        geo_fact_id=str(payload["geo_fact_id"]),
        processor_id=str(payload["processor_id"]),
        dataset_id=str(payload["dataset_id"]),
        config_hash=str(payload["config_hash"]),
    )
    existing = connection.execute(
        "SELECT status FROM place_resolutions WHERE resolution_id = ?", (resolution_id,)
    ).fetchone()
    if existing is not None:
        if str(existing["status"]) != str(payload["status"]):
            raise CatalogError(
                ARTIFACT_CONFLICT,
                "the same logical resolution input already produced a different status",
                details={"asset_ids": [asset_id]},
            )
        return False
    candidates = list(payload["candidates"])
    local_place_ids = _local_place_ids(
        connection, dataset_id=str(payload["dataset_id"]), candidates=candidates
    )
    assignment = connection.execute(
        "SELECT revision FROM asset_place_assignments WHERE asset_id = ?", (asset_id,)
    ).fetchone()
    control_revision_seen = 0 if assignment is None else int(assignment["revision"])
    locked = suggestion_module.has_manual_assignment(connection, asset_id)
    excluded = suggestion_module.excluded_place_ids(connection, asset_id)
    before = suggestion_module.visible_signature(connection, asset_id)
    predicted = _predicted_signature(
        candidates=candidates,
        local_place_ids=local_place_ids,
        excluded=excluded,
        locked=locked,
    )
    suggestion_revision = revision_module.read_suggestion_revision(connection)
    changed = predicted != before
    if changed:
        suggestion_revision = revision_module.bump_suggestion_revision(
            connection,
            cause=revision_module.CAUSE_SUGGESTION_CHANGED,
            now_ms=int(now_ms),
        )
    connection.execute(
        "INSERT INTO place_resolutions(resolution_id, asset_id, geo_fact_id, input_revision, "
        "control_revision_seen, processor_id, dataset_id, dataset_version, config_hash, status, "
        "suggestion_revision, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            resolution_id,
            asset_id,
            str(payload["geo_fact_id"]),
            str(payload["input_revision"]),
            int(control_revision_seen),
            str(payload["processor_id"]),
            str(payload["dataset_id"]),
            str(payload["dataset_version"]),
            str(payload["config_hash"]),
            str(payload["status"]),
            int(suggestion_revision),
            int(now_ms),
        ),
    )
    for candidate in candidates:
        connection.execute(
            "INSERT INTO place_resolution_candidates(resolution_id, rank, candidate_id, "
            "dataset_place_id, local_place_id, kind, display_name, ancestor_names_json, "
            "distance_m, match_basis, confidence_basis, disposition, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                resolution_id,
                int(candidate["rank"]),
                str(candidate["candidate_id"]),
                str(candidate["dataset_place_id"]),
                local_place_ids.get(str(candidate["dataset_place_id"])),
                str(candidate["kind"]),
                str(candidate["display_name"]),
                json.dumps(list(candidate["ancestor_names"]), ensure_ascii=False),
                float(candidate["distance_m"]),
                str(candidate["match_basis"]),
                str(candidate["confidence_basis"]),
                str(candidate["disposition"]),
                int(now_ms),
            ),
        )
    actual = suggestion_module.visible_signature(connection, asset_id)
    if actual != predicted:  # pragma: no cover - defensive: the prediction must hold
        raise CatalogError(
            ARTIFACT_INVALID,
            "the visible candidate set did not match the recorded publication",
            details={"asset_ids": [asset_id]},
        )
    return True
