"""Personal person collections backed by stable person IDs and effective face links."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import secrets
import unicodedata
from typing import Any

from photo_search.api.errors import INVALID_CURSOR, VALIDATION_ERROR, ApiError
from photo_search.api.queries import encode_capture_cursor, read_asset_page_in_snapshot
from photo_search.catalog.errors import CATALOG_FILTER_REVISION_STALE
from photo_search.catalog.video import photo_asset_sql
from photo_search.persons import albums
from photo_search.persons.collection_sql import collection_condition
from photo_search.persons.contracts import normalize_person_name
from photo_search.persons.identity import resolve_canonical_person_id
from photo_search.persons.members import MEMBER_PREDICATE
from photo_search.places import revision as filter_revision
from photo_search.places.revision import read_filter_revision

_MISSING = "PERSON_COLLECTION_NOT_FOUND"
_CURSOR_PREFIX = "pc1"
_PEOPLE_CURSOR_PREFIX = "pcp-recent1"
_PHOTOS_CURSOR_PREFIX = "pcf1"
_MAX_BATCH = 500
_MAX_NAME = 80

_TRUSTED_REASSOCIATION_CTE = (
    "trusted_reassociations AS ("
    "SELECT m.old_face_id, MIN(m.new_face_id) AS new_face_id "
    "FROM archive_page AS archived "
    "CROSS JOIN face_redetection_mappings AS m ON m.old_face_id=archived.source_face_id "
    "JOIN face_redetection_groups AS g ON g.old_face_id=m.old_face_id "
    "JOIN faces AS mapped_face ON mapped_face.face_id=m.new_face_id "
    "AND mapped_face.asset_id=m.asset_id AND mapped_face.spec_revision=m.new_spec_revision "
    "WHERE g.asset_id=m.asset_id AND g.old_spec_revision=m.old_spec_revision "
    "AND g.state='CONFIRMED' AND g.candidate_state='UNIQUE_HIGH' "
    "AND m.state='CONFIRMED' AND m.match_state='HIGH' "
    "GROUP BY m.old_face_id HAVING COUNT(*)=1)"
)


def _encode(prefix: str, binding: list[Any], position: Any) -> str:
    raw = json.dumps([prefix, *binding, position], separators=(",", ":"), ensure_ascii=True)
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode(cursor: str | None, prefix: str, binding: list[Any]) -> Any:
    if not cursor:
        return None
    try:
        if len(cursor) > 2048:
            raise ValueError
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        values = json.loads(raw)
        if not isinstance(values, list) or values[:-1] != [prefix, *binding]:
            raise ValueError
        return values[-1]
    except (ValueError, TypeError, UnicodeError, binascii.Error) as error:
        raise ApiError(INVALID_CURSOR, "合集内容已变化，请刷新后继续", status=409) from error


def _require_collection(connection: Any, collection_id: int) -> Any:
    row = connection.execute(
        "SELECT * FROM person_collections WHERE collection_id = ?", (int(collection_id),)
    ).fetchone()
    if row is None:
        raise ApiError(_MISSING, "人物合集不存在", status=404)
    return row


def _require_collection_instance(connection: Any, collection_id: int, instance_token: str) -> Any:
    row = _require_collection(connection, collection_id)
    if str(row["instance_token"]) != str(instance_token):
        raise ApiError(_MISSING, "人物合集实例已变化，请刷新", status=404)
    return row


def _normalise_collection_name(value: Any) -> tuple[str, str]:
    display, _ = normalize_person_name(value)
    if len(display) > _MAX_NAME:
        raise ApiError(VALIDATION_ERROR, "合集名称最多 80 个字符", status=422)
    # The display spelling is preserved; the key treats case and whitespace variants as one.
    return display, "nfc1:" + unicodedata.normalize("NFC", display.casefold())


def _canonical_cte(*, collection_count: int = 0) -> str:
    """Resolve stable anchors, optionally binding a bounded collection page first."""
    scope = (
        "WHERE m.collection_id IN (" + ", ".join("?" for _ in range(collection_count)) + ") "
        if collection_count else ""
    )
    return (
        "WITH RECURSIVE chain(collection_id, origin_id, person_id, merged_into, path) AS ("
        "SELECT m.collection_id, p.person_id, p.person_id, p.merged_into, ',' || p.person_id || ',' "
        "FROM person_collection_members AS m JOIN persons AS p ON p.person_id = m.person_id "
        + scope +
        "UNION ALL SELECT c.collection_id, c.origin_id, p.person_id, p.merged_into, "
        "c.path || p.person_id || ',' FROM chain AS c JOIN persons AS p "
        "ON p.person_id = c.merged_into WHERE c.merged_into IS NOT NULL "
        "AND instr(c.path, ',' || p.person_id || ',') = 0), "
        "canonical_members AS (SELECT DISTINCT collection_id, origin_id, person_id "
        "FROM chain WHERE merged_into IS NULL) "
    )


def membership_version(connection: Any) -> str:
    """Return a stable version for all collection membership/name changes."""
    rows = connection.execute(
        "SELECT collection_id, instance_token, revision FROM person_collections "
        "ORDER BY collection_id"
    ).fetchall()
    payload = json.dumps(
        [(int(row["collection_id"]), str(row["instance_token"]), int(row["revision"])) for row in rows],
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def project_person_collections(connection: Any, person_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """Batch-project current canonical collection labels for one bounded people page."""
    if not person_ids:
        return {}
    page_values = ", ".join("(?)" for _ in person_ids)
    rows = connection.execute(
        "WITH RECURSIVE page(person_id) AS (VALUES " + page_values + "), "
        "lineage(canonical_id, anchor_id, path) AS ("
        "SELECT person_id, person_id, ',' || person_id || ',' FROM page "
        "UNION ALL SELECT lineage.canonical_id, p.person_id, "
        "lineage.path || p.person_id || ',' FROM lineage JOIN persons AS p "
        "ON p.merged_into = lineage.anchor_id "
        "WHERE instr(lineage.path, ',' || p.person_id || ',') = 0) "
        "SELECT DISTINCT lineage.canonical_id AS person_id, pc.collection_id, pc.name "
        "FROM lineage JOIN person_collection_members AS m ON m.person_id = lineage.anchor_id "
        "JOIN person_collections AS pc ON pc.collection_id = m.collection_id "
        "ORDER BY lineage.canonical_id, pc.collection_id",
        tuple(int(person_id) for person_id in person_ids),
    ).fetchall()
    result: dict[int, list[dict[str, Any]]] = {int(person_id): [] for person_id in person_ids}
    for row in rows:
        result[int(row["person_id"])].append(
            {"collection_id": int(row["collection_id"]), "name": str(row["name"])}
        )
    return result


def _collection_summary(row: Any) -> dict[str, Any]:
    return {
        "collection_id": int(row["collection_id"]),
        "instance_token": str(row["instance_token"]),
        "name": str(row["name"]),
        "revision": int(row["revision"]),
        "member_count": int(row["member_count"]),
        "face_member_count": int(row["face_member_count"]),
        "created_at": int(row["created_at"]),
        "updated_at": int(row["updated_at"]),
    }


def _collection_photo_counts(connection: Any, collection_ids: list[int]) -> dict[int, int]:
    """Count distinct active assets for one bounded page of collections."""
    if not collection_ids:
        return {}
    placeholders = ", ".join("?" for _ in collection_ids)
    photo_sql, photo_parameters = photo_asset_sql(asset_alias="a")
    ids = tuple(int(collection_id) for collection_id in collection_ids)
    # Bound the recursive seed before reading faces. Multiple stable anchors may
    # resolve to the same person after a merge, so visit that person's faces once.
    # Without an explicit join order SQLite can scan every effective face link
    # before checking the bounded page, even when it contains only small albums.
    rows = connection.execute(
        _canonical_cte(collection_count=len(collection_ids))
        + ", current_members AS (SELECT DISTINCT collection_id, person_id FROM canonical_members), "
        "eligible_assets AS (SELECT cm.collection_id, f.asset_id "
        "FROM current_members AS cm "
        "CROSS JOIN face_person_links AS l INDEXED BY idx_face_person_links_person "
        "ON l.person_id = cm.person_id "
        "JOIN faces AS f ON f.face_id = l.face_id "
        "JOIN assets AS a ON a.asset_id = f.asset_id "
        "WHERE a.state = 'ACTIVE' AND "
        + MEMBER_PREDICATE + " AND " + photo_sql + " AND "
        "(NOT EXISTS (SELECT 1 FROM file_locations any_location WHERE any_location.asset_id=a.asset_id) "
        "OR EXISTS (SELECT 1 FROM file_locations present_location WHERE present_location.asset_id=a.asset_id "
        "AND present_location.availability='PRESENT')) UNION "
        "SELECT fm.collection_id, a.asset_id FROM person_collection_face_members AS fm "
        "JOIN assets AS a ON a.asset_id = fm.asset_id WHERE fm.collection_id IN (" + placeholders + ") "
        "AND a.state='ACTIVE' AND a.sha256=fm.input_revision AND " + photo_sql + " AND "
        "(NOT EXISTS (SELECT 1 FROM file_locations any_location WHERE any_location.asset_id=fm.asset_id) "
        "OR EXISTS (SELECT 1 FROM file_locations present_location WHERE present_location.asset_id=fm.asset_id "
        "AND present_location.availability='PRESENT'))) "
        "SELECT collection_id, COUNT(DISTINCT asset_id) AS photo_count FROM eligible_assets GROUP BY collection_id",
        (*ids, *photo_parameters, *ids, *photo_parameters),
    ).fetchall()
    return {int(row["collection_id"]): int(row["photo_count"]) for row in rows}


def list_collections(catalog: Any, *, limit: int = 40, cursor: str | None = None) -> dict[str, Any]:
    """Read collection identities, member counts and photo counts together."""
    with catalog.read_snapshot():
        return _list_collections(catalog, limit=limit, cursor=cursor)


def _list_collections(catalog: Any, *, limit: int = 40, cursor: str | None = None) -> dict[str, Any]:
    limit = max(1, min(int(limit), 100))
    binding: list[Any] = ["collections1"]
    after = _decode(cursor, _CURSOR_PREFIX, binding)
    if after is not None and (type(after) is not int or after < 1):
        raise ApiError(INVALID_CURSOR, "合集游标无效", status=409)
    where = "WHERE c.collection_id > ?" if after else ""
    params: tuple[Any, ...] = ((int(after),) if after is not None else ()) + (limit + 1,)
    rows = catalog.read_all(
        _canonical_cte()
        + ", collection_page AS (SELECT c.* FROM person_collections AS c " + where + " ORDER BY c.collection_id LIMIT ?), "
        "face_counts AS (SELECT fm.collection_id, COUNT(*) AS face_member_count "
        "FROM person_collection_face_members AS fm JOIN collection_page AS cp ON cp.collection_id=fm.collection_id "
        "GROUP BY fm.collection_id) "
        "SELECT cp.*, COUNT(DISTINCT cm.person_id) AS member_count, COALESCE(fc.face_member_count, 0) AS face_member_count "
        "FROM collection_page AS cp LEFT JOIN canonical_members AS cm ON cm.collection_id=cp.collection_id "
        "LEFT JOIN face_counts AS fc ON fc.collection_id=cp.collection_id "
        "GROUP BY cp.collection_id ORDER BY cp.collection_id",
        params,
    )
    more = len(rows) > limit
    rows = rows[:limit]
    photo_counts = _collection_photo_counts(
        catalog.connection, [int(row["collection_id"]) for row in rows]
    )
    return {
        "collections": [
            {**_collection_summary(row), "photo_count": photo_counts.get(int(row["collection_id"]), 0)}
            for row in rows
        ],
        "has_more": more,
        "next_cursor": _encode(_CURSOR_PREFIX, binding, int(rows[-1]["collection_id"])) if more else None,
    }


def create_collection(catalog: Any, name: Any) -> dict[str, Any]:
    display, name_key = _normalise_collection_name(name)
    now = catalog.now_ms()
    instance_token = secrets.token_hex(16)
    try:
        with catalog.write() as connection:
            cursor = connection.execute(
                "INSERT INTO person_collections(name, name_key, revision, created_at, updated_at, instance_token) "
                "VALUES (?, ?, 1, ?, ?, ?)", (display, name_key, now, now, instance_token)
            )
            collection_id = int(cursor.lastrowid)
            row = connection.execute(
                "SELECT c.*, 0 AS member_count, 0 AS face_member_count FROM person_collections AS c WHERE collection_id = ?",
                (collection_id,),
            ).fetchone()
            return {"collection": _collection_summary(row)}
    except Exception as error:
        if "UNIQUE constraint failed" in str(error):
            raise ApiError(VALIDATION_ERROR, "已存在同名人物合集", status=409) from error
        raise


def collection_detail(catalog: Any, collection_id: int, *, instance_token: str) -> dict[str, Any]:
    with catalog.read_snapshot():
        return _collection_detail(catalog, collection_id, instance_token=instance_token, include_photo_count=True)


def _collection_detail(catalog: Any, collection_id: int, *, instance_token: str, include_photo_count: bool = False) -> dict[str, Any]:
    connection = catalog.connection
    row = _require_collection_instance(connection, collection_id, instance_token)
    count = connection.execute(
        _canonical_cte(collection_count=1) + "SELECT COUNT(DISTINCT person_id) FROM canonical_members",
        (int(collection_id),),
    ).fetchone()[0]
    face_count = connection.execute(
        "SELECT COUNT(*) FROM person_collection_face_members WHERE collection_id = ?", (int(collection_id),)
    ).fetchone()[0]
    summary = _collection_summary({**dict(row), "member_count": int(count), "face_member_count": int(face_count)})
    if include_photo_count:
        summary["photo_count"] = _collection_photo_counts(connection, [int(collection_id)]).get(int(collection_id), 0)
    return {"collection": summary}


def rename_collection(catalog: Any, collection_id: int, name: Any, *, instance_token: str) -> dict[str, Any]:
    display, name_key = _normalise_collection_name(name)
    try:
        with catalog.write() as connection:
            current = _require_collection_instance(connection, collection_id, instance_token)
            if str(current["name"]) != display:
                connection.execute(
                    "UPDATE person_collections SET name = ?, name_key = ?, revision = revision + 1, updated_at = ? "
                    "WHERE collection_id = ?",
                    (display, name_key, catalog.now_ms(), int(collection_id)),
                )
            # Reuse the already-held write transaction, without nesting a read snapshot.
            return _collection_detail(catalog, collection_id, instance_token=instance_token)
    except Exception as error:
        if "UNIQUE constraint failed" in str(error):
            raise ApiError(VALIDATION_ERROR, "已存在同名人物合集", status=409) from error
        raise


def delete_collection(catalog: Any, collection_id: int, *, instance_token: str) -> dict[str, Any]:
    with catalog.write() as connection:
        _require_collection_instance(connection, collection_id, instance_token)
        connection.execute("DELETE FROM person_collections WHERE collection_id = ?", (int(collection_id),))
        filter_revision.bump_filter_revision(
            connection, cause=filter_revision.CAUSE_PERSON_COLLECTION_CHANGED, now_ms=catalog.now_ms()
        )
    return {"deleted": True, "collection_id": int(collection_id)}


def edit_members(
    catalog: Any,
    collection_id: int,
    *,
    action: Any,
    person_ids: Any,
    instance_token: str,
    hide_after_add: Any = False,
) -> dict[str, Any]:
    if action not in ("add", "remove"):
        raise ApiError(VALIDATION_ERROR, "action 必须是 add 或 remove", status=422)
    if not isinstance(person_ids, list) or not 1 <= len(person_ids) <= _MAX_BATCH:
        raise ApiError(VALIDATION_ERROR, "每次需要提交 1 到 500 个 person_id", status=422)
    if any(type(value) is not int or value < 1 for value in person_ids) or len(set(person_ids)) != len(person_ids):
        raise ApiError(VALIDATION_ERROR, "person_ids 必须是互不重复的正整数", status=422)
    if type(hide_after_add) is not bool or (hide_after_add and action != "add"):
        raise ApiError(VALIDATION_ERROR, "hide_after_add 只可用于 add", status=422)
    with catalog.write() as connection:
        _require_collection_instance(connection, collection_id, instance_token)
        # Validate the complete input set and resolve redirects before the first write.
        canonical: list[int] = []
        for requested in person_ids:
            canonical.append(resolve_canonical_person_id(connection, requested))
        canonical = sorted(set(canonical))
        if action == "add":
            changed = 0
            for person_id in canonical:
                result = connection.execute(
                    "INSERT OR IGNORE INTO person_collection_members(collection_id, person_id) VALUES (?, ?)",
                    (int(collection_id), person_id),
                )
                changed += int(result.rowcount == 1)
            if hide_after_add:
                connection.executemany(
                    "INSERT OR IGNORE INTO person_hidden(person_id) VALUES (?)",
                    [(person_id,) for person_id in canonical],
                )
        else:
            marks = ",".join("?" for _ in canonical)
            query = (
                _canonical_cte()
                + "DELETE FROM person_collection_members WHERE collection_id = ? AND person_id IN ("
                "SELECT origin_id FROM canonical_members WHERE collection_id = ? AND person_id IN ("
                + marks + ")) RETURNING person_id"
            )
            removed_anchors = connection.execute(
                query, (int(collection_id), int(collection_id), *canonical)
            ).fetchall()
            changed = len(removed_anchors)
        if changed:
            connection.execute(
                "UPDATE person_collections SET revision = revision + 1, updated_at = ? WHERE collection_id = ?",
                (catalog.now_ms(), int(collection_id)),
            )
            filter_revision.bump_filter_revision(
                connection, cause=filter_revision.CAUSE_PERSON_COLLECTION_CHANGED, now_ms=catalog.now_ms()
            )
        row = connection.execute(
            "SELECT revision FROM person_collections WHERE collection_id = ?", (int(collection_id),)
        ).fetchone()
        return {"action": action, "changed": int(changed), "revision": int(row["revision"]), "hidden": bool(hide_after_add)}


def edit_face_members(
    catalog: Any,
    collection_id: int,
    *,
    action: Any,
    face_ids: Any,
    instance_token: str,
    expected_revision: int,
    hide_after_add: bool = False,
) -> dict[str, Any]:
    """Add or remove bounded static face anchors without changing face identity state."""
    if action not in ("add", "remove"):
        raise ApiError(VALIDATION_ERROR, "action 必须是 add 或 remove", status=422)
    if not isinstance(face_ids, list) or not 1 <= len(face_ids) <= _MAX_BATCH:
        raise ApiError(VALIDATION_ERROR, "每次需要提交 1 到 500 个 face_id", status=422)
    if any(not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value) for value in face_ids):
        raise ApiError(VALIDATION_ERROR, "face_ids 必须是 64 位小写十六进制字符串", status=422)
    if len(set(face_ids)) != len(face_ids):
        raise ApiError(VALIDATION_ERROR, "face_ids 必须互不重复", status=422)
    if type(hide_after_add) is not bool or (hide_after_add and action != "add"):
        raise ApiError(VALIDATION_ERROR, "hide_after_add 只可用于 add", status=422)
    with catalog.write() as connection:
        collection = _require_collection_instance(connection, collection_id, instance_token)
        if int(collection["revision"]) != int(expected_revision):
            raise ApiError(INVALID_CURSOR, "合集版本已变化，请刷新后重试", status=409)
        ids = tuple(face_ids)
        placeholders = ",".join("?" for _ in ids)
        if action == "add":
            photo_sql, photo_parameters = photo_asset_sql(asset_alias="a")
            rows = connection.execute(
                "SELECT f.face_id, f.asset_id, f.input_revision FROM faces AS f "
                "JOIN assets AS a ON a.asset_id=f.asset_id WHERE f.face_id IN (" + placeholders + ") "
                "AND a.state='ACTIVE' AND f.input_revision=a.sha256 "
                "AND (NOT EXISTS (SELECT 1 FROM file_locations AS any_location "
                "WHERE any_location.asset_id=a.asset_id) OR EXISTS ("
                "SELECT 1 FROM file_locations AS present_location "
                "WHERE present_location.asset_id=a.asset_id AND present_location.availability='PRESENT')) "
                "AND " + photo_sql,
                (*ids, *photo_parameters),
            ).fetchall()
            found = {str(row["face_id"]): row for row in rows}
            if len(found) != len(ids):
                raise ApiError(VALIDATION_ERROR, "所选人脸已失效或照片内容已变化，请刷新后重新选择", status=409)
            old = connection.execute(
                "SELECT source_face_id FROM person_collection_face_members WHERE collection_id=? "
                "AND source_face_id IN (" + placeholders + ")",
                (int(collection_id), *ids),
            ).fetchall()
            old_ids = {str(row["source_face_id"]) for row in old}
            now = catalog.now_ms()
            connection.executemany(
                "INSERT OR IGNORE INTO person_collection_face_members "
                "(collection_id, source_face_id, asset_id, input_revision, created_at) VALUES (?, ?, ?, ?, ?)",
                [
                    (int(collection_id), face_id, int(found[face_id]["asset_id"]), str(found[face_id]["input_revision"]), now)
                    for face_id in ids
                ],
            )
            changed = len(ids) - len(old_ids)
            if hide_after_add:
                connection.executemany(
                    "INSERT OR IGNORE INTO face_inbox_hidden(face_id, ignore_state) VALUES (?, 'TEMPORARY')",
                    [(face_id,) for face_id in ids],
                )
        else:
            deleted = connection.execute(
                "DELETE FROM person_collection_face_members WHERE collection_id=? AND source_face_id IN (" + placeholders + ")",
                (int(collection_id), *ids),
            )
            changed = max(0, int(deleted.rowcount))
        if changed:
            connection.execute(
                "UPDATE person_collections SET revision=revision+1, updated_at=? WHERE collection_id=?",
                (catalog.now_ms(), int(collection_id)),
            )
            filter_revision.bump_filter_revision(
                connection, cause=filter_revision.CAUSE_PERSON_COLLECTION_CHANGED, now_ms=catalog.now_ms()
            )
        current = connection.execute(
            "SELECT revision FROM person_collections WHERE collection_id=?", (int(collection_id),)
        ).fetchone()
        return {"action": action, "changed": changed, "revision": int(current["revision"]), "face_count": len(ids), "hidden": bool(hide_after_add)}


def face_members_page(
    catalog: Any,
    collection_id: int,
    *,
    instance_token: str,
    limit: int = 40,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Read archive revision, cursor and face facts in the same snapshot."""
    with catalog.read_snapshot():
        return _face_members_page(catalog, collection_id, instance_token=instance_token, limit=limit, cursor=cursor)


def _face_members_page(
    catalog: Any,
    collection_id: int,
    *,
    instance_token: str,
    limit: int = 40,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Page saved soft anchors and mark stale or missing source detections honestly."""
    limit = max(1, min(int(limit), 100))
    connection = catalog.connection
    collection = _require_collection_instance(connection, collection_id, instance_token)
    current_filter_revision = read_filter_revision(connection)
    binding = [int(collection_id), str(collection["instance_token"]), int(collection["revision"]), int(current_filter_revision)]
    after = _decode(cursor, "pcf-members1", binding)
    if after is not None and (not isinstance(after, str) or len(after) != 64 or any(c not in "0123456789abcdef" for c in after)):
        raise ApiError(INVALID_CURSOR, "合集人脸游标无效", status=409)
    continuation = " AND fm.source_face_id > ?" if after is not None else ""
    values: tuple[Any, ...] = (int(collection_id),) + (() if after is None else (after,)) + (limit + 1,)
    rows = catalog.read_all(
        "WITH archive_page AS (SELECT fm.* FROM person_collection_face_members AS fm "
        "WHERE fm.collection_id=?" + continuation + " ORDER BY fm.source_face_id LIMIT ?), "
        + _TRUSTED_REASSOCIATION_CTE + ", page AS (SELECT fm.source_face_id, fm.asset_id, fm.input_revision, fm.created_at, "
        "a.sha256, a.state, COALESCE(mapped_face.face_id, f.face_id) AS current_face_id, "
        "COALESCE(mapped_face.quality_state, f.quality_state) AS quality_state, "
        "COALESCE(mapped_face.quality_reason, f.quality_reason) AS quality_reason, "
        "(NOT EXISTS (SELECT 1 FROM file_locations any_location WHERE any_location.asset_id=fm.asset_id) "
        "OR EXISTS (SELECT 1 FROM file_locations present_location WHERE present_location.asset_id=fm.asset_id "
        "AND present_location.availability='PRESENT')) AS source_present "
        "FROM archive_page AS fm LEFT JOIN assets AS a ON a.asset_id=fm.asset_id "
        "LEFT JOIN trusted_reassociations AS reassociation ON reassociation.old_face_id=fm.source_face_id "
        "LEFT JOIN faces AS mapped_face ON mapped_face.face_id=reassociation.new_face_id "
        "AND mapped_face.asset_id=fm.asset_id AND mapped_face.input_revision=fm.input_revision "
        "LEFT JOIN faces AS f ON f.face_id=fm.source_face_id AND f.asset_id=fm.asset_id "
        "AND f.input_revision=fm.input_revision), suggestions AS ("
        "SELECT l.face_id, l.person_id, ROW_NUMBER() OVER (PARTITION BY l.face_id ORDER BY l.confidence DESC, l.person_id) AS position "
        "FROM page JOIN face_person_links AS l ON l.face_id=page.current_face_id "
        "JOIN persons AS p ON p.person_id=l.person_id "
        "WHERE l.decision='SUGGESTED' AND p.merged_into IS NULL AND NOT EXISTS ("
        "SELECT 1 FROM person_hidden AS h WHERE h.person_id=p.person_id AND h.ignore_state='EXCLUDED') "
        "AND NOT EXISTS (SELECT 1 FROM face_exclusions AS e WHERE e.face_id=l.face_id AND e.person_id=l.person_id AND e.active=1)) "
        "SELECT page.*, h.ignore_state, l.person_id AS assigned_person_id, assigned_person.name AS assigned_person_name, "
        "suggestions.person_id AS candidate_person_id, candidate_person.name AS candidate_name, candidate_person.avatar_face_id AS candidate_avatar_face_id "
        "FROM page LEFT JOIN face_inbox_hidden AS h ON h.face_id=page.current_face_id "
        "LEFT JOIN face_person_links AS l ON l.face_id=page.current_face_id "
        "AND " + MEMBER_PREDICATE + " LEFT JOIN persons AS assigned_person ON assigned_person.person_id=l.person_id "
        "LEFT JOIN suggestions ON suggestions.face_id=page.current_face_id AND suggestions.position=1 "
        "LEFT JOIN persons AS candidate_person ON candidate_person.person_id=suggestions.person_id "
        "ORDER BY page.source_face_id",
        values,
    )
    more = len(rows) > limit
    rows = rows[:limit]
    faces = []
    for row in rows:
        asset_active = row["state"] == "ACTIVE" and bool(row["source_present"])
        content_current = asset_active and row["sha256"] == row["input_revision"]
        detected = bool(row["current_face_id"])
        faces.append({
            "face_id": str(row["source_face_id"]),
            "display_face_id": row["current_face_id"],
            "asset_id": int(row["asset_id"]),
            "input_revision": str(row["input_revision"]),
            "created_at": int(row["created_at"]),
            "status": "CURRENT" if content_current and detected else "NEEDS_REASSOCIATION" if content_current else "CONTENT_CHANGED" if asset_active else "SOURCE_MISSING",
            "thumbnail_url": f"/api/v1/faces/{row['current_face_id']}/thumbnail" if content_current and detected else None,
            "ignore_state": row["ignore_state"],
            "quality_state": row["quality_state"],
            "assigned_person": None if row["assigned_person_id"] is None else {
                "person_id": int(row["assigned_person_id"]), "name": row["assigned_person_name"] or "未命名的人",
            },
            "candidate": None if (
                row["assigned_person_id"] is not None or row["candidate_person_id"] is None
                or row["ignore_state"] == "EXCLUDED" or row["quality_state"] != "OK"
                or not (content_current and detected)
            ) else {
                "person_id": int(row["candidate_person_id"]), "name": row["candidate_name"] or "未命名的人",
                "thumbnail_url": None if row["candidate_avatar_face_id"] is None else f"/api/v1/faces/{row['candidate_avatar_face_id']}/thumbnail",
            },
        })
    return {
        "collection": {"collection_id": int(collection_id), "instance_token": str(collection["instance_token"]), "revision": int(collection["revision"])},
        "faces": faces,
        "has_more": more,
        "next_cursor": _encode("pcf-members1", binding, str(rows[-1]["source_face_id"])) if more else None,
    }


def people_page(
    catalog: Any, collection_id: int, *, instance_token: str, limit: int = 40, cursor: str | None = None
) -> dict[str, Any]:
    with catalog.read_snapshot():
        return _people_page(catalog, collection_id, instance_token=instance_token, limit=limit, cursor=cursor)


def _people_page(
    catalog: Any, collection_id: int, *, instance_token: str, limit: int = 40, cursor: str | None = None
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 100))
    connection = catalog.connection
    collection = _require_collection_instance(connection, collection_id, instance_token)
    filter_revision = read_filter_revision(connection)
    binding = [int(collection_id), str(collection["instance_token"]), int(collection["revision"]), int(filter_revision)]
    after = _decode(cursor, _PEOPLE_CURSOR_PREFIX, binding)
    if after is not None and (
        not isinstance(after, list) or len(after) != 3
        or any(type(value) is not int or value < 0 for value in after[:2])
        or type(after[2]) is not int or after[2] < 1
    ):
        raise ApiError(INVALID_CURSOR, "合集人物游标无效", status=409)
    continuation = ""
    photo_sql, _photo_parameters = photo_asset_sql(asset_alias="f")
    parameters: list[Any] = [int(collection_id)]
    if after is not None:
        continuation = (
            "WHERE (COALESCE(ec.asset_count, 0) < ? OR "
            "(COALESCE(ec.asset_count, 0) = ? AND COALESCE(ec.latest_asset_added_at, 0) < ?) OR "
            "(COALESCE(ec.asset_count, 0) = ? AND COALESCE(ec.latest_asset_added_at, 0) = ? AND p.person_id > ?)) "
        )
        parameters.extend([after[0], after[0], after[1], after[0], after[1], after[2]])
    rows = catalog.read_all(
        _canonical_cte()
        + ", candidates AS (SELECT DISTINCT p.* FROM canonical_members AS cm "
        "JOIN persons AS p ON p.person_id = cm.person_id WHERE cm.collection_id = ?), "
        "effective_counts AS (SELECT l.person_id, COUNT(DISTINCT f.asset_id) AS asset_count, "
        "COALESCE(MAX(a.created_at), 0) AS latest_asset_added_at "
        "FROM candidates AS c JOIN face_person_links AS l ON l.person_id=c.person_id "
        "JOIN faces AS f ON f.face_id=l.face_id JOIN assets AS a ON a.asset_id=f.asset_id WHERE " + MEMBER_PREDICATE + " AND " + photo_sql + " GROUP BY l.person_id) "
        "SELECT p.*, COALESCE(ec.asset_count, 0) AS page_asset_count, "
        "COALESCE(ec.latest_asset_added_at, 0) AS page_latest_asset_added_at FROM candidates AS p "
        "LEFT JOIN effective_counts AS ec ON ec.person_id=p.person_id "
        + continuation + "ORDER BY COALESCE(ec.asset_count, 0) DESC, COALESCE(ec.latest_asset_added_at, 0) DESC, p.person_id LIMIT ?",
        (*parameters, limit + 1),
    )
    more = len(rows) > limit
    rows = rows[:limit]
    summaries = albums._album_summaries(catalog.connection, rows, reuse_asset_counts=True)
    return {
        "collection": {"collection_id": int(collection_id), "instance_token": str(collection["instance_token"]), "name": str(collection["name"]), "revision": int(collection["revision"])},
        "people": summaries,
        "has_more": more,
        "next_cursor": _encode(
            _PEOPLE_CURSOR_PREFIX, binding,
            [int(rows[-1]["page_asset_count"]), int(rows[-1]["page_latest_asset_added_at"]), int(rows[-1]["person_id"])],
        ) if more else None,
    }


def _collection_photo_faces(connection: Any, collection_id: int, asset_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not asset_ids:
        return {}
    placeholders = ",".join("?" for _ in asset_ids)
    rows = connection.execute(
        _canonical_cte(collection_count=1)
        + ", current_members AS (SELECT DISTINCT collection_id, person_id FROM canonical_members), "
        "eligible_faces AS (SELECT DISTINCT f.asset_id, f.face_id, f.face_pixels "
        "FROM faces AS f JOIN face_person_links AS l ON l.face_id = f.face_id "
        "JOIN current_members AS cm ON cm.person_id = l.person_id "
        "WHERE cm.collection_id = ? AND f.asset_id IN (" + placeholders + ") AND " + MEMBER_PREDICATE + "), "
        "ranked_faces AS (SELECT asset_id, face_id, ROW_NUMBER() OVER "
        "(PARTITION BY asset_id ORDER BY face_pixels DESC, face_id) AS position FROM eligible_faces) "
        "SELECT asset_id, face_id, position FROM ranked_faces "
        "WHERE position <= 9 ORDER BY asset_id, position",
        (int(collection_id), int(collection_id), *asset_ids),
    ).fetchall()
    grouped: dict[int, list[str]] = {}
    for row in rows:
        grouped.setdefault(int(row["asset_id"]), []).append(str(row["face_id"]))
    archived_rows = connection.execute(
        _canonical_cte(collection_count=1)
        + ", archive_page AS (SELECT fm.* FROM person_collection_face_members AS fm "
        "WHERE fm.collection_id=? AND fm.asset_id IN (" + placeholders + ")), "
        + _TRUSTED_REASSOCIATION_CTE
        + ", current_members AS (SELECT DISTINCT collection_id, person_id FROM canonical_members), "
        "ranked AS (SELECT fm.asset_id, fm.source_face_id, COALESCE(mapped_face.face_id, f.face_id) AS current_face_id, "
        "ROW_NUMBER() OVER (PARTITION BY fm.asset_id ORDER BY fm.created_at, fm.source_face_id) AS position, "
        "COUNT(*) OVER (PARTITION BY fm.asset_id) AS total "
        "FROM archive_page AS fm JOIN assets AS a ON a.asset_id=fm.asset_id "
        "LEFT JOIN trusted_reassociations AS reassociation ON reassociation.old_face_id=fm.source_face_id "
        "LEFT JOIN faces AS mapped_face ON mapped_face.face_id=reassociation.new_face_id "
        "AND mapped_face.asset_id=fm.asset_id AND mapped_face.input_revision=fm.input_revision "
        "LEFT JOIN faces AS f ON f.face_id=fm.source_face_id AND f.asset_id=fm.asset_id "
        "AND f.input_revision=fm.input_revision WHERE a.state='ACTIVE' "
        "AND a.sha256=fm.input_revision AND (NOT EXISTS (SELECT 1 FROM file_locations any_location WHERE any_location.asset_id=fm.asset_id) "
        "OR EXISTS (SELECT 1 FROM file_locations present_location WHERE present_location.asset_id=fm.asset_id "
        "AND present_location.availability='PRESENT')) "
        "AND NOT EXISTS (SELECT 1 FROM face_person_links AS l JOIN current_members AS cm ON cm.person_id=l.person_id "
        "WHERE cm.collection_id=fm.collection_id AND l.face_id=COALESCE(mapped_face.face_id, f.face_id) AND " + MEMBER_PREDICATE + ")) "
        "SELECT * FROM ranked WHERE position<=20 ORDER BY asset_id, position",
        (int(collection_id), int(collection_id), *asset_ids),
    ).fetchall()
    archived: dict[int, list[dict[str, Any]]] = {}
    archived_more: dict[int, bool] = {}
    for row in archived_rows:
        archived.setdefault(int(row["asset_id"]), []).append({
            "face_id": str(row["source_face_id"]),
            "display_face_id": row["current_face_id"],
            "thumbnail_url": f"/api/v1/faces/{row['current_face_id']}/thumbnail" if row["current_face_id"] else None,
            "status": "CURRENT" if row["current_face_id"] else "NEEDS_REASSOCIATION",
        })
        archived_more[int(row["asset_id"])] = int(row["total"]) > 20
    return {
        asset_id: {
            "person_faces": [{"face_id": face_id, "thumbnail_url": f"/api/v1/faces/{face_id}/thumbnail"} for face_id in ids[:8]],
            "person_faces_has_more": len(ids) > 8,
            "archived_faces": archived.get(asset_id, []),
            "archived_faces_has_more": archived_more.get(asset_id, False),
        }
        for asset_id in grouped.keys() | archived.keys()
        for ids in [grouped.get(asset_id, [])]
    }


def photo_page(
    catalog: Any, cache: Any, collection_id: int, *, instance_token: str, limit: int = 60, cursor: str | None = None
) -> dict[str, Any]:
    with catalog.read_snapshot():
        return _photo_page(catalog, cache, collection_id, instance_token=instance_token, limit=limit, cursor=cursor)


def _photo_page(
    catalog: Any, cache: Any, collection_id: int, *, instance_token: str, limit: int = 60, cursor: str | None = None
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 100))
    connection = catalog.connection
    collection = _require_collection_instance(connection, collection_id, instance_token)
    filter_revision = read_filter_revision(connection)
    binding = [int(collection_id), str(collection["instance_token"]), int(collection["revision"]), int(filter_revision)]
    inner = _decode(cursor, _PHOTOS_CURSOR_PREFIX, binding)
    if inner is not None and not isinstance(inner, str):
        raise ApiError(INVALID_CURSOR, "合集照片游标无效", status=409)
    condition, condition_parameters = collection_condition(int(collection_id))
    photo_sql, photo_parameters = photo_asset_sql()
    photo_count = int(
        catalog.read_one(
            f"SELECT COUNT(*) AS total FROM assets AS a WHERE {condition} AND {photo_sql}",
            (*condition_parameters, *photo_parameters),
        )["total"]
    )
    page = read_asset_page_in_snapshot(
        catalog, cache, limit=limit + 1, cursor=inner, order="capture_desc", collection_id=int(collection_id)
    )
    if int(page["catalog_filter_revision"]) != int(filter_revision):
        raise ApiError(CATALOG_FILTER_REVISION_STALE, "图库人物关系已变化，请刷新合集", status=409)
    page["has_more"] = len(page["assets"]) > limit
    page["assets"] = page["assets"][:limit]
    page["limit"] = limit
    page["next_cursor"] = None
    if page["has_more"]:
        last_id = int(page["assets"][-1]["asset_id"])
        metadata = catalog.read_one("SELECT taken_at_sort FROM asset_metadata WHERE asset_id = ?", (last_id,))
        sort_key = None if metadata is None else metadata["taken_at_sort"]
        next_inner = encode_capture_cursor(int(sort_key is not None), int(sort_key or 0), last_id, page["catalog_filter_revision"])
        page["next_cursor"] = _encode(_PHOTOS_CURSOR_PREFIX, binding, next_inner)
    ids = [int(item["asset_id"]) for item in page["assets"]]
    time_views, _ = albums._photo_page_facts(connection, asset_ids=ids, person_id=-1)
    faces = _collection_photo_faces(connection, collection_id, ids)
    for item in page["assets"]:
        item.pop("current_location", None)
        asset_id = int(item["asset_id"])
        item["taken_time"] = time_views.get(asset_id)
        item.update(faces.get(asset_id, {"person_faces": [], "person_faces_has_more": False, "archived_faces": [], "archived_faces_has_more": False}))
    page["collection"] = {
        "collection_id": int(collection_id),
        "instance_token": str(collection["instance_token"]),
        "name": str(collection["name"]),
        "revision": int(collection["revision"]),
        "photo_count": photo_count,
    }
    page.pop("search", None)
    return page


__all__ = ["collection_detail", "create_collection", "delete_collection", "edit_members", "list_collections", "people_page", "photo_page", "rename_collection"]
