"""Shared read-only SQL for effective person collection photo membership."""

from __future__ import annotations

from photo_search.persons.members import MEMBER_PREDICATE


def collection_condition(collection_id: int, *, alias: str = "a") -> tuple[str, tuple[int, int]]:
    """Return a fixed, set-based asset predicate for one collection.

    Resolve stable person anchors and union their photos with current static archives.
    Both collection ID parameters stay in SQL, without expanding members into Python IDs
    or probing the static archive separately for every asset in the library.
    """
    sql = (
        f"({alias}.asset_id IN ("
        "WITH RECURSIVE chain(origin_id, person_id, merged_into, path) AS ("
        "SELECT m.person_id, p.person_id, p.merged_into, ',' || p.person_id || ',' "
        "FROM person_collection_members AS m JOIN persons AS p ON p.person_id = m.person_id "
        "WHERE m.collection_id = ? UNION ALL "
        "SELECT c.origin_id, p.person_id, p.merged_into, c.path || p.person_id || ',' "
        "FROM chain AS c JOIN persons AS p ON p.person_id = c.merged_into "
        "WHERE c.merged_into IS NOT NULL AND instr(c.path, ',' || p.person_id || ',') = 0), "
        "current_members AS (SELECT DISTINCT person_id FROM chain WHERE merged_into IS NULL) "
        "SELECT f.asset_id FROM current_members AS cm "
        "CROSS JOIN face_person_links AS l INDEXED BY idx_face_person_links_person ON l.person_id=cm.person_id "
        "JOIN faces AS f ON f.face_id=l.face_id "
        "JOIN assets AS member_asset ON member_asset.asset_id=f.asset_id "
        "WHERE member_asset.state='ACTIVE' AND "
        + MEMBER_PREDICATE
        + " UNION SELECT fm.asset_id FROM person_collection_face_members AS fm "
        "JOIN assets AS archived_asset ON archived_asset.asset_id = fm.asset_id "
        "WHERE fm.collection_id = ? "
        "AND archived_asset.state = 'ACTIVE' AND archived_asset.sha256 = fm.input_revision)) "
        f"AND (NOT EXISTS (SELECT 1 FROM file_locations AS any_location WHERE any_location.asset_id={alias}.asset_id) "
        f"OR EXISTS (SELECT 1 FROM file_locations AS present_location WHERE present_location.asset_id={alias}.asset_id "
        f"AND present_location.availability='PRESENT'))"
    )
    return sql, (int(collection_id), int(collection_id))
