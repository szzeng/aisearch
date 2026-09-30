"""Photo-bearing place hierarchy, with distinct counts and local cover thumbnails."""

from __future__ import annotations

from photo_search.places import directory, revision
from photo_search.places.contracts import normalize_name
from photo_search.places.service import decode_place_cursor, encode_place_cursor


def browse(db, *, parent=None, name="", show_empty=False, scenic=False, limit=48, cursor=None):
    current_revision = revision.read_filter_revision(db)
    after = decode_place_cursor(cursor, current_revision=current_revision)
    conditions = ["p.lifecycle='ACTIVE'", "p.place_id>?"]
    params = [after]
    if name:
        needle = "%" + normalize_name(name, field="name") + "%"
        conditions.append(
            "(p.normalized_name LIKE ? OR EXISTS(SELECT 1 FROM place_aliases al "
            "WHERE al.place_id=p.place_id AND al.lifecycle='ACTIVE' AND al.normalized LIKE ?))"
        )
        params.extend([needle, needle])
    elif scenic:
        conditions.append("p.kind='attraction'")
    elif parent is None:
        conditions.append("p.parent_place_id IS NULL AND p.kind<>'attraction'")
    else:
        directory.get_place_row(db, parent)
        conditions.append("p.parent_place_id=?")
        params.append(parent)
    if not show_empty:
        conditions.append("COALESCE(counts.asset_count,0)>0")
    rows = db.execute(
        "WITH RECURSIVE scope(root,leaf) AS (SELECT place_id,place_id FROM places "
        "UNION SELECT s.root,p.place_id FROM scope s JOIN places p ON p.parent_place_id=s.leaf), "
        "counts AS (SELECT s.root, COUNT(DISTINCT e.asset_id) AS asset_count, MAX(e.asset_id) AS cover_asset_id "
        "FROM scope s JOIN effective_asset_places e ON e.place_id=s.leaf "
        "JOIN assets a ON a.asset_id=e.asset_id WHERE a.state='ACTIVE' GROUP BY s.root) "
        "SELECT p.place_id,COALESCE(counts.asset_count,0) AS asset_count,counts.cover_asset_id "
        "FROM places p LEFT JOIN counts ON counts.root=p.place_id WHERE "
        + " AND ".join(conditions)
        + " ORDER BY p.place_id LIMIT ?",
        (*params, limit + 1),
    ).fetchall()
    items = []
    for row in rows[:limit]:
        place = directory.get_place_view(db, row["place_id"])
        place.update(
            asset_count=row["asset_count"],
            cover_asset_id=row["cover_asset_id"],
            ancestors=directory.ancestor_views(db, row["place_id"]),
        )
        items.append(place)
    more = len(rows) > limit
    current = None if parent is None else directory.get_place_view(db, parent)
    # A scenic overlay can overlap multiple administrative branches. Show only areas
    # that share photos with the currently viewed region, without inventing parent links.
    areas = []
    if parent is not None:
        subtree, roots = directory.descendant_query([parent])
        area_rows = db.execute(
            "SELECT DISTINCT p.place_id FROM effective_asset_places e JOIN places p USING(place_id) "
            "WHERE p.kind='attraction' AND EXISTS (SELECT 1 FROM effective_asset_places other "
            f"WHERE other.asset_id=e.asset_id AND other.place_id IN ({subtree})) ORDER BY p.place_id LIMIT 100",
            roots,
        ).fetchall()
        areas = [directory.get_place_view(db, row[0]) for row in area_rows if row[0] != parent]
    return {
        "places": items,
        "current": current,
        "ancestors": [] if parent is None else directory.ancestor_views(db, parent),
        "areas": areas,
        "has_more": more,
        "next_cursor": encode_place_cursor(
            revision=current_revision, place_id=rows[limit - 1]["place_id"]
        )
        if more
        else None,
        "catalog_filter_revision": current_revision,
    }
