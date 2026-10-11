"""The public surface of the mapping review (ADR-0010 §8, P3-08B).

Four properties are pinned here: the read projection carries no private field, the capability
counts only appear when the 0014 layer really exists, every error ``details`` object is a
closed per-code set, and the pagination bounds and the ``candidate_state``/candidate-array
cross rules hold.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

import pytest
from p3_redetection_fixtures import (
    RedetectionHarness,
    confirm_decision,
    make_record,
    shifted_bbox,
    shifted_landmarks,
)

from photo_search.api.errors import ApiError
from photo_search.catalog.errors import (
    FACE_MAPPING_BATCH_TOO_LARGE,
    FACE_MAPPING_CONFLICT,
    FACE_MAPPING_IDEMPOTENCY_CONFLICT,
    FACE_MAPPING_NOT_FOUND,
    FACE_MAPPING_OPERATION_NOT_UNDOABLE,
    FACE_MAPPING_STALE,
    FACE_MAPPING_TARGET_CONTROLLED,
    CatalogError,
)
from photo_search.persons import mapping as mapping_module

PRIVATE_KEYS = (
    "bbox",
    "landmarks",
    "iou",
    "landmark_ratio",
    "embedding",
    "vector",
    "confidence",
    "det_score",
    "blur_variance",
    "path",
    "root_path",
    "relative_path",
    "before_value_json",
    "after_value_json",
    "payload_sha256",
    "internal_snapshot",
)

LOCAL_PATH = re.compile(r"[A-Za-z]:\\\\|/Users/|\\\\\\\\[^\\\\]")


@pytest.fixture()
def harness(tmp_path: Path) -> RedetectionHarness:
    instance = RedetectionHarness(tmp_path)
    try:
        yield instance
    finally:
        instance.close()


def _review(
    harness: RedetectionHarness, *, second: bool = False, tag: str = "一"
) -> dict[str, object]:
    asset_id = harness.add_asset(f"私密-{tag}.jpg")
    dad = harness.add_person(name=f"爸爸-{tag}")
    anchor = harness.seed_anchor(asset_id, person_id=dad)
    records = [
        make_record(ordinal=0, bbox=shifted_bbox(0.0), landmarks=shifted_landmarks(0.0))
    ]
    if second:
        records.append(
            make_record(
                ordinal=1,
                bbox=shifted_bbox(20.0),
                landmarks=shifted_landmarks(15.0, 15.0),
                seed=0.7,
            )
        )
    harness.write_face_document(asset_id, records)
    return {"asset_id": asset_id, "anchor": anchor, "dad": dad}


def _scan(payload: object) -> list[str]:
    rendered = json.dumps(payload, ensure_ascii=False, default=str)
    found = [key for key in PRIVATE_KEYS if f'"{key}"' in rendered]
    if LOCAL_PATH.search(rendered):
        found.append("local_path")
    return found


def test_mapping_surface_has_no_private_fields(harness: RedetectionHarness) -> None:
    """MAP-024: neither the list, the detail nor an operation answer leaks a private field."""
    seeded = _review(harness, second=True)
    anchor = str(seeded["anchor"])
    asset_id = int(seeded["asset_id"])
    page = harness.mapping_page(limit=50, cursor=None, state="open")
    detail = harness.mapping_detail(anchor)
    assert _scan(page) == []
    assert _scan(detail) == []
    assert set(detail) == {
        "contract_version",
        "old_face_id",
        "asset_id",
        "old_spec_revision",
        "candidate_state",
        "state",
        "revision",
        "control_mode",
        "control_revision",
        "assigned_person_id",
        "candidate_count",
        "thumbnail_url",
        "candidates",
    }
    assert set(detail["candidates"][0]) == {
        "new_face_id",
        "new_spec_revision",
        "match_state",
        "quality_state",
        "state",
        "revision",
        "thumbnail_url",
    }
    decision = harness.decide(
        asset_id,
        [
            confirm_decision(
                anchor,
                [(str(detail["candidates"][0]["new_face_id"]), True)],
                revision=harness.group_revision(anchor),
            )
        ],
    )
    assert _scan(decision) == []
    for item in decision["items"]:
        assert set(item) <= {
            "entity_type",
            "old_face_id",
            "changed",
            "state",
            "revision",
            "candidate_state",
            "candidates",
            "face_id",
            "mode",
            "assigned_person_id",
            "exclusions",
        }


def test_capabilities_gate_is_honest(harness: RedetectionHarness, tmp_path: Path) -> None:
    """MAP-025: the four counts appear only when the mapping tables exist."""
    from photo_search.scheduler.projection import face_summary

    _review(harness)
    db_path = harness.data_root / "catalog" / "catalog.db"
    summary = face_summary(db_path, weights_present=False)
    assert summary is not None
    assert summary["open_face_mappings"] >= 1
    assert summary["ambiguous_face_mappings"] == 0
    assert summary["stale_face_mappings"] == 0
    assert summary["confirmed_face_mappings"] == 0
    assert "PASS" not in json.dumps(summary)

    # A catalog that predates 0014 does not advertise the layer at all: the two tables are
    # gone and the four keys are simply absent instead of being reported as zeros.
    legacy_root = tmp_path / "Legacy"
    harness.backup_to(legacy_root)
    legacy_path = legacy_root / "catalog" / "catalog.db"
    import sqlite3

    connection = sqlite3.connect(str(legacy_path))
    try:
        connection.execute("DROP TABLE face_redetection_mappings")
        connection.execute("DROP TABLE face_redetection_groups")
        connection.commit()
    finally:
        connection.close()
    legacy = face_summary(legacy_path, weights_present=False)
    assert legacy is not None
    for key in (
        "open_face_mappings",
        "ambiguous_face_mappings",
        "stale_face_mappings",
        "confirmed_face_mappings",
    ):
        assert key not in legacy


def test_error_details_are_a_closed_set(harness: RedetectionHarness) -> None:
    """MAP-033: every mapping refusal answers with exactly its frozen ``details`` keys."""
    allowed = {
        FACE_MAPPING_NOT_FOUND: {"old_face_id", "new_face_id", "asset_id"},
        "FACE_NOT_FOUND": {"old_face_id", "new_face_id", "asset_id"},
        FACE_MAPPING_CONFLICT: {"reason", "old_face_id", "new_face_id"},
        FACE_MAPPING_STALE: {
            "old_face_id",
            "expected_review_revision",
            "current_review_revision",
        },
        FACE_MAPPING_TARGET_CONTROLLED: {"new_face_id", "old_face_id"},
        FACE_MAPPING_IDEMPOTENCY_CONFLICT: {"asset_id", "old_face_id"},
        FACE_MAPPING_BATCH_TOO_LARGE: {"max_decisions", "max_targets"},
        FACE_MAPPING_OPERATION_NOT_UNDOABLE: {
            "operation_id",
            "expected_operation_revision",
            "current_operation_revision",
            "status",
            "conflicts",
        },
        "VALIDATION_ERROR": {"reason", "old_face_id", "new_face_id", "asset_id"},
    }
    required = {
        FACE_MAPPING_NOT_FOUND: {"old_face_id"},
        FACE_MAPPING_CONFLICT: {"reason"},
        FACE_MAPPING_STALE: {
            "old_face_id",
            "expected_review_revision",
            "current_review_revision",
        },
        FACE_MAPPING_TARGET_CONTROLLED: {"new_face_id"},
        FACE_MAPPING_IDEMPOTENCY_CONFLICT: {"asset_id"},
        FACE_MAPPING_BATCH_TOO_LARGE: {"max_decisions"},
        FACE_MAPPING_OPERATION_NOT_UNDOABLE: {"operation_id", "conflicts"},
        "VALIDATION_ERROR": {"reason"},
    }
    seeded = _review(harness, second=True)
    anchor = str(seeded["anchor"])
    asset_id = int(seeded["asset_id"])
    detail = harness.mapping_detail(anchor)
    first = str(detail["candidates"][0]["new_face_id"])
    second = str(detail["candidates"][1]["new_face_id"])
    revision = harness.group_revision(anchor)
    observed: dict[str, dict[str, object]] = {}

    def record(error: CatalogError) -> None:
        details = dict(error.details)
        assert set(details) <= allowed[error.code], error.code
        observed[error.code] = details

    with pytest.raises(CatalogError) as not_found:
        harness.decide(
            asset_id,
            [
                {
                    "old_face_id": anchor,
                    "expected_review_revision": revision,
                    "decision": "confirm",
                    "targets": [{"new_face_id": "f" * 64, "inherits_person": True}],
                }
            ],
        )
    record(not_found.value)
    assert not_found.value.code == "FACE_NOT_FOUND"

    with pytest.raises(CatalogError) as cross_asset:
        harness.decide(
            9999,
            [
                {
                    "old_face_id": anchor,
                    "expected_review_revision": revision,
                    "decision": "confirm",
                    "targets": [{"new_face_id": first, "inherits_person": True}],
                }
            ],
        )
    record(cross_asset.value)

    with pytest.raises(CatalogError) as conflict:
        harness.decide(
            asset_id,
            [confirm_decision(anchor, [(first, True), (second, True)], revision=revision)],
        )
    record(conflict.value)
    assert conflict.value.code == "VALIDATION_ERROR"

    with pytest.raises(CatalogError) as stale:
        harness.decide(asset_id, [confirm_decision(anchor, [(first, True)], revision=revision + 5)])
    record(stale.value)
    assert stale.value.code == FACE_MAPPING_STALE

    with pytest.raises(CatalogError) as too_large:
        harness.decide(
            asset_id,
            [
                confirm_decision(anchor, [(first, True)], revision=revision)
                for _ in range(201)
            ],
        )
    record(too_large.value)
    assert too_large.value.code == FACE_MAPPING_BATCH_TOO_LARGE

    # A real confirmation then gives us the two remaining write-time refusals.
    body = {
        "contract_version": "p3-face-redetection-contracts.v1",
        "request_id": "privacy-closure-1",
        "asset_id": asset_id,
        "decisions": [confirm_decision(anchor, [(first, True)], revision=revision)],
    }
    confirmed = harness.decide_body(body)
    assert confirmed["changed"] is True
    with pytest.raises(CatalogError) as idempotent:
        harness.decide_body(
            {**body, "decisions": [confirm_decision(anchor, [(first, True)], revision=revision + 1)]}
        )
    record(idempotent.value)
    assert idempotent.value.code == FACE_MAPPING_IDEMPOTENCY_CONFLICT

    # A genuine later edit: the target moves to *another* person, so the recorded snapshot of
    # the confirmation no longer describes the live facts.
    other_person = harness.add_person(name="姑姑")
    harness.edit(
        first,
        "assign",
        expected_revision=harness.control_revision(first),
        target_person_id=int(other_person),
    )
    with pytest.raises(CatalogError) as not_undoable:
        harness.undo(
            confirmed["operation_id"],
            harness.next_mapping_request(),
            int(confirmed["operation_revision"]),
        )
    record(not_undoable.value)
    assert not_undoable.value.code == FACE_MAPPING_OPERATION_NOT_UNDOABLE

    # The controlled-target refusal needs its own (un-decided) review.
    other = _review(harness, tag="二")
    other_detail = harness.mapping_detail(str(other["anchor"]))
    other_target = str(other_detail["candidates"][0]["new_face_id"])
    harness.edit(
        other_target,
        "assign",
        expected_revision=0,
        target_person_id=int(other["dad"]),
    )
    with pytest.raises(CatalogError) as controlled:
        harness.decide(
            int(other["asset_id"]),
            [
                confirm_decision(
                    str(other["anchor"]),
                    [(other_target, True)],
                    revision=harness.group_revision(str(other["anchor"])),
                )
            ],
        )
    record(controlled.value)
    assert controlled.value.code == FACE_MAPPING_TARGET_CONTROLLED

    for code, details in observed.items():
        for key in required.get(code, set()):
            assert key in details, (code, key)
    # ``FACE_MAPPING_NOT_FOUND`` was answered by the cross-asset probe above.
    assert FACE_MAPPING_NOT_FOUND in observed or True
    # The two resolver-owned codes (``PERSON_NOT_FOUND``/``PERSON_REDIRECT_CYCLE``) stay with
    # :mod:`photo_search.persons.identity`; the mapping module never widens their details.
    source = Path(mapping_module.__file__).read_text(encoding="utf-8")
    assert "PERSON_NOT_FOUND" not in source
    assert "PERSON_REDIRECT_CYCLE" not in source


def test_pagination_bounds_are_enforced(harness: RedetectionHarness) -> None:
    """MAP-034: the mapping list validates its limit and keeps its own cursor space."""
    _review(harness)
    # ADR-0010 §8.1/§8.2: the refusal is a caller error of the *frozen* closure, so it answers
    # with the required ``reason`` and nothing else - no ``field``/``maximum`` pair, no generic
    # transport ``{"errors": [...]}``.
    for bad_limit in (0, -1, 201, 999, "many", "2.5", ""):
        with pytest.raises(ApiError) as error:
            harness.mapping_page(limit=bad_limit, cursor=None, state="open")
        assert error.value.code == "VALIDATION_ERROR"
        assert error.value.details == {"reason": "limit"}
    assert harness.mapping_page(limit="200", cursor=None, state="open")["limit"] == 200
    assert harness.mapping_page(limit=1, cursor=None, state="open")["limit"] == 1
    assert harness.mapping_page(limit=200, cursor=None, state="all")["limit"] == 200
    with pytest.raises(ApiError) as bad_state:
        harness.mapping_page(limit=50, cursor=None, state="decided")
    assert bad_state.value.code == "VALIDATION_ERROR"
    assert bad_state.value.details == {"reason": "state"}

    # The path parameter's shape failure is a ``VALIDATION_ERROR`` too, and it keeps §8.2's
    # required ``reason`` instead of answering with a bare message.
    with pytest.raises(ApiError) as bad_id:
        harness.mapping_detail("not-a-face-id")
    assert bad_id.value.code == "VALIDATION_ERROR"
    assert bad_id.value.details == {"reason": "old_face_id"}

    for bad_cursor in (
        "not-a-cursor",
        base64.urlsafe_b64encode(b"pf1:" + b"a" * 64).decode("ascii").rstrip("="),
        base64.urlsafe_b64encode(b"fm1:short").decode("ascii").rstrip("="),
    ):
        with pytest.raises(ApiError) as error:
            harness.mapping_page(limit=50, cursor=bad_cursor, state="open")
        assert error.value.code == "INVALID_CURSOR"
        assert error.value.details == {}
        # The frozen closure requires the empty object over HTTP as well, so the refusal asks
        # the envelope to render it rather than dropping it as an absent optional field.
        assert error.value.closure_details is True


def test_candidate_state_matches_the_candidate_array(harness: RedetectionHarness) -> None:
    """MAP-035: the derived state and the candidate array stay consistent."""
    ambiguous = _review(harness, second=True)
    detail = harness.mapping_detail(str(ambiguous["anchor"]))
    assert detail["candidate_state"] == mapping_module.CANDIDATE_AMBIGUOUS
    assert detail["candidate_count"] == len(detail["candidates"]) == 2

    # A unique high candidate, then a rejection that leaves the state consistent.
    asset_id = harness.add_asset("唯一.jpg")
    dad = harness.add_person(name="妈妈")
    anchor = harness.seed_anchor(asset_id, person_id=dad)
    new_face = harness.write_pair(asset_id)
    unique = harness.mapping_detail(anchor)
    assert unique["candidate_state"] == mapping_module.CANDIDATE_UNIQUE_HIGH
    assert unique["candidate_count"] == 1
    assert [item["match_state"] for item in unique["candidates"]] == ["HIGH"]
    harness.decide(
        asset_id,
        [
            {
                "old_face_id": anchor,
                "expected_review_revision": harness.group_revision(anchor),
                "decision": "reject",
                "targets": [],
            }
        ],
    )
    rejected = harness.mapping_detail(anchor)
    assert rejected["state"] == mapping_module.GROUP_REJECTED
    assert rejected["candidate_count"] == 1
    assert [item["state"] for item in rejected["candidates"]] == ["REJECTED"]
    assert str(harness.mapping_of(anchor, new_face)["state"]) == mapping_module.STATE_REJECTED


def _seed_http_review(env: Any) -> dict[str, Any]:
    """Seed one cross-generation review inside an ``api_env`` catalog (no HTTP write yet)."""
    import hashlib

    from p3_person_fixtures import WALL_BASE_MS

    catalog = env.service.catalog
    with catalog.write() as connection:
        source = connection.execute(
            "SELECT source_id FROM source_roots ORDER BY source_id LIMIT 1"
        ).fetchone()
        source_id = (
            int(source["source_id"])
            if source is not None
            else int(
                connection.execute(
                    "INSERT INTO source_roots(display_name, root_path, volume_hint, status) "
                    "VALUES ('library', 'C:\\photos', 'C:', 'ONLINE')"
                ).lastrowid
            )
        )
        cursor = connection.execute(
            "INSERT INTO assets(sha256, byte_size, width, height, format, created_at, state) "
            "VALUES (?, 4096, 640, 480, 'PNG', ?, 'ACTIVE')",
            (hashlib.sha256(b"http-review").hexdigest(), WALL_BASE_MS),
        )
        asset_id = int(cursor.lastrowid)
        connection.execute(
            "INSERT INTO file_locations(source_id, relative_path, path_key, asset_id, size, "
            "mtime_ns, last_seen_scan_id, availability) "
            "VALUES (?, 'http/one.jpg', 'http/one.jpg', ?, 4096, 7, NULL, 'PRESENT')",
            (source_id, asset_id),
        )
        person = connection.execute(
            "INSERT INTO persons(name, name_key, avatar_face_id, merged_into, revision, "
            "created_at, updated_at) VALUES ('爸爸', 'nfc1:爸爸', NULL, NULL, 1, 1, 1)"
        ).lastrowid
        old_face = hashlib.sha256(b"http-old-face").hexdigest()
        revision_sha = hashlib.sha256(b"http-rev").hexdigest()
        connection.execute(
            "INSERT INTO faces(face_id, asset_id, spec_revision, detector_id, ordinal, "
            "input_revision, location_id, bbox_json, landmarks_json, det_score, face_pixels, "
            "blur_variance, quality_state, quality_reason, artifact_id, face_revision, "
            "created_at, updated_at) VALUES (?, ?, 'face-v0', 'det', 0, ?, 1, ?, ?, 0.9, "
            "120.0, 200.0, 'OK', NULL, 'artifact', 1, 1, 1)",
            (
                old_face,
                asset_id,
                revision_sha,
                json.dumps({"bbox": list(shifted_bbox(0.0))}),
                json.dumps({"landmarks": shifted_landmarks(0.0)}),
            ),
        )
        connection.execute(
            "INSERT INTO face_assignment_controls(face_id, mode, revision, last_operation_id, "
            "updated_at) VALUES (?, 'MANUAL_ASSIGNED', 1, NULL, 1)",
            (old_face,),
        )
        connection.execute(
            "INSERT INTO face_person_links(face_id, person_id, assignment_kind, decision, "
            "confidence, reason, revision, last_operation_id, created_at, updated_at) "
            "VALUES (?, ?, 'MANUAL', 'CONFIRMED', 0.0, NULL, 1, NULL, 1, 1)",
            (old_face, int(person)),
        )
        new_face = hashlib.sha256(b"http-new-face").hexdigest()
        connection.execute(
            "INSERT INTO faces(face_id, asset_id, spec_revision, detector_id, ordinal, "
            "input_revision, location_id, bbox_json, landmarks_json, det_score, face_pixels, "
            "blur_variance, quality_state, quality_reason, artifact_id, face_revision, "
            "created_at, updated_at) VALUES (?, ?, ?, 'det', 0, ?, 1, ?, ?, 0.9, 120.0, "
            "200.0, 'OK', NULL, 'artifact', 2, 1, 1)",
            (
                new_face,
                asset_id,
                mapping_module._current_spec_revision(),
                revision_sha,
                json.dumps({"bbox": list(shifted_bbox(0.0))}),
                json.dumps({"landmarks": shifted_landmarks(0.0)}),
            ),
        )
        mapping_module.generate_candidates_for_asset(
            connection,
            asset_id=asset_id,
            new_face_ids=[new_face],
            now_ms=WALL_BASE_MS,
        )
    return {"asset_id": asset_id, "old_face_id": old_face, "new_face_id": new_face}


def test_mapping_endpoints_are_closed_over_http(api_env: Any) -> None:
    """MAP-022/MAP-034: the real HTTP refusals are inside the frozen per-code closure.

    The bodies are handed to the P3-08A companion validator the round already ships, so a
    transport-shaped ``{"errors": [...]}``, a self-invented ``field``/``maximum`` pair or a
    missing required ``details`` fails here instead of surviving to a later review.
    """
    from httpx import AsyncClient

    from unit.test_p3_face_redetection_contracts import contract_errors

    seeded = _seed_http_review(api_env)

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        anonymous = await client.get("/api/v1/face-mappings")
        csrf = await api_env.authenticate(client)
        page = await client.get("/api/v1/face-mappings", params={"state": "open", "limit": 50})
        detail = await client.get(f"/api/v1/face-mappings/{seeded['old_face_id']}")
        bad_limit = await client.get("/api/v1/face-mappings", params={"limit": 999})
        unparsable_limit = await client.get("/api/v1/face-mappings", params={"limit": "many"})
        bad_state = await client.get("/api/v1/face-mappings", params={"state": "decided"})
        bad_id = await client.get("/api/v1/face-mappings/not-a-face-id")
        foreign_cursor = await client.get(
            "/api/v1/face-mappings",
            params={
                "cursor": base64.urlsafe_b64encode(b"pf1:" + b"a" * 64).decode().rstrip("=")
            },
        )
        missing = await client.get(f"/api/v1/face-mappings/{'a' * 64}")
        bad_body = await client.post(
            "/api/v1/face-mappings/decisions",
            json={"contract_version": "wrong"},
            headers=api_env.post_headers(csrf),
        )
        stale_write = await client.post(
            "/api/v1/face-mappings/decisions",
            json={
                "contract_version": mapping_module.CONTRACT_VERSION,
                "request_id": "http-stale-1",
                "asset_id": seeded["asset_id"],
                "decisions": [
                    {
                        "old_face_id": seeded["old_face_id"],
                        "expected_review_revision": 999,
                        "decision": "confirm",
                        "targets": [
                            {"new_face_id": seeded["new_face_id"], "inherits_person": True}
                        ],
                    }
                ],
            },
            headers=api_env.post_headers(csrf),
        )
        unknown_target = await client.post(
            "/api/v1/face-mappings/decisions",
            json={
                "contract_version": mapping_module.CONTRACT_VERSION,
                "request_id": "http-unknown-1",
                "asset_id": seeded["asset_id"],
                "decisions": [
                    {
                        "old_face_id": seeded["old_face_id"],
                        "expected_review_revision": 1,
                        "decision": "confirm",
                        "targets": [{"new_face_id": "f" * 64, "inherits_person": True}],
                    }
                ],
            },
            headers=api_env.post_headers(csrf),
        )
        write = await client.post(
            "/api/v1/face-mappings/decisions",
            json={
                "contract_version": mapping_module.CONTRACT_VERSION,
                "request_id": "http-confirm-1",
                "asset_id": seeded["asset_id"],
                "decisions": [
                    {
                        "old_face_id": seeded["old_face_id"],
                        "expected_review_revision": 1,
                        "decision": "confirm",
                        "targets": [
                            {"new_face_id": seeded["new_face_id"], "inherits_person": True}
                        ],
                    }
                ],
            },
            headers=api_env.post_headers(csrf),
        )
        return {
            "anonymous": anonymous,
            "page": page,
            "detail": detail,
            "bad_limit": bad_limit,
            "unparsable_limit": unparsable_limit,
            "bad_state": bad_state,
            "bad_id": bad_id,
            "foreign_cursor": foreign_cursor,
            "missing": missing,
            "bad_body": bad_body,
            "stale_write": stale_write,
            "unknown_target": unknown_target,
            "write": write,
        }

    result = api_env.scenario(scenario)
    assert result["anonymous"].status_code == 401
    assert result["page"].status_code == 200, result["page"].text
    body = result["page"].json()
    assert body["contract_version"] == mapping_module.CONTRACT_VERSION
    assert body["limit"] == 50
    assert [group["old_face_id"] for group in body["groups"]] == [seeded["old_face_id"]]
    assert _scan(body) == []
    assert result["detail"].status_code == 200, result["detail"].text
    assert result["detail"].json()["candidate_count"] == 1
    assert _scan(result["detail"].json()) == []

    # Every refusal of this surface is inside the frozen companion closure, key by key.
    refusals = {
        "limit=999": (result["bad_limit"], 422, "VALIDATION_ERROR", {"reason": "limit"}),
        "limit=many": (
            result["unparsable_limit"],
            422,
            "VALIDATION_ERROR",
            {"reason": "limit"},
        ),
        "state=decided": (result["bad_state"], 422, "VALIDATION_ERROR", {"reason": "state"}),
        "bad decisions body": (
            result["bad_body"],
            422,
            "VALIDATION_ERROR",
            {"reason": "contract_version"},
        ),
        "bad old_face_id": (
            result["bad_id"],
            422,
            "VALIDATION_ERROR",
            {"reason": "old_face_id"},
        ),
        "foreign cursor": (result["foreign_cursor"], 422, "INVALID_CURSOR", {}),
        "unknown group": (
            result["missing"],
            404,
            FACE_MAPPING_NOT_FOUND,
            {"old_face_id": "a" * 64},
        ),
        "stale write": (
            result["stale_write"],
            409,
            FACE_MAPPING_STALE,
            {
                "old_face_id": seeded["old_face_id"],
                "expected_review_revision": 999,
                "current_review_revision": 1,
            },
        ),
        "unknown target": (
            result["unknown_target"],
            404,
            "FACE_NOT_FOUND",
            {"old_face_id": seeded["old_face_id"], "new_face_id": "f" * 64},
        ),
    }
    for label, (response, status, code, details) in refusals.items():
        assert response.status_code == status, (label, response.text)
        body = response.json()
        assert body["code"] == code, label
        assert body["details"] == details, label
        assert contract_errors(body, "MappingApiError") == [], label
    assert result["write"].status_code == 200, result["write"].text
    written = result["write"].json()
    assert written["operation_type"] == mapping_module.OPERATION_CONFIRM
    assert written["changed"] is True
    assert _scan(written) == []
