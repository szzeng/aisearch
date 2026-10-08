"""The local place directory: stable ids, bounded acyclic hierarchy, aliases, retirement.

Every function takes an open catalog connection and runs inside the caller's single-writer
transaction. Cross-row rules the DDL cannot express (the kind/parent matrix, the ancestor
cycle and the retirement references) are checked here, inside that same short transaction,
so the database never holds a hierarchy the ADR forbids.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from photo_search.catalog.errors import (
    PLACE_ASSIGNMENT_CONFLICT,
    PLACE_NOT_FOUND,
    PLACE_PARENT_CYCLE,
    PLACE_REFERENCED,
)
from photo_search.places import revision as revision_module
from photo_search.places.contracts import (
    ALIAS_SOURCES,
    MAX_ALIASES_PER_PLACE,
    PLACE_KINDS,
    PLACE_LIFECYCLE,
    PLACE_ORIGINS,
    check_choice,
    display_name,
    fail,
    normalize_name,
    place_view,
)

PLACE_FIELDS = (
    "place_id",
    "kind",
    "canonical_name",
    "normalized_name",
    "parent_place_id",
    "origin",
    "lifecycle",
    "revision",
    "created_at",
    "updated_at",
)

ALIAS_FIELDS = (
    "alias_id",
    "place_id",
    "text",
    "normalized",
    "language",
    "source",
    "lifecycle",
    "revision",
    "created_at",
    "updated_at",
)

#: ADR-0005 §2: which parent kinds each place kind may hang under. ``country`` never has
#: a parent; a ``custom`` place may hang under any active kind.
PARENT_KINDS: dict[str, tuple[str, ...]] = {
    "country": (),
    "city": ("country",),
    "attraction": ("country", "city"),
    "custom": ("country", "city", "attraction", "custom"),
}

#: Hard bound on one ancestor walk, so a corrupted hierarchy cannot loop forever.
MAX_HIERARCHY_DEPTH = 64


def get_place_row(connection: sqlite3.Connection, place_id: int) -> sqlite3.Row:
    row = connection.execute(
        f"SELECT {', '.join(PLACE_FIELDS)} FROM places WHERE place_id = ?", (int(place_id),)
    ).fetchone()
    if row is None:
        raise fail(PLACE_NOT_FOUND, f"place {int(place_id)} does not exist", place_id=int(place_id))
    return row


def _find_place(connection: sqlite3.Connection, place_id: int) -> sqlite3.Row | None:
    return connection.execute(
        f"SELECT {', '.join(PLACE_FIELDS)} FROM places WHERE place_id = ?", (int(place_id),)
    ).fetchone()


def active_alias_rows(connection: sqlite3.Connection, place_id: int) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            f"SELECT {', '.join(ALIAS_FIELDS)} FROM place_aliases "
            "WHERE place_id = ? AND lifecycle = 'ACTIVE' ORDER BY alias_id",
            (int(place_id),),
        ).fetchall()
    )


def external_ref_rows(connection: sqlite3.Connection, place_id: int) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            "SELECT provider, external_id FROM place_external_refs WHERE place_id = ? "
            "ORDER BY provider, external_id",
            (int(place_id),),
        ).fetchall()
    )


def get_place_view(connection: sqlite3.Connection, place_id: int) -> dict[str, Any]:
    return place_view(
        get_place_row(connection, place_id),
        aliases=active_alias_rows(connection, place_id),
        external_refs=external_ref_rows(connection, place_id),
    )


def ancestor_rows(connection: sqlite3.Connection, place_id: int) -> list[sqlite3.Row]:
    """The full ancestor chain of ``place_id``, closest parent first."""
    chain: list[sqlite3.Row] = []
    current = get_place_row(connection, place_id)
    seen = {int(current["place_id"])}
    parent = current["parent_place_id"]
    steps = 0
    while parent is not None:
        steps += 1
        if steps > MAX_HIERARCHY_DEPTH:
            raise fail(
                PLACE_PARENT_CYCLE,
                "the place hierarchy is deeper than this build allows",
                place_id=int(place_id),
            )
        row = get_place_row(connection, int(parent))
        if int(row["place_id"]) in seen:
            raise fail(
                PLACE_PARENT_CYCLE,
                "the place hierarchy contains a cycle",
                place_id=int(place_id),
            )
        seen.add(int(row["place_id"]))
        chain.append(row)
        parent = row["parent_place_id"]
    return chain


def ancestor_views(connection: sqlite3.Connection, place_id: int) -> list[dict[str, Any]]:
    """Root-first ancestor summaries, so a selector can disambiguate same-name places."""
    rows = list(reversed(ancestor_rows(connection, place_id)))
    return [
        {
            "place_id": int(row["place_id"]),
            "kind": str(row["kind"]),
            "canonical_name": str(row["canonical_name"]),
        }
        for row in rows
    ]


def descendant_ids(connection: sqlite3.Connection, place_ids: Iterable[int]) -> list[int]:
    """Every descendant (inclusive) of the given places, using one recursive CTE."""
    roots = sorted({int(value) for value in place_ids})
    if not roots:
        return []
    placeholders = ", ".join("?" for _ in roots)
    rows = connection.execute(
        "WITH RECURSIVE tree(place_id) AS ("
        f" SELECT place_id FROM places WHERE place_id IN ({placeholders})"
        " UNION"
        " SELECT p.place_id FROM places p JOIN tree t ON p.parent_place_id = t.place_id"
        ") SELECT place_id FROM tree ORDER BY place_id",
        tuple(roots),
    ).fetchall()
    return [int(row[0]) for row in rows]


def check_parent(
    connection: sqlite3.Connection,
    *,
    kind: str,
    parent_place_id: int | None,
) -> sqlite3.Row | None:
    """Validate the kind/parent matrix and the ACTIVE-parent rule."""
    if parent_place_id is None:
        return None
    parent = _find_place(connection, int(parent_place_id))
    if parent is None:
        raise fail(
            PLACE_NOT_FOUND,
            f"parent place {int(parent_place_id)} does not exist",
            place_id=int(parent_place_id),
        )
    if str(parent["lifecycle"]) != "ACTIVE":
        raise fail(
            PLACE_REFERENCED,
            "the parent place is retired and cannot receive new children",
            place_id=int(parent_place_id),
        )
    allowed = PARENT_KINDS.get(str(kind), ())
    if str(parent["kind"]) not in allowed:
        # ADR-0005 §9 folds an illegal parent *type* into PLACE_PARENT_CYCLE; the detail
        # set stays inside the frozen error-detail shape (place_id only).
        raise fail(
            PLACE_PARENT_CYCLE,
            f"a {kind} place cannot hang under a {parent['kind']} place",
            place_id=int(parent_place_id),
        )
    return parent


def assert_no_cycle(
    connection: sqlite3.Connection, *, place_id: int, parent_place_id: int | None
) -> None:
    """Refuse a re-parent that would make ``place_id`` its own ancestor."""
    if parent_place_id is None:
        return
    found = connection.execute(
        "WITH RECURSIVE chain(place_id, depth) AS ("
        " SELECT place_id, 1 FROM places WHERE place_id = ?"
        " UNION ALL"
        " SELECT p.parent_place_id, c.depth + 1 FROM places p JOIN chain c ON p.place_id = c.place_id"
        " WHERE p.parent_place_id IS NOT NULL AND c.depth <= ?"
        ") SELECT place_id FROM chain WHERE place_id = ? LIMIT 1",
        (int(parent_place_id), MAX_HIERARCHY_DEPTH, int(place_id)),
    ).fetchone()
    if found is not None:
        raise fail(
            PLACE_PARENT_CYCLE,
            "the requested parent would create an ancestor cycle",
            place_id=int(place_id),
        )


def create_place(
    connection: sqlite3.Connection,
    *,
    kind: str,
    canonical_name: Any,
    parent_place_id: int | None = None,
    origin: str = "USER",
    aliases: Sequence[Mapping[str, Any]] = (),
    external_refs: Sequence[Mapping[str, Any]] = (),
    now_ms: int,
) -> int:
    """Insert one place and its aliases; the caller owns the transaction."""
    check_choice(kind, PLACE_KINDS, field="kind")
    check_choice(origin, PLACE_ORIGINS, field="origin")
    display = display_name(canonical_name, field="canonical_name")
    normalized = normalize_name(canonical_name, field="canonical_name")
    parent = check_parent(connection, kind=str(kind), parent_place_id=parent_place_id)
    cursor = connection.execute(
        "INSERT INTO places(kind, canonical_name, normalized_name, parent_place_id, origin, "
        "lifecycle, revision, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 'ACTIVE', 1, ?, ?)",
        (
            str(kind),
            display,
            normalized,
            None if parent is None else int(parent["place_id"]),
            str(origin),
            int(now_ms),
            int(now_ms),
        ),
    )
    place_id = int(cursor.lastrowid)
    _insert_aliases(
        connection, place_id=place_id, aliases=aliases, origin=str(origin), now_ms=int(now_ms)
    )
    _insert_external_refs(
        connection, place_id=place_id, external_refs=external_refs, now_ms=int(now_ms)
    )
    return place_id


def _normalized_alias_specs(aliases: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if len(aliases) > MAX_ALIASES_PER_PLACE:
        raise fail(
            "VALIDATION_ERROR",
            f"a place may carry at most {MAX_ALIASES_PER_PLACE} aliases",
            aliases=len(aliases),
            max_aliases=MAX_ALIASES_PER_PLACE,
        )
    specs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(aliases):
        if not isinstance(item, Mapping):
            raise fail("VALIDATION_ERROR", "each alias must be an object", index=index)
        text = display_name(item.get("text"), field=f"aliases[{index}].text")
        normalized = normalize_name(item.get("text"), field=f"aliases[{index}].text")
        if normalized in seen:
            raise fail("VALIDATION_ERROR", "the same alias is listed twice", index=index)
        seen.add(normalized)
        language = item.get("language")
        if language is not None and not isinstance(language, str):
            raise fail("VALIDATION_ERROR", "alias language must be a string or null", index=index)
        specs.append(
            {
                "text": text,
                "normalized": normalized,
                "language": None if language is None else (str(language).strip() or None),
            }
        )
    return specs


def _insert_aliases(
    connection: sqlite3.Connection,
    *,
    place_id: int,
    aliases: Sequence[Mapping[str, Any]],
    origin: str,
    now_ms: int,
) -> None:
    check_choice(origin, ALIAS_SOURCES, field="alias source")
    for spec in _normalized_alias_specs(aliases):
        connection.execute(
            "INSERT INTO place_aliases(place_id, text, normalized, language, source, lifecycle, "
            "revision, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'ACTIVE', 1, ?, ?)",
            (
                int(place_id),
                spec["text"],
                spec["normalized"],
                spec["language"],
                str(origin),
                int(now_ms),
                int(now_ms),
            ),
        )


def _insert_external_refs(
    connection: sqlite3.Connection,
    *,
    place_id: int,
    external_refs: Sequence[Mapping[str, Any]],
    now_ms: int,
) -> None:
    for index, item in enumerate(external_refs):
        if not isinstance(item, Mapping):
            raise fail("VALIDATION_ERROR", "each external_ref must be an object", index=index)
        provider = item.get("provider")
        external_id = item.get("external_id")
        if not isinstance(provider, str) or not provider.strip():
            raise fail(
                "VALIDATION_ERROR", "external_ref.provider must be a non-empty string", index=index
            )
        if not isinstance(external_id, str) or not external_id.strip():
            raise fail(
                "VALIDATION_ERROR", "external_ref.external_id must be a non-empty string", index=index
            )
        existing = connection.execute(
            "SELECT place_id FROM place_external_refs WHERE provider = ? AND external_id = ?",
            (provider.strip(), external_id.strip()),
        ).fetchone()
        if existing is not None:
            raise fail(
                PLACE_REFERENCED,
                "this external reference already maps to a local place",
                place_id=int(existing[0]),
            )
        connection.execute(
            "INSERT INTO place_external_refs(provider, external_id, place_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            (provider.strip(), external_id.strip(), int(place_id), int(now_ms)),
        )


def patch_place(
    connection: sqlite3.Connection,
    *,
    place_id: int,
    expected_revision: int,
    canonical_name: Any = None,
    aliases: Sequence[Mapping[str, Any]] | None = None,
    now_ms: int,
    refresh: revision_module.ProjectionRefresh | None = None,
) -> tuple[dict[str, Any], bool]:
    """PATCH v1: rename and edit the ACTIVE alias set. Never re-parents or merges."""
    if connection.execute(
        "SELECT 1 FROM cn_admin_place_refs WHERE place_id=?", (int(place_id),)
    ).fetchone() is not None:
        raise fail(
            PLACE_REFERENCED,
            "a source-backed administrative place cannot be renamed",
            place_id=int(place_id),
        )
    row = get_place_row(connection, place_id)
    current_revision = int(row["revision"])
    if int(expected_revision) != current_revision:
        raise fail(
            PLACE_ASSIGNMENT_CONFLICT,
            "the place revision changed since it was read",
            place_id=int(place_id),
            expected_revision=int(expected_revision),
            current_revision=current_revision,
        )
    changed = False
    renamed = False
    if canonical_name is not None:
        display = display_name(canonical_name, field="canonical_name")
        normalized = normalize_name(canonical_name, field="canonical_name")
        if display != str(row["canonical_name"]) or normalized != str(row["normalized_name"]):
            connection.execute(
                "UPDATE places SET canonical_name = ?, normalized_name = ?, revision = revision + 1, "
                "updated_at = ? WHERE place_id = ?",
                (display, normalized, int(now_ms), int(place_id)),
            )
            changed = True
            renamed = True
    if aliases is not None:
        aliases_changed = _replace_active_aliases(
            connection, place_id=int(place_id), aliases=aliases, now_ms=int(now_ms)
        )
        if aliases_changed:
            if not renamed:
                connection.execute(
                    "UPDATE places SET revision = revision + 1, updated_at = ? WHERE place_id = ?",
                    (int(now_ms), int(place_id)),
                )
            changed = True
    if not changed:
        return get_place_view(connection, place_id), False
    view = get_place_view(connection, place_id)
    revision_module.bump_filter_revision(
        connection,
        cause=revision_module.CAUSE_NAME_CHANGED,
        now_ms=int(now_ms),
        asset_ids=assets_effectively_assigned_to(connection, [int(place_id)]),
        refresh=refresh,
    )
    return view, True


def _replace_active_aliases(
    connection: sqlite3.Connection,
    *,
    place_id: int,
    aliases: Sequence[Mapping[str, Any]],
    now_ms: int,
) -> bool:
    specs = _normalized_alias_specs(aliases)
    wanted = {spec["normalized"]: spec for spec in specs}
    changed = False
    for row in active_alias_rows(connection, place_id):
        normalized = str(row["normalized"])
        spec = wanted.pop(normalized, None)
        if spec is None:
            connection.execute(
                "UPDATE place_aliases SET lifecycle = 'RETIRED', revision = revision + 1, "
                "updated_at = ? WHERE alias_id = ?",
                (int(now_ms), int(row["alias_id"])),
            )
            changed = True
            continue
        if str(row["text"]) != spec["text"] or row["language"] != spec["language"]:
            connection.execute(
                "UPDATE place_aliases SET text = ?, language = ?, revision = revision + 1, "
                "updated_at = ? WHERE alias_id = ?",
                (spec["text"], spec["language"], int(now_ms), int(row["alias_id"])),
            )
            changed = True
    for spec in wanted.values():
        connection.execute(
            "INSERT INTO place_aliases(place_id, text, normalized, language, source, lifecycle, "
            "revision, created_at, updated_at) VALUES (?, ?, ?, ?, 'USER', 'ACTIVE', 1, ?, ?)",
            (
                int(place_id),
                spec["text"],
                spec["normalized"],
                spec["language"],
                int(now_ms),
                int(now_ms),
            ),
        )
        changed = True
    return changed


def retire_place(
    connection: sqlite3.Connection,
    *,
    place_id: int,
    now_ms: int,
    refresh: revision_module.ProjectionRefresh | None = None,
) -> dict[str, Any]:
    """Soft-delete one place, refusing while anything still references it."""
    row = get_place_row(connection, place_id)
    if str(row["lifecycle"]) == "RETIRED":
        return get_place_view(connection, place_id)
    children = int(
        connection.execute(
            "SELECT COUNT(*) FROM places WHERE parent_place_id = ? AND lifecycle = 'ACTIVE'",
            (int(place_id),),
        ).fetchone()[0]
    )
    assigned = int(
        connection.execute(
            "SELECT COUNT(*) FROM asset_place_assignments WHERE place_id = ? "
            "AND mode = 'MANUAL_ASSIGNED'",
            (int(place_id),),
        ).fetchone()[0]
    )
    excluded = int(
        connection.execute(
            "SELECT COUNT(*) FROM asset_place_exclusions WHERE place_id = ? AND active = 1",
            (int(place_id),),
        ).fetchone()[0]
    )
    if children or assigned or excluded:
        raise fail(
            PLACE_REFERENCED,
            "this place is still referenced and cannot be retired",
            place_id=int(place_id),
        )
    connection.execute(
        "UPDATE places SET lifecycle = 'RETIRED', revision = revision + 1, updated_at = ? "
        "WHERE place_id = ?",
        (int(now_ms), int(place_id)),
    )
    view = get_place_view(connection, place_id)
    revision_module.bump_filter_revision(
        connection,
        cause=revision_module.CAUSE_HIERARCHY_CHANGED,
        now_ms=int(now_ms),
        asset_ids=(),
        refresh=refresh,
    )
    return view


def assets_effectively_assigned_to(
    connection: sqlite3.Connection, place_ids: Iterable[int]
) -> list[int]:
    """Assets whose current effective place is one of ``place_ids`` (no descendants)."""
    ids = sorted({int(value) for value in place_ids})
    if not ids:
        return []
    placeholders = ", ".join("?" for _ in ids)
    return [
        int(row[0])
        for row in connection.execute(
            "SELECT asset_id FROM asset_place_assignments "
            f"WHERE mode = 'MANUAL_ASSIGNED' AND place_id IN ({placeholders}) ORDER BY asset_id",
            tuple(ids),
        ).fetchall()
    ]


def assets_effectively_assigned_in_subtree(
    connection: sqlite3.Connection, place_ids: Iterable[int]
) -> list[int]:
    """Assets effectively assigned to any of these places or one of their descendants."""
    return assets_effectively_assigned_to(
        connection, descendant_ids(connection, place_ids)
    )


def list_places(
    connection: sqlite3.Connection,
    *,
    limit: int,
    after_place_id: int = 0,
    name: str | None = None,
    kind: str | None = None,
    parent_place_id: int | None = None,
    ancestor_place_id: int | None = None,
    lifecycle: str | None = None,
) -> list[sqlite3.Row]:
    """One bounded keyset page of ``places`` rows, filtered by the frozen predicates."""
    conditions = [
        "p.place_id > ?",
        "NOT EXISTS (SELECT 1 FROM cn_admin_place_refs m WHERE m.place_id=p.place_id)",
    ]
    parameters: list[Any] = [int(after_place_id)]
    if name:
        needle = f"%{normalize_name(name, field='name')}%"
        conditions.append(
            "(p.normalized_name LIKE ? OR EXISTS (SELECT 1 FROM place_aliases a "
            "WHERE a.place_id = p.place_id AND a.lifecycle = 'ACTIVE' AND a.normalized LIKE ?))"
        )
        parameters.extend([needle, needle])
    if kind:
        check_choice(kind, PLACE_KINDS, field="kind")
        conditions.append("p.kind = ?")
        parameters.append(str(kind))
    if parent_place_id is not None:
        conditions.append("p.parent_place_id = ?")
        parameters.append(int(parent_place_id))
    if ancestor_place_id is not None:
        subtree = descendant_ids(connection, [int(ancestor_place_id)])
        if not subtree:
            return []
        placeholders = ", ".join("?" for _ in subtree)
        conditions.append(f"p.place_id IN ({placeholders})")
        parameters.extend(subtree)
    if lifecycle:
        check_choice(lifecycle, PLACE_LIFECYCLE, field="lifecycle")
        conditions.append("p.lifecycle = ?")
        parameters.append(str(lifecycle))
    parameters.append(int(limit))
    projection = ", ".join(f"p.{field}" for field in PLACE_FIELDS)
    return list(
        connection.execute(
            f"SELECT {projection} FROM places AS p WHERE {' AND '.join(conditions)} "
            "ORDER BY p.place_id LIMIT ?",
            tuple(parameters),
        ).fetchall()
    )
