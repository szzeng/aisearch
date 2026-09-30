-- ADR-0014: administrative polygons and independent scenic-area overlays.
-- Migration runner disables FK enforcement ONLY around this atomic table rebuild,
-- checks every FK before commit and restores enforcement even on failure.
PRAGMA legacy_alter_table = ON;
CREATE TABLE places_new (
    place_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    kind            TEXT NOT NULL CHECK(kind IN ('country', 'province', 'city', 'district', 'attraction', 'custom')),
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

CREATE TABLE places_sequence_backup AS SELECT seq FROM sqlite_sequence WHERE name='places';
INSERT INTO places_new SELECT * FROM places;
DROP TABLE places;
ALTER TABLE places_new RENAME TO places;
UPDATE sqlite_sequence SET seq=MAX(seq,COALESCE((SELECT seq FROM places_sequence_backup),0)) WHERE name='places';
DROP TABLE places_sequence_backup;
PRAGMA legacy_alter_table = OFF;
CREATE INDEX idx_places_parent ON places(parent_place_id, lifecycle);
CREATE INDEX idx_places_kind ON places(kind, lifecycle, place_id);
CREATE INDEX idx_places_name ON places(normalized_name);


CREATE TABLE boundary_packages (
    package_id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL,
    version TEXT NOT NULL,
    checksum TEXT NOT NULL CHECK(length(checksum)=64),
    layer TEXT NOT NULL CHECK(layer IN ('administrative','scenic')),
    metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
    active INTEGER NOT NULL CHECK(active IN (0,1)),
    installed_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX uq_boundary_active ON boundary_packages(namespace) WHERE active=1;
CREATE TABLE boundary_features (
    package_id TEXT NOT NULL REFERENCES boundary_packages(package_id),
    place_id INTEGER NOT NULL REFERENCES places(place_id),
    geometry_wkb BLOB NOT NULL,
    PRIMARY KEY(package_id,place_id)
);
CREATE TABLE boundary_results (
    package_id TEXT NOT NULL REFERENCES boundary_packages(package_id),
    asset_id INTEGER NOT NULL REFERENCES assets(asset_id),
    geo_fact_id TEXT NOT NULL,
    input_revision TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('MATCHED','AMBIGUOUS','NO_MATCH','UNSUPPORTED')),
    updated_at INTEGER NOT NULL,
    PRIMARY KEY(package_id,asset_id)
);
CREATE TABLE boundary_memberships (
    package_id TEXT NOT NULL,
    asset_id INTEGER NOT NULL,
    place_id INTEGER NOT NULL REFERENCES places(place_id),
    PRIMARY KEY(package_id,asset_id,place_id),
    FOREIGN KEY(package_id,asset_id) REFERENCES boundary_results(package_id,asset_id)
);
CREATE INDEX idx_boundary_memberships_asset ON boundary_memberships(asset_id,place_id);
CREATE INDEX idx_boundary_results_asset ON boundary_results(asset_id,package_id);
CREATE INDEX idx_boundary_memberships_place ON boundary_memberships(place_id,asset_id);

-- Only this view decides what is searchable. Automatic results never impersonate a
-- manual control row. Parent exclusions suppress the entire excluded subtree.
CREATE VIEW effective_asset_places AS
SELECT ap.asset_id, ap.place_id, 'MANUAL' AS origin
FROM asset_place_assignments ap JOIN places p ON p.place_id=ap.place_id
WHERE ap.mode='MANUAL_ASSIGNED' AND ap.record_state='ACTIVE' AND p.lifecycle='ACTIVE'
UNION
SELECT m.asset_id, m.place_id, 'GPS_BOUNDARY' AS origin
FROM boundary_memberships m
JOIN boundary_results r USING(package_id,asset_id)
JOIN boundary_packages b USING(package_id)
JOIN asset_geo_facts g ON g.asset_id=r.asset_id AND g.geo_fact_id=r.geo_fact_id
JOIN assets a ON a.asset_id=r.asset_id AND a.sha256=r.input_revision
JOIN places p ON p.place_id=m.place_id
WHERE b.active=1 AND p.lifecycle='ACTIVE'
AND NOT EXISTS (SELECT 1 FROM asset_place_assignments ap
    WHERE ap.asset_id=m.asset_id AND ap.mode<>'AUTO_ALLOWED')
AND NOT EXISTS (SELECT 1 FROM asset_place_exclusions x WHERE x.asset_id=m.asset_id
    AND x.active=1 AND x.place_id IN (
        WITH RECURSIVE parents(id) AS (
            SELECT m.place_id UNION
            SELECT p.parent_place_id FROM places p JOIN parents ON p.place_id=parents.id
            WHERE p.parent_place_id IS NOT NULL
        ) SELECT id FROM parents));
DROP TRIGGER trg_place_aliases_search_queue_insert;
DROP TRIGGER trg_place_aliases_search_queue_update;
CREATE TRIGGER trg_places_search_queue_change
AFTER UPDATE ON places
WHEN (old.canonical_name IS NOT new.canonical_name
      OR old.lifecycle IS NOT new.lifecycle)
BEGIN
    INSERT INTO search_projection_queue(asset_id, cause, queued_at)
    SELECT ap.asset_id, 'PLACE_NAME_CHANGED',
           CAST(strftime('%s', 'now') AS INTEGER) * 1000
    FROM effective_asset_places AS ap
    WHERE ap.place_id IN (
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
    FROM effective_asset_places AS ap
    WHERE ap.place_id IN (
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
    FROM effective_asset_places AS ap
    WHERE ap.place_id IN (
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

CREATE TRIGGER trg_places_no_delete
BEFORE DELETE ON places
BEGIN
    SELECT RAISE(ABORT, 'places are retired, never physically deleted');
END;
