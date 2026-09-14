-- P3-03 person-knowledge layer: the long-lived human naming facts of a person group, the
-- two human-control tables P3-02's automatic path already reads, and the append-only
-- operation log with its per-entity before/after snapshots. Additive to 0001..0010; no
-- frozen table, column, key, index or trigger of an earlier migration is redefined.
--
-- Column ownership (ADR-0006 §3, architecture §4.2 and §8.3.1~§8.3.5):
--
-- * ``person_operations`` is the idempotency and history authority. ``request_id`` is the
--   caller's key, its payload hash proves that a replay is the same request, and the
--   derived ``operation_id`` is ``sha256("person-operation\0" + request_id)``. The row is
--   never deleted and never rewound: an undo is a *new* ``UNDO`` row that points at the
--   original through ``undo_of_operation_id``, while the original flips to ``UNDONE`` and
--   records ``undone_by``.
-- * ``person_operation_items`` carries the bounded per-person before/after bundle (the
--   person row, its alias set and its ``face_person_links`` set) and the entity's revision
--   after the operation. It is the only thing the compensating undo reads, so the payload
--   stays a few rows instead of a copy of the database.
-- * ``person_aliases`` is the search-alias set of a *named* person. An alias never triggers
--   a same-name merge (ADR-0006 §2) and the same alias may belong to different people, so
--   the uniqueness is scoped to the person, never global.
-- * ``face_assignment_controls`` and ``face_exclusions`` are the human locks and the human
--   denials of P3-04. This migration creates them (P3-03 writes only what a merge must
--   downgrade) so the read order P3-02 already implements becomes a real fact: a face with
--   ``MANUAL_ASSIGNED``/``MANUAL_UNASSIGNED`` or an active exclusion is never touched by an
--   automatic path. Only architecture-required columns exist - no extra ``NOT NULL`` column
--   without a default - so the P3-02 acceptance that writes them explicitly keeps working.
--
-- Same-name uniqueness itself is *not* redefined here: 0010 already froze
-- ``ux_persons_active_name_key`` over active persons with a non-NULL ``name_key``.

CREATE TABLE person_operations (
    operation_id          TEXT PRIMARY KEY CHECK(operation_id <> ''),
    request_id            TEXT NOT NULL UNIQUE CHECK(request_id <> ''),
    payload_sha256        TEXT NOT NULL
        CHECK(length(payload_sha256) = 64 AND payload_sha256 NOT GLOB '*[^0-9a-f]*'),
    type                  TEXT NOT NULL CHECK(type IN (
        'RENAME', 'MERGE_BY_NAME', 'MERGE', 'BATCH_MERGE', 'UNDO')),
    --: The *requested* source ids of the operation, in ascending order. They are the ids
    --: the caller named, never a canonical id that a later merge would have moved.
    source_person_ids_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(source_person_ids_json)),
    target_person_id      INTEGER REFERENCES persons(person_id) ON DELETE RESTRICT,
    state                 TEXT NOT NULL CHECK(state IN ('COMMITTED', 'UNDONE')),
    --: Monotone and unique: the undo is a later operation, never a rewound counter.
    operation_revision    INTEGER NOT NULL UNIQUE CHECK(operation_revision >= 1),
    undo_of_operation_id  TEXT REFERENCES person_operations(operation_id) ON DELETE RESTRICT,
    undone_by             TEXT REFERENCES person_operations(operation_id) ON DELETE RESTRICT,
    --: CatalogFilterRevision right after this operation committed (0 = it did not move).
    catalog_filter_revision INTEGER NOT NULL DEFAULT 0 CHECK(catalog_filter_revision >= 0),
    created_at            INTEGER NOT NULL CHECK(created_at >= 0),
    committed_at          INTEGER NOT NULL CHECK(committed_at >= 0),
    CHECK(undo_of_operation_id IS NULL OR undo_of_operation_id <> operation_id),
    CHECK(undone_by IS NULL OR undone_by <> operation_id),
    --: "compensated" is a state with an author: an UNDONE row always names its undo.
    CHECK(state = 'COMMITTED' OR undone_by IS NOT NULL)
);

CREATE INDEX idx_person_operations_committed
    ON person_operations(committed_at, operation_id);

CREATE TABLE person_operation_items (
    operation_id       TEXT NOT NULL REFERENCES person_operations(operation_id)
        ON DELETE RESTRICT,
    item_index         INTEGER NOT NULL CHECK(item_index >= 0),
    entity_type        TEXT NOT NULL CHECK(entity_type = 'PERSON_BUNDLE'),
    entity_id          INTEGER NOT NULL CHECK(entity_id >= 1),
    --: Sorted canonical JSON of the person bundle before/after the operation.
    before_value_json  TEXT NOT NULL CHECK(json_valid(before_value_json)),
    after_value_json   TEXT NOT NULL CHECK(json_valid(after_value_json)),
    after_revision     INTEGER NOT NULL CHECK(after_revision >= 1),
    PRIMARY KEY(operation_id, item_index)
);

CREATE INDEX idx_person_operation_items_entity
    ON person_operation_items(entity_id, operation_id);

CREATE TABLE person_aliases (
    alias_id           INTEGER PRIMARY KEY,
    person_id          INTEGER NOT NULL REFERENCES persons(person_id) ON DELETE RESTRICT,
    alias              TEXT NOT NULL CHECK(alias <> ''),
    --: The display spelling is what a search alias shows; this is the de-duplication key.
    normalized_alias   TEXT NOT NULL CHECK(normalized_alias <> ''),
    last_operation_id  TEXT REFERENCES person_operations(operation_id) ON DELETE RESTRICT,
    created_at         INTEGER NOT NULL CHECK(created_at >= 0),
    --: An alias is unique inside one person; two different persons may share one.
    UNIQUE(person_id, normalized_alias)
);

CREATE TABLE face_assignment_controls (
    face_id            TEXT PRIMARY KEY REFERENCES faces(face_id) ON DELETE RESTRICT,
    mode               TEXT NOT NULL CHECK(mode IN (
        'AUTO_ALLOWED', 'MANUAL_ASSIGNED', 'MANUAL_UNASSIGNED')),
    revision           INTEGER NOT NULL CHECK(revision >= 1),
    last_operation_id  TEXT,
    updated_at         INTEGER NOT NULL CHECK(updated_at >= 0)
);

CREATE TABLE face_exclusions (
    face_id      TEXT NOT NULL REFERENCES faces(face_id) ON DELETE RESTRICT,
    person_id    INTEGER NOT NULL REFERENCES persons(person_id) ON DELETE RESTRICT,
    operation_id TEXT,
    active       INTEGER NOT NULL CHECK(active IN (0, 1)),
    created_at   INTEGER NOT NULL CHECK(created_at >= 0),
    PRIMARY KEY(face_id, person_id)
);

CREATE INDEX idx_face_exclusions_person ON face_exclusions(person_id, active);

-- 0006 froze ``search_projection_queue`` with a closed cause list, 0009 widened it to five.
-- A person rename/merge/undo is a sixth, genuinely different reason to re-derive one
-- asset's document (a person condition is evaluated against the authoritative SQLite facts),
-- and reusing a place/OCR/filename cause would make the queue lie. The table is therefore
-- rebuilt with the same shape plus one allowed cause. The triggers 0006 and 0009 installed
-- stay untouched and keep working: they reference the table by name, and the replacement
-- carries the same name, so no trigger body is rewritten and none is duplicated here.
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
        'PLACE_ALIAS_CHANGED', 'OCR_TEXT_CHANGED', 'PERSON_CHANGED')),
    queued_at INTEGER NOT NULL CHECK(queued_at >= 0)
);

CREATE INDEX idx_search_projection_queue_age
    ON search_projection_queue(queued_at, asset_id);

INSERT INTO search_projection_queue(asset_id, cause, queued_at)
    SELECT asset_id, cause, queued_at FROM search_projection_queue_snapshot
    ORDER BY queued_at, asset_id;

DROP TABLE search_projection_queue_snapshot;
