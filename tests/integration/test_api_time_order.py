"""P1-05 scenario 3: the additive ``order=time_desc`` keyset pagination.

``created_at`` is the catalog insertion time, so these fixtures set it explicitly to
exercise strict ordering and the ``asset_id`` tie-break. Every file lives under
``tmp_path``; no real photo library is read.
"""

from __future__ import annotations

from httpx import AsyncClient

ORDERED_AT_MS = 1_700_000_000_000


def _seed(api_env, count: int) -> None:
    for index in range(count):
        api_env.add_photo(
            f"photos/t{index:03d}.jpg",
            size=(48, 36),
            color=(index % 256, (index * 37) % 256, (index * 91) % 256),
        )


def _set_created_at(api_env, *, step_ms: int, group_size: int = 1) -> list[int]:
    """Give every asset a deterministic insertion time, grouped for tie-breaks."""
    rows = api_env.service.catalog.read_all("SELECT asset_id FROM assets ORDER BY asset_id")
    asset_ids = [int(row["asset_id"]) for row in rows]
    with api_env.service.catalog.write() as connection:
        for position, asset_id in enumerate(asset_ids):
            created_at = ORDERED_AT_MS + (position // max(group_size, 1)) * step_ms
            connection.execute(
                "UPDATE assets SET created_at = ? WHERE asset_id = ?",
                (created_at, asset_id),
            )
    return asset_ids


def test_time_desc_paging_is_complete_ordered_and_tie_break_stable(api_env) -> None:
    _seed(api_env, 26)
    source_id = api_env.register_source()["source_id"]
    api_env.scan(source_id)
    asset_ids = _set_created_at(api_env, step_ms=1_000)

    walk = _walk(api_env, {"order": "time_desc", "limit": 7})
    collected = walk["assets"]
    identities = [item["asset_id"] for item in collected]

    assert len(identities) == len(set(identities)) == 26
    assert identities == sorted(asset_ids, reverse=True)
    keys = [(item["created_at"], item["asset_id"]) for item in collected]
    assert keys == sorted(keys, reverse=True)
    assert all(page["order"] == "time_desc" for page in walk["pages"])
    assert walk["pages"][-1]["next_cursor"] is None
    assert all(page["limit"] == 7 for page in walk["pages"])

    # The default ordering is untouched by the new parameter.
    default = _walk(api_env, {"limit": 7})
    assert [item["asset_id"] for item in default["assets"]] == asset_ids
    assert all(page["order"] == "asset_id" for page in default["pages"])


def test_equal_created_at_falls_back_to_a_stable_asset_id_order(api_env) -> None:
    _seed(api_env, 26)
    source_id = api_env.register_source()["source_id"]
    api_env.scan(source_id)
    asset_ids = _set_created_at(api_env, step_ms=5_000, group_size=5)

    walk = _walk(api_env, {"order": "time_desc", "limit": 3})
    collected = walk["assets"]
    identities = [item["asset_id"] for item in collected]

    assert len(identities) == len(set(identities)) == 26
    assert identities == sorted(asset_ids, reverse=True)
    groups = [item["created_at"] for item in collected]
    assert groups == sorted(groups, reverse=True)
    assert len(set(groups)) == 6


def _walk(api_env, params: dict) -> dict:
    async def run(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        pages = []
        collected = []
        cursor = None
        for _ in range(40):
            current = dict(params)
            if cursor:
                current["cursor"] = cursor
            response = await client.get("/api/v1/assets", params=current)
            assert response.status_code == 200, response.text
            page = response.json()
            pages.append(page)
            collected.extend(page["assets"])
            cursor = page["next_cursor"]
            if not page["has_more"]:
                break
        return {"pages": pages, "assets": collected}

    return api_env.scenario(run)


def test_cursors_never_mix_between_the_two_orderings(api_env) -> None:
    _seed(api_env, 6)
    source_id = api_env.register_source()["source_id"]
    api_env.scan(source_id)
    _set_created_at(api_env, step_ms=1_000)

    async def scenario(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        timed = (await client.get("/api/v1/assets", params={"order": "time_desc", "limit": 2})).json()
        keyed = (await client.get("/api/v1/assets", params={"limit": 2})).json()
        crossed_one = await client.get(
            "/api/v1/assets", params={"cursor": timed["next_cursor"]}
        )
        crossed_two = await client.get(
            "/api/v1/assets", params={"order": "time_desc", "cursor": keyed["next_cursor"]}
        )
        return {"timed": timed, "keyed": keyed, "crossed_one": crossed_one, "crossed_two": crossed_two}

    result = api_env.scenario(scenario)
    assert result["timed"]["next_cursor"] and result["keyed"]["next_cursor"]
    assert result["crossed_one"].status_code == 422
    assert result["crossed_one"].json()["code"] == "INVALID_CURSOR"
    assert result["crossed_two"].status_code == 422
    assert result["crossed_two"].json()["code"] == "INVALID_CURSOR"


def test_order_bounds_and_empty_library(api_env) -> None:
    async def empty_probe(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        page = await client.get("/api/v1/assets", params={"order": "time_desc"})
        bad_order = await client.get("/api/v1/assets", params={"order": "captured_at"})
        zero = await client.get("/api/v1/assets", params={"order": "time_desc", "limit": 0})
        return {"page": page.json(), "bad_order": bad_order, "zero": zero}

    empty = api_env.scenario(empty_probe)
    assert empty["page"]["assets"] == []
    assert empty["page"]["has_more"] is False
    assert empty["page"]["next_cursor"] is None
    assert empty["page"]["order"] == "time_desc"
    assert empty["bad_order"].status_code == 422
    assert empty["bad_order"].json()["code"] == "VALIDATION_ERROR"
    assert empty["zero"].status_code == 422
    assert empty["zero"].json()["code"] == "VALIDATION_ERROR"

    _seed(api_env, 26)
    source_id = api_env.register_source()["source_id"]
    api_env.scan(source_id)

    async def bounds(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        large = await client.get(
            "/api/v1/assets", params={"order": "time_desc", "limit": 5_000}
        )
        filtered = await client.get(
            "/api/v1/assets",
            params={"order": "time_desc", "source_id": source_id, "limit": 5},
        )
        absent = await client.get(
            "/api/v1/assets", params={"order": "time_desc", "source_id": source_id + 1}
        )
        bad_cursor = await client.get(
            "/api/v1/assets", params={"order": "time_desc", "cursor": "!!!!"}
        )
        return {
            "large": large,
            "filtered": filtered,
            "absent": absent,
            "bad_cursor": bad_cursor,
        }

    bounds_result = api_env.scenario(bounds)
    assert bounds_result["large"].status_code == 200
    assert bounds_result["large"].json()["limit"] == 200
    assert len(bounds_result["large"].json()["assets"]) == 26
    assert bounds_result["filtered"].status_code == 200
    assert len(bounds_result["filtered"].json()["assets"]) == 5
    assert bounds_result["absent"].json()["assets"] == []
    assert bounds_result["absent"].json()["has_more"] is False
    assert bounds_result["bad_cursor"].status_code == 422
    assert bounds_result["bad_cursor"].json()["code"] == "INVALID_CURSOR"
