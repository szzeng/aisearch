-- P4-LIBRARY-RETENTION-C2-FIX: bound each persisted batch while allowing one confirmed
-- run to traverse its click-time candidate range across arbitrarily many batches.
ALTER TABLE library_retention_runs ADD COLUMN selection_max_asset_id INTEGER NOT NULL DEFAULT 0
    CHECK (selection_max_asset_id >= 0);
ALTER TABLE library_retention_runs ADD COLUMN selection_cutoff_at_ms INTEGER NOT NULL DEFAULT 0
    CHECK (selection_cutoff_at_ms >= 0);
ALTER TABLE library_retention_runs ADD COLUMN selection_after_asset_id INTEGER NOT NULL DEFAULT 0
    CHECK (selection_after_asset_id >= 0);
ALTER TABLE library_retention_runs ADD COLUMN checked_assets INTEGER NOT NULL DEFAULT 0
    CHECK (checked_assets >= 0);
ALTER TABLE library_retention_runs ADD COLUMN skipped_human_assets INTEGER NOT NULL DEFAULT 0
    CHECK (skipped_human_assets >= 0);
ALTER TABLE library_retention_runs ADD COLUMN skipped_source_assets INTEGER NOT NULL DEFAULT 0
    CHECK (skipped_source_assets >= 0);
ALTER TABLE library_retention_runs ADD COLUMN skipped_other_assets INTEGER NOT NULL DEFAULT 0
    CHECK (skipped_other_assets >= 0);
ALTER TABLE library_retention_runs ADD COLUMN has_more_candidates INTEGER NOT NULL DEFAULT 0
    CHECK (has_more_candidates IN (0,1));

ALTER TABLE library_retention_run_items ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0
    CHECK (attempt_count >= 0);
ALTER TABLE library_retention_run_items ADD COLUMN outcome_category TEXT NOT NULL DEFAULT 'OTHER'
    CHECK (outcome_category IN ('HUMAN','SOURCE','OTHER'));

CREATE INDEX idx_library_retention_runs_resume
    ON library_retention_runs(state, mode, updated_at_ms);

-- Retain the click-time identity set while facts are rechecked at execution time.
-- This table stores only integer identities in SQLite; workers still materialize at
-- most one bounded batch into run_items.
CREATE TABLE library_retention_run_candidates (
    run_id TEXT NOT NULL REFERENCES library_retention_runs(run_id) ON DELETE CASCADE,
    asset_id INTEGER NOT NULL CHECK (asset_id > 0),
    PRIMARY KEY (run_id, asset_id)
);

-- Schema 41 runs already have a bounded current item batch. Seed that authorized
-- identity set so an in-flight run can resume under the new cursor protocol. Clear
-- aggregates so settling those durable items reconstructs the counters exactly once.
INSERT INTO library_retention_run_candidates(run_id, asset_id)
SELECT i.run_id, i.asset_id
  FROM library_retention_run_items i
  JOIN library_retention_runs r USING(run_id)
 WHERE r.state IN ('QUEUED','RUNNING','PARTIAL');

UPDATE library_retention_runs
   SET selection_max_asset_id=COALESCE((SELECT MAX(c.asset_id)
                                         FROM library_retention_run_candidates c
                                        WHERE c.run_id=library_retention_runs.run_id),0),
       selection_cutoff_at_ms=updated_at_ms,
       selection_after_asset_id=0,
       total_assets=0,completed_assets=0,skipped_assets=0,failed_assets=0,
       checked_assets=0,skipped_human_assets=0,skipped_source_assets=0,skipped_other_assets=0,
       has_more_candidates=EXISTS(SELECT 1 FROM library_retention_run_candidates c
                                   WHERE c.run_id=library_retention_runs.run_id)
 WHERE state IN ('QUEUED','RUNNING','PARTIAL');
