-- Keep stable person operation identity and a compact, replayable history receipt while
-- removing only the replaceable before/after snapshots from expired operations.
CREATE TABLE person_operation_retention (
    operation_id       TEXT PRIMARY KEY REFERENCES person_operations(operation_id) ON DELETE RESTRICT,
    expired_at         INTEGER NOT NULL CHECK (expired_at >= 0),
    summary_json       TEXT NOT NULL CHECK (json_valid(summary_json)),
    replay_json        TEXT NOT NULL CHECK (json_valid(replay_json)),
    item_count         INTEGER NOT NULL CHECK (item_count > 0),
    snapshot_bytes     INTEGER NOT NULL CHECK (snapshot_bytes > 0),
    receipt_bytes      INTEGER NOT NULL CHECK (receipt_bytes > 0),
    CHECK (snapshot_bytes > receipt_bytes)
);
