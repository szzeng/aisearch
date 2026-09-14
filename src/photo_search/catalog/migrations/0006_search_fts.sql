-- P2-04 search projection: the derived search document of every asset, the external
-- content FTS5 index over it, and the durable queue of assets whose derived text is
-- stale. Additive to 0001..0005; no frozen table, column, key or trigger is touched.
--
-- Architecture §4.3: ``search_documents`` is a normal content table keyed by
-- ``asset_id``; the FTS5 table is an *external content* table whose rowid is exactly
-- ``asset_id`` (never a different table's coincidental rowid), and it is kept in sync
-- inside the caller's transaction by the triggers below. The columns hold the
-- normalised search text ("统一分词后的检索文本"): the original spellings stay in their
-- authoritative tables (``file_locations.relative_path``, ``places``/``place_aliases``)
-- and can be re-derived at any time, so a tokenizer change only rebuilds this table.
--
-- P2-04 only fills ``filename_text`` (basenames of PRESENT locations) and
-- ``place_text`` (the current effective place). ``user_text``/``ocr_text``/
-- ``caption_text``/``tag_text`` are reserved for P2-05/P3 and are written as '' - never
-- with fabricated content.

CREATE TABLE search_documents (
    asset_id           INTEGER PRIMARY KEY REFERENCES assets(asset_id) ON DELETE CASCADE,
    filename_text      TEXT NOT NULL DEFAULT '',
    user_text          TEXT NOT NULL DEFAULT '',
    ocr_text           TEXT NOT NULL DEFAULT '',
    caption_text       TEXT NOT NULL DEFAULT '',
    tag_text           TEXT NOT NULL DEFAULT '',
    place_text         TEXT NOT NULL DEFAULT '',
    --: sha256 of the canonical JSON of the six text columns plus the tokenizer version:
    --: the projection is stale exactly when it differs from a freshly computed source.
    source_fingerprint TEXT NOT NULL
        CHECK(length(source_fingerprint) = 64
              AND source_fingerprint NOT GLOB '*[^0-9a-f]*'),
    tokenizer_version  INTEGER NOT NULL CHECK(tokenizer_version >= 1),
    fts_generation_id  TEXT NOT NULL CHECK(fts_generation_id <> ''),
    updated_at         INTEGER NOT NULL CHECK(updated_at >= 0)
);

CREATE INDEX idx_search_documents_tokenizer
    ON search_documents(tokenizer_version, fts_generation_id);

CREATE VIRTUAL TABLE search_documents_fts USING fts5(
    filename_text, user_text, ocr_text, caption_text, tag_text, place_text,
    content = 'search_documents',
    content_rowid = 'asset_id',
    tokenize = 'unicode61'
);

-- The index is maintained explicitly, with the rowid spelled out on both sides, inside
-- the same transaction as the content row (including cascaded deletes).
CREATE TRIGGER trg_search_documents_fts_insert
AFTER INSERT ON search_documents
BEGIN
    INSERT INTO search_documents_fts(
        rowid, filename_text, user_text, ocr_text, caption_text, tag_text, place_text)
    VALUES (new.asset_id, new.filename_text, new.user_text, new.ocr_text,
            new.caption_text, new.tag_text, new.place_text);
END;

CREATE TRIGGER trg_search_documents_fts_delete
AFTER DELETE ON search_documents
BEGIN
    INSERT INTO search_documents_fts(
        search_documents_fts, rowid, filename_text, user_text, ocr_text, caption_text,
        tag_text, place_text)
    VALUES ('delete', old.asset_id, old.filename_text, old.user_text, old.ocr_text,
            old.caption_text, old.tag_text, old.place_text);
END;

CREATE TRIGGER trg_search_documents_fts_update
AFTER UPDATE ON search_documents
BEGIN
    INSERT INTO search_documents_fts(
        search_documents_fts, rowid, filename_text, user_text, ocr_text, caption_text,
        tag_text, place_text)
    VALUES ('delete', old.asset_id, old.filename_text, old.user_text, old.ocr_text,
            old.caption_text, old.tag_text, old.place_text);
    INSERT INTO search_documents_fts(
        rowid, filename_text, user_text, ocr_text, caption_text, tag_text, place_text)
    VALUES (new.asset_id, new.filename_text, new.user_text, new.ocr_text,
            new.caption_text, new.tag_text, new.place_text);
END;

-- The durable affected set (architecture §4.5.3): a fact change records which assets
-- must be re-derived, in the same short transaction that changed the fact. Deriving the
-- text itself happens later, in bounded batches, so no authority transaction ever
-- rewrites the whole library.
CREATE TABLE search_projection_queue (
    asset_id  INTEGER PRIMARY KEY REFERENCES assets(asset_id) ON DELETE CASCADE,
    cause     TEXT NOT NULL CHECK(cause IN (
        'FILENAME_CHANGED', 'PLACE_ASSIGNMENT_CHANGED', 'PLACE_NAME_CHANGED',
        'PLACE_ALIAS_CHANGED')),
    queued_at INTEGER NOT NULL CHECK(queued_at >= 0)
);

CREATE INDEX idx_search_projection_queue_age
    ON search_projection_queue(queued_at, asset_id);

-- Filename text changed: a location appeared, moved, was renamed, was unbound or was
-- marked MISSING. ``asset_id`` is nullable on ``file_locations``; an unbound location
-- carries no text and is not queued.
CREATE TRIGGER trg_file_locations_search_queue_insert
AFTER INSERT ON file_locations
WHEN new.asset_id IS NOT NULL
BEGIN
    INSERT INTO search_projection_queue(asset_id, cause, queued_at)
    VALUES (new.asset_id, 'FILENAME_CHANGED',
            CAST(strftime('%s', 'now') AS INTEGER) * 1000)
    ON CONFLICT(asset_id) DO UPDATE
        SET cause = excluded.cause, queued_at = excluded.queued_at;
END;

CREATE TRIGGER trg_file_locations_search_queue_update
AFTER UPDATE OF relative_path, availability, asset_id ON file_locations
WHEN (old.relative_path IS NOT new.relative_path
      OR old.availability IS NOT new.availability
      OR old.asset_id IS NOT new.asset_id)
BEGIN
    INSERT INTO search_projection_queue(asset_id, cause, queued_at)
    SELECT new.asset_id, 'FILENAME_CHANGED', CAST(strftime('%s', 'now') AS INTEGER) * 1000
    WHERE new.asset_id IS NOT NULL
    ON CONFLICT(asset_id) DO UPDATE
        SET cause = excluded.cause, queued_at = excluded.queued_at;
    INSERT INTO search_projection_queue(asset_id, cause, queued_at)
    SELECT old.asset_id, 'FILENAME_CHANGED', CAST(strftime('%s', 'now') AS INTEGER) * 1000
    WHERE old.asset_id IS NOT NULL AND old.asset_id IS NOT new.asset_id
    ON CONFLICT(asset_id) DO UPDATE
        SET cause = excluded.cause, queued_at = excluded.queued_at;
END;

CREATE TRIGGER trg_file_locations_search_queue_delete
AFTER DELETE ON file_locations
WHEN old.asset_id IS NOT NULL
BEGIN
    INSERT INTO search_projection_queue(asset_id, cause, queued_at)
    SELECT old.asset_id, 'FILENAME_CHANGED',
           CAST(strftime('%s', 'now') AS INTEGER) * 1000
    WHERE EXISTS (SELECT 1 FROM assets WHERE asset_id = old.asset_id)
    ON CONFLICT(asset_id) DO UPDATE
        SET cause = excluded.cause, queued_at = excluded.queued_at;
END;

-- Effective place changed: assign / unassign / restore_auto / batch / undo all upsert
-- the single control row of one asset.
CREATE TRIGGER trg_asset_place_assignments_search_queue_insert
AFTER INSERT ON asset_place_assignments
BEGIN
    INSERT INTO search_projection_queue(asset_id, cause, queued_at)
    VALUES (new.asset_id, 'PLACE_ASSIGNMENT_CHANGED',
            CAST(strftime('%s', 'now') AS INTEGER) * 1000)
    ON CONFLICT(asset_id) DO UPDATE
        SET cause = excluded.cause, queued_at = excluded.queued_at;
END;

CREATE TRIGGER trg_asset_place_assignments_search_queue_update
AFTER UPDATE ON asset_place_assignments
WHEN (old.place_id IS NOT new.place_id
      OR old.mode IS NOT new.mode
      OR old.record_state IS NOT new.record_state)
BEGIN
    INSERT INTO search_projection_queue(asset_id, cause, queued_at)
    VALUES (new.asset_id, 'PLACE_ASSIGNMENT_CHANGED',
            CAST(strftime('%s', 'now') AS INTEGER) * 1000)
    ON CONFLICT(asset_id) DO UPDATE
        SET cause = excluded.cause, queued_at = excluded.queued_at;
END;

-- A rename (or a retirement) changes ``place_text`` for every asset whose effective place
-- is that place *or a descendant of it*, because the text carries the ancestor chain.
CREATE TRIGGER trg_places_search_queue_change
AFTER UPDATE ON places
WHEN (old.canonical_name IS NOT new.canonical_name
      OR old.lifecycle IS NOT new.lifecycle)
BEGIN
    INSERT INTO search_projection_queue(asset_id, cause, queued_at)
    SELECT ap.asset_id, 'PLACE_NAME_CHANGED',
           CAST(strftime('%s', 'now') AS INTEGER) * 1000
    FROM asset_place_assignments AS ap
    WHERE ap.mode = 'MANUAL_ASSIGNED'
      AND ap.place_id IN (
          WITH RECURSIVE subtree(place_id) AS (
              SELECT new.place_id
              UNION
              SELECT p.place_id FROM places AS p
              JOIN subtree AS s ON p.parent_place_id = s.place_id
          )
          SELECT place_id FROM subtree)
    ON CONFLICT(asset_id) DO UPDATE
        SET cause = excluded.cause, queued_at = excluded.queued_at;
END;

CREATE TRIGGER trg_place_aliases_search_queue_insert
AFTER INSERT ON place_aliases
WHEN new.lifecycle = 'ACTIVE'
BEGIN
    INSERT INTO search_projection_queue(asset_id, cause, queued_at)
    SELECT ap.asset_id, 'PLACE_ALIAS_CHANGED',
           CAST(strftime('%s', 'now') AS INTEGER) * 1000
    FROM asset_place_assignments AS ap
    WHERE ap.mode = 'MANUAL_ASSIGNED'
      AND ap.place_id IN (
          WITH RECURSIVE subtree(place_id) AS (
              SELECT new.place_id
              UNION
              SELECT p.place_id FROM places AS p
              JOIN subtree AS s ON p.parent_place_id = s.place_id
          )
          SELECT place_id FROM subtree)
    ON CONFLICT(asset_id) DO UPDATE
        SET cause = excluded.cause, queued_at = excluded.queued_at;
END;

CREATE TRIGGER trg_place_aliases_search_queue_update
AFTER UPDATE OF text, language, lifecycle ON place_aliases
WHEN (old.text IS NOT new.text
      OR old.language IS NOT new.language
      OR old.lifecycle IS NOT new.lifecycle)
BEGIN
    INSERT INTO search_projection_queue(asset_id, cause, queued_at)
    SELECT ap.asset_id, 'PLACE_ALIAS_CHANGED',
           CAST(strftime('%s', 'now') AS INTEGER) * 1000
    FROM asset_place_assignments AS ap
    WHERE ap.mode = 'MANUAL_ASSIGNED'
      AND ap.place_id IN (
          WITH RECURSIVE subtree(place_id) AS (
              SELECT new.place_id
              UNION
              SELECT p.place_id FROM places AS p
              JOIN subtree AS s ON p.parent_place_id = s.place_id
          )
          SELECT place_id FROM subtree)
    ON CONFLICT(asset_id) DO UPDATE
        SET cause = excluded.cause, queued_at = excluded.queued_at;
END;
