"""Synthetic GPS membership, pagination, antimeridian and revision coverage for MAP."""

from __future__ import annotations

import json


def _seed_map(api_env, count: int = 20) -> None:
    for index in range(count):
        api_env.add_photo(
            f"map/photo-{index:03}.jpg",
            size=(32, 24),
            color=((index * 47) % 256, (index * 83) % 256, (index * 121) % 256),
        )
    source = api_env.service.register_source(display_name="map fixture", root_path=api_env.library)
    api_env.service.run_scan(source["source_id"])
    catalog = api_env.service.catalog
    rows = catalog.read_all(
        "SELECT a.asset_id,a.sha256,MIN(l.location_id) AS location_id FROM assets a JOIN file_locations l USING(asset_id) GROUP BY a.asset_id,a.sha256 ORDER BY a.asset_id"
    )
    with catalog.write() as conn:
        for index, row in enumerate(rows):
            if index == 17:  # deliberately no GPS fact
                continue
            latitude, longitude = (
                (None, None)
                if index == 16
                else (35.0, 179.5)
                if index == 1
                else (35.0, -179.5)
                if index == 2
                else (0.0, 0.0)
                if index in (0, 3)
                else (45.0, -73.0)
                if index == 19
                else (30.0 + index * 0.01, 120.0 + index * 0.01)
            )
            status = "INVALID" if index == 16 else "VALID"
            revision = "b" * 64 if index == 15 else row["sha256"]
            conn.execute(
                """INSERT INTO asset_geo_facts(
                    asset_id,geo_fact_id,location_id,input_revision,fact_revision,status,source,
                    processor_id,raw_fields_checksum_sha256,raw_gps_json,latitude,longitude,
                    datum_status,created_at,updated_at)
                    VALUES(?,?,?,?,1,?,'EXIF_GPS','geo-exif-gps@1',?,?,?,?,'WGS84',1,1)""",
                (
                    row["asset_id"],
                    f"geo-{row['asset_id']}",
                    row["location_id"],
                    revision,
                    status,
                    "a" * 64,
                    json.dumps({"fixture": True}),
                    latitude,
                    longitude,
                ),
            )
        # Another PRESENT copy still contributes exactly one asset; this source shares a file identity.
        duplicate = rows[12]
        location = conn.execute(
            "SELECT source_id FROM file_locations WHERE location_id=?", (duplicate["location_id"],)
        ).fetchone()
        conn.execute(
            "INSERT INTO file_locations(source_id,relative_path,path_key,asset_id,size,mtime_ns,availability) VALUES(?,?,?,?,1024,1,'PRESENT')",
            (
                location["source_id"],
                "duplicate-copy.jpg",
                "duplicate-copy.jpg",
                duplicate["asset_id"],
            ),
        )
        conn.execute(
            "UPDATE file_locations SET availability='MISSING' WHERE asset_id=?",
            (rows[14]["asset_id"],),
        )


def test_map_membership_bbox_cursor_and_revision(api_env) -> None:
    _seed_map(api_env)

    async def scenario(client):
        await api_env.authenticate(client)
        summary = (await client.get("/api/v1/map/summary")).json()
        assert summary["total"] == 16
        assert summary["member_version"] > 1
        zero = (
            await client.get(
                "/api/v1/map/points", params={"west": -1, "south": -1, "east": 1, "north": 1}
            )
        ).json()
        assert zero["mode"] == "photos" and zero["total"] == 2
        assert zero["features"][0]["geometry"]["coordinates"] == [0.0, 0.0]
        crossing = (
            await client.get(
                "/api/v1/map/points", params={"west": 170, "south": 30, "east": -170, "north": 40}
            )
        ).json()
        assert crossing["total"] == 2
        page = (await client.get("/api/v1/map/assets", params={"limit": 7})).json()
        all_ids = [asset["asset_id"] for asset in page["assets"]]
        while page["next_cursor"]:
            page = (
                await client.get(
                    "/api/v1/map/assets", params={"limit": 7, "cursor": page["next_cursor"]}
                )
            ).json()
            all_ids.extend(asset["asset_id"] for asset in page["assets"])
        assert len(all_ids) == len(set(all_ids)) == 16
        old_page = (await client.get("/api/v1/map/assets", params={"limit": 1})).json()
        with api_env.service.catalog.write() as conn:
            conn.execute(
                "UPDATE file_locations SET availability='MISSING' WHERE asset_id=?", (all_ids[0],)
            )
        stale = await client.get(
            "/api/v1/map/assets", params={"limit": 1, "cursor": old_page["next_cursor"]}
        )
        assert stale.status_code == 422
        invalid = await client.get(
            "/api/v1/map/points", params={"west": 181, "south": 0, "east": 0, "north": 1}
        )
        assert invalid.status_code == 422

    api_env.scenario(scenario)
