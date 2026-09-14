"""P2-06A: the in-process session store, its TTL/LRU bounds and its opaque cursor.

The store is the only place a page can come from, so its contract is deliberately strict:
a cursor is bound to one session, an expired session is an identifiable refusal, the
cached candidate list is bounded, and the whole store is safe to touch from more than one
thread (the API continues a page from a worker thread).
"""

from __future__ import annotations

import base64
import threading

import pytest

from photo_search.catalog.errors import INVALID_CURSOR, SEARCH_SESSION_EXPIRED, CatalogError
from photo_search.search.sessions import (
    MAX_SESSION_IDS,
    SESSION_TTL_MS,
    SearchSessionStore,
    SessionEntry,
    query_hash,
)


class Clock:
    def __init__(self, start: int = 1_000) -> None:
        self.now = int(start)

    def __call__(self) -> int:
        return self.now

    def advance(self, delta: int) -> None:
        self.now += int(delta)


def entry(asset_id: int) -> SessionEntry:
    return SessionEntry(
        asset_id=int(asset_id),
        score=1.0 / (60 + asset_id),
        matched_by={"fts": asset_id},
        hit_reason="TEXT",
        matched_text=("filename_text",),
        semantic_ready=False,
    )


def make_session(store: SearchSessionStore, *, ids: tuple[int, ...] = (3, 1, 2), **overrides):
    options: dict[str, object] = {
        "query_hash": "hash-1",
        "entries": [entry(value) for value in ids],
        "profile_id": None,
        "image_generation_id": "image-gen-x",
        "space_id": "test-space:a1",
        "dimension": 3,
        "catalog_filter_revision": 4,
        "suggestion_revision": 0,
        "fusion_config": {"method": "RRF"},
        "query": {"text": "sunset"},
        "filters": {"availability": "any"},
        "channels": {"fts": {"candidates": 1}},
        "pending_semantic": None,
        "search_projection": {"state": "SYNCED"},
        "state": "OK",
    }
    options.update(overrides)
    return store.create(**options)  # type: ignore[arg-type]


def test_query_hash_binds_the_conditions_but_not_the_profile() -> None:
    base = dict(
        text="sunset",
        source_id=None,
        availability="any",
        place_ids=(),
        include_descendants=True,
        place_state="any",
        top_k=200,
        page_size=20,
    )
    first = query_hash(**base)  # type: ignore[arg-type]
    assert first == query_hash(**base)  # type: ignore[arg-type]
    assert first != query_hash(**{**base, "page_size": 10})  # type: ignore[arg-type]
    assert first != query_hash(**{**base, "place_ids": (2,)})  # type: ignore[arg-type]
    assert first != query_hash(**{**base, "text": None})  # type: ignore[arg-type]


def test_a_cursor_is_opaque_bound_and_never_a_server_error() -> None:
    store = SearchSessionStore()
    session = make_session(store)
    token = store.encode_cursor(session.session_id, 20)
    assert token == store.encode_cursor(session.session_id, 20)
    assert store.decode_cursor(token) == (session.session_id, 20)
    raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
    assert raw == f"s1:{session.session_id}:20"

    for broken in (
        "",
        "!!!not-base64!!!",
        token[:-3],
        base64.urlsafe_b64encode(b"\xff\xfe\xfd").decode().rstrip("="),
        base64.urlsafe_b64encode(b"s2:session:0").decode().rstrip("="),
        base64.urlsafe_b64encode(b"s1::0").decode().rstrip("="),
        base64.urlsafe_b64encode(b"s1:session:not-a-number").decode().rstrip("="),
        "A" * 600,
    ):
        with pytest.raises(CatalogError) as error:
            store.decode_cursor(broken)
        assert error.value.code == INVALID_CURSOR
        assert error.value.details["field"] == "cursor"


def test_the_store_expires_sessions_and_touches_the_lru_order() -> None:
    clock = Clock()
    store = SearchSessionStore(now_ms=clock, ttl_ms=1_000, max_sessions=2)
    first = make_session(store)
    clock.advance(400)
    second = make_session(store, query_hash="hash-2")
    # Touch ``first`` at a strictly later moment so the LRU order is unambiguous: with an
    # identical ``last_used_ms`` the tie-break is the (random) session id on purpose.
    clock.advance(100)
    assert store.get(first.session_id) is first
    clock.advance(400)
    third = make_session(store, query_hash="hash-3")  # evicts ``second``, the least recent
    assert len(store) == 2
    assert store.get(second.session_id) is None
    assert store.get(first.session_id) is not None
    assert store.get(third.session_id) is not None
    # The TTL is measured from creation, and an expired session is evicted on lookup.
    clock.advance(1_001)
    assert store.get(first.session_id) is None
    assert store.get(third.session_id) is None
    with pytest.raises(CatalogError) as error:
        store.require(third.session_id)
    assert error.value.code == SEARCH_SESSION_EXPIRED
    assert error.value.details["ttl_ms"] == 1_000
    assert SESSION_TTL_MS == 600_000


def test_the_cached_candidate_list_is_bounded_and_the_token_stops_at_the_end() -> None:
    store = SearchSessionStore(max_ids=4)
    session = make_session(store, ids=(1, 2, 3, 4, 5, 6))
    assert [item.asset_id for item in session.entries] == [1, 2, 3, 4]
    assert session.candidates_truncated is True
    assert store.cursor_for_offset(session, 0) is not None
    assert store.cursor_for_offset(session, 3) is not None
    assert store.cursor_for_offset(session, 4) is None
    assert MAX_SESSION_IDS == 1000


def test_concurrent_use_stays_consistent() -> None:
    store = SearchSessionStore(max_sessions=8)
    seen: list[str] = []
    failures: list[BaseException] = []
    lock = threading.Lock()

    def worker(index: int) -> None:
        try:
            session = make_session(store, query_hash=f"hash-{index}")
            token = store.encode_cursor(session.session_id, 0)
            assert store.decode_cursor(token) == (session.session_id, 0)
            live = store.get(session.session_id)
            with lock:
                seen.append(session.session_id)
            assert live is None or live.session_id == session.session_id
        except BaseException as error:  # pragma: no cover - reported through the list
            with lock:
                failures.append(error)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(24)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert failures == []
    assert len(seen) == 24
