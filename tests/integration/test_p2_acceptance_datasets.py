"""P2-08 acceptance matrix, part 3: the offline name-package lifecycle (card case 9).

The package state machine itself is proven by P2-03B. This case only adds the combination
the acceptance card asks for: a manual place and an exclusion that must survive a missing,
tampered, swapped and rolled-back package (plus a process restart), while old suggestions
stay readable and only *new* resolutions are blocked.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import pytest
from p2_acceptance_fixtures import (
    acceptance_env,
    add_gps_photo,
    asset_for,
    assignment_row,
    dataset_package_dir,
    dataset_row,
    dataset_status,
    filter_revision,
    install_dataset,
    place_action,
    reopen_service,
    rescan,
    run_gps,
    run_resolver,
    sha_of,
    source_package,
)
from p2_dataset_fixtures import HANGZHOU_POSITION
from p2_search_api_fixtures import SearchEnv

from photo_search.catalog.errors import (
    PLACE_DATASET_INVALID,
    PLACE_DATASET_MISSING,
    CatalogError,
)
from photo_search.geo import resolver as resolver_module
from photo_search.places import datasets as datasets_module
from photo_search.places import suggestions as suggestions_module
from photo_search.places.service import PlaceService


def _resolve_now(env: SearchEnv, asset_id: int) -> dict[str, Any]:
    """One production resolution evaluation (no write), exactly as the executor calls it."""
    return resolver_module.evaluate(
        env.catalog,
        asset_id=int(asset_id),
        input_revision=sha_of(env.catalog, int(asset_id)),
        data_root=env.data_root,
    )


def _datasets_api(env: SearchEnv) -> dict[str, Any]:
    async def scenario(client: Any) -> Any:
        await env.authenticate(client)
        return await client.get("/api/v1/places/datasets")

    response = env.scenario(scenario)
    assert response.status_code == 200, response.text
    return response.json()


def _jobs_with_code(catalog: Any, code: str) -> list[Any]:
    return list(
        catalog.read_all(
            "SELECT * FROM jobs WHERE last_error_code = ? ORDER BY job_id", (str(code),)
        )
    )


def _geo_place(env: SearchEnv, external_id: str) -> int:
    row = env.catalog.read_one(
        "SELECT place_id FROM place_external_refs WHERE external_id = ?", (str(external_id),)
    )
    assert row is not None, f"{external_id} is not mapped"
    return int(row["place_id"])


def _visible_ids(view: dict[str, Any]) -> list[int]:
    return [
        int(candidate["local_place_id"])
        for candidate in view["candidates"]
        if candidate["local_place_id"] is not None
    ]


def _curated_state(env: SearchEnv, asset_ids: Sequence[int]) -> dict[str, Any]:
    catalog = env.catalog
    return {
        "assignments": {
            int(asset_id): tuple(assignment_row(catalog, int(asset_id)) or ())
            for asset_id in asset_ids
        },
        "exclusions": [
            tuple(row)
            for row in catalog.read_all(
                "SELECT asset_id, place_id, active, revision FROM asset_place_exclusions "
                "ORDER BY asset_id, place_id"
            )
        ],
        "filter_revision": filter_revision(catalog),
        "places": [
            tuple(row)
            for row in catalog.read_all(
                "SELECT place_id, parent_place_id, kind, canonical_name, lifecycle "
                "FROM places ORDER BY place_id"
            )
        ],
    }


def test_the_name_package_lifecycle_never_touches_manual_places(tmp_path: Path) -> None:
    env = acceptance_env(tmp_path)
    try:
        catalog = env.catalog
        install_dataset(env, tmp_path=tmp_path, name="mini-1", dataset_id="geo-mini-cn-1")
        assert dataset_status(env)["state"] == "active"
        names = ("西湖 甲.jpg", "西湖 乙.jpg", "西湖 丙.jpg")
        colours = ((10, 30, 200), (200, 30, 30), (30, 200, 30))
        for name, colour in zip(names, colours, strict=True):
            add_gps_photo(env, name, position=HANGZHOU_POSITION, colour=colour)
        rescan(env)
        assets = [asset_for(env, name) for name in names]
        assert len(set(assets)) == 3
        resolved, blocked_asset, late_asset = assets
        run_gps(env, assets)
        assert run_resolver(env, [resolved, blocked_asset]) == 2

        hangzhou = _geo_place(env, "city-hangzhou")
        place_action(
            catalog,
            asset_id=resolved,
            action="ASSIGN",
            target_place_id=hangzhou,
            request_id="p2-08-case9-assign",
        )
        place_action(
            catalog,
            asset_id=blocked_asset,
            action="ASSIGN",
            target_place_id=hangzhou,
            request_id="p2-08-case9-lock",
        )
        place_action(
            catalog,
            asset_id=blocked_asset,
            action="UNASSIGN",
            request_id="p2-08-case9-unassign",
        )
        curated = _curated_state(env, [resolved, blocked_asset])
        before_view = suggestions_module.suggestion_view(catalog.connection, blocked_asset)
        assert before_view["state"] == "candidates"
        assert hangzhou not in _visible_ids(before_view)
        assert before_view["excluded_candidate_count"] == 1
        resolutions_before = int(
            catalog.read_one("SELECT COUNT(*) AS value FROM place_resolutions")["value"]
        )

        # (a) The active package is deleted: the resolver is unavailable, nothing else moves.
        package = dataset_package_dir(env, "geo-mini-cn-1")
        (package / "places.jsonl").unlink()
        missing = dataset_status(env)
        assert missing["state"] == "missing"
        assert missing["reason"] == PLACE_DATASET_MISSING
        assert missing["resolver_available"] is False
        api_view = _datasets_api(env)
        assert api_view["state"] == "missing"
        assert api_view["resolver_available"] is False
        assert str(env.data_root) not in str(api_view)

        # Existing suggestions stay readable, manual locks/exclusions are untouched.
        after_view = suggestions_module.suggestion_view(catalog.connection, blocked_asset)
        assert after_view == before_view
        assert _curated_state(env, [resolved, blocked_asset]) == curated
        served = PlaceService.open(catalog).datasets()
        assert served["state"] == "missing"

        # Warm process: the parse verified earlier in this process is still usable and is the
        # *same* package (the cache key includes the verified content hash), so a resolution
        # computed now is still correct - the deletion only blocks the cold path below.
        warm = _resolve_now(env, late_asset)
        assert warm["dataset_id"] == "geo-mini-cn-1"
        assert warm["status"] == "CANDIDATES"

        # Cold process (the module's sanctioned "fresh process" helper for exactly this): a
        # new resolution is blocked, no resolution row appears and the job names the code.
        resolver_module.clear_content_cache()
        with pytest.raises(CatalogError) as missing_error:
            _resolve_now(env, late_asset)
        assert missing_error.value.code == PLACE_DATASET_MISSING
        run_gps(env, [late_asset])
        run_resolver(env, [late_asset], now_ms=4_000)
        assert int(
            catalog.read_one("SELECT COUNT(*) AS value FROM place_resolutions")["value"]
        ) == resolutions_before, [
            dict(row)
            for row in catalog.read_all(
                "SELECT asset_id, dataset_id, status FROM place_resolutions ORDER BY asset_id"
            )
        ]
        failures = _jobs_with_code(catalog, PLACE_DATASET_MISSING)
        assert failures, "the blocked resolution was not reported with its stable code"
        assert all(str(row["state"]) != "SUCCEEDED" for row in failures)
        assert _curated_state(env, [resolved, blocked_asset]) == curated

        # (b) A tampered package is reported as invalid, and the pointer does not move.
        original = (source_package(tmp_path, "mini-1") / "places.jsonl").read_bytes()
        tamp = original.replace("杭州".encode(), "杭洲".encode())
        assert len(tamp) == len(original)
        (package / "places.jsonl").write_bytes(tamp)
        resolver_module.clear_content_cache()
        invalid = dataset_status(env)
        assert invalid["state"] == "invalid"
        assert invalid["reason"] == PLACE_DATASET_INVALID
        assert invalid["active_dataset"]["problems"][0]["code"] == PLACE_DATASET_INVALID
        assert _datasets_api(env)["state"] == "invalid"
        assert str(env.data_root) not in str(_datasets_api(env))
        with pytest.raises(CatalogError) as invalid_resolution:
            _resolve_now(env, late_asset)
        assert invalid_resolution.value.code == PLACE_DATASET_INVALID
        assert _curated_state(env, [resolved, blocked_asset]) == curated

        # (c) Restoring the bytes brings the same package back; a second package is activated,
        # the pointer survives a restart and the rollback returns to the first package.
        (package / "places.jsonl").write_bytes(original)
        resolver_module.clear_content_cache()
        assert dataset_status(env)["state"] == "active"
        install_dataset(
            env,
            tmp_path=tmp_path,
            name="mini-2",
            dataset_id="geo-mini-cn-2",
            package_version="2",
            previous_dataset_id="geo-mini-cn-1",
        )
        assert dataset_status(env)["active_dataset"]["dataset_id"] == "geo-mini-cn-2"
        assert str(dataset_row(env, "geo-mini-cn-1")["state"]) == "RETAINED"

        service = reopen_service(env)
        catalog = service.catalog
        assert dataset_status(env)["active_dataset"]["dataset_id"] == "geo-mini-cn-2"
        assert _curated_state(env, [resolved, blocked_asset]) == curated
        rolled = datasets_module.rollback_dataset(catalog)
        assert rolled["dataset_id"] == "geo-mini-cn-1"
        assert rolled["rolled_back_from"] == "geo-mini-cn-2"
        assert dataset_status(env)["state"] == "active"
        assert _curated_state(env, [resolved, blocked_asset]) == curated
        assert suggestions_module.suggestion_view(
            catalog.connection, blocked_asset
        ) == before_view
    finally:
        env.context.close()
        env.library.close()
