"""Date routing over real synthetic EXIF and the existing authenticated API."""

from p4_time_fixtures import assets_by_basename, run_extraction, write_exif_jpeg, write_plain_jpeg


def test_month_filter_paging_content_and_refusals(api_env, monkeypatch):
    from photo_search.api import routes

    for name, stamp, color in (
        ("july-start.jpg", "2024:07:01 00:00:00", (10, 40, 90)),
        ("july-end.jpg", "2024:07:31 23:59:59", (70, 80, 20)),
        ("august.jpg", "2024:08:01 00:00:00", (60, 10, 70)),
    ):
        write_exif_jpeg(api_env.library / name, original=stamp, color=color)
    write_plain_jpeg(api_env.library / "unknown.jpg")
    source = api_env.register_source()
    api_env.scan(source["source_id"])
    run_extraction(api_env.service.catalog)
    names = assets_by_basename(api_env.service.catalog)
    observed = []

    def semantic(_catalog, envelope, **_kwargs):
        observed.append(envelope)
        return {"filters": {}, "hits": [], "total_candidates": 0}

    monkeypatch.setattr(routes, "search_request", semantic)

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        payload = {"request_id": "date-first", "original_query": "July 2024", "page_size": 1}
        response = await client.post("/api/v1/search/place-aware", headers=headers, json=payload)
        assert response.status_code == 200, response.text
        first = response.json()
        assert first["total_candidates"] == 2
        assert first["has_more"] and first["next_cursor"]
        assert first["filters"]["capture_from"].startswith("2024-07-01")
        assert first["filters"]["capture_to_exclusive"].startswith("2024-08-01")
        continuation = {
            **payload, "request_id": "date-next", "intent_plan": first["intent_plan"],
            "search_session_id": first["search_session_id"], "cursor": first["next_cursor"],
        }
        response = await client.post("/api/v1/search/place-aware", headers=headers, json=continuation)
        assert response.status_code == 200, response.text
        second = response.json()
        assert not second["has_more"]
        ids = [item["asset_id"] for page in (first, second) for item in page["hits"]]
        assert len(ids) == len(set(ids)) == 2
        assert set(ids) == {names["july-start.jpg"], names["july-end.jpg"]}
        assert observed == []  # Pure time scope never invokes semantic/model search.

        conflict = await client.post("/api/v1/search/place-aware", headers=headers, json={
            **payload, "request_id": "date-conflict", "capture_from": "2023-01-01",
        })
        assert conflict.status_code == 409
        tampered = dict(first["intent_plan"], capture_from="2023-07-01")
        refused = await client.post("/api/v1/search/place-aware", headers=headers, json={
            **payload, "request_id": "date-plan-tampered", "intent_plan": tampered,
        })
        assert refused.status_code == 409

        for query in ("2024-13的猫", "2024年7月之前的猫", "2024年7月和8月的猫",
                      "2024年7月到现在的猫", "2024年7月至今的猫", "截至2024年7月的照片",
                      "除了2024年7月的照片", "not during July 2024 cats"):
            refused = await client.post("/api/v1/search/place-aware", headers=headers, json={
                "request_id": "date-unsupported", "original_query": query,
            })
            assert refused.status_code == 200, refused.text
            assert refused.json()["unsupported_intent"] is True
        assert observed == []

        for query, mode in (("带有2024年7月字样的照片", "combined"),
                            ("2024年7月", "text")):
            response = await client.post("/api/v1/search/place-aware", headers=headers, json={
                "request_id": "date-content", "original_query": query, "retrieval_mode": mode,
            })
            assert response.status_code == 200, response.text
            assert observed[-1].text == query
            assert observed[-1].capture_from is None
            assert observed[-1].capture_to_exclusive is None

    api_env.scenario(scenario)
