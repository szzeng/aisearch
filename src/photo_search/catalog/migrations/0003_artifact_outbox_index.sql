-- P2-03 durable processor artifacts, replayable index outbox and authoritative
-- confirmation rows. SQLite remains authoritative; LanceDB is always derived.

CREATE TABLE index_generations (
    generation_id TEXT PRIMARY KEY CHECK(generation_id <> ''),
    space_id       TEXT NOT NULL CHECK(space_id <> ''),
    processor_id   TEXT NOT NULL CHECK(processor_id <> ''),
    dimension      INTEGER NOT NULL CHECK(dimension > 0),
    state          TEXT NOT NULL CHECK(state IN ('PENDING', 'READY', 'RETIRED')),
    is_active      INTEGER NOT NULL DEFAULT 0 CHECK(is_active IN (0, 1)),
    created_at     INTEGER NOT NULL CHECK(created_at >= 0),
    updated_at     INTEGER NOT NULL CHECK(updated_at >= 0),
    CHECK(is_active = 0 OR state = 'READY'),
    UNIQUE(generation_id, space_id, processor_id)
);

CREATE UNIQUE INDEX uq_index_generations_active_space
    ON index_generations(space_id) WHERE is_active = 1;
CREATE INDEX idx_index_generations_space_state
    ON index_generations(space_id, state, updated_at);

CREATE TABLE artifacts (
    artifact_id       TEXT PRIMARY KEY CHECK(artifact_id <> ''),
    processor_id      TEXT NOT NULL CHECK(processor_id <> ''),
    input_revision    TEXT NOT NULL CHECK(input_revision <> ''),
    artifact_type     TEXT NOT NULL
        CHECK(artifact_type IN ('embedding', 'structured', 'binary')),
    artifact_revision TEXT NOT NULL CHECK(artifact_revision <> ''),
    media_type        TEXT NOT NULL CHECK(media_type <> ''),
    checksum_sha256   TEXT NOT NULL
        CHECK(length(checksum_sha256) = 64 AND checksum_sha256 NOT GLOB '*[^0-9a-f]*'),
    byte_size         INTEGER NOT NULL CHECK(byte_size >= 0),
    storage_uri       TEXT NOT NULL
        CHECK(storage_uri <> '' AND substr(storage_uri, 1, 1) <> '/'
              AND substr(storage_uri, 1, 1) <> char(92)
              AND instr(storage_uri, ':') = 0 AND instr(storage_uri, '..') = 0),
    space_id          TEXT,
    dimension         INTEGER,
    job_id            INTEGER NOT NULL REFERENCES jobs(job_id) ON DELETE RESTRICT,
    lifecycle_state   TEXT NOT NULL
        CHECK(lifecycle_state IN ('PENDING', 'PUBLISHED', 'RECLAIMED')),
    created_at        INTEGER NOT NULL CHECK(created_at >= 0),
    updated_at        INTEGER NOT NULL CHECK(updated_at >= 0),
    CHECK(
        (artifact_type = 'embedding' AND space_id IS NOT NULL AND space_id <> ''
         AND dimension IS NOT NULL AND dimension > 0)
        OR
        (artifact_type IN ('structured', 'binary') AND space_id IS NULL AND dimension IS NULL)
    ),
    UNIQUE(processor_id, input_revision, artifact_type, artifact_revision)
);

CREATE INDEX idx_artifacts_job ON artifacts(job_id);
CREATE INDEX idx_artifacts_lifecycle ON artifacts(lifecycle_state, updated_at);
CREATE INDEX idx_artifacts_space ON artifacts(space_id, lifecycle_state);

CREATE TABLE index_outbox (
    event_id             TEXT PRIMARY KEY CHECK(event_id <> ''),
    event_type           TEXT NOT NULL CHECK(event_type IN ('UPSERT_VECTOR', 'DELETE_VECTOR')),
    artifact_id          TEXT NOT NULL REFERENCES artifacts(artifact_id) ON DELETE RESTRICT,
    space_id             TEXT NOT NULL CHECK(space_id <> ''),
    asset_id             INTEGER NOT NULL REFERENCES assets(asset_id) ON DELETE RESTRICT,
    generation_id        TEXT NOT NULL REFERENCES index_generations(generation_id) ON DELETE RESTRICT,
    projection_revision  INTEGER NOT NULL CHECK(projection_revision >= 0),
    payload_json         TEXT NOT NULL CHECK(json_valid(payload_json)),
    payload_checksum     TEXT NOT NULL
        CHECK(length(payload_checksum) = 64 AND payload_checksum NOT GLOB '*[^0-9a-f]*'),
    state                TEXT NOT NULL CHECK(state IN ('PENDING', 'IN_FLIGHT', 'CONFIRMED')),
    attempts             INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    last_error_code      TEXT,
    created_at           INTEGER NOT NULL CHECK(created_at >= 0),
    updated_at           INTEGER NOT NULL CHECK(updated_at >= 0),
    UNIQUE(space_id, asset_id, generation_id, projection_revision, event_type)
);

CREATE INDEX idx_index_outbox_pending
    ON index_outbox(state, created_at, event_id);
CREATE INDEX idx_index_outbox_entity
    ON index_outbox(space_id, asset_id, generation_id, projection_revision);

CREATE TABLE index_entries (
    space_id              TEXT NOT NULL CHECK(space_id <> ''),
    asset_id              INTEGER NOT NULL REFERENCES assets(asset_id) ON DELETE RESTRICT,
    generation_id         TEXT NOT NULL REFERENCES index_generations(generation_id) ON DELETE RESTRICT,
    artifact_id           TEXT NOT NULL REFERENCES artifacts(artifact_id) ON DELETE RESTRICT,
    confirmation_revision INTEGER NOT NULL CHECK(confirmation_revision >= 0),
    confirmed_at          INTEGER,
    state                 TEXT NOT NULL
        CHECK(state IN ('CONFIRMED', 'STALE', 'PENDING_REPAIR')),
    CHECK((state = 'CONFIRMED' AND confirmed_at IS NOT NULL)
          OR (state <> 'CONFIRMED' AND confirmed_at IS NULL)),
    PRIMARY KEY(space_id, asset_id, generation_id)
);

CREATE INDEX idx_index_entries_state ON index_entries(state, generation_id);
