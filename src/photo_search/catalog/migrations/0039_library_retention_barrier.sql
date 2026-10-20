-- P4-LIBRARY-RETENTION-C2-B: durable asset barriers survive deletion of the asset row.
-- Barrier rows are tombstones. ACTIVE blocks cleanup-time publication; COMPLETE continues
-- blocking stale jobs, repairs and replays after the catalog asset has been removed.
CREATE TABLE asset_retention_barriers (
    asset_id INTEGER PRIMARY KEY CHECK (asset_id > 0),
    barrier_id TEXT NOT NULL UNIQUE CHECK (length(barrier_id) = 32),
    state TEXT NOT NULL CHECK (state IN ('ACTIVE', 'COMPLETE')),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
    updated_at_ms INTEGER NOT NULL CHECK (updated_at_ms >= created_at_ms)
);
