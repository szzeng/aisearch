CREATE TABLE photo_smart_collections (
    smart_id INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_token TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL UNIQUE,
    rule_version INTEGER NOT NULL CHECK(rule_version >= 1),
    rule_json TEXT NOT NULL,
    rule_hash TEXT NOT NULL,
    execution_kind TEXT NOT NULL CHECK(execution_kind IN ('FILTER','TEXT','COMBINED')),
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    rule_revision INTEGER NOT NULL DEFAULT 1 CHECK(rule_revision >= 1),
    exclusions_revision INTEGER NOT NULL DEFAULT 0 CHECK(exclusions_revision >= 0),
    create_request_id TEXT NOT NULL UNIQUE,
    create_request_hash TEXT NOT NULL,
    create_auth_hash TEXT NOT NULL,
    create_source_hash TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms >= 0)
);
CREATE INDEX idx_photo_smart_collections_recent
    ON photo_smart_collections(updated_at_ms DESC, smart_id DESC);

CREATE TABLE photo_smart_exclusions (
    smart_id INTEGER NOT NULL REFERENCES photo_smart_collections(smart_id) ON DELETE CASCADE,
    asset_id INTEGER NOT NULL REFERENCES assets(asset_id) ON DELETE RESTRICT,
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    PRIMARY KEY(smart_id, asset_id)
);
CREATE INDEX idx_photo_smart_exclusions_page
    ON photo_smart_exclusions(smart_id, created_at_ms DESC, asset_id DESC);
CREATE INDEX idx_photo_smart_exclusions_asset
    ON photo_smart_exclusions(asset_id, smart_id);

CREATE TABLE photo_smart_cache_state (
    smart_id INTEGER PRIMARY KEY REFERENCES photo_smart_collections(smart_id) ON DELETE CASCADE,
    build_id TEXT,
    dependency_signature TEXT,
    cache_rule_revision INTEGER,
    candidate_count INTEGER NOT NULL DEFAULT 0 CHECK(candidate_count >= 0 AND candidate_count <= 5000),
    truncated INTEGER NOT NULL DEFAULT 0 CHECK(truncated IN (0,1)),
    indexing INTEGER NOT NULL DEFAULT 0 CHECK(indexing IN (0,1)),
    refreshed_at_ms INTEGER,
    last_error_code TEXT,
    lease_owner TEXT,
    lease_until_ms INTEGER,
    refresh_request_id TEXT,
    refresh_status TEXT NOT NULL DEFAULT 'NO_CACHE'
        CHECK(refresh_status IN ('NO_CACHE','QUEUED','RUNNING','DEFERRED','FAILED'))
);

CREATE TABLE photo_smart_cache_entries (
    smart_id INTEGER NOT NULL REFERENCES photo_smart_collections(smart_id) ON DELETE CASCADE,
    build_id TEXT NOT NULL,
    rank INTEGER NOT NULL CHECK(rank >= 1 AND rank <= 5000),
    asset_id INTEGER NOT NULL REFERENCES assets(asset_id) ON DELETE CASCADE,
    PRIMARY KEY(smart_id, build_id, rank),
    UNIQUE(smart_id, build_id, asset_id)
);
CREATE INDEX idx_photo_smart_cache_asset
    ON photo_smart_cache_entries(asset_id, smart_id);

CREATE TABLE photo_smart_rule_replacements (
    request_id TEXT PRIMARY KEY,
    smart_id INTEGER NOT NULL REFERENCES photo_smart_collections(smart_id) ON DELETE CASCADE,
    auth_hash TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
);
CREATE INDEX idx_photo_smart_rule_replacements_recent
    ON photo_smart_rule_replacements(smart_id, created_at_ms DESC);

INSERT INTO metadata(key, value) VALUES ('photo_smart_collections_revision', '0')
    ON CONFLICT(key) DO NOTHING;
INSERT INTO metadata(key, value) VALUES ('photo_smart_fts_publish_revision', '0')
    ON CONFLICT(key) DO NOTHING;
CREATE TABLE photo_smart_vector_publish_revisions (
    generation_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL CHECK(revision >= 0)
);
