-- P3-04B face-correction layer: widen the *closed* CHECK vocabulary of the one person
-- operation log so a human can correct a single ``face_id``.
--
-- No new fact table is needed. ADR-0007 §10 records why this migration exists anyway:
-- 0011 wrote ``person_operations.type`` and ``person_operation_items.entity_type`` as
-- closed ``CHECK`` lists, and ADR-0007 §2 adds four face-level operation types plus the
-- ``FACE_CONTROL`` item shape (``entity_key = face_id`` with ``entity_id IS NULL``). A
-- closed CHECK cannot be widened in place, so those two tables are rebuilt. Everything
-- else - ``persons``, ``face_person_links``, ``face_assignment_controls``,
-- ``face_exclusions``, ``person_aliases`` and ``search_projection_queue`` - keeps its
-- 0010/0011 shape, because the human control facts already had the columns they need.
--
-- Rebuild rules this script has to obey (all four are exercised by the migration suite):
--
-- * every existing row of the two tables is copied over column by column, and the copy is
--   proven lossless - including the ``UNDO`` links between operations and the
--   ``person_aliases.last_operation_id`` references into the log;
-- * ``foreign_keys`` is ON and cannot be turned off inside a transaction, and SQLite
--   refuses to drop a parent table while a *surviving* child row still references it
--   (deferred or not - the deferred counter is not re-evaluated at COMMIT, so a temporary
--   violation fails the commit even when the final state is consistent). The parent is
--   therefore dropped only after its two referencing children are out of the way: the
--   item table is rebuilt anyway, and the alias references are parked in a backup table
--   and written back verbatim once the log exists again;
-- * ``PRAGMA defer_foreign_keys = ON`` only makes the *order* of the rows inside one
--   ``INSERT ... SELECT`` irrelevant (an ``UNDO`` row may name an earlier operation);
-- * the whole script runs inside the single migration transaction (ADR-0002 §7), so an
--   interrupted 0012 rolls back to a complete, untouched v11 database.
--
-- `search_projection_queue` is deliberately *not* rebuilt: ADR-0007 §8 keeps
-- ``PERSON_CHANGED`` as the cause of every person/face authority change and adds no cause.

PRAGMA defer_foreign_keys = ON;

-- (1) Park the alias references and the full log, then take the two children out of the
--     way so the parent table can be dropped with no surviving dependant row.
CREATE TABLE person_alias_operation_refs_backup AS
    SELECT alias_id, last_operation_id FROM person_aliases
    WHERE last_operation_id IS NOT NULL;

UPDATE person_aliases SET last_operation_id = NULL
    WHERE last_operation_id IS NOT NULL;

CREATE TABLE person_operations_backup AS SELECT * FROM person_operations;
CREATE TABLE person_operation_items_backup AS SELECT * FROM person_operation_items;

DROP TABLE person_operation_items;
DROP TABLE person_operations;

-- (2) The rebuilt operation log: the frozen 0011 shape plus the ADR-0007 §2 vocabulary.
CREATE TABLE person_operations (
    operation_id          TEXT PRIMARY KEY CHECK(operation_id <> ''),
    request_id            TEXT NOT NULL UNIQUE CHECK(request_id <> ''),
    payload_sha256        TEXT NOT NULL
        CHECK(length(payload_sha256) = 64 AND payload_sha256 NOT GLOB '*[^0-9a-f]*'),
    type                  TEXT NOT NULL CHECK(type IN (
        'RENAME', 'MERGE_BY_NAME', 'MERGE', 'BATCH_MERGE', 'UNDO',
        'FACE_ASSIGN', 'FACE_UNASSIGN', 'FACE_RESTORE_AUTO', 'BATCH_FACE_EDIT')),
    source_person_ids_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(source_person_ids_json)),
    target_person_id      INTEGER REFERENCES persons(person_id) ON DELETE RESTRICT,
    state                 TEXT NOT NULL CHECK(state IN ('COMMITTED', 'UNDONE')),
    operation_revision    INTEGER NOT NULL UNIQUE CHECK(operation_revision >= 1),
    undo_of_operation_id  TEXT REFERENCES person_operations(operation_id) ON DELETE RESTRICT,
    undone_by             TEXT REFERENCES person_operations(operation_id) ON DELETE RESTRICT,
    catalog_filter_revision INTEGER NOT NULL DEFAULT 0 CHECK(catalog_filter_revision >= 0),
    created_at            INTEGER NOT NULL CHECK(created_at >= 0),
    committed_at          INTEGER NOT NULL CHECK(committed_at >= 0),
    CHECK(undo_of_operation_id IS NULL OR undo_of_operation_id <> operation_id),
    CHECK(undone_by IS NULL OR undone_by <> operation_id),
    CHECK(state = 'COMMITTED' OR undone_by IS NOT NULL)
);

-- (3) The rebuilt item table. The two ``entity`` shapes stay mutually exclusive, so a
--     person bundle can never carry a ``face_id`` and a face control can never carry an
--     integer person-bundle id. The P3-03 revision floor is kept for PERSON_BUNDLE rows;
--     a FACE_CONTROL row may legitimately record revision 0 (no control row yet).
CREATE TABLE person_operation_items (
    operation_id       TEXT NOT NULL REFERENCES person_operations(operation_id)
        ON DELETE RESTRICT,
    item_index         INTEGER NOT NULL CHECK(item_index >= 0),
    entity_type        TEXT NOT NULL CHECK(entity_type IN ('PERSON_BUNDLE', 'FACE_CONTROL')),
    entity_id          INTEGER CHECK(entity_id IS NULL OR entity_id >= 1),
    entity_key         TEXT CHECK(entity_key IS NULL OR entity_key <> ''),
    before_value_json  TEXT NOT NULL CHECK(json_valid(before_value_json)),
    after_value_json   TEXT NOT NULL CHECK(json_valid(after_value_json)),
    --: Literal value ``-1`` marks "not applicable" only for FACE_CONTROL; every real
    --: revision is a non-negative integer, and a PERSON_BUNDLE item keeps the P3-03 floor.
    after_revision     INTEGER NOT NULL CHECK(after_revision >= 0
        AND (entity_type <> 'PERSON_BUNDLE' OR after_revision >= 1)),
    PRIMARY KEY(operation_id, item_index),
    CHECK((entity_type = 'PERSON_BUNDLE') = (entity_id IS NOT NULL)),
    CHECK((entity_type = 'FACE_CONTROL') = (entity_key IS NOT NULL))
);

-- (4) Write every historical row back unchanged. The column lists are explicit so a
--     future column addition cannot silently reorder this copy.
INSERT INTO person_operations(
    operation_id, request_id, payload_sha256, type, source_person_ids_json,
    target_person_id, state, operation_revision, undo_of_operation_id, undone_by,
    catalog_filter_revision, created_at, committed_at)
    SELECT operation_id, request_id, payload_sha256, type, source_person_ids_json,
           target_person_id, state, operation_revision, undo_of_operation_id, undone_by,
           catalog_filter_revision, created_at, committed_at
    FROM person_operations_backup;

INSERT INTO person_operation_items(
    operation_id, item_index, entity_type, entity_id, entity_key,
    before_value_json, after_value_json, after_revision)
    SELECT operation_id, item_index, entity_type, entity_id, NULL,
           before_value_json, after_value_json, after_revision
    FROM person_operation_items_backup;

-- (5) Restore the alias references verbatim, then drop the parking tables and rebuild the
--     indexes with their frozen 0011 names and shapes.
UPDATE person_aliases SET last_operation_id = (
        SELECT backup.last_operation_id
        FROM person_alias_operation_refs_backup AS backup
        WHERE backup.alias_id = person_aliases.alias_id)
    WHERE alias_id IN (SELECT alias_id FROM person_alias_operation_refs_backup);

DROP TABLE person_operations_backup;
DROP TABLE person_operation_items_backup;
DROP TABLE person_alias_operation_refs_backup;

CREATE INDEX idx_person_operations_committed
    ON person_operations(committed_at, operation_id);

CREATE INDEX idx_person_operation_items_entity
    ON person_operation_items(entity_id, operation_id);
