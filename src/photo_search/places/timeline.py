"""Bounded SQL predicates for a selected place timeline scope.

The predicate deliberately returns SQL and bind values instead of materializing the
place's asset IDs. It mirrors the existing album membership rules and keeps CN facets
on their separately versioned membership projection.
"""

from __future__ import annotations

import json
from typing import Any

from photo_search.api.errors import ApiError
from photo_search.catalog.video import photo_asset_sql
from photo_search.places import albums, cn_facets, revision


def _place_ids_for_province(catalog: Any, data_root: Any, key: str) -> tuple[list[int], str | None, bool]:
    index, package_revision, provider, available = albums._index(catalog, data_root)
    external_ids: list[str] = []
    if available and provider:
        for external_id, region in index.get("city_to_admin1", {}).items():
            if isinstance(region, dict) and str(region.get("id", "")) == str(key):
                external_ids.append(str(external_id))
    ids: list[int] = []
    # Resolve package city refs in bounded batches instead of issuing one query per
    # city. Reserve one bind for provider under SQLite's traditional 999-variable cap.
    for start in range(0, len(external_ids), 800):
        chunk = external_ids[start : start + 800]
        marks = ",".join("?" for _ in chunk)
        ids.extend(
            int(row["place_id"])
            for row in catalog.read_all(
                f"SELECT place_id FROM place_external_refs WHERE provider=? AND external_id IN ({marks})",
                (provider, *chunk),
            )
        )
    return sorted(set(ids)), package_revision, available


def _in_ids(column: str, values: list[int]) -> tuple[str, list[int]]:
    if not values:
        return "0", []
    # These are integer place primary keys read from the active package's own refs.
    # Inline only their int-normalized form so a province with many cities never
    # exceeds SQLite's total bind-variable limit when manual + automatic branches
    # both need the same selected-place set.
    ids = ",".join(str(int(value)) for value in values)
    return f"{column} IN ({ids})", []


def selected_members_sql(
    catalog: Any, data_root: Any, *, kind: str, key: str
) -> tuple[str, tuple[Any, ...], dict[str, Any]]:
    """Return a selected place's authoritative live member SQL and scope revisions."""
    if kind not in ("city", "province", "district", "country") or not str(key):
        raise ApiError("VALIDATION_ERROR", "invalid place timeline identity", status=422)
    filter_revision = revision.read_filter_revision(catalog.connection)
    suggestion_revision = revision.read_suggestion_revision(catalog.connection)
    photo_sql, photo_params = photo_asset_sql()

    if str(key).startswith("cn:") or kind == "district":
        if kind not in ("province", "city", "district"):
            raise ApiError("VALIDATION_ERROR", "invalid CN place timeline identity", status=422)
        package_revision = cn_facets.revisions(catalog)[2]
        members, member_params = cn_facets.members_sql(kind, key=key)
        sql = (
            "a.state='ACTIVE' AND (" + photo_sql + ") AND EXISTS "
            "(SELECT 1 FROM file_locations live WHERE live.asset_id=a.asset_id AND live.availability='PRESENT') "
            "AND a.asset_id IN (SELECT place_members.asset_id FROM (" + members + ") AS place_members)"
        )
        return sql, (*photo_params, *member_params), {
            "catalog_filter_revision": filter_revision,
            "suggestion_revision": suggestion_revision,
            "package_revision": package_revision,
        }

    if kind == "province":
        city_ids, package_revision, package_available = _place_ids_for_province(catalog, data_root, key)
        manual_target, manual_params = _in_ids("p.place_id", city_ids)
        candidate_target, candidate_params = _in_ids("candidate.place_id", city_ids)
        manual_condition = f"p.kind='city' AND {manual_target}"
        candidate_condition = f"candidate.kind='city' AND {candidate_target}"
    else:
        try:
            place_id = int(key)
        except (TypeError, ValueError) as error:
            raise ApiError("VALIDATION_ERROR", "invalid place key", status=422) from error
        if place_id < 1:
            raise ApiError("VALIDATION_ERROR", "invalid place key", status=422)
        index, package_revision, _, package_available = albums._index(catalog, data_root)
        manual_params = candidate_params = []
        if kind == "city":
            manual_condition = "p.kind='city' AND p.place_id=?"
            candidate_condition = "candidate.kind='city' AND candidate.place_id=?"
            manual_params = [place_id]
            candidate_params = [place_id]
        else:  # country identity is the parent country of an automatic/manual city.
            manual_condition = "((p.kind='country' AND p.place_id=?) OR (p.kind='city' AND p.parent_place_id=?))"
            candidate_condition = "((candidate.kind='country' AND candidate.place_id=?) OR (candidate.kind='city' AND candidate.parent_place_id=?))"
            manual_params = [place_id, place_id]
            candidate_params = [place_id, place_id]

    suppress_cn = kind != "country"
    sql = f"""a.state='ACTIVE' AND ({photo_sql})
       AND EXISTS(SELECT 1 FROM file_locations live WHERE live.asset_id=a.asset_id AND live.availability='PRESENT')
       AND (
         (ap.mode='MANUAL_ASSIGNED' AND {manual_condition}
          AND p.lifecycle='ACTIVE'
          AND (?=0 OR NOT EXISTS(SELECT 1 FROM cn_legacy_links cl
              JOIN active_geo_dataset active ON active.singleton=1 AND active.dataset_id=cl.dataset_id
              WHERE cl.place_id=ap.place_id)))
         OR (COALESCE(ap.mode,'AUTO_ALLOWED')='AUTO_ALLOWED'
          AND gf.status='VALID' AND gf.input_revision=a.sha256
          AND gf.datum_status IN ('WGS84','ASSUMED_WGS84')
          AND pr.status='CANDIDATES' AND pc.local_place_id IS NOT NULL
          AND candidate.lifecycle='ACTIVE' AND pc.distance_m<=?
          AND ?=1 AND {candidate_condition}
          AND NOT EXISTS(SELECT 1 FROM place_resolution_candidates pc2
              WHERE pc2.resolution_id=pr.resolution_id AND pc2.rank=2 AND pc2.distance_m=pc.distance_m)
          AND NOT EXISTS(SELECT 1 FROM asset_place_exclusions x
              WHERE x.asset_id=a.asset_id AND x.place_id=pc.local_place_id AND x.active=1)
          AND (?=0 OR NOT EXISTS(SELECT 1 FROM cn_base_china_places cp
              JOIN active_geo_dataset active ON active.singleton=1 AND active.dataset_id=cp.dataset_id
              WHERE cp.place_id=pc.local_place_id)))
       )"""
    # The subquery uses the exact live resolution selection and manual-over-auto rule
    # from _album_rows, while the outer timeline query owns date ordering and LIMIT.
    sql = f"""a.asset_id IN (
        SELECT a.asset_id
          FROM assets a
          LEFT JOIN asset_place_assignments ap ON ap.asset_id=a.asset_id AND ap.record_state='ACTIVE'
          LEFT JOIN places p ON p.place_id=ap.place_id
          LEFT JOIN asset_geo_facts gf ON gf.asset_id=a.asset_id
          LEFT JOIN active_geo_dataset active ON active.singleton=1
          LEFT JOIN place_resolutions pr ON pr.resolution_id=(
            SELECT pr2.resolution_id FROM place_resolutions pr2
             WHERE pr2.asset_id=a.asset_id AND pr2.dataset_id=active.dataset_id
               AND pr2.geo_fact_id=gf.geo_fact_id AND pr2.input_revision=a.sha256
             ORDER BY pr2.created_at DESC,pr2.resolution_id DESC LIMIT 1)
          LEFT JOIN place_resolution_candidates pc ON pc.resolution_id=pr.resolution_id AND pc.rank=1
          LEFT JOIN places candidate ON candidate.place_id=pc.local_place_id
         WHERE {sql}
    )"""
    params = [*photo_params, *manual_params, int(suppress_cn), albums.AUTO_MAX_DISTANCE_M,
              int(bool(package_available)), *candidate_params, int(suppress_cn)]
    return sql, tuple(params), {
        "catalog_filter_revision": filter_revision,
        "suggestion_revision": suggestion_revision,
        "package_revision": package_revision,
    }


def cursor_revision(catalog_revision: int, place_revisions: dict[str, Any]) -> int:
    raw = json.dumps(
        [int(catalog_revision), place_revisions["suggestion_revision"], place_revisions["package_revision"]],
        separators=(",", ":"),
    ).encode("utf-8")
    # The timeline cursor stores an integer revision. A stable 60-bit digest binds the
    # two independent place revisions without exposing package content details.
    import hashlib

    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big") & ((1 << 60) - 1)
