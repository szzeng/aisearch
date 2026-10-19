CREATE TABLE asset_favorites (
    asset_id INTEGER PRIMARY KEY
        REFERENCES assets(asset_id) ON DELETE RESTRICT,
    added_at_ms INTEGER NOT NULL CHECK (added_at_ms >= 0)
);
CREATE INDEX idx_asset_favorites_recent
    ON asset_favorites(added_at_ms DESC, asset_id DESC);
INSERT INTO metadata(key, value) VALUES ('favorites_revision', '0')
    ON CONFLICT(key) DO NOTHING;
