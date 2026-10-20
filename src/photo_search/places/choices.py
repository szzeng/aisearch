"""Unified, source-backed place choices for search and manual assignment.

The response is a projection of the validated active local package. OSM facets are the
only district source. GeoNames rows are package-scoped through geo_dataset_place_refs;
the older provider-wide external-ref table is not accepted as proof after a package swap.
"""

from __future__ import annotations

import time
import unicodedata
from typing import Any

from photo_search.api.errors import ApiError
from photo_search.places import albums, cn_facets


def _compact(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


def _active_dataset(catalog: Any) -> Any | None:
    row = catalog.read_one(
        "SELECT g.* FROM geo_datasets g JOIN active_geo_dataset a "
        "ON a.singleton=1 AND a.dataset_id=g.dataset_id WHERE g.state='ACTIVE'"
    )
    return row


def _read_index(catalog: Any, data_root: Any) -> tuple[dict[str, Any], str | None, str | None, bool]:
    started = time.perf_counter() if isinstance(catalog, _PlaceMetricsCatalog) else 0.0
    try:
        return albums._index(catalog, data_root)
    finally:
        if isinstance(catalog, _PlaceMetricsCatalog):
            metrics = catalog._metrics
            metrics["place_index_ms"] = round(
                float(metrics.get("place_index_ms", 0.0))
                + (time.perf_counter() - started) * 1000, 3
            )
            metrics["place_index_calls"] = int(metrics.get("place_index_calls", 0)) + 1


def verified_geo_source_ids(catalog: Any, data_root: Any) -> set[str]:
    """Return source IDs listed by the active package's checksum-verified INDEX."""
    index, _, _, available = _read_index(catalog, data_root)
    if not available:
        return set()
    return set(map(str, index.get("city_to_admin1", {}).keys()))


def choices(
    catalog: Any, text: str | None = None, *, data_root: Any = None,
    include_parent_identity: bool = False,
    perf_metrics: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """List source-proven candidates, optionally restricting to spellings in ``text``."""
    if perf_metrics is None:
        return _choices_impl(catalog, text, data_root=data_root,
                             include_parent_identity=include_parent_identity)
    started = time.perf_counter()
    measured = _PlaceMetricsCatalog(catalog, perf_metrics)
    perf_metrics["place_choices_calls"] = int(perf_metrics.get("place_choices_calls", 0)) + 1
    result: list[dict[str, Any]] = []
    try:
        result = _choices_impl(measured, text, data_root=data_root,
                               include_parent_identity=include_parent_identity)
        return result
    finally:
        elapsed = round((time.perf_counter() - started) * 1000, 3)
        perf_metrics["place_choices_ms"] = round(
            float(perf_metrics.get("place_choices_ms", 0.0)) + elapsed, 3
        )
        perf_metrics["place_full_candidates"] = max(
            int(perf_metrics.get("place_full_candidates", 0)), measured.full_candidates
        )
        perf_metrics["place_matched_candidates"] = len(result)
        runs = perf_metrics.setdefault("place_choice_runs", [])
        if isinstance(runs, list) and len(runs) < 8:
            runs.append({
                "full_candidates": measured.full_candidates,
                "matched_candidates": len(result),
                "elapsed_ms": elapsed,
            })


class _PlaceMetricsCatalog:
    """Request-local timing/counting proxy; never stores SQL text or row values."""

    def __init__(self, catalog: Any, metrics: dict[str, Any]) -> None:
        self._catalog = catalog
        self._metrics = metrics
        self.full_candidates = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._catalog, name)

    def _read(self, method: str, sql: str, parameters: Any = ()) -> Any:
        started = time.perf_counter()
        result = getattr(self._catalog, method)(sql, parameters)
        metrics = self._metrics
        metrics["place_sql_ms"] = round(
            float(metrics.get("place_sql_ms", 0.0)) + (time.perf_counter() - started) * 1000, 3
        )
        metrics["place_sql_queries"] = int(metrics.get("place_sql_queries", 0)) + 1
        row_count = len(result) if method == "read_all" else int(result is not None)
        metrics["place_sql_rows"] = int(metrics.get("place_sql_rows", 0)) + row_count
        normalized = sql.casefold()
        if "place_aliases" in normalized:
            metrics["place_alias_queries"] = int(metrics.get("place_alias_queries", 0)) + 1
            metrics["place_alias_rows"] = int(metrics.get("place_alias_rows", 0)) + row_count
        if "cn_admin_place_refs" in normalized:
            metrics["place_mapping_queries"] = int(metrics.get("place_mapping_queries", 0)) + 1
        if "cn_admin_ids" in normalized:
            metrics["place_admin_rows"] = int(metrics.get("place_admin_rows", 0)) + row_count
        if "geo_dataset_place_refs" in normalized or "place_external_refs" in normalized:
            metrics["place_geonames_rows"] = int(metrics.get("place_geonames_rows", 0)) + row_count
        if "cn_legacy_links" in normalized:
            metrics["place_link_rows"] = int(metrics.get("place_link_rows", 0)) + row_count
        return result

    def read_one(self, sql: str, parameters: Any = ()) -> Any:
        return self._read("read_one", sql, parameters)

    def read_all(self, sql: str, parameters: Any = ()) -> Any:
        return self._read("read_all", sql, parameters)


def _choices_impl(
    catalog: Any, text: str | None, *, data_root: Any,
    include_parent_identity: bool,
) -> list[dict[str, Any]]:
    active = _active_dataset(catalog)
    if active is None:
        return []
    package_id = str(active["dataset_id"])
    osm = cn_facets.choices(catalog)
    if isinstance(catalog, _PlaceMetricsCatalog):
        catalog._metrics["place_osm_rows"] = (
            int(catalog._metrics.get("place_osm_rows", 0)) + len(osm)
        )
    admin_ids = {
        int(row["admin_id"]): str(row["external_id"])
        for row in catalog.read_all("SELECT admin_id,external_id FROM cn_admin_ids")
    }
    admin_place_ids: dict[int, int] = {}
    osm_admin_ids = [int(str(item["key"])[3:]) for item in osm]
    for offset in range(0, len(osm_admin_ids), 400):
        chunk = osm_admin_ids[offset:offset + 400]
        if not chunk:
            continue
        placeholders = ",".join("?" for _ in chunk)
        rows = catalog.read_all(
            f"SELECT admin_id,place_id FROM cn_admin_place_refs WHERE admin_id IN ({placeholders})",
            tuple(chunk),
        )
        admin_place_ids.update({int(row["admin_id"]): int(row["place_id"]) for row in rows})
    for item in osm:
        admin_id = int(str(item["key"])[3:])
        item.update(
            {
                "source": "OSM",
                "source_id": admin_ids[admin_id],
                "dataset_id": package_id,
                "dataset_version": str(active["package_version"]),
                "selection_ref": f"osm|{package_id}|{admin_ids[admin_id]}",
                "place_id": admin_place_ids.get(admin_id),
            }
        )

    geo: list[dict[str, Any]] = []
    refs = catalog.read_all(
        """SELECT x.external_id,x.place_id,p.kind,p.canonical_name,p.parent_place_id,
                  parent.canonical_name AS parent_name
             FROM geo_dataset_place_refs x
             JOIN active_geo_dataset a ON a.singleton=1 AND a.dataset_id=x.dataset_id
             JOIN places p ON p.place_id=x.place_id AND p.lifecycle='ACTIVE'
             LEFT JOIN places parent ON parent.place_id=p.parent_place_id
            WHERE x.dataset_id=? AND x.provider=? AND p.kind IN ('city','country')
            ORDER BY length(p.canonical_name) DESC,p.place_id""",
        (package_id, str(active["provider"])),
    )
    known_refs = {str(row["external_id"]) for row in refs}
    # A schema-20 catalog may already have a validated active package before this
    # package-scoped bridge existed. Its immutable INDEX is verified by _index; use it
    # to recover exact current source membership without mutating the catalog.
    if data_root is not None:
        index, _, _, available = _read_index(catalog, data_root)
        if available:
            source_ids = set(map(str, index.get("city_to_admin1", {}).keys())) - known_refs
            fallback = catalog.read_all(
                """SELECT e.external_id,e.place_id,p.kind,p.canonical_name,p.parent_place_id,
                          parent.canonical_name AS parent_name
                     FROM place_external_refs e JOIN places p ON p.place_id=e.place_id
                       AND p.lifecycle='ACTIVE' LEFT JOIN places parent ON parent.place_id=p.parent_place_id
                    WHERE e.provider=? AND p.kind IN ('city','country') ORDER BY p.place_id""",
                (str(active["provider"]),),
            )
            refs = [*refs, *(row for row in fallback if str(row["external_id"]) in source_ids)]
    aliases_by_place: dict[int, list[str]] = {}
    geo_place_ids = [int(row["place_id"]) for row in refs]
    for offset in range(0, len(geo_place_ids), 400):
        chunk = geo_place_ids[offset:offset + 400]
        if not chunk:
            continue
        placeholders = ",".join("?" for _ in chunk)
        alias_rows = catalog.read_all(
            "SELECT place_id,text FROM place_aliases WHERE place_id IN (" + placeholders
            + ") AND lifecycle='ACTIVE' ORDER BY place_id,alias_id",
            tuple(chunk),
        )
        for alias in alias_rows:
            aliases_by_place.setdefault(int(alias["place_id"]), []).append(str(alias["text"]))
    for row in refs:
        place_id = int(row["place_id"])
        aliases = aliases_by_place.get(place_id, [])
        item = {
            "kind": str(row["kind"]),
            "key": str(place_id),
            "name": str(row["canonical_name"]),
            "parent": str(row["parent_name"] or ""),
            "aliases": aliases,
            "source": "GEONAMES",
            "source_id": str(row["external_id"]),
            "dataset_id": package_id,
            "dataset_version": str(active["package_version"]),
            "selection_ref": f"geonames|{package_id}|{row['external_id']}",
            "place_id": place_id,
        }
        if include_parent_identity and row["parent_place_id"] is not None:
            item["_parent_key"] = str(row["parent_place_id"])
        geo.append(item)

    # Only package-generated cn_legacy_links prove identity. Names are retained as
    # searchable spellings only after the link itself has mapped this exact GeoNames row.
    linked = catalog.read_all(
        """SELECT l.place_id,l.city_id,l.province_id,r.admin_id
             FROM cn_legacy_links l
             JOIN active_geo_dataset a ON a.singleton=1 AND a.dataset_id=l.dataset_id
             JOIN cn_dataset_regions r ON r.dataset_id=l.dataset_id
                AND r.admin_id=COALESCE(l.city_id,l.province_id)
            WHERE l.dataset_id=?""",
        (package_id,),
    )
    osm_by_admin = {int(item["key"][3:]): item for item in osm}
    linked_local: set[int] = set()
    for link in linked:
        target = osm_by_admin.get(int(link["admin_id"]))
        legacy = next((item for item in geo if item["place_id"] == int(link["place_id"])), None)
        if target is None or legacy is None or target["kind"] != "city":
            continue
        linked_local.add(int(link["place_id"]))
        spellings = [legacy["name"], *legacy["aliases"]]
        target["aliases"] = list(dict.fromkeys([*target["aliases"], *spellings]))

    candidates = [*osm, *(item for item in geo if item["place_id"] not in linked_local)]
    if hasattr(catalog, "full_candidates"):
        catalog.full_candidates = len(candidates)
    if text is not None:
        needle = _compact(text)
        if needle:
            candidates = [
                item
                for item in candidates
                if any(_compact(str(spelling)) in needle for spelling in [item["name"], *item["aliases"]])
            ]
    return candidates


def _mapped_admin_place(catalog: Any, admin_id: int) -> int | None:
    row = catalog.read_one(
        "SELECT m.place_id FROM cn_admin_place_refs m WHERE m.admin_id=?", (int(admin_id),)
    )
    return None if row is None else int(row["place_id"])


def resolve_selection_ref(
    connection: Any,
    value: str,
    *,
    now_ms: int,
    verified_geo_ids: set[str] | None = None,
) -> int:
    """Resolve a submitted source token against the current package in the caller's txn."""
    if not isinstance(value, str) or len(value) > 512:
        raise ApiError("PLACE_NOT_FOUND", "the selected place source is unavailable", status=409)
    parts = value.split("|", 2)
    if len(parts) != 3:
        raise ApiError("PLACE_NOT_FOUND", "the selected place source is unavailable", status=409)
    source, dataset_id, source_id = parts
    active = connection.execute(
        "SELECT dataset_id FROM active_geo_dataset WHERE singleton=1"
    ).fetchone()
    if active is None or active["dataset_id"] != dataset_id:
        raise ApiError("CATALOG_FILTER_REVISION_STALE", "the place package changed; choose again", status=409)
    if source == "geonames":
        row = connection.execute(
            """SELECT x.place_id FROM geo_dataset_place_refs x
                 JOIN places p ON p.place_id=x.place_id AND p.lifecycle='ACTIVE'
                WHERE x.dataset_id=? AND x.external_id=?
                  AND x.provider=(SELECT provider FROM geo_datasets WHERE dataset_id=?)""",
            (dataset_id, source_id, dataset_id),
        ).fetchone()
        if row is not None:
            return int(row["place_id"])
        if verified_geo_ids and source_id in verified_geo_ids:
            row = connection.execute(
                """SELECT e.place_id FROM place_external_refs e
                     JOIN places p ON p.place_id=e.place_id AND p.lifecycle='ACTIVE'
                    WHERE e.provider=(SELECT provider FROM geo_datasets WHERE dataset_id=?)
                      AND e.external_id=? AND p.kind IN ('city','country')""",
                (dataset_id, source_id),
            ).fetchone()
            if row is not None:
                return int(row["place_id"])
    elif source == "osm":
        row = connection.execute(
            """SELECT i.admin_id,r.kind,r.name,r.aliases_json
                 FROM cn_admin_ids i JOIN cn_dataset_regions r ON r.admin_id=i.admin_id
                WHERE i.external_id=? AND r.dataset_id=?""",
            (source_id, dataset_id),
        ).fetchone()
        if row is not None:
            mapped = connection.execute(
                "SELECT place_id FROM cn_admin_place_refs WHERE admin_id=?",
                (int(row["admin_id"]),),
            ).fetchone()
            if mapped is not None:
                return int(mapped["place_id"])
            # This custom row is an FK carrier only. The OSM identity table, never this
            # legacy enum, determines province/city/district type and parent path.
            from photo_search.places import directory

            name = str(row["name"])
            place_id = directory.create_place(
                connection,
                kind="custom",
                canonical_name=name,
                origin="DATASET",
                now_ms=int(now_ms),
            )
            connection.execute(
                "INSERT INTO cn_admin_place_refs(admin_id,place_id) VALUES (?,?)",
                (int(row["admin_id"]), place_id),
            )
            return place_id
    raise ApiError("PLACE_NOT_FOUND", "the selected place is no longer available", status=409)


def is_source_backed_place(
    connection: Any, place_id: int, *, verified_geo_ids: set[str] | None = None
) -> bool:
    """Whether a Place ID is selectable in the currently active validated package."""
    if connection.execute(
        """SELECT 1 FROM places p WHERE p.place_id=? AND p.lifecycle='ACTIVE' AND (
             EXISTS(SELECT 1 FROM geo_dataset_place_refs g JOIN active_geo_dataset a
                      ON a.singleton=1 AND a.dataset_id=g.dataset_id
                    WHERE g.place_id=p.place_id)
          OR EXISTS(SELECT 1 FROM cn_admin_place_refs m JOIN cn_dataset_regions r
                      ON r.admin_id=m.admin_id JOIN active_geo_dataset a
                      ON a.singleton=1 AND a.dataset_id=r.dataset_id
                    WHERE m.place_id=p.place_id)) LIMIT 1""",
        (int(place_id),),
    ).fetchone() is not None:
        return True
    if verified_geo_ids:
        row = connection.execute(
            """SELECT e.external_id FROM places p JOIN place_external_refs e ON e.place_id=p.place_id
                 JOIN active_geo_dataset a ON a.singleton=1
                 JOIN geo_datasets g ON g.dataset_id=a.dataset_id AND g.provider=e.provider
                WHERE p.place_id=? AND p.lifecycle='ACTIVE' LIMIT 1""",
            (int(place_id),),
        ).fetchone()
        return row is not None and str(row["external_id"]) in verified_geo_ids
    return False


def validate_selection_ref(value: str) -> str:
    """Normalize only the transport shape; source proof is checked in the transaction."""
    if not isinstance(value, str) or len(value) > 512 or value.count("|") != 2:
        raise ApiError("VALIDATION_ERROR", "target_place_ref must be a source choice", status=422)
    return value
