"""The place service: one object the transport layer can call without owning a transaction.

Reads go straight through the catalog connection; every write runs inside
``Catalog.write()`` and is handed the injected P2-03 projection refresh, so an authority
change and its outbox event share one ``BEGIN IMMEDIATE`` transaction.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping, Sequence
from typing import Any

from photo_search.catalog.db import Catalog
from photo_search.places import assignments as assignment_module
from photo_search.places import directory
from photo_search.places import filter as filter_module
from photo_search.places import revision as revision_module
from photo_search.places.contracts import CONTRACT_VERSION, fail, place_view

#: Place-list cursor prefix; the token carries the directory revision it was minted under.
PLACE_CURSOR_PREFIX = "pl1"
OPERATION_CURSOR_PREFIX = "op1"
DEFAULT_PLACE_PAGE_LIMIT = 50
MAX_PLACE_PAGE_LIMIT = 200


def encode_place_cursor(*, revision: int, place_id: int) -> str:
    payload = f"{PLACE_CURSOR_PREFIX}:{int(revision)}:{int(place_id)}"
    return base64.urlsafe_b64encode(payload.encode("ascii")).decode("ascii").rstrip("=")


def decode_place_cursor(text: str | None, *, current_revision: int) -> int:
    """Decode a directory cursor; a stale revision is a structured 409, not a silent page."""
    if text is None or str(text).strip() == "":
        return 0
    raw = str(text).strip()
    try:
        decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("ascii")
    except (binascii.Error, UnicodeDecodeError, ValueError) as error:
        raise fail("INVALID_CURSOR", "the pagination cursor is not valid") from error
    parts = decoded.split(":")
    if len(parts) != 3 or parts[0] != PLACE_CURSOR_PREFIX:
        raise fail("INVALID_CURSOR", "the pagination cursor is not valid")
    try:
        revision = int(parts[1])
        place_id = int(parts[2])
    except ValueError as error:
        raise fail("INVALID_CURSOR", "the pagination cursor is not valid") from error
    if revision != int(current_revision):
        raise fail(
            "CATALOG_FILTER_REVISION_STALE",
            "the place directory changed since this cursor was issued",
            current_revision=int(current_revision),
        )
    return place_id


def clamp_place_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_PLACE_PAGE_LIMIT
    if int(limit) < 1:
        raise fail("VALIDATION_ERROR", "limit must be at least 1")
    return min(int(limit), MAX_PLACE_PAGE_LIMIT)


class PlaceService:
    """Thin facade over the catalog plus the place modules."""

    def __init__(
        self,
        catalog: Catalog,
        *,
        refresh: revision_module.ProjectionRefresh | None = None,
        clock: Any | None = None,
    ) -> None:
        self.catalog = catalog
        self.refresh = (
            refresh if refresh is not None else revision_module.projection_refresher(catalog)
        )
        self.clock = clock or catalog.now_ms

    @classmethod
    def open(cls, catalog: Catalog, **options: Any) -> PlaceService:
        return cls(catalog, **options)

    # ------------------------------------------------------------------- reads
    def list_places(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        name: str | None = None,
        kind: str | None = None,
        parent_place_id: int | None = None,
        ancestor_place_id: int | None = None,
        lifecycle: str | None = None,
    ) -> dict[str, Any]:
        connection = self.catalog.connection
        revision = revision_module.read_filter_revision(connection)
        after = decode_place_cursor(cursor, current_revision=revision)
        rows = directory.list_places(
            connection,
            limit=int(limit) + 1,
            after_place_id=after,
            name=name,
            kind=kind,
            parent_place_id=parent_place_id,
            ancestor_place_id=ancestor_place_id,
            lifecycle=lifecycle,
        )
        has_more = len(rows) > int(limit)
        rows = rows[: int(limit)]
        places = [
            place_view(
                row,
                aliases=directory.active_alias_rows(connection, int(row["place_id"])),
                external_refs=directory.external_ref_rows(connection, int(row["place_id"])),
            )
            for row in rows
        ]
        next_cursor = None
        if has_more and rows:
            next_cursor = encode_place_cursor(
                revision=revision, place_id=int(rows[-1]["place_id"])
            )
        return {
            "places": places,
            "limit": int(limit),
            "next_cursor": next_cursor,
            "has_more": has_more,
            "catalog_filter_revision": revision,
        }

    def get_place(self, place_id: int) -> dict[str, Any]:
        return directory.get_place_view(self.catalog.connection, int(place_id))

    def coverage(self) -> dict[str, Any]:
        return filter_module.coverage(self.catalog.connection)

    def asset_place(self, asset_id: int) -> dict[str, Any]:
        self._require_asset(int(asset_id))
        return filter_module.asset_place_view(self.catalog.connection, int(asset_id))

    def asset_geo(self, asset_id: int) -> dict[str, Any] | None:
        self._require_asset(int(asset_id))
        return filter_module.geo_fact_view(self.catalog.connection, int(asset_id))

    def list_operations(self, *, limit: int, cursor: str | None = None) -> dict[str, Any]:
        connection = self.catalog.connection
        after = 0
        if cursor:
            raw = str(cursor).strip()
            try:
                decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("ascii")
                prefix, _, value = decoded.partition(":")
                if prefix != OPERATION_CURSOR_PREFIX:
                    raise ValueError(prefix)
                after = int(value)
            except (binascii.Error, UnicodeDecodeError, ValueError) as error:
                raise fail("INVALID_CURSOR", "the pagination cursor is not valid") from error
        rows = assignment_module.list_operations(
            connection, limit=int(limit) + 1, after_revision=after
        )
        has_more = len(rows) > int(limit)
        rows = rows[: int(limit)]
        operations = [_operation_record(row) for row in rows]
        next_cursor = None
        if has_more and rows:
            payload = f"{OPERATION_CURSOR_PREFIX}:{int(rows[-1]['operation_revision'])}"
            next_cursor = (
                base64.urlsafe_b64encode(payload.encode("ascii")).decode("ascii").rstrip("=")
            )
        return {
            "operations": operations,
            "limit": int(limit),
            "next_cursor": next_cursor,
            "has_more": has_more,
        }

    # ------------------------------------------------------------------ writes
    def create_place(
        self,
        *,
        kind: str,
        canonical_name: str,
        parent_place_id: int | None = None,
        aliases: Sequence[Mapping[str, Any]] = (),
        origin: str = "USER",
    ) -> dict[str, Any]:
        now_ms = int(self.clock())
        with self.catalog.write() as connection:
            place_id = directory.create_place(
                connection,
                kind=str(kind),
                canonical_name=canonical_name,
                parent_place_id=parent_place_id,
                origin=str(origin),
                aliases=aliases,
                now_ms=now_ms,
            )
            return directory.get_place_view(connection, place_id)

    def patch_place(
        self,
        *,
        place_id: int,
        expected_revision: int,
        canonical_name: str | None = None,
        aliases: Sequence[Mapping[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        now_ms = int(self.clock())
        with self.catalog.write() as connection:
            return directory.patch_place(
                connection,
                place_id=int(place_id),
                expected_revision=int(expected_revision),
                canonical_name=canonical_name,
                aliases=aliases,
                now_ms=now_ms,
                refresh=self.refresh,
            )

    def retire_place(self, *, place_id: int) -> dict[str, Any]:
        now_ms = int(self.clock())
        with self.catalog.write() as connection:
            return directory.retire_place(
                connection, place_id=int(place_id), now_ms=now_ms, refresh=self.refresh
            )

    def write_assignment(
        self,
        *,
        asset_id: int,
        action: str,
        target_place_id: int | None,
        request_id: str,
        expected_revision: int,
        batch: bool = False,
    ) -> dict[str, Any]:
        now_ms = int(self.clock())
        items = [
            {
                "asset_id": int(asset_id),
                "action": str(action),
                "target_place_id": target_place_id,
                "expected_revision": expected_revision,
            }
        ]
        with self.catalog.write() as connection:
            return assignment_module.apply_request(
                connection,
                items=items,
                request_id=request_id,
                now_ms=now_ms,
                refresh=self.refresh,
                batch=batch,
            )

    def write_batch(
        self,
        *,
        items: Sequence[Mapping[str, Any]],
        request_id: str,
    ) -> dict[str, Any]:
        now_ms = int(self.clock())
        with self.catalog.write() as connection:
            return assignment_module.apply_request(
                connection,
                items=items,
                request_id=request_id,
                now_ms=now_ms,
                refresh=self.refresh,
                batch=True,
            )

    def undo(
        self,
        *,
        operation_id: str,
        request_id: str,
        expected_operation_revision: int,
    ) -> dict[str, Any]:
        now_ms = int(self.clock())
        with self.catalog.write() as connection:
            return assignment_module.undo_operation(
                connection,
                operation_id=str(operation_id),
                request_id=request_id,
                expected_operation_revision=int(expected_operation_revision),
                now_ms=now_ms,
                refresh=self.refresh,
            )

    # ---------------------------------------------------------------- internals
    def _require_asset(self, asset_id: int) -> None:
        row = self.catalog.read_one("SELECT 1 FROM assets WHERE asset_id = ?", (int(asset_id),))
        if row is None:
            raise fail(
                "PLACE_NOT_FOUND",
                f"asset {int(asset_id)} does not exist",
                asset_ids=[int(asset_id)],
            )


def _operation_record(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "operation_id": str(row["operation_id"]),
        "request_id": str(row["request_id"]),
        "payload_sha256": str(row["payload_sha256"]),
        "operation_type": str(row["operation_type"]),
        "operation_revision": int(row["operation_revision"]),
        "actor": str(row["actor"]),
        "atomic": bool(int(row["atomic"])),
        "status": str(row["status"]),
        "undo_of_operation_id": (
            None if row["undo_of_operation_id"] is None else str(row["undo_of_operation_id"])
        ),
        "catalog_filter_revision": int(row["catalog_filter_revision"]),
        "created_at": int(row["created_at"]),
        "committed_at": int(row["committed_at"]),
    }
