-- P2-03A place core: stable place directory, GPS facts, one manual control row per
-- asset, append-only history, exclusions, operations/items and the two singleton
-- revisions. Additive to 0001/0002/0003; no frozen table, column or key is touched.
--
-- P2-03A deliberately does not create the P2-03B resolver objects (place_resolutions,
-- place_resolution_candidates, geo_datasets, geo_dataset_files, active_geo_dataset):
-- this task ships no dataset package and must not invent a fake ACTIVE one.

CREATE TABLE places (
    place_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    kind            TEXT NOT NULL CHECK(kind IN ('country', 'city', 'attraction', 'custom')),
    canonical_name  TEXT NOT NULL CHECK(length(canonical_name) BETWEEN 1 AND 200),
    -- Derived comparison key of ``canonical_name`` (same pattern as
    -- ``file_locations.path_key`` in ADR-0002 §4): the display spelling stays readable
    -- while alias uniqueness and name search compare one stable normalised form.
    normalized_name TEXT NOT NULL CHECK(length(normalized_name) BETWEEN 1 AND 200),
    parent_place_id INTEGER REFERENCES places(place_id) ON DELETE RESTRICT,
    origin          TEXT NOT NULL CHECK(origin IN ('USER', 'DATASET')),
    lifecycle       TEXT NOT NULL CHECK(lifecycle IN ('ACTIVE', 'RETIRED')),
    revision        INTEGER NOT NULL CHECK(revision >= 1),
    created_at      INTEGER NOT NULL CHECK(created_at >= 0),
    updated_at      INTEGER NOT NULL CHECK(updated_at >= 0),
    -- ADR-0005 §2: a country never has a parent, and no place is its own parent. A
    -- longer ancestor cycle needs a cross-row walk and is enforced by the single-writer
    -- transaction (SQLite CHECK cannot express it).
    CHECK(parent_place_id IS NULL OR parent_place_id <> place_id),
    CHECK(kind <> 'country' OR parent_place_id IS NULL)
);

CREATE INDEX idx_places_parent ON places(parent_place_id, lifecycle);
CREATE INDEX idx_places_kind ON places(kind, lifecycle, place_id);
CREATE INDEX idx_places_name ON places(normalized_name);

CREATE TABLE place_aliases (
    alias_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id   INTEGER NOT NULL REFERENCES places(place_id) ON DELETE RESTRICT,
    text       TEXT NOT NULL CHECK(length(text) BETWEEN 1 AND 200),
    normalized TEXT NOT NULL CHECK(length(normalized) BETWEEN 1 AND 200),
    language   TEXT,
    source     TEXT NOT NULL CHECK(source IN ('USER', 'DATASET')),
    lifecycle  TEXT NOT NULL CHECK(lifecycle IN ('ACTIVE', 'RETIRED')),
    revision   INTEGER NOT NULL CHECK(revision >= 1),
    created_at INTEGER NOT NULL CHECK(created_at >= 0),
    updated_at INTEGER NOT NULL CHECK(updated_at >= 0)
);

-- One ACTIVE alias spelling per place; retired spellings stay readable as history.
CREATE UNIQUE INDEX uq_place_aliases_active
    ON place_aliases(place_id, normalized) WHERE lifecycle = 'ACTIVE';
CREATE INDEX idx_place_aliases_normalized ON place_aliases(normalized, lifecycle);
CREATE INDEX idx_place_aliases_place ON place_aliases(place_id, lifecycle);

CREATE TABLE place_external_refs (
    provider    TEXT NOT NULL CHECK(provider <> ''),
    external_id TEXT NOT NULL CHECK(external_id <> ''),
    place_id    INTEGER NOT NULL REFERENCES places(place_id) ON DELETE RESTRICT,
    created_at  INTEGER NOT NULL CHECK(created_at >= 0),
    PRIMARY KEY(provider, external_id)
);

CREATE INDEX idx_place_external_refs_place ON place_external_refs(place_id);

-- Operation log. Every write path (single, batch, undo) is one COMMITTED operation with
-- an immutable item list; undo never deletes an entry, it reverses one.
CREATE TABLE place_operations (
    operation_id         TEXT PRIMARY KEY CHECK(operation_id <> ''),
    request_id           TEXT NOT NULL UNIQUE CHECK(request_id <> ''),
    payload_sha256       TEXT NOT NULL
        CHECK(length(payload_sha256) = 64 AND payload_sha256 NOT GLOB '*[^0-9a-f]*'),
    operation_type       TEXT NOT NULL CHECK(operation_type IN (
        'ASSIGN', 'UNASSIGN', 'RESTORE_AUTO', 'RENAME_PLACE', 'REPARENT_PLACE',
        'BATCH_ASSIGN', 'BATCH_UNASSIGN', 'BATCH_RESTORE_AUTO', 'UNDO')),
    operation_revision   INTEGER NOT NULL CHECK(operation_revision >= 1),
    actor                TEXT NOT NULL CHECK(actor = 'LOCAL_USER'),
    atomic               INTEGER NOT NULL CHECK(atomic = 1),
    status               TEXT NOT NULL CHECK(status IN ('COMMITTED', 'REVERSED')),
    undo_of_operation_id TEXT REFERENCES place_operations(operation_id) ON DELETE RESTRICT,
    -- CatalogFilterRevision right after this operation committed (0 = it did not move).
    catalog_filter_revision INTEGER NOT NULL DEFAULT 0 CHECK(catalog_filter_revision >= 0),
    created_at           INTEGER NOT NULL CHECK(created_at >= 0),
    committed_at         INTEGER NOT NULL CHECK(committed_at >= 0),
    CHECK(undo_of_operation_id IS NULL OR undo_of_operation_id <> operation_id)
);

CREATE INDEX idx_place_operations_committed ON place_operations(committed_at, operation_id);

-- Current GPS fact: at most one row per asset. ``SOURCE_UNAVAILABLE`` is deliberately
-- absent from the stored enum - a blocked read must never replace the previous fact
-- (ADR-0005 §3), so it can only appear in a job result, never in this table.
CREATE TABLE asset_geo_facts (
    asset_id                  INTEGER PRIMARY KEY REFERENCES assets(asset_id) ON DELETE RESTRICT,
    geo_fact_id               TEXT NOT NULL UNIQUE CHECK(geo_fact_id <> ''),
    location_id               INTEGER REFERENCES file_locations(location_id) ON DELETE RESTRICT,
    input_revision            TEXT NOT NULL
        CHECK(length(input_revision) = 64 AND input_revision NOT GLOB '*[^0-9a-f]*'),
    fact_revision             INTEGER NOT NULL CHECK(fact_revision >= 1),
    status                    TEXT NOT NULL
        CHECK(status IN ('VALID', 'ABSENT', 'INVALID', 'UNSUPPORTED_CRS')),
    source                    TEXT NOT NULL CHECK(source = 'EXIF_GPS'),
    processor_id              TEXT NOT NULL CHECK(processor_id <> ''),
    raw_fields_checksum_sha256 TEXT NOT NULL
        CHECK(length(raw_fields_checksum_sha256) = 64
              AND raw_fields_checksum_sha256 NOT GLOB '*[^0-9a-f]*'),
    raw_gps_json              TEXT CHECK(raw_gps_json IS NULL OR json_valid(raw_gps_json)),
    latitude                  REAL,
    longitude                 REAL,
    datum_status              TEXT NOT NULL
        CHECK(datum_status IN ('WGS84', 'ASSUMED_WGS84', 'UNSUPPORTED', 'ABSENT')),
    created_at                INTEGER NOT NULL CHECK(created_at >= 0),
    updated_at                INTEGER NOT NULL CHECK(updated_at >= 0),
    CHECK(latitude IS NULL OR (latitude >= -90.0 AND latitude <= 90.0)),
    CHECK(longitude IS NULL OR (longitude >= -180.0 AND longitude <= 180.0)),
    CHECK(
        (status = 'VALID' AND raw_gps_json IS NOT NULL AND latitude IS NOT NULL
         AND longitude IS NOT NULL AND datum_status IN ('WGS84', 'ASSUMED_WGS84'))
        OR
        (status = 'ABSENT' AND raw_gps_json IS NULL AND latitude IS NULL
         AND longitude IS NULL AND datum_status = 'ABSENT')
        OR
        (status IN ('INVALID', 'UNSUPPORTED_CRS') AND latitude IS NULL AND longitude IS NULL)
    )
);

CREATE INDEX idx_asset_geo_facts_status ON asset_geo_facts(status, updated_at);

-- Exactly one current control row per asset (ADR-0005 §5).
CREATE TABLE asset_place_assignments (
    asset_id      INTEGER PRIMARY KEY REFERENCES assets(asset_id) ON DELETE RESTRICT,
    assignment_id TEXT NOT NULL UNIQUE CHECK(assignment_id <> ''),
    place_id      INTEGER REFERENCES places(place_id) ON DELETE RESTRICT,
    mode          TEXT NOT NULL
        CHECK(mode IN ('AUTO_ALLOWED', 'MANUAL_ASSIGNED', 'MANUAL_UNASSIGNED')),
    origin        TEXT NOT NULL CHECK(origin IN ('NONE', 'MANUAL', 'CONFIRMED_SUGGESTION')),
    manual_lock   INTEGER NOT NULL CHECK(manual_lock IN (0, 1)),
    resolution_id TEXT,
    revision      INTEGER NOT NULL CHECK(revision >= 1),
    record_state  TEXT NOT NULL CHECK(record_state IN ('ACTIVE', 'SUPERSEDED')),
    actor         TEXT NOT NULL CHECK(actor = 'LOCAL_USER'),
    operation_id  TEXT NOT NULL REFERENCES place_operations(operation_id) ON DELETE RESTRICT,
    created_at    INTEGER NOT NULL CHECK(created_at >= 0),
    updated_at    INTEGER NOT NULL CHECK(updated_at >= 0),
    CHECK(
        (mode = 'AUTO_ALLOWED' AND place_id IS NULL AND origin = 'NONE'
         AND manual_lock = 0 AND resolution_id IS NULL)
        OR
        (mode = 'MANUAL_ASSIGNED' AND place_id IS NOT NULL
         AND origin IN ('MANUAL', 'CONFIRMED_SUGGESTION') AND manual_lock = 1)
        OR
        (mode = 'MANUAL_UNASSIGNED' AND place_id IS NULL AND origin = 'MANUAL'
         AND manual_lock = 1 AND resolution_id IS NULL)
    )
);

CREATE INDEX idx_asset_place_assignments_place
    ON asset_place_assignments(place_id, mode);
CREATE INDEX idx_asset_place_assignments_mode
    ON asset_place_assignments(mode, updated_at);

CREATE TABLE asset_place_assignment_history (
    asset_id      INTEGER NOT NULL REFERENCES assets(asset_id) ON DELETE RESTRICT,
    revision      INTEGER NOT NULL CHECK(revision >= 1),
    assignment_id TEXT NOT NULL CHECK(assignment_id <> ''),
    place_id      INTEGER REFERENCES places(place_id) ON DELETE RESTRICT,
    mode          TEXT NOT NULL
        CHECK(mode IN ('AUTO_ALLOWED', 'MANUAL_ASSIGNED', 'MANUAL_UNASSIGNED')),
    origin        TEXT NOT NULL CHECK(origin IN ('NONE', 'MANUAL', 'CONFIRMED_SUGGESTION')),
    manual_lock   INTEGER NOT NULL CHECK(manual_lock IN (0, 1)),
    resolution_id TEXT,
    record_state  TEXT NOT NULL CHECK(record_state IN ('ACTIVE', 'SUPERSEDED')),
    actor         TEXT NOT NULL CHECK(actor = 'LOCAL_USER'),
    operation_id  TEXT NOT NULL CHECK(operation_id <> ''),
    created_at    INTEGER NOT NULL CHECK(created_at >= 0),
    updated_at    INTEGER NOT NULL CHECK(updated_at >= 0),
    PRIMARY KEY(asset_id, revision),
    CHECK(
        (mode = 'AUTO_ALLOWED' AND place_id IS NULL AND origin = 'NONE'
         AND manual_lock = 0 AND resolution_id IS NULL)
        OR
        (mode = 'MANUAL_ASSIGNED' AND place_id IS NOT NULL
         AND origin IN ('MANUAL', 'CONFIRMED_SUGGESTION') AND manual_lock = 1)
        OR
        (mode = 'MANUAL_UNASSIGNED' AND place_id IS NULL AND origin = 'MANUAL'
         AND manual_lock = 1 AND resolution_id IS NULL)
    )
);

CREATE UNIQUE INDEX uq_assignment_history_assignment_id
    ON asset_place_assignment_history(assignment_id);
CREATE INDEX idx_assignment_history_operation
    ON asset_place_assignment_history(operation_id);

CREATE TABLE asset_place_exclusions (
    asset_id     INTEGER NOT NULL REFERENCES assets(asset_id) ON DELETE RESTRICT,
    place_id     INTEGER NOT NULL REFERENCES places(place_id) ON DELETE RESTRICT,
    active       INTEGER NOT NULL CHECK(active IN (0, 1)),
    revision     INTEGER NOT NULL CHECK(revision >= 1),
    operation_id TEXT NOT NULL REFERENCES place_operations(operation_id) ON DELETE RESTRICT,
    created_at   INTEGER NOT NULL CHECK(created_at >= 0),
    updated_at   INTEGER NOT NULL CHECK(updated_at >= 0),
    PRIMARY KEY(asset_id, place_id)
);

CREATE INDEX idx_asset_place_exclusions_place
    ON asset_place_exclusions(place_id, active);

CREATE TABLE place_operation_items (
    operation_id          TEXT NOT NULL REFERENCES place_operations(operation_id) ON DELETE RESTRICT,
    item_index            INTEGER NOT NULL CHECK(item_index >= 0),
    asset_id              INTEGER REFERENCES assets(asset_id) ON DELETE RESTRICT,
    place_id              INTEGER REFERENCES places(place_id) ON DELETE RESTRICT,
    action                TEXT NOT NULL CHECK(action IN (
        'ASSIGN', 'UNASSIGN', 'RESTORE_AUTO', 'RENAME_PLACE', 'REPARENT_PLACE')),
    target_place_id       INTEGER REFERENCES places(place_id) ON DELETE RESTRICT,
    expected_revision     INTEGER NOT NULL CHECK(expected_revision >= 0),
    before_assignment_json TEXT CHECK(
        before_assignment_json IS NULL OR json_valid(before_assignment_json)),
    after_assignment_json TEXT CHECK(
        after_assignment_json IS NULL OR json_valid(after_assignment_json)),
    -- The exclusion side of the same item as a bounded JSON array of
    -- ``{"place_id", "before", "after"}`` deltas. One item can touch two places at once
    -- (assign A->B excludes A and may clear B's own exclusion), so a single place column
    -- could not describe the compensation exactly.
    exclusion_deltas_json TEXT NOT NULL DEFAULT '[]'
        CHECK(json_valid(exclusion_deltas_json)),
    PRIMARY KEY(operation_id, item_index)
);

-- One item per asset inside one operation; place-level items carry a NULL asset_id and
-- are bounded by the item_index primary key instead.
CREATE UNIQUE INDEX uq_place_operation_items_asset
    ON place_operation_items(operation_id, asset_id) WHERE asset_id IS NOT NULL;
CREATE INDEX idx_place_operation_items_asset ON place_operation_items(asset_id);

-- The authoritative place directory/filter revision (ADR-0003 shell, landed here).
CREATE TABLE catalog_filter_revision (
    revision_id  INTEGER PRIMARY KEY CHECK(revision_id = 1),
    revision     INTEGER NOT NULL CHECK(revision >= 0),
    committed_at INTEGER NOT NULL CHECK(committed_at >= 0),
    cause        TEXT
);

INSERT INTO catalog_filter_revision (revision_id, revision, committed_at, cause)
VALUES (1, 0, 0, NULL);

CREATE TABLE catalog_filter_revision_events (
    revision      INTEGER PRIMARY KEY CHECK(revision >= 1),
    cause         TEXT NOT NULL CHECK(cause <> ''),
    changed_count INTEGER NOT NULL CHECK(changed_count >= 0),
    all_assets    INTEGER NOT NULL CHECK(all_assets IN (0, 1)),
    asset_ids_json TEXT NOT NULL CHECK(json_valid(asset_ids_json)),
    committed_at  INTEGER NOT NULL CHECK(committed_at >= 0)
);

-- Independent of the filter revision: candidate visibility only (P2-03B increments it).
CREATE TABLE place_suggestion_revision (
    revision_id  INTEGER PRIMARY KEY CHECK(revision_id = 1),
    revision     INTEGER NOT NULL CHECK(revision >= 0),
    committed_at INTEGER NOT NULL CHECK(committed_at >= 0),
    cause        TEXT
);

INSERT INTO place_suggestion_revision (revision_id, revision, committed_at, cause)
VALUES (1, 0, 0, NULL);

-- Append-only / no-physical-delete guarantees the ADR freezes for history and identity.
CREATE TRIGGER trg_places_no_delete
BEFORE DELETE ON places
BEGIN
    SELECT RAISE(ABORT, 'places are retired, never physically deleted');
END;

-- The snapshot of a history row is immutable. The only legal update is the one the ADR
-- itself defines: an older revision stops being the current control row, so its
-- ``record_state`` flips ACTIVE -> SUPERSEDED. Every other column must stay identical.
CREATE TRIGGER trg_assignment_history_no_update
BEFORE UPDATE ON asset_place_assignment_history
WHEN NOT (
    OLD.record_state = 'ACTIVE'
    AND NEW.record_state = 'SUPERSEDED'
    AND NEW.asset_id = OLD.asset_id
    AND NEW.revision = OLD.revision
    AND NEW.assignment_id = OLD.assignment_id
    AND NEW.place_id IS OLD.place_id
    AND NEW.mode = OLD.mode
    AND NEW.origin = OLD.origin
    AND NEW.manual_lock = OLD.manual_lock
    AND NEW.resolution_id IS OLD.resolution_id
    AND NEW.actor = OLD.actor
    AND NEW.operation_id = OLD.operation_id
    AND NEW.created_at = OLD.created_at
    AND NEW.updated_at = OLD.updated_at
)
BEGIN
    SELECT RAISE(ABORT, 'assignment history is append-only');
END;

CREATE TRIGGER trg_assignment_history_no_delete
BEFORE DELETE ON asset_place_assignment_history
BEGIN
    SELECT RAISE(ABORT, 'assignment history is append-only');
END;

CREATE TRIGGER trg_place_operations_no_delete
BEFORE DELETE ON place_operations
BEGIN
    SELECT RAISE(ABORT, 'place operations are append-only');
END;

CREATE TRIGGER trg_place_operation_items_no_update
BEFORE UPDATE ON place_operation_items
BEGIN
    SELECT RAISE(ABORT, 'place operation items are append-only');
END;

CREATE TRIGGER trg_place_operation_items_no_delete
BEFORE DELETE ON place_operation_items
BEGIN
    SELECT RAISE(ABORT, 'place operation items are append-only');
END;
