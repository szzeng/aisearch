"""Bounded search rechecks preserve the full authoritative membership semantics."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from p3_persons_page_fixtures import (
    add_link,
    add_person,
    insert_face,
    set_control,
    set_exclusion,
)

from photo_search.catalog.db import Catalog
from photo_search.config import catalog_db_path
from photo_search.face.contract import FACE_DETAIL_SPEC_REVISION
from photo_search.face.detail_policy import POLICY_REVISION
from photo_search.search.retrieval import SearchScope, person_condition, scope_condition
from photo_search.search.service import _verified_ids


@pytest.fixture(scope="module")
def library_template(tmp_path_factory):
    # Build the synthetic metadata once. Tests receive ordinary file copies below,
    # never this connection or a shared writable database.
    tmp_path = tmp_path_factory.mktemp("candidate-recheck-template")
    with Catalog.open(data_root=tmp_path / "data") as catalog:
        names = (
            "ab", "a", "b", "blocked", "excluded", "suggested", "manual", "detail",
            "held", "hidden", "missing", "video", "other_source", "inactive", "no_location", "cross_source",
        )
        ids, faces = {}, {}
        people = {name: add_person(catalog, name=name) for name in ("A", "B", "C")}
        with catalog.write() as connection:
            connection.executemany(
                "INSERT INTO source_roots(source_id,display_name,root_path,status) VALUES(?,?,?,'ONLINE')",
                [(1, "one", "synthetic-one"), (2, "two", "synthetic-two")],
            )
            for index, name in enumerate(names, 1):
                ids[name] = index
                connection.execute(
                    "INSERT INTO assets(asset_id,sha256,byte_size,created_at) VALUES(?,?,1,1)",
                    (index, f"{index:064x}"),
                )
                if name != "no_location":
                    path = name + (".mp4" if name in {"video", "cross_source"} else ".jpg")
                    connection.execute(
                        "INSERT INTO file_locations(source_id,relative_path,path_key,asset_id,"
                        "size,mtime_ns,availability) VALUES(?,?,?,?,1,1,?)",
                        (2 if name == "other_source" else 1, path, path, index,
                         "MISSING" if name == "missing" else "PRESENT"),
                    )
            connection.execute(
                "INSERT INTO file_locations(source_id,relative_path,path_key,asset_id,size,mtime_ns,availability) "
                "VALUES(2,'cross_source.jpg','cross_source.jpg',?,1,1,'PRESENT')",
                (ids["cross_source"],),
            )
            connection.execute(
                "INSERT INTO file_locations(source_id,relative_path,path_key,asset_id,size,mtime_ns,availability) "
                "VALUES(1,'unbound.jpg','unbound.jpg',NULL,1,1,'PRESENT')"
            )
        for name, asset_id in ids.items():
            faces[name] = insert_face(
                catalog, asset_id=asset_id, sha256=f"{asset_id:064x}",
                bbox=(0, 0, 50, 50), ordinal=0,
            )
            add_link(
                catalog, faces[name], people["B" if name == "b" else "A"],
                decision="SUGGESTED" if name == "suggested" else "CONFIRMED",
                assignment_kind="MANUAL" if name == "manual" else "AUTO",
            )
        for name, person in (("ab", "B"), ("a", "C"), ("b", "C"), ("detail", "B")):
            extra = insert_face(
                catalog, asset_id=ids[name], sha256=f"{ids[name]:064x}",
                bbox=(60, 0, 110, 50), ordinal=1,
            )
            add_link(catalog, extra, people[person])
            if name == "detail":
                with catalog.write() as connection:
                    connection.execute(
                        "UPDATE faces SET spec_revision=? WHERE face_id=?",
                        (FACE_DETAIL_SPEC_REVISION, extra),
                    )
        set_control(catalog, faces["blocked"], "MANUAL_UNASSIGNED")
        set_control(catalog, faces["manual"], "MANUAL_ASSIGNED")
        set_exclusion(catalog, faces["excluded"], people["A"])
        set_exclusion(catalog, faces["inactive"], people["A"], active=False)
        with catalog.write() as connection:
            for name in ("detail", "manual", "held"):
                connection.execute(
                    "INSERT INTO face_detail_auto_assets VALUES(?,?,?, ?,1,1)",
                    (ids[name], f"{ids[name]:064x}", "detail-result", POLICY_REVISION),
                )
            connection.execute(
                "INSERT INTO face_redetection_groups(old_face_id,asset_id,old_spec_revision,"
                "candidate_state,state,revision,created_at,updated_at) "
                "VALUES(?,?,'old-spec','NO_CANDIDATE','OPEN',1,1,1)",
                (faces["held"], ids["held"]),
            )
            # Hiding a person/card is independent of their confirmed identity.
            connection.execute("INSERT INTO person_hidden(person_id) VALUES(?)", (people["A"],))
            connection.execute("INSERT INTO face_inbox_hidden(face_id) VALUES(?)", (faces["hidden"],))
            connection.execute(
                "INSERT INTO person_collections(collection_id,name,name_key,created_at,updated_at,instance_token) "
                "VALUES(1,'collection','collection',1,1,'11111111111111111111111111111111')"
            )
            connection.execute(
                "INSERT INTO person_collection_members VALUES(1,?)", (people["A"],)
            )
            connection.execute(
                "INSERT INTO person_collection_face_members VALUES(1,?,?,?,1)",
                (faces["b"], ids["b"], f"{ids['b']:064x}"),
            )
        snapshot = SimpleNamespace(db_path=catalog.db_path, ids=ids, faces=faces, people=people)
        catalog.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    assert not Path(f"{snapshot.db_path}-wal").exists()
    original_digest = hashlib.sha256(snapshot.db_path.read_bytes()).digest()
    yield snapshot
    assert hashlib.sha256(snapshot.db_path.read_bytes()).digest() == original_digest


@pytest.fixture()
def library(tmp_path, library_template):
    data_root = tmp_path / "data"
    db_path = catalog_db_path(data_root)
    db_path.parent.mkdir(parents=True)
    shutil.copyfile(library_template.db_path, db_path)
    # Every test keeps the normal compatibility/WAL checks and its own mutable state.
    with Catalog.open(data_root=data_root) as catalog:
        yield SimpleNamespace(
            catalog=catalog, ids=dict(library_template.ids),
            faces=dict(library_template.faces), people=dict(library_template.people),
        )


def checked(library, scope, expected_names):
    candidates = [*library.ids.values(), library.ids["a"], 999999]
    expected = {library.ids[name] for name in expected_names}
    changes = library.catalog.connection.total_changes
    assert _verified_ids(library.catalog, scope, candidates) == expected
    where, parameters = scope_condition(library.catalog.connection, scope)
    assert {
        row[0] for row in library.catalog.read_all(
            "SELECT a.asset_id FROM assets AS a WHERE " + where, parameters
        )
    } == expected
    assert library.catalog.connection.total_changes == changes


A_MEMBERS = {"ab", "a", "manual", "held", "hidden", "missing", "other_source", "inactive", "no_location", "cross_source"}
B_MEMBERS = {"ab", "b", "detail"}


@pytest.mark.parametrize("people,mode,expected", [
    (("A",), "any", A_MEMBERS),
    (("A", "B"), "any", A_MEMBERS | B_MEMBERS),
    (("A", "B"), "all", {"ab"}),
    (("A", "B", "C"), "all", set()),
])
@pytest.mark.parametrize("source,availability", [(None, "any"), (1, "present"), (2, "any"), (None, "missing")])
def test_recheck_all_any_controls_replacement_and_source(library, people, mode, expected, source, availability):
    expected = set(expected)
    if source == 1:
        expected -= {"other_source", "no_location", "cross_source"}
    elif source == 2:
        expected &= {"other_source", "cross_source"}
    if availability == "present":
        expected -= {"missing", "no_location"}
    elif availability == "missing":
        expected &= {"missing", "no_location"}
    checked(library, SearchScope.build(
        person_ids=[library.people[name] for name in people], person_match=mode,
        source_id=source, availability=availability,
    ), expected)


def test_recheck_preserves_independent_album_and_additional_conditions(library):
    fixed_person, fixed_parameters = person_condition(SearchScope.build(person_ids=[library.people["A"]]))
    checked(library, SearchScope.build(
        person_ids=[library.people["B"], library.people["C"]], person_match="any",
        additional_condition_sql=fixed_person, additional_condition_parameters=fixed_parameters,
    ), {"ab", "a"})
    checked(library, SearchScope.build(
        person_ids=[library.people["B"]], collection_id=1,
    ), {"ab", "b"})
    checked(library, SearchScope.build(
        person_ids=[library.people["A"]], album_asset_ids=[library.ids["a"], library.ids["b"]],
        album_membership_sql="a.asset_id >= ?", album_membership_parameters=(library.ids["a"],),
    ), {"a"})


def test_recheck_reads_current_human_state_without_caching(library):
    scope = SearchScope.build(person_ids=[library.people["A"]])
    checked(library, scope, A_MEMBERS)
    set_control(library.catalog, library.faces["a"], "MANUAL_UNASSIGNED")
    set_exclusion(library.catalog, library.faces["inactive"], library.people["A"])
    checked(library, scope, A_MEMBERS - {"a", "inactive"})
    set_control(library.catalog, library.faces["a"], "AUTO_ALLOWED")
    set_exclusion(library.catalog, library.faces["inactive"], library.people["A"], active=False)
    checked(library, scope, A_MEMBERS)
    assert _verified_ids(library.catalog, scope, []) == set()


def test_bounded_plan_probes_candidate_faces_and_locations(library):
    scope = SearchScope.build(person_ids=[library.people["A"]], source_id=1)
    where, parameters = scope_condition(library.catalog.connection, scope, bounded_candidates=True)
    plan = [row[3] for row in library.catalog.read_all(
        "EXPLAIN QUERY PLAN SELECT a.asset_id FROM assets AS a WHERE " + where + " AND a.asset_id IN (?,?)",
        (*parameters, library.ids["a"], library.ids["ab"]),
    )]
    # Protect the measured access path, not a machine-dependent latency threshold.
    assert any("idx_faces_asset (asset_id=?)" in step for step in plan), plan
    assert any("idx_file_locations_asset (asset_id=?)" in step for step in plan), plan
    assert any("ux_face_person_links_effective (face_id=?)" in step for step in plan), plan
    assert not any("idx_face_person_links_person" in step for step in plan), plan


def test_large_recheck_retains_set_based_path(library, monkeypatch):
    from photo_search.search import retrieval

    observed = []
    original = retrieval.scope_condition

    def record(*args, **kwargs):
        observed.append(kwargs["bounded_candidates"])
        return original(*args, **kwargs)

    monkeypatch.setattr(retrieval, "scope_condition", record)
    scope = SearchScope.build(person_ids=[library.people["A"]])
    assert _verified_ids(library.catalog, scope, range(1, 5002)) == {
        library.ids[name] for name in A_MEMBERS
    }
    assert observed == [False]


def test_collection_recheck_preserves_controls_archives_and_availability(library):
    scope = SearchScope.build(collection_id=1)
    expected = A_MEMBERS - {"missing"} | {"b"}
    checked(library, scope, expected)
    with library.catalog.write() as connection:
        # Static archives remain independent of person assignment and face existence.
        connection.execute(
            "INSERT INTO person_collection_face_members VALUES(1,?,?,?,1)",
            ("f" * 64, library.ids["excluded"], f"{library.ids['excluded']:064x}"),
        )
        connection.execute(
            "INSERT INTO person_collection_face_members VALUES(1,?,?,?,1)",
            ("e" * 64, library.ids["suggested"], "0" * 64),
        )
        connection.execute(
            "INSERT INTO person_collection_face_members VALUES(1,?,?,?,1)",
            ("d" * 64, library.ids["missing"], f"{library.ids['missing']:064x}"),
        )
    # A stale input_revision and an offline archived asset still cannot leak in.
    checked(library, scope, expected | {"excluded"})
    checked(library, SearchScope.build(collection_id=1, source_id=1),
            (expected | {"excluded"}) - {"other_source", "no_location", "cross_source"})


def test_collection_recheck_resolves_merge_chains_cycles_and_current_state(library):
    scope = SearchScope.build(collection_id=1)
    with library.catalog.write() as connection:
        connection.execute("UPDATE persons SET merged_into=? WHERE person_id=?",
                           (library.people["B"], library.people["A"]))
        connection.execute("UPDATE persons SET merged_into=? WHERE person_id=?",
                           (library.people["C"], library.people["B"]))
        connection.execute("INSERT INTO person_collection_members VALUES(1,?)", (library.people["B"],))
    checked(library, scope, {"a", "b"})
    with library.catalog.write() as connection:
        connection.execute("UPDATE persons SET merged_into=? WHERE person_id=?",
                           (library.people["A"], library.people["B"]))
    # Cyclic anchors contribute no dynamic members; independent static archives survive.
    checked(library, scope, {"b"})
    with library.catalog.write() as connection:
        connection.execute("UPDATE persons SET merged_into=NULL")
        connection.execute("DELETE FROM person_collection_members WHERE person_id=?", (library.people["B"],))
    checked(library, scope, A_MEMBERS - {"missing"} | {"b"})


def test_collection_recheck_plan_probes_assets_and_builds_identity_list_once(library):
    scope = SearchScope.build(collection_id=1)
    where, parameters = scope_condition(library.catalog.connection, scope, bounded_candidates=True)
    plan = [row[3] for row in library.catalog.read_all(
        "EXPLAIN QUERY PLAN SELECT a.asset_id FROM assets AS a WHERE " + where + " AND a.asset_id IN (?,?)",
        (*parameters, library.ids["a"], library.ids["b"]),
    )]
    assert any("idx_faces_asset (asset_id=?)" in step for step in plan), plan
    assert any("ux_face_person_links_effective (face_id=?)" in step for step in plan), plan
    assert any("idx_person_collection_face_members_asset (collection_id=? AND asset_id=?)" in step for step in plan), plan
    assert any(step.startswith("LIST SUBQUERY") for step in plan), plan
    assert not any("idx_face_person_links_person" in step for step in plan), plan
