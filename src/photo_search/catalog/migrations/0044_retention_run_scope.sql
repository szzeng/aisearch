-- Preserve the explicit manual authorization across crashes and restarts.
ALTER TABLE library_retention_runs ADD COLUMN cleanup_scope TEXT NOT NULL DEFAULT 'MISSING'
    CHECK (cleanup_scope IN ('MISSING','ZERO_LOCATION'));
-- Bounded per-asset protection probes in zero-location previews and execution.
CREATE INDEX idx_retention_face_detail_asset_state ON face_detail_batch_assets(asset_id, state);
CREATE INDEX idx_retention_person_avatar ON persons(avatar_face_id);
CREATE INDEX idx_retention_redetection_groups_asset ON face_redetection_groups(asset_id);
CREATE INDEX idx_retention_redetection_mappings_asset ON face_redetection_mappings(asset_id);
CREATE INDEX idx_retention_outbox_asset_event_state ON index_outbox(asset_id, event_type, state);
CREATE INDEX idx_retention_entries_asset ON index_entries(asset_id);
