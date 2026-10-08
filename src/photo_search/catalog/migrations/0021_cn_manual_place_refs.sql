-- A versioned bridge from immutable OSM admin identities to legacy Place IDs.
-- The proxy Place is only an FK carrier for ADR-0005 assignment/history; its kind
-- does not claim the administrative type. cn_dataset_regions remains that authority.
CREATE TABLE cn_admin_place_refs (
    admin_id INTEGER PRIMARY KEY REFERENCES cn_admin_ids(admin_id) ON DELETE RESTRICT,
    place_id INTEGER NOT NULL UNIQUE REFERENCES places(place_id) ON DELETE RESTRICT
);

-- Bind each legacy GeoNames mapping to the exact package which proved it.
-- Historical provider/external_id mappings alone may outlive a package switch.
CREATE TABLE geo_dataset_place_refs (
    dataset_id TEXT NOT NULL REFERENCES geo_datasets(dataset_id) ON DELETE RESTRICT,
    provider TEXT NOT NULL,
    external_id TEXT NOT NULL,
    place_id INTEGER NOT NULL REFERENCES places(place_id) ON DELETE RESTRICT,
    PRIMARY KEY(dataset_id,provider,external_id),
    UNIQUE(dataset_id,place_id)
);
CREATE INDEX idx_geo_dataset_place_refs_place ON geo_dataset_place_refs(place_id,dataset_id);
