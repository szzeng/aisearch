"""P2-04: the additive ``GET /api/v1/assets?text=`` surface behind the local session.

Only ``%TEMP%`` fixtures are used. The photos are written and scanned through the real
production path, so the derived documents here come from the scanner's own writes (and its
database triggers), not from seeded rows.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient
from p2_search_fixtures import install_source_places


async def get_json(client: AsyncClient, url: str, **kwargs: Any) -> dict[str, Any]:
    response = await client.get(url, **kwargs)
    try:
        body = response.json()
    except ValueError:  # pragma: no cover - every JSON endpoint answers JSON here
        body = None
    return {"status": response.status_code, "body": body, "headers": dict(response.headers)}


def prepare(env: Any) -> dict[str, int]:
    """Three distinct scanned photos, keyed by their file name."""
    photos = ("2024 年/杭州西湖 照片.jpg", "beach-sunset.jpg", "IMG_0001.JPG")
    for index, relative_path in enumerate(photos):
        env.add_photo(relative_path, noise=index + 1)
    source = env.register_source()
    env.scan(int(source["source_id"]))
    names: dict[str, int] = {}
    rows = env.service.catalog.read_all("SELECT asset_id FROM assets ORDER BY asset_id")
    for row in rows:
        for location in env.service.catalog.read_all(
            "SELECT relative_path FROM file_locations WHERE asset_id = ? ORDER BY location_id",
            (int(row["asset_id"]),),
        ):
            names[str(location["relative_path"]).replace("\\", "/").rsplit("/", 1)[-1]] = int(
                row["asset_id"]
            )
    assert len(names) == len(photos)
    return names


def test_text_search_matches_file_names_and_the_effective_place(api_env) -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        names = prepare(api_env)
        csrf = await api_env.authenticate(client)
        lake = names["杭州西湖 照片.jpg"]
        sunset = names["beach-sunset.jpg"]
        image = names["IMG_0001.JPG"]

        place_id = install_source_places(api_env.service.catalog)["hangzhou"]
        assigned = await client.put(
            f"/api/v1/assets/{sunset}/place-assignment",
            json={
                "action": "assign",
                "target_place_id": place_id,
                "request_id": "req-search-1",
                "expected_revision": 0,
            },
            headers=api_env.post_headers(csrf),
        )
        assert assigned.status_code == 200, assigned.text

        # The scan queued every asset for re-derivation; the request catches up first.
        queued_before = api_env.service.catalog.read_one(
            "SELECT COUNT(*) AS value FROM search_projection_queue"
        )["value"]
        assert int(queued_before) >= 3

        chinese = await get_json(client, "/api/v1/assets", params={"text": "西湖"})
        latin = await get_json(client, "/api/v1/assets", params={"text": "sunset"})
        digits = await get_json(client, "/api/v1/assets", params={"text": "IMG_0001"})
        place = await get_json(client, "/api/v1/assets", params={"text": "hangzhou"})
        both = await get_json(client, "/api/v1/assets", params={"text": "杭州 照片"})
        none = await get_json(client, "/api/v1/assets", params={"text": "不存在的地名"})
        plain = await get_json(client, "/api/v1/assets", params={"limit": 5})
        return {
            "ids": [
                [item["asset_id"] for item in payload["body"]["assets"]]
                for payload in (chinese, latin, digits, place, both, none)
            ],
            "statuses": [
                payload["status"]
                for payload in (chinese, latin, digits, place, both, none, plain)
            ],
            "search": place["body"]["search"],
            "first_search": chinese["body"]["search"],
            "plain": plain["body"],
            "expected": {"lake": lake, "sunset": sunset, "image": image},
            "ordered": sorted([lake, sunset, image]),
            "bodies": [payload["body"] for payload in (chinese, place)],
        }

    result = api_env.scenario(scenario)
    lake = result["expected"]["lake"]
    sunset = result["expected"]["sunset"]
    image = result["expected"]["image"]
    assert result["statuses"] == [200] * 7
    assert result["ids"] == [
        [lake],
        [sunset],
        [image],
        [sunset],  # the effective place is searchable through its alias
        [lake],  # an AND over "杭州 照片" hits the file name only
        [],
    ]

    # The projection was caught up inside the request and the summary says so honestly.
    summary = result["search"]
    assert summary["state"] == "SYNCED"
    assert summary["queued_assets"] == 0
    # The first text request is the one that re-derived the whole scanned backlog.
    assert result["first_search"]["processed_assets"] >= 3
    assert result["first_search"]["state"] == "SYNCED"
    # A later request has nothing left to re-derive, so it stays a pure read.
    assert summary["processed_assets"] == 0
    assert summary["tokenizer_version"] == 1
    assert summary["fts_generation_id"] == "fts-v1"
    assert summary["terms"] == ['"hangzhou"*']

    # Without the parameter nothing about the existing response changed except the new,
    # explicitly empty ``search`` echo: same ordering, same cursor space, same filters.
    plain = result["plain"]
    assert plain["search"] is None
    assert plain["order"] == "asset_id"
    assert [item["asset_id"] for item in plain["assets"]] == result["ordered"]

    # No response carries an absolute path or a coordinate.
    for body in result["bodies"] + [plain]:
        text = str(body)
        assert str(api_env.library) not in text
        assert "C:\\\\" not in text
        assert "latitude" not in text


def test_text_search_ands_with_the_authoritative_place_filter_and_keeps_the_cursor(
    api_env,
) -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        names = prepare(api_env)
        csrf = await api_env.authenticate(client)
        lake = names["杭州西湖 照片.jpg"]
        sunset = names["beach-sunset.jpg"]
        image = names["IMG_0001.JPG"]
        place_id = install_source_places(api_env.service.catalog)["hangzhou"]
        for index, asset_id in enumerate((sunset, image)):
            response = await client.put(
                f"/api/v1/assets/{asset_id}/place-assignment",
                json={
                    "action": "assign",
                    "target_place_id": place_id,
                    "request_id": f"req-and-{index}",
                    "expected_revision": 0,
                },
                headers=api_env.post_headers(csrf),
            )
            assert response.status_code == 200, response.text

        page_one = await get_json(
            client, "/api/v1/assets", params={"text": "jpg", "limit": 1, "order": "asset_id"}
        )
        cursor = page_one["body"]["next_cursor"]
        page_two = await get_json(
            client,
            "/api/v1/assets",
            params={"text": "jpg", "limit": 1, "cursor": cursor, "order": "asset_id"},
        )
        filtered = await get_json(
            client,
            "/api/v1/assets",
            params={"text": "jpg", "place_ids": place_id, "limit": 10},
        )
        unassigned = await get_json(
            client,
            "/api/v1/assets",
            params={"text": "jpg", "place_state": "unassigned", "limit": 10},
        )
        # A cursor minted without a text filter stays valid and keeps its own meaning.
        reused = await get_json(
            client, "/api/v1/assets", params={"text": "jpg", "limit": 2, "cursor": cursor}
        )
        return {
            "page_one": [item["asset_id"] for item in page_one["body"]["assets"]],
            "page_two": [item["asset_id"] for item in page_two["body"]["assets"]],
            "filtered": [item["asset_id"] for item in filtered["body"]["assets"]],
            "unassigned": [item["asset_id"] for item in unassigned["body"]["assets"]],
            "reused": [item["asset_id"] for item in reused["body"]["assets"]],
            "has_more": page_one["body"]["has_more"],
            "revisions": (
                page_one["body"]["catalog_filter_revision"],
                filtered["body"]["catalog_filter_revision"],
            ),
            "place_ids": filtered["body"]["place_ids"],
            "expected": {"lake": lake, "sunset": sunset, "image": image, "place": place_id},
            "ordered": sorted([lake, sunset, image]),
        }

    result = api_env.scenario(scenario)
    lake = result["expected"]["lake"]
    sunset = result["expected"]["sunset"]
    image = result["expected"]["image"]
    ordered = result["ordered"]
    assert result["page_one"] == [ordered[0]]
    assert result["page_two"] == [ordered[1]]
    assert result["has_more"] is True
    assert result["filtered"] == [sorted([sunset, image])[0], sorted([sunset, image])[1]]
    assert result["unassigned"] == [lake]
    assert result["reused"] == ordered[1:]
    assert result["place_ids"] == [result["expected"]["place"]]


def test_an_unusable_query_is_a_structured_422_and_a_session_is_still_required(api_env) -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        prepare(api_env)
        anonymous = await get_json(client, "/api/v1/assets", params={"text": "西湖"})
        csrf = await api_env.authenticate(client)
        assert csrf
        refusals = {}
        for label, value in (
            ("empty", ""),
            ("spaces", "   "),
            ("quote", '"'),
            ("stars", "***"),
            ("parens", "()"),
            ("long", "杭" * 257),
        ):
            payload = await get_json(client, "/api/v1/assets", params={"text": value})
            body = payload["body"] if isinstance(payload["body"], dict) else {}
            refusals[label] = (payload["status"], body.get("code"))
        ok = await get_json(client, "/api/v1/assets", params={"text": "(西湖)"})
        return {"anonymous": anonymous, "refusals": refusals, "ok": ok["status"]}

    result = api_env.scenario(scenario)
    assert result["anonymous"]["status"] == 401
    for status, code in result["refusals"].values():
        assert (status, code) == (422, "VALIDATION_ERROR")
    # A query with real terms is accepted even though FTS5 would treat the characters as
    # syntax: the parentheses are separators, and the AND is built by the server.
    assert result["ok"] == 200


def test_openapi_declares_the_text_parameter_and_capabilities_reports_the_projection(
    api_env,
) -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        prepare(api_env)
        await api_env.authenticate(client)
        await get_json(client, "/api/v1/assets", params={"text": "jpg"})
        return await get_json(client, "/api/v1/capabilities")

    result = api_env.scenario(scenario)
    assert result["status"] == 200
    search = result["body"]["search"]
    assert search["documents"] >= 3
    assert search["queued_assets"] == 0
    assert search["state"] == "SYNCED"
    assert search["tokenizer_version"] == 1
    assert search["fts_generation_id"] == "fts-v1"
    assert search["generations"] == 1

    parameters = api_env.app.openapi()["paths"]["/api/v1/assets"]["get"]["parameters"]
    declared = {item["name"] for item in parameters}
    assert "text" in declared
    assert {"place_ids", "place_state", "order", "cursor"} <= declared
