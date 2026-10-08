-- ADR-0016: independent versioned, derived administrative facets. Frozen Place,
-- manual controls, GeoFacts, datasets and search protocols are unchanged.
CREATE TABLE cn_admin_ids (
    admin_id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id TEXT NOT NULL UNIQUE CHECK(external_id GLOB 'relation/[0-9]*')
);
CREATE TABLE cn_dataset_regions (
    dataset_id TEXT NOT NULL REFERENCES geo_datasets(dataset_id) ON DELETE RESTRICT,
    admin_id INTEGER NOT NULL REFERENCES cn_admin_ids(admin_id) ON DELETE RESTRICT,
    kind TEXT NOT NULL CHECK(kind IN ('province','city','district')),
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 200),
    aliases_json TEXT NOT NULL CHECK(json_valid(aliases_json)),
    province_id INTEGER REFERENCES cn_admin_ids(admin_id) ON DELETE RESTRICT,
    city_id INTEGER REFERENCES cn_admin_ids(admin_id) ON DELETE RESTRICT,
    PRIMARY KEY(dataset_id,admin_id),
    CHECK((kind='province' AND province_id IS NULL AND city_id IS NULL)
       OR (kind='city' AND province_id IS NOT NULL AND city_id IS NULL)
       OR (kind='district' AND province_id IS NOT NULL))
);
CREATE INDEX idx_cn_regions_kind ON cn_dataset_regions(dataset_id,kind,admin_id);
CREATE TABLE cn_base_china_places (
    dataset_id TEXT NOT NULL REFERENCES geo_datasets(dataset_id) ON DELETE RESTRICT,
    place_id INTEGER NOT NULL REFERENCES places(place_id) ON DELETE RESTRICT,
    PRIMARY KEY(dataset_id,place_id)
);
CREATE TABLE cn_legacy_links (
    dataset_id TEXT NOT NULL REFERENCES geo_datasets(dataset_id) ON DELETE RESTRICT,
    place_id INTEGER NOT NULL REFERENCES places(place_id) ON DELETE RESTRICT,
    province_id INTEGER NOT NULL REFERENCES cn_admin_ids(admin_id) ON DELETE RESTRICT,
    city_id INTEGER REFERENCES cn_admin_ids(admin_id) ON DELETE RESTRICT,
    PRIMARY KEY(dataset_id,place_id)
);
CREATE TABLE cn_admin_resolutions (
    asset_id INTEGER NOT NULL REFERENCES assets(asset_id) ON DELETE RESTRICT,
    dataset_id TEXT NOT NULL REFERENCES geo_datasets(dataset_id) ON DELETE RESTRICT,
    geo_fact_id TEXT NOT NULL CHECK(geo_fact_id<>''),
    input_revision TEXT NOT NULL CHECK(length(input_revision)=64 AND input_revision NOT GLOB '*[^0-9a-f]*'),
    processor_id TEXT NOT NULL CHECK(processor_id='geo-cn-admin@1'),
    province_id INTEGER REFERENCES cn_admin_ids(admin_id) ON DELETE RESTRICT,
    city_id INTEGER REFERENCES cn_admin_ids(admin_id) ON DELETE RESTRICT,
    district_id INTEGER REFERENCES cn_admin_ids(admin_id) ON DELETE RESTRICT,
    reason TEXT NOT NULL CHECK(reason IN ('MATCHED','UPPER_ONLY','UNKNOWN','BOUNDARY_OR_OVERLAP')),
    created_at INTEGER NOT NULL CHECK(created_at>=0),
    PRIMARY KEY(dataset_id,asset_id,geo_fact_id,input_revision,processor_id),
    CHECK((province_id IS NOT NULL OR (city_id IS NULL AND district_id IS NULL)))
);
CREATE INDEX idx_cn_resolution_province ON cn_admin_resolutions(dataset_id,province_id,asset_id);
CREATE INDEX idx_cn_resolution_city ON cn_admin_resolutions(dataset_id,city_id,asset_id);
CREATE INDEX idx_cn_resolution_district ON cn_admin_resolutions(dataset_id,district_id,asset_id);
CREATE TRIGGER trg_cn_resolution_immutable BEFORE UPDATE ON cn_admin_resolutions
BEGIN SELECT RAISE(ABORT,'CN administrative resolutions are immutable'); END;
CREATE TABLE cn_update_runs (
    dataset_id TEXT PRIMARY KEY REFERENCES geo_datasets(dataset_id) ON DELETE RESTRICT,
    after_asset_id INTEGER NOT NULL CHECK(after_asset_id>=0),
    upper_asset_id INTEGER NOT NULL CHECK(upper_asset_id>=0),
    scanned INTEGER NOT NULL CHECK(scanned>=0),
    processed INTEGER NOT NULL CHECK(processed>=0),
    complete INTEGER NOT NULL CHECK(complete IN (0,1)),
    generation INTEGER NOT NULL CHECK(generation>=1)
);
