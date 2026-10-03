from __future__ import annotations

from types import SimpleNamespace

from photo_search.search.contracts import SearchRequest
from photo_search.search.retrieval import DEFAULT_TOP_K, MAX_TOP_K
from photo_search.search.service import _channel_summary
from photo_search.search.sessions import MAX_SESSION_IDS


def channel_summary(rows, *, top_k: int, scan_truncated: bool = False):
    payload = {
        "channels": {"fts": [], "vector": rows},
        "vector_scan": {"blocks": 20, "scanned_assets": 300_000, "truncated": scan_truncated},
        "authoritative": {"total": 300_000, "count": 300_000, "truncated": False, "order": "asset_id"},
    }
    binding = SimpleNamespace(space_id="chinese-clip-vit-b-16:test")
    return _channel_summary(payload, match=object(), binding=binding, top_k=top_k)["vector"]


def test_search_and_session_capacity_are_5000_and_request_default_is_fixed() -> None:
    assert (DEFAULT_TOP_K, MAX_TOP_K, MAX_SESSION_IDS) == (5000, 5000, 5000)
    request = SearchRequest.from_envelope({"request_id": "capacity", "contract_version": "p2-contracts.v1"})
    assert request.top_k == 5000


def test_strong_match_distance_boundary_only_adds_vector_counts() -> None:
    rows = [
        {"asset_id": 1, "distance": 1.29},
        {"asset_id": 2, "distance": 1.30},
        {"asset_id": 3, "distance": 1.300001},
    ]
    original_order = [row["asset_id"] for row in rows]
    summary = channel_summary(rows, top_k=5000)
    assert summary["strong_match_count"] == 2
    assert summary["candidates"] == len(rows)
    assert summary["strong_match_more_possible"] is False
    assert summary["truncated"] is False
    assert [row["asset_id"] for row in rows] == original_order


def test_full_top_k_strong_boundary_and_real_scan_truncation_are_distinct() -> None:
    rows = [{"asset_id": i, "distance": 1.30} for i in range(5000)]
    strong = channel_summary(rows, top_k=5000)
    assert strong["strong_match_count"] == 5000
    assert strong["strong_match_more_possible"] is True
    assert strong["truncated"] is False
    assert strong["scan_truncated"] is False

    rows[-1] = {"asset_id": 4999, "distance": 1.300001}
    weak_boundary = channel_summary(rows, top_k=5000)
    assert weak_boundary["strong_match_more_possible"] is False
    assert weak_boundary["truncated"] is False

    actually_truncated = channel_summary(rows, top_k=5000, scan_truncated=True)
    assert actually_truncated["truncated"] is True
    assert actually_truncated["scan_truncated"] is True

    shorter_request = channel_summary(rows[:1000], top_k=1000)
    assert shorter_request["strong_match_more_possible"] is False
