"""The derived search document of one asset, and its durable reprojection queue.

Architecture §4.3 separates the six text columns of ``search_documents``; §4.5.3 fixes how
a directory edit propagates: the affected set is persisted in the *same short transaction*
that changed the fact, and the derived text is written later in bounded batches. That is
exactly the split implemented here.

What P2-04 fills:

* ``filename_text`` - the basenames (extension included) of this asset's ``PRESENT``
  locations. Never a directory name and never an absolute path.
* ``place_text`` - the current *effective* place: its canonical name, the full ancestor
  chain, and the ACTIVE aliases of that place and of its ancestors. Nothing else: a
  ``SUGGESTION_ONLY`` candidate, an excluded place, ``MANUAL_UNASSIGNED`` and a retired
  place never reach this column.

P3-01 fills:

* ``ocr_text`` - the normalised search copy of the asset's current OCR document
  (``asset_ocr_text.search_text``, written by the OCR processor). The *original* text and
  the exact polygons stay in the immutable structured artifact, never in this table. A row
  whose ``input_revision`` no longer equals ``assets.sha256`` is history and contributes
  nothing: text recognised before a content change is never attached to the new content.

``user_text``/``caption_text``/``tag_text`` stay empty strings (P2-05 and later own them);
no content is ever fabricated for them.

P4-02V adds the contention rule of a *read* path: an interactive catch-up must never let the
background drain's write lock turn a legal search into an error. It waits a bounded
``CATCH_UP_BUSY_BUDGET_MS`` for the writer lock and, while the lock is still held, returns the
lag it leaves (``state = LAGGING``, unchanged ``queued_assets``) instead of raising
``CATALOG_UNREADABLE``. Nothing is dropped: the affected set is durable, so the next catch-up
- or the same search once the drain stops - re-derives it.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import CatalogError
from photo_search.places.directory import active_alias_rows, ancestor_rows, get_place_row
from photo_search.search.tokens import FTS_GENERATION_ID, TOKENIZER_VERSION, index_text

#: The six derived text columns, in the frozen order of the content table.
TEXT_COLUMNS = (
    "filename_text",
    "user_text",
    "ocr_text",
    "caption_text",
    "tag_text",
    "place_text",
)

#: Assets re-derived in one short transaction, and how many such batches one interactive
#: catch-up performs before it reports the remaining lag instead of holding a long write.
DRAIN_BATCH_SIZE = 200
CATCH_UP_BATCHES = 4

#: How long one interactive catch-up waits for the single-writer lock before it gives up and
#: reports the lag it leaves (P4-02V). ``Catalog``'s own 5 s budget is the *maintenance*
#: budget: P4-02U measured the background drain holding the write lock for ~99% of a 57.6 s
#: build, with continuous unavailability up to 32 449.4 ms while every single transaction
#: stayed below 0.7 s. Inside such a window a longer wait cannot succeed - it only makes the
#: reader wait five seconds and then refuse the whole search. Skipping is lossless: queued
#: assets stay in ``search_projection_queue``, so the next catch-up re-derives them.
CATCH_UP_BUSY_BUDGET_MS = 100

_PRESENT_LOCATIONS_SQL = (
    "SELECT relative_path FROM file_locations WHERE asset_id = ? AND availability = 'PRESENT' "
    "ORDER BY location_id"
)
_EFFECTIVE_PLACE_SQL = (
    "SELECT place_id FROM asset_place_assignments WHERE asset_id = ? "
    "AND mode = 'MANUAL_ASSIGNED'"
)
_ASSET_EXISTS_SQL = "SELECT 1 FROM assets WHERE asset_id = ?"
#: The current OCR search copy of one asset: only a document recognised for exactly the
#: content revision the asset carries today may feed the derived text.
_OCR_TEXT_SQL = (
    "SELECT t.search_text FROM asset_ocr_text AS t "
    "JOIN assets AS a ON a.asset_id = t.asset_id "
    "WHERE t.asset_id = ? AND t.input_revision = a.sha256"
)


def basename(relative_path: str) -> str:
    """The last path segment, accepting both separators the catalog may store."""
    text = str(relative_path).replace("\\", "/")
    return text.rsplit("/", 1)[-1]


def filename_values(connection: sqlite3.Connection, asset_id: int) -> list[str]:
    """Distinct basenames of the ``PRESENT`` locations, in registration order."""
    seen: set[str] = set()
    names: list[str] = []
    for row in connection.execute(_PRESENT_LOCATIONS_SQL, (int(asset_id),)).fetchall():
        name = basename(str(row["relative_path"]))
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def place_values(connection: sqlite3.Connection, asset_id: int) -> list[str]:
    """Canonical names and ACTIVE aliases of the effective place chain, root first."""
    row = connection.execute(_EFFECTIVE_PLACE_SQL, (int(asset_id),)).fetchone()
    if row is None or row["place_id"] is None:
        return []
    place_id = int(row["place_id"])
    chain = [*reversed(ancestor_rows(connection, place_id)), get_place_row(connection, place_id)]
    seen: set[str] = set()
    values: list[str] = []
    for place in chain:
        # Only an ACTIVE directory entry is part of a current classification; a RETIRED
        # one (which the write path refuses to leave assigned) contributes no text.
        if str(place["lifecycle"]) != "ACTIVE":
            continue
        for candidate in (
            str(place["canonical_name"]),
            *(str(alias["text"]) for alias in active_alias_rows(connection, int(place["place_id"]))),
        ):
            key = index_text(candidate)
            if key and key not in seen:
                seen.add(key)
                values.append(candidate)
    return values


def ocr_text_value(connection: sqlite3.Connection, asset_id: int) -> str:
    """The normalised OCR search copy of the *current* content revision, or ``''``."""
    row = connection.execute(_OCR_TEXT_SQL, (int(asset_id),)).fetchone()
    if row is None:
        return ""
    return str(row["search_text"])


def document_source(connection: sqlite3.Connection, asset_id: int) -> dict[str, Any] | None:
    """The authoritative text of one asset, normalised for search; ``None`` if it is gone."""
    if connection.execute(_ASSET_EXISTS_SQL, (int(asset_id),)).fetchone() is None:
        return None
    values = {
        "filename_text": index_text(" ".join(filename_values(connection, int(asset_id)))),
        "place_text": index_text(" ".join(place_values(connection, int(asset_id)))),
        "ocr_text": ocr_text_value(connection, int(asset_id)),
    }
    columns = {column: values.get(column, "") for column in TEXT_COLUMNS}
    return {
        "asset_id": int(asset_id),
        **columns,
        "source_fingerprint": fingerprint(columns),
    }


def fingerprint(columns: dict[str, str]) -> str:
    """Stable identity of one derived document, including the tokenizer generation."""
    payload = {
        "columns": {column: str(columns.get(column, "")) for column in TEXT_COLUMNS},
        "fts_generation_id": FTS_GENERATION_ID,
        "tokenizer_version": TOKENIZER_VERSION,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def document_row(connection: sqlite3.Connection, asset_id: int) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM search_documents WHERE asset_id = ?", (int(asset_id),)
    ).fetchone()


def sync_document(connection: sqlite3.Connection, asset_id: int, *, now_ms: int) -> str:
    """Write one derived document, idempotently. ``written``/``unchanged``/``removed``.

    Running it twice with unchanged authority produces no second row and no second FTS
    entry; running it after an interrupted batch is therefore always safe.
    """
    source = document_source(connection, int(asset_id))
    if source is None:
        cursor = connection.execute(
            "DELETE FROM search_documents WHERE asset_id = ?", (int(asset_id),)
        )
        return "removed" if cursor.rowcount else "unchanged"
    current = document_row(connection, int(asset_id))
    if (
        current is not None
        and str(current["source_fingerprint"]) == source["source_fingerprint"]
        and int(current["tokenizer_version"]) == int(TOKENIZER_VERSION)
        and str(current["fts_generation_id"]) == FTS_GENERATION_ID
    ):
        return "unchanged"
    connection.execute(
        "INSERT INTO search_documents(asset_id, filename_text, user_text, ocr_text, "
        "caption_text, tag_text, place_text, source_fingerprint, tokenizer_version, "
        "fts_generation_id, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(asset_id) DO UPDATE SET filename_text = excluded.filename_text, "
        "user_text = excluded.user_text, ocr_text = excluded.ocr_text, "
        "caption_text = excluded.caption_text, tag_text = excluded.tag_text, "
        "place_text = excluded.place_text, "
        "source_fingerprint = excluded.source_fingerprint, "
        "tokenizer_version = excluded.tokenizer_version, "
        "fts_generation_id = excluded.fts_generation_id, updated_at = excluded.updated_at",
        (
            int(asset_id),
            source["filename_text"],
            source["user_text"],
            source["ocr_text"],
            source["caption_text"],
            source["tag_text"],
            source["place_text"],
            source["source_fingerprint"],
            int(TOKENIZER_VERSION),
            FTS_GENERATION_ID,
            int(now_ms),
        ),
    )
    return "written"


def delete_document(connection: sqlite3.Connection, asset_id: int) -> bool:
    """Drop one derived document and its index entry."""
    cursor = connection.execute(
        "DELETE FROM search_documents WHERE asset_id = ?", (int(asset_id),)
    )
    return bool(cursor.rowcount)


def queue_depth(connection: sqlite3.Connection) -> int:
    return int(connection.execute("SELECT COUNT(*) FROM search_projection_queue").fetchone()[0])


def drain_queue(
    connection: sqlite3.Connection, *, now_ms: int, limit: int = DRAIN_BATCH_SIZE
) -> dict[str, int]:
    """Re-derive up to ``limit`` queued assets inside the caller's transaction."""
    rows = connection.execute(
        "SELECT asset_id FROM search_projection_queue ORDER BY queued_at, asset_id LIMIT ?",
        (int(limit),),
    ).fetchall()
    written = 0
    for row in rows:
        asset_id = int(row["asset_id"])
        if sync_document(connection, asset_id, now_ms=int(now_ms)) == "written":
            written += 1
        connection.execute(
            "DELETE FROM search_projection_queue WHERE asset_id = ?", (asset_id,)
        )
    return {"processed": len(rows), "written": written, "remaining": queue_depth(connection)}


@contextmanager
def _bounded_catch_up_write(catalog: Catalog) -> Iterator[sqlite3.Connection]:
    """One catch-up write whose wait for the writer lock is bounded (P4-02V).

    The bound is a *connection* setting, so it is restored in ``finally``: the maintenance
    drain of the same catalog keeps the 5 s budget its own retry policy is built on, and a
    reader that later needs the lock for something else is not silently made impatient.
    """
    connection = catalog.connection
    previous = int(connection.execute("PRAGMA busy_timeout").fetchone()[0])
    narrowed = previous > int(CATCH_UP_BUSY_BUDGET_MS)
    if narrowed:
        connection.execute(f"PRAGMA busy_timeout = {int(CATCH_UP_BUSY_BUDGET_MS)}")
    try:
        with catalog.write() as writer:
            yield writer
    finally:
        if narrowed:
            connection.execute(f"PRAGMA busy_timeout = {int(previous)}")


def catch_up(
    catalog: Catalog, *, batches: int = CATCH_UP_BATCHES, batch_size: int = DRAIN_BATCH_SIZE
) -> dict[str, int | str]:
    """Catch the projection up in bounded batches and report the lag that is left.

    Nothing is written when the queue is already empty, so a plain read stays a read.

    A batch that cannot get the single-writer lock inside ``CATCH_UP_BUSY_BUDGET_MS`` ends the
    catch-up instead of failing it (P4-02V): the refused transaction wrote nothing, the
    affected set stays durable and the caller is told the projection is ``LAGGING``.
    """
    queued = queue_depth(catalog.connection)
    if queued == 0:
        return {
            "processed": 0,
            "written": 0,
            "remaining": 0,
            "batches": 0,
            "state": "SYNCED",
            "deferred_batches": 0,
        }
    processed = written = used = deferred = 0
    remaining = queued
    for _ in range(max(1, int(batches))):
        try:
            with _bounded_catch_up_write(catalog) as connection:
                result = drain_queue(
                    connection, now_ms=catalog.now_ms(), limit=int(batch_size)
                )
        except CatalogError as error:
            # Keep the maintenance dependency local: maintenance imports scheduler
            # projections, which reach this module through the person/API package.
            from photo_search.indexing.maintenance import is_busy_rejection

            if not is_busy_rejection(error):
                raise
            # The background drain owns the write lock for the whole build (P4-02U):
            # waiting cannot win inside that window, so stop here and report the lag.
            deferred += 1
            remaining = queue_depth(catalog.connection)
            break
        processed += int(result["processed"])
        written += int(result["written"])
        remaining = int(result["remaining"])
        used += 1
        if int(result["processed"]) < int(batch_size):
            break
    return {
        "processed": processed,
        "written": written,
        "remaining": remaining,
        "batches": used,
        "state": "SYNCED" if remaining == 0 else "LAGGING",
        "deferred_batches": deferred,
    }


def rebuild_all(catalog: Catalog, *, batch_size: int = DRAIN_BATCH_SIZE) -> dict[str, int]:
    """Re-derive every asset, in bounded batches (a tokenizer generation change).

    Both halves of the projection are derived from SQLite alone, so this never re-runs OCR
    or any model, and rows that are already current are left untouched.
    """
    processed = written = batches = 0
    after = 0
    while True:
        with catalog.write() as connection:
            rows = connection.execute(
                "SELECT asset_id FROM assets WHERE asset_id > ? ORDER BY asset_id LIMIT ?",
                (int(after), int(batch_size)),
            ).fetchall()
            if not rows:
                break
            now_ms = catalog.now_ms()
            for row in rows:
                asset_id = int(row["asset_id"])
                if sync_document(connection, asset_id, now_ms=now_ms) == "written":
                    written += 1
                connection.execute(
                    "DELETE FROM search_projection_queue WHERE asset_id = ?", (asset_id,)
                )
            after = int(rows[-1]["asset_id"])
            processed += len(rows)
            batches += 1
        if len(rows) < int(batch_size):
            break
    return {"processed": processed, "written": written, "batches": batches}


def projection_summary(connection: sqlite3.Connection) -> dict[str, Any]:
    """Cheap, path-free lag facts for diagnostics and the list response."""
    documents = int(connection.execute("SELECT COUNT(*) FROM search_documents").fetchone()[0])
    stale = int(
        connection.execute(
            "SELECT COUNT(*) FROM search_documents WHERE tokenizer_version <> ? "
            "OR fts_generation_id <> ?",
            (int(TOKENIZER_VERSION), FTS_GENERATION_ID),
        ).fetchone()[0]
    )
    return {
        "documents": documents,
        "queued_assets": queue_depth(connection),
        "stale_documents": stale,
        "tokenizer_version": int(TOKENIZER_VERSION),
        "fts_generation_id": FTS_GENERATION_ID,
    }


def stale_document_asset_ids(
    connection: sqlite3.Connection, *, limit: int = 200
) -> list[int]:
    """Assets whose derived text no longer matches the authority, newest queue last.

    The queue covers every fact change by construction; this independent check exists for
    tests and diagnostics (it is deliberately bounded, never a full-table comparison).
    """
    checked = 0
    stale: list[int] = []
    for row in connection.execute(
        "SELECT asset_id FROM assets ORDER BY asset_id LIMIT ?", (int(limit),)
    ).fetchall():
        asset_id = int(row["asset_id"])
        checked += 1
        source = document_source(connection, asset_id)
        current = document_row(connection, asset_id)
        if source is None or current is None:
            stale.append(asset_id)
            continue
        if str(current["source_fingerprint"]) != source["source_fingerprint"]:
            stale.append(asset_id)
    return stale


def verify_index(connection: sqlite3.Connection) -> None:
    """Refuse an FTS index that no longer matches ``search_documents`` (SQLite's own check)."""
    connection.execute("INSERT INTO search_documents_fts(search_documents_fts) VALUES('integrity-check')")
