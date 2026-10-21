"""Private, read-only catalog input for the bounded WeMM quality pilot.

An active WAL is refused: even a mode=ro SQLite reader can modify shared-memory
reader marks. Only a checkpointed, unchanged main database is opened immutable.
No production service, migration, image decoder, vector reader or writer is used.
Returned sample paths are private local data and must never enter a shared report.
"""

from __future__ import annotations

import hashlib
import json
import ntpath
import sqlite3
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath
from typing import Any

from photo_search.config import catalog_db_path
from tools.evaluation.wemm_pilot_core import MAX_ASSETS, select_sample

SUPPORTED_FORMATS = ("JPEG", "JPG", "PNG", "WEBP", "BMP", "GIF", "MPO", "HEIC", "HEIF", "CR2")
_FORMATS_SQL = ",".join("?" for _ in SUPPORTED_FORMATS)
_ELIGIBLE = (
    "a.state = 'ACTIVE' AND UPPER(a.format) IN (" + _FORMATS_SQL + ") AND EXISTS ("
    "SELECT 1 FROM file_locations f JOIN source_roots s ON s.source_id=f.source_id "
    "WHERE f.asset_id=a.asset_id AND f.availability='PRESENT' AND s.status='ONLINE')"
)


class PilotCatalogError(ValueError):
    """A public reason code without an original path, SQL text or SQLite error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _stat_identity(path: Path) -> dict[str, int]:
    value = path.stat()
    return {"device": value.st_dev, "inode": value.st_ino, "bytes": value.st_size,
            "mtime_ns": value.st_mtime_ns, "ctime_ns": value.st_ctime_ns}


def _refuse_sidecars(path: Path) -> None:
    if Path(str(path) + "-wal").exists():
        raise PilotCatalogError("ACTIVE_WAL_REQUIRES_OFFLINE_SNAPSHOT")
    if any(Path(str(path) + suffix).exists() for suffix in ("-shm", "-journal")):
        raise PilotCatalogError("CATALOG_SIDECARS_REQUIRE_OFFLINE_SNAPSHOT")


def _fingerprint(path: Path) -> dict[str, Any]:
    """Full main-file identity, with changes during hashing refused as well."""
    _refuse_sidecars(path)
    before = _stat_identity(path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    _refuse_sidecars(path)
    if before != _stat_identity(path):
        raise PilotCatalogError("CATALOG_CHANGED_DURING_READ")
    return {"catalog_sha256": digest.hexdigest(), "file_identity": before}


def _authorize(action: int, first: str | None, second: str | None, *_: Any) -> int:
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION and str(second).lower() in {
        "count", "sum", "coalesce", "upper",
    }:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_TRANSACTION and first in {"BEGIN", "ROLLBACK"}:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _database_path(data_root: str | Path) -> Path:
    root = Path(data_root).resolve(strict=True)
    path = catalog_db_path(root).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise PilotCatalogError("CATALOG_PATH_UNSAFE")
    return path


@contextmanager
def _snapshot(
    data_root: str | Path, *, verify_on_exit: bool = True,
) -> Iterator[tuple[sqlite3.Connection, dict[str, Any]]]:
    """Short immutable SQL snapshot; do not freeze or checkpoint another writer."""
    connection = None
    try:
        path = _database_path(data_root)
        before = _fingerprint(path)
        connection = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.set_authorizer(_authorize)
        connection.execute("BEGIN")
        yield connection, before
        connection.execute("ROLLBACK")
        connection.close()
        connection = None
        if verify_on_exit and before != _fingerprint(path):
            raise PilotCatalogError("CATALOG_CHANGED_DURING_READ")
    except PilotCatalogError:
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        raise PilotCatalogError("CATALOG_UNREADABLE_OR_UNSUPPORTED") from None
    finally:
        if connection is not None:
            connection.close()


def _preflight(connection: sqlite3.Connection, identity: dict[str, Any]) -> dict[str, Any]:
    version = connection.execute(
        "SELECT value FROM metadata WHERE key='schema_version'"
    ).fetchone()
    pointer = connection.execute(
        "SELECT profile_id, updated_at FROM active_search_profile WHERE state_id=1"
    ).fetchone()
    if version is None or pointer is None:
        raise PilotCatalogError("CATALOG_BINDING_INVALID")
    coverage = {
        "active_assets": int(connection.execute(
            "SELECT COUNT(*) FROM assets WHERE state='ACTIVE'"
        ).fetchone()[0]),
        "eligible_metadata_assets": int(connection.execute(
            "SELECT COUNT(*) FROM assets a WHERE " + _ELIGIBLE, SUPPORTED_FORMATS
        ).fetchone()[0]),
    }
    # Metadata counts only: no stat/decode over the entire photo library.
    states = connection.execute(
        "SELECT s.status, f.availability, COUNT(*) AS n FROM file_locations f "
        "JOIN source_roots s ON s.source_id=f.source_id "
        "JOIN assets a ON a.asset_id=f.asset_id WHERE a.state='ACTIVE' "
        "GROUP BY s.status, f.availability"
    ).fetchall()
    coverage["locations_by_state"] = [dict(row) for row in states]
    coverage["missing_locations"] = sum(
        int(row["n"]) for row in states if row["availability"] == "MISSING"
    )
    coverage["offline_or_error_locations"] = sum(
        int(row["n"]) for row in states if row["status"] != "ONLINE"
    )
    binding = None
    status = "NO_ACTIVE_PROFILE"
    if pointer["profile_id"] is not None:
        row = connection.execute(
            "SELECT p.profile_id, p.embedding_processor_id, p.space_id, "
            "p.image_generation_id, g.generation_id, g.processor_id AS generation_processor_id, "
            "g.space_id AS generation_space_id, g.dimension, g.state, g.is_active "
            "FROM search_profiles p LEFT JOIN index_generations g "
            "ON g.generation_id=p.image_generation_id WHERE p.profile_id=?",
            (pointer["profile_id"],),
        ).fetchone()
        if (row is None or row["generation_id"] is None
                or row["embedding_processor_id"] != row["generation_processor_id"]
                or row["space_id"] != row["generation_space_id"]
                or int(row["dimension"] or 0) <= 0):
            raise PilotCatalogError("CATALOG_BINDING_INVALID")
        binding = {"profile_id": row["profile_id"], "processor_id": row["embedding_processor_id"],
                   "space_id": row["space_id"], "generation_id": row["generation_id"],
                   "dimension": int(row["dimension"]), "generation_state": row["state"],
                   "generation_is_active": bool(row["is_active"]),
                   "activated_at": int(pointer["updated_at"])}
        status = "READY" if row["state"] == "READY" and row["is_active"] == 1 else "NOT_READY"
        entries = connection.execute(
            "SELECT e.state, COUNT(*) AS n FROM index_entries e "
            "JOIN assets a ON a.asset_id=e.asset_id WHERE a.state='ACTIVE' "
            "AND e.generation_id=? AND e.space_id=? GROUP BY e.state",
            (binding["generation_id"], binding["space_id"]),
        ).fetchall()
        coverage["bound_entries_by_state"] = {row["state"]: int(row["n"]) for row in entries}
        coverage["confirmed_eligible_assets"] = int(connection.execute(
            "SELECT COUNT(*) FROM assets a WHERE " + _ELIGIBLE + " AND EXISTS ("
            "SELECT 1 FROM index_entries e WHERE e.asset_id=a.asset_id AND e.generation_id=? "
            "AND e.space_id=? AND e.state='CONFIRMED')",
            (*SUPPORTED_FORMATS, binding["generation_id"], binding["space_id"]),
        ).fetchone()[0])
    return {"schema": "wemm-pilot-catalog-v1", "status": status,
            "source_identity": {**identity, "schema_version": int(version[0]), "binding": binding},
            "coverage": coverage, "supported_metadata_formats": list(SUPPORTED_FORMATS),
            "file_readability_verified": False}


def catalog_preflight(data_root: str | Path) -> dict[str, Any]:
    """Read authoritative binding and coverage; nothing is inferred from model folders."""
    with _snapshot(data_root) as (connection, identity):
        result = _preflight(connection, identity)
    return result


def _safe_photo_path(root_text: str, relative: str) -> Path:
    """Resolve registered relative paths without following a link beyond its source root."""
    if (not relative or "\x00" in relative or ":" in relative or ntpath.isabs(relative)
            or ntpath.splitdrive(relative)[0]
            or any(part in {".", ".."} for part in PureWindowsPath(relative).parts)):
        raise PilotCatalogError("SAMPLE_PATH_UNSAFE")
    root = Path(root_text)
    if not root.is_absolute():
        raise PilotCatalogError("SAMPLE_PATH_UNSAFE")
    root = root.resolve()
    path = root.joinpath(*PureWindowsPath(relative).parts).resolve()
    if not path.is_relative_to(root):
        raise PilotCatalogError("SAMPLE_PATH_UNSAFE")
    return path


def _locate(row: dict[str, Any], locations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    result = {**row, "path": None, "input_status": "NO_ONLINE_PRESENT_LOCATION"}
    for location in locations:
        if location["availability"] != "PRESENT" or location["status"] != "ONLINE":
            continue
        try:
            path = _safe_photo_path(str(location["root_path"]), str(location["relative_path"]))
            if not path.is_file():
                result["input_status"] = "MISSING_FILE"
                continue
            result.update(path=str(path), source_id=int(location["source_id"]),
                          location_id=int(location["location_id"]),
                          file_identity=_stat_identity(path), input_status="READY_FOR_HASH_CHECK")
            if result["file_identity"]["bytes"] != int(row["byte_size"]):
                result["input_status"] = "SIZE_CHANGED"
            return result
        except OSError:
            result["input_status"] = "UNREADABLE_FILE"
    return result


def freeze_sample(
    data_root: str | Path, max_assets: int, seed: int,
    problem_ids: Sequence[int] | Mapping[str, Sequence[int]] = (),
) -> dict[str, Any]:
    """Select first, then inspect only selected paths; failures never trigger replacement.

    The metadata population is supported-format ACTIVE assets with an ONLINE/PRESENT
    location. Its id/SHA scalars are materialized for the shared deterministic sampler;
    other metadata and paths are fetched in one batch of at most 1000 selected IDs.
    Actual source bytes must match catalog SHA before encoding (not verified here).
    """
    if type(max_assets) is not int or not 1 <= max_assets <= MAX_ASSETS:
        raise PilotCatalogError("INVALID_SAMPLE_SIZE")
    if type(seed) is not int:
        raise PilotCatalogError("INVALID_SAMPLE_SEED")
    with _snapshot(data_root, verify_on_exit=False) as (connection, identity):
        preflight = _preflight(connection, identity)
        population = [dict(row) for row in connection.execute(
            "SELECT a.asset_id, a.sha256 FROM assets a WHERE " + _ELIGIBLE
            + " ORDER BY a.asset_id", SUPPORTED_FORMATS,
        )]
        try:
            groups = problem_ids.values() if isinstance(problem_ids, Mapping) else (problem_ids,)
            if not population and any(group for group in groups):
                raise ValueError("problem IDs have no eligible population")
            selected = select_sample(population, max_assets, seed, problem_ids)
        except (ValueError, KeyError, TypeError):
            raise PilotCatalogError("INVALID_SAMPLE_POPULATION_OR_STRATA") from None
        ids = [row["asset_id"] for row in selected]
        metadata, locations = {}, {}
        if ids:
            placeholders = ",".join("?" for _ in ids)
            metadata = {int(row["asset_id"]): dict(row) for row in connection.execute(
                "SELECT asset_id, sha256, byte_size, width, height, format FROM assets "
                f"WHERE asset_id IN ({placeholders})", ids,
            )}
            for row in connection.execute(
                "SELECT f.asset_id, f.location_id, f.source_id, f.relative_path, "
                "f.availability, s.root_path, s.status FROM file_locations f "
                "JOIN source_roots s ON s.source_id=f.source_id "
                f"WHERE f.asset_id IN ({placeholders}) ORDER BY f.asset_id, "
                "CASE WHEN f.availability='PRESENT' AND s.status='ONLINE' THEN 0 ELSE 1 END, "
                "f.location_id", ids,
            ):
                locations.setdefault(int(row["asset_id"]), []).append(dict(row))
    # Path checks are outside the SQL transaction and only over the fixed sample.
    try:
        assets = [_locate({**metadata[row["asset_id"]],
                           "selection_stratum": row["selection_stratum"]},
                          locations.get(row["asset_id"], ())) for row in selected]
        source_roots = sorted({str(Path(location["root_path"]).resolve())
                               for group in locations.values() for location in group})
        if identity != _fingerprint(_database_path(data_root)):
            raise PilotCatalogError("CATALOG_CHANGED_DURING_READ")
    except OSError:
        raise PilotCatalogError("SAMPLE_INPUT_UNREADABLE") from None
    sample_hash = hashlib.sha256(json.dumps(
        [{key: row[key] for key in ("asset_id", "sha256", "selection_stratum")} for row in assets],
        sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    return {"schema": "wemm-pilot-sample-v1", "source_identity": preflight["source_identity"],
            "seed": seed, "requested_max_assets": max_assets, "sample_sha256": sample_hash,
            "population_assets": len(population), "assets": assets, "source_roots": source_roots,
            "coverage": preflight["coverage"],
            "input_status_counts": dict(Counter(row["input_status"] for row in assets)),
            "source_bytes_verified": False,
            "selection_policy": "metadata eligible; at least half random; no failure replacement"}
