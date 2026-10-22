-- Optional, versioned machine labels. They never enter manual facts or OCR/FTS.
-- Integer model keys and one packed payload avoid millions of per-label SQL rows.
CREATE TABLE machine_tag_models (
    model_key INTEGER PRIMARY KEY,
    processor_id TEXT NOT NULL UNIQUE CHECK(processor_id <> ''),
    model_id TEXT NOT NULL CHECK(model_id <> ''),
    policy_id TEXT NOT NULL CHECK(policy_id <> ''),
    manifest_sha256 TEXT NOT NULL
        CHECK(length(manifest_sha256)=64 AND manifest_sha256 NOT GLOB '*[^0-9a-f]*'),
    class_count INTEGER NOT NULL CHECK(class_count BETWEEN 1 AND 65535),
    max_scores INTEGER NOT NULL CHECK(max_scores BETWEEN 1 AND 64),
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
    completed_count INTEGER NOT NULL DEFAULT 0 CHECK(completed_count >= 0),
    score_count INTEGER NOT NULL DEFAULT 0 CHECK(score_count >= 0),
    created_at INTEGER NOT NULL CHECK(created_at >= 0),
    UNIQUE(model_id, policy_id)
);

CREATE TABLE machine_tag_results (
    model_key INTEGER NOT NULL REFERENCES machine_tag_models(model_key) ON DELETE RESTRICT,
    asset_id INTEGER NOT NULL REFERENCES assets(asset_id) ON DELETE CASCADE,
    input_revision TEXT NOT NULL
        CHECK(length(input_revision)=64 AND input_revision NOT GLOB '*[^0-9a-f]*'),
    job_id INTEGER NOT NULL REFERENCES jobs(job_id) ON DELETE RESTRICT,
    artifact_id TEXT NOT NULL CHECK(artifact_id <> ''),
    payload_sha256 TEXT NOT NULL
        CHECK(length(payload_sha256)=64 AND payload_sha256 NOT GLOB '*[^0-9a-f]*'),
    score_count INTEGER NOT NULL CHECK(score_count BETWEEN 0 AND 64),
    scores BLOB NOT NULL CHECK(typeof(scores)='blob' AND length(scores)=score_count*5),
    updated_at INTEGER NOT NULL CHECK(updated_at >= 0),
    PRIMARY KEY(model_key, asset_id)
) WITHOUT ROWID;
CREATE INDEX idx_machine_tag_results_asset ON machine_tag_results(asset_id, model_key);

-- Coverage and snapshot identity are changed atomically with a result, including GC.
CREATE TRIGGER trg_machine_tag_results_insert AFTER INSERT ON machine_tag_results
BEGIN
    UPDATE machine_tag_models SET revision=revision+1, completed_count=completed_count+1,
        score_count=score_count+new.score_count WHERE model_key=new.model_key;
END;
CREATE TRIGGER trg_machine_tag_results_delete AFTER DELETE ON machine_tag_results
BEGIN
    UPDATE machine_tag_models SET revision=revision+1, completed_count=completed_count-1,
        score_count=score_count-old.score_count WHERE model_key=old.model_key;
END;
CREATE TRIGGER trg_machine_tag_results_update AFTER UPDATE ON machine_tag_results
BEGIN
    UPDATE machine_tag_models SET revision=revision+1,
        score_count=score_count-old.score_count+new.score_count WHERE model_key=new.model_key;
END;

-- Read-only progress polling reads at most the seven existing job-state counters.
-- The shared queue remains the sole job authority; no second lease/state protocol.
CREATE TABLE machine_tag_job_counts (
    model_key INTEGER NOT NULL REFERENCES machine_tag_models(model_key) ON DELETE CASCADE,
    state TEXT NOT NULL CHECK(state IN
        ('QUEUED','RUNNING','SUCCEEDED','RETRY_WAIT','BLOCKED','FAILED','CANCELLED')),
    job_count INTEGER NOT NULL CHECK(job_count >= 0),
    PRIMARY KEY(model_key, state)
) WITHOUT ROWID;
CREATE TRIGGER trg_machine_tag_job_insert AFTER INSERT ON jobs
WHEN new.task_type='image_tagging'
BEGIN
    INSERT INTO machine_tag_job_counts(model_key,state,job_count)
        SELECT model_key,new.state,1 FROM machine_tag_models WHERE processor_id=new.processor_id
        ON CONFLICT(model_key,state) DO UPDATE SET job_count=job_count+1;
END;
CREATE TRIGGER trg_machine_tag_job_delete AFTER DELETE ON jobs
WHEN old.task_type='image_tagging'
BEGIN
    UPDATE machine_tag_job_counts SET job_count=job_count-1
        WHERE state=old.state AND model_key IN
        (SELECT model_key FROM machine_tag_models WHERE processor_id=old.processor_id);
END;
CREATE TRIGGER trg_machine_tag_job_update AFTER UPDATE OF state,processor_id,task_type ON jobs
WHEN old.state<>new.state OR old.processor_id<>new.processor_id OR old.task_type<>new.task_type
BEGIN
    UPDATE machine_tag_job_counts SET job_count=job_count-1
        WHERE old.task_type='image_tagging' AND state=old.state AND model_key IN
        (SELECT model_key FROM machine_tag_models WHERE processor_id=old.processor_id);
    INSERT INTO machine_tag_job_counts(model_key,state,job_count)
        SELECT model_key,new.state,1 FROM machine_tag_models
        WHERE new.task_type='image_tagging' AND processor_id=new.processor_id
        ON CONFLICT(model_key,state) DO UPDATE SET job_count=job_count+1;
END;
