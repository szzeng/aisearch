"""The read half of the P2-04 search surface: one bound predicate plus the lag signal.

``text_condition`` never concatenates user input into SQL: the MATCH expression is built
by :mod:`photo_search.search.tokens` (every token quoted) and passed as a bound parameter.
The condition is an ADD on the existing asset listing, so the cursor space, the ordering
and the authoritative SQLite place filters are untouched (architecture §4.5.4).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from photo_search.search import documents as document_module
from photo_search.search.tokens import MatchQuery, build_match

__all__ = ["TEXT_CONDITION", "build_match", "search_summary", "text_condition"]

#: ``search_documents_fts`` is an external content table whose rowid *is* ``asset_id``, so
#: the predicate is a rowid lookup against the index - never a join on a coincidental id.
TEXT_CONDITION = (
    "EXISTS (SELECT 1 FROM search_documents_fts WHERE search_documents_fts MATCH ? "
    "AND search_documents_fts.rowid = {alias}.asset_id)"
)


def text_condition(match: MatchQuery, *, alias: str = "a") -> tuple[str, list[Any]]:
    """The ADD predicate over ``<alias>.asset_id`` plus its single bound parameter."""
    return TEXT_CONDITION.format(alias=str(alias)), [match.expression]


def search_summary(
    connection: sqlite3.Connection,
    match: MatchQuery,
    *,
    catch_up: dict[str, Any],
) -> dict[str, Any]:
    """The echo of one text query: what was searched, and how fresh the projection is."""
    projection = document_module.projection_summary(connection)
    summary: dict[str, Any] = {
        **projection,
        "query": match.raw,
        "normalized_query": match.normalized,
        "terms": match.term_texts(),
        "match_expression": match.expression,
        # Catch-up precedes the read snapshot. A writer may have queued more work
        # (or another worker drained it) since then; publish this snapshot's lag.
        "state": "LAGGING" if projection["queued_assets"] or projection["stale_documents"] else "SYNCED",
        "processed_assets": int(catch_up["processed"]),
    }
    return summary
