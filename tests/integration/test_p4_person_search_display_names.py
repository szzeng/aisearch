"""Display names for unified person search plans."""

from __future__ import annotations

from conftest import ApiEnv
from p3_persons_page_fixtures import add_person


def test_person_plan_recovers_missing_display_name_from_catalog(api_env: ApiEnv) -> None:
    person_id = add_person(api_env.service.catalog, name="爸爸")

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        first = await client.post("/api/v1/search/place-aware", headers=headers, json={
            "request_id": "person-name-plan-first", "original_query": "爸爸的照片", "page_size": 10,
        })
        assert first.status_code == 200, first.text
        plan = dict(first.json()["intent_plan"])
        assert plan["person_ids"] == [person_id]
        plan.pop("person_names", None)

        recovered = await client.post("/api/v1/search/place-aware", headers=headers, json={
            "request_id": "person-name-plan-recovered", "original_query": "爸爸的照片",
            "intent_plan": plan, "page_size": 10,
        })
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["intent_plan"]["person_names"] == ["爸爸"]

    api_env.scenario(scenario)
