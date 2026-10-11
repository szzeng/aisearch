-- Add explicit atomic regrouping; preserve all v28 history and references.
CREATE TABLE person_operations_backup AS SELECT * FROM person_operations;
CREATE TABLE person_operation_items_backup AS SELECT * FROM person_operation_items;
CREATE TABLE person_operation_retention_backup AS SELECT * FROM person_operation_retention;
CREATE TABLE person_alias_operation_refs_backup AS
    SELECT alias_id, last_operation_id FROM person_aliases WHERE last_operation_id IS NOT NULL;
UPDATE person_aliases SET last_operation_id = NULL WHERE last_operation_id IS NOT NULL;

-- The operation table has self-referencing undo edges. The backup above is deliberately
-- unconstrained, so temporarily sever those edges before dropping the original table; the
-- full state and references are restored from the backup below.
UPDATE person_operations
    SET state = 'COMMITTED', undo_of_operation_id = NULL, undone_by = NULL;

DROP TABLE person_operation_retention;
DROP TABLE person_operation_items;
DROP TABLE person_operations;

CREATE TABLE person_operations (
    operation_id          TEXT PRIMARY KEY CHECK(operation_id <> ''),
    request_id            TEXT NOT NULL UNIQUE CHECK(request_id <> ''),
    payload_sha256        TEXT NOT NULL
        CHECK(length(payload_sha256) = 64 AND payload_sha256 NOT GLOB '*[^0-9a-f]*'),
    type                  TEXT NOT NULL CHECK(type IN (
        'RENAME', 'MERGE_BY_NAME', 'MERGE', 'BATCH_MERGE', 'UNDO',
        'FACE_ASSIGN', 'FACE_UNASSIGN', 'FACE_RESTORE_AUTO', 'BATCH_FACE_EDIT',
        'SPLIT', 'FACE_MAPPING_CONFIRM', 'FACE_MAPPING_REJECT',
        'CREATE_PERSON_FROM_FACE', 'FACE_REGROUP')),
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

CREATE TABLE person_operation_items (
    operation_id       TEXT NOT NULL REFERENCES person_operations(operation_id)
        ON DELETE RESTRICT,
    item_index         INTEGER NOT NULL CHECK(item_index >= 0),
    entity_type        TEXT NOT NULL CHECK(entity_type IN (
        'PERSON_BUNDLE', 'FACE_CONTROL', 'SPLIT_GROUP', 'FACE_MAPPING_GROUP')),
    entity_id          INTEGER CHECK(entity_id IS NULL OR entity_id >= 1),
    entity_key         TEXT CHECK(entity_key IS NULL OR entity_key <> ''),
    before_value_json  TEXT NOT NULL CHECK(json_valid(before_value_json)),
    after_value_json   TEXT NOT NULL CHECK(json_valid(after_value_json)),
    after_revision     INTEGER NOT NULL CHECK(after_revision >= 0
        AND (entity_type <> 'PERSON_BUNDLE' OR after_revision >= 1)),
    PRIMARY KEY(operation_id, item_index),
    CHECK((entity_type IN ('PERSON_BUNDLE', 'SPLIT_GROUP')) = (entity_id IS NOT NULL)),
    CHECK((entity_type IN ('FACE_CONTROL', 'FACE_MAPPING_GROUP')) = (entity_key IS NOT NULL))
);

CREATE TABLE person_operation_retention (
    operation_id       TEXT PRIMARY KEY REFERENCES person_operations(operation_id) ON DELETE RESTRICT,
    expired_at         INTEGER NOT NULL CHECK (expired_at >= 0),
    summary_json       TEXT NOT NULL CHECK (json_valid(summary_json)),
    replay_json        TEXT NOT NULL CHECK (json_valid(replay_json)),
    item_count         INTEGER NOT NULL CHECK (item_count > 0),
    snapshot_bytes     INTEGER NOT NULL CHECK (snapshot_bytes > 0),
    receipt_bytes      INTEGER NOT NULL CHECK (receipt_bytes > 0),
    CHECK (snapshot_bytes > receipt_bytes)
);

INSERT INTO person_operations(
    operation_id, request_id, payload_sha256, type, source_person_ids_json,
    target_person_id, state, operation_revision, undo_of_operation_id, undone_by,
    catalog_filter_revision, created_at, committed_at)
    SELECT operation_id, request_id, payload_sha256, type, source_person_ids_json,
           target_person_id, state, operation_revision, undo_of_operation_id, undone_by,
           catalog_filter_revision, created_at, committed_at
    FROM person_operations_backup
    ORDER BY operation_revision ASC;

INSERT INTO person_operation_items(
    operation_id, item_index, entity_type, entity_id, entity_key,
    before_value_json, after_value_json, after_revision)
    SELECT operation_id, item_index, entity_type, entity_id, entity_key,
           before_value_json, after_value_json, after_revision
    FROM person_operation_items_backup;

INSERT INTO person_operation_retention(
    operation_id, expired_at, summary_json, replay_json, item_count, snapshot_bytes, receipt_bytes)
    SELECT operation_id, expired_at, summary_json, replay_json, item_count, snapshot_bytes, receipt_bytes
    FROM person_operation_retention_backup;

UPDATE person_aliases SET last_operation_id = (
        SELECT backup.last_operation_id
        FROM person_alias_operation_refs_backup AS backup
        WHERE backup.alias_id = person_aliases.alias_id)
    WHERE alias_id IN (SELECT alias_id FROM person_alias_operation_refs_backup);

DROP TABLE person_operations_backup;
DROP TABLE person_operation_items_backup;
DROP TABLE person_operation_retention_backup;
DROP TABLE person_alias_operation_refs_backup;

CREATE INDEX idx_person_operations_committed
    ON person_operations(committed_at, operation_id);
CREATE INDEX idx_person_operation_items_entity
    ON person_operation_items(entity_id, operation_id);
