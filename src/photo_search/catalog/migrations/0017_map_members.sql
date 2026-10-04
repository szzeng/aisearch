-- P4-PLACE-MAP-A: monotonic revision for the effective GPS map membership.
-- The triggers are deliberately additive; authoritative GPS facts remain unchanged.
CREATE TABLE map_member_revision (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    revision INTEGER NOT NULL CHECK(revision >= 1)
);
INSERT INTO map_member_revision(singleton, revision) VALUES (1, 1);

CREATE TRIGGER trg_map_member_geo_insert AFTER INSERT ON asset_geo_facts
BEGIN UPDATE map_member_revision SET revision = revision + 1 WHERE singleton = 1; END;
CREATE TRIGGER trg_map_member_geo_update AFTER UPDATE ON asset_geo_facts
BEGIN UPDATE map_member_revision SET revision = revision + 1 WHERE singleton = 1; END;
CREATE TRIGGER trg_map_member_geo_delete AFTER DELETE ON asset_geo_facts
BEGIN UPDATE map_member_revision SET revision = revision + 1 WHERE singleton = 1; END;
CREATE TRIGGER trg_map_member_asset_update AFTER UPDATE OF sha256, state ON assets
BEGIN UPDATE map_member_revision SET revision = revision + 1 WHERE singleton = 1; END;
CREATE TRIGGER trg_map_member_asset_delete AFTER DELETE ON assets
BEGIN UPDATE map_member_revision SET revision = revision + 1 WHERE singleton = 1; END;
CREATE TRIGGER trg_map_member_location_insert AFTER INSERT ON file_locations
BEGIN UPDATE map_member_revision SET revision = revision + 1 WHERE singleton = 1; END;
CREATE TRIGGER trg_map_member_location_update AFTER UPDATE OF asset_id, availability ON file_locations
BEGIN UPDATE map_member_revision SET revision = revision + 1 WHERE singleton = 1; END;
CREATE TRIGGER trg_map_member_location_delete AFTER DELETE ON file_locations
BEGIN UPDATE map_member_revision SET revision = revision + 1 WHERE singleton = 1; END;

CREATE INDEX idx_asset_geo_map_bbox
    ON asset_geo_facts(status, datum_status, latitude, longitude, asset_id, input_revision);
