from __future__ import annotations

import json
import subprocess

import pytest

from photo_search.search import perf_diagnostics


@pytest.mark.parametrize("provider", sorted(perf_diagnostics.SUPPORTED_PROVIDERS))
def test_perf_identity_uses_bound_space_for_all_supported_models(provider: str) -> None:
    identity = perf_diagnostics.safe_identity({
        "profile_id": "active-profile",
        "generation_id": "generation-1",
        "space_id": provider + ":space-1",
        "processor_id": provider + "@processor-v1",
        "dimension": 512,
    })

    assert identity == {
        "active_profile_id": "active-profile",
        "active_image_generation_id": "generation-1",
        "provider_id": provider,
        "processor_id": provider + "@processor-v1",
        "space_id": provider + ":space-1",
        "dimension": 512,
        "encoder_identity": provider,
    }


def test_perf_identity_rejects_unrecognized_or_unbound_spaces() -> None:
    assert perf_diagnostics.safe_identity(None) is None
    assert perf_diagnostics.safe_identity({"space_id": "unknown:space", "dimension": 512}) is None
    assert perf_diagnostics.safe_identity({"space_id": "qwen3-vl-embedding-2b", "dimension": 512}) is None


def test_model_binding_mismatch_is_explicit_without_worker_calls() -> None:
    binding = {"processor_id": "qwen-processor", "space_id": "qwen:space", "dimension": 512}
    encoder = {"processor_id": "qwen-processor", "space_id": "qwen:space", "dimension": 512}

    assert perf_diagnostics.binding_mismatch(binding, encoder) is False
    assert perf_diagnostics.binding_mismatch(binding, {**encoder, "space_id": "clip:space"}) is True
    assert perf_diagnostics.binding_mismatch(binding, {**encoder, "dimension": 768}) is True
    assert perf_diagnostics.binding_mismatch(binding, None) is None


def test_diagnostic_export_metadata_is_allow_listed_and_query_free() -> None:
    encoded = perf_diagnostics.encode_metrics({
        "binding": {"profile_id": "p1", "generation_id": "g1",
                    "space_id": "chinese-clip-vit-b-16:space", "dimension": 512},
        "query_encode_ms": 12.5,
        "worker_pid": 123,
        "original_query": "private beach search",
        "local_path": "D:/private/photo.jpg",
    })
    payload = json.loads(encoded)

    assert payload["model_identity"]["provider_id"] == "chinese-clip-vit-b-16"
    assert payload["query_encode_ms"] == 12.5
    assert payload["worker_pid"] == 123
    assert "original_query" not in payload
    assert "local_path" not in payload
    assert "private beach search" not in encoded


def test_server_timing_contains_only_numeric_stage_values() -> None:
    value = perf_diagnostics.server_timing({"query_encode_ms": 4.25, "bad_ms": "skip"}, 8.5)

    assert "query_encode_ms;dur=4.250" in value
    assert "search_execution_ms;dur=8.500" in value
    assert "bad_ms" not in value


def test_phase_tree_accounts_non_overlapping_stages_and_keeps_nested_detail() -> None:
    tree = perf_diagnostics.phase_tree({
        "place_aware_prepare_ms": 30.0,
        "intent_collect_ms": 20.0,
        "place_choices_ms": 10.0,
        "search_prepare_ms": 5.0,
        "retrieval_total_ms": 40.0,
        "query_encode_ms": 4.0,
        "page_render_ms": 10.0,
    }, 100.0)

    assert tree["preparation"]["intent_collect_ms"] == 20.0
    assert tree["retrieval"]["query_encode_ms"] == 4.0
    assert tree["accounting"]["non_overlapping_ms"] == 85.0
    assert tree["accounting"]["unaccounted_ms"] == 15.0
    assert tree["accounting"]["coverage_ratio"] == 0.85


def test_server_gate_requires_explicit_environment_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(perf_diagnostics.PERF_ENV, raising=False)
    assert perf_diagnostics.enabled() is False
    monkeypatch.setenv(perf_diagnostics.PERF_ENV, "true")
    assert perf_diagnostics.enabled() is False
    monkeypatch.setenv(perf_diagnostics.PERF_ENV, "1")
    assert perf_diagnostics.enabled() is True


def test_perf_config_endpoint_is_authenticated_and_independent_of_server_environment(api_env, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario(client) -> None:
        monkeypatch.delenv(perf_diagnostics.PERF_ENV, raising=False)
        unauthenticated = await client.get("/api/v1/search-perf/config")
        assert unauthenticated.status_code == 401
        await api_env.authenticate(client)
        disabled = await client.get("/api/v1/search-perf/config")
        assert disabled.status_code == 200
        assert disabled.json() == {"enabled": True}
        monkeypatch.setenv(perf_diagnostics.PERF_ENV, "1")
        enabled = await client.get("/api/v1/search-perf/config")
        assert enabled.status_code == 200
        assert enabled.json() == {"enabled": True}

    api_env.scenario(scenario)


@pytest.mark.parametrize("provider", sorted(perf_diagnostics.SUPPORTED_PROVIDERS))
def test_search_perf_identity_reads_only_the_active_profile_generation(api_env, monkeypatch: pytest.MonkeyPatch, provider: str) -> None:
    processor = provider + "@processor-v1"
    target_space = provider + ":target-space"
    other_space = "qwen3-vl-embedding-2b:other-space"
    with api_env.service.catalog.write() as connection:
        connection.execute(
            "INSERT INTO index_generations(generation_id,space_id,processor_id,dimension,state,"
            "is_active,created_at,updated_at) VALUES(?,?,?,?, 'READY',1,1,1)",
            ("aaa-other-active", other_space, "other@processor", 2048),
        )
        connection.execute(
            "INSERT INTO index_generations(generation_id,space_id,processor_id,dimension,state,"
            "is_active,created_at,updated_at) VALUES(?,?,?,?, 'READY',0,1,1)",
            ("zzz-profile-generation", target_space, processor, 512),
        )
        connection.execute(
            "INSERT INTO search_profiles(profile_id,embedding_processor_id,space_id,"
            "image_generation_id,query_preprocess_version,query_parser_version,"
            "fts_tokenizer_version,fts_generation_id,reranker_processor_id,"
            "fusion_config_json,runtime_package_id,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,1)",
            ("profile-target", processor, target_space, "zzz-profile-generation", "query-v1",
             "parser-v1", "1", "fts-v1", None,
             '{"revision":"test","method":"RRF","parameters":{"k":60}}', "test-runtime"),
        )
        connection.execute(
            "UPDATE active_search_profile SET profile_id=?,updated_at=2 WHERE state_id=1",
            ("profile-target",),
        )

    monkeypatch.setattr(api_env.context, "query_encoder", lambda: pytest.fail("identity lookup must not resolve a worker"))

    async def scenario(client) -> None:
        unauthenticated = await client.get("/api/v1/search-perf/identity")
        assert unauthenticated.status_code == 401
        await api_env.authenticate(client)
        identity = await client.get("/api/v1/search-perf/identity")
        assert identity.status_code == 200, identity.text
        assert identity.json() == {
            "identity_confirmed": True,
            "reason": None,
            "active_profile_id": "profile-target",
            "active_image_generation_id": "zzz-profile-generation",
            "provider_id": provider,
            "processor_id": processor,
            "space_id": target_space,
            "dimension": 512,
            "generation_status": "READY",
            "binding_consistent": True,
        }

    api_env.scenario(scenario)


def test_search_perf_identity_reports_no_active_profile_without_loading_model(api_env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_env.context, "query_encoder", lambda: pytest.fail("identity lookup must not resolve a worker"))

    async def scenario(client) -> None:
        await api_env.authenticate(client)
        identity = await client.get("/api/v1/search-perf/identity")
        assert identity.status_code == 200, identity.text
        assert identity.json()["identity_confirmed"] is False
        assert identity.json()["reason"] == "NO_ACTIVE_PROFILE"

    api_env.scenario(scenario)


def test_place_aware_route_adds_diagnostic_headers_only_for_opted_in_requests(api_env, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario(client) -> None:
        csrf = await api_env.authenticate(client)
        payload = {"request_id": "perf-diag-synthetic", "retrieval_mode": "text",
                   "text": "synthetic", "original_query": "synthetic"}
        monkeypatch.delenv(perf_diagnostics.PERF_ENV, raising=False)
        headers = {**api_env.post_headers(csrf), "X-Search-Perf": "1"}
        measured = await client.post("/api/v1/search/place-aware", json=payload, headers=headers)
        assert measured.status_code == 200, measured.text
        assert "search_execution_ms;dur=" in measured.headers["Server-Timing"]
        meta = json.loads(measured.headers["X-Search-Perf-Meta"])
        assert meta["page_size"] == 20
        assert meta["first_page_hits"] == 0
        assert meta["phase_tree"]["accounting"]["unaccounted_ms"] >= 0
        assert meta["search_execution_ms"] == meta["phase_tree"]["total_ms"]
        assert isinstance(meta["catalog_queue_ms"], (int, float))
        assert isinstance(meta["catalog_open_ms"], (int, float))
        assert isinstance(meta["search_prepare_ms"], (int, float))
        assert isinstance(meta["retrieval_total_ms"], (int, float))
        assert isinstance(meta["page_render_ms"], (int, float))
        assert "synthetic" not in measured.headers["X-Search-Perf-Meta"]
        assert measured.json()["request_id"] == payload["request_id"]

        ordinary = await client.post(
            "/api/v1/search/place-aware", json={**payload, "request_id": "normal-search"},
            headers=api_env.post_headers(csrf),
        )
        assert ordinary.status_code == 200, ordinary.text
        assert "Server-Timing" not in ordinary.headers

    api_env.scenario(scenario)


def test_search_page_exposes_default_off_in_app_diagnostic_controls() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    html = (root / "src/photo_search/web/search.html").read_text(encoding="utf-8")
    script = (root / "src/photo_search/web/search.js").read_text(encoding="utf-8")
    assert 'id="search-perf-toggle" type="checkbox"' in html
    assert 'id="search-perf-start"' in html and 'id="search-perf-stop"' in html
    assert 'new URLSearchParams(window.location.search).get("perf")' not in script
    assert 'Number(sample[key])' not in script
    assert 'typeof value === "number" && Number.isFinite(value)' in script
    assert 'perfThumb(requestId, assetId, ok)' in script
    assert '"first_observed", "warmup", "warmup"' in script
    assert 'i < 20' in script
    assert 'sample.test_mode === "standard_auto" && sample.sample_type === "hot"' in script
    assert 'perfDiag.runSamplesStart = perfDiag.samples.length' in script
    assert script.count('perfCurrentRunSamples().filter') >= 3
    assert 'validHotCount === 20' in script
    assert '有效热样本仅 " + validHotCount + "/20；失败样本已保留且未重试' in script


def test_search_perf_frontend_uses_light_identity_and_server_stage_metrics() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    script = (root / "src/photo_search/web/search.js").read_text(encoding="utf-8")
    startup = script.split("function loadCapabilities()", 1)[1].split("function ", 1)[0]
    conditions = script.split("function perfConditionsSnapshot()", 1)[1].split(
        "function perfSemanticSnapshot()", 1
    )[0]
    assert "request(\"/search-perf/identity\")" in script
    assert "perfIdentityFromCapabilities" not in script
    assert "sequence !== perfDiag.identitySeq" in script
    assert "!perfDiag.enabled" in script
    assert "/search-perf/identity" not in startup
    assert "sample.server[key]" in script
    assert "typeof value === \"number\" && Number.isFinite(value)" in script
    assert "state.search.intentPlan" not in conditions
    assert "state.search.placeAware" not in conditions
    assert "perfSemanticSnapshot()" in script
    assert "actual.processor_id === expected.processor_id" in script
    assert "failure && failure.code || \"SEARCH_FAILED\"" in script


def test_perf_identity_reads_ignore_late_results_after_rapid_toggle() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    script = (root / "src/photo_search/web/search.js").read_text(encoding="utf-8")
    start = script.index("function perfIdentityFromResponse(")
    end = script.index("function perfProviderName(", start)
    identity_functions = script[start:end]
    harness = r"""
var perfDiag={enabled:true,identitySeq:0,identityChecking:false,model:null,autoRunning:false};
var pending=[],target={textContent:"",},retry={hidden:true},startButton={disabled:true};
var document={getElementById:function(id){return {"search-perf-model":target,"search-perf-identity-retry":retry,"search-perf-start":startButton}[id];}};
function request(){return new Promise(function(resolve){pending.push(resolve);});}
function perfProviderName(id){return id==="qwen3-vl-embedding-2b"?"Qwen3-VL-Embedding-2B":"Chinese-CLIP";}
function identity(profile){return {body:{identity_confirmed:true,binding_consistent:true,generation_status:"READY",
  active_profile_id:profile,active_image_generation_id:"generation-"+profile,provider_id:"qwen3-vl-embedding-2b",
  processor_id:"qwen@1",space_id:"qwen3-vl-embedding-2b:space",dimension:2048}};}
""" + identity_functions + r"""
(async function(){
  var first=perfRefreshIdentity();
  perfDiag.enabled=false; perfDiag.identitySeq+=1; perfDiag.identityChecking=false;
  target.textContent="诊断关闭";
  perfDiag.enabled=true;
  var second=perfRefreshIdentity();
  if(pending.length!==2) throw new Error("expected one identity read per enabled transition");
  pending[0](identity("old-profile")); await first;
  if(perfDiag.model!==null || target.textContent!=="正在确认当前模型…") throw new Error("late identity response overwrote current UI");
  pending[1](identity("new-profile"));
  var actual=await second;
  if(actual.active_profile_id!=="new-profile" || perfDiag.model.active_profile_id!=="new-profile") throw new Error("latest identity response was not applied");
  if(startButton.disabled || retry.hidden!==true) throw new Error("confirmed model did not enable testing");
})().catch(function(error){console.error(error);process.exit(1);});
"""
    subprocess.run(["node", "-e", harness], cwd=root, check=True, capture_output=True, text=True)


def test_perf_metrics_use_server_values_and_user_conditions_ignore_derived_state() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    script = (root / "src/photo_search/web/search.js").read_text(encoding="utf-8")
    metrics = script[script.index("function perfSampleMatchesCurrentIdentity("):script.index("function perfExport(")]
    conditions = script[script.index("function perfConditionsSnapshot()"):script.index("function freezePerfControls(")]
    harness = r"""
const assert=require("node:assert/strict");
""" + metrics + r"""
const summary=perfPercentile([
  {server:{query_encode_ms:12.5,vector_ms:8}},
  {server:{query_encode_ms:null,vector_ms:undefined}},
  {server:{query_encode_ms:NaN,vector_ms:0}},
  {server:{query_encode_ms:"4",vector_ms:null}}
],"query_encode_ms",.5);
assert.deepEqual(summary,{value:12.5,n:1});
assert.deepEqual(perfPercentile([{server:{vector_ms:null}},{server:{}}],"vector_ms",.5),{value:null,n:0});

var perfDiag={runSamplesStart:1,samples:[
  {valid:true,test_mode:"standard_auto",sample_type:"hot",server:{search_execution_ms:1}},
  {valid:true,test_mode:"standard_auto",sample_type:"hot",server:{search_execution_ms:20}},
  {valid:true,test_mode:"standard_auto",sample_type:"hot",server:{search_execution_ms:30}}
]};
assert.equal(perfCurrentRunSamples().length,2,"previous test run samples must not enter current statistics");
assert.deepEqual(perfPercentile(perfCurrentRunSamples(),"search_execution_ms",.5),{value:20,n:2});
"""
    harness += r"""
var state={search:{favoritesOnly:false,intentPlan:null,placeAware:null,selectedScope:null}};
var els={queryText:{value:"日落"},queryPageSize:{value:"20"}};
function collectFilters(){return {availability:"online",pageSize:20};}
function textRetrievalMode(){return "combined";}
""" + conditions + r"""
const initial=perfConditionsSnapshot();
state.search.intentPlan={original_query:"日落",content_query:"落日"};
state.search.placeAware={original_query:"日落",content_query:"落日",place_kind:"city",place_key:"x"};
assert.equal(perfConditionsSnapshot(),initial,"server derived intent changes must not look like user edits");
const accepted=perfSemanticSnapshot();
state.search.intentPlan={original_query:"日落",content_query:"海滩"};
assert.notEqual(perfSemanticSnapshot(),accepted,"semantic changes must remain detectable");
"""
    subprocess.run(["node", "-e", harness], cwd=root, check=True, capture_output=True, text=True)
