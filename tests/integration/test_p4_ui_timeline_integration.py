"""Focused homepage timeline entry and basic cursor-browse smoke tests."""

from __future__ import annotations

import asyncio

from conftest import ApiEnv
from p4_time_fixtures import write_exif_jpeg


def test_homepage_timeline_assets_and_basic_browse(api_env: ApiEnv) -> None:
    for number in range(3):
        write_exif_jpeg(
            api_env.library / f"timeline-{number}.jpg",
            original=f"2022:01:0{number + 1} 12:00:00",
            color=(30 + number * 60, 50, 80),
        )
    source = api_env.register_source()
    api_env.scan(int(source["source_id"]))

    async def scenario() -> None:
        async with api_env.client() as client:
            await api_env.authenticate(client)

            page = await client.get("/")
            assert page.status_code == 200
            assert 'id="browse-show-timeline"' in page.text
            assert 'id="browse-timeline"' in page.text
            assert 'id="browse-show-list" aria-pressed="false"' in page.text
            assert "/web/photo-timeline.js?v=p4-ui-photos-b-1" in page.text
            assert "/web/photo-timeline.css?v=p4-ui-photos-b-1" in page.text

            script = await client.get("/web/photo-timeline.js")
            stylesheet = await client.get("/web/photo-timeline.css")
            assert script.status_code == 200 and "PhotoTimeline" in script.text
            assert stylesheet.status_code == 200 and ".photo-timeline__page" in stylesheet.text

            first = await client.get(
                "/api/v1/timeline/assets",
                params={"kind": "source", "source_id": source["source_id"], "time_basis": "library", "limit": 2},
            )
            assert first.status_code == 200, first.text
            first_body = first.json()
            assert len(first_body["assets"]) == 2
            assert first_body["next_cursor"]
            assert all(item["thumbnail_url"].startswith("/api/v1/assets/") for item in first_body["assets"])

            second = await client.get(
                "/api/v1/timeline/assets",
                params={
                    "kind": "source",
                    "source_id": source["source_id"],
                    "time_basis": "library",
                    "limit": 2,
                    "cursor": first_body["next_cursor"],
                },
            )
            assert second.status_code == 200, second.text
            assert len(second.json()["assets"]) == 1
            assert first_body["assets"][0]["asset_id"] != second.json()["assets"][0]["asset_id"]

            periods = await client.get(
                "/api/v1/timeline/periods",
                params={"kind": "source", "source_id": source["source_id"], "time_basis": "library"},
            )
            assert periods.status_code == 200, periods.text
            assert periods.json()["counts"]["effective_total"] == 3

    asyncio.run(scenario())
