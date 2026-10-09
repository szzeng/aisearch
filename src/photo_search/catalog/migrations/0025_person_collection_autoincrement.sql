-- Upgrade v24 collections without rewriting the frozen 0024 migration or losing members.
CREATE TABLE person_collections_v25 (
    collection_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 80),
    name_key TEXT NOT NULL UNIQUE CHECK(name_key <> ''),
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    created_at INTEGER NOT NULL CHECK(created_at >= 0),
    updated_at INTEGER NOT NULL CHECK(updated_at >= 0)
);

INSERT INTO person_collections_v25(
    collection_id, name, name_key, revision, created_at, updated_at
)
SELECT collection_id, name, name_key, revision, created_at, updated_at
FROM person_collections;

CREATE TABLE person_collection_members_v25 (
    collection_id INTEGER NOT NULL REFERENCES person_collections_v25(collection_id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES persons(person_id) ON DELETE RESTRICT,
    PRIMARY KEY (collection_id, person_id)
);

INSERT INTO person_collection_members_v25(collection_id, person_id)
SELECT collection_id, person_id FROM person_collection_members;

DROP TABLE person_collection_members;
DROP TABLE person_collections;

ALTER TABLE person_collections_v25 RENAME TO person_collections;
ALTER TABLE person_collection_members_v25 RENAME TO person_collection_members;

CREATE INDEX idx_person_collection_members_person
    ON person_collection_members(person_id, collection_id);
