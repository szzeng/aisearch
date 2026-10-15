"""Shared read-only SQL for effective person collection photo membership."""

from __future__ import annotations

from photo_search.persons.members import MEMBER_PREDICATE


def collection_condition(collection_id: int, *, alias: str = "a") -> tuple[str, tuple[int]]:
    """Return a fixed, set-based asset predicate for one collection.

    Stable person anchors are recursively resolved at read time. The single collection ID
    parameter keeps this usable by retrieval without expanding members into Python IDs.
    """
    sql = (
        f"({alias}.asset_id IN (SELECT DISTINCT f.asset_id FROM faces AS f "
        "JOIN face_person_links AS l ON l.face_id = f.face_id "
        "JOIN assets AS member_asset ON member_asset.asset_id = f.asset_id "
        "WHERE member_asset.state = 'ACTIVE' AND l.person_id IN ("
        "WITH RECURSIVE chain(origin_id, person_id, merged_into, path) AS ("
        "SELECT m.person_id, p.person_id, p.merged_into, ',' || p.person_id || ',' "
        "FROM person_collection_members AS m JOIN persons AS p ON p.person_id = m.person_id "
        "WHERE m.collection_id = ? UNION ALL "
        "SELECT c.origin_id, p.person_id, p.merged_into, c.path || p.person_id || ',' "
        "FROM chain AS c JOIN persons AS p ON p.person_id = c.merged_into "
        "WHERE c.merged_into IS NOT NULL AND instr(c.path, ',' || p.person_id || ',') = 0) "
        "SELECT DISTINCT person_id FROM chain WHERE merged_into IS NULL) AND "
        + MEMBER_PREDICATE
        + ") OR "
        f"EXISTS (SELECT 1 FROM person_collection_face_members AS fm "
        f"JOIN assets AS archived_asset ON archived_asset.asset_id = fm.asset_id "
        f"WHERE fm.collection_id = ? AND fm.asset_id = {alias}.asset_id "
        f"AND archived_asset.state = 'ACTIVE' AND archived_asset.sha256 = fm.input_revision)) "
        f"AND (NOT EXISTS (SELECT 1 FROM file_locations AS any_location WHERE any_location.asset_id={alias}.asset_id) "
        f"OR EXISTS (SELECT 1 FROM file_locations AS present_location WHERE present_location.asset_id={alias}.asset_id "
        f"AND present_location.availability='PRESENT'))"
    )
    return sql, (int(collection_id), int(collection_id))
