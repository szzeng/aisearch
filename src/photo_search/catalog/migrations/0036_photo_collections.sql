CREATE TABLE photo_collections (
    collection_id INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_token TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL UNIQUE,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    member_count INTEGER NOT NULL DEFAULT 0 CHECK (member_count >= 0),
    create_request_id TEXT NOT NULL UNIQUE,
    create_request_hash TEXT NOT NULL,
    create_auth_hash TEXT,
    create_source_token_hash TEXT,
    next_position INTEGER NOT NULL DEFAULT 1 CHECK (next_position >= 1),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
    updated_at_ms INTEGER NOT NULL CHECK (updated_at_ms >= 0)
);
CREATE INDEX idx_photo_collections_recent
    ON photo_collections(updated_at_ms DESC, collection_id DESC);
CREATE TABLE photo_collection_members (
    collection_id INTEGER NOT NULL REFERENCES photo_collections(collection_id) ON DELETE CASCADE,
    asset_id INTEGER NOT NULL REFERENCES assets(asset_id) ON DELETE RESTRICT,
    position INTEGER NOT NULL CHECK (position >= 1),
    added_at_ms INTEGER NOT NULL CHECK (added_at_ms >= 0),
    PRIMARY KEY (collection_id, asset_id),
    UNIQUE (collection_id, position)
);
CREATE INDEX idx_photo_collection_member_reverse
    ON photo_collection_members(asset_id, collection_id);
INSERT INTO metadata(key, value) VALUES ('photo_collections_revision', '0')
    ON CONFLICT(key) DO NOTHING;
CREATE TABLE photo_collection_search_appends (
    request_id TEXT PRIMARY KEY,
    collection_id INTEGER NOT NULL REFERENCES photo_collections(collection_id) ON DELETE CASCADE,
    instance_token TEXT NOT NULL,
    auth_session_hash TEXT NOT NULL,
    request_payload_hash TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0)
);
CREATE INDEX idx_photo_collection_search_appends_recent
    ON photo_collection_search_appends(collection_id, created_at_ms DESC);
