"""P4-02Y-I: durable, per-source progress of "has this source been built yet?".

The source card of the P4-02Y-I card answers three questions without re-reading the library
and without trusting a process-local run:

* how many files does this source have (the last scan's denominator);
* how much of the source's *image* content is already in the served semantic index, and is
  the backlog really empty (only then may the card say "可以语义搜索");
* how are the enrichment processors (taken-time, GPS, place, face, OCR) doing - coarse
  states are enough, but they must not lie about a processor this machine cannot run.

Every number comes from persisted state only: ``file_locations``/``source_roots`` for the
scan denominator, ``jobs`` plus ``index_entries``/``artifacts`` for the semantic backlog and
``jobs`` again for the enrichment stages. A restarted process therefore renders the same
card, and an interrupted build continues from the same durable backlog.

Two deliberate definitions, because they are what makes the numbers exact *and* cheap:

* the semantic denominator is the set of assets of this scope that the semantic layer has
  **classified as images** - evidenced by an ``image_embedding`` job row for this processor
  (every confirmed embedding came from exactly such a job). Documents, videos and archives
  never enter it, so they cannot keep a source at "231 400 / 250 000" for ever;
* an enrichment asset counts as *handled* once a job for its **current** content revision
  reached a terminal state. A processor this machine cannot run never enqueues anything, so
  it is reported as ``UNAVAILABLE`` from its weight/dataset check instead of "in progress".
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: The frozen semantic task type (P2/P4 spelling), the same constant
#: :mod:`photo_search.refresh.pipeline` routes on.
SEMANTIC_TASK_TYPE = "image_embedding"

#: Semantic states of one source. ``UNBUILT`` means the durable evidence says no image of
#: this scope has ever been classified - the card then offers "开始建库" instead of a
#: progress bar, and it never claims the source is searchable.
SEMANTIC_UNAVAILABLE = "UNAVAILABLE"
SEMANTIC_UNBUILT = "UNBUILT"
SEMANTIC_BUILDING = "BUILDING"
SEMANTIC_SEARCHABLE = "SEARCHABLE"

#: Enrichment states of one source (P4-02Y-I §3: "处理中 / 完成 / 不可用").
STAGE_UNAVAILABLE = "UNAVAILABLE"
STAGE_IN_PROGRESS = "IN_PROGRESS"
STAGE_COMPLETE = "COMPLETE"

#: The enrichment processors one source card shows, as
#: ``(name, task_type, processor_id, fact_table)``. The first two read the original file with
#: the standard library (always runnable), the last three need weights or a place dataset and
#: are reported as ``UNAVAILABLE`` when this machine has none.
#:
#: ``fact_table`` names the derived table that proves "this asset was processed". It is only
#: needed for the taken-time extractor: unlike GPS/place/face/OCR - whose job revision *is*
#: the content hash - the capture-time input revision also digests the recorded position
#: facts (ADR-0011 §2), so no job revision can be compared against ``assets.sha256``.
ENRICHMENT_STAGES: tuple[tuple[str, str, str, str | None], ...] = (
    ("taken_time", "taken_time_extract", "taken-time-exif@1", "asset_metadata"),
    ("gps", "geo_extract", "geo-exif-gps@1", None),
    ("place", "place_resolve", "geo-nearby-city@1", None),
    ("face", "image_face", "face-model@1", None),
    ("ocr", "image_ocr", "ocr-text@1", None),
)

#: The stages whose availability depends on installed weights (the place stage depends on a
#: dataset instead and is probed separately).
_NEEDS_WEIGHTS = frozenset({"face", "ocr"})

_LIVE_JOB_STATES = ("QUEUED", "RETRY_WAIT", "RUNNING")
#: A job that reached a state the user has to know about: it ran (SUCCEEDED) or the encoder
#: gave up on that asset (FAILED/CANCELLED/BLOCKED).
_TERMINAL_JOB_STATES = ("SUCCEEDED", "FAILED", "CANCELLED", "BLOCKED")
#: The states that mean "this asset was handed to the encoder and refused": the only ones a
#: *semantic* breakdown may call ``failed``. A SUCCEEDED embedding job is not a refusal - the
#: confirmed entry it produced is what ``confirmed`` counts.
_REFUSED_JOB_STATES = ("FAILED", "CANCELLED", "BLOCKED")


def _placeholders(values: tuple[str, ...]) -> str:
    return ", ".join("?" for _ in values)


def _scope_clause(source_id: int | None) -> tuple[str, tuple[Any, ...]]:
    """The asset set one progress query is expressed against, plus its bound parameters.

    ``source_id=None`` means the whole library; otherwise the assets that still have a
    ``PRESENT``, content-bound position in that source.
    """
    if source_id is None:
        return (
            "SELECT DISTINCT l.asset_id AS asset_id FROM file_locations AS l "
            "WHERE l.availability = 'PRESENT' AND l.asset_id IS NOT NULL",
            (),
        )
    return (
        "SELECT DISTINCT l.asset_id AS asset_id FROM file_locations AS l "
        "WHERE l.source_id = ? AND l.availability = 'PRESENT' AND l.asset_id IS NOT NULL",
        (int(source_id),),
    )


def scoped_asset_count(catalog: Any, *, source_id: int | None) -> int:
    """Bound, currently readable assets of one source (the coarse enrichment denominator)."""
    clause, parameters = _scope_clause(source_id)
    return int(catalog.read_one(f"SELECT COUNT(*) AS n FROM ({clause})", parameters)["n"])


def scan_progress(catalog: Any, *, source_id: int) -> dict[str, Any]:
    """How many positions this source has, and how many of them are readable today."""
    row = catalog.read_one(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN availability = 'PRESENT' THEN 1 ELSE 0 END) AS present, "
        "SUM(CASE WHEN availability = 'MISSING' THEN 1 ELSE 0 END) AS missing "
        "FROM file_locations WHERE source_id = ?",
        (int(source_id),),
    )
    return {
        "files": int(row["total"] or 0),
        "present": int(row["present"] or 0),
        "missing": int(row["missing"] or 0),
    }


def semantic_breakdown(
    catalog: Any,
    *,
    space_id: str,
    processor_id: str,
    source_id: int | None = None,
) -> dict[str, int]:
    """``total``/``confirmed``/``pending``/``failed``/``queued`` of one scope's images.

    ``total`` is the classified image set (see the module docstring); ``confirmed`` is the
    part whose current content revision is confirmed in the served generation; ``failed`` is
    the part the encoder already refused for this revision (a terminal job, so it is not
    "pending" any more); ``pending`` is the honest remainder; ``queued`` is how much of the
    work is sitting in the durable queue right now.
    """
    clause, parameters = _scope_clause(source_id)
    total = int(
        catalog.read_one(
            "SELECT COUNT(*) AS n FROM ("
            "SELECT CAST(j.subject_id AS INTEGER) AS asset_id FROM jobs AS j "
            "WHERE j.subject_type = 'asset' AND j.task_type = ? AND j.processor_id = ? "
            f"AND CAST(j.subject_id AS INTEGER) IN ({clause})"
            " UNION "
            "SELECT e.asset_id FROM index_entries AS e "
            "WHERE e.space_id = ? AND e.state = 'CONFIRMED' "
            f"AND e.asset_id IN ({clause})"
            ")",
            (
                SEMANTIC_TASK_TYPE,
                str(processor_id),
                *parameters,
                str(space_id),
                *parameters,
            ),
        )["n"]
    )
    confirmed = int(
        catalog.read_one(
            "SELECT COUNT(*) AS n FROM index_entries AS e "
            "JOIN artifacts AS ar ON ar.artifact_id = e.artifact_id "
            "JOIN assets AS a ON a.asset_id = e.asset_id "
            "WHERE e.space_id = ? AND e.state = 'CONFIRMED' AND ar.input_revision = a.sha256 "
            f"AND e.asset_id IN ({clause})",
            (str(space_id), *parameters),
        )["n"]
    )
    failed = int(
        catalog.read_one(
            "SELECT COUNT(*) AS n FROM jobs AS j "
            "JOIN assets AS a ON a.asset_id = CAST(j.subject_id AS INTEGER) "
            "WHERE j.subject_type = 'asset' AND j.task_type = ? AND j.processor_id = ? "
            f"AND j.state IN ({_placeholders(_REFUSED_JOB_STATES)}) "
            "AND j.input_revision = a.sha256 "
            f"AND a.asset_id IN ({clause})",
            (SEMANTIC_TASK_TYPE, str(processor_id), *_REFUSED_JOB_STATES, *parameters),
        )["n"]
    )
    queued = int(
        catalog.read_one(
            "SELECT COUNT(*) AS n FROM jobs AS j "
            "JOIN assets AS a ON a.asset_id = CAST(j.subject_id AS INTEGER) "
            "WHERE j.subject_type = 'asset' AND j.task_type = ? AND j.processor_id = ? "
            f"AND j.state IN ({_placeholders(_LIVE_JOB_STATES)}) "
            "AND j.input_revision = a.sha256 "
            f"AND a.asset_id IN ({clause})",
            (SEMANTIC_TASK_TYPE, str(processor_id), *_LIVE_JOB_STATES, *parameters),
        )["n"]
    )
    return {
        "total": total,
        "confirmed": confirmed,
        "failed": failed,
        "queued": queued,
        "pending": max(total - confirmed - failed, 0),
    }


def semantic_state(breakdown: Mapping[str, int]) -> str:
    """The card's semantic state word for one durable breakdown."""
    total = int(breakdown["total"])
    confirmed = int(breakdown["confirmed"])
    pending = int(breakdown["pending"])
    if total == 0 and confirmed == 0:
        return SEMANTIC_UNBUILT
    if pending == 0 and confirmed > 0:
        return SEMANTIC_SEARCHABLE
    return SEMANTIC_BUILDING


def enrichment_counts(
    catalog: Any,
    *,
    task_type: str,
    processor_id: str,
    source_id: int | None = None,
    fact_table: str | None = None,
) -> dict[str, int]:
    """``handled``/``pending`` of one enrichment processor for one scope.

    ``handled`` counts the assets whose **current** content revision has a job that reached a
    terminal state - a finished job of an older revision is history and must not make a
    changed photo look processed. The taken-time extractor is the one exception: its job
    revision digests the recorded positions as well (ADR-0011 §2), so its durable "this asset
    was processed" evidence is the row of its own fact table instead.
    """
    clause, parameters = _scope_clause(source_id)
    if fact_table is None:
        handled = int(
            catalog.read_one(
                "SELECT COUNT(*) AS n FROM jobs AS j "
                "JOIN assets AS a ON a.asset_id = CAST(j.subject_id AS INTEGER) "
                "WHERE j.subject_type = 'asset' AND j.task_type = ? AND j.processor_id = ? "
                f"AND j.state IN ({_placeholders(_TERMINAL_JOB_STATES)}) "
                "AND j.input_revision = a.sha256 "
                f"AND a.asset_id IN ({clause})",
                (str(task_type), str(processor_id), *_TERMINAL_JOB_STATES, *parameters),
            )["n"]
        )
    else:
        handled = int(
            catalog.read_one(
                f"SELECT COUNT(*) AS n FROM {fact_table} AS f "
                f"WHERE f.asset_id IN ({clause})",
                parameters,
            )["n"]
        )
    pending = int(
        catalog.read_one(
            "SELECT COUNT(*) AS n FROM jobs AS j "
            "JOIN assets AS a ON a.asset_id = CAST(j.subject_id AS INTEGER) "
            "WHERE j.subject_type = 'asset' AND j.task_type = ? AND j.processor_id = ? "
            f"AND j.state IN ({_placeholders(_LIVE_JOB_STATES)}) "
            "AND j.input_revision = a.sha256 "
            f"AND a.asset_id IN ({clause})",
            (str(task_type), str(processor_id), *_LIVE_JOB_STATES, *parameters),
        )["n"]
    )
    return {"handled": handled, "pending": pending}


def served_binding(
    catalog: Any, *, manifest_path: str | None, device_policy: str = "cpu_allowed"
) -> dict[str, Any] | None:
    """The ``(space, generation, processor)`` the *served* search path uses, or ``None``.

    The served pair is the active search profile's when one exists (that is what a search
    really queries), otherwise the pinned manifest's own derived generation. A profile that
    names a different space than the pinned model is reported as ``None``: this build cannot
    index into it, so the card must not show progress against it either (P4-03B owns the
    model upgrade).
    """
    if manifest_path is None:
        return None
    from photo_search.artifacts.store import generation_id_for
    from photo_search.indexing import semantic
    from photo_search.search import profile as profile_module

    spec = semantic.semantic_spec(manifest_path, {}, device_policy=device_policy)
    space_id = str(spec.envelope["space_id"])
    processor_id = str(spec.processor_id)
    derived_generation = generation_id_for(space_id, processor_id)
    active = profile_module.active_profile_id(catalog.connection)
    if active is not None:
        profile = profile_module.load_profile(catalog, str(active))
        if profile is not None:
            profile_space = str(getattr(profile, "space_id", "") or space_id)
            if profile_space != space_id:
                return None
            return {
                "space_id": profile_space,
                "generation_id": str(
                    getattr(profile, "image_generation_id", "") or derived_generation
                ),
                "processor_id": processor_id,
                "source": "active-profile",
            }
    return {
        "space_id": space_id,
        "generation_id": derived_generation,
        "processor_id": processor_id,
        "source": "pinned-manifest",
    }


def enrichment_availability(catalog: Any, *, data_root: Any) -> dict[str, bool]:
    """Which enrichment processors this machine can actually run (weights / dataset).

    An absent weight set or an unset place dataset is reported so the card can say
    "不可用" instead of an "in progress" that would never finish.
    """
    from photo_search.face.weights import weights_present as face_weights_present
    from photo_search.ocr.weights import weights_present as ocr_weights_present

    try:
        row = catalog.read_one("SELECT dataset_id FROM active_geo_dataset WHERE singleton = 1")
        place = row is not None and row["dataset_id"] is not None
    except Exception:  # noqa: BLE001 - a catalog without the place layer reports "unavailable"
        place = False
    return {
        "face": bool(face_weights_present(data_root)),
        "ocr": bool(ocr_weights_present(data_root)),
        "place": bool(place),
    }


def source_progress(
    catalog: Any,
    *,
    source_id: int,
    binding: Mapping[str, Any] | None,
    available: Mapping[str, bool] | None = None,
) -> dict[str, Any]:
    """The whole durable card of one source: scan counts, semantic backlog, enrichments."""
    available = dict(available or {})
    scan = scan_progress(catalog, source_id=source_id)
    stages: dict[str, Any] = {}
    if binding is None:
        semantic: dict[str, Any] = {
            "state": SEMANTIC_UNAVAILABLE,
            "reason": "SEMANTIC_CHANNEL_UNAVAILABLE",
            "total": 0,
            "confirmed": 0,
            "pending": 0,
            "failed": 0,
            "queued": 0,
            "space_id": None,
            "generation_id": None,
        }
    else:
        breakdown = semantic_breakdown(
            catalog,
            source_id=int(source_id),
            space_id=str(binding["space_id"]),
            processor_id=str(binding["processor_id"]),
        )
        semantic = {
            "state": semantic_state(breakdown),
            "reason": None,
            **breakdown,
            "space_id": str(binding["space_id"]),
            "generation_id": str(binding["generation_id"]),
        }
    stages["semantic"] = semantic
    total_assets = scoped_asset_count(catalog, source_id=int(source_id))
    for name, task_type, processor_id, fact_table in ENRICHMENT_STAGES:
        if not bool(available.get(name, name not in _NEEDS_WEIGHTS)):
            stages[name] = {
                "state": STAGE_UNAVAILABLE,
                "handled": 0,
                "pending": 0,
                "total": total_assets,
            }
            continue
        counts = enrichment_counts(
            catalog,
            source_id=int(source_id),
            task_type=task_type,
            processor_id=processor_id,
            fact_table=fact_table,
        )
        stages[name] = {
            "state": _enrichment_state(counts, total_assets),
            **counts,
            "total": total_assets,
        }
    return {
        "source_id": int(source_id),
        "scan": scan,
        # ``built`` is exactly "some image of this source has been classified by the semantic
        # layer", a durable fact rather than a process-local one.
        "built": int(semantic["total"]) > 0,
        "search_ready": str(semantic["state"]) == SEMANTIC_SEARCHABLE,
        "stages": stages,
    }


def _enrichment_state(counts: Mapping[str, int], total_assets: int) -> str:
    if int(counts["pending"]) > 0:
        return STAGE_IN_PROGRESS
    if total_assets <= 0 or int(counts["handled"]) >= int(total_assets):
        return STAGE_COMPLETE
    return STAGE_IN_PROGRESS


__all__ = [
    "ENRICHMENT_STAGES",
    "SEMANTIC_BUILDING",
    "SEMANTIC_SEARCHABLE",
    "SEMANTIC_TASK_TYPE",
    "SEMANTIC_UNBUILT",
    "SEMANTIC_UNAVAILABLE",
    "STAGE_COMPLETE",
    "STAGE_IN_PROGRESS",
    "STAGE_UNAVAILABLE",
    "enrichment_availability",
    "enrichment_counts",
    "scan_progress",
    "scoped_asset_count",
    "semantic_breakdown",
    "semantic_state",
    "served_binding",
    "source_progress",
]
