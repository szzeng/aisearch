"""P3-07B: ``POST /api/v1/search`` accepts the person condition of ADR-0009.

The library, its FTS documents, its vectors and its places all come from the production
paths of the P2 fixtures; the person authority is seeded as rows (see
``p3_person_search_fixtures``) and the query encoder is the documented deterministic
stand-in. Nothing here downloads a model, opens a GPU or touches a real photo library.

The cases are the ten evidence items of the P3-07B card: the shape table, the AND matrix,
canonical resolution, the same-source hit set, the three ways a face is *not* a member, the
profile-free pure filter, session invalidation after every kind of person edit, the frozen
projection/top-up bounds, the privacy closure and the untouched frozen artefacts.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from p2_search_api_fixtures import QUERY_VECTORS, SearchEnv, build_search_env
from p2_search_fixtures import (
    FakeImageEncoder,
    assign_place,
    confirm_pending,
    create_place,
    embedding_spec,
)
from p3_person_search_fixtures import PersonSearchEnv, build_person_search_env

from photo_search.api.errors import ApiError
from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import (
    CATALOG_FILTER_REVISION_STALE,
    INVALID_CURSOR,
    INVALID_PERSON_FILTER,
    PERSON_NOT_FOUND,
    PERSON_REDIRECT_CYCLE,
    PLACE_SUGGESTION_REVISION_STALE,
)
from photo_search.catalog.schema import SUPPORTED_SCHEMA_VERSION
from photo_search.persons.members import MEMBER_PREDICATE
from photo_search.persons.read import person_page
from photo_search.persons.service import PersonService
from photo_search.places import revision as place_revision
from photo_search.search import service as search_service
from photo_search.search.contracts import MAX_PERSON_IDS
from photo_search.search.sessions import SESSION_TTL_MS, SearchSessionStore
from photo_search.search.tokens import VALIDATION_ERROR

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "docs" / "decisions" / "contracts"
COMPANION = json.loads(
    (CONTRACTS / "p3-person-search-contracts.v1.json").read_text(encoding="utf-8")
)

#: The four companions P3-07B promises to leave byte-identical, plus the P3-07A one.
FROZEN_CONTRACT_SHA256 = {
    "p2-contracts.v1.json": (
        "ec7de5f0cbe6d4c2ef2bf357326fa79abc3b427e6133e42186d61778ef0e4a57"
    ),
    "p2-place-contracts.v1.json": (
        "192db8ca53a9aaef835872187c4afbbfdbe2ad7f0fffa56a28061f86f84221fc"
    ),
    "p3-face-correction-contracts.v1.json": (
        "d6d2e9f2e27bf9d7312cef6b5c4c1f24b7bf33e7003289d506d64c4723235a73"
    ),
    "p3-person-split-contracts.v1.json": (
        "462f09358a7088ad1634b096c50fe297cfb85a9c87d8bff7f0725451af89b2ff"
    ),
}

#: The query the deterministic fake adapter can encode, for the semantic AND cases.
TEXT_QUERY = "jpg"


@dataclass
class Seeded:
    """The seeded library plus the ids every case below refers to."""

    env: PersonSearchEnv
    sunset: int
    lake: int
    image: int
    a: int
    b: int
    c: int
    d: int
    f_sunset_a: str
    f_lake_a: str
    f_sunset_b: str
    f_image_b: str
    f_image_d: str
    f_image_a_excluded: str
    f_lake_c_suggested: str
    f_image_c_locked: str
    place_sunset: int
    place_lake: int

    @property
    def search(self) -> SearchEnv:
        return self.env.search

    def member_assets(self, person_id: int) -> list[int]:
        """The expected hit set, computed from the one member predicate directly."""
        rows = self.env.catalog.read_all(
            "SELECT DISTINCT f.asset_id AS asset_id FROM face_person_links AS l "
            "JOIN faces AS f ON f.face_id = l.face_id "
            f"WHERE l.person_id = ? AND {MEMBER_PREDICATE} ORDER BY f.asset_id",
            (int(person_id),),
        )
        return [int(row["asset_id"]) for row in rows]


def seed(env: PersonSearchEnv) -> Seeded:
    """The standard constellation: four people, three assets, three ways not to be a member.

    ``A`` owns the sunset and the lake photo; ``B`` owns the sunset and the third photo;
    ``C`` owns nothing (one suggestion, one ``MANUAL_UNASSIGNED`` face); ``D`` owns the third
    photo. The third photo also carries an ``A`` link that a human exclusion denies, so the
    exclusion is observable without touching any other person's membership of that photo.
    """
    sunset = env.assets["beach sunset.jpg"]
    lake = env.assets["杭州西湖 照片.jpg"]
    image = env.assets["IMG_0001.JPG"]
    a = env.person()
    b = env.person()
    c = env.person()
    d = env.person()
    f_sunset_a = env.face(sunset)
    f_lake_a = env.face(lake)
    f_sunset_b = env.face(sunset, ordinal=1)
    f_image_b = env.face(image)
    f_image_d = env.face(image, ordinal=1)
    f_image_a_excluded = env.face(image, ordinal=2)
    f_lake_c_suggested = env.face(lake, ordinal=1)
    f_image_c_locked = env.face(image, ordinal=3)
    env.link(f_sunset_a, a)
    env.link(f_lake_a, a)
    env.link(f_sunset_b, b)
    env.link(f_image_b, b)
    env.link(f_image_d, d)
    env.link(f_image_a_excluded, a)
    env.exclusion(f_image_a_excluded, a, active=True)
    env.link(f_lake_c_suggested, c, decision="SUGGESTED")
    env.link(f_image_c_locked, c)
    env.control(f_image_c_locked, "MANUAL_UNASSIGNED")
    place_sunset = create_place(env.catalog, "北京", kind="city")
    place_lake = create_place(env.catalog, "杭州", kind="city")
    assign_place(env.catalog, asset_id=sunset, place_id=place_sunset, request_id="req-p-sunset")
    assign_place(env.catalog, asset_id=lake, place_id=place_lake, request_id="req-p-lake")
    # A place edit queues the affected confirmed vectors for reprojection. Replaying the
    # outbox here (as a restart would) keeps the vector channel comparable without hiding
    # the person behaviour behind a pending catch-up.
    confirm_pending(env.catalog)
    # Ninety-six further unnamed people so the frozen 100-id ceiling can be demonstrated
    # with ids that really exist: only the four above carry any face.
    for _ in range(MAX_PERSON_IDS - 4):
        env.person()
    return Seeded(
        env=env,
        sunset=sunset,
        lake=lake,
        image=image,
        a=a,
        b=b,
        c=c,
        d=d,
        f_sunset_a=f_sunset_a,
        f_lake_a=f_lake_a,
        f_sunset_b=f_sunset_b,
        f_image_b=f_image_b,
        f_image_d=f_image_d,
        f_image_a_excluded=f_image_a_excluded,
        f_lake_c_suggested=f_lake_c_suggested,
        f_image_c_locked=f_image_c_locked,
        place_sunset=place_sunset,
        place_lake=place_lake,
    )


@pytest.fixture()
def seeded(tmp_path: Path):
    env = build_person_search_env(tmp_path)
    value = seed(env)
    try:
        yield value
    finally:
        env.close()


@pytest.fixture()
def image_env(tmp_path: Path):
    encoder = FakeImageEncoder(
        embedding_spec(), vectors=QUERY_VECTORS, image_vector=[1.0, 0.0, 0.0]
    )
    env = build_search_env(tmp_path, image_encoder=encoder)
    value = seed(PersonSearchEnv(search=env))
    try:
        yield value
    finally:
        value.env.close()


def body(state: Seeded, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"request_id": "req-1", "page_size": 100}
    payload.update(overrides)
    return payload


async def post(
    client: AsyncClient, state: Seeded, csrf: str, **overrides: Any
) -> Any:
    return await client.post(
        "/api/v1/search",
        json=body(state, **overrides),
        headers=SearchEnv.post_headers(csrf),
    )


def hit_ids(response: Any) -> list[int]:
    return [int(row["asset_id"]) for row in response.json()["hits"]]


def hit_set(response: Any) -> set[int]:
    return set(hit_ids(response))


def inject_dangling_redirect(state: Seeded, person_id: int, missing_person_id: int) -> None:
    """Fault injection: a dangling ``merged_into`` the schema's foreign key forbids.

    The catalog can only ever store a ``merged_into`` that names a real person (and refuses
    a self-redirect in the DDL), so this state is written through a raw connection with the
    foreign key check disabled - exactly the "corrupt catalog" the resolver has to answer
    for instead of guessing a target.
    """
    connection = sqlite3.connect(state.env.catalog.db_path)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            "UPDATE persons SET merged_into = ? WHERE person_id = ?",
            (int(missing_person_id), int(person_id)),
        )
        connection.commit()
    finally:
        connection.close()


def authority_snapshot(state: Seeded) -> dict[str, Any]:
    catalog = state.env.catalog

    def scalar(sql: str) -> int:
        return int(catalog.read_one(sql)["value"])

    return {
        "filter_revision": state.env.filter_revision(),
        "assets": scalar("SELECT COUNT(*) AS value FROM assets"),
        "faces": scalar("SELECT COUNT(*) AS value FROM faces"),
        "links": scalar("SELECT COUNT(*) AS value FROM face_person_links"),
        "persons": scalar("SELECT COUNT(*) AS value FROM persons"),
        "sessions": len(state.search.context.search_sessions),
    }


# ---------------------------------------------------------------- 1. the shape table
@pytest.mark.parametrize(
    "overrides",
    [
        {"person_ids": "3"},
        {"person_ids": 3},
        {"person_ids": [0]},
        {"person_ids": [-1]},
        {"person_ids": [1.5]},
        {"person_ids": ["3"]},
        {"person_ids": [True]},
        {"person_ids": [None]},
        {"person_ids": [3, 3]},
        {"person_match": "SOME"},
        {"person_match": "all"},
        {"surprise": True},
    ],
)
def test_shape_refusals_are_structured_and_write_nothing(
    seeded: Seeded, overrides: dict[str, Any]
) -> None:
    state = seeded
    before = authority_snapshot(state)

    async def scenario(client: AsyncClient) -> Any:
        csrf = await state.search.authenticate(client)
        return await post(client, state, csrf, **overrides)

    response = state.search.scenario(scenario)
    assert response.status_code == 422, response.text
    payload = response.json()
    if overrides.get("person_match") == "all":
        assert payload["code"] == INVALID_PERSON_FILTER
        assert payload["details"]["reason"] == "person_match_all_requires_ids"
    else:
        assert payload["code"] == VALIDATION_ERROR
    assert authority_snapshot(state) == before


def test_the_person_id_ceiling_is_its_own_frozen_refusal(seeded: Seeded) -> None:
    state = seeded
    before = authority_snapshot(state)

    async def scenario(client: AsyncClient) -> Any:
        csrf = await state.search.authenticate(client)
        too_many = await post(
            client, state, csrf, person_ids=list(range(1, MAX_PERSON_IDS + 2))
        )
        refused_snapshot = authority_snapshot(state)
        allowed = await post(
            client, state, csrf, person_ids=list(range(1, MAX_PERSON_IDS + 1))
        )
        return {"too_many": too_many, "refused_snapshot": refused_snapshot, "allowed": allowed}

    outcomes = state.search.scenario(scenario)
    too_many = outcomes["too_many"]
    allowed = outcomes["allowed"]
    # Exactly 100 ids is a shape-valid request (these ids exist; only four of them own
    # faces), while 101 is the frozen size refusal rather than a generic shape error.
    assert allowed.status_code == 200, allowed.text
    assert hit_set(allowed) == {state.sunset, state.lake, state.image}
    assert too_many.status_code == 422
    assert too_many.json()["code"] == INVALID_PERSON_FILTER
    assert too_many.json()["details"] == {
        "reason": "person_filter_too_large",
        "max_person_ids": MAX_PERSON_IDS,
    }
    # The refused request wrote nothing and created no session; the accepted one created
    # exactly one, which is the only difference the two requests may leave behind.
    assert outcomes["refused_snapshot"] == before
    assert authority_snapshot(state) == {**before, "sessions": before["sessions"] + 1}


# ------------------------------------------------------- 2. the AND matrix, all and any
def test_all_any_and_every_other_condition_are_combined_with_and(seeded: Seeded) -> None:
    state = seeded
    other_source = 999
    # Mark the third photo offline before the matrix runs: the person condition must AND
    # with ``availability`` in both directions, while ``availability=any`` ignores it.
    state.env.availability(state.image, "MISSING")

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await state.search.authenticate(client)
        outcomes: dict[str, Any] = {}
        for name, overrides in {
            "any_a": {"person_ids": [state.a]},
            "all_a": {"person_ids": [state.a], "person_match": "all"},
            "any_ab": {"person_ids": [state.a, state.b]},
            "all_ab": {"person_ids": [state.a, state.b], "person_match": "all"},
            "any_ab_place": {
                "person_ids": [state.a, state.b],
                "place_ids": [state.place_sunset],
            },
            "all_ab_place_lake": {
                "person_ids": [state.a, state.b],
                "person_match": "all",
                "place_ids": [state.place_lake],
            },
            "any_a_other_source": {
                "person_ids": [state.a],
                "source_id": other_source,
            },
            "any_b_missing": {"person_ids": [state.b], "availability": "missing"},
            "any_a_missing": {"person_ids": [state.a], "availability": "missing"},
            "place_state_conflict": {
                "person_ids": [state.a],
                "place_ids": [state.place_lake],
                "place_state": "unassigned",
            },
            "text_any_a": {
                "person_ids": [state.a],
                "text": TEXT_QUERY,
                **state.search.explicit_binding(),
            },
            "text_any_b": {
                "person_ids": [state.b],
                "text": TEXT_QUERY,
                **state.search.explicit_binding(),
            },
        }.items():
            outcomes[name] = await post(client, state, csrf, **overrides)
        return outcomes

    outcomes = state.search.scenario(scenario)
    sunset, lake, image = state.sunset, state.lake, state.image

    assert hit_set(outcomes["any_a"]) == {sunset, lake}
    assert hit_set(outcomes["all_a"]) == {sunset, lake}
    assert hit_set(outcomes["any_ab"]) == {sunset, lake, image}
    assert hit_set(outcomes["all_ab"]) == {sunset}
    assert hit_set(outcomes["any_ab_place"]) == {sunset}
    assert hit_set(outcomes["all_ab_place_lake"]) == set()
    assert hit_set(outcomes["any_a_other_source"]) == set()
    # ``availability`` ANDs with the person condition in both directions: ``B`` keeps only
    # its offline photo, and ``A`` has nothing offline at all.
    assert hit_set(outcomes["any_b_missing"]) == {image}
    assert hit_set(outcomes["any_a_missing"]) == set()
    # The place/state rejection matrix of ADR-0005 §10 is unchanged by a valid person filter.
    conflict = outcomes["place_state_conflict"]
    assert conflict.status_code == 422
    assert conflict.json()["code"] == "INVALID_PLACE_FILTER"

    # A semantic query is still restricted by the person condition in *both* channels, and
    # the person condition never becomes a hit reason.
    for name, person, expected in (
        ("text_any_a", state.a, {sunset, lake}),
        ("text_any_b", state.b, {sunset, image}),
    ):
        response = outcomes[name]
        assert response.status_code == 200, response.text
        assert set(hit_ids(response)) == expected
        assert response.json()["channels"]["vector"]["candidates"] == len(expected)
        for row in response.json()["hits"]:
            assert row["hit_reason"] in ("TEXT", "SEMANTIC", "TEXT+SEMANTIC", "FILTER_ONLY")
            assert set(row["matched_by"]) <= {"fts", "vector", "filter"}
        assert response.json()["filters"]["person_ids"] == [person]


def test_availability_ands_with_the_person_condition(seeded: Seeded) -> None:
    state = seeded
    state.env.availability(state.image, "MISSING")

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await state.search.authenticate(client)
        return {
            "present": await post(
                client, state, csrf, person_ids=[state.b], availability="present"
            ),
            "missing": await post(
                client, state, csrf, person_ids=[state.b], availability="missing"
            ),
        }

    outcomes = state.search.scenario(scenario)
    assert hit_set(outcomes["present"]) == {state.sunset}
    assert hit_set(outcomes["missing"]) == {state.image}


# ------------------------------------------------------------- 3. canonical resolution
def test_old_ids_fold_ids_and_unresolvable_ids(seeded: Seeded) -> None:
    state = seeded
    state.env.merge(state.a, [state.b], "req-merge-ab")

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await state.search.authenticate(client)
        merged = await post(client, state, csrf, person_ids=[state.b])
        folded = await post(client, state, csrf, person_ids=[state.a, state.b])
        missing = await post(client, state, csrf, person_ids=[state.b, 999_999])
        return {"merged": merged, "folded": folded, "missing": missing}

    outcomes = state.search.scenario(scenario)
    merged, folded, missing = (
        outcomes["merged"],
        outcomes["folded"],
        outcomes["missing"],
    )
    assert merged.status_code == 200, merged.text
    # The merged-away id resolves to the canonical person and answers that person's photos.
    assert hit_set(merged) == {state.sunset, state.lake, state.image}
    assert merged.json()["filters"]["person_ids"] == [state.a]
    # Two raw ids that now name one person are a legitimate condition, folded silently.
    assert folded.status_code == 200
    assert folded.json()["filters"]["person_ids"] == [state.a]
    assert hit_set(folded) == hit_set(merged)
    # One unresolvable id refuses the whole request: no id is dropped, nothing is partial.
    assert missing.status_code == 404
    assert missing.json()["code"] == PERSON_NOT_FOUND
    assert missing.json()["details"]["person_id"] == 999_999


def test_a_cyclic_redirect_chain_is_refused_whole(seeded: Seeded) -> None:
    state = seeded
    with state.env.catalog.write() as connection:
        connection.execute(
            "UPDATE persons SET merged_into = ? WHERE person_id = ?", (state.a, state.b)
        )
        connection.execute(
            "UPDATE persons SET merged_into = ? WHERE person_id = ?", (state.b, state.a)
        )

    async def scenario(client: AsyncClient) -> Any:
        csrf = await state.search.authenticate(client)
        return await post(client, state, csrf, person_ids=[state.a, state.c])

    response = state.search.scenario(scenario)
    assert response.status_code == 409
    assert response.json()["code"] == PERSON_REDIRECT_CYCLE
    assert response.json()["details"]["person_id"] in (state.a, state.b)


def test_a_dangling_redirect_target_is_refused_whole(seeded: Seeded) -> None:
    state = seeded
    inject_dangling_redirect(state, state.a, 999_999)

    async def scenario(client: AsyncClient) -> Any:
        csrf = await state.search.authenticate(client)
        return await post(client, state, csrf, person_ids=[state.a])

    response = state.search.scenario(scenario)
    assert response.status_code == 409
    assert response.json()["code"] == PERSON_REDIRECT_CYCLE
    assert response.json()["details"]["missing_person_id"] == 999_999


def test_a_merge_during_the_canonical_resolution_is_never_a_mixed_answer(
    seeded: Seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0009 §4.2/§4.3: one request resolves its ids in ONE read snapshot.

    A merge committed by a *second* Catalog connection (another process) right after the first
    id has been resolved must not yield "the pre-merge canonical set with the post-merge
    membership" - the mixed ``([A, B], {})`` neither state can produce. Both consistent
    answers are accepted; the mixed one is not.
    """
    state = seeded
    second = Catalog.open(db_path=state.env.catalog.db_path)
    try:
        persons = PersonService(second, clock=second.now_ms)
        merged: dict[str, Any] = {}
        real_resolve = search_service.resolve_canonical_person_id

        def commit_merge() -> None:
            rows = second.read_all(
                "SELECT person_id, revision FROM persons WHERE person_id IN (?, ?)",
                (int(state.a), int(state.b)),
            )
            revisions = {int(row["person_id"]): int(row["revision"]) for row in rows}
            merged["result"] = persons.merge(
                target_person_id=int(state.b),
                target_expected_revision=revisions[int(state.b)],
                sources=[
                    {
                        "person_id": int(state.a),
                        "expected_revision": revisions[int(state.a)],
                    }
                ],
                request_id="req-second-connection-merge",
            )

        def spy(connection: Any, person_id: int) -> int:
            value = real_resolve(connection, person_id)
            if not merged:
                commit_merge()
            return value

        monkeypatch.setattr(search_service, "resolve_canonical_person_id", spy)

        async def scenario(client: AsyncClient) -> Any:
            csrf = await state.search.authenticate(client)
            return await post(
                client,
                state,
                csrf,
                person_ids=[state.a, state.b],
                person_match="all",
            )

        response = state.search.scenario(scenario)
    finally:
        second.close()
    assert merged, "the second connection must really have committed the merge"
    assert response.status_code == 200, response.text
    observed = (
        list(response.json()["filters"]["person_ids"]),
        hit_set(response),
    )
    pre_merge = ([int(state.a), int(state.b)], {int(state.sunset)})
    post_merge = ([int(state.b)], {int(state.sunset), int(state.lake), int(state.image)})
    assert observed in (pre_merge, post_merge), observed


def test_a_catalog_that_keeps_moving_the_revision_refuses_instead_of_mixing(
    seeded: Seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bounded re-run still ends in a structured refusal, never in a mixed page."""
    state = seeded
    second = Catalog.open(db_path=state.env.catalog.db_path)
    try:
        persons = PersonService(second, clock=second.now_ms)
        moves = {"count": 0}
        real_resolve = search_service.resolve_canonical_person_id

        def rename_every_time() -> None:
            moves["count"] += 1
            row = second.read_one(
                "SELECT revision FROM persons WHERE person_id = ?", (int(state.b),)
            )
            persons.rename(
                person_id=int(state.b),
                name=f"Alice {moves['count']}",
                request_id=f"req-moving-{moves['count']}",
                expected_revision=int(row["revision"]),
            )

        def spy(connection: Any, person_id: int) -> int:
            value = real_resolve(connection, person_id)
            rename_every_time()
            return value

        monkeypatch.setattr(search_service, "resolve_canonical_person_id", spy)
        sessions_before = len(state.search.context.search_sessions)

        async def scenario(client: AsyncClient) -> Any:
            csrf = await state.search.authenticate(client)
            return await post(client, state, csrf, person_ids=[state.b])

        response = state.search.scenario(scenario)
        sessions_after = len(state.search.context.search_sessions)
    finally:
        second.close()
    assert response.status_code == 409, response.text
    payload = response.json()
    assert payload["code"] == CATALOG_FILTER_REVISION_STALE
    details = payload["details"]
    assert details["reason"] == "CATALOG_FILTER_REVISION_CHANGED"
    assert set(details) == {
        "reason",
        "session_catalog_filter_revision",
        "current_catalog_filter_revision",
    }
    assert details["current_catalog_filter_revision"] > details["session_catalog_filter_revision"]
    # One edit per bounded attempt, and no session is ever published for a discarded search.
    assert moves["count"] == search_service.SEARCH_PUBLISH_ATTEMPTS
    assert sessions_after == sessions_before


def _second_connection_merger(state: Seeded, second: Catalog) -> Any:
    """A callable that commits one legitimate merge (target higher) on a second connection."""
    persons = PersonService(second, clock=second.now_ms)
    merged: dict[str, Any] = {}

    def commit() -> None:
        rows = second.read_all(
            "SELECT person_id, revision FROM persons WHERE person_id IN (?, ?)",
            (int(state.a), int(state.b)),
        )
        revisions = {int(row["person_id"]): int(row["revision"]) for row in rows}
        merged["result"] = persons.merge(
            target_person_id=int(state.b),
            target_expected_revision=revisions[int(state.b)],
            sources=[
                {"person_id": int(state.a), "expected_revision": revisions[int(state.a)]}
            ],
            request_id="req-second-connection-merge",
        )

    return commit, merged


def test_a_merge_after_the_last_retrieval_read_is_never_a_mixed_page(
    seeded: Seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The publication gate covers the first page's *render*, not only the retrieval.

    ``_render_page`` re-verifies every row against the current authority, so a merge landing
    right after ``search_assets()`` returned - the window that starts when the last retrieval
    revision was compared - would otherwise publish the old canonical echo with the
    post-merge membership. The gate therefore runs after the page is rendered, and the
    session is registered only once the page is known to be publishable.
    """
    state = seeded
    second = Catalog.open(db_path=state.env.catalog.db_path)
    try:
        commit, merged = _second_connection_merger(state, second)
        real_entries_of = search_service._entries_of
        attempts = {"count": 0}

        def spy(payload: dict[str, Any]) -> Any:
            value = real_entries_of(payload)
            attempts["count"] += 1
            if not merged:
                commit()
            return value

        monkeypatch.setattr(search_service, "_entries_of", spy)
        sessions_before = len(state.search.context.search_sessions)

        async def scenario(client: AsyncClient) -> Any:
            csrf = await state.search.authenticate(client)
            return await post(
                client,
                state,
                csrf,
                person_ids=[state.a, state.b],
                person_match="all",
            )

        response = state.search.scenario(scenario)
        sessions_after = len(state.search.context.search_sessions)
    finally:
        second.close()
    assert merged, "the second connection must really have committed the merge"
    assert response.status_code == 200, response.text
    observed = (list(response.json()["filters"]["person_ids"]), hit_set(response))
    pre_merge = ([int(state.a), int(state.b)], {int(state.sunset)})
    post_merge = ([int(state.b)], {int(state.sunset), int(state.lake), int(state.image)})
    assert observed in (pre_merge, post_merge), observed
    # The straddling attempt was really discarded (a page was built, then rejected), and it
    # left exactly one session behind: the published one.
    assert attempts["count"] >= 2
    assert sessions_after == sessions_before + 1


def test_a_suggested_candidate_change_after_the_last_read_is_never_a_mixed_page(
    seeded: Seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same gate covers the independent suggestion revision the suggested page depends on."""
    state = seeded
    second = Catalog.open(db_path=state.env.catalog.db_path)
    try:
        moved: dict[str, int] = {}
        real_entries_of = search_service._entries_of

        def spy(payload: dict[str, Any]) -> Any:
            value = real_entries_of(payload)
            if not moved:
                with second.write() as connection:
                    moved["revision"] = place_revision.bump_suggestion_revision(
                        connection,
                        cause=place_revision.CAUSE_SUGGESTION_CHANGED,
                        now_ms=1,
                    )
            return value

        monkeypatch.setattr(search_service, "_entries_of", spy)

        async def scenario(client: AsyncClient) -> Any:
            csrf = await state.search.authenticate(client)
            return await post(client, state, csrf, place_state="suggested")

        response = state.search.scenario(scenario)
    finally:
        second.close()
    assert moved, "the second connection must really have moved the suggestion revision"
    assert response.status_code == 200, response.text
    # The answered page is anchored to the state its predicate was verified against, not to
    # the pre-bump revision the retrieval had read.
    assert response.json()["filters"]["suggestion_revision"] == moved["revision"]
    assert response.json()["suggestion_revision"] == moved["revision"]


@pytest.mark.parametrize("mutation", ["suggestion", "both"])
def test_an_exhausted_retry_names_the_counter_that_moved(
    seeded: Seeded, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    """A bounded re-run that never wins must not blame the wrong revision.

    ADR-0005 §9/§10 keep the two frozen counters - and therefore their staleness codes -
    distinct: a search whose page only ever straddles a *suggestion* change answers
    ``PLACE_SUGGESTION_REVISION_STALE``, not ``CATALOG_FILTER_REVISION_STALE`` (whose details
    would then claim a change that its own two values disprove). When both move, the filter
    revision wins, exactly as ``_continue_session`` orders them.
    """
    state = seeded
    second = Catalog.open(db_path=state.env.catalog.db_path)
    try:
        persons = PersonService(second, clock=second.now_ms)
        moves = {"count": 0}
        real_entries_of = search_service._entries_of

        def mutate() -> None:
            moves["count"] += 1
            if mutation == "both":
                row = second.read_one(
                    "SELECT revision FROM persons WHERE person_id = ?", (int(state.b),)
                )
                persons.rename(
                    person_id=int(state.b),
                    name=f"Alice {moves['count']}",
                    request_id=f"req-moving-{moves['count']}",
                    expected_revision=int(row["revision"]),
                )
            with second.write() as connection:
                place_revision.bump_suggestion_revision(
                    connection,
                    cause=place_revision.CAUSE_SUGGESTION_CHANGED,
                    now_ms=moves["count"],
                )

        def spy(payload: dict[str, Any]) -> Any:
            value = real_entries_of(payload)
            mutate()
            return value

        monkeypatch.setattr(search_service, "_entries_of", spy)
        filter_before = state.env.filter_revision()
        sessions_before = len(state.search.context.search_sessions)

        async def scenario(client: AsyncClient) -> Any:
            csrf = await state.search.authenticate(client)
            return await post(client, state, csrf, place_state="suggested")

        response = state.search.scenario(scenario)
        filter_after = state.env.filter_revision()
        sessions_after = len(state.search.context.search_sessions)
    finally:
        second.close()
    assert response.status_code == 409, response.text
    payload = response.json()
    details = payload["details"]
    assert details["reason"] == (
        "CATALOG_FILTER_REVISION_CHANGED"
        if mutation == "both"
        else "SUGGESTION_REVISION_CHANGED"
    )
    if mutation == "both":
        assert payload["code"] == CATALOG_FILTER_REVISION_STALE
        assert set(details) == {
            "reason",
            "session_catalog_filter_revision",
            "current_catalog_filter_revision",
        }
        assert details["current_catalog_filter_revision"] > details["session_catalog_filter_revision"]
        assert filter_after > filter_before
    else:
        assert payload["code"] == PLACE_SUGGESTION_REVISION_STALE
        assert set(details) == {
            "reason",
            "session_suggestion_revision",
            "current_suggestion_revision",
        }
        assert details["current_suggestion_revision"] > details["session_suggestion_revision"]
        # The authoritative filter revision really did not move: the refusal has to say so.
        assert filter_after == filter_before
    # One collision per bounded attempt, and a search that never published leaves no session.
    assert moves["count"] == search_service.SEARCH_PUBLISH_ATTEMPTS
    assert sessions_after == sessions_before


# --------------------------------------------------- 4. same source as the person reads
def test_the_hit_set_is_the_person_asset_count_on_the_real_fixture(seeded: Seeded) -> None:
    state = seeded

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await state.search.authenticate(client)
        responses = {
            person: await post(client, state, csrf, person_ids=[person])
            for person in (state.a, state.b, state.c, state.d)
        }
        reads = {
            person: await client.get(f"/api/v1/persons/{person}")
            for person in (state.a, state.b, state.c, state.d)
        }
        return {"responses": responses, "reads": reads}

    outcomes = state.search.scenario(scenario)
    for person in (state.a, state.b, state.c, state.d):
        response = outcomes["responses"][person]
        assert response.status_code == 200, response.text
        read = outcomes["reads"][person]
        assert read.status_code == 200
        assert read.json()["person_id"] == person
        assert hit_ids(response) == state.member_assets(person)
        assert len(response.json()["hits"]) == int(read.json()["asset_count"])
    assert state.member_assets(state.a) == [state.sunset, state.lake]
    assert state.member_assets(state.b) == [state.sunset, state.image]
    assert state.member_assets(state.c) == []
    assert state.member_assets(state.d) == [state.image]


# ------------------------------------------- 5. three ways a face is not a member
def test_suggestions_locks_and_exclusions_never_make_a_photo_a_member(seeded: Seeded) -> None:
    state = seeded

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await state.search.authenticate(client)
        before = {
            "c": await post(client, state, csrf, person_ids=[state.c]),
            "a": await post(client, state, csrf, person_ids=[state.a]),
            "b": await post(client, state, csrf, person_ids=[state.b]),
            "d": await post(client, state, csrf, person_ids=[state.d]),
        }
        # Lift exactly one human denials/lock and re-ask: the predicate is authoritative and
        # immediately consistent, and the photo's other people are untouched either way.
        state.env.exclusion(state.f_image_a_excluded, state.a, active=False)
        state.env.control(state.f_image_c_locked, "AUTO_ALLOWED")
        after = {
            "c": await post(client, state, csrf, person_ids=[state.c]),
            "a": await post(client, state, csrf, person_ids=[state.a]),
            "b": await post(client, state, csrf, person_ids=[state.b]),
            "d": await post(client, state, csrf, person_ids=[state.d]),
        }
        return {"before": before, "after": after}

    outcomes = state.search.scenario(scenario)
    before, after = outcomes["before"], outcomes["after"]
    # A suggestion and a MANUAL_UNASSIGNED face are never effective members.
    assert hit_set(before["c"]) == set()
    assert hit_set(before["b"]) == {state.sunset, state.image}
    assert hit_set(before["d"]) == {state.image}
    # The exclusion denied A on the third photo while B and D kept their own faces there.
    assert hit_set(before["a"]) == {state.sunset, state.lake}
    # Lifting the two human facts restores exactly those memberships.
    assert hit_set(after["a"]) == {state.sunset, state.lake, state.image}
    assert hit_set(after["c"]) == {state.image}
    assert hit_set(after["b"]) == hit_set(before["b"])
    assert hit_set(after["d"]) == hit_set(before["d"])


def test_a_manually_unassigned_face_is_not_a_member_even_without_a_rival(seeded: Seeded) -> None:
    state = seeded
    with state.env.catalog.write() as connection:
        connection.execute(
            "DELETE FROM face_person_links WHERE face_id = ?", (state.f_image_b,)
        )

    async def scenario(client: AsyncClient) -> Any:
        csrf = await state.search.authenticate(client)
        return await post(client, state, csrf, person_ids=[state.b])

    response = state.search.scenario(scenario)
    # ``B`` only has its sunset face left. The third photo still carries a locked face of
    # ``C`` and a denied face of ``A``, and neither of them becomes a membership for ``B``.
    assert hit_set(response) == {state.sunset}
    assert state.member_assets(state.c) == []


# ------------------------------------------------ 6. pure filter needs no profile/GPU
def test_a_person_only_request_is_a_profile_free_filter_only_answer(seeded: Seeded) -> None:
    state = seeded

    async def scenario(client: AsyncClient) -> Any:
        csrf = await state.search.authenticate(client)
        return await post(client, state, csrf, person_ids=[state.a], page_size=5)

    response = state.search.scenario(scenario)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["profile_id"] is None
    assert payload["space_id"] is None and payload["image_generation_id"] is None
    assert payload["filters"]["binding"] is None
    assert payload["query"] is None
    assert payload["channels"]["fts"]["engaged"] is False
    assert payload["channels"]["vector"]["engaged"] is False
    assert payload["channels"]["vector"]["reason"] == "NO_QUERY_TEXT"
    assert payload["pending_semantic"] is None
    assert [row["hit_reason"] for row in payload["hits"]] == ["FILTER_ONLY"] * 2
    assert [row["matched_by"] for row in payload["hits"]] == [
        {"filter": 1},
        {"filter": 2},
    ]
    # The query encoder was never asked: there is no GPU work behind a person filter.
    assert state.search.encoder.calls == []


def test_a_person_filter_under_a_profile_never_engages_the_encoder_or_the_vector(seeded: Seeded) -> None:
    state = seeded
    state.search.create_profile("profile-1")

    async def scenario(client: AsyncClient) -> Any:
        csrf = await state.search.authenticate(client)
        activated = await client.post(
            "/api/v1/search-profile/activate",
            json={"profile_id": "profile-1"},
            headers=SearchEnv.post_headers(csrf),
        )
        assert activated.status_code == 200, activated.text
        return await post(client, state, csrf, person_ids=[state.b])

    response = state.search.scenario(scenario)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["profile_id"] == "profile-1"
    assert payload["channels"]["vector"]["engaged"] is False
    assert payload["pending_semantic"] is None
    assert [row["hit_reason"] for row in payload["hits"]] == ["FILTER_ONLY"] * 2
    assert state.search.encoder.calls == []


# ------------------------------------ 7. session invalidation after every person edit
@pytest.mark.parametrize(
    "mutation",
    ["rename", "merge", "split", "face-correction", "undo"],
)
def test_every_person_edit_makes_the_old_session_stale_and_researching_works(
    tmp_path: Path, mutation: str
) -> None:
    env = build_person_search_env(tmp_path)
    try:
        state = seed(env)
        old_id = state.b if mutation == "merge" else state.a

        async def first_page(client: AsyncClient) -> dict[str, Any]:
            csrf = await state.search.authenticate(client)
            response = await post(
                client, state, csrf, person_ids=[old_id], page_size=1
            )
            assert response.status_code == 200, response.text
            return response.json()

        page = state.search.scenario(first_page)
        assert page["has_more"] is True and page["next_cursor"]
        before = state.env.filter_revision()

        if mutation == "rename":
            state.env.rename(state.a, "Alice", "req-rename")
            bumps = 1
        elif mutation == "merge":
            state.env.merge(state.a, [state.b], "req-merge")
            bumps = 1
        elif mutation == "split":
            state.env.split(state.a, [state.f_sunset_a], "req-split")
            bumps = 1
        elif mutation == "face-correction":
            state.env.face_control(state.f_sunset_a, "unassign", request_id="req-unassign")
            bumps = 1
        else:
            outcome = state.env.rename(state.a, "Alice", "req-rename-undo")
            state.env.undo(
                str(outcome["operation_id"]),
                "req-undo",
                int(outcome["operation_revision"]),
            )
            # A rename and its compensation are two separate committed operations, so the
            # shared counter moves once for each (ADR-0006 §8).
            bumps = 2

        assert state.env.filter_revision() == before + bumps
        assert state.env.filter_causes()[-1] == "PERSON_CHANGED"

        async def continuation(client: AsyncClient) -> Any:
            csrf = await state.search.authenticate(client)
            return await post(
                client,
                state,
                csrf,
                person_ids=[old_id],
                page_size=1,
                search_session_id=page["search_session_id"],
                cursor=page["next_cursor"],
            )

        stale = state.search.scenario(continuation)
        assert stale.status_code == 409, stale.text
        assert stale.json()["code"] == CATALOG_FILTER_REVISION_STALE
        details = stale.json()["details"]
        assert details["reason"] == "CATALOG_FILTER_REVISION_CHANGED"
        assert set(details) == {
            "reason",
            "search_session_id",
            "session_catalog_filter_revision",
            "current_catalog_filter_revision",
        }
        assert details["session_catalog_filter_revision"] == before
        assert details["current_catalog_filter_revision"] == before + bumps

        async def research(client: AsyncClient) -> Any:
            csrf = await state.search.authenticate(client)
            return await post(client, state, csrf, person_ids=[old_id])

        fresh = state.search.scenario(research)
        assert fresh.status_code == 200, fresh.text
        canonical = state.env.persons.get_person(old_id)["person_id"]
        assert sorted(hit_ids(fresh)) == state.member_assets(canonical)
        if mutation == "merge":
            assert hit_set(fresh) == {state.sunset, state.lake, state.image}
        if mutation in ("split", "face-correction"):
            assert hit_set(fresh) == {state.lake}
    finally:
        env.close()


def test_a_continuation_with_a_different_person_condition_is_refused_by_the_digest(
    seeded: Seeded,
) -> None:
    state = seeded

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await state.search.authenticate(client)
        page = (await post(client, state, csrf, person_ids=[state.a], page_size=1)).json()
        same = await post(
            client,
            state,
            csrf,
            person_ids=[state.a],
            page_size=1,
            search_session_id=page["search_session_id"],
            cursor=page["next_cursor"],
        )
        other_person = await post(
            client,
            state,
            csrf,
            person_ids=[state.b],
            page_size=1,
            search_session_id=page["search_session_id"],
            cursor=page["next_cursor"],
        )
        other_mode = await post(
            client,
            state,
            csrf,
            person_ids=[state.a],
            person_match="all",
            page_size=1,
            search_session_id=page["search_session_id"],
            cursor=page["next_cursor"],
        )
        no_person = await post(
            client,
            state,
            csrf,
            page_size=1,
            search_session_id=page["search_session_id"],
            cursor=page["next_cursor"],
        )
        return {
            "same": same,
            "other_person": other_person,
            "other_mode": other_mode,
            "no_person": no_person,
        }

    outcomes = state.search.scenario(scenario)
    assert outcomes["same"].status_code == 200
    assert outcomes["same"].json()["page_offset"] == 1
    for label in ("other_person", "other_mode", "no_person"):
        response = outcomes[label]
        assert response.status_code == 422, (label, response.text)
        assert response.json()["code"] == INVALID_CURSOR


def test_an_expired_person_search_session_is_identified(seeded: Seeded) -> None:
    state = seeded
    clock = {"now_ms": 1_000_000}
    state.search.context.search_sessions = SearchSessionStore(now_ms=lambda: clock["now_ms"])

    async def scenario(client: AsyncClient) -> Any:
        csrf = await state.search.authenticate(client)
        page = (await post(client, state, csrf, person_ids=[state.a], page_size=1)).json()
        clock["now_ms"] += SESSION_TTL_MS + 1
        return await post(
            client,
            state,
            csrf,
            person_ids=[state.a],
            page_size=1,
            search_session_id=page["search_session_id"],
            cursor=page["next_cursor"],
        )

    response = state.search.scenario(scenario)
    assert response.status_code == 409
    assert response.json()["code"] == "SEARCH_SESSION_EXPIRED"
    assert response.json()["details"] == {"reason": "SESSION_NOT_LIVE", "ttl_ms": SESSION_TTL_MS}


# ------------------------------------------- 8. the frozen projection/top-up blocks
def test_the_projection_and_top_up_blocks_keep_their_frozen_shape_and_bounds(
    seeded: Seeded,
) -> None:
    state = seeded

    async def scenario(client: AsyncClient) -> Any:
        csrf = await state.search.authenticate(client)
        return await post(
            client,
            state,
            csrf,
            person_ids=[state.a],
            text=TEXT_QUERY,
            **state.search.explicit_binding(),
        )

    response = state.search.scenario(scenario)
    assert response.status_code == 200, response.text
    payload = response.json()
    projection = payload["search_projection"]
    required = set(COMPANION["$defs"]["SearchProjectionBlock"]["required"])
    assert set(projection) == required | {"terms", "match_expression"}
    assert projection["state"] in ("SYNCED", "LAGGING")
    assert (projection["state"] == "SYNCED") == (projection["queued_assets"] == 0)
    assert projection["tokenizer_version"] >= 1
    assert not any(str(key).startswith("person") for key in projection)

    topup = payload["pending_semantic"]
    assert topup is not None
    assert set(topup) == set(COMPANION["$defs"]["PendingSemanticPage"]["required"])
    assert topup["limit"] == 200
    assert topup["count"] == len(topup["entries"]) <= 200
    assert topup["reason"] in ("GENERATION_NOT_READY", "ENTRY_NOT_CONFIRMED")
    for entry in topup["entries"]:
        assert set(entry) == set(
            COMPANION["$defs"]["PendingSemanticEntry"]["required"]
        )
        assert entry["semantic_ready"] is False


def test_an_old_projection_never_resurrects_a_hit_the_person_condition_removed(
    seeded: Seeded,
) -> None:
    state = seeded

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await state.search.authenticate(client)
        before = await post(
            client,
            state,
            csrf,
            person_ids=[state.a],
            text=TEXT_QUERY,
            **state.search.explicit_binding(),
        )
        # Deny A on the lake photo: the text projection of that photo still exists, so only
        # the authoritative pre-page re-check can remove it.
        state.env.exclusion(state.f_lake_a, state.a, active=True)
        after = await post(
            client,
            state,
            csrf,
            person_ids=[state.a],
            text=TEXT_QUERY,
            **state.search.explicit_binding(),
        )
        return {"before": before, "after": after}

    outcomes = state.search.scenario(scenario)
    assert hit_set(outcomes["before"]) == {state.sunset, state.lake}
    assert hit_set(outcomes["after"]) == {state.sunset}
    assert outcomes["after"].json()["total_candidates"] == 1


# ------------------------------------------------------------ 9. the privacy closure
def test_person_search_responses_and_refusals_stay_inside_the_privacy_closure(
    seeded: Seeded,
) -> None:
    state = seeded
    state.env.rename(state.a, "Alice Example", "req-name")
    forbidden = (
        "Alice",
        "name_key",
        "aliases",
        "bbox",
        "landmark",
        "embedding",
        "similarity",
        "confidence",
        "payload_sha256",
        "before_value_json",
        "latitude",
        "longitude",
        str(state.env.catalog.data_root),
        "C:\\\\",
    )

    async def scenario(client: AsyncClient) -> list[str]:
        csrf = await state.search.authenticate(client)
        rendered: list[str] = []
        for response in (
            await post(client, state, csrf, person_ids=[state.a]),
            await post(client, state, csrf, person_ids=[999_999]),
            await post(client, state, csrf, person_match="all"),
            await post(client, state, csrf, person_ids=list(range(1, MAX_PERSON_IDS + 2))),
        ):
            rendered.append(response.text)
        return rendered

    bodies = state.search.scenario(scenario)
    for text in bodies:
        for needle in forbidden:
            assert needle not in text, needle


def test_the_filters_echo_is_the_frozen_closed_key_set(seeded: Seeded) -> None:
    state = seeded

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await state.search.authenticate(client)
        with_person = await post(client, state, csrf, person_ids=[state.b])
        without = await post(client, state, csrf)
        return {"with_person": with_person, "without": without}

    outcomes = state.search.scenario(scenario)
    # P4-00B (ADR-0011 §8.2) extends the live echo, and only by adding keys: the frozen
    # ADR-0009 eleven keys plus ``capture_from``/``capture_to_exclusive``. The P3 companion
    # keeps its historical shape and the P4 companion freezes the V2 superset.
    required = set(COMPANION["$defs"]["SearchFiltersEcho"]["required"]) | {
        "capture_from",
        "capture_to_exclusive",
    }
    for key in ("with_person", "without"):
        payload = outcomes[key].json()
        assert set(payload["filters"]) == required
        assert payload["filters"]["capture_from"] is None
        assert payload["filters"]["capture_to_exclusive"] is None
    assert outcomes["with_person"].json()["filters"]["person_ids"] == [state.b]
    assert outcomes["with_person"].json()["filters"]["person_match"] == "any"
    assert outcomes["without"].json()["filters"]["person_ids"] == []
    assert outcomes["without"].json()["filters"]["person_match"] == "any"


def test_the_image_endpoint_echoes_the_empty_person_condition_and_refuses_the_fields(
    image_env: Seeded,
) -> None:
    state = image_env

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await state.search.authenticate(client)
        files = {"image": ("query.jpg", _jpeg_bytes(), "image/jpeg")}
        data = {"request_id": "req-image", **state.search.explicit_binding()}
        accepted = await client.post(
            "/api/v1/search/image",
            data=data,
            files=files,
            headers=SearchEnv.post_headers(csrf),
        )
        refused = await client.post(
            "/api/v1/search/image",
            data={**data, "person_ids": str(state.a)},
            files={"image": ("query.jpg", _jpeg_bytes(), "image/jpeg")},
            headers=SearchEnv.post_headers(csrf),
        )
        return {"accepted": accepted, "refused": refused}

    outcomes = state.search.scenario(scenario)
    accepted = outcomes["accepted"]
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["filters"]["person_ids"] == []
    assert accepted.json()["filters"]["person_match"] == "any"
    refused = outcomes["refused"]
    assert refused.status_code == 422
    assert refused.json()["code"] == VALIDATION_ERROR


def _jpeg_bytes() -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (32, 24), (40, 120, 200)).save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


# -------------------------------------------- 10. the frozen artefacts stay untouched
def test_the_frozen_contracts_migrations_and_schema_version_are_untouched(
    seeded: Seeded,
) -> None:
    state = seeded
    for name, expected in FROZEN_CONTRACT_SHA256.items():
        raw = (CONTRACTS / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == expected, name
    companion = CONTRACTS / "p3-person-search-contracts.v1.json"
    assert json.loads(companion.read_text(encoding="utf-8")) == COMPANION

    migrations = sorted(
        path.name
        for path in (ROOT / "src" / "photo_search" / "catalog" / "migrations").glob("*.sql")
    )
    # The chain is still continuous from 0001 to whatever this build supports. P3-07B adds no
    # schema of its own (``0013`` stays present and byte-frozen); P3-08B appends ``0014``.
    assert len(migrations) == SUPPORTED_SCHEMA_VERSION
    assert [name[:4] for name in migrations] == [
        f"{index:04d}" for index in range(1, SUPPORTED_SCHEMA_VERSION + 1)
    ]
    assert migrations[0] == "0001_initial.sql"
    assert "0013_person_split_operations.sql" in migrations
    assert int(state.env.catalog.schema_version) == SUPPORTED_SCHEMA_VERSION
    # The frozen envelopes keep their own version strings.
    assert COMPANION["$defs"]["SearchContractVersion"]["const"] == "p2-contracts.v1"
    assert COMPANION["$defs"]["SessionTtlMs"]["const"] == SESSION_TTL_MS


def test_the_person_list_page_still_counts_the_same_members(seeded: Seeded) -> None:
    state = seeded
    page = person_page(state.env.catalog, limit=50)
    counts = {int(row["person_id"]): int(row["asset_count"]) for row in page["persons"]}
    assert counts[state.a] == len(state.member_assets(state.a)) == 2
    assert counts[state.b] == len(state.member_assets(state.b)) == 2
    assert counts[state.c] == 0
    assert counts[state.d] == 1
    with pytest.raises(ApiError):
        person_page(state.env.catalog, limit=50, kind="some")
