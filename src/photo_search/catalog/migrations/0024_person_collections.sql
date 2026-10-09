-- Person collections organize stable person identities without changing face ownership.
CREATE TABLE person_collections (
    collection_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 80),
    name_key TEXT NOT NULL UNIQUE CHECK(name_key <> ''),
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    created_at INTEGER NOT NULL CHECK(created_at >= 0),
    updated_at INTEGER NOT NULL CHECK(updated_at >= 0)
);

CREATE TABLE person_collection_members (
    collection_id INTEGER NOT NULL REFERENCES person_collections(collection_id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES persons(person_id) ON DELETE RESTRICT,
    PRIMARY KEY (collection_id, person_id)
);

CREATE INDEX idx_person_collection_members_person
    ON person_collection_members(person_id, collection_id);
