"""The offline name-dataset registry: import, verify, activate, roll back and clean up.

ADR-0005 §8 fixes the lifecycle ``STAGED -> VALIDATED -> ACTIVE -> RETAINED`` plus an
explicit ``RETAINED -> ACTIVE`` rollback. The package files are read-only and immutable;
the catalog row is the authority for its state, and the singleton ``active_geo_dataset``
pointer is moved in the same SQLite transaction that retires the previous package and
materialises the new one's places.

A missing or broken package only ever blocks resolution: the manual place directory,
manual assignment, filtering and the operation history keep working.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from photo_search.catalog import schema as schema_module
from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import (
    PLACE_DATASET_INVALID,
    PLACE_DATASET_MISSING,
    PLACE_NOT_FOUND,
    PLACE_REFERENCED,
    CatalogError,
)
from photo_search.config import (
    DATASETS_DIRNAME,
    PLACES_DIRNAME,
    place_datasets_dir,
)
from photo_search.geo import dataset as dataset_module
from photo_search.geo.dataset import DatasetContent, DatasetFile
from photo_search.places import revision as revision_module
from photo_search.places.contracts import (
    CONTRACT_VERSION,
    fail,
    normalize_name,
)
from photo_search.places.contracts import (
    display_name as place_display_name,
)

CAUSE_DATASET_ACTIVATED = "PLACE_DATASET_ACTIVATED"

DATASET_ROW_FIELDS = (
    "dataset_id, package_id, package_version, contract_version, manifest_schema, provider, "
    "source_url, source_snapshot, license_id, license_url, attribution, builder_id, "
    "builder_version, parameters_sha256, content_sha256, coordinate_reference_system, "
    "coverage_json, compatible_schema_min, compatible_schema_max, relative_dir, "
    "previous_dataset_id, state, record_count, file_count, generated_at, validated_at, "
    "activated_at, retained_at, created_at, updated_at"
)

#: The only states a rollback may return to.
ROLLBACK_STATES = ("VALIDATED", "RETAINED")


def dataset_relative_dir(dataset_id: str) -> str:
    """The package directory relative to the data root, as stored in the catalog."""
    return f"{PLACES_DIRNAME}/{DATASETS_DIRNAME}/{dataset_id}"


def dataset_dir(data_root: str | Path | None, dataset_id: str) -> Path:
    """The package directory of one dataset inside the application data root."""
    name = str(dataset_id)
    if not name or name != Path(name).name or Path(name).is_absolute() or ".." in name:
        raise fail(PLACE_DATASET_INVALID, "the dataset id is not a safe directory name")
    root = place_datasets_dir(data_root)
    resolved = (root / name).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise fail(PLACE_DATASET_INVALID, "the dataset directory escapes the data root")
    return resolved


def _rows(connection: sqlite3.Connection, sql: str, parameters: tuple = ()) -> list[sqlite3.Row]:
    return list(connection.execute(sql, parameters).fetchall())


def dataset_row(connection: sqlite3.Connection, dataset_id: str) -> sqlite3.Row | None:
    return connection.execute(
        f"SELECT {DATASET_ROW_FIELDS} FROM geo_datasets WHERE dataset_id = ?", (str(dataset_id),)
    ).fetchone()


def active_row(connection: sqlite3.Connection) -> sqlite3.Row | None:
    """The active dataset row, or ``None`` when no package is installed."""
    pointer = connection.execute(
        "SELECT dataset_id FROM active_geo_dataset WHERE singleton = 1"
    ).fetchone()
    if pointer is None or pointer["dataset_id"] is None:
        return None
    return dataset_row(connection, str(pointer["dataset_id"]))


def resolver_available(connection: sqlite3.Connection) -> bool:
    row = active_row(connection)
    return row is not None and str(row["state"]) in ("VALIDATED", "ACTIVE")


# ------------------------------------------------------------------ API projections
def dataset_view(
    row: Mapping[str, Any], *, problems: Iterable[Mapping[str, Any]] = ()
) -> dict[str, Any]:
    """One dataset record for the API. Never carries a local path."""
    return {
        "contract_version": CONTRACT_VERSION,
        "dataset_id": str(row["dataset_id"]),
        "package_id": str(row["package_id"]),
        "package_version": str(row["package_version"]),
        "provider": str(row["provider"]),
        "license_id": str(row["license_id"]),
        "attribution": str(row["attribution"]),
        "state": str(row["state"]),
        "coverage": json.loads(str(row["coverage_json"])),
        "compatible_catalog_schema": {
            "minimum": int(row["compatible_schema_min"]),
            "maximum": int(row["compatible_schema_max"]),
        },
        "record_count": int(row["record_count"]),
        "file_count": int(row["file_count"]),
        "previous_dataset_id": (
            None if row["previous_dataset_id"] is None else str(row["previous_dataset_id"])
        ),
        "generated_at": int(row["generated_at"]),
        "validated_at": None if row["validated_at"] is None else int(row["validated_at"]),
        "activated_at": None if row["activated_at"] is None else int(row["activated_at"]),
        "retained_at": None if row["retained_at"] is None else int(row["retained_at"]),
        "problems": [dict(problem) for problem in problems],
    }


def verify_dataset_on_disk(
    row: Mapping[str, Any],
    *,
    data_root: str | Path | None,
    supported_schema_version: int,
) -> tuple[list[dict[str, Any]], bool]:
    """Verify one package on disk; returns ``(problems, available)`` and never raises."""
    try:
        dataset_module.load_content(
            dataset_dir(data_root, str(row["dataset_id"])),
            supported_schema_version=int(supported_schema_version),
        )
    except CatalogError as error:
        return (
            [
                {
                    "code": str(error.code),
                    "message": str(error.message),
                    "details": dict(error.details),
                }
            ],
            False,
        )
    return ([], True)


def dataset_status(
    connection: sqlite3.Connection,
    *,
    data_root: str | Path | None = None,
    supported_schema_version: int | None = None,
) -> dict[str, Any]:
    """The ``GET /places/datasets`` projection: real state, never a fabricated ACTIVE one."""
    supported = (
        int(schema_module.SUPPORTED_SCHEMA_VERSION)
        if supported_schema_version is None
        else int(supported_schema_version)
    )
    rows = _rows(
        connection,
        f"SELECT {DATASET_ROW_FIELDS} FROM geo_datasets ORDER BY created_at, dataset_id",
    )
    active = active_row(connection)
    rendered: list[dict[str, Any]] = []
    active_problems: list[dict[str, Any]] = []
    active_available = False
    for row in rows:
        problems, available = verify_dataset_on_disk(
            row, data_root=data_root, supported_schema_version=supported
        )
        rendered.append(dataset_view(row, problems=problems))
        if active is not None and str(row["dataset_id"]) == str(active["dataset_id"]):
            active_problems = problems
            active_available = available
    if active is None:
        state = "unavailable" if not rows else "installed"
        reason = "PLACE_DATASET_NOT_INSTALLED" if not rows else "NO_ACTIVE_DATASET"
    elif not active_available:
        defect = str(active_problems[0]["code"]) if active_problems else PLACE_DATASET_MISSING
        state = "missing" if defect == PLACE_DATASET_MISSING else "invalid"
        reason = defect
    else:
        state = "active"
        reason = None
    return {
        "contract_version": CONTRACT_VERSION,
        "state": state,
        "reason": reason,
        "resolver_available": bool(active_available),
        "active_dataset": None if active is None else dataset_view(active, problems=active_problems),
        "datasets": rendered,
        "suggestion_revision": revision_module.read_suggestion_revision(connection),
    }


def _root_for(catalog: Catalog, data_root: str | Path | None) -> Path:
    """The data root a write path must use: the caller's, else this catalog's own root.

    Never the process default: a diagnostic or test catalog opened over an explicit data
    root must not fall back to ``%LOCALAPPDATA%`` and materialise a package there.
    """
    return Path(data_root) if data_root is not None else Path(catalog.data_root)


# ---------------------------------------------------------------------- import/verify
def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(str(path), os.O_RDONLY)
    except OSError:  # pragma: no cover - Windows may refuse a directory handle
        return
    try:
        os.fsync(descriptor)
    except OSError:  # pragma: no cover - not every filesystem supports directory fsync
        pass
    finally:
        os.close(descriptor)


def _copy_package_files(source: Path, destination: Path, files: tuple[DatasetFile, ...]) -> None:
    """Copy the verified files into the data root, fsync each, then fsync the directory."""
    destination.mkdir(parents=True, exist_ok=True)
    for item in files:
        target = destination / item.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        data = (source / item.relative_path).read_bytes()
        with target.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    # The manifest is copied last: a package without one is visibly incomplete.
    with (destination / dataset_module.MANIFEST_FILENAME).open("wb") as stream:
        stream.write((source / dataset_module.MANIFEST_FILENAME).read_bytes())
        stream.flush()
        os.fsync(stream.fileno())
    _fsync_directory(destination)


def _insert_dataset_rows(
    connection: sqlite3.Connection,
    content: DatasetContent,
    *,
    relative_dir: str,
    now_ms: int,
) -> None:
    manifest = content.manifest
    connection.execute(
        "INSERT INTO geo_datasets(dataset_id, package_id, package_version, contract_version, "
        "manifest_schema, provider, source_url, source_snapshot, license_id, license_url, "
        "attribution, builder_id, builder_version, parameters_sha256, content_sha256, "
        "coordinate_reference_system, coverage_json, compatible_schema_min, "
        "compatible_schema_max, relative_dir, previous_dataset_id, state, record_count, "
        "file_count, generated_at, validated_at, activated_at, retained_at, created_at, "
        "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
        "'STAGED', ?, ?, ?, NULL, NULL, NULL, ?, ?)",
        (
            content.dataset_id,
            content.package_id,
            content.package_version,
            manifest["contract_version"],
            manifest["manifest_schema"],
            manifest["source"]["provider"],
            manifest["source"]["source_url"],
            manifest["source"]["snapshot"],
            manifest["source"]["license_id"],
            manifest["source"]["license_url"],
            manifest["source"]["attribution"],
            manifest["build"]["builder_id"],
            manifest["build"]["builder_version"],
            manifest["build"]["parameters_sha256"],
            manifest["content_sha256"],
            manifest["coordinate_reference_system"],
            _canonical(manifest["coverage"]),
            int(manifest["compatible_catalog_schema"]["minimum"]),
            int(manifest["compatible_catalog_schema"]["maximum"]),
            relative_dir,
            manifest["previous_dataset_id"],
            int(manifest["coverage"]["record_count"]),
            len(content.files),
            int(manifest["build"]["generated_at"]),
            int(now_ms),
            int(now_ms),
        ),
    )
    for item in content.files:
        connection.execute(
            "INSERT INTO geo_dataset_files(dataset_id, relative_path, role, media_type, "
            "sha256, byte_size, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                content.dataset_id,
                item.relative_path,
                item.role,
                item.media_type,
                item.sha256,
                int(item.byte_size),
                int(now_ms),
            ),
        )


def _verify_stored(connection: sqlite3.Connection, row: Mapping[str, Any], root: Path) -> DatasetContent:
    """Re-verify a stored package before it is validated, activated or rolled back to."""
    return dataset_module.load_content(
        dataset_dir(root, str(row["dataset_id"])),
        supported_schema_version=int(schema_module.SUPPORTED_SCHEMA_VERSION),
    )


def stage_package(
    catalog: Catalog,
    source: str | Path,
    *,
    now_ms: int | None = None,
    data_root: str | Path | None = None,
) -> dict[str, Any]:
    """Verify a built package and copy it into a dataset directory inside the data root.

    The row starts as ``STAGED``: an import alone never makes a package usable, and a
    defect found here leaves the active pointer untouched.
    """
    root = _root_for(catalog, data_root)
    source_dir = Path(source)
    content = dataset_module.load_content(
        source_dir, supported_schema_version=int(schema_module.SUPPORTED_SCHEMA_VERSION)
    )
    with catalog.write() as connection:
        if dataset_row(connection, content.dataset_id) is not None:
            raise fail(
                PLACE_DATASET_INVALID,
                "this dataset_id is already imported",
                dataset_id=content.dataset_id,
            )
        now = int(catalog.now_ms() if now_ms is None else now_ms)
        _copy_package_files(source_dir, dataset_dir(root, content.dataset_id), content.files)
        _insert_dataset_rows(
            connection,
            content,
            relative_dir=dataset_relative_dir(content.dataset_id),
            now_ms=now,
        )
        return dataset_view(
            dataset_row(connection, content.dataset_id) or {},
            problems=[],
        )


def validate_dataset(
    catalog: Catalog,
    dataset_id: str,
    *,
    now_ms: int | None = None,
    data_root: str | Path | None = None,
) -> dict[str, Any]:
    """Re-verify a staged package and mark it ``VALIDATED``."""
    root = _root_for(catalog, data_root)
    with catalog.write() as connection:
        row = dataset_row(connection, dataset_id)
        if row is None:
            raise fail(PLACE_NOT_FOUND, "unknown dataset", dataset_id=str(dataset_id))
        if str(row["state"]) not in ("STAGED", "VALIDATED"):
            raise fail(
                PLACE_DATASET_INVALID,
                "only a staged dataset can be validated",
                dataset_id=str(dataset_id),
                state=str(row["state"]),
            )
        _verify_stored(connection, row, root)
        now = int(catalog.now_ms() if now_ms is None else now_ms)
        connection.execute(
            "UPDATE geo_datasets SET state = 'VALIDATED', validated_at = COALESCE(validated_at, ?), "
            "updated_at = ? WHERE dataset_id = ?",
            (now, now, str(dataset_id)),
        )
        return dataset_view(dataset_row(connection, dataset_id) or {}, problems=[])


# ------------------------------------------------------------- activate/rollback/cleanup
def _has_resolutions(connection: sqlite3.Connection) -> bool:
    row = connection.execute("SELECT 1 FROM place_resolutions LIMIT 1").fetchone()
    return row is not None


def _set_active(
    connection: sqlite3.Connection,
    *,
    content: DatasetContent,
    now_ms: int,
) -> dict[str, Any]:
    """Retire the previous package and activate this one inside the caller's transaction."""
    dataset_id = content.dataset_id
    connection.execute(
        "UPDATE geo_datasets SET state = 'RETAINED', retained_at = ?, updated_at = ? "
        "WHERE state = 'ACTIVE' AND dataset_id <> ?",
        (int(now_ms), int(now_ms), dataset_id),
    )
    connection.execute(
        "UPDATE geo_datasets SET state = 'ACTIVE', activated_at = ?, updated_at = ? "
        "WHERE dataset_id = ?",
        (int(now_ms), int(now_ms), dataset_id),
    )
    connection.execute(
        "UPDATE active_geo_dataset SET dataset_id = ?, revision = revision + 1, "
        "updated_at = ?, cause = ? WHERE singleton = 1",
        (dataset_id, int(now_ms), CAUSE_DATASET_ACTIVATED),
    )
    mapping = materialize_dataset_places(connection, content, now_ms=int(now_ms))
    suggestion_revision = revision_module.read_suggestion_revision(connection)
    if _has_resolutions(connection):
        # Every stored resolution named a dataset; a different active package changes the
        # set of candidates that may be shown, so the independent suggestion revision moves.
        suggestion_revision = revision_module.bump_suggestion_revision(
            connection, cause=CAUSE_DATASET_ACTIVATED, now_ms=int(now_ms)
        )
    return {
        "dataset_id": dataset_id,
        "state": "ACTIVE",
        "places_mapped": len(mapping),
        "suggestion_revision": int(suggestion_revision),
    }


def activate_dataset(
    catalog: Catalog,
    dataset_id: str,
    *,
    now_ms: int | None = None,
    data_root: str | Path | None = None,
) -> dict[str, Any]:
    """Re-verify a package and make it the active one in one SQLite transaction.

    The package is verified against its own manifest *before* the transaction opens, so a
    tampered or half-removed package fails with ``PLACE_DATASET_INVALID`` and the previous
    active pointer keeps working (ADR-0005 §8).
    """
    root = _root_for(catalog, data_root)
    with catalog.write() as connection:
        row = dataset_row(connection, dataset_id)
        if row is None:
            raise fail(PLACE_NOT_FOUND, "unknown dataset", dataset_id=str(dataset_id))
        if str(row["state"]) not in ("VALIDATED", "RETAINED", "ACTIVE"):
            raise fail(
                PLACE_DATASET_INVALID,
                "only a validated or retained dataset can be activated",
                dataset_id=str(dataset_id),
                state=str(row["state"]),
            )
        content = _verify_stored(connection, row, root)
        now = int(catalog.now_ms() if now_ms is None else now_ms)
        return _set_active(connection, content=content, now_ms=now)


def rollback_dataset(
    catalog: Catalog,
    *,
    now_ms: int | None = None,
    data_root: str | Path | None = None,
) -> dict[str, Any]:
    """Activate the previous validated/retained package again.

    The target is the active package's declared predecessor when it is still usable,
    otherwise the most recently activated retained/validated package.
    """
    root = _root_for(catalog, data_root)
    with catalog.write() as connection:
        active = active_row(connection)
        if active is None:
            raise fail(
                PLACE_DATASET_MISSING,
                "there is no active dataset to roll back from",
                dataset_id=None,
            )
        target: sqlite3.Row | None = None
        previous_id = active["previous_dataset_id"]
        if previous_id is not None:
            candidate = dataset_row(connection, str(previous_id))
            if candidate is not None and str(candidate["state"]) in ROLLBACK_STATES:
                target = candidate
        if target is None:
            placeholders = ", ".join("?" for _ in ROLLBACK_STATES)
            target = connection.execute(
                f"SELECT {DATASET_ROW_FIELDS} FROM geo_datasets "
                f"WHERE state IN ({placeholders}) AND dataset_id <> ? "
                "ORDER BY COALESCE(retained_at, activated_at, validated_at, created_at) DESC, "
                "dataset_id DESC LIMIT 1",
                (*ROLLBACK_STATES, str(active["dataset_id"])),
            ).fetchone()
        if target is None:
            raise fail(
                PLACE_DATASET_MISSING,
                "there is no retained previous dataset to roll back to",
                dataset_id=str(active["dataset_id"]),
            )
        content = _verify_stored(connection, target, root)
        now = int(catalog.now_ms() if now_ms is None else now_ms)
        result = _set_active(connection, content=content, now_ms=now)
        result["rolled_back_from"] = str(active["dataset_id"])
        return result


def cleanup_dataset(
    catalog: Catalog,
    dataset_id: str,
    *,
    now_ms: int | None = None,
    data_root: str | Path | None = None,
) -> dict[str, Any]:
    """Delete an unreferenced package and its registry rows.

    A package referenced by the active pointer, a resolution, a rollback edge or the
    currently active state is refused with ``PLACE_REFERENCED`` (ADR-0005 §8).
    """
    root = _root_for(catalog, data_root)
    target: Path | None = None
    with catalog.write() as connection:
        row = dataset_row(connection, dataset_id)
        if row is None:
            raise fail(PLACE_NOT_FOUND, "unknown dataset", dataset_id=str(dataset_id))
        active = active_row(connection)
        if str(row["state"]) == "ACTIVE" or (
            active is not None and str(active["dataset_id"]) == str(dataset_id)
        ):
            raise fail(
                PLACE_REFERENCED,
                "the active dataset cannot be cleaned up",
                dataset_id=str(dataset_id),
            )
        if connection.execute(
            "SELECT 1 FROM place_resolutions WHERE dataset_id = ? LIMIT 1", (str(dataset_id),)
        ).fetchone() is not None:
            raise fail(
                PLACE_REFERENCED,
                "a dataset referenced by a stored resolution cannot be cleaned up",
                dataset_id=str(dataset_id),
            )
        if connection.execute(
            "SELECT 1 FROM geo_datasets WHERE previous_dataset_id = ? LIMIT 1",
            (str(dataset_id),),
        ).fetchone() is not None:
            raise fail(
                PLACE_REFERENCED,
                "a dataset still used as a rollback target cannot be cleaned up",
                dataset_id=str(dataset_id),
            )
        connection.execute("DELETE FROM geo_dataset_files WHERE dataset_id = ?", (str(dataset_id),))
        connection.execute("DELETE FROM geo_datasets WHERE dataset_id = ?", (str(dataset_id),))
        target = dataset_dir(root, str(dataset_id))
    if target is not None:
        datasets_root = place_datasets_dir(root).resolve()
        resolved = target.resolve()
        if resolved.parent == datasets_root and resolved.is_dir():
            shutil.rmtree(resolved)
    return {"dataset_id": str(dataset_id), "state": "REMOVED"}


# --------------------------------------------------------------- place materialisation
def materialize_dataset_places(
    connection: sqlite3.Connection,
    content: DatasetContent,
    *,
    now_ms: int,
) -> dict[str, int]:
    """Map the dataset's countries/cities onto local places through ``place_external_refs``.

    ADR-0005 §2: an existing ``(provider, external_id)`` mapping is reused and a user's
    canonical name, aliases and parent are never overwritten by a package. Only places this
    package introduces are created, and their names/aliases come from the package.
    """
    provider = content.provider
    existing = {
        str(row["external_id"]): int(row["place_id"])
        for row in connection.execute(
            "SELECT external_id, place_id FROM place_external_refs WHERE provider = ?",
            (provider,),
        ).fetchall()
    }
    ordered = sorted(
        content.places.values(),
        key=lambda place: (0 if place.kind == "country" else 1, place.dataset_place_id),
    )
    mapping: dict[str, int] = {}
    created: set[str] = set()
    for place in ordered:
        known = existing.get(place.dataset_place_id)
        if known is not None:
            mapping[place.dataset_place_id] = known
            continue
        display = place_display_name(place.display_name, field="canonical_name")
        cursor = connection.execute(
            "INSERT INTO places(kind, canonical_name, normalized_name, parent_place_id, origin, "
            "lifecycle, revision, created_at, updated_at) "
            "VALUES (?, ?, ?, NULL, 'DATASET', 'ACTIVE', 1, ?, ?)",
            (place.kind, display, normalize_name(display), int(now_ms), int(now_ms)),
        )
        place_id = int(cursor.lastrowid)
        connection.execute(
            "INSERT INTO place_external_refs(provider, external_id, place_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            (provider, place.dataset_place_id, place_id, int(now_ms)),
        )
        mapping[place.dataset_place_id] = place_id
        created.add(place.dataset_place_id)
    for place in ordered:
        if place.dataset_place_id not in created or place.parent_dataset_place_id is None:
            continue
        parent = content.places.get(place.parent_dataset_place_id)
        parent_local = mapping.get(place.parent_dataset_place_id)
        if parent is None or parent_local is None or parent.kind not in ("country", "city"):
            raise fail(
                PLACE_DATASET_INVALID,
                "the dataset hierarchy names a parent that cannot be used",
                dataset_id=content.dataset_id,
            )
        connection.execute(
            "UPDATE places SET parent_place_id = ?, updated_at = ? WHERE place_id = ?",
            (int(parent_local), int(now_ms), int(mapping[place.dataset_place_id])),
        )
    for place in ordered:
        place_id = int(mapping[place.dataset_place_id])
        for alias in content.aliases.get(place.dataset_place_id, ()):
            text = place_display_name(alias["text"], field="alias")
            normalized = normalize_name(text, field="alias")
            present = connection.execute(
                "SELECT 1 FROM place_aliases WHERE place_id = ? AND normalized = ? "
                "AND lifecycle = 'ACTIVE' LIMIT 1",
                (place_id, normalized),
            ).fetchone()
            if present is not None:
                continue
            connection.execute(
                "INSERT INTO place_aliases(place_id, text, normalized, language, source, "
                "lifecycle, revision, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'DATASET', 'ACTIVE', 1, ?, ?)",
                (place_id, text, normalized, alias["language"], int(now_ms), int(now_ms)),
            )
    return mapping
