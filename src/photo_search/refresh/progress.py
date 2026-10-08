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

from collections.abc import Mapping, Sequence
from typing import Any

#: The frozen semantic task type (P2/P4 spelling), the same constant
#: :mod:`photo_search.refresh.pipeline` routes on.
SEMANTIC_TASK_TYPE = "image_embedding"

#: Semantic states of one source.
#:
#: * ``UNBUILT`` - no image of this scope has ever been classified *and* no build has run
#:   over it: the card offers "开始建库" instead of a progress bar;
#: * ``NO_IMAGES`` - a build ran and found nothing the image-text index is about (documents,
#:   videos, ...). Honest and terminal: there is nothing to index, and pressing "开始建库"
#:   again would only repeat the same empty sweep;
#: * ``BUILDING`` - images are classified and some of them are still pending;
#: * ``SEARCHABLE`` - every classified image of the scope is confirmed in the served
#:   generation (``failed`` may still be > 0: a refused image does not block the rest, and the
#:   card shows that count);
#: * ``UNINDEXABLE`` - images exist but none of them could be confirmed (the encoder refused
#:   every one of them), so this scope has nothing searchable;
#: * ``UNAVAILABLE`` - this process has no usable semantic channel at all.
SEMANTIC_UNAVAILABLE = "UNAVAILABLE"
SEMANTIC_UNBUILT = "UNBUILT"
SEMANTIC_NO_IMAGES = "NO_IMAGES"
SEMANTIC_BUILDING = "BUILDING"
SEMANTIC_SEARCHABLE = "SEARCHABLE"
SEMANTIC_UNINDEXABLE = "UNINDEXABLE"

#: ``metadata`` key prefix of the durable "a build has classified this source" marker. The
#: ``metadata`` table is this catalog's existing key/value store (``schema_version`` and
#: ``taken_time_rules_version`` already live there), so this adds no table, column or
#: migration - it only records the *fact* that one full build classified the scope, which is
#: what tells ``UNBUILT`` ("never looked at") apart from ``NO_IMAGES`` ("looked at, nothing to
#: index"). The two are otherwise indistinguishable in durable state: both have zero jobs and
#: zero confirmed entries.
BUILT_MARKER_PREFIX = "source_build:"

#: Enrichment states of one source. The card must never call a stage "完成" that has not run:
#:
#: * ``NOT_STARTED`` - no task of this stage has ever been recorded for the source's current
#:   content. This is the honest answer for a freshly registered (or only scanned) source, and
#:   the reason it exists as its own state: with nothing in scope, "0 of 0 handled" used to be
#:   reported as ``COMPLETE``;
#: * ``PENDING`` - some of the source's content has been handled before, but part of it has no
#:   terminal result and nothing is queued right now (a new photo between two refreshes);
#: * ``IN_PROGRESS`` - work of this stage is queued or running right now;
#: * ``COMPLETE`` - every currently readable asset has a terminal result for its current
#:   revision;
#: * ``UNAVAILABLE`` - this machine has no weights/dataset for the stage.
STAGE_UNAVAILABLE = "UNAVAILABLE"
STAGE_NOT_STARTED = "NOT_STARTED"
STAGE_PENDING = "PENDING"
STAGE_IN_PROGRESS = "IN_PROGRESS"
STAGE_COMPLETE = "COMPLETE"

#: The enrichment processors one source card shows, as
#: ``(name, task_type(s), processor_id, fact_table)``. One displayed stage may aggregate
#: multiple durable task types that implement the same processor. The first two read the original file with
#: the standard library (always runnable), the last three need weights or a place dataset and
#: are reported as ``UNAVAILABLE`` when this machine has none.
#:
#: ``fact_table`` names the derived table that proves "this asset was processed". It is only
#: needed for the taken-time extractor: unlike GPS/place/face/OCR - whose job revision *is*
#: the content hash - the capture-time input revision also digests the recorded position
#: facts (ADR-0011 §2), so no job revision can be compared against ``assets.sha256``.
ENRICHMENT_STAGES: tuple[tuple[str, str | tuple[str, ...], str, str | None], ...] = (
    ("taken_time", "taken_time_extract", "taken-time-exif@1", "asset_metadata"),
    ("gps", "geo_extract", "geo-exif-gps@1", None),
    ("place", "place_resolve", "geo-nearby-city@1", None),
    ("face", ("image_face", "image_face_detail_auto"), "face-model@1", None),
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


def _scope_clause(
    source_id: int | None = None, *, source_ids: Sequence[int] | None = None
) -> tuple[str, tuple[Any, ...]]:
    """The asset set one progress query is expressed against, plus its bound parameters.

    ``source_id=None`` means the whole library; otherwise the assets that still have a
    ``PRESENT``, content-bound position in that source. ``source_ids`` is the same set for a
    *union* of named sources, which is how several scopes are reported without counting an
    asset shared by two of them twice.
    """
    if source_ids is not None:
        identifiers = sorted({int(value) for value in source_ids})
        if not identifiers:
            return ("SELECT DISTINCT l.asset_id AS asset_id FROM file_locations AS l WHERE 0", ())
        placeholders = ", ".join("?" for _ in identifiers)
        return (
            "SELECT DISTINCT l.asset_id AS asset_id FROM file_locations AS l "
            f"WHERE l.source_id IN ({placeholders}) "
            "AND l.availability = 'PRESENT' AND l.asset_id IS NOT NULL",
            tuple(identifiers),
        )
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
    source_ids: Sequence[int] | None = None,
) -> dict[str, int]:
    """``total``/``confirmed``/``pending``/``failed``/``queued`` of one scope's images.

    ``total`` is the classified image set (see the module docstring); ``confirmed`` is the
    part whose current content revision is confirmed in the served generation; ``failed`` is
    the part the encoder already refused for this revision (a terminal job, so it is not
    "pending" any more); ``pending`` is the honest remainder; ``queued`` is how much of the
    work is sitting in the durable queue right now.
    """
    clause, parameters = _scope_clause(source_id, source_ids=source_ids)
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


def built_marker_key(source_id: int) -> str:
    """The ``metadata`` key that records "a build has classified this source"."""
    return f"{BUILT_MARKER_PREFIX}{int(source_id)}"


def mark_source_built(catalog: Any, *, source_id: int, value: str) -> None:
    """Record durably that one build has classified this source's scope.

    Written by the build's classification sweep, in its own short transaction. It is not a
    progress cache (every number stays derived from ``jobs``/``index_entries``); it only
    answers "was this scope ever looked at?", which is what distinguishes ``UNBUILT`` from
    ``NO_IMAGES`` (P4-02Y-I review F2).
    """
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (built_marker_key(source_id), str(value)),
        )


def source_built(catalog: Any, *, source_id: int) -> bool:
    """Whether a build has ever classified this source (durable marker lookup)."""
    try:
        row = catalog.read_one(
            "SELECT 1 FROM metadata WHERE key = ?", (built_marker_key(source_id),)
        )
    except Exception:  # noqa: BLE001 - a catalog without the metadata table is "unbuilt"
        return False
    return row is not None


def semantic_state(breakdown: Mapping[str, int], *, built: bool) -> str:
    """The card's semantic state word for one durable breakdown.

    ``failed`` is *deliberately* not part of the "is it searchable" decision: a refused image
    never becomes searchable and never blocks the rest (P4-02Y's own rule). It is reported so
    the card can say how many images are missing and why, instead of a bare "可搜索".
    """
    total = int(breakdown["total"])
    confirmed = int(breakdown["confirmed"])
    pending = int(breakdown["pending"])
    if total == 0 and confirmed == 0:
        return SEMANTIC_NO_IMAGES if built else SEMANTIC_UNBUILT
    if pending > 0:
        return SEMANTIC_BUILDING
    if confirmed > 0:
        return SEMANTIC_SEARCHABLE
    return SEMANTIC_UNINDEXABLE


def enrichment_counts(
    catalog: Any,
    *,
    task_type: str | Sequence[str],
    processor_id: str,
    source_id: int | None = None,
    fact_table: str | None = None,
) -> dict[str, int]:
    """``handled``/``pending``/``attempted`` of one enrichment processor for one scope.

    ``handled`` counts the assets whose **current** content revision has a job that reached a
    terminal state - a finished job of an older revision is history and must not make a
    changed photo look processed. The taken-time extractor is the one exception: its job
    revision digests the recorded positions as well (ADR-0011 §2), so its durable "this asset
    was processed" evidence is the row of its own fact table instead. ``attempted`` counts the
    in-scope assets that have *any* job row for this processor, whatever its revision: it is
    what tells "this stage never ran for this source" (``NOT_STARTED``) apart from "part of the
    content is not processed yet" (``PENDING``).
    """
    task_types = (str(task_type),) if isinstance(task_type, str) else tuple(map(str, task_type))
    if not task_types:
        raise ValueError("task_type must contain at least one task type")
    task_placeholders = _placeholders(task_types)
    clause, parameters = _scope_clause(source_id)
    if fact_table is None:
        handled = int(
            catalog.read_one(
                "SELECT COUNT(DISTINCT a.asset_id) AS n FROM jobs AS j "
                "JOIN assets AS a ON a.asset_id = CAST(j.subject_id AS INTEGER) "
                f"WHERE j.subject_type = 'asset' AND j.task_type IN ({task_placeholders}) "
                "AND j.processor_id = ? "
                f"AND j.state IN ({_placeholders(_TERMINAL_JOB_STATES)}) "
                "AND j.input_revision = a.sha256 "
                f"AND a.asset_id IN ({clause})",
                (*task_types, str(processor_id), *_TERMINAL_JOB_STATES, *parameters),
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
            "SELECT COUNT(DISTINCT a.asset_id) AS n FROM jobs AS j "
            "JOIN assets AS a ON a.asset_id = CAST(j.subject_id AS INTEGER) "
            f"WHERE j.subject_type = 'asset' AND j.task_type IN ({task_placeholders}) "
            "AND j.processor_id = ? "
            f"AND j.state IN ({_placeholders(_LIVE_JOB_STATES)}) "
            "AND j.input_revision = a.sha256 "
            f"AND a.asset_id IN ({clause})",
            (*task_types, str(processor_id), *_LIVE_JOB_STATES, *parameters),
        )["n"]
    )
    attempted = int(
        catalog.read_one(
            "SELECT COUNT(DISTINCT a.asset_id) AS n FROM jobs AS j "
            "JOIN assets AS a ON a.asset_id = CAST(j.subject_id AS INTEGER) "
            f"WHERE j.subject_type = 'asset' AND j.task_type IN ({task_placeholders}) "
            "AND j.processor_id = ? "
            f"AND a.asset_id IN ({clause})",
            (*task_types, str(processor_id), *parameters),
        )["n"]
    )
    return {"handled": handled, "pending": pending, "attempted": attempted}


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
    # "Has a build classified this scope?" is durable and separate from the semantic counts:
    # a scope whose files are all documents has zero of both, and only this marker tells the
    # card that the sweep really happened (P4-02Y-I review F2).
    classified_once = source_built(catalog, source_id=int(source_id))
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
            "state": semantic_state(breakdown, built=classified_once),
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
                "attempted": 0,
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
        # ``built`` is exactly "a build has classified this source" (or at least one image of
        # it is classified, for a library built before this marker existed) - a durable fact
        # rather than a process-local one.
        "built": bool(classified_once or int(semantic["total"]) > 0),
        # ``NO_IMAGES`` counts as ready on purpose: the scope's backlog is empty because the
        # index is not about any of its files, which the state word says explicitly.
        "search_ready": str(semantic["state"]) in (SEMANTIC_SEARCHABLE, SEMANTIC_NO_IMAGES),
        "stages": stages,
    }


def _enrichment_state(counts: Mapping[str, int], total_assets: int) -> str:
    """One honest word for a stage, from durable counts only.

    The order matters: ``pending`` first (something is really running), then "nothing readable
    to analyse" (the case that used to be mislabelled ``COMPLETE`` for a brand-new source),
    then "everything handled", and only then the two "nothing happened yet" answers.
    """
    total = int(total_assets)
    if int(counts["pending"]) > 0:
        return STAGE_IN_PROGRESS
    if total <= 0:
        return STAGE_NOT_STARTED
    if int(counts["handled"]) >= total:
        return STAGE_COMPLETE
    if int(counts["attempted"]) == 0:
        return STAGE_NOT_STARTED
    return STAGE_PENDING


__all__ = [
    "BUILT_MARKER_PREFIX",
    "ENRICHMENT_STAGES",
    "SEMANTIC_BUILDING",
    "SEMANTIC_NO_IMAGES",
    "SEMANTIC_SEARCHABLE",
    "SEMANTIC_TASK_TYPE",
    "SEMANTIC_UNINDEXABLE",
    "SEMANTIC_UNBUILT",
    "SEMANTIC_UNAVAILABLE",
    "STAGE_COMPLETE",
    "STAGE_IN_PROGRESS",
    "STAGE_NOT_STARTED",
    "STAGE_PENDING",
    "STAGE_UNAVAILABLE",
    "built_marker_key",
    "enrichment_availability",
    "enrichment_counts",
    "mark_source_built",
    "scan_progress",
    "scoped_asset_count",
    "semantic_breakdown",
    "semantic_state",
    "served_binding",
    "source_built",
    "source_progress",
]
