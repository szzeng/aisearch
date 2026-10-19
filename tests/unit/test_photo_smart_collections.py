from __future__ import annotations

import hashlib
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from photo_search.api.errors import ApiError
from photo_search.catalog.db import Catalog
from photo_search.photo_smart_collections import (
    SmartRefreshRunner,
    claim_refresh_lease,
    create_collection,
    delete_collection,
    dependency_signature,
    detail,
    exclusions_page,
    list_collections,
    publish_candidates,
    ranked_assets_page,
    recover_interrupted_refreshes,
    rename_collection,
    renew_refresh_lease,
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
    assert second["status"] == "QUEUED"
    assert second["refresh_id"] == first["refresh_id"]
    started = claim_refresh_lease(catalog, smart["smart_id"], refresh_id=first["refresh_id"],
                                  rule_revision=first["rule_revision"])
    assert started is not None
    running = request_refresh(catalog, smart["smart_id"], instance_token=smart["instance_token"])
    assert running["status"] == "RUNNING"
    with catalog.write() as connection:
        connection.execute("UPDATE photo_smart_cache_state SET lease_until_ms=0 WHERE smart_id=?",
                           (smart["smart_id"],))
    third = request_refresh(catalog, smart["smart_id"], instance_token=smart["instance_token"])
    assert third["status"] == "QUEUED"
    assert third["refresh_id"] != first["refresh_id"]
    assert not renew_refresh_lease(catalog, smart["smart_id"], owner=started["lease_owner"],
                                   refresh_id=first["refresh_id"], rule_revision=first["rule_revision"])
    with pytest.raises(ApiError) as error:
            publish_candidates(catalog, smart["smart_id"], [1], signature=_signature(catalog, rule),
                           expected_rule_revision=1, lease_owner=started["lease_owner"],
                           refresh_id=first["refresh_id"])
    assert error.value.code == "PHOTO_SMART_LEASE_EXPIRED"
    assert catalog.read_one("SELECT build_id FROM photo_smart_cache_state WHERE smart_id=?",
                            (smart["smart_id"],))[0] is None


def test_combined_missing_active_profile_is_reported_and_refresh_is_deferred(catalog):
    rule = {**_rule("COMBINED"), "search_binding": {
        "profile_id": "pinned-profile", "space_id": "space-a",
        "image_generation_id": "image-a", "dimension": 3,
    }}
    smart = create_collection(catalog, "Unavailable model", "missing-profile", rule,
                              request_hash="missing-profile", auth_hash="auth",
                              source_hash="source")['collection']
    published = publish_candidates(catalog, smart["smart_id"], [1, 2],
                                   signature=_signature(catalog, rule))
    with catalog.read_snapshot() as connection:
        current_signature, indexing, current_profile = dependency_signature(connection, rule)
    assert isinstance(indexing, bool)
    assert current_profile is None
    assert current_signature == catalog.read_one(
        "SELECT dependency_signature FROM photo_smart_cache_state WHERE smart_id=?",
        (smart["smart_id"],),
    )[0]

    current = detail(catalog, smart["smart_id"], instance_token=smart["instance_token"])
    assert current["collection"]["freshness"] == "MODEL_REBIND_REQUIRED"
    refresh = request_refresh(catalog, smart["smart_id"], instance_token=smart["instance_token"])
    assert refresh == {"status": "DEFERRED", "refresh_id": None,
                       "reason": "MODEL_REBIND_REQUIRED", "cached": True}
    state = catalog.read_one(
        "SELECT build_id,lease_owner,refresh_status,last_error_code "
        "FROM photo_smart_cache_state WHERE smart_id=?", (smart["smart_id"],),
    )
    assert state["build_id"] == published["build_id"]
    assert state["lease_owner"] is None
    assert state["refresh_status"] == "FAILED"
    assert state["last_error_code"] == "MODEL_REBIND_REQUIRED"


def test_combined_cache_invalidates_when_pinned_vector_generation_publishes(catalog):
    rule = {**_rule("COMBINED"), "search_binding": {
        "profile_id": "pinned-profile", "space_id": "space-a",
        "image_generation_id": "image-a", "dimension": 3,
    }}
    smart = create_collection(catalog, "Vector revision", "vector-revision", rule,
                              request_hash="vector-revision", auth_hash="a", source_hash="v1")['collection']
    signature_before = _signature(catalog, rule)
    published = publish_candidates(catalog, smart["smart_id"], [1], signature=signature_before)
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO photo_smart_vector_publish_revisions(generation_id,revision) VALUES('image-a',1)"
        )

    assert _signature(catalog, rule) != signature_before
    assert catalog.read_one("SELECT build_id FROM photo_smart_cache_state WHERE smart_id=?",
                            (smart["smart_id"],))[0] == published["build_id"]


def test_publish_rejects_expired_lease_even_before_another_worker_takes_over(catalog):
    rule = _rule("TEXT")
    smart = create_collection(catalog, "Expired publish", "text-create-expired", rule, request_hash="h2",
                              auth_hash="a", source_hash="s2")["collection"]
    claim = request_refresh(catalog, smart["smart_id"], instance_token=smart["instance_token"])
    lease = claim_refresh_lease(catalog, smart["smart_id"], refresh_id=claim["refresh_id"],
                                rule_revision=claim["rule_revision"])
    claim.update(lease)
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
    lease = claim_refresh_lease(catalog, smart["smart_id"], refresh_id=claim["refresh_id"],
                                rule_revision=claim["rule_revision"])
    claim.update(lease)
    next_rule = {**rule, "display_query": "another query", "content_query": "another query"}
    replace_rule(catalog, smart["smart_id"], instance_token=smart["instance_token"], expected_revision=1,
                 rule=next_rule, request_id="replace-rule-after-claim", auth_hash="a", request_hash="rr")
    assert not renew_refresh_lease(catalog, smart["smart_id"], owner=claim["lease_owner"],
                                   refresh_id=claim["refresh_id"], rule_revision=claim["rule_revision"])
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
    old_lease = claim_refresh_lease(catalog, smart["smart_id"], refresh_id=old_claim["refresh_id"],
                                    rule_revision=old_claim["rule_revision"])
    old_claim.update(old_lease)
    reopened = Catalog.open(data_root=tmp_path / "data")
    try:
        still_owned = request_refresh(reopened, smart["smart_id"], instance_token=smart["instance_token"])
        assert still_owned["status"] == "RUNNING"
        assert recover_interrupted_refreshes(reopened) == 1
        recovered = request_refresh(reopened, smart["smart_id"], instance_token=smart["instance_token"])
        assert recovered["status"] == "QUEUED"
        recovered_lease = claim_refresh_lease(reopened, smart["smart_id"],
            refresh_id=recovered["refresh_id"], rule_revision=recovered["rule_revision"])
        recovered.update(recovered_lease)
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
        runner._run(smart["smart_id"], claim["refresh_id"], claim["rule_revision"])
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


def test_refresh_runner_bounds_queue_and_deduplicates_ids(catalog, monkeypatch):
    created = [create_collection(catalog, f"Queued {i}", f"queued-{i}", _rule("TEXT"),
                                 request_hash=f"queued-{i}", auth_hash="a", source_hash=f"s-{i}")["collection"]
               for i in range(4)]
    context = SimpleNamespace(service=lambda: SimpleNamespace(catalog=catalog),
                              _service_factory=lambda: SimpleNamespace(catalog=catalog))
    runner = SmartRefreshRunner(context)
    entered = threading.Event()
    release = threading.Event()
    active_lock = threading.Lock()
    active = 0
    max_active = 0
    started: list[int] = []

    def blocked_run(smart_id: int, refresh_id: str, rule_revision: int) -> None:
        nonlocal active, max_active
        with runner._lock:
            runner._tasks[smart_id]["status"] = "RUNNING"
        with active_lock:
            active += 1
            max_active = max(max_active, active)
            started.append(smart_id)
        entered.set()
        assert release.wait(5)
        with active_lock:
            active -= 1

    monkeypatch.setattr(runner, "_run", blocked_run)
    try:
        first = runner.submit(created[0]["smart_id"], instance_token=created[0]["instance_token"], manual=True)
        assert first["status"] == "QUEUED"
        assert entered.wait(2)
        duplicate = runner.submit(created[0]["smart_id"], instance_token=created[0]["instance_token"], manual=True)
        assert duplicate["status"] == "RUNNING"
        assert duplicate["refresh_id"] == first["refresh_id"]
        with pytest.raises(ApiError) as unauthorized_duplicate:
            runner.submit(created[0]["smart_id"], instance_token="wrong-instance", manual=True)
        assert unauthorized_duplicate.value.code == "PHOTO_SMART_COLLECTION_STALE"
        second = runner.submit(created[1]["smart_id"], instance_token=created[1]["instance_token"], manual=True)
        third = runner.submit(created[2]["smart_id"], instance_token=created[2]["instance_token"], manual=True)
        busy = runner.submit(created[3]["smart_id"], instance_token=created[3]["instance_token"], manual=True)
        assert second["status"] == third["status"] == "QUEUED"
        assert busy == {"status": "BUSY", "retryable": True,
                        "queue_limit": 3, "queue_depth": 3}
        with pytest.raises(ApiError) as unauthorized_busy:
            runner.submit(created[3]["smart_id"], instance_token="wrong-instance", manual=True)
        assert unauthorized_busy.value.code == "PHOTO_SMART_COLLECTION_STALE"
        assert catalog.read_one("SELECT refresh_status FROM photo_smart_cache_state WHERE smart_id=?",
                                (created[3]["smart_id"],))[0] == "NO_CACHE"
    finally:
        release.set()
        for _ in range(500):
            if len(started) >= 3:
                break
            threading.Event().wait(0.01)
        runner.close()
    assert max_active == 1
    assert len(started) == 3


def test_refresh_runner_immediate_future_callback_does_not_deadlock(catalog, monkeypatch):
    smart = create_collection(catalog, "Immediate", "immediate", _rule("TEXT"),
                              request_hash="immediate", auth_hash="a", source_hash="s")["collection"]
    context = SimpleNamespace(service=lambda: SimpleNamespace(catalog=catalog),
                              _service_factory=lambda: SimpleNamespace(catalog=catalog))
    runner = SmartRefreshRunner(context)
    monkeypatch.setattr(runner, "_run", lambda *_args: None)
    try:
        result = runner.submit(smart["smart_id"], instance_token=smart["instance_token"], manual=True)
        assert result["status"] == "QUEUED"
        for _ in range(100):
            with runner._lock:
                if not runner._tasks:
                    break
            threading.Event().wait(0.01)
        with runner._lock:
            assert not runner._tasks
    finally:
        runner.close()


def test_refresh_submission_failure_releases_slot_and_can_retry(catalog, monkeypatch):
    smart = create_collection(catalog, "Submit failure", "submit-failure", _rule("TEXT"),
                              request_hash="submit-failure", auth_hash="a", source_hash="s")["collection"]
    context = SimpleNamespace(service=lambda: SimpleNamespace(catalog=catalog),
                              _service_factory=lambda: SimpleNamespace(catalog=catalog))
    runner = SmartRefreshRunner(context)
    real_executor = runner._executor

    class FailedExecutor:
        @staticmethod
        def submit(*_args, **_kwargs):
            raise RuntimeError("executor unavailable")

        @staticmethod
        def shutdown(**_kwargs):
            return None

    runner._executor = FailedExecutor()
    with pytest.raises(RuntimeError, match="executor unavailable"):
        runner.submit(smart["smart_id"], instance_token=smart["instance_token"], manual=True)
    assert tuple(catalog.read_one("SELECT refresh_status,refresh_request_id,lease_owner FROM photo_smart_cache_state WHERE smart_id=?",
                                  (smart["smart_id"],))) == ("FAILED", None, None)
    with runner._lock:
        assert not runner._tasks
    runner._executor = real_executor
    monkeypatch.setattr(runner, "_run", lambda *_args: None)
    try:
        retried = runner.submit(smart["smart_id"], instance_token=smart["instance_token"], manual=True)
        assert retried["status"] == "QUEUED"
    finally:
        runner.close()


def test_active_refresh_renews_lease_during_long_search(catalog, monkeypatch):
    import photo_search.photo_smart_collections as smart_module
    from photo_search.api import routes

    rule = {**_rule("TEXT"), "request": {"retrieval_mode": "text", "original_query": "合同"}}
    smart = create_collection(catalog, "Heartbeat", "heartbeat", rule,
                              request_hash="heartbeat", auth_hash="a", source_hash="s")["collection"]
    previous = publish_candidates(catalog, smart["smart_id"], [1], signature=_signature(catalog, rule))
    monkeypatch.setattr(smart_module, "SMART_REFRESH_LEASE_MS", 100)
    monkeypatch.setattr(smart_module, "SMART_REFRESH_HEARTBEAT_SECONDS", 0.02)
    queued = request_refresh(catalog, smart["smart_id"], instance_token=smart["instance_token"], manual=True)
    entered = threading.Event()
    release = threading.Event()
    session = SimpleNamespace(
        entries=[SimpleNamespace(asset_id=1)], channels={}, search_projection={"state": "SYNCED"},
        pending_semantic=None, candidates_truncated=False,
    )

    class Service:
        def __init__(self):
            self.catalog = Catalog.open(data_root=catalog.data_root)
            self.data_root = self.catalog.data_root

        def close(self):
            self.catalog.close()

    context = SimpleNamespace(
        service=lambda: SimpleNamespace(catalog=catalog),
        _service_factory=Service,
        search_sessions=SimpleNamespace(get=lambda _session_id: session),
    )
    monkeypatch.setattr(routes, "_place_aware_search_worker",
                        lambda *_args, **_kwargs: (entered.set(), release.wait(5),
                                                  {"search_session_id": "heartbeat-session"})[-1])
    runner = SmartRefreshRunner(context)
    try:
        submitted = runner.submit(smart["smart_id"], instance_token=smart["instance_token"], manual=True)
        assert submitted["refresh_id"] == queued["refresh_id"]
        assert entered.wait(2)
        threading.Event().wait(0.24)
        state = catalog.read_one(
            "SELECT lease_until_ms,lease_owner,refresh_status FROM photo_smart_cache_state WHERE smart_id=?",
            (smart["smart_id"],),
        )
        assert state["refresh_status"] == "RUNNING"
        assert state["lease_until_ms"] > catalog.now_ms() + 40
    finally:
        release.set()
        runner.close()
    result = catalog.read_one(
        "SELECT build_id,lease_owner,refresh_status FROM photo_smart_cache_state WHERE smart_id=?",
        (smart["smart_id"],),
    )
    assert result["build_id"] != previous["build_id"]
    assert result["lease_owner"] is None
    assert result["refresh_status"] == "NO_CACHE"


def test_failed_search_releases_lease_keeps_cache_and_allows_retry(catalog, monkeypatch):
    from photo_search.api import routes

    rule = {**_rule("TEXT"), "request": {"retrieval_mode": "text", "original_query": "合同"}}
    smart = create_collection(catalog, "Search error", "search-error", rule,
                              request_hash="search-error", auth_hash="a", source_hash="s")["collection"]
    previous = publish_candidates(catalog, smart["smart_id"], [1], signature=_signature(catalog, rule))
    queued = request_refresh(catalog, smart["smart_id"], instance_token=smart["instance_token"], manual=True)

    class Service:
        def __init__(self):
            self.catalog = Catalog.open(data_root=catalog.data_root)
            self.data_root = self.catalog.data_root

        def close(self):
            self.catalog.close()

    def fail_search(*_args, **_kwargs):
        raise RuntimeError("synthetic retrieval failure")

    monkeypatch.setattr(routes, "_place_aware_search_worker", fail_search)
    context = SimpleNamespace(service=lambda: SimpleNamespace(catalog=catalog),
                              _service_factory=Service, search_sessions=SimpleNamespace(get=lambda _sid: None))
    runner = SmartRefreshRunner(context)
    try:
        submitted = runner.submit(smart["smart_id"], instance_token=smart["instance_token"], manual=True)
        assert submitted["refresh_id"] == queued["refresh_id"]
        with runner._lock:
            future = runner._tasks[smart["smart_id"]]["future"]
        future.result(timeout=5)
    finally:
        runner.close()
    state = catalog.read_one(
        "SELECT build_id,lease_owner,lease_until_ms,refresh_status,last_error_code "
        "FROM photo_smart_cache_state WHERE smart_id=?", (smart["smart_id"],),
    )
    assert state["build_id"] == previous["build_id"]
    assert state["lease_owner"] is None and state["lease_until_ms"] is None
    assert state["refresh_status"] == "FAILED"
    assert state["last_error_code"] == "PHOTO_SMART_REFRESH_FAILED"
    retry = request_refresh(catalog, smart["smart_id"], instance_token=smart["instance_token"], manual=True)
    assert retry["status"] == "QUEUED"
    assert retry["refresh_id"] != queued["refresh_id"]


def test_rule_replacement_during_running_refresh_releases_stale_lease(catalog, monkeypatch):
    from photo_search.api import routes

    rule = {**_rule("TEXT"), "request": {"retrieval_mode": "text", "original_query": "合同"}}
    smart = create_collection(catalog, "Replace during refresh", "replace-during-refresh", rule,
                              request_hash="replace-during-refresh", auth_hash="a",
                              source_hash="replace-during-refresh")['collection']
    previous = publish_candidates(catalog, smart["smart_id"], [1], signature=_signature(catalog, rule))
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO photo_smart_exclusions(smart_id,asset_id,created_at_ms) VALUES(?,2,1)",
            (smart["smart_id"],),
        )

    class Service:
        def __init__(self):
            self.catalog = Catalog.open(data_root=catalog.data_root)
            self.data_root = self.catalog.data_root

        def close(self):
            self.catalog.close()

    entered = threading.Event()
    release = threading.Event()
    session = SimpleNamespace(
        entries=[SimpleNamespace(asset_id=3)], channels={}, search_projection={"state": "SYNCED"},
        pending_semantic=None, candidates_truncated=False,
    )
    monkeypatch.setattr(routes, "_place_aware_search_worker",
                        lambda *_args, **_kwargs: (entered.set(), release.wait(5),
                                                  {"search_session_id": "replaced-session"})[-1])
    context = SimpleNamespace(
        service=lambda: SimpleNamespace(catalog=catalog), _service_factory=Service,
        search_sessions=SimpleNamespace(get=lambda _session_id: session),
    )
    runner = SmartRefreshRunner(context)
    try:
        submitted = runner.submit(smart["smart_id"], instance_token=smart["instance_token"], manual=True)
        assert submitted["status"] == "QUEUED"
        assert entered.wait(2)
        next_rule = {**rule, "display_query": "new confirmed rule"}
        replace_rule(catalog, smart["smart_id"], instance_token=smart["instance_token"],
                     expected_revision=1, rule=next_rule, request_id="replace-while-running",
                     auth_hash="a", request_hash="replace-while-running")
        release.set()
        with runner._lock:
            future = runner._tasks[smart["smart_id"]]["future"]
        future.result(timeout=5)
        for _ in range(100):
            with runner._lock:
                if smart["smart_id"] not in runner._tasks:
                    break
            threading.Event().wait(0.01)
    finally:
        release.set()
        runner.close()

    state = catalog.read_one(
        "SELECT build_id,lease_owner,lease_until_ms,refresh_status,last_error_code "
        "FROM photo_smart_cache_state WHERE smart_id=?", (smart["smart_id"],),
    )
    assert state["build_id"] == previous["build_id"]
    assert state["lease_owner"] is None and state["lease_until_ms"] is None
    assert state["refresh_status"] == "FAILED"
    assert state["last_error_code"] == "PHOTO_SMART_RULE_CHANGED"
    assert catalog.read_one("SELECT COUNT(*) FROM photo_smart_exclusions WHERE smart_id=?",
                            (smart["smart_id"],))[0] == 1
    retry = request_refresh(catalog, smart["smart_id"], instance_token=smart["instance_token"], manual=True)
    assert retry["status"] == "QUEUED"


def test_refresh_close_cancels_waiting_tasks_without_deadlock(catalog, monkeypatch):
    created = [create_collection(catalog, f"Close {i}", f"close-{i}", _rule("TEXT"),
                                 request_hash=f"close-{i}", auth_hash="a", source_hash=f"c-{i}")["collection"]
               for i in range(3)]
    context = SimpleNamespace(service=lambda: SimpleNamespace(catalog=catalog),
                              _service_factory=lambda: SimpleNamespace(catalog=catalog))
    runner = SmartRefreshRunner(context)
    entered = threading.Event()
    release = threading.Event()

    def blocked_run(_smart_id: int, _refresh_id: str, _revision: int) -> None:
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(runner, "_run", blocked_run)
    for item in created:
        assert runner.submit(item["smart_id"], instance_token=item["instance_token"], manual=True)["status"] == "QUEUED"
    assert entered.wait(2)
    threading.Timer(0.1, release.set).start()
    runner.close()
    statuses = [tuple(catalog.read_one(
        "SELECT refresh_status,refresh_request_id FROM photo_smart_cache_state WHERE smart_id=?", (item["smart_id"],)
    )) for item in created[1:]]
    assert statuses == [("FAILED", None), ("FAILED", None)]


def test_app_startup_recovers_crashed_refresh_after_os_lease_release(catalog, monkeypatch):
    import asyncio

    from photo_search.api.settings import ApiSettings
    from photo_search.app import create_app

    smart = create_collection(catalog, "Crash recovery", "crash-recovery", _rule("TEXT"),
                              request_hash="crash-recovery", auth_hash="a", source_hash="s")["collection"]
    published = publish_candidates(catalog, smart["smart_id"], [1], signature=_signature(catalog, _rule("TEXT")))
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO photo_smart_exclusions(smart_id,asset_id,created_at_ms) VALUES(?,1,1)",
            (smart["smart_id"],),
        )
    rule_before = catalog.read_one("SELECT rule_json FROM photo_smart_collections WHERE smart_id=?",
                                   (smart["smart_id"],))[0]
    data_root = catalog.data_root
    catalog.close()
    script = """
import os, sys
from photo_search.catalog.db import Catalog
from photo_search.photo_smart_collections import claim_refresh_lease, request_refresh
from photo_search.runtime.data_root_lease import acquire_data_root_lease
root, smart_id, instance_token = sys.argv[1], int(sys.argv[2]), sys.argv[3]
lease = acquire_data_root_lease(root)
catalog = Catalog.open(data_root=root)
queued = request_refresh(catalog, smart_id, instance_token=instance_token, manual=True)
assert queued['status'] == 'QUEUED'
assert claim_refresh_lease(catalog, smart_id, refresh_id=queued['refresh_id'],
                           rule_revision=queued['rule_revision']) is not None
os._exit(23)
"""
    child = subprocess.run([sys.executable, "-c", script, str(data_root), str(smart["smart_id"]),
                            str(smart["instance_token"])], capture_output=True, timeout=30)
    assert child.returncode == 23, child.stderr.decode(errors="replace")

    app = create_app(settings=ApiSettings.create(data_root=data_root))
    monkeypatch.setattr(app.state.api, "start_place_cover_warming", lambda: None)

    async def start_application():
        async with app.router.lifespan_context(app):
            pass

    asyncio.run(start_application())
    recovered_catalog = Catalog.open(data_root=data_root)
    try:
        state = recovered_catalog.read_one(
            "SELECT build_id,refresh_status,refresh_request_id,lease_owner,lease_until_ms,last_error_code "
            "FROM photo_smart_cache_state WHERE smart_id=?", (smart["smart_id"],),
        )
        assert state["build_id"] == published["build_id"]
        assert state["refresh_status"] == "FAILED"
        assert state["refresh_request_id"] is not None
        assert state["lease_owner"] is None and state["lease_until_ms"] is None
        assert state["last_error_code"] == "PHOTO_SMART_REFRESH_INTERRUPTED"
        assert recovered_catalog.read_one("SELECT rule_json FROM photo_smart_collections WHERE smart_id=?",
                                          (smart["smart_id"],))[0] == rule_before
        assert recovered_catalog.read_one("SELECT COUNT(*) FROM photo_smart_exclusions WHERE smart_id=?",
                                          (smart["smart_id"],))[0] == 1
        retry = request_refresh(recovered_catalog, smart["smart_id"],
                                instance_token=smart["instance_token"], manual=True)
        assert retry["status"] == "QUEUED"
        assert claim_refresh_lease(recovered_catalog, smart["smart_id"],
            refresh_id=retry["refresh_id"], rule_revision=retry["rule_revision"]) is not None
    finally:
        recovered_catalog.close()
