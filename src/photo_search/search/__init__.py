"""P2-04 search projection: deterministic tokenisation, the derived document, MATCH.

The frozen protocol is the architecture §4.3/§4.5.3/§4.5.4/§9.4-9.5 plus ADR-0005 §10/§11.
Nothing here decides place semantics: SQLite stays authoritative for every filter, and a
text hit never proves where a photo was taken.
"""

from photo_search.search.documents import (
    CATCH_UP_BATCHES,
    DRAIN_BATCH_SIZE,
    TEXT_COLUMNS,
    basename,
    catch_up,
    delete_document,
    document_row,
    document_source,
    drain_queue,
    filename_values,
    place_values,
    projection_summary,
    queue_depth,
    rebuild_all,
    sync_document,
    verify_index,
)
from photo_search.search.query import TEXT_CONDITION, build_match, search_summary, text_condition
from photo_search.search.tokens import (
    FTS_GENERATION_ID,
    TOKENIZER_VERSION,
    MatchQuery,
    Term,
    index_text,
    scan_tokens,
)

__all__ = [
    "CATCH_UP_BATCHES",
    "DRAIN_BATCH_SIZE",
    "FTS_GENERATION_ID",
    "TEXT_COLUMNS",
    "TEXT_CONDITION",
    "TOKENIZER_VERSION",
    "MatchQuery",
    "Term",
    "basename",
    "build_match",
    "catch_up",
    "delete_document",
    "document_row",
    "document_source",
    "drain_queue",
    "filename_values",
    "index_text",
    "place_values",
    "projection_summary",
    "queue_depth",
    "rebuild_all",
    "scan_tokens",
    "search_summary",
    "sync_document",
    "text_condition",
    "verify_index",
]
