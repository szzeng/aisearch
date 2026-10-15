-- Face-level collection membership is a soft anchor.  Collection deletion may
-- discard its memberships, but detection replacement/GC must not erase a user's
-- archived-face decision or block normal face maintenance.
CREATE TABLE person_collection_face_members (
    collection_id INTEGER NOT NULL REFERENCES person_collections(collection_id) ON DELETE CASCADE,
    source_face_id TEXT NOT NULL CHECK(length(source_face_id) = 64 AND source_face_id NOT GLOB '*[^0-9a-f]*'),
    asset_id INTEGER NOT NULL,
    input_revision TEXT NOT NULL CHECK(length(input_revision) = 64 AND input_revision NOT GLOB '*[^0-9a-f]*'),
    created_at INTEGER NOT NULL CHECK(created_at >= 0),
    PRIMARY KEY(collection_id, source_face_id)
);

CREATE INDEX idx_person_collection_face_members_asset
    ON person_collection_face_members(collection_id, asset_id);
