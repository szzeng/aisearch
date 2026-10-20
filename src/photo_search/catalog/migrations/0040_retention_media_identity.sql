-- P4-LIBRARY-RETENTION-C2-E: preserve derived-media identities after the asset is removed.
-- Older completed barriers have no recoverable SHA/face crop identity and remain protected.
ALTER TABLE asset_retention_barriers ADD COLUMN media_sha256 TEXT
    CHECK (media_sha256 IS NULL OR (length(media_sha256) = 64 AND media_sha256 NOT GLOB '*[^0-9a-f]*'));
ALTER TABLE asset_retention_barriers ADD COLUMN face_crop_keys_json TEXT NOT NULL DEFAULT '[]'
    CHECK (json_valid(face_crop_keys_json));
ALTER TABLE asset_retention_barriers ADD COLUMN media_cleaned_at_ms INTEGER
    CHECK (media_cleaned_at_ms IS NULL OR media_cleaned_at_ms >= 0);
ALTER TABLE asset_retention_barriers ADD COLUMN media_deleted_files INTEGER NOT NULL DEFAULT 0
    CHECK (media_deleted_files >= 0);
ALTER TABLE asset_retention_barriers ADD COLUMN media_freed_bytes INTEGER NOT NULL DEFAULT 0
    CHECK (media_freed_bytes >= 0);
ALTER TABLE asset_retention_barriers ADD COLUMN artifact_metadata_cleaned_at_ms INTEGER
    CHECK (artifact_metadata_cleaned_at_ms IS NULL OR artifact_metadata_cleaned_at_ms >= 0);
ALTER TABLE asset_retention_barriers ADD COLUMN artifact_rows_deleted INTEGER NOT NULL DEFAULT 0
    CHECK (artifact_rows_deleted >= 0);
ALTER TABLE asset_retention_barriers ADD COLUMN jobs_deleted INTEGER NOT NULL DEFAULT 0
    CHECK (jobs_deleted >= 0);
