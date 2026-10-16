"""The one definition of "an effective member face of a person" (ADR-0006 §5).

The person view (``GET /api/v1/persons/{id}``) and the P3-06 person page both have to count
the same set, and the architecture fixes that set as: a face whose *effective* link to the
person is ``CONFIRMED`` and which is neither under the human ``MANUAL_UNASSIGNED`` lock nor
denied by an active human exclusion. Keeping one SQL fragment for it means a page can never
count a face the detail endpoint has already excluded (or the other way round).

Nothing here writes, and no field of the exclusion/lock tables is projected: this module is
only the membership predicate.
"""

from __future__ import annotations

import sqlite3

from photo_search.face.detail_policy import superseded_machine_sql

#: The membership predicate over an alias ``l`` of ``face_person_links``. It is a plain SQL
#: fragment so it can be reused as a ``WHERE``/``JOIN`` condition without a second copy.
MEMBER_PREDICATE = (
    "l.decision = 'CONFIRMED' "
    "AND NOT EXISTS (SELECT 1 FROM face_assignment_controls AS c "
    " WHERE c.face_id = l.face_id AND c.mode = 'MANUAL_UNASSIGNED') "
    "AND NOT EXISTS (SELECT 1 FROM face_exclusions AS e "
    " WHERE e.face_id = l.face_id AND e.person_id = l.person_id AND e.active = 1)"
    " AND NOT EXISTS (SELECT 1 FROM faces AS member_face WHERE member_face.face_id=l.face_id AND "
    + superseded_machine_sql("member_face") + ")"
)

#: ``COUNT({aggregate})`` over the member set; ``*`` counts faces, ``DISTINCT f.asset_id``
#: counts photos.
MEMBER_SQL = (
    "SELECT COUNT({aggregate}) AS total FROM face_person_links AS l "
    "JOIN faces AS f ON f.face_id = l.face_id "
    "WHERE l.person_id = ? AND " + MEMBER_PREDICATE
)


def member_counts(connection: sqlite3.Connection, person_id: int) -> tuple[int, int]:
    """``(faces, assets)`` of the effective member set of one person."""
    row = connection.execute(
        "SELECT COUNT(*) AS faces, COUNT(DISTINCT f.asset_id) AS assets "
        "FROM face_person_links AS l JOIN faces AS f ON f.face_id = l.face_id "
        "WHERE l.person_id = ? AND " + MEMBER_PREDICATE,
        (int(person_id),),
    ).fetchone()
    return int(row["faces"]), int(row["assets"])


__all__ = ["MEMBER_PREDICATE", "MEMBER_SQL", "member_counts"]
