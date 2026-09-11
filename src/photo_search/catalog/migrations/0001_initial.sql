-- P1-01 initial catalog schema (ADR-0002 §3).
-- These tables, columns, unique keys and CHECK constraints are the frozen P1-01
-- contract. Later migrations may add tables or non-unique indexes; they must not
-- change the identity or uniqueness semantics below in place.
-- The runner wraps this script, and the schema_version bump, in one transaction.

CREATE TABLE metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE scan_runs (
    scan_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id   INTEGER NOT NULL REFERENCES source_roots(source_id) ON DELETE RESTRICT,
    started_at  INTEGER,
    finished_at INTEGER,
    state       TEXT NOT NULL CHECK(state IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'BLOCKED', 'FAILED', 'CANCELLED')),
    error       TEXT
);

CREATE TABLE source_roots (
    source_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name         TEXT NOT NULL CHECK(display_name <> ''),
    root_path            TEXT NOT NULL CHECK(root_path <> ''),
    volume_hint          TEXT,
    status               TEXT NOT NULL CHECK(status IN ('ONLINE', 'OFFLINE', 'ERROR')),
    last_successful_scan INTEGER REFERENCES scan_runs(scan_id) ON DELETE RESTRICT
);

CREATE TABLE assets (
    asset_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256     TEXT NOT NULL UNIQUE CHECK(length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
    byte_size  INTEGER NOT NULL CHECK(byte_size >= 0),
    width      INTEGER,
    height     INTEGER,
    format     TEXT,
    created_at INTEGER NOT NULL,
    state      TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(state = 'ACTIVE')
);

CREATE TABLE file_locations (
    location_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id         INTEGER NOT NULL REFERENCES source_roots(source_id) ON DELETE RESTRICT,
    relative_path     TEXT NOT NULL CHECK(relative_path <> ''),
    path_key          TEXT NOT NULL CHECK(path_key <> ''),
    asset_id          INTEGER REFERENCES assets(asset_id) ON DELETE RESTRICT,
    size              INTEGER NOT NULL CHECK(size >= 0),
    mtime_ns          INTEGER NOT NULL CHECK(mtime_ns >= 0),
    last_seen_scan_id INTEGER REFERENCES scan_runs(scan_id) ON DELETE RESTRICT,
    availability      TEXT NOT NULL CHECK(availability IN ('PRESENT', 'MISSING')),
    UNIQUE(source_id, path_key)
);

CREATE TABLE jobs (
    job_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_type       TEXT NOT NULL CHECK(subject_type <> ''),
    subject_id         TEXT NOT NULL CHECK(subject_id <> ''),
    input_revision     TEXT NOT NULL CHECK(input_revision <> ''),
    task_type          TEXT NOT NULL CHECK(task_type <> ''),
    processor_id       TEXT NOT NULL CHECK(processor_id <> ''),
    state              TEXT NOT NULL CHECK(state IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'RETRY_WAIT', 'BLOCKED', 'FAILED', 'CANCELLED')),
    priority           INTEGER NOT NULL DEFAULT 0,
    attempts           INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    max_attempts       INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts > 0),
    run_after          INTEGER NOT NULL,
    lease_token        TEXT,
    lease_expires_at   INTEGER,
    worker_id          TEXT,
    progress           REAL NOT NULL DEFAULT 0.0 CHECK(progress >= 0.0 AND progress <= 1.0),
    last_error_code    TEXT,
    last_error_message TEXT,
    created_at         INTEGER NOT NULL,
    updated_at         INTEGER NOT NULL,
    UNIQUE(subject_type, subject_id, input_revision, task_type, processor_id),
    CHECK(state = 'RUNNING' OR (lease_token IS NULL AND lease_expires_at IS NULL AND worker_id IS NULL))
);

-- A source may have at most one non-terminal scan request (ADR-0002 §3.3).
CREATE UNIQUE INDEX idx_scan_runs_active_source
    ON scan_runs(source_id)
    WHERE state IN ('QUEUED', 'RUNNING', 'BLOCKED');

CREATE INDEX idx_scan_runs_source_state ON scan_runs(source_id, state);
CREATE INDEX idx_file_locations_asset ON file_locations(asset_id);
CREATE INDEX idx_file_locations_source_seen ON file_locations(source_id, last_seen_scan_id);
CREATE INDEX idx_jobs_schedulable ON jobs(state, run_after, priority DESC);
CREATE INDEX idx_jobs_subject ON jobs(subject_type, subject_id);
