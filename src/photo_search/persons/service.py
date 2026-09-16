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
from photo_search.persons import operations as operation_module
from photo_search.persons.contracts import person_view
from photo_search.persons.identity import (
    resolve_canonical_person_id,
    resolve_redirect,
)
from photo_search.places import revision as revision_module

#: A member face is one *effective* link that neither the human lock nor an active human
#: denial forbids. A photo count is that same set counted by distinct ``asset_id``.
_MEMBER_SQL = (
    "SELECT COUNT({aggregate}) AS total FROM face_person_links AS l "
    "JOIN faces AS f ON f.face_id = l.face_id "
    "WHERE l.person_id = ? AND l.decision = 'CONFIRMED' "
    "AND NOT EXISTS (SELECT 1 FROM face_assignment_controls AS c "
    " WHERE c.face_id = l.face_id AND c.mode = 'MANUAL_UNASSIGNED') "
    "AND NOT EXISTS (SELECT 1 FROM face_exclusions AS e "
    " WHERE e.face_id = l.face_id AND e.person_id = l.person_id AND e.active = 1)"
)


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
            connection.execute(
                _MEMBER_SQL.format(aggregate="*"), (canonical,)
            ).fetchone()["total"]
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

    def operation(self, operation_id: str) -> dict[str, Any] | None:
        """A read-only echo of one recorded operation (0 personal data)."""
        row = operation_module.operation_row(self.catalog.connection, str(operation_id))
        if row is None:
            return None
        return operation_module.operation_result(self.catalog.connection, row)


__all__ = ["PersonService", "contracts", "face_correction"]
