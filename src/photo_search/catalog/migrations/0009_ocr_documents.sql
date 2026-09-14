-- P3-01 OCR layer: the current OCR document of every asset, its per-region metadata, and
-- the fifth reprojection cause an OCR text change needs. Additive to 0001..0008; no frozen
-- table, column, key, trigger or index of the earlier migrations is redefined - the only
-- table this script replaces is ``search_projection_queue``, whose 0006 ``CHECK`` list is a
-- closed set of four causes and would otherwise refuse a truthful OCR cause.
--
-- Column ownership (architecture §4.3, §8.2 and the P3-01 invariants):
--
-- * ``asset_ocr_text`` is the *authority* of the current OCR document. It carries the
--   normalised search copy (``search_text``: exactly what ``search_documents.ocr_text``
--   is derived from), the content revision it was produced for, the processor/weight
--   identity and the region statistics. The original, uncorrected text and the exact
--   polygons live only in the immutable structured artifact of the job, so this table
--   never becomes a second copy of the pixel-accurate result.
-- * ``asset_ocr_regions`` carries the *region metadata* in reading order: the region
--   index, its axis-aligned box and its confidence. No text and no polygon is stored
--   here.
-- * a row of ``asset_ocr_text`` whose ``input_revision`` no longer equals
--   ``assets.sha256`` is history, not a current fact: the derived projection ignores it
--   (see ``photo_search.search.documents``), which is what keeps text recognised before a
--   content change from being attached to the new content.

CREATE TABLE asset_ocr_text (
    asset_id          INTEGER PRIMARY KEY REFERENCES assets(asset_id) ON DELETE RESTRICT,
    ocr_document_id   TEXT NOT NULL UNIQUE CHECK(ocr_document_id <> ''),
    --: The content revision this recognition was produced for (``assets.sha256``).
    input_revision    TEXT NOT NULL
        CHECK(length(input_revision) = 64 AND input_revision NOT GLOB '*[^0-9a-f]*'),
    location_id       INTEGER REFERENCES file_locations(location_id) ON DELETE RESTRICT,
    --: Monotone per asset: a deterministic re-execution of the same content bumps it, so a
    --: replayed submission never looks like a second document.
    text_revision     INTEGER NOT NULL CHECK(text_revision >= 1),
    processor_id      TEXT NOT NULL CHECK(processor_id <> ''),
    spec_revision     TEXT NOT NULL CHECK(spec_revision <> ''),
    model_fingerprint TEXT NOT NULL
        CHECK(length(model_fingerprint) = 64
              AND model_fingerprint NOT GLOB '*[^0-9a-f]*'),
    artifact_id       TEXT NOT NULL CHECK(artifact_id <> ''),
    region_count      INTEGER NOT NULL CHECK(region_count >= 0),
    min_confidence    REAL
        CHECK(min_confidence IS NULL OR (min_confidence >= 0.0 AND min_confidence <= 1.0)),
    mean_confidence   REAL
        CHECK(mean_confidence IS NULL OR (mean_confidence >= 0.0 AND mean_confidence <= 1.0)),
    max_confidence    REAL
        CHECK(max_confidence IS NULL OR (max_confidence >= 0.0 AND max_confidence <= 1.0)),
    --: The normalised search copy; the ONLY OCR text this build stores in SQLite.
    search_text       TEXT NOT NULL DEFAULT '',
    tokenizer_version INTEGER NOT NULL CHECK(tokenizer_version >= 1),
    created_at        INTEGER NOT NULL CHECK(created_at >= 0),
    updated_at        INTEGER NOT NULL CHECK(updated_at >= 0),
    CHECK(region_count = 0
          OR (min_confidence IS NOT NULL AND mean_confidence IS NOT NULL
              AND max_confidence IS NOT NULL)),
    CHECK(min_confidence IS NULL
          OR (min_confidence <= mean_confidence AND mean_confidence <= max_confidence))
);

CREATE TABLE asset_ocr_regions (
    asset_id      INTEGER NOT NULL REFERENCES assets(asset_id) ON DELETE RESTRICT,
    region_index  INTEGER NOT NULL CHECK(region_index >= 0),
    text_revision INTEGER NOT NULL CHECK(text_revision >= 1),
    box_json      TEXT NOT NULL CHECK(json_valid(box_json)),
    confidence    REAL NOT NULL CHECK(confidence >= 0.0 AND confidence <= 1.0),
    PRIMARY KEY (asset_id, region_index)
);

-- 0006 froze ``search_projection_queue`` with a closed four-value cause list. An OCR text
-- change is a fifth, genuinely different reason to re-derive one asset's document, and
-- reusing a place/filename cause would make the queue lie. The table is therefore rebuilt
-- with the same shape plus one allowed cause. The triggers of 0006 stay untouched and keep
-- working: they reference the table by name and the replacement carries the same name, so
-- no trigger body has to be rewritten (and none is duplicated here).
CREATE TABLE search_projection_queue_snapshot (
    asset_id  INTEGER NOT NULL,
    cause     TEXT NOT NULL,
    queued_at INTEGER NOT NULL
);

INSERT INTO search_projection_queue_snapshot(asset_id, cause, queued_at)
    SELECT asset_id, cause, queued_at FROM search_projection_queue;

DROP TABLE search_projection_queue;

CREATE TABLE search_projection_queue (
    asset_id  INTEGER PRIMARY KEY REFERENCES assets(asset_id) ON DELETE CASCADE,
    cause     TEXT NOT NULL CHECK(cause IN (
        'FILENAME_CHANGED', 'PLACE_ASSIGNMENT_CHANGED', 'PLACE_NAME_CHANGED',
        'PLACE_ALIAS_CHANGED', 'OCR_TEXT_CHANGED')),
    queued_at INTEGER NOT NULL CHECK(queued_at >= 0)
);

CREATE INDEX idx_search_projection_queue_age
    ON search_projection_queue(queued_at, asset_id);

INSERT INTO search_projection_queue(asset_id, cause, queued_at)
    SELECT asset_id, cause, queued_at FROM search_projection_queue_snapshot
    ORDER BY queued_at, asset_id;

DROP TABLE search_projection_queue_snapshot;

-- A changed OCR document (new text, new content revision or a new tokenizer generation)
-- joins the durable affected set in the same short transaction that wrote the document,
-- exactly like the P2-04 triggers do for filenames and places.
CREATE TRIGGER trg_asset_ocr_text_search_queue_insert
AFTER INSERT ON asset_ocr_text
BEGIN
    INSERT INTO search_projection_queue(asset_id, cause, queued_at)
    VALUES (new.asset_id, 'OCR_TEXT_CHANGED',
            CAST(strftime('%s', 'now') AS INTEGER) * 1000)
    ON CONFLICT(asset_id) DO UPDATE
        SET cause = excluded.cause, queued_at = excluded.queued_at;
END;

CREATE TRIGGER trg_asset_ocr_text_search_queue_update
AFTER UPDATE OF search_text, input_revision, tokenizer_version ON asset_ocr_text
WHEN (old.search_text IS NOT new.search_text
      OR old.input_revision IS NOT new.input_revision
      OR old.tokenizer_version IS NOT new.tokenizer_version)
BEGIN
    INSERT INTO search_projection_queue(asset_id, cause, queued_at)
    VALUES (new.asset_id, 'OCR_TEXT_CHANGED',
            CAST(strftime('%s', 'now') AS INTEGER) * 1000)
    ON CONFLICT(asset_id) DO UPDATE
        SET cause = excluded.cause, queued_at = excluded.queued_at;
END;
