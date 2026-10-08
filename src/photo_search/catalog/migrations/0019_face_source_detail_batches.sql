-- P4-PERSON-DETAIL-B: resumable one-click source-detail batches.
-- Keep the original face_association_queue contract (one normal-profile task per asset)
-- unchanged. Source-detail association jobs use a separate profile-keyed queue so a newer
-- profile can never overwrite a pending legacy/v2 pass for the same asset.
CREATE TABLE face_detail_association_queue (
    asset_id      INTEGER NOT NULL REFERENCES assets(asset_id) ON DELETE CASCADE,
    spec_revision TEXT NOT NULL CHECK(spec_revision <> ''),
    face_revision INTEGER NOT NULL CHECK(face_revision >= 1),
    queued_at     INTEGER NOT NULL CHECK(queued_at >= 0),
    PRIMARY KEY (asset_id, spec_revision)
);
CREATE INDEX idx_face_detail_association_queue_age
    ON face_detail_association_queue(spec_revision, queued_at, asset_id);

CREATE TABLE face_detail_batches (
    batch_id        TEXT PRIMARY KEY CHECK(length(batch_id) BETWEEN 1 AND 64),
    source_id       INTEGER REFERENCES source_roots(source_id) ON DELETE RESTRICT,
    upper_asset_id  INTEGER NOT NULL CHECK(upper_asset_id >= 0),
    after_asset_id  INTEGER NOT NULL DEFAULT 0 CHECK(after_asset_id >= 0),
    state           TEXT NOT NULL CHECK(state IN ('QUEUED','RUNNING','PAUSED','COMPLETE','PARTIAL_FAILED')),
    total           INTEGER NOT NULL DEFAULT 0 CHECK(total >= 0),
    succeeded       INTEGER NOT NULL DEFAULT 0 CHECK(succeeded >= 0),
    skipped         INTEGER NOT NULL DEFAULT 0 CHECK(skipped >= 0),
    failed          INTEGER NOT NULL DEFAULT 0 CHECK(failed >= 0),
    needs_review    INTEGER NOT NULL DEFAULT 0 CHECK(needs_review >= 0),
    created_at      INTEGER NOT NULL CHECK(created_at >= 0),
    updated_at      INTEGER NOT NULL CHECK(updated_at >= 0),
    CHECK(after_asset_id <= upper_asset_id OR upper_asset_id = 0)
);
CREATE INDEX idx_face_detail_batches_state
    ON face_detail_batches(state, updated_at, batch_id);
CREATE UNIQUE INDEX ux_face_detail_batches_active_scope
    ON face_detail_batches(COALESCE(source_id, -1))
    WHERE state IN ('QUEUED','RUNNING','PAUSED');

CREATE TABLE face_detail_batch_assets (
    batch_id       TEXT NOT NULL REFERENCES face_detail_batches(batch_id) ON DELETE CASCADE,
    asset_id       INTEGER NOT NULL REFERENCES assets(asset_id) ON DELETE RESTRICT,
    input_revision TEXT NOT NULL CHECK(length(input_revision) = 64 AND input_revision NOT GLOB '*[^0-9a-f]*'),
    job_id         INTEGER REFERENCES jobs(job_id) ON DELETE SET NULL,
    state          TEXT NOT NULL CHECK(state IN ('QUEUED','RUNNING','SUCCEEDED','SKIPPED','FAILED')),
    face_count     INTEGER NOT NULL DEFAULT 0 CHECK(face_count >= 0),
    review_count   INTEGER NOT NULL DEFAULT 0 CHECK(review_count >= 0),
    error_code     TEXT,
    updated_at     INTEGER NOT NULL CHECK(updated_at >= 0),
    PRIMARY KEY (batch_id, asset_id)
);
CREATE INDEX idx_face_detail_batch_assets_state
    ON face_detail_batch_assets(batch_id, state, asset_id);
CREATE UNIQUE INDEX ux_face_detail_batch_asset_job
    ON face_detail_batch_assets(job_id) WHERE job_id IS NOT NULL;
