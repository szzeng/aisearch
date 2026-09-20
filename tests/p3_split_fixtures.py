"""Shared ``%TEMP%`` harness for the P3-05B person-split suites.

It extends :class:`tests.p3_face_correction_fixtures.FaceCorrectionHarness` instead of
copying it: the authority rows (assets, faces, persons, links, controls, denials, candidate
entries) are seeded directly, so a case can exercise the ADR-0008 matrix without spending a
detector run, and the real-detector end-to-end path stays where P3-02/P3-03 put it. Nothing
here ever touches a real photo library, and no case needs a model, a network call or a GPU.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from p3_face_correction_fixtures import FaceCorrectionHarness

CONTRACT_VERSION = "p3-person-split-contracts.v1"


class SplitHarness(FaceCorrectionHarness):
    """The face-correction harness plus the split readers and writers of ADR-0008."""

    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)

    # ----------------------------------------------------------------- operation helpers
    def selection(
        self, face_id: str, *, control: int = 0, assignment: int = 3
    ) -> dict[str, Any]:
        return {
            "face_id": str(face_id),
            "expected_control_revision": int(control),
            "expected_assignment_revision": int(assignment),
        }

    def split(
        self,
        source_person_id: int,
        faces: list[dict[str, Any]],
        *,
        request_id: str | None = None,
        contract_version: str = CONTRACT_VERSION,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        revision = (
            self.revision_of(int(source_person_id))
            if expected_revision is None
            else int(expected_revision)
        )
        return self.service.split(
            source_person_id=int(source_person_id),
            contract_version=contract_version,
            source_expected_revision=int(revision),
            faces=faces,
            request_id=request_id or self.next_request_id("split"),
        )

    # ------------------------------------------------------------------------ seeding
    def seed_group(
        self,
        *,
        face_count: int = 3,
        name: str = "爸爸",
        assignment_kind: str = "AUTO",
        link_revision: int = 3,
        asset_name: str = "合照 一.jpg",
    ) -> dict[str, Any]:
        """One photo whose faces are all effective members of one person.

        The faces are seeded with ascending ``face_pixels`` so the representative query has a
        deterministic winner (the last face) and the avatar cases can be stated exactly.
        """
        asset_id = self.add_asset(asset_name)
        faces = [
            self.add_face(asset_id, ordinal=index, face_pixels=100.0 + index)
            for index in range(int(face_count))
        ]
        for face_id in faces:
            self.add_embedding(face_id)
        person = self.add_person(name=name)
        for face_id in faces:
            self.add_link(
                face_id,
                person,
                decision="CONFIRMED",
                assignment_kind=str(assignment_kind),
                confidence=0.9,
                revision=int(link_revision),
            )
        # The automatic path picks a representative when a group gains its first member; the
        # harness has to do the same, otherwise the avatar cases would start from "no avatar"
        # for a reason that has nothing to do with the split.
        with self.catalog.write() as connection:
            connection.execute(
                "UPDATE persons SET avatar_face_id = ? WHERE person_id = ?",
                (str(faces[-1]), int(person)),
            )
        self.seed_candidate_index(list(faces))
        return {"asset_id": int(asset_id), "faces": faces, "person": int(person)}

    def add_suggestion(self, face_id: str, person_id: int, *, revision: int = 1) -> None:
        self.add_link(
            face_id,
            int(person_id),
            decision="SUGGESTED",
            assignment_kind="AUTO",
            confidence=0.4,
            reason="BORDERLINE",
            revision=int(revision),
        )

    # ------------------------------------------------------------------------- readers
    def split_items(self, operation_id: str) -> list[Any]:
        return self.catalog.read_all(
            "SELECT * FROM person_operation_items WHERE operation_id = ? ORDER BY item_index",
            (str(operation_id),),
        )

    def group_item(self, answer: dict[str, Any], role: str) -> dict[str, Any]:
        for item in answer["items"]:
            if item.get("entity_type") == "SPLIT_GROUP" and item.get("role") == str(role):
                return item
        raise AssertionError(f"no {role} group item in {answer}")

    def face_item(self, answer: dict[str, Any], face_id: str) -> dict[str, Any]:
        for item in answer["items"]:
            if item.get("entity_type") == "FACE_CONTROL" and item.get("face_id") == str(face_id):
                return item
        raise AssertionError(f"no face item for {face_id} in {answer}")

    def members(self, person_id: int) -> set[str]:
        return {
            str(row["face_id"])
            for row in self.catalog.read_all(
                "SELECT l.face_id AS face_id FROM face_person_links AS l "
                "WHERE l.person_id = ? AND l.decision = 'CONFIRMED'",
                (int(person_id),),
            )
        }

    def avatar(self, person_id: int) -> str | None:
        row = self.person(int(person_id))
        return None if row is None or row["avatar_face_id"] is None else str(
            row["avatar_face_id"]
        )

    def aliases(self, person_id: int) -> list[str]:
        return [
            str(row["alias"])
            for row in self.catalog.read_all(
                "SELECT alias FROM person_aliases WHERE person_id = ? ORDER BY alias",
                (int(person_id),),
            )
        ]

    def canonical(self, person_id: int) -> int:
        return int(self.service.canonical_person_id(int(person_id)))

    def filter_cause(self) -> str:
        return str(
            self.catalog.read_one(
                "SELECT cause FROM catalog_filter_revision WHERE revision_id = 1"
            )["cause"]
        )

    def face_outbox(self) -> list[Any]:
        return self.catalog.read_all(
            "SELECT face_id, state, projection_revision FROM face_index_outbox "
            "ORDER BY face_id, projection_revision"
        )

    def embeddings(self) -> list[Any]:
        return self.catalog.read_all(
            "SELECT face_id, processor_id, space_id FROM face_embeddings ORDER BY face_id"
        )

    def generations(self) -> list[Any]:
        return self.catalog.read_all(
            "SELECT generation_id, state, is_active FROM face_index_generations "
            "ORDER BY generation_id"
        )

    def purge_face(self, face_id: str) -> None:
        """Delete one face and every row that references it (a "detection disappeared" case).

        This is a *test* injection, not a product path: the harness has to remove the
        dependent rows by hand because the schema deliberately uses ``ON DELETE RESTRICT``
        everywhere a real deletion would have to make a decision first.
        """
        with self.catalog.write() as connection:
            connection.execute(
                "UPDATE persons SET avatar_face_id = NULL WHERE avatar_face_id = ?",
                (str(face_id),),
            )
            connection.execute("DELETE FROM face_index_entries WHERE face_id = ?", (str(face_id),))
            connection.execute("DELETE FROM face_index_outbox WHERE face_id = ?", (str(face_id),))
            connection.execute("DELETE FROM face_association_queue WHERE asset_id IN "
                               "(SELECT asset_id FROM faces WHERE face_id = ?)", (str(face_id),))
            connection.execute("DELETE FROM face_person_links WHERE face_id = ?", (str(face_id),))
            connection.execute(
                "DELETE FROM face_assignment_controls WHERE face_id = ?", (str(face_id),)
            )
            connection.execute("DELETE FROM face_exclusions WHERE face_id = ?", (str(face_id),))
            connection.execute("DELETE FROM face_embeddings WHERE face_id = ?", (str(face_id),))
            connection.execute("DELETE FROM faces WHERE face_id = ?", (str(face_id),))

    def reopen(self) -> None:
        """Close and reopen the catalog, modelling a process restart over the same data root."""
        from photo_search.catalog.db import Catalog
        from photo_search.persons.service import PersonService

        self.catalog.close()
        self.catalog = Catalog.open(
            data_root=self.data_root, clock=self.clock, lease_seconds=60
        )
        self.service = PersonService(self.catalog, clock=self.clock)
