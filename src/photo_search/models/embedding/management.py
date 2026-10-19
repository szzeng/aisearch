"""Read-only, lightweight projection for the gallery's semantic-model controls."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from photo_search.artifacts.store import generation_id_for
from photo_search.config import catalog_db_path, vectors_dir
from photo_search.indexing.store import _table_name
from photo_search.models.embedding.cli import read_active_profile
from photo_search.models.embedding.registry import registry_for


def embedding_management_status(data_root: str | Path) -> dict[str, Any]:
    """Return provider and generation metadata without loading models or scanning assets."""
    root = Path(data_root).resolve()
    registry = registry_for(root)
    path = Path(catalog_db_path(root))
    active = read_active_profile(root)
    generations: dict[str, dict[str, Any]] = {}
    registered_tables: set[str] = set()

    if path.is_file():
        try:
            connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
        except sqlite3.Error:
            connection = None
        if connection is not None:
            try:
                tables = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                if "index_generations" in tables:
                    for row in connection.execute(
                        "SELECT g.generation_id,g.space_id,g.processor_id,g.state,g.dimension,"
                        "(SELECT p.profile_id FROM search_profiles p WHERE p.image_generation_id=g.generation_id "
                        "ORDER BY p.created_at DESC LIMIT 1) profile_id,"
                        "COALESCE(e.confirmed,0) confirmed,COALESCE(e.repair,0) repair,"
                        "COALESCE(e.stale,0) stale,COALESCE(o.pending,0) pending_outbox "
                        "FROM index_generations g LEFT JOIN ("
                        "SELECT generation_id,SUM(state='CONFIRMED') confirmed,"
                        "SUM(state='PENDING_REPAIR') repair,SUM(state='STALE') stale "
                        "FROM index_entries GROUP BY generation_id) e USING(generation_id) "
                        "LEFT JOIN (SELECT generation_id,COUNT(*) pending FROM index_outbox "
                        "WHERE state<>'CONFIRMED' GROUP BY generation_id) o USING(generation_id)"
                    ):
                        generation_id = str(row["generation_id"])
                        registered_tables.add(_table_name(generation_id))
                        generations[generation_id] = {
                            "generation_id": generation_id,
                            "space_id": str(row["space_id"]),
                            "processor_id": str(row["processor_id"]),
                            "state": str(row["state"]),
                            "dimension": int(row["dimension"]),
                            "profile_id": None if row["profile_id"] is None else str(row["profile_id"]),
                            "confirmed": int(row["confirmed"] or 0),
                            "pending_repair": int(row["repair"] or 0),
                            "stale": int(row["stale"] or 0),
                            "pending_outbox": int(row["pending_outbox"] or 0),
                        }
            except sqlite3.Error:
                # A new, missing, or older catalog must not be mutated by a status read.
                generations = {}
            finally:
                connection.close()

    physical_tables: set[str] = set()
    vector_root = Path(vectors_dir(root))
    if vector_root.is_dir():
        try:
            import lancedb

            physical_tables = {str(name) for name in lancedb.connect(str(vector_root)).list_tables().tables}
        except Exception:  # noqa: BLE001 - status reports unavailable instead of opening a write path.
            physical_tables = set()

    providers: list[dict[str, Any]] = []
    for entry in registry.providers():
        generation_id = generation_id_for(entry.identity.space_id, entry.identity.processor_id)
        generation = generations.get(generation_id)
        has_table = _table_name(generation_id) in physical_tables
        if generation is None and has_table:
            index_state = "ORPHAN_TABLE"
        elif generation is None:
            index_state = "NO_GENERATION"
        elif generation["pending_outbox"] or generation["pending_repair"] or generation["stale"]:
            index_state = "PARTIAL"
        elif generation["state"] != "READY":
            index_state = "PARTIAL"
        else:
            index_state = "CHECK_REQUIRED"
        providers.append(
            {
                "provider_id": entry.provider_id,
                "display_name": entry.display_name,
                "adapter": entry.adapter,
                "installed": bool(entry.installed),
                "install_reason": entry.reason,
                "processor_id": entry.identity.processor_id,
                "space_id": entry.identity.space_id,
                "generation_id": generation_id,
                "dimension": int(entry.identity.dimension),
                "is_active": bool(active and active["processor_id"] == entry.identity.processor_id
                                   and active["space_id"] == entry.identity.space_id
                                   and active["generation_id"] == generation_id),
                "index_state": index_state,
                "generation": generation,
                "profile_id": None if generation is None else generation.get("profile_id"),
                "physical_table_present": has_table,
                "coverage": {"state": "CHECK_REQUIRED", "required": None,
                             "covered": None, "percent": None},
                "switch_eligible": False,
            }
        )

    orphan_tables = sorted(name for name in physical_tables if name.startswith("vectors_") and name not in registered_tables)
    return {
        "status": "ok",
        "active_profile": active,
        "providers": providers,
        "orphan_tables": orphan_tables,
        "coverage_state": "CHECK_REQUIRED",
    }


__all__ = ["embedding_management_status"]
