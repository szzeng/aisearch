"""Shared ``%TEMP%`` harness for the P3-04B single-face correction suites.

It extends :class:`tests.p3_person_fixtures.PersonHarness` instead of copying it: the
authority rows are seeded directly (assets, faces, persons, links, controls, denials), so a
case can exercise the ADR-0007 matrix without spending a detector run, and the real-detector
end-to-end path stays where P3-02/P3-03 put it. Nothing here ever touches a real photo
library, and no case needs a model, a network call or a GPU.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from p3_person_fixtures import PersonHarness


class FaceCorrectionHarness(PersonHarness):
    """The P3-03 harness plus the face-correction readers and writers of ADR-0007."""

    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        #: The last face-level request_id, so a case can replay it verbatim.
        self.request_serial = 0

    # ----------------------------------------------------------------- operation helpers
    def next_request_id(self, prefix: str = "face") -> str:
        self.request_serial += 1
        return f"{prefix}-{self.request_serial}"

    def assignment(self, face_id: str) -> dict[str, Any]:
        return self.service.face_assignment(str(face_id))

    def edit(
        self,
        face_id: str,
        action: str,
        *,
        expected_revision: int,
        request_id: str | None = None,
        target_person_id: int | None = None,
        contract_version: str = "p3-face-correction-contracts.v1",
    ) -> dict[str, Any]:
        return self.service.set_face_assignment(
            face_id=str(face_id),
            contract_version=contract_version,
            action=str(action),
            target_person_id=target_person_id,
            expected_revision=int(expected_revision),
            request_id=request_id or self.next_request_id(),
        )

    def batch(
        self,
        items: list[dict[str, Any]],
        *,
        request_id: str | None = None,
        contract_version: str = "p3-face-correction-contracts.v1",
    ) -> dict[str, Any]:
        return self.service.edit_face_assignments(
            items=items,
            contract_version=contract_version,
            request_id=request_id or self.next_request_id("batch"),
        )

    # ------------------------------------------------------------------------ readers
    @staticmethod
    def effective_spec_revision() -> str:
        from photo_search.face.contract import FACE_SPEC_REVISION

        return str(FACE_SPEC_REVISION)

    def reconcile(self, **options: Any) -> dict[str, Any]:
        """Run the real conservative association pass over the queued assets."""
        from photo_search.face.index import reconcile

        return reconcile(self.catalog, **options)

    def reconcile_asset(self, asset_id: int, **options: Any) -> list[dict[str, Any]]:
        """Run one asset's association pass, optionally with a stale control observation."""
        from photo_search.face.index import reconcile_asset

        return reconcile_asset(self.catalog, asset_id=int(asset_id), **options)

    def control(self, face_id: str) -> Any:
        return self.catalog.read_one(
            "SELECT * FROM face_assignment_controls WHERE face_id = ?", (str(face_id),)
        )

    def control_revision(self, face_id: str) -> int:
        row = self.control(face_id)
        return 0 if row is None else int(row["revision"])

    def effective_link(self, face_id: str) -> Any:
        return self.catalog.read_one(
            "SELECT * FROM face_person_links WHERE face_id = ? AND decision = 'CONFIRMED'",
            (str(face_id),),
        )

    def face_links(self, face_id: str) -> list[Any]:
        return self.catalog.read_all(
            "SELECT * FROM face_person_links WHERE face_id = ? "
            "ORDER BY decision, person_id, assignment_kind",
            (str(face_id),),
        )

    def exclusion_rows(self, face_id: str) -> list[Any]:
        return self.catalog.read_all(
            "SELECT face_id, person_id, active, operation_id FROM face_exclusions "
            "WHERE face_id = ? ORDER BY person_id",
            (str(face_id),),
        )

    def active_exclusions(self, face_id: str) -> list[int]:
        return [
            int(row["person_id"])
            for row in self.exclusion_rows(str(face_id))
            if int(row["active"])
        ]

    def face_items(self, operation_id: str) -> list[Any]:
        return self.catalog.read_all(
            "SELECT * FROM person_operation_items WHERE operation_id = ? ORDER BY item_index",
            (str(operation_id),),
        )

    def operation(self, operation_id: str) -> Any:
        return self.catalog.read_one(
            "SELECT * FROM person_operations WHERE operation_id = ?", (str(operation_id),)
        )

    def library_snapshot(self) -> dict[str, Any]:
        """Every authority/derived fact a face correction must not disturb by accident."""
        return {
            "faces": self.catalog.read_all("SELECT * FROM faces ORDER BY face_id"),
            "embeddings": self.catalog.read_all(
                "SELECT face_id, processor_id, space_id FROM face_embeddings ORDER BY face_id"
            ),
            "links": self.catalog.read_all(
                "SELECT * FROM face_person_links ORDER BY face_id, person_id, assignment_kind"
            ),
            "exclusions": self.catalog.read_all(
                "SELECT * FROM face_exclusions ORDER BY face_id, person_id"
            ),
            "queue": self.catalog.read_all(
                "SELECT asset_id, cause FROM search_projection_queue ORDER BY asset_id"
            ),
            "index_outbox": self.catalog.read_all(
                "SELECT event_id, state FROM index_outbox ORDER BY event_id"
            ),
        }

    def seeded_pair(self) -> dict[str, Any]:
        """One photo with two faces, one person, an automatic link and a live candidate entry.

        This is the smallest faithful subset of "a dad photo": the requested face is linked
        automatically, and a second face on the same picture must survive every correction
        of the first one untouched.
        """
        asset_id = self.add_asset("合照.jpg")
        linked_face = self.add_face(asset_id, ordinal=0)
        neighbour = self.add_face(asset_id, ordinal=1)
        self.add_embedding(linked_face)
        self.add_embedding(neighbour)
        dad = self.add_person(name="爸爸")
        other = self.add_person(name="叔叔")
        mom = self.add_person(name="妈妈")
        self.add_link(linked_face, dad, decision="CONFIRMED", assignment_kind="AUTO", confidence=0.9)
        self.add_link(neighbour, mom, decision="CONFIRMED", assignment_kind="AUTO", confidence=0.9)
        self.seed_candidate_index([linked_face, neighbour])
        return {
            "asset_id": asset_id,
            "face": linked_face,
            "neighbour": neighbour,
            "dad": dad,
            "other": other,
            "mom": mom,
        }
