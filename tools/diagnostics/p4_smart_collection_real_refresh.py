"""Real Chinese-CLIP COMBINED smart-collection refresh and cache closure.

Creates a fresh synthetic library under ``--work-dir``, encodes its images with the pinned
local model, publishes vectors through the production outbox path, then exercises the real
authenticated API and refresh worker. The model directory and manifest are read-only inputs;
this driver never reads a user's existing photo library or catalog.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tools" / "diagnostics"))

import p4_02n_real_search as real_search  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from photo_search.api import routes  # noqa: E402
from photo_search.api.settings import ApiSettings  # noqa: E402
from photo_search.app import create_app  # noqa: E402
from photo_search.search.profile import DEFAULT_FUSION_CONFIG, activate_profile  # noqa: E402
from photo_search.search.tokens import FTS_GENERATION_ID, TOKENIZER_VERSION  # noqa: E402

ORIGINAL_SEARCH_WORKER = routes._place_aware_search_worker
QUERY_TEXT = "红色"


def _activate_real_profile(service: Any, spec: Any, generation_id: str) -> str:
    profile_id = "p4-smart-real-chinese-clip"
    with service.catalog.write() as connection:
        connection.execute(
            "INSERT INTO search_profiles(profile_id,embedding_processor_id,space_id,image_generation_id,"
            "query_preprocess_version,query_parser_version,fts_tokenizer_version,fts_generation_id,"
            "reranker_processor_id,fusion_config_json,runtime_package_id,created_at) "
            "VALUES(?,?,?,?,?,'parser-v1',?,?,NULL,?,?,?)",
            (profile_id, str(spec.processor_id), str(spec.envelope["space_id"]), str(generation_id),
             str(spec.envelope.get("query_preprocess_version", "query-v1")), str(TOKENIZER_VERSION),
             FTS_GENERATION_ID, json.dumps(DEFAULT_FUSION_CONFIG, sort_keys=True),
             str(spec.runtime_package_id), service.catalog.now_ms()),
        )
    activate_profile(service.catalog, profile_id=profile_id)
    return profile_id


def run(args: argparse.Namespace) -> int:
    work = Path(args.work_dir).resolve()
    if work.exists():
        raise FileExistsError(f"refusing to reuse diagnostic work directory: {work}")
    work.mkdir(parents=True)
    report: dict[str, Any] = {
        "status": "running", "work_dir": str(work), "model_dir": str(args.model_dir),
        "manifest": str(args.manifest), "requested_device": args.device,
        "query_text": QUERY_TEXT, "checks": {},
    }
    service = encoder = None
    app = None
    device = None if args.device == "auto" else args.device
    sql_lock = threading.Lock()
    sql_counts = {"statements": 0}
    try:
        service, image_root, assets = real_search._seed_library(work)
        report["synthetic_assets"] = assets
        encoder = real_search.build_worker_query_encoder(
            model_dir=args.model_dir, manifest_path=args.manifest, device=device,
        )
        spec = encoder.spec()
        health = encoder.health()
        real_search._record_evidence(service, spec)
        indexed = real_search._publish_vectors(service, spec, image_root, assets, encoder)
        profile_id = _activate_real_profile(service, spec, str(indexed["generation_id"]))
        wiring = real_search.resolve_query_encoder(
            model_dir=args.model_dir, manifest_path=args.manifest, device=device,
        )
        report["model"] = {
            "processor_id": str(spec.processor_id), "model_id": str(spec.envelope.get("model_id")),
            "space_id": str(spec.envelope["space_id"]), "dimension": int(spec.envelope["dimension"]),
            "runtime_package_id": str(spec.runtime_package_id), "provider_id": wiring.provider_id,
            "device": (health.get("device") or {}).get("device"),
            "device_provider": (health.get("device") or {}).get("provider"),
            "runtime_providers_available": list((health.get("runtime") or {}).get("providers") or []),
            "profile_id": profile_id,
        }
        report["wiring"] = wiring.to_dict()
        report["index"] = indexed

        settings = ApiSettings(
            data_root=Path(service.data_root), bind_host="127.0.0.1", bind_port=8000,
            session_token=real_search.SESSION_TOKEN, allowed_hosts=real_search.ALLOWED_HOSTS,
        )
        app = create_app(settings=settings, encoder_factory=wiring.factory)
        api_context = app.state.api
        api_context.start_place_cover_warming = lambda: None
        service_factory = api_context._service_factory

        def traced_service_factory():
            created = service_factory()

            def count_statement(_statement: str) -> None:
                with sql_lock:
                    sql_counts["statements"] += 1

            created.catalog.connection.set_trace_callback(count_statement)
            return created

        api_context._service_factory = traced_service_factory
        block_next_search = threading.Event()
        entered_search = threading.Event()
        release_search = threading.Event()
        fail_next_search = threading.Event()
        search_worker = routes._place_aware_search_worker

        def controlled_search(*call_args, **call_kwargs):
            result = search_worker(*call_args, **call_kwargs)
            if block_next_search.is_set():
                block_next_search.clear()
                entered_search.set()
                if not release_search.wait(45):
                    raise TimeoutError("diagnostic refresh gate timed out")
            if fail_next_search.is_set():
                fail_next_search.clear()
                raise RuntimeError("diagnostic injected after real COMBINED retrieval")
            return result

        routes._place_aware_search_worker = controlled_search
        query_counter = {"calls": 0}

        class CountingEncoder:
            def __init__(self, wrapped: Any) -> None:
                self._wrapped = wrapped

            def __getattr__(self, name: str) -> Any:
                return getattr(self._wrapped, name)

            def encode_queries(self, texts: Any, cancellation: Any = None):
                query_counter["calls"] += len(texts)
                return self._wrapped.encode_queries(texts, cancellation)

        async def scenario() -> None:
            async with app.router.lifespan_context(app):
                async with AsyncClient(transport=ASGITransport(app=app), base_url=real_search.ORIGIN,
                                       follow_redirects=False) as client:
                    session = await client.get("/api/v1/session", params={"token": real_search.SESSION_TOKEN})
                    assert session.status_code == 200, session.text
                    csrf = str(session.json()["csrf_token"])
                    headers = {"X-CSRF-Token": csrf, "Origin": real_search.ORIGIN}
                    capabilities = await client.get("/api/v1/capabilities")
                    assert capabilities.status_code == 200, capabilities.text
                    report["capabilities"] = capabilities.json().get("search")
                    assert capabilities.json()["search"]["query_encoder_available"] is True
                    real_encoder = api_context.query_encoder()
                    assert real_encoder is not None
                    api_context._encoder = CountingEncoder(real_encoder)

                    search_started = time.perf_counter()
                    search_response = await client.post("/api/v1/search/place-aware", headers=headers, json={
                        "request_id": "p4-smart-real-source", "retrieval_mode": "combined",
                        "original_query": QUERY_TEXT, "content_query": QUERY_TEXT,
                        "space_id": str(spec.envelope["space_id"]),
                        "image_generation_id": str(indexed["generation_id"]),
                        "dimension": int(spec.envelope["dimension"]), "top_k": 3, "page_size": 50,
                    })
                    search_elapsed_ms = round((time.perf_counter() - search_started) * 1000, 1)
                    assert search_response.status_code == 200, search_response.text
                    search_body = search_response.json()
                    report["search_source_evidence"] = {
                        "response_keys": sorted(search_body),
                        "has_search_session_id": bool(search_body.get("search_session_id")),
                        "source_mode": search_body.get("photo_collection_source_mode"),
                        "client_cookie_names": sorted(client.cookies.keys()),
                    }
                    assert search_body.get("photo_collection_source_token"), json.dumps(
                        report["search_source_evidence"], ensure_ascii=False,
                    )
                    channels = search_body.get("channels") or {}
                    vector_engaged = bool((channels.get("vector") or {}).get("engaged"))
                    hits = search_body.get("hits") or []
                    by_id = {int(value): name for name, value in assets.items()}
                    top_name = by_id.get(int(hits[0]["asset_id"])) if hits else None
                    report["combined_search"] = {
                        "status": search_response.status_code, "elapsed_ms": search_elapsed_ms,
                        "vector_channel_engaged": vector_engaged, "top_hit": top_name,
                        "query_encoder_calls": query_counter["calls"],
                    }
                    assert vector_engaged
                    assert top_name == real_search.EXPECTED_TOP

                    created = await client.post("/api/v1/photo-smart-collections/from-search",
                        headers=headers, json={"name": "真实模型收口", "create_request_id": "smart-real-create",
                                               "source_token": search_body["photo_collection_source_token"]})
                    assert created.status_code == 200, created.text
                    smart = created.json()["collection"]
                    assert smart["execution_kind"] == "COMBINED"
                    smart_id = int(smart["smart_id"])
                    instance_token = str(smart["instance_token"])
                    rule = json.loads(api_context.service().catalog.read_one(
                        "SELECT rule_json FROM photo_smart_collections WHERE smart_id=?", (smart_id,),
                    )[0])
                    report["smart_binding"] = rule.get("search_binding")
                    assert rule.get("search_binding", {}).get("profile_id") == profile_id
                    assert rule.get("search_binding", {}).get("space_id") == str(spec.envelope["space_id"])
                    assert rule.get("search_binding", {}).get("image_generation_id") == indexed["generation_id"]

                    async def refresh():
                        return await client.post(
                            f"/api/v1/photo-smart-collections/{smart_id}/refresh", headers=headers,
                            json={"instance_token": instance_token, "manual": True},
                        )

                    async def wait_refresh():
                        started = time.perf_counter()
                        for _ in range(9000):
                            detail = await client.get(
                                f"/api/v1/photo-smart-collections/{smart_id}", params={"instance_token": instance_token},
                            )
                            assert detail.status_code == 200, detail.text
                            row = detail.json()["collection"]
                            if row["refresh_status"] not in {"QUEUED", "RUNNING"}:
                                return detail.json(), round((time.perf_counter() - started) * 1000, 1)
                            await asyncio.sleep(0.1)
                        raise TimeoutError("smart refresh did not reach a terminal state")

                    block_next_search.set()
                    refresh_started = time.perf_counter()
                    first_refresh = await refresh()
                    assert first_refresh.status_code == 200 and first_refresh.json()["status"] == "QUEUED"
                    assert await asyncio.to_thread(entered_search.wait, 45)
                    queries_at_refresh = query_counter["calls"]
                    duplicate = await refresh()
                    assert duplicate.status_code == 200
                    assert duplicate.json()["status"] == "RUNNING"
                    assert duplicate.json()["refresh_id"] == first_refresh.json()["refresh_id"]
                    assert query_counter["calls"] == queries_at_refresh
                    release_search.set()
                    detail, _ = await wait_refresh()
                    assert detail["collection"]["refresh_status"] == "NO_CACHE"
                    cache = api_context.service().catalog.read_one(
                        "SELECT build_id,candidate_count FROM photo_smart_cache_state WHERE smart_id=?", (smart_id,),
                    )
                    assert cache["build_id"]
                    calls_before_cache_open = query_counter["calls"]
                    opened = await client.get(
                        f"/api/v1/photo-smart-collections/{smart_id}/assets",
                        params={"instance_token": instance_token, "limit": 50},
                    )
                    assert opened.status_code == 200, opened.text
                    assert query_counter["calls"] == calls_before_cache_open
                    report["refresh"] = {
                        "status": detail["collection"]["refresh_status"],
                        "elapsed_ms": round((time.perf_counter() - refresh_started) * 1000, 1),
                        "candidate_count": int(cache["candidate_count"]), "build_id": str(cache["build_id"]),
                        "query_encoder_calls_after_cache_open": query_counter["calls"],
                    }
                    report["cache_hit"] = {"http_status": opened.status_code,
                                             "assets": len(opened.json()["assets"]),
                                             "query_encoder_calls_unchanged": True}

                    red_id = int(assets[real_search.EXPECTED_TOP])
                    excluded = await client.put(
                        f"/api/v1/photo-smart-collections/{smart_id}/exclusions/{red_id}", headers=headers,
                        json={"instance_token": instance_token, "expected_revision": 1},
                    )
                    assert excluded.status_code == 200, excluded.text
                    refresh_after_exclusion = await refresh()
                    assert refresh_after_exclusion.status_code == 200
                    excluded_detail, _ = await wait_refresh()
                    excluded_page = await client.get(
                        f"/api/v1/photo-smart-collections/{smart_id}/assets",
                        params={"instance_token": instance_token, "limit": 50},
                    )
                    excluded_ids = {int(item["asset_id"]) for item in excluded_page.json()["assets"]}
                    assert red_id not in excluded_ids
                    report["exclusion"] = {"excluded_asset_id": red_id,
                                            "absent_after_refresh": red_id not in excluded_ids,
                                            "refresh_status": excluded_detail["collection"]["refresh_status"]}

                    state_catalog = api_context.service().catalog
                    active_profile = state_catalog.read_one(
                        "SELECT profile_id FROM active_search_profile WHERE state_id=1",
                    )["profile_id"]
                    assert active_profile == profile_id
                    excluded_build_id = str(state_catalog.read_one(
                        "SELECT build_id FROM photo_smart_cache_state WHERE smart_id=?", (smart_id,),
                    )[0])
                    with state_catalog.write() as connection:
                        connection.execute("UPDATE active_search_profile SET profile_id=NULL WHERE state_id=1")
                    missing_model = await client.get(
                        f"/api/v1/photo-smart-collections/{smart_id}", params={"instance_token": instance_token},
                    )
                    assert missing_model.json()["collection"]["freshness"] == "MODEL_REBIND_REQUIRED"
                    deferred = await refresh()
                    assert deferred.status_code == 200 and deferred.json()["status"] == "DEFERRED"
                    state_after_defer = state_catalog.read_one(
                        "SELECT build_id FROM photo_smart_cache_state WHERE smart_id=?", (smart_id,),
                    )[0]
                    with state_catalog.write() as connection:
                        connection.execute("UPDATE active_search_profile SET profile_id=? WHERE state_id=1",
                                           (profile_id,))
                    report["model_state_change"] = {
                        "freshness": missing_model.json()["collection"]["freshness"],
                        "refresh_status": deferred.json()["status"],
                        "previous_cache_preserved": state_after_defer == excluded_build_id,
                    }

                    stable_build = str(state_after_defer)
                    fail_next_search.set()
                    failed = await refresh()
                    assert failed.status_code == 200 and failed.json()["status"] == "QUEUED"
                    failed_detail, _ = await wait_refresh()
                    failed_cache = state_catalog.read_one(
                        "SELECT build_id,refresh_status,last_error_code FROM photo_smart_cache_state WHERE smart_id=?",
                        (smart_id,),
                    )
                    assert failed_cache["build_id"] == stable_build
                    assert failed_cache["refresh_status"] == "FAILED"
                    assert failed_cache["last_error_code"] == "PHOTO_SMART_REFRESH_FAILED"
                    runner = api_context.photo_smart_refresh_runner()
                    for _ in range(100):
                        with runner._lock:
                            if smart_id not in runner._tasks:
                                break
                        await asyncio.sleep(0.02)
                    report["retrieval_failure"] = {
                        "status": failed_detail["collection"]["refresh_status"],
                        "error_code": failed_cache["last_error_code"],
                        "cache_preserved": failed_cache["build_id"] == stable_build,
                    }
                    retried = await refresh()
                    assert retried.status_code == 200 and retried.json()["status"] == "QUEUED"
                    retry_detail, retry_elapsed_ms = await wait_refresh()
                    assert retry_detail["collection"]["refresh_status"] == "NO_CACHE"
                    report["retry"] = {"status": retry_detail["collection"]["refresh_status"],
                                       "elapsed_ms": retry_elapsed_ms}
                    report["sql_statement_count"] = sql_counts["statements"]

        asyncio.run(scenario())
        report["checks"] = {
            "real_query_encoder_available": True,
            "combined_vector_channel": bool(report["combined_search"]["vector_channel_engaged"]),
            "expected_real_model_hit": report["combined_search"]["top_hit"] == real_search.EXPECTED_TOP,
            "profile_space_generation_frozen": report["smart_binding"]["profile_id"] == profile_id,
            "refresh_published_candidates": int(report["refresh"]["candidate_count"]) > 0,
            "cache_open_skips_query_encoder": report["cache_hit"]["query_encoder_calls_unchanged"],
            "duplicate_request_single_flight": True,
            "exclusion_survives_refresh": report["exclusion"]["absent_after_refresh"],
            "model_change_defers_and_preserves_cache": report["model_state_change"]["previous_cache_preserved"],
            "retrieval_exception_preserves_cache": report["retrieval_failure"]["cache_preserved"],
            "failed_refresh_retries": report["retry"]["status"] == "NO_CACHE",
        }
        report["status"] = "pass" if all(report["checks"].values()) else "fail"
    except Exception as error:
        report["status"] = "fail"
        report["failure"] = f"{type(error).__name__}: {error}"
        report["failure_trace"] = traceback.format_exc(limit=12)
    finally:
        routes._place_aware_search_worker = ORIGINAL_SEARCH_WORKER
        if app is not None:
            try:
                app.state.api.close()
            except Exception:
                pass
        if encoder is not None:
            encoder.close()
        if service is not None:
            service.close()
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8", newline="\n")
    print(rendered)
    return 0 if report["status"] == "pass" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--output", type=Path, required=True)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
