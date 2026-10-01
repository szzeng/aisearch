"""The authoritative coverage/health gate of the controlled profile selection (ADR-0003 §8).

Publishing a search profile moves exactly one pointer, and the invariant behind that pointer is
"the served generation covers the current library". ``README``-level wording ("the build said
READY") is not that invariant: a generation can be READY while the library has grown, while an
outbox event is still pending, or while the artifact files are gone.

So this module computes the facts from the catalog itself:

* every current ``ACTIVE`` asset with a ``PRESENT`` location **that this index is about** must have
  a ``CONFIRMED`` entry for *this* generation whose artifact was produced from the asset's
  *current* ``sha256``. "What this index is about" is the frozen media policy - a video, an archive
  or a file the decoder cannot identify is out of scope for an image-text index and can never be
  covered by one, so requiring it would turn a permanent property of the library into a permanent
  refusal (P4-Embedding-Arch: the real 3610-asset library holds 316 videos, 76 unidentified files
  and 36 TIFFs; only 3182 files are indexable images);
* an outbox event that is not ``CONFIRMED``, or an entry in ``PENDING_REPAIR``/``STALE``, means the
  generation is still moving and is refused;
* an ``ACTIVE`` asset whose every location is ``MISSING`` is an explicit refusal too: the
  selector never guesses that an offline file "probably still matches";
* every artifact row the generation references must be ``PUBLISHED`` and its stored content must
  still be readable (checked outside the transaction; the rows are re-read inside it).

The heavy, I/O-bound half (artifact bytes, the Lance table) is prepared *before* the transaction;
the cheap, authoritative half (the rows of the same facts) is re-read by
:func:`selection_precondition` **inside** the ``BEGIN IMMEDIATE`` transaction that moves the
pointer, which is what ADR-0003 §8 requires.

The classification itself is I/O (it opens the files), so it happens outside the transaction and
hands the transaction two things: the exact list of indexable ``asset_id``s, and a digest of the
``(asset_id, sha256)`` set of the whole searchable library. The transaction re-reads that digest
and the coverage rows for exactly those ids, so a file added, removed or re-scanned between
preparation and commit is a structured refusal rather than a silent partial switch.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from hashlib import sha256
from pathlib import Path
from typing import Any

from photo_search.catalog.errors import (
    SELECTION_ARTIFACT_UNREADABLE,
    SELECTION_COVERAGE_INCOMPLETE,
    SELECTION_EMPTY_GENERATION,
    SELECTION_INDEX_UNHEALTHY,
    SELECTION_OUTBOX_PENDING,
    CatalogError,
)

#: How many missing asset ids the report lists (the counts stay exact).
MISSING_SAMPLE_LIMIT = 20

#: A full Lance row-count cross-check materialises the generation, so it is bounded here. A
#: larger generation is reported as ``checked="bounded"`` and the gate refuses instead of
#: pretending the index was verified.
FULL_LANCE_CHECK_MAX_ROWS = 20_000

#: How many confirmed artifacts are read back (and checksum-verified) before the rest are only
#: checked for existence. A 0-16 MiB embedding file is cheap to read; the bound keeps the
#: selection predictable on a large library.
FULL_ARTIFACT_VERIFY_MAX = 20_000

#: Every current asset with a readable location, with the content revision that is current now.
_SEARCHABLE_SQL = (
    "SELECT a.asset_id AS asset_id, a.sha256 AS sha256, l.relative_path AS relative_path, "
    "s.root_path AS root_path FROM assets AS a "
    "JOIN file_locations AS l ON l.location_id = ("
    "SELECT l2.location_id FROM file_locations AS l2 WHERE l2.asset_id = a.asset_id "
    "AND l2.availability = 'PRESENT' ORDER BY l2.location_id LIMIT 1) "
    "JOIN source_roots AS s ON s.source_id = l.source_id "
    "WHERE a.state = 'ACTIVE' ORDER BY a.asset_id"
)
_OFFLINE_SQL = (
    "SELECT COUNT(*) AS count FROM assets AS a WHERE a.state = 'ACTIVE' AND NOT EXISTS ("
    "SELECT 1 FROM file_locations AS l WHERE l.asset_id = a.asset_id "
    "AND l.availability = 'PRESENT')"
)
#: The confirmed coverage of one *given* set of asset ids at their current content revision.
_COVERED_SQL = (
    "SELECT COUNT(*) AS count FROM index_entries AS e "
    "JOIN artifacts AS ar ON ar.artifact_id = e.artifact_id "
    "JOIN assets AS a ON a.asset_id = e.asset_id "
    "WHERE e.space_id = ? AND e.generation_id = ? AND e.state = 'CONFIRMED' "
    "AND ar.lifecycle_state = 'PUBLISHED' AND ar.input_revision = a.sha256 "
    "AND e.asset_id IN (SELECT value FROM json_each(?))"
)
_MISSING_IDS_SQL = (
    "SELECT value AS asset_id FROM json_each(?) WHERE value NOT IN ("
    "SELECT e.asset_id FROM index_entries AS e "
    "JOIN artifacts AS ar ON ar.artifact_id = e.artifact_id "
    "JOIN assets AS a ON a.asset_id = e.asset_id "
    "WHERE e.space_id = ? AND e.generation_id = ? AND e.state = 'CONFIRMED' "
    "AND ar.lifecycle_state = 'PUBLISHED' AND ar.input_revision = a.sha256 "
    "AND e.asset_id IN (SELECT value FROM json_each(?))) ORDER BY value LIMIT ?"
)
_PENDING_OUTBOX_SQL = (
    "SELECT COUNT(*) AS count FROM index_outbox "
    "WHERE space_id = ? AND generation_id = ? AND state <> 'CONFIRMED'"
)
_ENTRY_STATE_SQL = (
    "SELECT state, COUNT(*) AS count FROM index_entries "
    "WHERE space_id = ? AND generation_id = ? GROUP BY state"
)
_CONFIRMED_ENTRIES_SQL = (
    "SELECT e.asset_id AS asset_id, e.artifact_id AS artifact_id, "
    "ar.storage_uri AS storage_uri, ar.byte_size AS byte_size, "
    "ar.checksum_sha256 AS checksum_sha256, ar.lifecycle_state AS lifecycle_state "
    "FROM index_entries AS e JOIN artifacts AS ar ON ar.artifact_id = e.artifact_id "
    "WHERE e.space_id = ? AND e.generation_id = ? AND e.state = 'CONFIRMED' "
    "ORDER BY e.asset_id"
)
_GENERATION_SQL = "SELECT * FROM index_generations WHERE generation_id = ?"


@dataclass(frozen=True)
class CoverageReport:
    """The authoritative coverage facts of one candidate generation."""

    generation_id: str
    space_id: str
    processor_id: str
    dimension: int | None
    generation_state: str | None
    #: The exact assets this index must cover (current, readable, indexable images).
    required_ids: tuple[int, ...]
    #: Digest over ``(asset_id, sha256)`` of every readable current asset, indexable or not.
    catalog_digest: str
    required: int
    covered: int
    missing: int
    missing_sample: tuple[int, ...]
    offline_assets: int
    pending_outbox: int
    pending_repair: int
    stale_entries: int
    confirmed_entries: int
    unreadable_artifacts: tuple[str, ...]
    artifacts_checked: int
    artifacts_verified: int
    out_of_scope: dict[str, int] = field(default_factory=dict)
    readable: bool = True
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return (
            self.missing == 0
            and self.offline_assets == 0
            and self.pending_outbox == 0
            and self.pending_repair == 0
            and self.stale_entries == 0
            and not self.unreadable_artifacts
            and self.covered > 0
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "generation_id": self.generation_id,
            "space_id": self.space_id,
            "processor_id": self.processor_id,
            "dimension": self.dimension,
            "generation_state": self.generation_state,
            "required_ids": len(self.required_ids),
            "catalog_digest": self.catalog_digest,
            "required": self.required,
            "covered": self.covered,
            "missing": self.missing,
            "missing_sample": list(self.missing_sample),
            "offline_assets": self.offline_assets,
            "pending_outbox": self.pending_outbox,
            "pending_repair": self.pending_repair,
            "stale_entries": self.stale_entries,
            "confirmed_entries": self.confirmed_entries,
            "out_of_scope": dict(self.out_of_scope),
            "unreadable_artifacts": list(self.unreadable_artifacts),
            "artifacts_checked": self.artifacts_checked,
            "artifacts_verified": self.artifacts_verified,
            "readable": bool(self.readable),
            "detail": dict(self.detail),
        }


def _connection_of(target: Any) -> sqlite3.Connection:
    return getattr(target, "connection", target)


def _scalar(connection: sqlite3.Connection, sql: str, parameters: tuple[Any, ...] = ()) -> int:
    row = connection.execute(sql, parameters).fetchone()
    if row is None:
        return 0
    return int(_value(row, "count", 0))


def _value(row: Any, name: str, index: int) -> Any:
    """Column access that works with this project's ``sqlite3.Row`` and with plain tuples."""
    if isinstance(row, sqlite3.Row):
        return row[name]
    return row[index]


@dataclass(frozen=True)
class LibraryRequirements:
    """What the searchable library currently is: exact ids plus a digest of their revisions."""

    asset_ids: tuple[int, ...]
    sha256_by_asset: dict[int, str]
    digest: str

    @property
    def count(self) -> int:
        return len(self.asset_ids)


def catalog_requirements(target: Any) -> LibraryRequirements:
    """Every current, readable asset with its current content revision (rows only, no I/O)."""
    connection = _connection_of(target)
    rows = connection.execute(_SEARCHABLE_SQL).fetchall()
    pairs: list[tuple[int, str]] = []
    revisions: dict[int, str] = {}
    for row in rows:
        asset_id = int(_value(row, "asset_id", 0))
        revision = str(_value(row, "sha256", 1))
        pairs.append((asset_id, revision))
        revisions[asset_id] = revision
    digest = sha256(
        "\n".join(f"{asset_id}:{revision}" for asset_id, revision in pairs).encode("utf-8")
    ).hexdigest()
    return LibraryRequirements(
        asset_ids=tuple(asset_id for asset_id, _ in pairs),
        sha256_by_asset=revisions,
        digest=digest,
    )


def indexable_assets(
    target: Any, requirements: LibraryRequirements
) -> tuple[tuple[int, ...], dict[str, int]]:
    """Split the searchable library into "an image this index is about" and everything else.

    This is the I/O half: it applies the frozen media policy (the same
    :func:`photo_search.indexing.semantic.image_eligibility` the build itself uses) to every
    current asset, so the gate and the build agree on what can possibly be covered. Callers run it
    *before* the selection transaction and pass the resulting ids into
    :func:`coverage_report`.
    """
    from photo_search.indexing import semantic

    connection = _connection_of(target)
    rows = {
        int(_value(row, "asset_id", 0)): row
        for row in connection.execute(_SEARCHABLE_SQL).fetchall()
    }
    indexable: list[int] = []
    labels: dict[str, int] = {}
    for asset_id in requirements.asset_ids:
        row = rows.get(int(asset_id))
        if row is None:  # pragma: no cover - the two reads share one snapshot in practice
            labels["NO_PRESENT_LOCATION"] = labels.get("NO_PRESENT_LOCATION", 0) + 1
            continue
        path = Path(str(_value(row, "root_path", 3))) / str(_value(row, "relative_path", 2))
        is_image, label = semantic.image_eligibility(path)
        if is_image:
            indexable.append(int(asset_id))
        else:
            labels[str(label)] = labels.get(str(label), 0) + 1
    return tuple(indexable), labels


def coverage_report(
    target: Any,
    *,
    generation_id: str,
    space_id: str,
    processor_id: str,
    required_ids: Sequence[int] | None = None,
    catalog_digest: str = "",
    out_of_scope: Mapping[str, int] | None = None,
    full_artifact_verify_max: int = FULL_ARTIFACT_VERIFY_MAX,
    sample_limit: int = MISSING_SAMPLE_LIMIT,
) -> CoverageReport:
    """The authoritative coverage of one generation, computed from the catalog rows only.

    ``required_ids`` is the classified indexable set from :func:`indexable_assets`; without it the
    report falls back to "every readable current asset", which is the right answer for a library
    that only holds images and is what the unit tests use. ``readable`` is left ``True`` here: the
    artifact *content* check needs the artifact store and is prepared outside the transaction by
    :func:`verify_artifacts`; the rows this function reads are exactly the ones re-read inside it.
    """
    connection = _connection_of(target)
    if required_ids is None:
        requirements = catalog_requirements(connection)
        required_ids = requirements.asset_ids
        catalog_digest = requirements.digest
    identifiers = [int(value) for value in required_ids]
    payload = json.dumps(identifiers, separators=(",", ":"))
    generation = connection.execute(_GENERATION_SQL, (str(generation_id),)).fetchone()
    dimension = None
    state = None
    if generation is not None:
        dimension = int(_value(generation, "dimension", 3))
        state = str(_value(generation, "state", 4))
    required = len(identifiers)
    covered = _scalar(
        connection, _COVERED_SQL, (str(space_id), str(generation_id), payload)
    )
    offline = _scalar(connection, _OFFLINE_SQL)
    pending_outbox = _scalar(
        connection, _PENDING_OUTBOX_SQL, (str(space_id), str(generation_id))
    )
    states = {
        str(_value(row, "state", 0)): int(_value(row, "count", 1))
        for row in connection.execute(
            _ENTRY_STATE_SQL, (str(space_id), str(generation_id))
        ).fetchall()
    }
    missing_rows = connection.execute(
        _MISSING_IDS_SQL,
        (payload, str(space_id), str(generation_id), payload, int(max(0, sample_limit))),
    ).fetchall()
    missing_sample = tuple(int(_value(row, "asset_id", 0)) for row in missing_rows)
    covered = min(int(covered), int(required))
    artifact_rows = connection.execute(
        _CONFIRMED_ENTRIES_SQL, (str(space_id), str(generation_id))
    ).fetchall()
    unreadable = tuple(
        str(_value(row, "artifact_id", 0))
        for row in artifact_rows
        if str(_value(row, "lifecycle_state", 5)) != "PUBLISHED"
    )
    return CoverageReport(
        generation_id=str(generation_id),
        space_id=str(space_id),
        processor_id=str(processor_id),
        dimension=dimension,
        generation_state=state,
        required_ids=tuple(identifiers),
        catalog_digest=str(catalog_digest),
        required=required,
        covered=covered,
        missing=max(0, required - covered),
        missing_sample=missing_sample,
        offline_assets=offline,
        pending_outbox=pending_outbox,
        pending_repair=int(states.get("PENDING_REPAIR", 0)),
        stale_entries=int(states.get("STALE", 0)),
        confirmed_entries=int(states.get("CONFIRMED", 0)),
        unreadable_artifacts=unreadable,
        artifacts_checked=len(artifact_rows),
        artifacts_verified=min(len(artifact_rows), int(full_artifact_verify_max)),
        out_of_scope=dict(out_of_scope or {}),
    )


def verify_artifacts(
    store: Any,
    target: Any,
    *,
    generation_id: str,
    space_id: str,
    verify_bytes: bool = True,
    max_verified: int = FULL_ARTIFACT_VERIFY_MAX,
) -> tuple[str, ...]:
    """Read back the generation's artifacts; return the ids that are not readable.

    This is the I/O half of the gate and runs *outside* the selection transaction. The stored
    content is read through the artifact store's own resolver, so a path that escaped the
    artifact root, a truncated file or a checksum mismatch is reported as unreadable instead of
    being counted as coverage.
    """
    connection = _connection_of(target)
    rows = connection.execute(
        _CONFIRMED_ENTRIES_SQL, (str(space_id), str(generation_id))
    ).fetchall()
    unreadable: list[str] = []
    for index, row in enumerate(rows):
        artifact_id = str(_value(row, "artifact_id", 0))
        if index >= int(max_verified) and not verify_bytes:
            break
        try:
            if verify_bytes and index < int(max_verified):
                store.read(row)
            else:
                path = store.resolve_uri(str(_value(row, "storage_uri", 1)))
                if (
                    not Path(path).is_file()
                    or int(_value(row, "byte_size", 2)) != Path(path).stat().st_size
                ):
                    unreadable.append(artifact_id)
        except Exception:  # noqa: BLE001 - an unreadable artifact is data, not a crash
            unreadable.append(artifact_id)
    return tuple(unreadable)


def lance_health(
    root: str | Path,
    *,
    generation_id: str,
    space_id: str,
    dimension: int,
    expected_rows: int,
    full_check_max_rows: int = FULL_LANCE_CHECK_MAX_ROWS,
) -> dict[str, Any]:
    """Whether the Lance table of one generation is openable and matches the catalog.

    The row-count cross-check materialises the generation through the existing adapter, so it is
    bounded: a larger generation is reported as ``checked="bounded"`` and the gate refuses rather
    than assuming the derived store is intact.
    """
    from photo_search.indexing.store import LanceVectorIndex

    index = LanceVectorIndex(Path(root))
    result: dict[str, Any] = {
        "generation_id": str(generation_id),
        "expected_rows": int(expected_rows),
        "checked": "none",
        "rows": None,
        "state": "unknown",
    }
    if int(expected_rows) > int(full_check_max_rows):
        result["state"] = "unverified"
        result["reason"] = "ROW_COUNT_CHECK_BOUNDED"
        return result
    try:
        rows = index.count(
            generation_id=str(generation_id), dimension=int(dimension), space_id=str(space_id)
        )
    except Exception as error:  # noqa: BLE001 - an unreadable store is reported, never assumed
        result["state"] = "unhealthy"
        result["reason"] = type(error).__name__
        return result
    result["checked"] = "full"
    result["rows"] = int(rows)
    if int(rows) != int(expected_rows):
        result["state"] = "unhealthy"
        result["reason"] = "ROW_COUNT_MISMATCH"
        return result
    result["state"] = "healthy"
    return result


def require_selection_ready(report: CoverageReport, *, lance: Mapping[str, Any] | None = None) -> None:
    """The structured refusal of one coverage/health report, or a silent pass."""
    if report.generation_state != "READY":
        raise CatalogError(
            SELECTION_INDEX_UNHEALTHY,
            "the candidate generation is not READY",
            details={"reason": "GENERATION_NOT_READY", "state": report.generation_state},
        )
    if report.covered == 0 or report.confirmed_entries == 0:
        raise CatalogError(
            SELECTION_EMPTY_GENERATION,
            "the candidate generation holds no confirmed image",
            details={"reason": "EMPTY_GENERATION", "generation_id": report.generation_id},
        )
    if report.offline_assets:
        raise CatalogError(
            SELECTION_COVERAGE_INCOMPLETE,
            "some current assets have no readable file location",
            details={
                "reason": "OFFLINE_ASSETS_PRESENT",
                "offline_assets": int(report.offline_assets),
                "generation_id": report.generation_id,
            },
        )
    if report.missing:
        raise CatalogError(
            SELECTION_COVERAGE_INCOMPLETE,
            "the candidate generation does not cover the current library",
            details={
                "reason": "COVERAGE_MISSING",
                "required": int(report.required),
                "covered": int(report.covered),
                "missing": int(report.missing),
                "missing_sample": list(report.missing_sample),
                "space_id": report.space_id,
            },
        )
    if report.pending_outbox or report.pending_repair or report.stale_entries:
        raise CatalogError(
            SELECTION_OUTBOX_PENDING,
            "the candidate generation still has unconfirmed or repairable entries",
            details={
                "reason": "OUTBOX_NOT_CONFIRMED",
                "pending_outbox": int(report.pending_outbox),
                "pending_repair": int(report.pending_repair),
                "stale_entries": int(report.stale_entries),
                "generation_id": report.generation_id,
            },
        )
    if report.unreadable_artifacts:
        raise CatalogError(
            SELECTION_ARTIFACT_UNREADABLE,
            "a referenced artifact of the candidate generation is not readable",
            details={
                "reason": "ARTIFACT_UNREADABLE",
                "artifacts": list(report.unreadable_artifacts)[:20],
                "generation_id": report.generation_id,
            },
        )
    if lance is not None and str(lance.get("state")) != "healthy":
        raise CatalogError(
            SELECTION_INDEX_UNHEALTHY,
            "the derived vector store of the candidate generation is not verifiable",
            details={
                "reason": str(lance.get("reason") or "LANCE_UNHEALTHY"),
                "rows": lance.get("rows"),
                "expected_rows": lance.get("expected_rows"),
                "checked": str(lance.get("checked")),
                "generation_id": report.generation_id,
            },
        )


def selection_precondition(
    prepared: CoverageReport,
    *,
    lance: Mapping[str, Any] | None = None,
) -> Callable[[sqlite3.Connection], None]:
    """The transaction-internal re-check of one prepared selection.

    The closure re-reads the *same* rows from the connection that is about to move the pointer and
    refuses if anything moved since the preparation. That is the whole point of the seam: the
    expensive checks happen before the transaction, but the authoritative decision is made
    inside it.
    """

    def check(connection: sqlite3.Connection) -> None:
        # The searchable library itself must not have moved: a file added, removed, re-scanned or
        # re-hashed between preparation and commit changes this digest and is refused, so the
        # classified id list the coverage was computed for is still the right one.
        current_requirements = catalog_requirements(connection)
        if current_requirements.digest != str(prepared.catalog_digest):
            raise CatalogError(
                SELECTION_COVERAGE_INCOMPLETE,
                "the searchable library changed while the selection was being validated",
                details={
                    "reason": "CATALOG_CHANGED_IN_TRANSACTION",
                    "generation_id": prepared.generation_id,
                    "prepared_assets": prepared.required,
                    "current_assets": current_requirements.count,
                },
            )
        current = coverage_report(
            connection,
            generation_id=prepared.generation_id,
            space_id=prepared.space_id,
            processor_id=prepared.processor_id,
            required_ids=prepared.required_ids,
            catalog_digest=prepared.catalog_digest,
            out_of_scope=prepared.out_of_scope,
        )
        changed = [
            name
            for name, before, after in (
                ("generation_state", prepared.generation_state, current.generation_state),
                ("catalog_digest", prepared.catalog_digest, current.catalog_digest),
                ("required", prepared.required, current.required),
                ("covered", prepared.covered, current.covered),
                ("missing", prepared.missing, current.missing),
                ("offline_assets", prepared.offline_assets, current.offline_assets),
                ("pending_outbox", prepared.pending_outbox, current.pending_outbox),
                ("pending_repair", prepared.pending_repair, current.pending_repair),
                ("stale_entries", prepared.stale_entries, current.stale_entries),
            )
            if before != after
        ]
        if changed:
            raise CatalogError(
                SELECTION_COVERAGE_INCOMPLETE,
                "the candidate generation changed while the selection was being validated",
                details={
                    "reason": "COVERAGE_CHANGED_IN_TRANSACTION",
                    "changed": changed,
                    "generation_id": prepared.generation_id,
                },
            )
        require_selection_ready(current, lance=lance)

    return check


def selection_report(
    target: Any,
    store: Any,
    *,
    generation_id: str,
    space_id: str,
    processor_id: str,
    vectors_root: str | Path | None = None,
    verify_artifact_bytes: bool = True,
) -> tuple[CoverageReport, dict[str, Any]]:
    """Prepare (outside any transaction) the coverage and health facts of one candidate."""
    requirements = catalog_requirements(target)
    required_ids, out_of_scope = indexable_assets(target, requirements)
    report = coverage_report(
        target,
        generation_id=str(generation_id),
        space_id=str(space_id),
        processor_id=str(processor_id),
        required_ids=required_ids,
        catalog_digest=requirements.digest,
        out_of_scope=out_of_scope,
    )
    unreadable = verify_artifacts(
        store,
        target,
        generation_id=str(generation_id),
        space_id=str(space_id),
        verify_bytes=bool(verify_artifact_bytes),
    )
    if unreadable:
        report = replace(report, unreadable_artifacts=unreadable)
    lance: dict[str, Any] = {"state": "skipped", "checked": "none"}
    if vectors_root is not None and report.dimension:
        lance = lance_health(
            vectors_root,
            generation_id=str(generation_id),
            space_id=str(space_id),
            dimension=int(report.dimension),
            expected_rows=int(report.confirmed_entries),
        )
    return report, lance


__all__ = [
    "FULL_ARTIFACT_VERIFY_MAX",
    "FULL_LANCE_CHECK_MAX_ROWS",
    "CoverageReport",
    "LibraryRequirements",
    "catalog_requirements",
    "coverage_report",
    "indexable_assets",
    "lance_health",
    "require_selection_ready",
    "selection_precondition",
    "selection_report",
    "verify_artifacts",
]
