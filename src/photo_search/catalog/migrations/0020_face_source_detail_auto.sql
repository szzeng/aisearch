-- DETAIL-B completion is additive to the already usable schema-19 batch prototype.
-- Existing schema-19 catalogs must upgrade through the normal backup/migration route.
CREATE TABLE face_detail_coordinate_frames (
    artifact_id TEXT PRIMARY KEY,
    input_revision TEXT NOT NULL,
    width INTEGER NOT NULL CHECK(width>0),
    height INTEGER NOT NULL CHECK(height>0)
);
CREATE TABLE face_detail_auto_assets (
    asset_id INTEGER PRIMARY KEY REFERENCES assets(asset_id) ON DELETE CASCADE,
    input_revision TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    policy_revision TEXT NOT NULL,
    ready INTEGER NOT NULL DEFAULT 0 CHECK(ready IN (0,1)),
    updated_at INTEGER NOT NULL
);

-- A durable inference result may be referenced by several historical batches/scopes.
-- The job's own frozen logical unique identity still prevents duplicate inference.
DROP INDEX ux_face_detail_batch_asset_job;
CREATE INDEX ux_face_detail_batch_asset_job ON face_detail_batch_assets(job_id)
    WHERE job_id IS NOT NULL;
