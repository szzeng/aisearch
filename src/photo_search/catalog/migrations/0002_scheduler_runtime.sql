-- P2-02 persistent scheduler, device qualification and runtime capability facts.
-- Additive to the frozen ADR-0002 §3.6 jobs contract: the five-field logical identity,
-- the seven-state machine, the lease columns and every existing CHECK stay untouched.
-- Everything the scheduler writes is either a nullable dispatch snapshot on the job row
-- (never part of the unique key) or an immutable runtime fact.

CREATE TABLE runtime_capabilities (
    capability_revision     TEXT PRIMARY KEY CHECK(capability_revision <> ''),
    contract_version        TEXT NOT NULL CHECK(contract_version = 'p2-contracts.v1'),
    runtime_package_id      TEXT NOT NULL CHECK(runtime_package_id <> ''),
    runtime_backend         TEXT NOT NULL CHECK(runtime_backend <> ''),
    runtime_package_name    TEXT NOT NULL CHECK(runtime_package_name <> ''),
    runtime_package_version TEXT NOT NULL CHECK(runtime_package_version <> ''),
    python_version          TEXT NOT NULL CHECK(python_version <> ''),
    platform                TEXT NOT NULL CHECK(platform <> ''),
    driver_version          TEXT,
    health_status           TEXT NOT NULL
        CHECK(health_status IN ('healthy', 'degraded', 'unhealthy', 'unknown')),
    health_checked_at       INTEGER NOT NULL CHECK(health_checked_at >= 0),
    measured_at             INTEGER NOT NULL CHECK(measured_at >= 0),
    evidence_ids_json       TEXT NOT NULL CHECK(json_valid(evidence_ids_json)),
    normalized_json         TEXT NOT NULL CHECK(json_valid(normalized_json)),
    checksum_sha256         TEXT NOT NULL
        CHECK(length(checksum_sha256) = 64 AND checksum_sha256 NOT GLOB '*[^0-9a-f]*'),
    recorded_at             INTEGER NOT NULL CHECK(recorded_at >= 0)
);

-- A capability snapshot is immutable history: recovery records a new revision instead of
-- rewriting the old one, so an old dispatch stays explainable (ADR-0003 §6).
CREATE TRIGGER trg_runtime_capabilities_no_update
BEFORE UPDATE ON runtime_capabilities
BEGIN
    SELECT RAISE(ABORT, 'runtime capability snapshots are immutable');
END;

CREATE TRIGGER trg_runtime_capabilities_no_delete
BEFORE DELETE ON runtime_capabilities
BEGIN
    SELECT RAISE(ABORT, 'runtime capability snapshots are immutable');
END;

-- Per-processor device/precision acceptance evidence. A healthy provider alone never
-- qualifies a processor (ADR-0003 §5); the evidence row is what proves the specific
-- processor/device/precision/runtime combination was actually accepted.
CREATE TABLE runtime_qualification_evidence (
    evidence_id        TEXT PRIMARY KEY CHECK(evidence_id <> ''),
    processor_id       TEXT NOT NULL CHECK(processor_id <> ''),
    spec_revision      TEXT NOT NULL CHECK(spec_revision <> ''),
    device             TEXT NOT NULL CHECK(device IN ('cpu', 'gpu')),
    precision          TEXT NOT NULL CHECK(precision <> ''),
    provider_name      TEXT NOT NULL CHECK(provider_name <> ''),
    runtime_package_id TEXT NOT NULL CHECK(runtime_package_id <> ''),
    verified_at        INTEGER NOT NULL CHECK(verified_at >= 0),
    note               TEXT
);

CREATE INDEX idx_runtime_qualification_lookup
    ON runtime_qualification_evidence(processor_id, spec_revision, device, precision);

-- Singleton scheduler policy row (ADR-0003 §2: pause is a scheduler strategy, not a job
-- state). ``aging_window_ms`` is persisted so the fairness rule survives a restart
-- unchanged instead of silently reverting to a build default.
CREATE TABLE scheduler_state (
    state_id        INTEGER PRIMARY KEY CHECK(state_id = 1),
    paused          INTEGER NOT NULL CHECK(paused IN (0, 1)),
    revision        INTEGER NOT NULL CHECK(revision >= 0),
    aging_window_ms INTEGER NOT NULL CHECK(aging_window_ms > 0),
    updated_at      INTEGER NOT NULL CHECK(updated_at >= 0)
);

INSERT INTO scheduler_state (state_id, paused, revision, aging_window_ms, updated_at)
VALUES (1, 0, 1, 60000, 0);

ALTER TABLE jobs ADD COLUMN requested_device TEXT NOT NULL DEFAULT 'auto'
    CHECK(requested_device IN ('auto', 'cpu', 'gpu'));
ALTER TABLE jobs ADD COLUMN resolved_device TEXT
    CHECK(resolved_device IS NULL OR resolved_device IN ('cpu', 'gpu'));
ALTER TABLE jobs ADD COLUMN runtime_provider TEXT
    CHECK(runtime_provider IS NULL OR runtime_provider <> '');
ALTER TABLE jobs ADD COLUMN precision TEXT
    CHECK(precision IS NULL OR precision <> '');
ALTER TABLE jobs ADD COLUMN runtime_capability_revision TEXT
    REFERENCES runtime_capabilities(capability_revision) ON DELETE RESTRICT;
ALTER TABLE jobs ADD COLUMN dispatch_spec_revision TEXT
    CHECK(dispatch_spec_revision IS NULL OR dispatch_spec_revision <> '');
ALTER TABLE jobs ADD COLUMN blocking_reason TEXT
    CHECK(blocking_reason IS NULL OR blocking_reason <> '');

CREATE INDEX idx_jobs_processor_state ON jobs(processor_id, state, run_after);
