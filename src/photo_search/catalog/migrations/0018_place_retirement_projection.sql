-- Retirement removes a place from the effective view before an AFTER trigger runs.
-- Queue by stored references so both retirement and reactivation refresh FTS.
DROP TRIGGER trg_places_search_queue_change;
CREATE TRIGGER trg_places_search_queue_change
AFTER UPDATE ON places
WHEN (old.canonical_name IS NOT new.canonical_name OR old.lifecycle IS NOT new.lifecycle)
BEGIN
    INSERT INTO search_projection_queue(asset_id, cause, queued_at)
    SELECT DISTINCT ap.asset_id, 'PLACE_NAME_CHANGED',
           CAST(strftime('%s', 'now') AS INTEGER) * 1000
    FROM (
        SELECT asset_id, place_id FROM asset_place_assignments WHERE mode='MANUAL_ASSIGNED'
        UNION
        SELECT asset_id, place_id FROM boundary_memberships
    ) AS ap
    WHERE ap.place_id IN (
        WITH RECURSIVE subtree(place_id) AS (
            SELECT new.place_id
            UNION
            SELECT p.place_id FROM places p JOIN subtree s ON p.parent_place_id=s.place_id
        ) SELECT place_id FROM subtree
    )
    ON CONFLICT(asset_id) DO UPDATE
        SET cause=excluded.cause, queued_at=excluded.queued_at;
END;
-- Repair documents left stale by a retirement under schema 17.
INSERT INTO search_projection_queue(asset_id,cause,queued_at)
SELECT DISTINCT a.asset_id,'PLACE_NAME_CHANGED',CAST(strftime('%s','now') AS INTEGER)*1000
FROM asset_place_assignments a JOIN places p USING(place_id)
WHERE a.mode='MANUAL_ASSIGNED' AND p.lifecycle='RETIRED'
UNION
SELECT DISTINCT b.asset_id,'PLACE_NAME_CHANGED',CAST(strftime('%s','now') AS INTEGER)*1000
FROM boundary_memberships b JOIN places p USING(place_id)
WHERE p.lifecycle='RETIRED'
ON CONFLICT(asset_id) DO UPDATE SET cause=excluded.cause,queued_at=excluded.queued_at;
