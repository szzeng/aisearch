"""Read-only taken-time projections: the per-asset detail and the honesty block.

Everything here is a pure read of the catalog; no path writes anything, and no response ever
carries ``taken_at_raw`` (ADR-0011 §8.4). Two rules of the contract live here because they
are read-side decisions:

* ``filter_active`` - the capture *range* condition only applies while the library's stored
  ``taken_time_rules_version`` equals this build's. When it does not, existing rows must not
  be trusted and must not participate in filtering (ADR-0011 §2), so the predicate becomes
  unsatisfiable and the honesty block reports ``PENDING`` with zero trusted counts.
* ``asset_view`` - the same gate applies to the per-asset object: an untrusted derived row is
  not published as if it were a fact. The row stays in the catalog; the caller sees ``null``
  (and the honesty block explains why), never a stale value dressed up as a fresh one.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from photo_search.taken_time import contract

#: The six stored columns, in the order the detail object needs them.
COLUMNS = (
    "taken_at_raw",
    "taken_at_sort",
    "time_source",
    "time_confidence",
    "timezone_offset",
    "camera",
)


def rules_version(connection: sqlite3.Connection) -> int | None:
    """The stored library-level rule version, or ``None`` when it is absent/unreadable."""
    try:
        row = connection.execute(
            "SELECT value FROM metadata WHERE key = ?", (contract.RULES_VERSION_KEY,)
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None or row[0] is None:
        return None
    try:
        return int(str(row[0]).strip())
    except (TypeError, ValueError):
        return None


def rules_version_matches(connection: sqlite3.Connection) -> bool:
    """Whether the stored rows may be trusted (and therefore filtered) by this build."""
    return rules_version(connection) == contract.RULES_VERSION


def filter_active(connection: sqlite3.Connection) -> bool:
    """ADR-0011 §2: a capture condition may only run on trusted rows."""
    return rules_version_matches(connection)


def capture_predicate(
    connection: sqlite3.Connection,
    capture: contract.CaptureRange | None,
    *,
    alias: str = "a",
) -> tuple[str, list[Any]]:
    """ADR-0011 §6.4: the one capture range predicate over ``<alias>.asset_id``.

    Both surfaces that may carry a capture condition - ``GET /api/v1/assets`` and the search
    scope every retrieval channel is restricted by - use this single fragment, so no surface
    can drift into a second interpretation of the same condition:

    * only ``label = capture`` rows (``EXIF_ORIGINAL``/``EXIF_DIGITIZED``) can match, so
      ``EXIF_ROOT``/``FILE_MTIME``/a missing row never satisfies a time condition;
    * the comparison is civil-to-civil (bounds and ``taken_at_sort`` are wall-clock keys), so
      no timezone conversion happens anywhere;
    * while the stored ``taken_time_rules_version`` differs from this build's the predicate is
      unsatisfiable (``0``), because ADR-0011 §2 forbids filtering on untrusted rows.
    """
    if capture is None or not capture.is_set:
        return "1", []
    if not filter_active(connection):
        return "0", []
    places = ", ".join("?" for _ in contract.CAPTURE_SOURCES)
    pieces = [
        "EXISTS (SELECT 1 FROM asset_metadata AS m WHERE m.asset_id = "
        f"{alias}.asset_id AND m.time_source IN ({places}) AND m.taken_at_sort IS NOT NULL"
    ]
    parameters: list[Any] = [*contract.CAPTURE_SOURCES]
    if capture.from_key is not None:
        pieces.append(" AND m.taken_at_sort >= ?")
        parameters.append(int(capture.from_key))
    if capture.to_key is not None:
        pieces.append(" AND m.taken_at_sort < ?")
        parameters.append(int(capture.to_key))
    pieces.append(")")
    return "".join(pieces), parameters


def counts(connection: sqlite3.Connection) -> tuple[int, int, int]:
    """``(assets_total, extracted_assets, capture_time_assets)`` as stored, ungated."""
    assets_total = int(
        connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
    )
    extracted = int(
        connection.execute("SELECT COUNT(*) FROM asset_metadata").fetchone()[0]
    )
    captured = int(
        connection.execute(
            "SELECT COUNT(*) FROM asset_metadata WHERE time_source IN "
            "('EXIF_ORIGINAL', 'EXIF_DIGITIZED')"
        ).fetchone()[0]
    )
    return assets_total, extracted, captured


def state_block(connection: sqlite3.Connection) -> dict[str, Any]:
    """ADR-0011 §8.1: the four-key honesty block, rule-version gate applied."""
    assets_total, extracted, captured = counts(connection)
    return contract.taken_time_block(
        assets_total=assets_total,
        extracted_assets=extracted,
        capture_time_assets=captured,
        rules_version_matches=rules_version_matches(connection),
    )


def asset_view(connection: sqlite3.Connection, asset_id: int) -> dict[str, Any] | None:
    """ADR-0011 §8.1 item 3: the closed per-asset object, or ``None`` when unavailable."""
    if not rules_version_matches(connection):
        return None
    row = connection.execute(
        f"SELECT {', '.join(COLUMNS)} FROM asset_metadata WHERE asset_id = ?",
        (int(asset_id),),
    ).fetchone()
    if row is None:
        return None
    sort_key = None if row["taken_at_sort"] is None else int(row["taken_at_sort"])
    source = str(row["time_source"])
    return {
        "taken_at": None if sort_key is None else contract.normalize_bound(sort_key),
        "taken_at_sort": sort_key,
        "time_source": source,
        "time_confidence": str(row["time_confidence"]),
        "timezone_offset_minutes": (
            None if row["timezone_offset"] is None else int(row["timezone_offset"])
        ),
        "label": contract.label_for(source),
        "camera": None if row["camera"] is None else str(row["camera"]),
    }


def asset_label(connection: sqlite3.Connection, asset_id: int) -> str | None:
    """The ``capture``/``file_time`` label of one asset, or ``None`` when unpublished."""
    view = asset_view(connection, asset_id)
    return None if view is None else str(view["label"])


__all__ = [
    "COLUMNS",
    "asset_label",
    "asset_view",
    "capture_predicate",
    "counts",
    "filter_active",
    "rules_version",
    "rules_version_matches",
    "state_block",
]
