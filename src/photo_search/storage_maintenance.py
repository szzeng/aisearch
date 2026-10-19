"""Stopped-service LanceDB maintenance and SQLite VACUUM for ``storage`` CLI."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import stat
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from photo_search.catalog.sqlite_io import open_readonly
from photo_search.config import catalog_db_path, resolve_data_root
from photo_search.indexing.store import ASSET_SCALAR_INDEX, LanceVectorIndex, _table_name
from photo_search.runtime.data_root_lease import DataRootLeaseBusy, acquire_data_root_lease
from photo_search.storage import (
    _checkpoint_stopped_catalog,
    _release_scan_lock,
    _safe_directory,
    _safe_file,
    _try_scan_lock,
)

VERSION_RETENTION = timedelta(days=14)
MIN_FREE_SPACE_BYTES = 64 * 1024 * 1024
MAX_VECTOR_ITEMS = 100_000
SAMPLE_ROWS = 16
SEARCH_ROWS = 20
_KEY_TABLES = (
    "assets",
    "file_locations",
    "source_roots",
    "index_generations",
    "index_entries",
    "index_outbox",
    "search_profiles",
    "active_search_profile",
    "persons",
    "faces",
    "face_embeddings",
    "face_candidates",
    "person_operations",
    "person_collections",
    "person_collection_members",
    "photo_collections",
    "photo_collection_members",
    "photo_collection_search_appends",
    "places",
    "place_members",
    "ocr_documents",
    "search_documents",
)


def _elapsed(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _storage_lock(root: Path) -> tuple[Any | None, dict[str, Any] | None]:
    try:
        lease = acquire_data_root_lease(root)
    except DataRootLeaseBusy:
        return None, {"status": "error", "code": "DATA_ROOT_BUSY"}
    scan = _try_scan_lock(root)
    if scan is None:
        lease.close()
        return None, {"status": "error", "code": "STORAGE_SCAN_BUSY"}
    return (lease, scan), None


def _release_storage_lock(lock: tuple[Any, Any]) -> None:
    lease, scan = lock
    try:
        _release_scan_lock(scan)
    finally:
        lease.close()


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _active_image_generation(
    connection: sqlite3.Connection, tables: set[str]
) -> tuple[str | None, str | None]:
    if not {"active_search_profile", "search_profiles"} <= tables:
        return None, None
    row = connection.execute(
        "SELECT a.profile_id, p.image_generation_id FROM active_search_profile AS a "
        "LEFT JOIN search_profiles AS p ON p.profile_id = a.profile_id WHERE a.state_id = 1"
    ).fetchone()
    if row is None:
        return None, None
    return (
        str(row[0]) if row[0] is not None else None,
        str(row[1]) if row[1] is not None else None,
    )


def _index_coverage(table: Any, row_count: int) -> dict[str, Any]:
    try:
        indices = list(table.list_indices())
    except Exception as error:  # noqa: BLE001 - diagnostics must be non-destructive on unknown metadata.
        return {"state": "unknown", "reason": type(error).__name__}
    named = [item for item in indices if str(getattr(item, "name", item)) == ASSET_SCALAR_INDEX]
    if not named:
        return {
            "state": "missing",
            "num_indexed_rows": 0,
            "num_unindexed_rows": int(row_count),
            "index": ASSET_SCALAR_INDEX,
        }
    try:
        stats = table.index_stats(ASSET_SCALAR_INDEX)
    except Exception as error:  # noqa: BLE001
        return {"state": "unknown", "reason": type(error).__name__, "index": ASSET_SCALAR_INDEX}
    indexed = getattr(stats, "num_indexed_rows", None) if stats is not None else None
    unindexed = getattr(stats, "num_unindexed_rows", None) if stats is not None else None
    if indexed is None:
        indexed = getattr(named[0], "num_indexed_rows", None)
    if unindexed is None:
        unindexed = getattr(named[0], "num_unindexed_rows", None)
    if indexed is None or unindexed is None:
        return {"state": "unknown", "index": ASSET_SCALAR_INDEX}
    indexed, unindexed = int(indexed), int(unindexed)
    return {
        "state": "complete" if unindexed == 0 and indexed == row_count else "incomplete",
        "index": ASSET_SCALAR_INDEX,
        "num_indexed_rows": indexed,
        "num_unindexed_rows": unindexed,
    }


def _stats_dict(table: Any) -> dict[str, Any]:
    raw = table.stats()
    if isinstance(raw, dict):
        return raw
    if hasattr(raw, "model_dump"):
        return raw.model_dump()
    return {
        name: getattr(raw, name)
        for name in ("total_bytes", "num_rows", "num_indices", "fragment_stats")
        if hasattr(raw, name)
    }


def _version_report(table: Any, now: datetime) -> dict[str, Any]:
    versions = list(table.list_versions())
    threshold = now - VERSION_RETENTION
    old_versions = 0
    for version in versions:
        stamp = version.get("timestamp")
        if not isinstance(stamp, datetime):
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        if stamp < threshold:
            old_versions += 1
    return {
        "version_count": len(versions),
        "older_than_retention_count": old_versions,
        "retention_days": VERSION_RETENTION.days,
        "reclaimable_bytes": None,
        "reclaimable_bytes_status": "unknown until LanceDB cleanup reports actual bytes",
    }


def _validation_snapshot(
    table: Any,
    index: LanceVectorIndex,
    generation: dict[str, Any],
) -> dict[str, Any]:
    count = int(table.count_rows())
    sample = (
        table.search([0.0] * int(generation["dimension"]))
        .select(["record_id", "asset_id", "space_id", "generation_id", "vector", "_distance"])
        .limit(SAMPLE_ROWS)
        .to_arrow()
        .to_pylist()
    )
    if any(
        str(row["space_id"]) != str(generation["space_id"])
        or str(row["generation_id"]) != str(generation["generation_id"])
        or len(row["vector"]) != int(generation["dimension"])
        for row in sample
    ):
        raise ValueError("sample contains a foreign generation, space, or dimension")
    sample_digest = hashlib.sha256(
        json.dumps(sample, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if sample:
        hits = index.search(
            generation_id=str(generation["generation_id"]),
            dimension=int(generation["dimension"]),
            query_vector=sample[0]["vector"],
            limit=min(SEARCH_ROWS, max(1, count)),
            space_id=str(generation["space_id"]),
        )
    else:
        hits = []
    return {
        "row_count": count,
        "sample_rows": len(sample),
        "sample_sha256": sample_digest,
        "search": [[int(hit["asset_id"]), round(float(hit["distance"]), 8)] for hit in hits],
        "version": int(table.version),
    }


def _vector_tree_bytes(root: Path) -> int | None:
    if not root.exists():
        return 0
    if not _safe_directory(root, root.parent):
        return None
    pending = [root]
    total = examined = 0
    while pending:
        directory = pending.pop()
        try:
            entries = list(directory.iterdir())
        except OSError:
            return None
        for path in entries:
            examined += 1
            if examined > MAX_VECTOR_ITEMS:
                return None
            try:
                info = path.lstat()
            except OSError:
                return None
            reparse = bool(
                getattr(info, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            )
            if stat.S_ISLNK(info.st_mode) or reparse:
                return None
            if stat.S_ISDIR(info.st_mode):
                pending.append(path)
            elif stat.S_ISREG(info.st_mode):
                total += int(info.st_size)
            else:
                return None
    return total


def _generation_diagnostics(
    root: Path,
) -> tuple[list[dict[str, Any]], str | None, list[str], sqlite3.Connection | None]:
    db_path = catalog_db_path(root)
    if not db_path.is_file():
        return [], None, [], None
    if _safe_file(db_path, root) is None:
        return [], None, [], None
    connection = open_readonly(db_path)
    tables = _table_names(connection)
    if not {"index_generations", "index_entries", "index_outbox"} <= tables:
        connection.close()
        return [], None, [], None
    profile_id, current_generation = _active_image_generation(connection, tables)
    pending_generations = [
        str(row[0])
        for row in connection.execute(
            "SELECT generation_id FROM index_generations WHERE state = 'PENDING' ORDER BY generation_id"
        )
    ]
    generations = connection.execute(
        "SELECT generation_id, space_id, processor_id, dimension, state, is_active "
        "FROM index_generations ORDER BY generation_id"
    ).fetchall()
    vectors_root = root / "vectors"
    database = None
    names: set[str] = set()
    if vectors_root.is_dir() and _safe_directory(vectors_root, root):
        try:
            import lancedb

            database = lancedb.connect(str(vectors_root))
            names = set(database.list_tables().tables)
        except Exception:  # noqa: BLE001 - unavailable metadata is reported per table.
            database = None
    refs: dict[str, list[str]] = {}
    if "search_profiles" in tables:
        for row in connection.execute(
            "SELECT profile_id, image_generation_id FROM search_profiles ORDER BY profile_id"
        ):
            refs.setdefault(str(row[1]), []).append(str(row[0]))
    result: list[dict[str, Any]] = []
    for row in generations:
        generation = {
            "generation_id": str(row[0]),
            "space_id": str(row[1]),
            "processor_id": str(row[2]),
            "dimension": int(row[3]),
            "state": str(row[4]),
            "is_active": bool(row[5]),
        }
        gid = generation["generation_id"]
        entries = {
            str(item[0]): int(item[1])
            for item in connection.execute(
                "SELECT state, COUNT(*) FROM index_entries WHERE generation_id = ? GROUP BY state",
                (gid,),
            )
        }
        outbox = int(
            connection.execute(
                "SELECT COUNT(*) FROM index_outbox WHERE generation_id = ? AND state <> 'CONFIRMED'",
                (gid,),
            ).fetchone()[0]
        )
        name = _table_name(gid)
        item: dict[str, Any] = {
            **generation,
            "profile_references": refs.get(gid, []),
            "current_profile_id": profile_id if current_generation == gid else None,
            "index_entries": {
                key: entries.get(key, 0) for key in ("CONFIRMED", "STALE", "PENDING_REPAIR")
            },
            "unconfirmed_outbox": outbox,
            "table_exists": name in names,
            "table_name": name,
        }
        if name in names and database is not None:
            try:
                table = database.open_table(name)
                vector_type = table.schema.field("vector").type
                dimension_ok = int(vector_type.list_size) == generation["dimension"]
                count = int(table.count_rows())
                stats = _stats_dict(table)
                fragments = stats.get("fragment_stats", {}) or {}
                item.update(
                    {
                        "table_open": True,
                        "dimension_matches": dimension_ok,
                        "row_count": count,
                        "physical_bytes": stats.get("total_bytes"),
                        "fragment_count": fragments.get("num_fragments"),
                        "small_fragment_count": fragments.get("num_small_fragments"),
                        "table_version": int(table.version),
                        "index_coverage": _index_coverage(table, count),
                        "versions": _version_report(table, datetime.now(timezone.utc)),
                    }
                )
            except Exception as error:  # noqa: BLE001
                item.update({"table_open": False, "table_error": type(error).__name__})
        else:
            item.update(
                {
                    "table_open": False,
                    "table_error": "TABLE_MISSING" if name not in names else "LANCEDB_UNAVAILABLE",
                }
            )
        reasons: list[str] = []
        if not generation["is_active"] or generation["state"] != "READY":
            reasons.append("generation_not_active_ready")
        if current_generation != gid or profile_id is None:
            reasons.append("not_current_search_profile_generation")
        if pending_generations:
            reasons.append("candidate_generation_building")
        if not item["table_open"]:
            reasons.append(str(item["table_error"]).lower())
        elif not item.get("dimension_matches"):
            reasons.append("dimension_mismatch")
        if outbox:
            reasons.append("unconfirmed_index_outbox")
        if entries.get("STALE", 0) or entries.get("PENDING_REPAIR", 0):
            reasons.append("index_entries_need_repair")
        if item.get("table_open") and entries.get("CONFIRMED", 0) != item.get("row_count"):
            reasons.append("confirmed_entry_vector_count_mismatch")
        item["maintenance_eligible"] = not reasons
        item["skip_reasons"] = reasons
        result.append(item)
    return result, current_generation, pending_generations, connection


def storage_optimize_vectors(
    data_root: str | Path | None, *, apply: bool = False, stopped: bool = False
) -> dict[str, Any]:
    """Inspect active image vectors; optionally repair, compact, and age-clean them offline."""
    root = resolve_data_root(data_root).resolve()
    if apply and not stopped:
        return {"status": "error", "code": "STOPPED_CONFIRMATION_REQUIRED", "stages": []}
    if apply:
        lock, error = _storage_lock(root)
        if error:
            return {**error, "stages": []}
    else:
        lock = None
    try:
        if apply:
            db_path = catalog_db_path(root)
            if db_path.exists() and _safe_file(db_path, root) is None:
                return {"status": "skipped", "reason": "catalog_path_unsafe", "stages": []}
            try:
                _checkpoint_stopped_catalog(root)
            except RuntimeError as error:
                code = "DATA_ROOT_BUSY" if str(error) == "catalog_busy" else "CATALOG_UNAVAILABLE"
                return {"status": "error", "code": code, "stages": []}
        generations, current_generation, pending, connection = _generation_diagnostics(root)
        if not generations:
            return {
                "status": "skipped",
                "reason": "catalog_or_generation_state_unavailable",
                "generations": [],
                "stages": [],
            }
        try:
            active = next(
                (item for item in generations if item["generation_id"] == current_generation), None
            )
            if active is None or not active["maintenance_eligible"]:
                return {
                    "status": "skipped",
                    "reason": "active_generation_not_safe_to_maintain",
                    "generations": generations,
                    "stages": [],
                }
            if not apply:
                index_state = active["index_coverage"].get("state", "unknown")
                actions = []
                if index_state in {"missing", "incomplete"}:
                    actions.append({"action": "ensure_scalar_index", "state": "would_run"})
                elif index_state == "unknown":
                    actions.append(
                        {
                            "action": "ensure_scalar_index",
                            "state": "skipped",
                            "reason": "index_metadata_unknown",
                        }
                    )
                else:
                    actions.append({"action": "ensure_scalar_index", "state": "already_complete"})
                if int(active.get("fragment_count") or 0) > 1:
                    actions.append({"action": "compact_files", "state": "would_run"})
                else:
                    actions.append(
                        {
                            "action": "compact_files",
                            "state": "skipped",
                            "reason": "no_fragmentation",
                        }
                    )
                actions.append(
                    {
                        "action": "cleanup_old_versions",
                        "state": "would_run",
                        "older_than_days": VERSION_RETENTION.days,
                        "delete_unverified": False,
                    }
                )
                return {"status": "preview", "generations": generations, "actions": actions}
        finally:
            if connection is not None:
                connection.close()

        vectors_root = root / "vectors"
        tree_bytes = _vector_tree_bytes(vectors_root)
        if tree_bytes is None:
            return {
                "status": "skipped",
                "reason": "vector_tree_size_unknown_or_unsafe",
                "generations": generations,
                "stages": [],
            }
        reserve = max(MIN_FREE_SPACE_BYTES, tree_bytes // 10)
        free = int(shutil.disk_usage(vectors_root if vectors_root.exists() else root).free)
        if free < tree_bytes + reserve:
            return {
                "status": "skipped",
                "reason": "insufficient_compaction_space",
                "required_free_bytes": tree_bytes + reserve,
                "available_free_bytes": free,
                "generations": generations,
                "stages": [],
            }

        # Open read connections and tables only after the lease/checkpoint boundary.
        generations, current_generation, pending, connection = _generation_diagnostics(root)
        stages: list[dict[str, Any]] = []
        try:
            active = next(
                (item for item in generations if item["generation_id"] == current_generation), None
            )
            if active is None or not active["maintenance_eligible"] or pending:
                return {
                    "status": "skipped",
                    "reason": "authoritative_state_changed_or_unstable",
                    "generations": generations,
                    "stages": stages,
                }
            generation = {
                key: active[key]
                for key in ("generation_id", "space_id", "processor_id", "dimension")
            }
            index = LanceVectorIndex(vectors_root)
            table = index._table(
                generation_id=generation["generation_id"],
                dimension=generation["dimension"],
                create=False,
            )
            if table is None:
                return {
                    "status": "skipped",
                    "reason": "active_table_missing",
                    "generations": generations,
                    "stages": stages,
                }
            before = _validation_snapshot(table, index, generation)
            coverage = _index_coverage(table, before["row_count"])
            stage_start = time.monotonic()
            if coverage["state"] == "unknown":
                stages.append(
                    {
                        "name": "index_coverage",
                        "status": "skipped",
                        "reason": "index_metadata_unknown",
                        "elapsed_ms": _elapsed(stage_start),
                    }
                )
                return {
                    "status": "skipped",
                    "reason": "index_metadata_unknown",
                    "generations": generations,
                    "stages": stages,
                }
            if coverage["state"] != "complete":
                outcome = index.ensure_scalar_index(
                    generation_id=generation["generation_id"], dimension=generation["dimension"]
                )
                table = index._table(
                    generation_id=generation["generation_id"],
                    dimension=generation["dimension"],
                    create=False,
                )
                if table is None:
                    stages.append(
                        {
                            "name": "index_coverage",
                            "status": "failed",
                            "reason": "table_missing_after_index_repair",
                            "elapsed_ms": _elapsed(stage_start),
                        }
                    )
                    return {"status": "failed", "generations": generations, "stages": stages}
                coverage = _index_coverage(table, before["row_count"])
                if coverage["state"] != "complete":
                    stages.append(
                        {
                            "name": "index_coverage",
                            "status": "skipped",
                            "reason": "scalar_index_not_verified_complete",
                            "outcome": outcome,
                            "coverage": coverage,
                            "elapsed_ms": _elapsed(stage_start),
                        }
                    )
                    return {
                        "status": "skipped",
                        "reason": "scalar_index_not_verified_complete",
                        "generations": generations,
                        "stages": stages,
                    }
                stages.append(
                    {
                        "name": "index_coverage",
                        "status": "done",
                        "outcome": outcome,
                        "coverage": coverage,
                        "elapsed_ms": _elapsed(stage_start),
                    }
                )
            else:
                stages.append(
                    {
                        "name": "index_coverage",
                        "status": "done",
                        "outcome": {"state": "already_complete"},
                        "coverage": coverage,
                        "elapsed_ms": _elapsed(stage_start),
                    }
                )

            stage_start = time.monotonic()
            compact_before_stats = _stats_dict(table)
            compact_before_fragments = (compact_before_stats.get("fragment_stats") or {}).get(
                "num_fragments"
            )
            if int(compact_before_fragments or 0) > 1:
                try:
                    compact_result = table.compact_files()
                    compact_details = _result_dict(compact_result)
                    compact_status = "done"
                except Exception as error:  # noqa: BLE001
                    stages.append(
                        {
                            "name": "compact_files",
                            "status": "failed",
                            "reason": type(error).__name__,
                            "elapsed_ms": _elapsed(stage_start),
                        }
                    )
                    return {
                        "status": "failed",
                        "generations": generations,
                        "stages": stages,
                        "before": before,
                    }
            else:
                compact_details = {"reason": "no_fragmentation"}
                compact_status = "skipped"
            table = index._table(
                generation_id=generation["generation_id"],
                dimension=generation["dimension"],
                create=False,
            )
            if table is None:
                stages.append(
                    {
                        "name": "compact_files",
                        "status": "failed",
                        "reason": "table_missing_after_compaction",
                        "elapsed_ms": _elapsed(stage_start),
                    }
                )
                return {
                    "status": "failed",
                    "generations": generations,
                    "stages": stages,
                    "before": before,
                }
            after_compact = _validation_snapshot(table, index, generation)
            compact_after_stats = _stats_dict(table)
            compact_after_fragments = (compact_after_stats.get("fragment_stats") or {}).get(
                "num_fragments"
            )
            coverage_after_compact = _index_coverage(table, after_compact["row_count"])
            if (
                not _equivalent(before, after_compact)
                or coverage_after_compact["state"] == "unknown"
            ):
                stages.append(
                    {
                        "name": "compact_files",
                        "status": "failed",
                        "reason": "post_compaction_verification_failed",
                        "result": compact_details,
                        "elapsed_ms": _elapsed(stage_start),
                    }
                )
                return {
                    "status": "failed",
                    "generations": generations,
                    "stages": stages,
                    "before": before,
                    "after_compact": after_compact,
                }
            if coverage_after_compact["state"] != "complete":
                repair = index.ensure_scalar_index(
                    generation_id=generation["generation_id"], dimension=generation["dimension"]
                )
                table = index._table(
                    generation_id=generation["generation_id"],
                    dimension=generation["dimension"],
                    create=False,
                )
                if table is None:
                    stages.append(
                        {
                            "name": "compact_files",
                            "status": "failed",
                            "reason": "table_missing_after_index_repair",
                            "elapsed_ms": _elapsed(stage_start),
                        }
                    )
                    return {"status": "failed", "generations": generations, "stages": stages}
                coverage_after_compact = _index_coverage(table, after_compact["row_count"])
                if coverage_after_compact["state"] != "complete":
                    stages.append(
                        {
                            "name": "compact_files",
                            "status": "failed",
                            "reason": "post_compaction_index_coverage_failed",
                            "result": compact_details,
                            "index_repair": repair,
                            "elapsed_ms": _elapsed(stage_start),
                        }
                    )
                    return {
                        "status": "failed",
                        "generations": generations,
                        "stages": stages,
                        "before": before,
                        "after_compact": after_compact,
                    }
            stages.append(
                {
                    "name": "compact_files",
                    "status": compact_status,
                    "result": compact_details,
                    "row_count": after_compact["row_count"],
                    "fragment_count_before": compact_before_fragments,
                    "fragment_count_after": compact_after_fragments,
                    "table_bytes_before": compact_before_stats.get("total_bytes"),
                    "table_bytes_after": compact_after_stats.get("total_bytes"),
                    "version_before": before["version"],
                    "version_after": after_compact["version"],
                    "vector_tree_bytes_before": tree_bytes,
                    "elapsed_ms": _elapsed(stage_start),
                }
            )

            stage_start = time.monotonic()
            try:
                cleanup_result = table.cleanup_old_versions(
                    older_than=VERSION_RETENTION, delete_unverified=False
                )
            except Exception as error:  # noqa: BLE001 - retain prior verified table state for retry.
                stages.append(
                    {
                        "name": "cleanup_old_versions",
                        "status": "failed",
                        "reason": type(error).__name__,
                        "detail": str(error)[:240],
                        "elapsed_ms": _elapsed(stage_start),
                    }
                )
                return {
                    "status": "failed",
                    "generations": generations,
                    "stages": stages,
                    "before": before,
                }
            cleanup_details = _result_dict(cleanup_result)
            cleanup_bytes = _first_int(
                cleanup_result, ("bytes_removed", "bytes_freed", "bytes_deleted")
            )
            cleanup_versions = _first_int(cleanup_result, ("old_versions", "versions_removed"))
            tree_bytes_after_cleanup = _vector_tree_bytes(vectors_root)
            stages.append(
                {
                    "name": "cleanup_old_versions",
                    "status": "done",
                    "older_than_days": VERSION_RETENTION.days,
                    "delete_unverified": False,
                    "actual_reclaimed_bytes": cleanup_bytes,
                    "vector_tree_bytes_before": tree_bytes,
                    "vector_tree_bytes_after": tree_bytes_after_cleanup,
                    "old_versions_removed": cleanup_versions,
                    "result": cleanup_details,
                    "elapsed_ms": _elapsed(stage_start),
                }
            )

            stage_start = time.monotonic()
            after = _validation_snapshot(table, index, generation)
            final_coverage = _index_coverage(table, after["row_count"])
            if not _equivalent(before, after) or final_coverage["state"] != "complete":
                stages.append(
                    {
                        "name": "result_verification",
                        "status": "failed",
                        "reason": "post_cleanup_verification_failed",
                        "coverage": final_coverage,
                        "elapsed_ms": _elapsed(stage_start),
                    }
                )
                return {
                    "status": "failed",
                    "generations": generations,
                    "stages": stages,
                    "before": before,
                    "after": after,
                }
            stages.append(
                {
                    "name": "result_verification",
                    "status": "done",
                    "before": before,
                    "after": after,
                    "coverage": final_coverage,
                    "elapsed_ms": _elapsed(stage_start),
                }
            )
            return {
                "status": "ok",
                "generations": generations,
                "stages": stages,
                "before": before,
                "after": after,
            }
        except Exception as error:  # noqa: BLE001
            stages.append(
                {
                    "name": "maintenance",
                    "status": "failed",
                    "reason": type(error).__name__,
                    "detail": str(error)[:240],
                }
            )
            return {"status": "failed", "generations": generations, "stages": stages}
        finally:
            if connection is not None:
                connection.close()
    finally:
        if lock is not None:
            _release_storage_lock(lock)


def _result_dict(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    if hasattr(result, "model_dump"):
        return result.model_dump()
    if hasattr(result, "__dict__"):
        return dict(result.__dict__)
    fields = getattr(result, "__slots__", ())
    return {str(name): getattr(result, name) for name in fields if hasattr(result, name)} or {
        "summary": str(result)
    }


def _first_int(result: Any, names: tuple[str, ...]) -> int | None:
    for name in names:
        value = getattr(result, name, None)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def _equivalent(before: dict[str, Any], after: dict[str, Any]) -> bool:
    return (
        before["row_count"] == after["row_count"]
        and before["sample_sha256"] == after["sample_sha256"]
        and before["search"] == after["search"]
    )


def _catalog_snapshot(connection: sqlite3.Connection) -> dict[str, Any]:
    tables = _table_names(connection)
    definitions = [
        [str(row[0]), str(row[1]), str(row[2] or "")]
        for row in connection.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        )
    ]
    counts: dict[str, int] = {}
    for name in _KEY_TABLES:
        if name in tables:
            counts[name] = int(connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
    pointer = None
    if "active_search_profile" in tables:
        row = connection.execute(
            "SELECT state_id, profile_id, updated_at FROM active_search_profile ORDER BY state_id"
        ).fetchall()
        pointer = [list(item) for item in row]
    page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
    page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
    free_pages = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
    return {
        "schema_version": int(connection.execute("PRAGMA schema_version").fetchone()[0]),
        "user_version": int(connection.execute("PRAGMA user_version").fetchone()[0]),
        "schema_sha256": hashlib.sha256(
            json.dumps(definitions, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "table_counts": counts,
        "active_search_profile": pointer,
        "page_count": page_count,
        "page_size": page_size,
        "freelist_count": free_pages,
        "freelist_bytes": free_pages * page_size,
    }


def _integrity_check(connection: sqlite3.Connection) -> str:
    rows = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
    return "ok" if rows == ["ok"] else "; ".join(rows[:10])


def storage_vacuum(
    data_root: str | Path | None, *, apply: bool = False, stopped: bool = False
) -> dict[str, Any]:
    """Preview or run a guarded SQLite VACUUM without changing logical catalog facts."""
    root = resolve_data_root(data_root).resolve()
    db_path = catalog_db_path(root)
    if apply and not stopped:
        return {"status": "error", "code": "STOPPED_CONFIRMATION_REQUIRED", "stages": []}
    if not db_path.is_file():
        return {"status": "skipped", "reason": "catalog_missing", "stages": []}
    if _safe_file(db_path, root) is None:
        return {"status": "skipped", "reason": "catalog_path_unsafe", "stages": []}
    if not apply:
        try:
            connection = open_readonly(db_path)
            snapshot = _catalog_snapshot(connection)
            integrity = _integrity_check(connection)
            connection.close()
        except sqlite3.Error as error:
            return {
                "status": "skipped",
                "reason": "catalog_unavailable",
                "detail": type(error).__name__,
                "stages": [],
            }
        size = int(db_path.stat().st_size)
        threshold = max(1024 * 1024, size // 20)
        should_vacuum = integrity == "ok" and snapshot["freelist_bytes"] >= threshold
        return {
            "status": "preview",
            "integrity_check": integrity,
            "catalog_bytes": size,
            "page_count": snapshot["page_count"],
            "page_size": snapshot["page_size"],
            "freelist_count": snapshot["freelist_count"],
            "freelist_bytes_estimate": snapshot["freelist_bytes"],
            "recommended": "vacuum" if should_vacuum else "skip",
            "reason": None
            if should_vacuum
            else ("integrity_check_failed" if integrity != "ok" else "little_reusable_space"),
        }

    lock, error = _storage_lock(root)
    if error:
        return {**error, "stages": []}
    try:
        try:
            _checkpoint_stopped_catalog(root)
        except RuntimeError as checkpoint_error:
            code = (
                "DATA_ROOT_BUSY"
                if str(checkpoint_error) == "catalog_busy"
                else "CATALOG_UNAVAILABLE"
            )
            return {"status": "error", "code": code, "stages": []}
        connection = sqlite3.connect(db_path, timeout=1.0, isolation_level=None)
        try:
            before = _catalog_snapshot(connection)
            integrity_before = _integrity_check(connection)
            if integrity_before != "ok":
                return {
                    "status": "skipped",
                    "reason": "integrity_check_failed",
                    "integrity_check": integrity_before,
                    "before": before,
                    "stages": [],
                }
            threshold = max(1024 * 1024, int(db_path.stat().st_size) // 20)
            if before["freelist_bytes"] < threshold:
                return {
                    "status": "skipped",
                    "reason": "little_reusable_space",
                    "before": before,
                    "stages": [{"name": "vacuum", "status": "skipped"}],
                }
            required = int(db_path.stat().st_size) + max(
                MIN_FREE_SPACE_BYTES, int(db_path.stat().st_size) // 10
            )
            free = int(shutil.disk_usage(db_path.parent).free)
            if free < required:
                return {
                    "status": "skipped",
                    "reason": "insufficient_vacuum_space",
                    "required_free_bytes": required,
                    "available_free_bytes": free,
                    "before": before,
                    "stages": [{"name": "vacuum", "status": "skipped"}],
                }
            started = time.monotonic()
            file_size_before = int(db_path.stat().st_size)
            try:
                connection.execute("VACUUM")
            except sqlite3.Error as vacuum_error:
                return {
                    "status": "failed",
                    "reason": "vacuum_failed",
                    "detail": type(vacuum_error).__name__,
                    "before": before,
                    "stages": [
                        {"name": "vacuum", "status": "failed", "elapsed_ms": _elapsed(started)}
                    ],
                }
            after = _catalog_snapshot(connection)
            integrity_after = _integrity_check(connection)
            before_check = {
                key: value
                for key, value in before.items()
                if key not in {"schema_version", "page_count", "freelist_count", "freelist_bytes"}
            }
            after_check = {
                key: value
                for key, value in after.items()
                if key not in {"schema_version", "page_count", "freelist_count", "freelist_bytes"}
            }
            unchanged = before_check == after_check and integrity_after == "ok"
            final_size = int(db_path.stat().st_size)
            return {
                "status": "ok" if unchanged else "failed",
                "reason": None if unchanged else "catalog_invariant_changed_after_vacuum",
                "integrity_check_before": integrity_before,
                "integrity_check_after": integrity_after,
                "catalog_bytes_before": int(before["page_count"] * before["page_size"]),
                "catalog_bytes_after": final_size,
                "actual_reclaimed_bytes": max(0, file_size_before - final_size),
                "freelist_bytes_before": before["freelist_bytes"],
                "freelist_bytes_after": after["freelist_bytes"],
                "before": before,
                "after": after,
                "stages": [
                    {
                        "name": "vacuum",
                        "status": "done" if unchanged else "failed",
                        "elapsed_ms": _elapsed(started),
                    }
                ],
            }
        finally:
            connection.close()
    finally:
        _release_storage_lock(lock)
