"""P4-02O: the production build entry of the semantic (image-text) index.

Before this module the semantic index had a *maintenance* half only: ``drain`` confirmed
already-published embedding events and ``reexecute`` re-ran reclaimed artifacts, but nothing in
``src/`` ever turned a scanned photo into an embedding. This module is that missing half, and
it follows the shape the face/OCR/taken-time processors already use: select the assets that
need the work, enqueue one idempotent job per asset, run the scheduler with an executor that
produces the artifact, then drain the outbox so the derived rows become ``CONFIRMED``.

Four rules decide what is written here:

* **The space is the manifest's.** :func:`photo_search.workers.query_encoder.pinned_embedding_spec`
  - the very helper the API's query encoder uses - derives ``processor_id``/``space_id``/
  ``dimension`` from the pinned manifest, so an index can never be built into a space the query
  side does not encode into.
* **The device is resolved, never guessed.** The caller loads one pinned adapter; the facts it
  reports (device, provider, precision, runtime package) are recorded as the capability
  snapshot and qualification evidence, which is exactly what the search-profile activation gate
  re-reads later.
* **Incremental by content revision.** A target is selected only when the asset has no
  ``CONFIRMED`` entry whose artifact came from the asset's current ``sha256``; the job key is
  the five-field logical identity, so re-running the command is idempotent and a changed file is
  picked up as a new revision.
* **Nothing here invents a second pipeline.** Jobs, artifacts, outbox rows, generations and the
  confirmation step are the P2-03/P2-04 production path; a build is "enqueue + execute + drain".
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from PIL import Image

from photo_search.artifacts.store import generation_id_for
from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import (
    PROCESSOR_MISMATCH,
    RUNTIME_RESOLUTION_MISMATCH,
    SPACE_MISMATCH,
    CatalogError,
)
from photo_search.indexing.maintenance import drain_semantic_outbox
from photo_search.jobs import queue as jobs
from photo_search.media import decode as media_decode
from photo_search.media import heif as media_heif
from photo_search.media import raw_cr2
from photo_search.runtime.device import CPU_PROVIDER, CUDA_PROVIDER
from photo_search.scheduler import engine as scheduler_engine
from photo_search.scheduler.contracts import RuntimeCapabilities, canonical_json
from photo_search.workers.query_encoder import pinned_embedding_spec

#: The frozen task/artifact spellings of the image-text embedding processor. They match what
#: the P2/P4 capacity tooling published, so a library built by either path shares one logical
#: key per (processor, input revision, kind, revision).
SEMANTIC_TASK_TYPE = "image_embedding"
ARTIFACT_ID_PREFIX = "image-embedding:"
ARTIFACT_REVISION = "embedding.v1"
MEDIA_TYPE = "application/json"
#: The profile this module publishes names the FTS expression builder it was built for; the
#: value is descriptive metadata (nothing validates it), unlike the two FTS constants below,
#: which the activation gate *does* compare against the supported ones.
PROFILE_ID_PREFIX = "semantic-"
PROFILE_QUERY_PARSER_VERSION = "match-expression-v1"
#: The default number of assets one build command enqueues when no bound is given.
DEFAULT_BUILD_LIMIT = 5000

#: The location a build reads from. Only a ``PRESENT`` location is usable: encoding a
#: ``MISSING`` one would publish a vector for a file the catalog no longer has.
_PRESENT_SOURCE_SQL = (
    "SELECT l.location_id, l.source_id, l.relative_path, s.root_path "
    "FROM file_locations AS l JOIN source_roots AS s ON s.source_id = l.source_id "
    "WHERE l.asset_id = ? AND l.availability = 'PRESENT' ORDER BY l.location_id LIMIT 1"
)

#: ``source_predicate`` is either empty or ``f.source_id = ? AND``. It must compose with the
#: ``NOT EXISTS`` clause - adding a second ``WHERE`` for the source filter made every
#: ``build --source-id`` call a SQL syntax error (DS-H review F1).
#: ``cursor_predicate`` is either empty or ``a.asset_id > ? AND``; it is what lets the
#: P4-02Y-I build walk a whole source in bounded pages instead of materialising the pending
#: set at once. Both predicates come *before* the ``NOT EXISTS`` so their parameters stay in
#: the same order as the placeholders.
#: Assets that have a readable original and no confirmed embedding for their *current*
#: content revision. A confirmed entry whose artifact came from a different revision (the file
#: changed) is deliberately not a hit: that asset needs a new job.
_TARGET_SQL = (
    "SELECT DISTINCT a.asset_id AS asset_id, a.sha256 AS sha256, "
    "f.relative_path AS relative_path, r.root_path AS root_path "
    "FROM assets AS a "
    "JOIN file_locations AS f ON f.location_id = ("
    "SELECT l.location_id FROM file_locations AS l WHERE l.asset_id = a.asset_id "
    "AND l.availability = 'PRESENT' ORDER BY l.location_id LIMIT 1) "
    "JOIN source_roots AS r ON r.source_id = f.source_id "
    "WHERE {source_predicate}{cursor_predicate}NOT EXISTS ("
    "SELECT 1 FROM index_entries AS e JOIN artifacts AS ar ON ar.artifact_id = e.artifact_id "
    "WHERE e.asset_id = a.asset_id AND e.space_id = ? AND e.generation_id = ? "
    "AND e.state = 'CONFIRMED' AND ar.input_revision = a.sha256) "
    "ORDER BY a.asset_id"
)


def artifact_id_for(processor_id: str, input_revision: str) -> str:
    """The deterministic artifact id of one asset's embedding (same shape as face/OCR)."""
    return f"{ARTIFACT_ID_PREFIX}{processor_id}:{str(input_revision)}"


def embedding_artifact(
    artifact_id: str,
    vector: Sequence[float],
    *,
    space_id: str,
    dimension: int,
) -> dict[str, Any]:
    """One embedding artifact in the frozen ``ArtifactRef`` shape (payload = the vector)."""
    payload = {"vector": [float(value) for value in vector]}
    data = canonical_json(payload).encode("utf-8")
    return {
        "artifact_id": str(artifact_id),
        "artifact_type": "embedding",
        "artifact_revision": ARTIFACT_REVISION,
        "media_type": MEDIA_TYPE,
        "checksum_sha256": hashlib.sha256(data).hexdigest(),
        "byte_size": len(data),
        "storage_uri": None,
        "payload": payload,
        "space_id": str(space_id),
        "dimension": int(dimension),
    }


def image_eligibility(path: str | Path) -> tuple[bool, str]:
    """Whether the media layer would decode this file as a supported image, and its label.

    The decision is the frozen media policy (:mod:`photo_search.media.decode`): a format
    Pillow identifies is accepted only when it is in the *supported* set, and a file Pillow
    cannot identify is classified by the same magic-byte sniff the decode path uses. Video,
    audio, documents and archives therefore come back as ``ISO-BMFF``/``UNIDENTIFIED`` and are
    out of scope for an image-text index - not failures.
    """
    if raw_cr2.is_cr2(path):
        return raw_cr2.runtime_available(), "CR2"
    media_heif.ensure_heif_support()
    try:
        with Image.open(Path(path)) as source:
            label, decodable = media_decode.plugin_format_decision(source.format)
        return bool(decodable), str(label)
    except Exception:  # noqa: BLE001 - one unreadable file must not stop the selection
        try:
            sniffed = media_decode.sniff_format(media_decode.read_header(Path(path)))
        except Exception:  # noqa: BLE001 - an unreadable header is reported, never raised
            return False, "UNREADABLE"
        if sniffed is None:
            return False, "UNIDENTIFIED"
        return str(sniffed) in media_decode.supported_input_formats(), str(sniffed)


def semantic_selection(
    catalog: Catalog,
    *,
    space_id: str,
    processor_id: str,
    source_id: int | None = None,
    after_asset_id: int | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Split the pending assets into embeddable targets and out-of-scope files.

    ``limit`` bounds how many *targets* are selected; the out-of-scope histogram is complete
    only when the whole pending set was examined, which the returned ``coverage_complete``
    says explicitly. ``after_asset_id`` starts the walk after one already-processed asset, so
    a caller that has to cover a 20-30 万 first build can page through the whole scope with
    bounded memory instead of asking for every pending row at once (P4-02Y-I §2).
    """
    source_predicate = ""
    parameters: tuple[Any, ...] = ()
    if source_id is not None:
        source_predicate = "f.source_id = ? AND "
        parameters = (int(source_id),)
    cursor_predicate = ""
    if after_asset_id is not None:
        cursor_predicate = "a.asset_id > ? AND "
        parameters = (*parameters, int(after_asset_id))
    generation_id = generation_id_for(str(space_id), str(processor_id))
    rows = catalog.read_all(
        _TARGET_SQL.format(
            source_predicate=source_predicate, cursor_predicate=cursor_predicate
        ),
        (*parameters, str(space_id), generation_id),
    )
    wanted = None if limit is None else max(0, int(limit))
    targets: list[dict[str, Any]] = []
    labels: dict[str, int] = {}
    sample: list[dict[str, Any]] = []
    out_of_scope = 0
    checked = 0
    for row in rows:
        checked += 1
        path = Path(str(row["root_path"])) / str(row["relative_path"])
        eligible, label = image_eligibility(path)
        if eligible:
            if wanted is None or len(targets) < wanted:
                targets.append(
                    {"asset_id": int(row["asset_id"]), "input_revision": str(row["sha256"])}
                )
            if wanted is not None and len(targets) >= wanted:
                break
            continue
        out_of_scope += 1
        labels[label] = labels.get(label, 0) + 1
        if len(sample) < 20:
            sample.append({"asset_id": int(row["asset_id"]), "label": label})
    return {
        "checked": checked,
        "coverage_complete": checked == len(rows),
        "targets": targets,
        "out_of_scope": {
            "count": out_of_scope,
            "labels": dict(sorted(labels.items())),
            "sample": sample,
        },
    }


def semantic_targets(
    catalog: Catalog,
    *,
    space_id: str,
    processor_id: str,
    source_id: int | None = None,
    after_asset_id: int | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """The embeddable assets that still need an embedding for this generation."""
    return semantic_selection(
        catalog,
        space_id=str(space_id),
        processor_id=str(processor_id),
        source_id=source_id,
        after_asset_id=after_asset_id,
        limit=limit,
    )["targets"]


def job_device(requested: str) -> str:
    """The *job-protocol* spelling (``auto``/``cpu``/``gpu``) of one device request.

    The CLI and the adapter speak ``cuda``/``cpu``/``auto`` (ADR-0001) while a job row and the
    scheduler speak ``auto``/``cpu``/``gpu`` (p2-contracts.v1). Translating in one place keeps
    the two vocabularies from leaking into each other.
    """
    value = str(requested).strip().lower()
    if value in ("", "auto", "none"):
        return "auto"
    if value in ("cpu", "host"):
        return "cpu"
    if value in ("cuda", "gpu"):
        return "gpu"
    raise CatalogError(
        "USAGE",
        "the requested device must be auto, cpu, cuda or gpu",
        details={"requested_device": str(requested)},
    )


def enqueue_targets(
    catalog: Catalog,
    targets: Sequence[Mapping[str, Any]],
    *,
    processor_id: str,
    now_ms: int,
    device: str = "auto",
    retry_failed: bool = False,
) -> dict[str, int]:
    """Queue one embedding job per target, idempotently (the logical key is the identity).

    ``retry_failed`` reuses the same logical job instead of creating a second row: a finished
    job is rewound to QUEUED with a fresh attempt budget, exactly like the face processor's
    forced re-detection. A live (RUNNING) job is never touched.
    """
    requested = job_device(device)
    queued = rewound = live = 0
    with catalog.write() as connection:
        for target in targets:
            asset_id = int(target["asset_id"])
            revision = str(target["input_revision"])
            if retry_failed and _rewind(
                connection,
                asset_id=asset_id,
                revision=revision,
                processor_id=str(processor_id),
                now_ms=int(now_ms),
            ):
                rewound += 1
                continue
            _enqueue_one(
                connection,
                asset_id=asset_id,
                input_revision=revision,
                processor_id=str(processor_id),
                now_ms=int(now_ms),
                device=requested,
            )
            queued += 1
    return {"targets": len(targets), "queued": queued, "rewound": rewound, "live": live}


#: States a retry may rewind: everything except a live execution.
REWINDABLE_STATES = ("SUCCEEDED", "FAILED", "CANCELLED", "BLOCKED", "RETRY_WAIT")


def _rewind(
    connection: sqlite3.Connection,
    *,
    asset_id: int,
    revision: str,
    processor_id: str,
    now_ms: int,
) -> bool:
    """Reuse the same logical job for a retry (never a second row)."""
    placeholders = ", ".join("?" for _ in REWINDABLE_STATES)
    cursor = connection.execute(
        "UPDATE jobs SET state = ?, attempts = 0, progress = 0.0, lease_token = NULL, "
        "lease_expires_at = NULL, worker_id = NULL, run_after = ?, last_error_code = NULL, "
        "last_error_message = NULL, blocking_reason = NULL, resolved_device = NULL, "
        "runtime_provider = NULL, precision = NULL, runtime_capability_revision = NULL, "
        "dispatch_spec_revision = NULL, updated_at = ? "
        "WHERE subject_type = 'asset' AND subject_id = ? AND input_revision = ? "
        "AND task_type = ? AND processor_id = ? "
        f"AND state IN ({placeholders})",
        (
            jobs.STATE_QUEUED,
            int(now_ms),
            int(now_ms),
            str(int(asset_id)),
            str(revision),
            SEMANTIC_TASK_TYPE,
            str(processor_id),
            *REWINDABLE_STATES,
        ),
    )
    return bool(cursor.rowcount)


def _enqueue_one(
    connection: sqlite3.Connection,
    *,
    asset_id: int,
    input_revision: str,
    processor_id: str,
    now_ms: int,
    device: str,
) -> None:
    jobs.enqueue(
        connection,
        subject_type="asset",
        subject_id=str(int(asset_id)),
        input_revision=str(input_revision),
        task_type=SEMANTIC_TASK_TYPE,
        processor_id=str(processor_id),
        now_ms=int(now_ms),
        requested_device=str(device),
    )


def adapter_runtime_facts(adapter: Any) -> dict[str, Any]:
    """The live facts one loaded pinned adapter reports (device, provider, package)."""
    health = adapter.health()
    runtime = health.get("runtime") or {}
    device = health.get("device") or {}
    resolved = str(device.get("device") or "cpu")
    resolved_provider = str(device.get("provider") or "")
    package = str(runtime.get("runtime_package") or "onnxruntime-gpu")
    version = str(runtime.get("runtime_version") or "")
    precision = str(device.get("precision") or "FP32")
    devices = ["cpu"]
    if bool(runtime.get("cuda_available")):
        devices.insert(0, "gpu")
    return {
        "runtime_package_id": f"{package}@{version}" if version else package,
        "runtime_backend": str(runtime.get("runtime_backend") or "onnxruntime"),
        "supported_devices": tuple(devices),
        "supported_precisions": (precision,),
        "resolved_device": "gpu" if resolved == "cuda" else resolved,
        "resolved_provider": resolved_provider,
        # The provider *names* of the two device entries of the capability snapshot. An ONNX
        # build reports its execution providers; the PyTorch provider reports its own honest
        # backend name, so the P2-02 qualification gate compares like with like and a PyTorch
        # CUDA run is never recorded as if it were an ONNX CUDA session.
        "gpu_provider_name": (
            resolved_provider if resolved == "cuda" and resolved_provider else CUDA_PROVIDER
        ),
        "cpu_provider_name": (
            resolved_provider if resolved == "cpu" and resolved_provider else CPU_PROVIDER
        ),
        "resolved_precision": precision,
        "providers": [str(name) for name in (runtime.get("providers") or ())],
        "cuda_available": bool(runtime.get("cuda_available")),
    }


def record_runtime_evidence(
    scheduler: scheduler_engine.Scheduler,
    spec: Any,
    facts: Mapping[str, Any],
    *,
    now_ms: int,
) -> dict[str, Any]:
    """Record the capability snapshot and the qualification of the device really used."""
    resolved = str(facts["resolved_device"])
    provider = str(facts["resolved_provider"])
    precision = str(facts["resolved_precision"])
    entries: list[dict[str, Any]] = []
    if bool(facts["cuda_available"]):
        entries.append(
            {
                "name": str(facts.get("gpu_provider_name") or CUDA_PROVIDER),
                "device": "gpu",
                "precisions": [precision],
                "available": resolved == "gpu",
                "health": "healthy" if resolved == "gpu" else "unknown",
                "health_checked_at": int(now_ms),
                "evidence_id": None,
            }
        )
    entries.append(
        {
            "name": str(facts.get("cpu_provider_name") or CPU_PROVIDER),
            "device": "cpu",
            "precisions": [precision],
            "available": resolved == "cpu",
            "health": "healthy" if resolved == "cpu" else "unknown",
            "health_checked_at": int(now_ms),
            "evidence_id": None,
        }
    )
    package = str(spec.runtime_package_id)
    package_name, _, version = package.partition("@")
    snapshot = RuntimeCapabilities.seal(
        {
            "runtime_package_id": package,
            "runtime_backend": str(spec.runtime_backend),
            "runtime_package_name": package_name,
            "runtime_package_version": version,
            "python_version": "3.12.10",
            "platform": "windows-x86_64",
            "driver_version": None,
            "providers": entries,
            "health_status": "healthy" if resolved in ("cpu", "gpu") else "unhealthy",
            "health_checked_at": int(now_ms),
            "measured_at": int(now_ms),
            "evidence_ids": [],
            "failure_code": None,
        }
    )
    scheduler.record_capability(snapshot, now_ms=int(now_ms))
    evidence = scheduler.record_qualification(
        processor_id=str(spec.processor_id),
        spec_revision=str(spec.spec_revision),
        device=resolved,
        precision=precision,
        provider_name=provider,
        runtime_package_id=package,
        now_ms=int(now_ms),
        note="pinned Chinese-CLIP ONNX sessions created for this device",
    )
    return {
        "capability_revision": str(snapshot.capability_revision),
        "runtime_package_id": package,
        "resolved_device": resolved,
        "resolved_provider": provider,
        "resolved_precision": precision,
        "qualification_evidence_id": str(getattr(evidence, "evidence_id", "")),
    }


def semantic_spec(
    manifest_path: str | Path, facts: Mapping[str, Any], *, device_policy: str = "cpu_allowed"
) -> Any:
    """The frozen embedding ``ProcessorSpec`` of the pinned manifest, with live facts."""
    return pinned_embedding_spec(
        manifest_path,
        device_policy=device_policy,
        runtime_package_id=facts.get("runtime_package_id"),
        runtime_backend=facts.get("runtime_backend"),
        supported_devices=facts.get("supported_devices"),
        supported_precisions=facts.get("supported_precisions"),
    )


def semantic_executor(
    catalog: Catalog,
    *,
    encoder: Any,
    space_id: str,
    dimension: int,
    processor_id: str,
    wall_clock: Callable[[], int] | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """The scheduler executor for ``image_embedding`` jobs over one catalog."""

    def run(request: Mapping[str, Any]) -> dict[str, Any]:
        moment = int((wall_clock or catalog.now_ms)())
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
            "completed_at": moment,
        }
        asset_id = int(request["subject_id"])
        try:
            source = catalog.read_one(_PRESENT_SOURCE_SQL, (asset_id,))
            if source is None:
                raise CatalogError(
                    "SOURCE_FILE_MISSING",
                    "the asset has no readable location to embed",
                    details={"asset_id": asset_id},
                )
            path = Path(str(source["root_path"])) / str(source["relative_path"])
            if not path.is_file():
                raise CatalogError(
                    "SOURCE_FILE_MISSING",
                    "the source file of this asset is gone",
                    details={"asset_id": asset_id},
                )
            vector = _validated_image_vector(
                encoder,
                path=path,
                dimension=int(dimension),
                temp_root=catalog.data_root,
            )
            result["artifacts"] = [
                embedding_artifact(
                    artifact_id_for(str(processor_id), str(request["input_revision"])),
                    vector,
                    space_id=str(space_id),
                    dimension=int(dimension),
                )
            ]
        except CatalogError as error:
            result.update(
                state="FAILED",
                progress=0.0,
                error={
                    "code": str(error.code),
                    "message": str(error.message),
                    "retryable": False,
                    "blocking_reason": None,
                },
            )
        except Exception as error:  # noqa: BLE001 - one bad photo must not stop the build
            result.update(
                state="FAILED",
                progress=0.0,
                error={
                    "code": str(RUNTIME_RESOLUTION_MISMATCH),
                    "message": f"the image encoder failed: {type(error).__name__}",
                    "retryable": True,
                    "blocking_reason": None,
                },
            )
        return result

    return run


def _validated_image_vector(
    encoder: Any, *, path: Path, dimension: int, temp_root: str | Path
) -> list[float]:
    """One image vector, validated exactly like a query vector (no padding, no trimming)."""
    with raw_cr2.encoder_input(path, temp_root=temp_root) as encoder_path:
        raw = encoder.encode_image(str(encoder_path))
    if not isinstance(raw, (list, tuple)) or len(raw) != int(dimension):
        raise CatalogError(
            RUNTIME_RESOLUTION_MISMATCH,
            "the encoded image vector does not match the embedding dimension",
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
                "the encoded vector contains a value that is not a number",
            )
        values.append(float(value))
    return values


def build_index(
    catalog: Catalog,
    *,
    encoder: Any,
    manifest_path: str | Path,
    expected_space_id: str | None = None,
    expected_processor_id: str | None = None,
    limit: int | None = DEFAULT_BUILD_LIMIT,
    source_id: int | None = None,
    batch_size: int | None = None,
    device: str = "auto",
    device_policy: str = "cpu_allowed",
    worker_id: str = "semantic-build",
    retry_failed: bool = False,
) -> dict[str, Any]:
    """Build (or extend) the semantic index of one catalog with the pinned model.

    One call does one bounded piece of work and returns: the live runtime facts and the
    evidence it recorded, the targets it selected, the jobs it enqueued, the scheduler steps it
    ran, the outbox confirmation it drained, and the resulting generation/entry counts.
    """
    facts = adapter_runtime_facts(encoder)
    spec = semantic_spec(manifest_path, facts, device_policy=device_policy)
    space_id = str(spec.envelope["space_id"])
    dimension = int(spec.envelope["dimension"])
    # P4-Embedding-Arch: the caller that follows an active profile names the space it intends to
    # extend. A worker that loaded a different (even same-dimensional) model is refused here, so a
    # build can never write vectors into a space nobody serves.
    if expected_space_id is not None and str(expected_space_id) != space_id:
        raise CatalogError(
            SPACE_MISMATCH,
            "the loaded encoder produces a different space than the one being built",
            details={
                "expected_space_id": str(expected_space_id),
                "derived_space_id": space_id,
                "processor_id": str(spec.processor_id),
            },
        )
    if expected_processor_id is not None and str(expected_processor_id) != str(spec.processor_id):
        raise CatalogError(
            PROCESSOR_MISMATCH,
            "the loaded encoder is a different processor than the one being built",
            details={
                "expected_processor_id": str(expected_processor_id),
                "derived_processor_id": str(spec.processor_id),
            },
        )
    scheduler = scheduler_engine.Scheduler(catalog, [spec], lease_seconds=catalog.lease_seconds)
    evidence = record_runtime_evidence(scheduler, spec, facts, now_ms=catalog.now_ms())
    selection = semantic_selection(
        catalog,
        space_id=space_id,
        processor_id=str(spec.processor_id),
        source_id=source_id,
        limit=limit,
    )
    targets = list(selection["targets"])
    enqueued = enqueue_targets(
        catalog,
        targets,
        processor_id=str(spec.processor_id),
        now_ms=catalog.now_ms(),
        device=str(device),
        retry_failed=bool(retry_failed),
    )
    executor = semantic_executor(
        catalog,
        encoder=encoder,
        space_id=space_id,
        dimension=dimension,
        processor_id=str(spec.processor_id),
    )
    steps = 0
    while scheduler.run_once(executor, worker_id=str(worker_id), now_ms=catalog.now_ms()):
        steps += 1
        if len(targets) and steps >= len(targets):
            break
    drained = drain_semantic_outbox(
        catalog,
        limit=max(0, len(targets)) + 16,
        batch_size=batch_size,
    )
    return {
        "status": "ok",
        "model": {
            "processor_id": str(spec.processor_id),
            "space_id": space_id,
            "dimension": dimension,
            "runtime_package_id": str(spec.runtime_package_id),
            "supported_devices": list(spec.envelope["supported_devices"]),
        },
        "evidence": evidence,
        "checked": int(selection["checked"]),
        "coverage_complete": bool(selection["coverage_complete"]),
        "targets": len(targets),
        "out_of_scope": dict(selection["out_of_scope"]),
        "enqueue": enqueued,
        "run": {"steps": steps, "jobs": job_counts(catalog, processor_id=str(spec.processor_id))},
        "drain": {
            "applied": int(drained.get("applied", 0)),
            "confirmed_delta": int(drained.get("confirmed_delta", 0)),
            "batches": int(drained.get("batches", 0)),
            "duration_ms": float(drained.get("duration_ms", 0.0)),
            "write_rejections": int((drained.get("write_rejections") or {}).get("count", 0)),
            "awaiting_reexecution": int(
                (drained.get("awaiting_reexecution") or {}).get("count", 0)
            ),
        },
        "generation": generation_summary(catalog, space_id=space_id, processor_id=str(spec.processor_id)),
    }


def generation_published(catalog: Catalog, *, space_id: str, processor_id: str) -> bool:
    """Whether this space/processor pair has ever had an embedding published.

    The ``index_generations`` row is written when the *first* embedding artifact of the space
    is published (:meth:`photo_search.artifacts.store.ArtifactStore.publish`), so "no row"
    means "nothing has ever been confirmed into this space". Both the search-profile
    publication below and the refresh pipeline need that fact: a build that confirmed nothing
    has no index to serve yet - a reportable outcome, not a failure (P4-02Y-I review B1).
    """
    generation_id = generation_id_for(str(space_id), str(processor_id))
    return (
        catalog.read_one(
            "SELECT 1 FROM index_generations WHERE generation_id = ?", (generation_id,)
        )
        is not None
    )


def ensure_search_profile(
    catalog: Catalog,
    spec: Any,
    *,
    space_id: str,
    generation_id: str,
    profile_id: str | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Create (once) and activate the search profile of one built generation.

    No product entry creates a search profile yet, so this writes the row the ``0007`` DDL
    defines - the schema stays the authority - and then publishes it through the product
    activation gate (capability snapshot + qualification evidence + generation state), which
    is the check a servable profile must pass. Search profiles are immutable: a rebuild
    publishes a new ``profile_id`` instead of editing the old row.

    A profile may only name a generation that *exists*: ``search_profiles.image_generation_id``
    is a foreign key into ``index_generations``. A run that confirmed no image at all - a scope
    with no indexable file, or one whose images the encoder refused - therefore has nothing to
    publish; that is returned as ``profile_id = None`` + ``reason`` instead of letting the
    foreign key fail, and the caller decides what the run's terminal state is (P4-02Y-I
    review B1).

    P4-Embedding-Arch changes exactly one thing: **a build for another space never moves the
    pointer**. The daily build of a new library (no active profile) and every incremental build
    of the space that is already served keep activating, which is the pre-existing behaviour;
    a *candidate* - a different provider or a different revision of one - only publishes its
    immutable profile row and waits for the controlled stopped-service selection.
    """
    from photo_search.search.profile import (
        DEFAULT_FUSION_CONFIG,
        SUPPORTED_FTS_GENERATION_ID,
        SUPPORTED_FTS_TOKENIZER_VERSION,
        activate_profile,
        active_profile_id,
        load_profile,
    )

    moment = int(catalog.now_ms() if now_ms is None else now_ms)
    if not generation_published(
        catalog, space_id=str(space_id), processor_id=str(spec.processor_id)
    ):
        return {
            "profile_id": None,
            "created": False,
            "activated": False,
            "readiness": None,
            "reason": "GENERATION_NOT_PUBLISHED",
            "active_profile_id": active_profile_id(catalog.connection),
        }
    identifier = str(profile_id or f"{PROFILE_ID_PREFIX}{generation_id}")
    created = False
    if load_profile(catalog, identifier) is None:
        with catalog.write() as connection:
            connection.execute(
                "INSERT INTO search_profiles(profile_id, embedding_processor_id, space_id, "
                "image_generation_id, query_preprocess_version, query_parser_version, "
                "fts_tokenizer_version, fts_generation_id, reranker_processor_id, "
                "fusion_config_json, runtime_package_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    identifier,
                    str(spec.processor_id),
                    str(space_id),
                    str(generation_id),
                    str(spec.envelope.get("query_preprocess_version") or "none-v1"),
                    PROFILE_QUERY_PARSER_VERSION,
                    SUPPORTED_FTS_TOKENIZER_VERSION,
                    SUPPORTED_FTS_GENERATION_ID,
                    None,
                    json.dumps(DEFAULT_FUSION_CONFIG, sort_keys=True),
                    str(spec.runtime_package_id),
                    moment,
                ),
            )
        created = True
    activated_id = active_profile_id(catalog.connection)
    if activated_id is not None:
        activated_profile = load_profile(catalog, activated_id)
        served_space = None if activated_profile is None else str(activated_profile.space_id)
        if served_space != str(space_id):
            # A different space is being built. Its profile exists as an immutable candidate and
            # nothing else changes: the served model keeps serving until the operator selects the
            # candidate through the controlled stopped-service entry.
            return {
                "profile_id": identifier,
                "created": created,
                "activated": False,
                "readiness": None,
                "reason": "CANDIDATE_NOT_ACTIVATED",
                "active_profile_id": activated_id,
                "active_space_id": served_space,
                "candidate_space_id": str(space_id),
            }
    profile, changed, readiness = activate_profile(catalog, profile_id=identifier, now_ms=moment)
    return {
        "profile_id": str(profile.profile_id),
        "created": created,
        "activated": bool(changed),
        "readiness": readiness,
        "active_profile_id": active_profile_id(catalog.connection),
    }


def job_counts(catalog: Catalog, *, processor_id: str) -> dict[str, int]:
    """Per-state job counts of one processor (the build's own work queue)."""
    return {
        str(row["state"]): int(row["count"])
        for row in catalog.read_all(
            "SELECT state, COUNT(*) AS count FROM jobs WHERE processor_id = ? GROUP BY state "
            "ORDER BY state",
            (str(processor_id),),
        )
    }


def generation_summary(catalog: Catalog, *, space_id: str, processor_id: str) -> dict[str, Any]:
    """The state of the generation this space/processor pair publishes into."""
    generation_id = generation_id_for(str(space_id), str(processor_id))
    row = catalog.read_one(
        "SELECT state, dimension, is_active FROM index_generations WHERE generation_id = ?",
        (generation_id,),
    )
    entries = catalog.read_all(
        "SELECT state, COUNT(*) AS count FROM index_entries WHERE generation_id = ? GROUP BY state",
        (generation_id,),
    )
    return {
        "generation_id": generation_id,
        "state": None if row is None else str(row["state"]),
        "dimension": None if row is None else int(row["dimension"]),
        "is_active": False if row is None else bool(row["is_active"]),
        "entries": {str(item["state"]): int(item["count"]) for item in entries},
    }


__all__ = [
    "ARTIFACT_ID_PREFIX",
    "ARTIFACT_REVISION",
    "DEFAULT_BUILD_LIMIT",
    "SEMANTIC_TASK_TYPE",
    "adapter_runtime_facts",
    "artifact_id_for",
    "build_index",
    "embedding_artifact",
    "enqueue_targets",
    "ensure_search_profile",
    "generation_published",
    "generation_summary",
    "image_eligibility",
    "job_counts",
    "record_runtime_evidence",
    "semantic_executor",
    "semantic_selection",
    "semantic_spec",
    "semantic_targets",
]
