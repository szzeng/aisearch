-- Persist user authorization, automatic-cleanup policy, and restart-visible cleanup progress.
CREATE TABLE library_retention_settings (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    automatic_enabled INTEGER NOT NULL DEFAULT 0 CHECK (automatic_enabled IN (0,1)),
    updated_at_ms INTEGER NOT NULL CHECK (updated_at_ms >= 0)
);
INSERT INTO library_retention_settings(singleton,automatic_enabled,updated_at_ms) VALUES (1,0,0);

CREATE TABLE library_retention_runs (
    run_id TEXT PRIMARY KEY,
    mode TEXT NOT NULL CHECK (mode IN ('MANUAL','AUTOMATIC')),
    state TEXT NOT NULL CHECK (state IN ('QUEUED','RUNNING','PARTIAL','COMPLETE','FAILED')),
    stage TEXT NOT NULL,
    total_assets INTEGER NOT NULL DEFAULT 0 CHECK (total_assets >= 0),
    completed_assets INTEGER NOT NULL DEFAULT 0 CHECK (completed_assets >= 0),
    skipped_assets INTEGER NOT NULL DEFAULT 0 CHECK (skipped_assets >= 0),
    failed_assets INTEGER NOT NULL DEFAULT 0 CHECK (failed_assets >= 0),
    logical_rows_removed INTEGER NOT NULL DEFAULT 0 CHECK (logical_rows_removed >= 0),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
    updated_at_ms INTEGER NOT NULL CHECK (updated_at_ms >= 0),
    last_error TEXT
);
CREATE UNIQUE INDEX idx_library_retention_one_active_run
    ON library_retention_runs(state) WHERE state IN ('QUEUED','RUNNING');

CREATE TABLE library_retention_run_items (
    run_id TEXT NOT NULL REFERENCES library_retention_runs(run_id) ON DELETE CASCADE,
    asset_id INTEGER NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('QUEUED','RUNNING','COMPLETE','SKIPPED','FAILED')),
    stage TEXT NOT NULL,
    reason TEXT,
    logical_rows_removed INTEGER NOT NULL DEFAULT 0 CHECK (logical_rows_removed >= 0),
    started_at_ms INTEGER,
    completed_at_ms INTEGER,
    PRIMARY KEY (run_id,asset_id)
);
CREATE INDEX idx_library_retention_run_items_state
    ON library_retention_run_items(run_id,state,asset_id);
