"""Canonical person identity: the one redirect resolver of ADR-0006 §4.

``persons.merged_into`` keeps an old ``person_id`` usable forever (architecture §8.3.3),
so every read, rename, merge, exclusion decision and representative query has to start from
the *canonical* person of an id. This module is the only place that walks that chain:

* a missing id is ``PERSON_NOT_FOUND``;
* a repeated id, or a chain longer than the current number of person rows, is
  ``PERSON_REDIRECT_CYCLE`` - the resolver never repairs the table and never guesses a
  target, because a cycle is a corrupt fact and silently picking one end would make the
  authority unanswerable.

The walk is bounded twice over: by the number of ``persons`` rows and by a visited set, so
even a corrupted catalog cannot make it loop.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from photo_search.persons.contracts import fail


def person_row(connection: sqlite3.Connection, person_id: int) -> Any:
    """The raw row of one person id, or ``None`` when the id does not exist."""
    return connection.execute(
        "SELECT person_id, name, name_key, avatar_face_id, merged_into, revision, "
        "created_at, updated_at FROM persons WHERE person_id = ?",
        (int(person_id),),
    ).fetchone()


def require_person_row(connection: sqlite3.Connection, person_id: int) -> Any:
    row = person_row(connection, person_id)
    if row is None:
        raise fail(
            "PERSON_NOT_FOUND",
            f"person {int(person_id)} does not exist",
            person_id=int(person_id),
        )
    return row


def person_row_count(connection: sqlite3.Connection) -> int:
    return int(connection.execute("SELECT COUNT(*) FROM persons").fetchone()[0])


def resolve_canonical_person_id(connection: sqlite3.Connection, person_id: int) -> int:
    """Follow ``merged_into`` until the active person; never repair, never guess."""
    requested = int(person_id)
    row = require_person_row(connection, requested)
    limit = person_row_count(connection)
    seen: set[int] = {requested}
    current = requested
    steps = 0
    while row is not None and row["merged_into"] is not None:
        steps += 1
        target = int(row["merged_into"])
        if target in seen or steps > limit:
            raise fail(
                "PERSON_REDIRECT_CYCLE",
                "the person redirect chain does not end at an active person",
                person_id=requested,
                steps=steps,
            )
        seen.add(target)
        row = connection.execute(
            "SELECT person_id, merged_into FROM persons WHERE person_id = ?", (target,)
        ).fetchone()
        if row is None:
            # A dangling target is not a cycle but it is just as unusable.
            raise fail(
                "PERSON_REDIRECT_CYCLE",
                "the person redirect chain points at a missing person",
                person_id=requested,
                missing_person_id=target,
            )
        current = target
    return int(current)


def resolve_redirect(connection: sqlite3.Connection, person_id: int) -> dict[str, Any]:
    """The ``requested``/``canonical``/``redirected`` triple of a read request."""
    requested = int(person_id)
    canonical = resolve_canonical_person_id(connection, requested)
    return {
        "requested_person_id": requested,
        "canonical_person_id": canonical,
        "redirected": canonical != requested,
    }


def active_person_id_for_name_key(
    connection: sqlite3.Connection, name_key: str, *, exclude_person_id: int | None = None
) -> int | None:
    """Find an active same-name person using Unicode case-insensitive comparison.

    Same-name resolution happens only here, i.e. only while a user is submitting a name
    (ADR-0019). Nothing in the background path calls it, and an alias is never a key.
    Stored nfc1 keys keep their original spelling for old snapshots and undo. Excluding
    the person being renamed lets an explicit save discover legacy case-only duplicates.
    Exact spelling takes priority, then the lowest id makes legacy ties deterministic.
    """
    folded = str(name_key).casefold()
    rows = connection.execute(
        "SELECT person_id, name_key FROM persons "
        "WHERE name_key IS NOT NULL AND merged_into IS NULL "
        "AND (? IS NULL OR person_id != ?) "
        "ORDER BY (name_key = ?) DESC, person_id ASC",
        (exclude_person_id, exclude_person_id, str(name_key)),
    )
    # SQLite NOCASE only handles ASCII, so compare Unicode keys in Python.
    for row in rows:
        if str(row["name_key"]).casefold() == folded:
            return int(row["person_id"])
    return None
