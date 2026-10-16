"""Reproducible 300k synthetic person/collection reads; no model, HTTP or UI claims.

``prepare`` backs up an existing isolated catalog and augments ONLY the copy.
``measure`` opens that copy read-only, builds an independent Python membership oracle,
and times unchanged product functions. Hashing/validation/JSON encoding are untimed.
``verify`` checks the saved evidence and writes measurement-validation JUnit (not pytest).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import statistics
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

# Initialize the person service before api.queries to respect its existing import cycle.
from photo_search.persons import albums, collections  # isort: skip
from photo_search.api.queries import encode_capture_cursor
from photo_search.catalog.db import Catalog
from photo_search.catalog.schema import SUPPORTED_SCHEMA_VERSION, verify_schema
from photo_search.catalog.sqlite_io import open_readonly
from photo_search.face.contract import FACE_DETECTOR_ID, FACE_SPEC_REVISION
from photo_search.face.processor import face_id_for
from photo_search.media.cache import MediaCache
from photo_search.places.revision import read_filter_revision

ASSETS = 300_000
ADDED_PEOPLE = 1_000
NOW = 1_792_000_000_000
SOURCE_FILES = (
    "src/photo_search/persons/albums.py", "src/photo_search/persons/collections.py",
    "src/photo_search/persons/members.py", "src/photo_search/persons/collection_sql.py",
    "src/photo_search/api/queries.py", "src/photo_search/catalog/image_policy.py",
    "src/photo_search/persons/read.py", "src/photo_search/face/detail_policy.py",
    "tools/benchmarks/p4_perf_people_300k.py",
)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def file_hash(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def database(root: Path) -> Path:
    return root / "data/catalog/catalog.db"


def prepare(root: Path, source: Path) -> dict[str, Any]:
    target = database(root)
    if target.exists() or source.resolve() == target.resolve():
        raise ValueError("use a fresh isolated output directory; never replace the source")
    source_before = {"sha256": file_hash(source), "size": source.stat().st_size,
                     "mtime_ns": source.stat().st_mtime_ns}
    target.parent.mkdir(parents=True)
    started = time.perf_counter()
    source_connection = open_readonly(source)
    connection = sqlite3.connect(target)
    connection.row_factory = sqlite3.Row
    try:
        source_connection.backup(connection)
    finally:
        source_connection.close()
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        schema_version = int(connection.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()[0])
        assert schema_version == SUPPORTED_SCHEMA_VERSION
        verify_schema(connection, schema_version)
        assert connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == ASSETS
        assert connection.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM faces").fetchone()[0] == 150_000
        assert connection.execute("SELECT COUNT(*) FROM person_collections").fetchone()[0] == 1
        people = []
        for person_id in range(2, 1022):
            name = None if person_id % 5 == 0 else f"合成人物 {person_id:04d}"
            people.append((person_id, name, None if name is None else "nfc1:" + name,
                           NOW + person_id, NOW + person_id))
        connection.executemany(
            "INSERT INTO persons(person_id,name,name_key,revision,created_at,updated_at) "
            "VALUES(?,?,?,1,?,?)", people,
        )
        connection.execute(
            "INSERT INTO persons(person_id,name,name_key,merged_into,revision,created_at,updated_at) "
            "VALUES(1022,'合成旧身份','nfc1:合成旧身份',2,1,?,?)", (NOW, NOW),
        )
        weights = [1 + i % 20 for i in range(ADDED_PEOPLE)]
        sizes = [150_000 * weight // sum(weights) for weight in weights]
        for index in range(150_000 - sum(sizes)):
            sizes[index] += 1
        primary_owner = []
        for index, count in enumerate(sizes):
            primary_owner.extend([index + 2] * count)
        assert len(primary_owner) == 150_000
        face_rows, link_rows, control_rows, exclusion_rows = [], [], [], []
        inserted = 0

        def flush() -> None:
            nonlocal inserted
            connection.executemany(
                "INSERT INTO faces(face_id,asset_id,spec_revision,detector_id,ordinal,input_revision,"
                "location_id,bbox_json,landmarks_json,det_score,face_pixels,blur_variance,"
                "quality_state,quality_reason,artifact_id,face_revision,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,'[0,0,64,64]','[[1,2],[2,3],[3,4],[4,5],[5,6]]',"
                "0.95,4096,200,'OK',NULL,'synthetic-people-benchmark',1,?,?)", face_rows,
            )
            connection.executemany(
                "INSERT INTO face_person_links(face_id,person_id,assignment_kind,decision,"
                "confidence,reason,revision,created_at,updated_at) VALUES(?,?,'AUTO',?,0.9,?,1,?,?)",
                link_rows,
            )
            connection.executemany(
                "INSERT INTO face_assignment_controls(face_id,mode,revision,updated_at) "
                "VALUES(?,'MANUAL_UNASSIGNED',1,?)", control_rows,
            )
            connection.executemany(
                "INSERT INTO face_exclusions(face_id,person_id,active,created_at) "
                "VALUES(?,?,1,?)", exclusion_rows,
            )
            connection.commit()
            inserted += len(face_rows)
            face_rows.clear()
            link_rows.clear()
            control_rows.clear()
            exclusion_rows.clear()

        for asset_id, person_id, ordinal in (
            [(150_001 + i, owner, 0) for i, owner in enumerate(primary_owner)]
            + [(asset_id, 2 + (asset_id - 1) % ADDED_PEOPLE, 1)
               for asset_id in range(1, 15_001)]
        ):
            sha = f"{asset_id:064x}"
            face_id = face_id_for(input_revision=sha, ordinal=ordinal, bbox=[0.0, 0.0, 64.0, 64.0])
            face_rows.append((face_id, asset_id, FACE_SPEC_REVISION, FACE_DETECTOR_ID,
                              ordinal, sha, asset_id, NOW, NOW))
            suggested = 150_101 <= asset_id <= 150_150 and ordinal == 0
            link_rows.append((face_id, person_id, "SUGGESTED" if suggested else "CONFIRMED",
                              "BORDERLINE" if suggested else None, NOW, NOW))
            if 150_001 <= asset_id <= 150_050 and ordinal == 0:
                control_rows.append((face_id, NOW))
            if 150_051 <= asset_id <= 150_100 and ordinal == 0:
                exclusion_rows.append((face_id, person_id, NOW))
            if len(face_rows) >= 5000:
                flush()
        if face_rows:
            flush()
        connection.executemany(
            "INSERT INTO person_hidden(person_id,ignore_state) VALUES(?,'TEMPORARY')",
            [(person_id,) for person_id in range(2, 1002) if person_id % 37 == 0],
        )
        for collection_id in range(2, 202):
            name = f"合成合集 {collection_id:03d}"
            token = hashlib.sha256(f"people-300k:{collection_id}".encode()).hexdigest()[:32]
            connection.execute(
                "INSERT INTO person_collections(collection_id,name,name_key,revision,"
                "created_at,updated_at,instance_token) VALUES(?,?,?,1,?,?,?)",
                (collection_id, name, "nfc1:" + name, NOW, NOW, token),
            )
            members = list(range(1, 1023)) if collection_id == 2 else [
                2 + ((collection_id - 3) * 5 + position) % ADDED_PEOPLE for position in range(5)
            ]
            connection.executemany(
                "INSERT INTO person_collection_members(collection_id,person_id) VALUES(?,?)",
                [(collection_id, member) for member in members],
            )
        connection.commit()
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        counts = {table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                  for table in ("assets", "file_locations", "faces", "persons", "face_person_links",
                                "face_assignment_controls", "face_exclusions", "person_hidden",
                                "person_collections", "person_collection_members")}
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()
    source_after = {"sha256": file_hash(source), "size": source.stat().st_size,
                    "mtime_ns": source.stat().st_mtime_ns}
    assert source_before == source_after
    payload = {
        "kind": "synthetic metadata/authority fixture; no media/model/face inference",
        "source": str(source), "source_before": source_before, "source_after": source_after,
        "source_unchanged": True, "target": str(target), "target_sha256": file_hash(target),
        "counts": counts, "additional_faces": inserted, "additional_nonempty_people": 1000,
        "empty_identities": 20, "redirect_identity": {"1022": 2},
        "large_original_person_id": 1, "large_original_person_expected_photos": 150_000,
        "shared_assets_with_additional_faces": 15_000,
        "negative_membership_controls": {"manual_unassigned": 50, "excluded": 50, "suggested": 50},
        "collections": "1 original 150k-photo collection; 1 all-identity collection; 199 five-person collections",
        "schema_version": schema_version, "elapsed_seconds": time.perf_counter() - started,
        "notes": ["Only the SQLite backup copy was modified.",
                  "Original 150k synthetic faces retain their prior timeline-scale-v1 spec.",
                  "Added rows use deterministic face_id_for and current spec but are not model output.",
                  "The catalog has metadata only; thumbnail/preview files are absent.",
                  "Manual-control, exclusion and redirect rows are explicit synthetic test state, not user history."],
    }
    save(root / "fixture.json", payload)
    return payload


class Oracle:
    """Independent raw-row/Python-set oracle, without product membership SQL fragments."""

    def __init__(self, connection: sqlite3.Connection):
        self.people = {int(r["person_id"]): dict(r) for r in connection.execute("SELECT * FROM persons")}
        self.collections = {int(r["collection_id"]): dict(r) for r in connection.execute("SELECT * FROM person_collections")}
        self.hidden = {int(r[0]) for r in connection.execute("SELECT person_id FROM person_hidden")}
        self.assets = {int(r[0]): (int(r[1]), None if r[2] is None else int(r[2])) for r in connection.execute(
            "SELECT a.asset_id,a.created_at,m.taken_at_sort FROM assets a LEFT JOIN asset_metadata m USING(asset_id) WHERE a.state='ACTIVE'"
        )}
        photo_ids = {int(r[0]) for r in connection.execute("SELECT asset_id,relative_path FROM file_locations")
                     if str(r[1]).lower().endswith((".jpg", ".jpeg", ".png"))}
        assert set(self.assets) == photo_ids and len(self.assets) == ASSETS
        # This oracle intentionally refuses a fixture requiring a second policy implementation.
        assert connection.execute("SELECT COUNT(*) FROM face_detail_auto_assets").fetchone()[0] == 0
        controls = {str(r[0]) for r in connection.execute("SELECT face_id FROM face_assignment_controls WHERE mode='MANUAL_UNASSIGNED'")}
        exclusions = {(str(r[0]), int(r[1])) for r in connection.execute("SELECT face_id,person_id FROM face_exclusions WHERE active=1")}
        self.members: dict[int, set[int]] = defaultdict(set)
        self.face_counts: dict[int, int] = defaultdict(int)
        self.faces: dict[int, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
        for face_id, asset_id, person_id, decision in connection.execute(
            "SELECT f.face_id,f.asset_id,l.person_id,l.decision FROM faces f JOIN face_person_links l USING(face_id)"
        ):
            if decision != "CONFIRMED" or face_id in controls or (face_id, person_id) in exclusions:
                continue
            assert asset_id in self.assets
            self.members[person_id].add(asset_id)
            self.face_counts[person_id] += 1
            self.faces[person_id][asset_id].append(face_id)
        assert self.members[1] == set(range(1, 150_001))
        self.collection_members: dict[int, set[int]] = defaultdict(set)
        for collection_id, person_id in connection.execute("SELECT collection_id,person_id FROM person_collection_members"):
            visited = set()
            while self.people[person_id]["merged_into"] is not None:
                assert person_id not in visited
                visited.add(person_id)
                person_id = int(self.people[person_id]["merged_into"])
            self.collection_members[collection_id].add(person_id)
        self.collection_assets = {
            cid: set().union(*(self.members[pid] for pid in pids))
            for cid, pids in self.collection_members.items()
        }
        self.latest = {pid: max((self.assets[aid][0] for aid in ids), default=0)
                       for pid, ids in self.members.items()}

    def ordered_photos(self, ids: set[int]) -> list[int]:
        return sorted(ids, key=lambda aid: (self.assets[aid][1] is not None,
                                            self.assets[aid][1] or 0, aid), reverse=True)

    def ordered_people(self, sort: str, *, kind: str = "all", visibility: str = "all", q: str = "") -> list[int]:
        result = [pid for pid, row in self.people.items() if row["merged_into"] is None and self.members[pid]
                  and (kind == "all" or (kind == "named") == (row["name"] is not None))
                  and (visibility == "all" or (visibility == "hidden") == (pid in self.hidden))
                  and (not q or q.casefold() in str(row["name"] or "").casefold())]
        return self.sort_people(result, sort)

    def sort_people(self, ids: list[int] | set[int], sort: str) -> list[int]:
        if sort == "person_id":
            return sorted(ids)
        if sort == "photos_desc":
            return sorted(ids, key=lambda pid: (-len(self.members[pid]), pid))
        return sorted(ids, key=lambda pid: (-len(self.members[pid]), -self.latest.get(pid, 0), pid))


@dataclass
class Scenario:
    name: str
    invoke: Callable[[], dict[str, Any]]
    ids: list[int]
    offset: int
    limit: int
    field: str
    kind: str
    owner: int | None = None


def scenarios(catalog: Catalog, cache: MediaCache, oracle: Oracle) -> list[Scenario]:
    result = []
    revision = read_filter_revision(catalog.connection)
    for sort in ("person_id", "photos_desc", "photos_recent_desc"):
        ids = oracle.ordered_people(sort)
        for offset, label in ((0, "first"), (800, "deep800")):
            cursor = None
            if offset:
                pid = ids[offset - 1]
                position = pid if sort == "person_id" else [len(oracle.members[pid]), pid] if sort == "photos_desc" else [len(oracle.members[pid]), oracle.latest[pid], pid]
                prefix = {"person_id": "people1", "photos_desc": "people-count1", "photos_recent_desc": "people-recent1"}[sort]
                cursor = albums._encode([prefix, "all", "", revision], position)
            result.append(Scenario(f"people_{sort}_{label}",
                lambda sort=sort, cursor=cursor: albums.people_page(catalog, kind="all", q="", limit=40, cursor=cursor, sort=sort),
                ids, offset, 40, "persons", "people"))
    for kind, visibility, query, label in (("unnamed", "all", "", "unnamed"), ("all", "hidden", "", "hidden"), ("named", "all", "001", "literal_name")):
        ids = oracle.ordered_people("person_id", kind=kind, visibility=visibility, q=query)
        result.append(Scenario("people_" + label,
            lambda kind=kind, visibility=visibility, query=query: albums.people_page(catalog, kind=kind, q=query, visibility=visibility, limit=40, cursor=None),
            ids, 0, 40, "persons", "people"))
    for pid, label in ((1, "150k"), (21, "ordinary")):
        ids = oracle.ordered_photos(oracle.members[pid])
        for offset, suffix in ((0, "first"), (len(ids) * 4 // 5, "deep80pct")):
            cursor = None
            if offset:
                aid = ids[offset - 1]
                taken = oracle.assets[aid][1]
                inner = encode_capture_cursor(int(taken is not None), int(taken or 0), aid, revision)
                cursor = albums._encode(["personphotos1", pid], inner)
            result.append(Scenario(f"person_{label}_{suffix}",
                lambda pid=pid, cursor=cursor: albums.photo_page(catalog, cache, person_id=pid, limit=60, cursor=cursor),
                ids, offset, 60, "assets", "person_photos", pid))
    cids = sorted(oracle.collections)
    for offset, label in ((0, "first"), (160, "deep160")):
        cursor = None if not offset else collections._encode("pc1", ["collections1"], cids[offset - 1])
        result.append(Scenario("collections_" + label,
            lambda cursor=cursor: collections.list_collections(catalog, limit=40, cursor=cursor),
            cids, offset, 40, "collections", "collections"))
    cid = 2
    row = oracle.collections[cid]
    binding = [cid, row["instance_token"], row["revision"], revision]
    ids = oracle.sort_people(oracle.collection_members[cid], "photos_recent_desc")
    for offset, label in ((0, "first"), (800, "deep800")):
        cursor = None
        if offset:
            pid = ids[offset - 1]
            cursor = collections._encode("pcp-recent1", binding, [len(oracle.members[pid]), oracle.latest.get(pid, 0), pid])
        result.append(Scenario("collection_people_" + label,
            lambda cursor=cursor, row=row: collections.people_page(catalog, 2, instance_token=row["instance_token"], limit=40, cursor=cursor),
            ids, offset, 40, "people", "collection_people", 2))
    for cid, label in ((1, "150k"), (2, "all"), (3, "ordinary")):
        row = oracle.collections[cid]
        ids = oracle.ordered_photos(oracle.collection_assets[cid])
        binding = [cid, row["instance_token"], row["revision"], revision]
        for offset, suffix in ((0, "first"), (len(ids) * 4 // 5, "deep80pct")):
            cursor = None
            if offset:
                aid = ids[offset - 1]
                taken = oracle.assets[aid][1]
                inner = encode_capture_cursor(int(taken is not None), int(taken or 0), aid, revision)
                cursor = collections._encode("pcf1", binding, inner)
            result.append(Scenario(f"collection_{label}_{suffix}",
                lambda cid=cid, token=row["instance_token"], cursor=cursor: collections.photo_page(catalog, cache, cid, instance_token=token, limit=60, cursor=cursor),
                ids, offset, 60, "assets", "collection_photos", cid))
    return result


def validate(payload: dict[str, Any], scenario: Scenario, oracle: Oracle) -> None:
    key = "asset_id" if scenario.field == "assets" else "collection_id" if scenario.field == "collections" else "person_id"
    actual = [int(row[key]) for row in payload[scenario.field]]
    expected = scenario.ids[scenario.offset:scenario.offset + scenario.limit]
    assert actual == expected, {"scenario": scenario.name, "actual": actual, "expected": expected}
    assert payload["has_more"] == (scenario.offset + len(actual) < len(scenario.ids))
    assert bool(payload["next_cursor"]) == bool(payload["has_more"])
    if scenario.kind == "people":
        assert payload["total"] == len(scenario.ids)
    if scenario.field in ("persons", "people"):
        for row in payload[scenario.field]:
            pid = int(row["person_id"])
            assert row["asset_count"] == len(oracle.members[pid])
            assert row["face_count"] == oracle.face_counts[pid]
    if scenario.kind == "collections":
        for row in payload["collections"]:
            cid = int(row["collection_id"])
            assert row["member_count"] == len(oracle.collection_members[cid])
            assert row["photo_count"] == len(oracle.collection_assets[cid])
    if scenario.kind == "person_photos":
        assert payload["person"]["asset_count"] == len(scenario.ids)
    if scenario.kind == "collection_photos":
        assert payload["collection"]["photo_count"] == len(scenario.ids)
    if scenario.field == "assets":
        pids = {scenario.owner} if scenario.kind == "person_photos" else oracle.collection_members[scenario.owner]
        for row in payload["assets"]:
            expected_faces = set().union(*(set(oracle.faces[pid].get(row["asset_id"], [])) for pid in pids))
            actual_faces = {face["face_id"] for face in row["person_faces"]}
            assert actual_faces <= expected_faces
            assert len(actual_faces) == min(8, len(expected_faces))
            assert row["person_faces_has_more"] == (len(expected_faces) > 8)


def paging_evidence(catalog: Catalog, cache: MediaCache, oracle: Oracle) -> list[dict[str, Any]]:
    """Untimed real next-cursor traversal plus independent tail checks."""
    records = []
    walks = [
        ("people_all_person_id", "persons", "people", oracle.ordered_people("person_id"), None,
         lambda cursor: albums.people_page(catalog, kind="all", q="", limit=100, cursor=cursor)),
        ("collections_all", "collections", "collections", sorted(oracle.collections), None,
         lambda cursor: collections.list_collections(catalog, limit=100, cursor=cursor)),
        ("collection_2_all_people", "people", "collection_people",
         oracle.sort_people(oracle.collection_members[2], "photos_recent_desc"), 2,
         lambda cursor: collections.people_page(catalog, 2, instance_token=oracle.collections[2]["instance_token"], limit=100, cursor=cursor)),
    ]
    for name, field, kind, ids, owner, invoke in walks:
        seen, hashes, cursor = [], [], None
        while True:
            answer = invoke(cursor)
            case = Scenario(name, lambda: answer, ids, len(seen), 100, field, kind, owner)
            validate(answer, case, oracle)
            key = "collection_id" if field == "collections" else "person_id"
            seen.extend(int(row[key]) for row in answer[field])
            hashes.append(digest(answer))
            if not answer["has_more"]:
                break
            cursor = answer["next_cursor"]
            assert len(hashes) <= math.ceil(len(ids) / 100)
        assert seen == ids and len(seen) == len(set(seen))
        records.append({"name": name, "kind": "complete_real_cursor_traversal", "pages": len(hashes),
                        "count": len(seen), "response_hashes": hashes, "ids_sha256": digest(seen), "passed": True})
    revision = read_filter_revision(catalog.connection)
    for kind, owner in (("person_photos", 1), ("person_photos", 21),
                        ("collection_photos", 1), ("collection_photos", 2), ("collection_photos", 3)):
        ids = oracle.ordered_photos(oracle.members[owner] if kind == "person_photos" else oracle.collection_assets[owner])
        if kind == "person_photos":
            invoke = lambda cursor, owner=owner: albums.photo_page(catalog, cache, person_id=owner, limit=60, cursor=cursor)
            encode = lambda inner, owner=owner: albums._encode(["personphotos1", owner], inner)
        else:
            row = oracle.collections[owner]
            binding = [owner, row["instance_token"], row["revision"], revision]
            invoke = lambda cursor, owner=owner, token=row["instance_token"]: collections.photo_page(catalog, cache, owner, instance_token=token, limit=60, cursor=cursor)
            encode = lambda inner, binding=binding: collections._encode("pcf1", binding, inner)
        first = invoke(None)
        validate(first, Scenario(kind, lambda: first, ids, 0, 60, "assets", kind, owner), oracle)
        second = invoke(first["next_cursor"])
        validate(second, Scenario(kind, lambda: second, ids, 60, 60, "assets", kind, owner), oracle)
        offset = max(1, len(ids) - 30)
        anchor = ids[offset - 1]
        taken = oracle.assets[anchor][1]
        tail_cursor = encode(encode_capture_cursor(int(taken is not None), int(taken or 0), anchor, revision))
        tail = invoke(tail_cursor)
        validate(tail, Scenario(kind, lambda: tail, ids, offset, 60, "assets", kind, owner), oracle)
        records.append({"name": f"{kind}_{owner}", "kind": "real_first_to_second_cursor_plus_oracle_tail",
                        "tail_offset": offset, "response_hashes": [digest(first), digest(second), digest(tail)],
                        "tail_has_more": tail["has_more"], "passed": True})
    return records


class TimedCursor:
    def __init__(self, cursor: sqlite3.Cursor, record: dict[str, Any]):
        self.cursor, self.record = cursor, record

    def fetchone(self) -> Any:
        start = time.perf_counter()
        try:
            return self.cursor.fetchone()
        finally:
            self.record["fetch_ms"] += (time.perf_counter() - start) * 1000

    def fetchall(self) -> Any:
        start = time.perf_counter()
        try:
            return self.cursor.fetchall()
        finally:
            self.record["fetch_ms"] += (time.perf_counter() - start) * 1000

    def __iter__(self):
        while (row := self.fetchone()) is not None:
            yield row

    def __getattr__(self, name: str) -> Any:
        return getattr(self.cursor, name)


class TimedConnection:
    """Diagnostic execute/fetch timing, never used in formal latency samples."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.records: list[dict[str, Any]] = []

    def execute(self, sql: str, parameters: Any = ()) -> TimedCursor:
        start = time.perf_counter()
        cursor = self.connection.execute(sql, parameters)
        execute_ms = (time.perf_counter() - start) * 1000
        record = {"sql": sql, "parameter_count": len(parameters), "parameters_sha256": digest(parameters),
                  "execute_ms": execute_ms, "fetch_ms": 0.0}
        self.records.append(record)
        return TimedCursor(cursor, record)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.connection, name)


def profile(root: Path, output: Path) -> None:
    path = database(root)
    before = file_hash(path)
    connection = open_readonly(path, immutable=True)
    catalog = Catalog(connection, db_path=path, data_root=root / "data")
    try:
        oracle = Oracle(connection)
        cases = scenarios(catalog, MediaCache.open(root / "data"), oracle)
        spy = TimedConnection(connection)
        catalog.connection = spy
        results = []
        for case in cases:
            if case.name not in {"people_photos_recent_desc_first", "collections_first", "collections_deep160",
                                  "person_150k_first", "collection_all_first"}:
                continue
            spy.records = []
            started = time.perf_counter()
            answer = case.invoke()
            total = (time.perf_counter() - started) * 1000
            validate(answer, case, oracle)
            sql_total = sum(row["execute_ms"] + row["fetch_ms"] for row in spy.records)
            results.append({"name": case.name, "total_ms": total, "sql_ms": sql_total,
                            "outside_sql_including_instrumentation_ms": total - sql_total,
                            "response_sha256": digest(answer), "queries": spy.records})
        save(output, {"note": "Single diagnostic calls with execute/fetch proxies; includes instrumentation overhead. Not 30-sample latency evidence. All responses independently validated.",
                      "source_sha256": {name: file_hash(Path(name)) for name in SOURCE_FILES},
                      "results": results, "fixture_before_sha256": before, "fixture_after_sha256": file_hash(path)})
    finally:
        catalog.close()


def measure(root: Path, output: Path, samples: int, selected: list[str] | None) -> dict[str, Any]:
    started_unix = time.time()
    path = database(root)
    before = file_hash(path)
    connection = open_readonly(path, immutable=True)
    catalog = Catalog(connection, db_path=path, data_root=root / "data")
    cache = MediaCache.open(root / "data")
    try:
        assert catalog.schema_version == SUPPORTED_SCHEMA_VERSION
        oracle = Oracle(connection)
        cases = scenarios(catalog, cache, oracle)
        if selected:
            cases = [case for case in cases if case.name in selected]
            assert {case.name for case in cases} == set(selected)
        payload = {
            "layer": "Unmodified product core functions; SQL, member/face/time projections and media readiness lookup included; HTTP/model/UI/JSON serialization excluded",
            "fixture": json.loads((root / "fixture.json").read_text(encoding="utf-8")),
            "source_sha256": {name: file_hash(Path(name)) for name in SOURCE_FILES},
            "samples_requested": samples, "results": [],
            "started_unix_seconds": started_unix, "schema_version_read": catalog.schema_version,
            "first_sample_note": "First call for this scenario in this process, after oracle construction; not an OS-cold or disk-cold sample.",
            "deep_cursor_note": "Deep anchors are constructed using product cursor encoders from independent sorted oracle rows; deep navigation is not traversed in a browser.",
            "oracle": {"implementation": "Raw rows + Python effective-member sets; no product member predicate or collection SQL used",
                       "people_with_members": len([pid for pid in oracle.people if oracle.members[pid]]),
                       "collection_2_canonical_members": len(oracle.collection_members[2]),
                       "collection_2_distinct_photos": len(oracle.collection_assets[2]),
                       "large_person_members_sha256": digest(sorted(oracle.members[1]))},
            "storage": "D: SATA HDD; scheduling and any external interference documented by caller; no SSD comparison",
        }
        for case in cases:
            record: dict[str, Any] = {"name": case.name, "member_count": len(case.ids),
                "offset": case.offset, "limit": case.limit, "oracle_member_order_sha256": digest(case.ids),
                "expected_page_sha256": digest(case.ids[case.offset:case.offset + case.limit]),
                "samples_ms": [], "hashes": [], "errors": [], "first_ms": None}
            for number in range(samples + 1):
                start = time.perf_counter()
                elapsed = None
                try:
                    answer = case.invoke()
                    elapsed = (time.perf_counter() - start) * 1000
                    validate(answer, case, oracle)
                    answer_hash = digest(answer)
                except Exception as error:  # Preserve every failed call in evidence.
                    if elapsed is None:
                        elapsed = (time.perf_counter() - start) * 1000
                    record["errors"].append({"sample": number, "type": type(error).__name__, "message": str(error)})
                    answer_hash = None
                if number == 0:
                    record["first_ms"] = elapsed
                else:
                    record["samples_ms"].append(elapsed)
                record["hashes"].append(answer_hash)
                if number and number % 5 == 0:
                    print(json.dumps({"scenario": case.name, "completed_samples": number,
                                      "last_ms": round(elapsed, 2)}, ensure_ascii=False), flush=True)
            values = record["samples_ms"]
            record.update(median_ms=statistics.median(values), p95_ms=sorted(values)[math.ceil(len(values) * .95) - 1],
                          max_ms=max(values), result_hash_stable=len(set(record["hashes"])) == 1 and None not in record["hashes"])
            payload["results"].append(record)
            save(output, payload)
            print(json.dumps({"scenario": case.name, "first_ms": round(record["first_ms"], 2),
                              "median_ms": round(record["median_ms"], 2), "p95_ms": round(record["p95_ms"], 2),
                              "errors": len(record["errors"])}, ensure_ascii=False), flush=True)
        if not selected:
            payload["paging_evidence"] = paging_evidence(catalog, cache, oracle)
        payload["fixture_after_sha256"] = file_hash(path)
        payload["fixture_unchanged"] = before == payload["fixture_after_sha256"]
        payload["source_code_unchanged"] = all(file_hash(Path(name)) == sha for name, sha in payload["source_sha256"].items())
        payload["finished_unix_seconds"] = time.time()
        save(output, payload)
        return payload
    finally:
        catalog.close()


def verify(path: Path, junit: Path) -> bool:
    payload = json.loads(path.read_text(encoding="utf-8"))
    checks = {"fixture_unchanged": payload.get("fixture_unchanged", False),
              "source_code_unchanged": payload.get("source_code_unchanged", False),
              "source_baseline_unchanged": payload["fixture"]["source_unchanged"],
              "300k_assets": payload["fixture"]["counts"]["assets"] == ASSETS,
              "supported_schema": payload.get("schema_version_read") == SUPPORTED_SCHEMA_VERSION}
    checks["all_23_scenarios"] = len(payload["results"]) == 23
    checks["independent_paging"] = (len(payload.get("paging_evidence", [])) == 8
                                    and all(row["passed"] for row in payload["paging_evidence"]))
    for result in payload["results"]:
        prefix = result["name"] + ":"
        values = result["samples_ms"]
        checks[prefix + "30_samples"] = len(values) >= 30
        checks[prefix + "no_failures"] = not result["errors"]
        checks[prefix + "stable_complete_response"] = result["result_hash_stable"]
        checks[prefix + "statistics"] = (result["median_ms"] == statistics.median(values)
            and result["p95_ms"] == sorted(values)[math.ceil(len(values) * .95) - 1]
            and result["max_ms"] == max(values))
    suite = ET.Element("testsuite", name="people_300k_measurement_evidence_not_pytest", tests=str(len(checks)),
                       failures=str(sum(not value for value in checks.values())), errors="0", skipped="0")
    for name, passed in checks.items():
        case = ET.SubElement(suite, "testcase", classname="people_300k_measurement", name=name)
        if not passed:
            ET.SubElement(case, "failure", message="measurement evidence invariant failed")
    junit.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(suite).write(junit, encoding="utf-8", xml_declaration=True)
    return all(checks.values())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "measure", "verify", "profile"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--junit", type=Path)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--scenarios", nargs="*")
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[2]
    temporary = repository / ".tmp"
    artifacts = repository / "docs/reports/artifacts"
    args.root = args.root.resolve()
    if not args.root.is_relative_to(temporary):
        parser.error("--root must stay in the repository-local .tmp directory")
    if args.source is not None:
        args.source = args.source.resolve()
        if not args.source.is_relative_to(temporary):
            parser.error("--source must be an isolated repository-local .tmp fixture")
    for option in ("output", "junit"):
        path = getattr(args, option)
        if path is not None:
            path = path.resolve()
            if not any(path.is_relative_to(parent) for parent in (temporary, artifacts)):
                parser.error(f"--{option} must stay in .tmp or docs/reports/artifacts")
            setattr(args, option, path)
    if args.action == "prepare":
        if args.source is None:
            parser.error("prepare requires --source")
        result = prepare(args.root, args.source)
        print(json.dumps({"counts": result["counts"], "seconds": result["elapsed_seconds"]}, ensure_ascii=False))
    elif args.action == "measure":
        if args.samples < 1 or args.output is None:
            parser.error("measure requires --output and at least one sample")
        result = measure(args.root, args.output, args.samples, args.scenarios)
        raise SystemExit(1 if any(row["errors"] for row in result["results"]) else 0)
    elif args.action == "profile":
        if args.output is None:
            parser.error("profile requires --output")
        profile(args.root, args.output)
    else:
        if args.output is None or args.junit is None:
            parser.error("verify requires --output (existing JSON) and --junit")
        raise SystemExit(0 if verify(args.output, args.junit) else 1)


if __name__ == "__main__":
    main()
