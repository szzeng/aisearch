from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from photo_search.api.errors import ApiError
from photo_search.catalog.db import Catalog
from photo_search.photo_smart_collections import (
    SmartRefreshRunner,
    create_collection,
    delete_collection,
    dependency_signature,
    detail,
    exclusions_page,
    list_collections,
    publish_candidates,
    ranked_assets_page,
    rename_collection,
    replace_rule,
    request_refresh,
    rule_from_search,
    session_coverage,
    set_exclusion,
)


@pytest.fixture
def catalog(tmp_path):
    instance = Catalog.open(data_root=tmp_path / "data")
    with instance.write() as connection:
        connection.execute("INSERT INTO source_roots(display_name,root_path,status) VALUES('s','/s','ONLINE')")
        source_id = int(connection.execute("SELECT source_id FROM source_roots WHERE display_name='s'").fetchone()[0])
        for i in range(3):
            digest = hashlib.sha256(f"smart-{i}".encode()).hexdigest()
            connection.execute("INSERT INTO assets(sha256,byte_size,created_at) VALUES(?,?,?)",
                               (digest, 1, 1_700_000_000_000 + i))
        connection.execute(
            "INSERT INTO file_locations(source_id,relative_path,path_key,asset_id,size,mtime_ns,availability) "
            "VALUES(?,?,?,?,?,?, 'PRESENT')", (source_id, "2.jpg", "2.jpg", 2, 1, 1))
    yield instance
    instance.close()


def _rule(kind="FILTER"):
    return {"version": 1, "execution_kind": kind, "request": {"retrieval_mode": "text"},
            "display_query": "", "content_query": None, "confirmed_scope": None,
            "confirmed_place_scope": None, "confirmed_person_scope": None,
            "confirmed_filter_revision": 0, "confirmed_suggestion_revision": 0,
            "confirmed_favorites_revision": None}


def _create(catalog, *, name="Dynamic", request_id="create-smart-a"):
    return create_collection(catalog, name, request_id, _rule(), request_hash=f"hash:{request_id}",
                             auth_hash="auth", source_hash=f"source:{request_id}")


def _signature(catalog, rule=None):
    with catalog.read_snapshot() as connection:
        return dependency_signature(connection, rule or _rule())[0]


def test_smart_collection_rule_is_server_structured_and_mode_is_explicit():
    text_rule = rule_from_search({"mode": "ranked_candidates", "confirmed_execution": {
        "mode": "ranked_candidates", "retrieval_mode": "text", "content_query": None,
        "intent_plan": None, "filters": {},
    }, "payload": {
        "request_id": "r1", "retrieval_mode": "text", "original_query": "合同",
        "page_size": 20,
    }})
    assert text_rule["execution_kind"] == "TEXT"
    assert text_rule["content_query"] == "合同"
    assert text_rule["request"]["text"] == "合同"
    assert "cursor" not in text_rule["request"]
    assert "search_session_id" not in text_rule["request"]

    combined = rule_from_search({"mode": "ranked_candidates", "confirmed_execution": {
        "mode": "ranked_candidates", "retrieval_mode": "combined", "content_query": "海边日落",
        "intent_plan": {"plan_version": "p1", "original_query": "海边日落", "content_query": "海边日落",
                        "person_ids": [], "person_match": "any"}, "filters": {},
    }, "payload": {"request_id": "r2", "retrieval_mode": "combined", "original_query": "海边日落"}})
    assert combined["execution_kind"] == "COMBINED"
    assert combined["request"]["intent_plan"]["content_query"] == "海边日落"

    filter_rule = rule_from_search({"mode": "all_filter_matches", "confirmed_execution": {
        "mode": "all_filter_matches", "retrieval_mode": "combined", "content_query": None,
        "intent_plan": {"plan_version": "p1", "content_query": None, "person_ids": [7], "person_match": "any"},
        "filters": {},
    }, "payload": {"request_id": "r3", "retrieval_mode": "combined", "original_query": "人物七"}})
    assert filter_rule["execution_kind"] == "FILTER"
    assert filter_rule["content_query"] is None
    assert filter_rule["request"]["intent_plan"]["person_ids"] == [7]
    with pytest.raises(ApiError):
        rule_from_search({"mode": "ranked_candidates", "payload": {"retrieval_mode": "text"}})


def test_smart_collection_create_idempotent_rename_delete_and_directory_cursor(catalog):
    made = _create(catalog)
    smart = made["collection"]
    assert made["reused"] is False
    replay = create_collection(catalog, "Dynamic", "create-smart-a", _rule(),
                               request_hash="hash:create-smart-a", auth_hash="auth",
                               source_hash="source:create-smart-a")
    assert replay["reused"] is True
    with pytest.raises(ApiError):
        create_collection(catalog, "Other", "create-smart-a", _rule(),
                          request_hash="changed", auth_hash="auth", source_hash="source:create-smart-a")
    renamed = rename_collection(catalog, smart["smart_id"], instance_token=smart["instance_token"],
                                expected_revision=1, name="Renamed")
    assert renamed["collection"]["revision"] == 2
    assert list_collections(catalog)["collections"][0]["name"] == "Renamed"
    with pytest.raises(ApiError):
        rename_collection(catalog, smart["smart_id"], instance_token=smart["instance_token"],
                          expected_revision=1, name="Stale")
    assert delete_collection(catalog, smart["smart_id"], instance_token=smart["instance_token"],
                             expected_revision=2)["deleted"] is True
    assert catalog.read_one("SELECT COUNT(*) FROM assets")[0] == 3
    assert list_collections(catalog)["collections"] == []


def test_rank_cache_is_bounded_atomic_and_rename_or_exclusion_does_not_rebuild(catalog):
    smart = _create(catalog)["collection"]
    smart_id, token = smart["smart_id"], smart["instance_token"]
    published = publish_candidates(catalog, smart_id, [1, 2, 3], signature=_signature(catalog))
    assert published["candidate_count"] == 3
    before = detail(catalog, smart_id, instance_token=token)
    assert before["collection"]["freshness"] == "CURRENT"
    rename_collection(catalog, smart_id, instance_token=token, expected_revision=1, name="Renamed")
    excluded = set_exclusion(catalog, smart_id, 2, instance_token=token,
                             expected_revision=2, excluded=True)
    assert excluded["exclusions_revision"] == 1
    assert catalog.read_one("SELECT build_id FROM photo_smart_cache_state WHERE smart_id=?", (smart_id,))[0] == published["build_id"]
    assert set_exclusion(catalog, smart_id, 2, instance_token=token,
                         expected_revision=3, excluded=True)["changed"] is False
    page = exclusions_page(catalog, smart_id, instance_token=token)
    assert [item["asset_id"] for item in page["assets"]] == [2]
    assert page["assets"][0]["thumbnail_url"] == "/assets/2/thumbnail"
    assert set_exclusion(catalog, smart_id, 2, instance_token=token,
                         expected_revision=3, excluded=False)["changed"] is True
    assert exclusions_page(catalog, smart_id, instance_token=token)["assets"] == []
    assert catalog.read_one("SELECT COUNT(*) FROM photo_smart_cache_entries WHERE smart_id=?", (smart_id,))[0] == 3


def test_rank_cache_rejects_more_than_candidate_cap_without_destroying_old_build(catalog):
    smart = _create(catalog)["collection"]
    published = publish_candidates(catalog, smart["smart_id"], [1, 2], signature=_signature(catalog))
    with pytest.raises(ApiError):
        publish_candidates(catalog, smart["smart_id"], list(range(1, 5002)), signature="sig-b")
    state = catalog.read_one("SELECT build_id FROM photo_smart_cache_state WHERE smart_id=?",
                             (smart["smart_id"],))
    assert state["build_id"] == published["build_id"]
    assert catalog.read_one("SELECT COUNT(*) FROM photo_smart_cache_entries WHERE smart_id=?",
                            (smart["smart_id"],))[0] == 2


def test_ranked_capture_order_uses_trusted_time_bucket_and_keyset_ties(catalog):
    from photo_search.api.queries import PREVIEW_SPEC, THUMBNAIL_SPEC

    smart = _create(catalog)["collection"]
    with catalog.write() as connection:
        connection.executemany(
            "INSERT INTO asset_metadata(asset_id,taken_at_sort,time_source,time_confidence) VALUES(?,?,?,?)",
            [(1, 900, "EXIF_ROOT", "LOW"), (2, 100, "EXIF_ORIGINAL", "HIGH"),
             (3, None, "FILE_MTIME", "LOW")],
        )
    signature = _signature(catalog)
    publish_candidates(catalog, smart["smart_id"], [1, 2, 3], signature=signature)

    class Cache:
        @staticmethod
        def artifact_path(*, spec, sha256):
            assert spec in (PREVIEW_SPEC, THUMBNAIL_SPEC)
            return sha256

        @staticmethod
        def is_published(_path):
            return False

    first = ranked_assets_page(catalog, Cache(), smart["smart_id"],
                               instance_token=smart["instance_token"], limit=1, order="capture_desc")
    second = ranked_assets_page(catalog, Cache(), smart["smart_id"],
                                instance_token=smart["instance_token"], limit=2,
                                cursor=first["next_cursor"], order="capture_desc")
    assert [asset["asset_id"] for asset in first["assets"] + second["assets"]] == [2, 3, 1]


def test_place_suggestion_and_package_revisions_invalidate_rule_signature(catalog):
    rule = _rule()
    before = _signature(catalog, rule)
    with catalog.write() as connection:
        connection.execute("UPDATE place_suggestion_revision SET revision=revision+1 WHERE revision_id=1")
    after_suggestions = _signature(catalog, rule)
    assert after_suggestions != before
    with catalog.write() as connection:
        connection.execute("UPDATE active_geo_dataset SET revision=revision+1 WHERE singleton=1")
    after_package = _signature(catalog, rule)
    assert after_package != after_suggestions


def test_search_tokenizer_contract_invalidates_ranked_signature(catalog, monkeypatch):
    from photo_search.search import tokens

    rule = _rule("TEXT")
    before = _signature(catalog, rule)
    monkeypatch.setattr(tokens, "TOKENIZER_VERSION", tokens.TOKENIZER_VERSION + 1)
    assert _signature(catalog, rule) != before


def test_ranked_coverage_uses_session_projection_and_channel_flags():
    from types import SimpleNamespace

    session = SimpleNamespace(
        candidates_truncated=False,
        channels={"fts": {"truncated": False}, "vector": {"truncated": True}},
        pending_semantic=None,
        search_projection={"state": "LAGGING"},
    )
    assert session_coverage(session) == (True, True)


def test_refresh_lease_is_single_flight_and_expired_owner_cannot_publish(catalog):
    rule = _rule("TEXT")
    smart = create_collection(catalog, "Text rule", "text-create", rule, request_hash="h",
                              auth_hash="a", source_hash="s")["collection"]
    first = request_refresh(catalog, smart["smart_id"], instance_token=smart["instance_token"])
    assert first["status"] == "QUEUED"
    second = request_refresh(catalog, smart["smart_id"], instance_token=smart["instance_token"])
    assert second["status"] == "RUNNING"
    with catalog.write() as connection:
        connection.execute("UPDATE photo_smart_cache_state SET lease_until_ms=0 WHERE smart_id=?",
                           (smart["smart_id"],))
    third = request_refresh(catalog, smart["smart_id"], instance_token=smart["instance_token"])
    assert third["status"] == "QUEUED"
    with pytest.raises(ApiError) as error:
            publish_candidates(catalog, smart["smart_id"], [1], signature=_signature(catalog, rule),
                           expected_rule_revision=1, lease_owner=first["lease_owner"])
    assert error.value.code == "PHOTO_SMART_LEASE_EXPIRED"
    assert catalog.read_one("SELECT build_id FROM photo_smart_cache_state WHERE smart_id=?",
                            (smart["smart_id"],))[0] is None


def test_publish_rejects_expired_lease_even_before_another_worker_takes_over(catalog):
    rule = _rule("TEXT")
    smart = create_collection(catalog, "Expired publish", "text-create-expired", rule, request_hash="h2",
                              auth_hash="a", source_hash="s2")["collection"]
    claim = request_refresh(catalog, smart["smart_id"], instance_token=smart["instance_token"])
    with catalog.write() as connection:
        connection.execute("UPDATE photo_smart_cache_state SET lease_until_ms=? WHERE smart_id=?",
                           (catalog.now_ms() - 1, smart["smart_id"]))
    with pytest.raises(ApiError) as error:
        publish_candidates(catalog, smart["smart_id"], [1], signature=_signature(catalog, rule),
                           expected_rule_revision=claim["rule_revision"], lease_owner=claim["lease_owner"])
    assert error.value.code == "PHOTO_SMART_LEASE_EXPIRED"


def test_replaced_rule_rejects_old_worker_publish_and_preserves_previous_build(catalog):
    rule = _rule("TEXT")
    smart = create_collection(catalog, "Replaced rule", "text-create-replaced", rule, request_hash="hr",
                              auth_hash="a", source_hash="sr")["collection"]
    previous = publish_candidates(catalog, smart["smart_id"], [1], signature=_signature(catalog, rule))
    claim = request_refresh(catalog, smart["smart_id"], instance_token=smart["instance_token"], manual=True)
    next_rule = {**rule, "display_query": "another query", "content_query": "another query"}
    replace_rule(catalog, smart["smart_id"], instance_token=smart["instance_token"], expected_revision=1,
                 rule=next_rule, request_id="replace-rule-after-claim", auth_hash="a", request_hash="rr")
    with pytest.raises(ApiError) as error:
        publish_candidates(catalog, smart["smart_id"], [2], signature=_signature(catalog, next_rule),
                           expected_rule_revision=claim["rule_revision"], lease_owner=claim["lease_owner"])
    assert error.value.code == "PHOTO_SMART_RULE_CHANGED"
    state = catalog.read_one("SELECT build_id FROM photo_smart_cache_state WHERE smart_id=?",
                             (smart["smart_id"],))
    assert state["build_id"] == previous["build_id"]


def test_refresh_lease_recovers_after_catalog_reopen_and_rejects_pre_restart_owner(catalog, tmp_path):
    rule = _rule("TEXT")
    smart = create_collection(catalog, "Restart recovery", "restart-smart", rule,
                              request_hash="restart", auth_hash="auth", source_hash="source")["collection"]
    previous = publish_candidates(catalog, smart["smart_id"], [1], signature=_signature(catalog, rule))
    old_claim = request_refresh(catalog, smart["smart_id"], instance_token=smart["instance_token"], manual=True)
    reopened = Catalog.open(data_root=tmp_path / "data")
    try:
        still_owned = request_refresh(reopened, smart["smart_id"], instance_token=smart["instance_token"])
        assert still_owned["status"] == "RUNNING"
        with reopened.write() as connection:
            connection.execute("UPDATE photo_smart_cache_state SET lease_until_ms=0 WHERE smart_id=?",
                               (smart["smart_id"],))
        recovered = request_refresh(reopened, smart["smart_id"], instance_token=smart["instance_token"])
        assert recovered["status"] == "QUEUED"
        with pytest.raises(ApiError) as error:
            publish_candidates(reopened, smart["smart_id"], [2], signature=_signature(reopened, rule),
                               expected_rule_revision=old_claim["rule_revision"],
                               lease_owner=old_claim["lease_owner"])
        assert error.value.code == "PHOTO_SMART_LEASE_EXPIRED"
        fresh = publish_candidates(reopened, smart["smart_id"], [3], signature=_signature(reopened, rule),
                                   expected_rule_revision=recovered["rule_revision"],
                                   lease_owner=recovered["lease_owner"])
        assert fresh["build_id"] != previous["build_id"]
        assert reopened.read_all("SELECT asset_id FROM photo_smart_cache_entries WHERE smart_id=? AND build_id=?",
                                 (smart["smart_id"], fresh["build_id"]))[0][0] == 3
    finally:
        reopened.close()


def test_refresh_factory_failure_releases_only_its_lease_and_keeps_old_cache(catalog):
    rule = _rule("TEXT")
    smart = create_collection(catalog, "Factory failure", "text-create-factory", rule, request_hash="hf",
                              auth_hash="a", source_hash="sf")["collection"]
    published = publish_candidates(catalog, smart["smart_id"], [1], signature=_signature(catalog, rule))
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO photo_smart_exclusions(smart_id,asset_id,created_at_ms) VALUES(?,?,1)",
            (smart["smart_id"], 1),
        )
        connection.execute(
            "UPDATE photo_smart_collections SET exclusions_revision=exclusions_revision+1 WHERE smart_id=?",
            (smart["smart_id"],),
        )
    rule_before = catalog.read_one("SELECT rule_json FROM photo_smart_collections WHERE smart_id=?",
                                   (smart["smart_id"],))[0]
    claim = request_refresh(catalog, smart["smart_id"], instance_token=smart["instance_token"], manual=True)

    class Context:
        _service_factory = staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("factory unavailable")))

        @staticmethod
        def service():
            return SimpleNamespace(catalog=catalog)

    runner = SmartRefreshRunner(Context())
    try:
        runner._run(smart["smart_id"], claim["lease_owner"], claim["rule_revision"])
    finally:
        runner.close()
    state = catalog.read_one("SELECT build_id,lease_owner,refresh_status FROM photo_smart_cache_state WHERE smart_id=?",
                             (smart["smart_id"],))
    assert state["build_id"] == published["build_id"]
    assert state["lease_owner"] is None
    assert state["refresh_status"] == "FAILED"
    assert catalog.read_one("SELECT COUNT(*) FROM photo_smart_exclusions WHERE smart_id=?",
                            (smart["smart_id"],))[0] == 1
    assert catalog.read_one("SELECT rule_json FROM photo_smart_collections WHERE smart_id=?",
                            (smart["smart_id"],))[0] == rule_before
