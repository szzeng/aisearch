"""Shared read-only SQL for effective person collection photo membership."""

from __future__ import annotations

from photo_search.persons.members import MEMBER_PREDICATE

_CURRENT_MEMBERS_CTE = (
    "WITH RECURSIVE chain(origin_id, person_id, merged_into, path) AS ("
    "SELECT m.person_id, p.person_id, p.merged_into, ',' || p.person_id || ',' "
    "FROM person_collection_members AS m JOIN persons AS p ON p.person_id = m.person_id "
    "WHERE m.collection_id = ? UNION ALL "
    "SELECT c.origin_id, p.person_id, p.merged_into, c.path || p.person_id || ',' "
    "FROM chain AS c JOIN persons AS p ON p.person_id = c.merged_into "
    "WHERE c.merged_into IS NOT NULL AND instr(c.path, ',' || p.person_id || ',') = 0), "
    "current_members AS (SELECT DISTINCT person_id FROM chain WHERE merged_into IS NULL) "
)


def collection_condition(
    collection_id: int, *, alias: str = "a", bounded_candidates: bool = False
) -> tuple[str, tuple[int, int]]:
    """Return the authoritative photo predicate for one collection.

    Resolve stable person anchors and union their photos with current static archives.
    Whole-library callers use set membership. Rechecks over a bounded asset-ID list may
    probe those assets' faces and archives instead, without expanding people in Python.
    Both paths share the same recursive identity resolution and membership definition.
    """
    if bounded_candidates:
        # Pin the candidate's asset-first access path. The uncorrelated person list is
        # built once, rather than resolving merge chains again for each detected face.
        membership = (
            f"({alias}.state='ACTIVE' AND (EXISTS ("
            "SELECT 1 FROM faces AS f INDEXED BY idx_faces_asset "
            "CROSS JOIN face_person_links AS l INDEXED BY ux_face_person_links_effective "
            f"WHERE f.asset_id={alias}.asset_id AND l.face_id=f.face_id "
            "AND l.person_id IN (" + _CURRENT_MEMBERS_CTE
            + "SELECT person_id FROM current_members) AND " + MEMBER_PREDICATE
            + ") OR EXISTS (SELECT 1 FROM person_collection_face_members AS fm "
            "INDEXED BY idx_person_collection_face_members_asset "
            f"WHERE fm.collection_id=? AND fm.asset_id={alias}.asset_id "
            f"AND fm.input_revision={alias}.sha256))) "
        )
    else:
        membership = (
            f"({alias}.asset_id IN (" + _CURRENT_MEMBERS_CTE
            + "SELECT f.asset_id FROM current_members AS cm "
            "CROSS JOIN face_person_links AS l INDEXED BY idx_face_person_links_person ON l.person_id=cm.person_id "
            "JOIN faces AS f ON f.face_id=l.face_id "
            "JOIN assets AS member_asset ON member_asset.asset_id=f.asset_id "
            "WHERE member_asset.state='ACTIVE' AND " + MEMBER_PREDICATE
            + " UNION SELECT fm.asset_id FROM person_collection_face_members AS fm "
            "JOIN assets AS archived_asset ON archived_asset.asset_id = fm.asset_id "
            "WHERE fm.collection_id = ? "
            "AND archived_asset.state = 'ACTIVE' AND archived_asset.sha256 = fm.input_revision)) "
        )
    sql = membership + (
        f"AND (NOT EXISTS (SELECT 1 FROM file_locations AS any_location WHERE any_location.asset_id={alias}.asset_id) "
        f"OR EXISTS (SELECT 1 FROM file_locations AS present_location WHERE present_location.asset_id={alias}.asset_id "
        f"AND present_location.availability='PRESENT'))"
    )
    return sql, (int(collection_id), int(collection_id))
