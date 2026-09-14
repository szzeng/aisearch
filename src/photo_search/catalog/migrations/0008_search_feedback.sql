-- P2-06B explicit relevance feedback (architecture §9.2/§12.4 item 12, ADR-0003 §8).
-- Additive to 0001..0007; no frozen table, column, key or trigger is touched.
--
-- The table is an append-only audit log of *what the user said*, not a ranking input:
--
--   * ``request_id`` is the idempotency key. The API's ``POST /search/feedback`` replays the
--     recorded answer for a repeated key with an identical payload and refuses a repeated
--     key with a different payload (``FEEDBACK_IDEMPOTENCY_CONFLICT``) - a UNIQUE index is
--     what makes "same key means same fact" enforceable instead of a convention.
--   * ``asset_id`` is a real asset (ON DELETE RESTRICT): feedback can never point at a row
--     that no longer exists, and it can never create an asset.
--   * ``relevance`` is a closed two-value enum; a half-spelled verdict must fail here rather
--     than be interpreted later.
--   * ``note`` is the only free text and is bounded, so the audit log cannot become a place
--     where arbitrary personal content accumulates without limit.
--   * ``search_session_id`` is the in-process session the verdict belongs to. Search sessions
--     live in memory (architecture §9.5), so this is deliberately *not* a foreign key: the
--     audit row outlives the session and says which ranking the user was looking at.
--
-- Nothing in this migration touches ``catalog_filter_revision``, ``suggestion_revision``,
-- ``search_documents``, ``index_entries`` or any vector row: marking a photo
-- NOT_RELEVANT does not hide it, re-classify it or re-rank anything (that would be a new
-- product decision, not a feedback record).

CREATE TABLE search_feedback (
    feedback_id       INTEGER PRIMARY KEY,
    request_id        TEXT NOT NULL UNIQUE CHECK(
        request_id <> '' AND length(request_id) <= 64),
    search_session_id TEXT NOT NULL CHECK(
        search_session_id <> '' AND length(search_session_id) <= 128),
    asset_id          INTEGER NOT NULL
        REFERENCES assets(asset_id) ON DELETE RESTRICT,
    rank              INTEGER NOT NULL CHECK(rank >= 1),
    relevance         TEXT NOT NULL CHECK(relevance IN ('RELEVANT', 'NOT_RELEVANT')),
    note              TEXT CHECK(note IS NULL OR length(note) <= 500),
    created_at        INTEGER NOT NULL CHECK(created_at >= 0)
);

-- Feedback is read back per search session (the audit view of one ranking). The global
-- keyset page walks the primary key, so no second index is needed for it.
CREATE INDEX idx_search_feedback_session
    ON search_feedback(search_session_id, feedback_id);

-- An audit chain that can be edited is not an audit chain: the only legal statements
-- against ``search_feedback`` are INSERT and SELECT. A correction is a new row with its own
-- ``request_id``, and the history of what was said stays readable.
CREATE TRIGGER trg_search_feedback_append_only_update
BEFORE UPDATE ON search_feedback
BEGIN
    SELECT RAISE(ABORT, 'search_feedback rows are append-only; record a new request_id');
END;

CREATE TRIGGER trg_search_feedback_append_only_delete
BEFORE DELETE ON search_feedback
BEGIN
    SELECT RAISE(ABORT, 'search_feedback rows are append-only; they are never deleted');
END;
