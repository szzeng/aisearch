-- P4-LIBRARY-RETENTION-C2-A: trustworthy observations only from complete successful scans.
-- These rows are eligibility evidence; they never authorize deletion by themselves.
CREATE TABLE asset_retention_source_evidence (
    asset_id INTEGER NOT NULL REFERENCES assets(asset_id) ON DELETE RESTRICT,
    source_id INTEGER NOT NULL REFERENCES source_roots(source_id) ON DELETE CASCADE,
    scan_id INTEGER NOT NULL REFERENCES scan_runs(scan_id) ON DELETE RESTRICT,
    missing_confirmed_at_ms INTEGER NOT NULL CHECK (missing_confirmed_at_ms >= 0),
    PRIMARY KEY (asset_id, source_id)
);
CREATE INDEX idx_asset_retention_evidence_source_scan
    ON asset_retention_source_evidence(source_id, scan_id, asset_id);

CREATE TABLE asset_retention_state (
    asset_id INTEGER PRIMARY KEY REFERENCES assets(asset_id) ON DELETE RESTRICT,
    first_missing_at_ms INTEGER CHECK (first_missing_at_ms >= 0),
    last_qualified_at_ms INTEGER CHECK (last_qualified_at_ms >= 0),
    state TEXT NOT NULL CHECK (state IN ('UNKNOWN', 'OBSERVING', 'ELIGIBLE', 'BLOCKED')),
    updated_at_ms INTEGER NOT NULL CHECK (updated_at_ms >= 0),
    CHECK (state <> 'UNKNOWN' OR first_missing_at_ms IS NULL),
    CHECK (state <> 'OBSERVING' OR first_missing_at_ms IS NOT NULL)
);
CREATE INDEX idx_asset_retention_due
    ON asset_retention_state(state, first_missing_at_ms, asset_id);

CREATE TABLE retention_source_anomalies (
    source_id INTEGER NOT NULL REFERENCES source_roots(source_id) ON DELETE RESTRICT,
    scan_id INTEGER NOT NULL REFERENCES scan_runs(scan_id) ON DELETE RESTRICT,
    baseline_present_assets INTEGER NOT NULL CHECK (baseline_present_assets >= 0),
    newly_missing_assets INTEGER NOT NULL CHECK (newly_missing_assets >= 0),
    state TEXT NOT NULL CHECK (state IN ('NEEDS_REVIEW', 'CLEARED')),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
    resolved_at_ms INTEGER CHECK (resolved_at_ms >= 0),
    PRIMARY KEY (source_id, scan_id),
    CHECK ((state = 'NEEDS_REVIEW' AND resolved_at_ms IS NULL)
        OR (state = 'CLEARED' AND resolved_at_ms IS NOT NULL))
);
CREATE INDEX idx_retention_source_anomalies_state
    ON retention_source_anomalies(source_id, state, scan_id);
