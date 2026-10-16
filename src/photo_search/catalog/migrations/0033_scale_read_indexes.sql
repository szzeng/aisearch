-- Additive access paths for 300k catalogs. No rows, identities or state transitions
-- change; confirmed outbox history stays durable without burdening READY checks.
CREATE INDEX idx_assets_created_id ON assets(created_at DESC, asset_id DESC);
CREATE INDEX idx_file_locations_present_source_asset
    ON file_locations(source_id, asset_id, location_id)
    WHERE availability='PRESENT' AND asset_id IS NOT NULL;
CREATE INDEX idx_file_locations_present_asset
    ON file_locations(asset_id, location_id)
    WHERE availability='PRESENT' AND asset_id IS NOT NULL;
CREATE INDEX idx_index_outbox_unconfirmed_generation
    ON index_outbox(generation_id)
    WHERE state <> 'CONFIRMED';
