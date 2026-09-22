-- P3-08B face-redetection mapping layer (ADR-0010). Two additive tables hold the
-- reviewable old/new ``face_id`` mapping of one asset, and the closed CHECK vocabulary of
-- the *shared* person operation log is widened a third time so a human decision on that
-- mapping is recorded exactly like every other human act.
--
-- Column ownership (ADR-0010 §2/§3/§5):
--
-- * ``face_redetection_groups`` is the *review unit*: one row per surviving old face that
--   carries human knowledge, keyed by ``old_face_id``. It is never a person authority - the
--   person authority stays ``face_assignment_controls``/``face_person_links``/
--   ``face_exclusions`` and the operation log stays ``person_operations``. A group only
--   records the review state machine (``OPEN``/``CONFIRMED``/``REJECTED``), the derived
--   ``candidate_state`` and its own forward-only ``revision``.
-- * ``face_redetection_mappings`` is one *candidate pair*: the stable
--   ``mapping_id = sha256(canonical JSON{asset_id, old_face_id, new_face_id,
--   old_spec_revision, new_spec_revision})``, the frozen ``match_state`` (``HIGH``/``WEAK``)
--   and the per-row state machine (``SUGGESTED``/``CONFIRMED``/``REJECTED``/``STALE``).
--   ``inherits_person`` remembers which target a confirmation chose to give the positive
--   knowledge to, which is what an operation snapshot has to replay.
--
-- Foreign-key shape (a deliberate, documented choice):
--
-- * ``old_face_id``/``asset_id`` are protected anchors: ADR-0010 §3.4 keeps a human-owned
--   old row alive during re-detection, so a real foreign key is safe and is kept. The
--   retirement path removes a group and its rows *before* it deletes the face.
-- * ``new_face_id`` deliberately carries **no** foreign key. ADR-0010 §7 requires the
--   opposite of a RESTRICT dependency: when a later re-detection removes a candidate face,
--   the mapping row must survive and become ``STALE`` (``+1`` revision) instead of being
--   cascaded away, and it must never be re-interpreted as "this pair is still a candidate".
--   A shape CHECK plus an index keeps the identifier honest without forbidding that
--   supersession.

CREATE TABLE face_redetection_groups (
    old_face_id       TEXT PRIMARY KEY
        CHECK(length(old_face_id) = 64 AND old_face_id NOT GLOB '*[^0-9a-f]*'),
    asset_id          INTEGER NOT NULL REFERENCES assets(asset_id) ON DELETE RESTRICT,
    old_spec_revision TEXT NOT NULL CHECK(old_spec_revision <> ''),
    candidate_state   TEXT NOT NULL CHECK(candidate_state IN (
        'NO_CANDIDATE', 'UNIQUE_HIGH', 'UNIQUE_WEAK', 'AMBIGUOUS')),
    state             TEXT NOT NULL CHECK(state IN ('OPEN', 'CONFIRMED', 'REJECTED')),
    revision          INTEGER NOT NULL CHECK(revision >= 1),
    last_operation_id TEXT,
    created_at        INTEGER NOT NULL CHECK(created_at >= 0),
    updated_at        INTEGER NOT NULL CHECK(updated_at >= 0)
);

CREATE INDEX idx_face_redetection_groups_open
    ON face_redetection_groups(state, old_face_id);

CREATE TABLE face_redetection_mappings (
    mapping_id        TEXT PRIMARY KEY
        CHECK(length(mapping_id) = 64 AND mapping_id NOT GLOB '*[^0-9a-f]*'),
    old_face_id       TEXT NOT NULL REFERENCES faces(face_id) ON DELETE RESTRICT,
    --: No foreign key on purpose: a removed candidate must be able to leave a STALE row.
    new_face_id       TEXT NOT NULL
        CHECK(length(new_face_id) = 64 AND new_face_id NOT GLOB '*[^0-9a-f]*'),
    asset_id          INTEGER NOT NULL REFERENCES assets(asset_id) ON DELETE RESTRICT,
    old_spec_revision TEXT NOT NULL CHECK(old_spec_revision <> ''),
    new_spec_revision TEXT NOT NULL CHECK(new_spec_revision <> ''),
    match_state       TEXT NOT NULL CHECK(match_state IN ('HIGH', 'WEAK')),
    state             TEXT NOT NULL CHECK(state IN (
        'SUGGESTED', 'CONFIRMED', 'REJECTED', 'STALE')),
    --: Which target a confirmation chose to carry the positive person knowledge.
    inherits_person   INTEGER NOT NULL DEFAULT 0 CHECK(inherits_person IN (0, 1)),
    revision          INTEGER NOT NULL CHECK(revision >= 1),
    last_operation_id TEXT,
    created_at        INTEGER NOT NULL CHECK(created_at >= 0),
    updated_at        INTEGER NOT NULL CHECK(updated_at >= 0),
    --: Self-mapping is not a mapping (ADR-0010 §2.1).
    CHECK(old_face_id <> new_face_id),
    --: A pair is at most one row; a re-detection updates the row, never inserts a second.
    UNIQUE (old_face_id, new_face_id)
);

CREATE INDEX idx_face_redetection_mappings_group
    ON face_redetection_mappings(old_face_id, state);

CREATE INDEX idx_face_redetection_mappings_candidate
    ON face_redetection_mappings(new_face_id);

-- The shared operation log is rebuilt a third time for the same reason 0012 and 0013
-- rebuilt it: ``person_operations.type`` and ``person_operation_items.entity_type`` are
-- closed CHECK lists, and a mapping decision adds two operation types
-- (``FACE_MAPPING_CONFIRM``/``FACE_MAPPING_REJECT``) plus the fourth item shape
-- ``FACE_MAPPING_GROUP`` (a text ``entity_key`` with ``entity_id IS NULL``). Nothing else
-- changes: ``persons``, ``face_person_links``, the human-control tables, the aliases, the
-- queue causes and the ``PERSON_CHANGED`` cause all keep their frozen shape, and every
-- historical row is copied column by column (including the ``UNDO`` links, the
-- ``SPLIT_GROUP``/``FACE_CONTROL`` items and the alias references).

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

-- (2) The rebuilt operation log: the frozen 0013 shape plus the two mapping types.
CREATE TABLE person_operations (
    operation_id          TEXT PRIMARY KEY CHECK(operation_id <> ''),
    request_id            TEXT NOT NULL UNIQUE CHECK(request_id <> ''),
    payload_sha256        TEXT NOT NULL
        CHECK(length(payload_sha256) = 64 AND payload_sha256 NOT GLOB '*[^0-9a-f]*'),
    type                  TEXT NOT NULL CHECK(type IN (
        'RENAME', 'MERGE_BY_NAME', 'MERGE', 'BATCH_MERGE', 'UNDO',
        'FACE_ASSIGN', 'FACE_UNASSIGN', 'FACE_RESTORE_AUTO', 'BATCH_FACE_EDIT',
        'SPLIT', 'FACE_MAPPING_CONFIRM', 'FACE_MAPPING_REJECT')),
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

-- (3) The rebuilt item table. A ``FACE_MAPPING_GROUP`` row is keyed by the old ``face_id``
--     (text, ``entity_id IS NULL``) exactly like a ``FACE_CONTROL`` row, while
--     ``PERSON_BUNDLE``/``SPLIT_GROUP`` keep the positive integer id. The P3-03 revision
--     floor stays for ``PERSON_BUNDLE`` rows; every other shape may record revision 0.
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
    SELECT operation_id, item_index, entity_type, entity_id, entity_key,
           before_value_json, after_value_json, after_revision
    FROM person_operation_items_backup;

-- (5) Restore the alias references verbatim, then drop the parking tables and rebuild the
--     indexes with their frozen 0011/0012 names and shapes.
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
