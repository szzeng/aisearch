-- P2-03B: the offline name-dataset registry and the immutable suggestion-only place
-- resolution products. Additive to 0001..0004; no frozen table, column or key is touched.
--
-- The dataset *content* is immutable here: an import writes one row plus its file list,
-- validation and activation only move ``state``/timestamps and the singleton pointer, and
-- a trigger refuses every later content edit. Resolution rows and their candidates are
-- machine products: they are insert-once and never rewritten in place.

CREATE TABLE geo_datasets (
    dataset_id          TEXT PRIMARY KEY CHECK(dataset_id <> ''),
    package_id          TEXT NOT NULL CHECK(package_id <> ''),
    package_version     TEXT NOT NULL CHECK(package_version <> ''),
    contract_version    TEXT NOT NULL CHECK(contract_version = 'p2-place-contracts.v1'),
    manifest_schema     TEXT NOT NULL
        CHECK(manifest_schema = 'photo-search-place-dataset-manifest.v1'),
    provider            TEXT NOT NULL CHECK(provider <> ''),
    source_url          TEXT NOT NULL CHECK(source_url <> ''),
    source_snapshot     TEXT NOT NULL CHECK(source_snapshot <> ''),
    license_id          TEXT NOT NULL CHECK(license_id <> ''),
    license_url         TEXT NOT NULL CHECK(license_url <> ''),
    attribution         TEXT NOT NULL CHECK(attribution <> ''),
    builder_id          TEXT NOT NULL CHECK(builder_id <> ''),
    builder_version     TEXT NOT NULL CHECK(builder_version <> ''),
    parameters_sha256   TEXT NOT NULL
        CHECK(length(parameters_sha256) = 64 AND parameters_sha256 NOT GLOB '*[^0-9a-f]*'),
    content_sha256      TEXT NOT NULL
        CHECK(length(content_sha256) = 64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'),
    -- ADR-0005 §8: a dataset that is not WGS84 is refused, never silently reprojected.
    coordinate_reference_system TEXT NOT NULL CHECK(coordinate_reference_system = 'WGS84'),
    coverage_json       TEXT NOT NULL CHECK(json_valid(coverage_json)),
    compatible_schema_min INTEGER NOT NULL CHECK(compatible_schema_min >= 1),
    compatible_schema_max INTEGER NOT NULL CHECK(compatible_schema_max >= compatible_schema_min),
    -- A directory inside the application data root, never an absolute local path.
    relative_dir        TEXT NOT NULL
        CHECK(relative_dir <> '' AND relative_dir NOT LIKE '%..%'),
    previous_dataset_id TEXT REFERENCES geo_datasets(dataset_id) ON DELETE RESTRICT,
    state               TEXT NOT NULL
        CHECK(state IN ('STAGED', 'VALIDATED', 'ACTIVE', 'RETAINED')),
    record_count        INTEGER NOT NULL CHECK(record_count >= 0),
    file_count          INTEGER NOT NULL CHECK(file_count >= 1),
    generated_at        INTEGER NOT NULL CHECK(generated_at >= 0),
    validated_at        INTEGER CHECK(validated_at IS NULL OR validated_at >= 0),
    activated_at        INTEGER CHECK(activated_at IS NULL OR activated_at >= 0),
    retained_at         INTEGER CHECK(retained_at IS NULL OR retained_at >= 0),
    created_at          INTEGER NOT NULL CHECK(created_at >= 0),
    updated_at          INTEGER NOT NULL CHECK(updated_at >= 0),
    CHECK(previous_dataset_id IS NULL OR previous_dataset_id <> dataset_id)
);

CREATE INDEX idx_geo_datasets_state ON geo_datasets(state, dataset_id);

CREATE TABLE geo_dataset_files (
    dataset_id    TEXT NOT NULL REFERENCES geo_datasets(dataset_id) ON DELETE RESTRICT,
    relative_path TEXT NOT NULL
        CHECK(relative_path <> '' AND relative_path NOT LIKE '%..%'),
    role          TEXT NOT NULL
        CHECK(role IN ('PLACES', 'ALIASES', 'HIERARCHY', 'LICENSE', 'INDEX')),
    media_type    TEXT NOT NULL CHECK(media_type <> ''),
    sha256        TEXT NOT NULL
        CHECK(length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
    byte_size     INTEGER NOT NULL CHECK(byte_size >= 0),
    created_at    INTEGER NOT NULL CHECK(created_at >= 0),
    PRIMARY KEY(dataset_id, relative_path)
);

CREATE INDEX idx_geo_dataset_files_role ON geo_dataset_files(dataset_id, role);

-- Singleton active-package pointer. NULL means "no package is installed": the API must
-- never invent an ACTIVE dataset, and every resolution task blocks instead.
CREATE TABLE active_geo_dataset (
    singleton  INTEGER PRIMARY KEY CHECK(singleton = 1),
    dataset_id TEXT REFERENCES geo_datasets(dataset_id) ON DELETE RESTRICT,
    revision   INTEGER NOT NULL CHECK(revision >= 0),
    updated_at INTEGER NOT NULL CHECK(updated_at >= 0),
    cause      TEXT
);

INSERT INTO active_geo_dataset(singleton, dataset_id, revision, updated_at, cause)
VALUES (1, NULL, 0, 0, NULL);

-- Immutable machine product of one resolution run (ADR-0005 §4). It binds the asset, the
-- GeoFact it consumed, the asset content revision, the resolver processor, the dataset and
-- the config hash. The control revision is only a *snapshot*: it is never an identity, so a
-- late result can be stored while the manual lock stays untouched.
CREATE TABLE place_resolutions (
    resolution_id        TEXT PRIMARY KEY CHECK(resolution_id <> ''),
    asset_id             INTEGER NOT NULL REFERENCES assets(asset_id) ON DELETE RESTRICT,
    geo_fact_id          TEXT NOT NULL CHECK(geo_fact_id <> ''),
    input_revision       TEXT NOT NULL
        CHECK(length(input_revision) = 64 AND input_revision NOT GLOB '*[^0-9a-f]*'),
    control_revision_seen INTEGER NOT NULL CHECK(control_revision_seen >= 0),
    processor_id         TEXT NOT NULL CHECK(processor_id <> ''),
    dataset_id           TEXT NOT NULL REFERENCES geo_datasets(dataset_id) ON DELETE RESTRICT,
    dataset_version      TEXT NOT NULL CHECK(dataset_version <> ''),
    config_hash          TEXT NOT NULL
        CHECK(length(config_hash) = 64 AND config_hash NOT GLOB '*[^0-9a-f]*'),
    status               TEXT NOT NULL CHECK(status IN ('CANDIDATES', 'NO_CANDIDATE')),
    suggestion_revision  INTEGER NOT NULL CHECK(suggestion_revision >= 0),
    created_at           INTEGER NOT NULL CHECK(created_at >= 0)
);

-- ADR-0005 §7: the logical input of a resolution is unique, so a replay is provably the
-- same computation instead of a second, competing machine product.
CREATE UNIQUE INDEX uq_place_resolutions_input
    ON place_resolutions(asset_id, geo_fact_id, processor_id, dataset_id, config_hash);
CREATE INDEX idx_place_resolutions_asset ON place_resolutions(asset_id, created_at);
CREATE INDEX idx_place_resolutions_dataset ON place_resolutions(dataset_id);

CREATE TABLE place_resolution_candidates (
    resolution_id     TEXT NOT NULL
        REFERENCES place_resolutions(resolution_id) ON DELETE RESTRICT,
    rank              INTEGER NOT NULL CHECK(rank >= 1),
    candidate_id      TEXT NOT NULL CHECK(candidate_id <> ''),
    dataset_place_id  TEXT NOT NULL CHECK(dataset_place_id <> ''),
    local_place_id    INTEGER REFERENCES places(place_id) ON DELETE RESTRICT,
    -- The first version only ever suggests a city point (ADR-0005 §4).
    kind              TEXT NOT NULL CHECK(kind = 'city'),
    display_name      TEXT NOT NULL CHECK(length(display_name) BETWEEN 1 AND 200),
    ancestor_names_json TEXT NOT NULL CHECK(json_valid(ancestor_names_json)),
    distance_m        REAL NOT NULL CHECK(distance_m >= 0.0),
    match_basis       TEXT NOT NULL CHECK(match_basis = 'NEARBY_CITY_POINT'),
    confidence_basis  TEXT NOT NULL CHECK(confidence_basis = 'DISTANCE_ONLY'),
    -- A candidate may never turn into an assignment by itself.
    disposition       TEXT NOT NULL CHECK(disposition = 'SUGGESTION_ONLY'),
    created_at        INTEGER NOT NULL CHECK(created_at >= 0),
    PRIMARY KEY(resolution_id, rank)
);

CREATE INDEX idx_place_resolution_candidates_local
    ON place_resolution_candidates(local_place_id, resolution_id);

-- Imported package content is immutable; only state/timestamps may move afterwards.
CREATE TRIGGER trg_geo_datasets_content_immutable
BEFORE UPDATE ON geo_datasets
WHEN NOT (
    NEW.dataset_id = OLD.dataset_id
    AND NEW.package_id = OLD.package_id
    AND NEW.package_version = OLD.package_version
    AND NEW.contract_version = OLD.contract_version
    AND NEW.manifest_schema = OLD.manifest_schema
    AND NEW.provider = OLD.provider
    AND NEW.source_url = OLD.source_url
    AND NEW.source_snapshot = OLD.source_snapshot
    AND NEW.license_id = OLD.license_id
    AND NEW.license_url = OLD.license_url
    AND NEW.attribution = OLD.attribution
    AND NEW.builder_id = OLD.builder_id
    AND NEW.builder_version = OLD.builder_version
    AND NEW.parameters_sha256 = OLD.parameters_sha256
    AND NEW.content_sha256 = OLD.content_sha256
    AND NEW.coordinate_reference_system = OLD.coordinate_reference_system
    AND NEW.coverage_json = OLD.coverage_json
    AND NEW.compatible_schema_min = OLD.compatible_schema_min
    AND NEW.compatible_schema_max = OLD.compatible_schema_max
    AND NEW.relative_dir = OLD.relative_dir
    AND NEW.previous_dataset_id IS OLD.previous_dataset_id
    AND NEW.record_count = OLD.record_count
    AND NEW.file_count = OLD.file_count
    AND NEW.generated_at = OLD.generated_at
    AND NEW.created_at = OLD.created_at
)
BEGIN
    SELECT RAISE(ABORT, 'geo dataset content is immutable');
END;

-- ADR-0005 §8 freezes the package lifecycle. Data is validated before it may be activated,
-- an active package can only be retired, and a retained package can be activated again by
-- an explicit rollback. Nothing may ever go back to STAGED.
CREATE TRIGGER trg_geo_datasets_state_transition
BEFORE UPDATE OF state ON geo_datasets
WHEN NOT (
    (OLD.state = 'STAGED' AND NEW.state IN ('STAGED', 'VALIDATED'))
    OR (OLD.state = 'VALIDATED' AND NEW.state IN ('VALIDATED', 'ACTIVE', 'RETAINED'))
    OR (OLD.state = 'ACTIVE' AND NEW.state IN ('ACTIVE', 'RETAINED'))
    OR (OLD.state = 'RETAINED' AND NEW.state IN ('RETAINED', 'ACTIVE'))
)
BEGIN
    SELECT RAISE(ABORT, 'illegal geo dataset state transition');
END;

CREATE TRIGGER trg_geo_datasets_validated_at_required
BEFORE UPDATE OF state ON geo_datasets
WHEN NEW.state IN ('VALIDATED', 'ACTIVE')
     AND (NEW.validated_at IS NULL OR NEW.activated_at IS NULL AND NEW.state = 'ACTIVE')
BEGIN
    SELECT RAISE(ABORT, 'a validated dataset needs validated_at and an active one needs activated_at');
END;

CREATE TRIGGER trg_place_resolutions_no_update
BEFORE UPDATE ON place_resolutions
BEGIN
    SELECT RAISE(ABORT, 'place resolutions are immutable machine products');
END;

CREATE TRIGGER trg_place_resolutions_no_delete
BEFORE DELETE ON place_resolutions
BEGIN
    SELECT RAISE(ABORT, 'place resolutions are immutable machine products');
END;

CREATE TRIGGER trg_place_resolution_candidates_no_update
BEFORE UPDATE ON place_resolution_candidates
BEGIN
    SELECT RAISE(ABORT, 'place resolution candidates are immutable machine products');
END;

CREATE TRIGGER trg_place_resolution_candidates_no_delete
BEFORE DELETE ON place_resolution_candidates
BEGIN
    SELECT RAISE(ABORT, 'place resolution candidates are immutable machine products');
END;
