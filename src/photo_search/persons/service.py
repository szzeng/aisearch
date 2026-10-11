"""The person facade: one object the transport layer calls without owning a transaction.

Reads run straight through the catalog connection (the authority is SQLite, ADR-0006 §8),
and every write runs inside ``Catalog.write()`` with the injected P2-03 projection refresh,
so the authority change and its derived catch-up share one ``BEGIN IMMEDIATE`` transaction.
The facade adds nothing else: the naming, merge, redirect, idempotency and compensation
semantics all live in :mod:`photo_search.persons.operations`, which is the only place that
implements the business use case.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from photo_search.catalog.db import Catalog
from photo_search.persons import contracts, face_correction
from photo_search.persons import create_from_face as create_from_face_module
from photo_search.persons import mapping as mapping_module
from photo_search.persons import operations as operation_module
from photo_search.persons import read as read_module
from photo_search.persons import retention as retention_module
from photo_search.persons import split as split_module
from photo_search.persons.contracts import person_view
from photo_search.persons.identity import (
    resolve_canonical_person_id,
    resolve_redirect,
)
from photo_search.persons.members import MEMBER_SQL as _MEMBER_SQL
from photo_search.places import revision as revision_module


class PersonService:
    """Thin facade over the catalog plus the person modules."""

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
    def open(cls, catalog: Catalog, **options: Any) -> PersonService:
        return cls(catalog, **options)

    # ------------------------------------------------------------------- reads
    def get_person(self, person_id: int) -> dict[str, Any]:
        """The canonical view of one person, following a merged id exactly once."""
        connection = self.catalog.connection
        redirect = resolve_redirect(connection, int(person_id))
        canonical = int(redirect["canonical_person_id"])
        row = connection.execute(
            "SELECT person_id, name, avatar_face_id, merged_into, revision, created_at, "
            "updated_at FROM persons WHERE person_id = ?",
            (canonical,),
        ).fetchone()
        aliases = connection.execute(
            "SELECT alias, normalized_alias FROM person_aliases WHERE person_id = ? "
            "ORDER BY normalized_alias, alias",
            (canonical,),
        ).fetchall()
        faces = int(
            connection.execute(_MEMBER_SQL.format(aggregate="*"), (canonical,)).fetchone()["total"]
        )
        assets = int(
            connection.execute(
                _MEMBER_SQL.format(aggregate="DISTINCT f.asset_id"), (canonical,)
            ).fetchone()["total"]
        )
        return person_view(
            row,
            requested_person_id=int(redirect["requested_person_id"]),
            canonical_person_id=canonical,
            aliases=aliases,
            face_count=faces,
            asset_count=assets,
        )

    def canonical_person_id(self, person_id: int) -> int:
        return resolve_canonical_person_id(self.catalog.connection, int(person_id))

    # ------------------------------------------------------------------ writes
    def rename(
        self,
        *,
        person_id: int,
        name: Any,
        request_id: str,
        expected_revision: int,
        merge_by_name: bool = True,
    ) -> dict[str, Any]:
        now_ms = int(self.clock())
        with self.catalog.write() as connection:
            return operation_module.rename_person(
                connection,
                person_id=person_id,
                name=name,
                request_id=request_id,
                expected_revision=expected_revision,
                now_ms=now_ms,
                refresh=self.refresh,
                merge_by_name=merge_by_name,
            )

    def merge(
        self,
        *,
        target_person_id: int,
        target_expected_revision: int,
        sources: Sequence[Mapping[str, Any]],
        request_id: str,
    ) -> dict[str, Any]:
        now_ms = int(self.clock())
        with self.catalog.write() as connection:
            return operation_module.merge_persons(
                connection,
                target_person_id=target_person_id,
                target_expected_revision=target_expected_revision,
                sources=sources,
                request_id=request_id,
                now_ms=now_ms,
                refresh=self.refresh,
            )

    def set_visibility(self, *, person_ids: Sequence[int], hidden: bool) -> dict[str, Any]:
        """Set album visibility for active canonical people as one atomic write."""
        with self.catalog.write() as connection:
            canonical_ids = sorted(
                {resolve_canonical_person_id(connection, int(person_id)) for person_id in person_ids}
            )
            changed = 0
            for person_id in canonical_ids:
                if hidden:
                    result = connection.execute(
                        "INSERT OR IGNORE INTO person_hidden(person_id) VALUES (?)", (person_id,)
                    )
                else:
                    result = connection.execute(
                        "DELETE FROM person_hidden WHERE person_id = ?", (person_id,)
                    )
                changed += max(0, int(result.rowcount))
            return {"hidden": bool(hidden), "person_count": len(canonical_ids), "changed": changed}

    def undo(
        self,
        *,
        operation_id: str,
        request_id: str,
        expected_operation_revision: int,
    ) -> dict[str, Any]:
        now_ms = int(self.clock())
        with self.catalog.write() as connection:
            return operation_module.undo_person_operation(
                connection,
                operation_id=operation_id,
                request_id=request_id,
                expected_operation_revision=expected_operation_revision,
                now_ms=now_ms,
                refresh=self.refresh,
            )

    def retention_preview(self) -> dict[str, Any]:
        return retention_module.preview(self.catalog.connection, now_ms=int(self.clock()))

    def retention_cleanup(self, *, after_operation_revision: int | None = None) -> dict[str, Any]:
        now_ms = int(self.clock())
        try:
            with self.catalog.write() as connection:
                batch = retention_module.cleanup_batch(
                    connection,
                    now_ms=now_ms,
                    after_operation_revision=after_operation_revision,
                )
            return {
                **batch,
                "failed_operations": 0,
                "max_batch_size": retention_module.MAX_BATCH_SIZE,
            }
        except Exception:
            # Catalog.write rolled the active batch back; earlier independent calls are durable.
            return {
                "cleaned_operations": 0,
                "skipped_operations": 0,
                "deleted_items": 0,
                "failed_operations": 1,
                "failure_code": "RETENTION_BATCH_FAILED",
                "max_batch_size": retention_module.MAX_BATCH_SIZE,
                "has_more": True,
                "next_after_operation_revision": after_operation_revision,
            }

    # ------------------------------------------------------------------ split
    def split(
        self,
        *,
        source_person_id: int,
        contract_version: str,
        source_expected_revision: int,
        faces: Sequence[Mapping[str, Any]],
        request_id: str,
    ) -> dict[str, Any]:
        """Split 1..500 explicit faces of one person into a new unnamed person (ADR-0008)."""
        now_ms = int(self.clock())
        with self.catalog.write() as connection:
            return split_module.split_person(
                connection,
                source_person_id=source_person_id,
                contract_version=contract_version,
                source_expected_revision=source_expected_revision,
                faces=list(faces),
                request_id=request_id,
                now_ms=now_ms,
                refresh=self.refresh,
            )

    # ----------------------------------------------------------------- face control
    def face_assignment(self, face_id: str) -> dict[str, Any]:
        """ADR-0007 §6: the read projection of one face's human control."""
        return face_correction.control_state(self.catalog.connection, str(face_id))

    def set_face_assignment(
        self,
        *,
        face_id: str,
        contract_version: str,
        action: str,
        target_person_id: int | None,
        expected_revision: int,
        request_id: str,
    ) -> dict[str, Any]:
        """One explicit ``face_id`` correction, in its own authority transaction."""
        now_ms = int(self.clock())
        with self.catalog.write() as connection:
            return face_correction.apply_single(
                connection,
                face_id=face_id,
                contract_version=contract_version,
                action=action,
                target_person_id=target_person_id,
                expected_revision=expected_revision,
                request_id=request_id,
                now_ms=now_ms,
                refresh=self.refresh,
            )

    def edit_face_assignments(
        self,
        *,
        items: Sequence[Mapping[str, Any]],
        contract_version: str,
        request_id: str,
    ) -> dict[str, Any]:
        """1..500 explicit face items, all-or-nothing, in one authority transaction."""
        now_ms = int(self.clock())
        with self.catalog.write() as connection:
            return face_correction.apply_batch(
                connection,
                items=list(items),
                contract_version=contract_version,
                request_id=request_id,
                now_ms=now_ms,
                refresh=self.refresh,
            )

    def create_person_from_face(
        self,
        *,
        face_id: str,
        contract_version: str,
        expected_control_revision: int,
        request_id: str,
    ) -> dict[str, Any]:
        now_ms = int(self.clock())
        with self.catalog.write() as connection:
            return create_from_face_module.create_person(
                connection,
                face_id=face_id,
                contract_version=contract_version,
                expected_control_revision=expected_control_revision,
                request_id=request_id,
                now_ms=now_ms,
            )

    def operation(self, operation_id: str) -> dict[str, Any] | None:
        """A read-only echo of one recorded operation (0 personal data)."""
        row = operation_module.operation_row(self.catalog.connection, str(operation_id))
        if row is None:
            return None
        return operation_module.operation_result(self.catalog.connection, row)

    # ------------------------------------------------------- face-redetection mapping
    def mapping_page(
        self, *, limit: Any, cursor: str | None = None, state: Any = "open"
    ) -> dict[str, Any]:
        """ADR-0010 §8.1: the read-only review list of the pending old/new mapping.

        ``limit``/``state`` are forwarded as received: the read layer owns the frozen
        ``VALIDATION_ERROR`` closure of §8.2, so an unparsable bound must not be coerced (or
        rejected) here with a different body.
        """
        return read_module.mapping_page(self.catalog, limit=limit, cursor=cursor, state=state)

    def mapping_group(self, old_face_id: str) -> dict[str, Any]:
        """ADR-0010 §8.1: one review group with its live candidate array."""
        return read_module.mapping_group(self.catalog, str(old_face_id))

    def asset_mapping_page(
        self, asset_id: int, *, limit: Any = None, cursor: str | None = None
    ) -> dict[str, Any]:
        """Read the bounded OPEN mapping reviews attached to one photo."""
        return read_module.asset_mapping_page(
            self.catalog, int(asset_id), limit=limit, cursor=cursor
        )

    def decide_face_mappings(
        self,
        *,
        payload: Any,
        contract_version: Any,
        request_id: Any,
        asset_id: Any,
        decisions: Any,
    ) -> dict[str, Any]:
        """One bounded, all-or-nothing decision request, in its own authority transaction."""
        now_ms = int(self.clock())
        with self.catalog.write() as connection:
            return mapping_module.decide_face_mappings(
                connection,
                payload=payload,
                contract_version=contract_version,
                request_id=request_id,
                asset_id=asset_id,
                decisions=decisions,
                now_ms=now_ms,
                refresh=self.refresh,
            )


__all__ = ["PersonService", "contracts", "face_correction", "split_module"]
