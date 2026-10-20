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


def _text_match(name: Any, aliases: list[str], needle: str) -> bool:
    return any(
        _compact(str(spelling)) in needle for spelling in [name, *aliases]
    )


def _stored_name_match(normalized_name: Any, normalized_aliases: list[str], needle: str) -> bool:
    # normalize_name() persists NFKC/casefolded values with whitespace collapsed to spaces.
    # Removing spaces here matches _compact() without re-normalizing every GeoNames spelling.
    if str(normalized_name).replace(" ", "") in needle:
        return True
    return any(str(alias).replace(" ", "") in needle for alias in normalized_aliases)


def _matching_geo_refs_sql(*, include_fallback: bool) -> tuple[str, str | None]:
    current = """SELECT x.external_id,x.place_id,p.kind,p.canonical_name,p.normalized_name,
                      p.parent_place_id,parent.canonical_name AS parent_name
                 FROM geo_dataset_place_refs x
                 JOIN active_geo_dataset a ON a.singleton=1 AND a.dataset_id=x.dataset_id
                 JOIN places p ON p.place_id=x.place_id AND p.lifecycle='ACTIVE'
                 LEFT JOIN places parent ON parent.place_id=p.parent_place_id
                WHERE x.dataset_id=? AND x.provider=? AND p.kind IN ('city','country')
                  AND (instr(?,replace(p.normalized_name,' ',''))>0 OR EXISTS(
                       SELECT 1 FROM place_aliases pa WHERE pa.place_id=p.place_id
                         AND pa.lifecycle='ACTIVE'
                         AND instr(?,replace(pa.normalized,' ',''))>0))
             ORDER BY length(p.canonical_name) DESC,p.place_id"""
    if not include_fallback:
        return current, None
    fallback = """SELECT e.external_id,e.place_id,p.kind,p.canonical_name,p.normalized_name,
                        p.parent_place_id,parent.canonical_name AS parent_name
                 FROM place_external_refs e
                 JOIN places p ON p.place_id=e.place_id AND p.lifecycle='ACTIVE'
                 LEFT JOIN places parent ON parent.place_id=p.parent_place_id
                WHERE e.provider=? AND p.kind IN ('city','country')
                  AND NOT EXISTS (
                      SELECT 1 FROM geo_dataset_place_refs x
                      JOIN places current_place ON current_place.place_id=x.place_id
                         AND current_place.lifecycle='ACTIVE'
                      WHERE x.dataset_id=? AND x.provider=? AND x.external_id=e.external_id
                        AND current_place.kind IN ('city','country'))
                  AND (instr(?,replace(p.normalized_name,' ',''))>0 OR EXISTS(
                       SELECT 1 FROM place_aliases pa WHERE pa.place_id=p.place_id
                         AND pa.lifecycle='ACTIVE'
                         AND instr(?,replace(pa.normalized,' ',''))>0))
             ORDER BY p.place_id"""
    return current, fallback


def _verified_link_place_ids(
    catalog: Any,
    package_id: str,
    provider: str,
    links: list[Any],
    verified_source_ids: set[str],
) -> set[int]:
    place_ids = list(dict.fromkeys(int(row["place_id"]) for row in links))
    if not place_ids:
        return set()
    valid: set[int] = set()
    for offset in range(0, len(place_ids), 400):
        chunk = place_ids[offset:offset + 400]
        placeholders = ",".join("?" for _ in chunk)
        rows = catalog.read_all(
            "SELECT place_id FROM geo_dataset_place_refs WHERE dataset_id=? AND provider=? "
            f"AND place_id IN ({placeholders})",
            (package_id, provider, *chunk),
        )
        valid.update(int(row["place_id"]) for row in rows)
    remaining = [place_id for place_id in place_ids if place_id not in valid]
    if not remaining or not verified_source_ids:
        return valid
    indexed_refs: list[Any] = []
    for offset in range(0, len(remaining), 400):
        chunk = remaining[offset:offset + 400]
        placeholders = ",".join("?" for _ in chunk)
        indexed_refs.extend(catalog.read_all(
            "SELECT place_id,external_id FROM place_external_refs "
            f"WHERE provider=? AND place_id IN ({placeholders})",
            (provider, *chunk),
        ))
    fallback_ids = {str(row["external_id"]) for row in indexed_refs
                    if str(row["external_id"]) in verified_source_ids}
    if not fallback_ids:
        return valid
    current_ids: set[str] = set()
    fallback_list = sorted(fallback_ids)
    for offset in range(0, len(fallback_list), 400):
        chunk = fallback_list[offset:offset + 400]
        placeholders = ",".join("?" for _ in chunk)
        rows = catalog.read_all(
            "SELECT x.external_id FROM geo_dataset_place_refs x "
            "JOIN places p ON p.place_id=x.place_id AND p.lifecycle='ACTIVE' "
            "WHERE x.dataset_id=? AND x.provider=? AND p.kind IN ('city','country') "
            f"AND x.external_id IN ({placeholders})",
            (package_id, provider, *chunk),
        )
        current_ids.update(str(row["external_id"]) for row in rows)
    valid.update(int(row["place_id"]) for row in indexed_refs
                 if str(row["external_id"]) in fallback_ids - current_ids)
    return valid


def _count_active_geo_refs(
    catalog: Any,
    package_id: str,
    provider: str,
    excluded_place_ids: set[int],
) -> int:
    exclusion = ""
    excluded_parameters: tuple[int, ...] = ()
    if excluded_place_ids:
        placeholders = ",".join("?" for _ in excluded_place_ids)
        exclusion = f" AND x.place_id NOT IN ({placeholders})"
        excluded_parameters = tuple(sorted(excluded_place_ids))
    row = catalog.read_one(
        f"""SELECT COUNT(*) AS count FROM geo_dataset_place_refs x
             JOIN active_geo_dataset a ON a.singleton=1 AND a.dataset_id=x.dataset_id
             JOIN places p ON p.place_id=x.place_id AND p.lifecycle='ACTIVE'
            WHERE x.dataset_id=? AND x.provider=? AND p.kind IN ('city','country')
              {exclusion}""",
        (package_id, provider, *excluded_parameters),
    )
    count = int(row["count"])
    # Diagnostics count the current package's full catalog. Historical verified
    # fallbacks remain in result semantics but are deliberately excluded here so
    # opt-in measurement never scans the provider-wide legacy reference table.
    return count


def _active_dataset(catalog: Any) -> Any | None:
    row = catalog.read_one(
        "SELECT g.* FROM geo_datasets g JOIN active_geo_dataset a "
        "ON a.singleton=1 AND a.dataset_id=g.dataset_id WHERE g.state='ACTIVE'"
    )
    return row


def _read_index(catalog: Any, data_root: Any) -> tuple[dict[str, Any], str | None, str | None, bool]:
    measured = isinstance(catalog, _PlaceMetricsCatalog)
    started = time.perf_counter() if measured else 0.0
    sql_before = float(catalog._metrics.get("place_sql_ms", 0.0)) if measured else 0.0
    try:
        return albums._index(catalog, data_root)
    finally:
        if isinstance(catalog, _PlaceMetricsCatalog):
            metrics = catalog._metrics
            elapsed = time.perf_counter() - started
            sql_elapsed = float(metrics.get("place_sql_ms", 0.0)) - sql_before
            metrics["place_index_ms"] = round(
                float(metrics.get("place_index_ms", 0.0))
                + elapsed * 1000, 3
            )
            metrics["place_index_exclusive_ms"] = round(
                float(metrics.get("place_index_exclusive_ms", 0.0))
                + max(0.0, elapsed * 1000 - sql_elapsed), 3
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
    sql_before = float(perf_metrics.get("place_sql_ms", 0.0))
    index_exclusive_before = float(perf_metrics.get("place_index_exclusive_ms", 0.0))
    phases = (
        "place_link_merge_ms",
        "place_name_normalize_ms",
        "place_stored_key_prepare_ms",
        "place_name_match_ms",
        "place_candidate_construct_ms",
    )
    phases_before = {key: float(perf_metrics.get(key, 0.0)) for key in phases}
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
        named_python_ms = sum(
            float(perf_metrics.get(key, 0.0)) - phases_before[key] for key in phases
        )
        sql_ms = float(perf_metrics.get("place_sql_ms", 0.0)) - sql_before
        index_exclusive_ms = (
            float(perf_metrics.get("place_index_exclusive_ms", 0.0)) - index_exclusive_before
        )
        perf_metrics["place_python_other_ms"] = round(
            float(perf_metrics.get("place_python_other_ms", 0.0))
            + max(0.0, elapsed - sql_ms - index_exclusive_ms - named_python_ms), 3
        )
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
        if ("geo_dataset_place_refs" in normalized or "place_external_refs" in normalized) \
                and "canonical_name" in normalized:
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
    measured = isinstance(catalog, _PlaceMetricsCatalog)
    phase_started = time.perf_counter() if measured else 0.0

    def phase_done(name: str, started: float) -> None:
        if measured:
            metrics = catalog._metrics
            metrics[name] = round(float(metrics.get(name, 0.0)) + (time.perf_counter() - started) * 1000, 3)

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

    provider = str(active["provider"])
    text_needle = _compact(text) if text is not None else ""
    filtered = text is not None and bool(text_needle)
    index: dict[str, Any] = {}
    index_available = False
    if data_root is not None:
        index, _, _, index_available = _read_index(catalog, data_root)
    verified_source_ids = (
        set(map(str, index.get("city_to_admin1", {}).keys())) if index_available else set()
    )

    full_refs_sql = """SELECT x.external_id,x.place_id,p.kind,p.canonical_name,p.normalized_name,
                              p.parent_place_id,parent.canonical_name AS parent_name
                         FROM geo_dataset_place_refs x
                         JOIN active_geo_dataset a ON a.singleton=1 AND a.dataset_id=x.dataset_id
                         JOIN places p ON p.place_id=x.place_id AND p.lifecycle='ACTIVE'
                         LEFT JOIN places parent ON parent.place_id=p.parent_place_id
                        WHERE x.dataset_id=? AND x.provider=? AND p.kind IN ('city','country')
                        ORDER BY length(p.canonical_name) DESC,p.place_id"""
    if filtered:
        current_sql, fallback_sql = _matching_geo_refs_sql(
            include_fallback=bool(verified_source_ids)
        )
        refs = catalog.read_all(current_sql, (package_id, provider, text_needle, text_needle))
        if fallback_sql is not None:
            fallback = catalog.read_all(
                fallback_sql,
                (provider, package_id, provider, text_needle, text_needle),
            )
            refs.extend(row for row in fallback
                        if str(row["external_id"]) in verified_source_ids)
        current_ref_count = len(refs)
    else:
        refs = catalog.read_all(full_refs_sql, (package_id, provider))
        current_ref_count = len(refs)
        known_refs = {str(row["external_id"]) for row in refs}
        # Historical fallback is admitted only by the verified immutable package INDEX.
        if verified_source_ids:
            fallback = catalog.read_all(
                """SELECT e.external_id,e.place_id,p.kind,p.canonical_name,p.normalized_name,
                          p.parent_place_id,parent.canonical_name AS parent_name
                     FROM place_external_refs e JOIN places p ON p.place_id=e.place_id
                       AND p.lifecycle='ACTIVE' LEFT JOIN places parent ON parent.place_id=p.parent_place_id
                    WHERE e.provider=? AND p.kind IN ('city','country') ORDER BY p.place_id""",
                (provider,),
            )
            source_ids = verified_source_ids - known_refs
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
            place_id = int(alias["place_id"])
            aliases_by_place.setdefault(place_id, []).append(str(alias["text"]))
    geo_rows = [(row, aliases_by_place.get(int(row["place_id"]), [])) for row in refs]
    phase_done("place_candidate_data_prepare_ms", phase_started)
    phase_started = time.perf_counter() if measured else 0.0

    # Only package-generated cn_legacy_links prove identity. Names are retained as
    # searchable spellings only after the link itself has mapped this exact GeoNames row.
    linked = catalog.read_all(
        """SELECT l.place_id,l.city_id,l.province_id,r.admin_id,
                  p.kind,p.canonical_name
             FROM cn_legacy_links l
             JOIN active_geo_dataset a ON a.singleton=1 AND a.dataset_id=l.dataset_id
             JOIN cn_dataset_regions r ON r.dataset_id=l.dataset_id
                AND r.admin_id=COALESCE(l.city_id,l.province_id)
             JOIN places p ON p.place_id=l.place_id AND p.lifecycle='ACTIVE'
                AND p.kind IN ('city','country')
            WHERE l.dataset_id=?""",
        (package_id,),
    )
    phase_started = time.perf_counter() if measured else 0.0
    osm_by_admin = {int(item["key"][3:]): item for item in osm}
    linked_local: set[int] = set()
    geo_by_place: dict[int, tuple[Any, list[str]]] = {}
    for record in geo_rows:
        geo_by_place.setdefault(int(record[0]["place_id"]), record)
    linked_aliases: dict[int, list[str]] = {}
    valid_link_place_ids = set(geo_by_place)
    if filtered:
        valid_link_place_ids = _verified_link_place_ids(
            catalog, package_id, provider, linked, verified_source_ids
        )
        linked_ids = sorted(valid_link_place_ids)
        for offset in range(0, len(linked_ids), 400):
            chunk = linked_ids[offset:offset + 400]
            if not chunk:
                continue
            placeholders = ",".join("?" for _ in chunk)
            alias_rows = catalog.read_all(
                "SELECT place_id,text FROM place_aliases WHERE place_id IN (" + placeholders
                + ") AND lifecycle='ACTIVE' ORDER BY place_id,alias_id",
                tuple(chunk),
            )
            for alias in alias_rows:
                linked_aliases.setdefault(int(alias["place_id"]), []).append(str(alias["text"]))
    for link in linked:
        target = osm_by_admin.get(int(link["admin_id"]))
        place_id = int(link["place_id"])
        if (target is None or place_id not in valid_link_place_ids
                or target["kind"] != "city"):
            continue
        linked_local.add(place_id)
        legacy = geo_by_place.get(place_id)
        legacy_aliases = (
            linked_aliases.get(place_id, []) if filtered
            else aliases_by_place.get(place_id, [])
        )
        legacy_name = link["canonical_name"]
        if legacy is not None:
            legacy_name = legacy[0]["canonical_name"]
        spellings = [str(legacy_name), *legacy_aliases]
        target["aliases"] = list(dict.fromkeys([*target["aliases"], *spellings]))

    phase_done("place_link_merge_ms", phase_started)
    if filtered and measured:
        candidate_count = len(osm) + _count_active_geo_refs(
            catalog, package_id, provider, linked_local
        )
    else:
        candidate_count = len(osm) + sum(
            int(row["place_id"]) not in linked_local
            for row, _ in geo_rows[:current_ref_count]
        )
    if hasattr(catalog, "full_candidates"):
        catalog.full_candidates = candidate_count
    if filtered:
        matched_osm: list[dict[str, Any]] = []
        matched_spellings = 0
        normalize_ms = 0.0
        match_started = time.perf_counter() if measured else 0.0
        for item in osm:
            for spelling in [item["name"], *item["aliases"]]:
                normalize_started = time.perf_counter() if measured else 0.0
                normalized = _compact(str(spelling))
                if measured:
                    normalize_ms += (time.perf_counter() - normalize_started) * 1000
                    matched_spellings += 1
                if normalized in text_needle:
                    matched_osm.append(item)
                    break
        if measured:
            catalog._metrics["place_name_normalize_ms"] = round(
                float(catalog._metrics.get("place_name_normalize_ms", 0.0)) + normalize_ms, 3
            )
            catalog._metrics["place_name_match_ms"] = round(
                float(catalog._metrics.get("place_name_match_ms", 0.0))
                + max(0.0, (time.perf_counter() - match_started) * 1000 - normalize_ms), 3
            )
            catalog._metrics["place_spellings_normalized"] = int(
                catalog._metrics.get("place_spellings_normalized", 0)
            ) + matched_spellings
            catalog._metrics["place_spellings_reused_normalized"] = int(
                catalog._metrics.get("place_spellings_reused_normalized", 0)
            ) + sum(1 + len(aliases_by_place.get(int(row["place_id"]), [])) for row, _ in geo_rows)
        build_started = time.perf_counter() if measured else 0.0
        geo = [_geo_choice(row, aliases_by_place, package_id, active, include_parent_identity)
               for row in refs if int(row["place_id"]) not in linked_local]
        if measured:
            catalog._metrics["place_geo_candidate_objects"] = (
                int(catalog._metrics.get("place_geo_candidate_objects", 0)) + len(geo)
            )
        phase_done("place_candidate_construct_ms", build_started)
        return [*matched_osm, *geo]

    build_started = time.perf_counter() if measured else 0.0
    geo = [_geo_choice(row, aliases_by_place, package_id, active, include_parent_identity)
           for row in refs]
    if measured:
        catalog._metrics["place_geo_candidate_objects"] = (
            int(catalog._metrics.get("place_geo_candidate_objects", 0)) + len(geo)
        )
    phase_done("place_candidate_construct_ms", build_started)
    return [*osm, *(item for item in geo if item["place_id"] not in linked_local)]


def _geo_choice(
    row: Any,
    aliases_by_place: dict[int, list[str]],
    package_id: str,
    active: Any,
    include_parent_identity: bool,
) -> dict[str, Any]:
    place_id = int(row["place_id"])
    item = {
        "kind": str(row["kind"]),
        "key": str(place_id),
        "name": str(row["canonical_name"]),
        "parent": str(row["parent_name"] or ""),
        "aliases": aliases_by_place.get(place_id, []),
        "source": "GEONAMES",
        "source_id": str(row["external_id"]),
        "dataset_id": package_id,
        "dataset_version": str(active["package_version"]),
        "selection_ref": f"geonames|{package_id}|{row['external_id']}",
        "place_id": place_id,
    }
    if include_parent_identity and row["parent_place_id"] is not None:
        item["_parent_key"] = str(row["parent_place_id"])
    return item


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
