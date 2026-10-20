"""Bounded natural-place parsing and live search-script state checks."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from photo_search.places.natural_search import parse_place_query

CHOICES = [
    {"kind": "province", "key": "sd", "name": "山东省", "parent": "", "aliases": ["山东"]},
    {"kind": "city", "key": "jn", "name": "济南", "parent": "山东省", "aliases": ["济南市", "Jinan"]},
    {"kind": "province", "key": "zj", "name": "浙江省", "parent": "", "aliases": []},
    {"kind": "city", "key": "hz", "name": "杭州市", "parent": "浙江省", "aliases": ["杭州"]},
    {"kind": "district", "key": "cn:west", "name": "西湖区", "parent": "浙江省 · 杭州市", "aliases": []},
    {"kind": "city", "key": "qd", "name": "青岛", "parent": "山东省", "aliases": ["青岛市"]},
]


@pytest.mark.parametrize(
    ("query", "expected_place", "expected_content"),
    [
        ("济南", "jn", ""),
        ("济南。", "jn", ""),
        ("济南，夜景", "jn", "夜景"),
        ("济南的照片", "jn", ""),
        ("在济南拍的照片", "jn", ""),
        ("找一下在济南拍摄的所有照片", "jn", ""),
        ("济南的日落", "jn", "日落"),
        ("在济南拍的日落照片", "jn", "日落"),
        ("帮我找济南拍的夜景", "jn", "夜景"),
        ("济南 夜景", "jn", "夜景"),
        ("日落照片，在济南拍的", "jn", "日落"),
        ("山东省济南市拍的照片", "jn", ""),
        ("在杭州西湖区拍的日落", "cn:west", "日落"),
        ("在 济南 拍的 日落", "jn", "日落"),
        ("Jinan 的 日落", "jn", "日落"),
    ],
)
def test_natural_place_queries_preserve_content_and_map_original_offsets(
    query: str, expected_place: str, expected_content: str
) -> None:
    result = parse_place_query(query, CHOICES)
    assert result["state"] == "recognized"
    assert result["choices"][0]["key"] == expected_place
    assert result["content_query"] == expected_content


@pytest.mark.parametrize(
    "query",
    [
        "老照片",
        "照片里的文字",
        "济南路上的夜景",
        "巴黎风格的房间",
    ],
)
def test_non_location_mentions_are_not_promoted_to_place_filters(query: str) -> None:
    result = parse_place_query(query, CHOICES)
    assert result["state"] is None
    assert result["notice"] is None


@pytest.mark.parametrize("query", ["太阳", "太阳的照片", "照片里的太阳"])
def test_common_visual_collision_word_is_not_promoted_to_a_place(query: str) -> None:
    choices = [{"kind": "city", "key": "gn:sun", "name": "太阳", "parent": "", "aliases": [], "source": "GEONAMES"}]
    result = parse_place_query(query, choices)
    assert result["state"] is None
    assert result["content_query"] is None
    assert result["notice"] is None


def test_explicit_shooting_clause_can_still_select_a_visual_collision_place() -> None:
    choices = [{"kind": "city", "key": "gn:sun", "name": "太阳", "parent": "", "aliases": [], "source": "GEONAMES"}]
    result = parse_place_query("在太阳拍的照片", choices)
    assert result["state"] == "recognized"
    assert result["choices"][0]["key"] == "gn:sun"


def test_visual_collision_in_content_does_not_hide_another_explicit_place_clause() -> None:
    choices = [
        {"kind": "city", "key": "gn:sun", "name": "太阳", "parent": "", "aliases": [], "source": "GEONAMES"},
        {"kind": "city", "key": "osm:shenzhen", "name": "深圳市", "parent": "广东省", "aliases": ["深圳"], "source": "OSM"},
    ]
    result = parse_place_query("太阳的照片，在深圳拍的", choices)
    assert result["state"] == "recognized"
    assert result["choices"][0]["key"] == "osm:shenzhen"
    assert result["content_query"] == "太阳"


@pytest.mark.parametrize("query", ["不是在济南拍的照片", "济南或青岛的照片"])
def test_negative_and_multiple_locations_are_preserved_without_a_hard_filter(query: str) -> None:
    result = parse_place_query(query, CHOICES)
    assert result["state"] == "unresolved"
    assert result["choices"] == []
    assert result["notice"]


def test_real_same_name_choices_keep_their_own_content_and_parent_context() -> None:
    choices = [
        {"kind": "city", "key": "local:1", "name": "西湖", "parent": "杭州", "aliases": []},
        {"kind": "district", "key": "cn:2", "name": "西湖", "parent": "浙江省 · 杭州市", "aliases": []},
    ]
    result = parse_place_query("西湖的日落", choices)
    assert result["state"] == "ambiguous"
    assert result["content_query"] == "日落"
    assert {choice["key"]: choice["content_query"] for choice in result["choices"]} == {
        "local:1": "日落",
        "cn:2": "日落",
    }


def test_osm_is_default_for_unqualified_same_name_place() -> None:
    choices = [
        {"kind": "city", "key": "osm:shenzhen", "name": "深圳市", "parent": "广东省", "aliases": ["深圳"], "source": "OSM"},
        {"kind": "city", "key": "gn:shenzhen-tw", "name": "深圳", "parent": "台湾", "aliases": [], "source": "GEONAMES"},
    ]
    for query, content in [("深圳", ""), ("深圳的海边", "海边")]:
        result = parse_place_query(query, choices)
        assert result["state"] == "recognized"
        assert result["choices"][0]["key"] == "osm:shenzhen"
        assert result["content_query"] == content


def test_explicit_parent_selects_only_verified_child() -> None:
    choices = [
        {"kind": "city", "key": "osm:shenzhen", "name": "深圳市", "parent": "广东省", "aliases": ["深圳"], "source": "OSM"},
        {"kind": "city", "key": "gn:shenzhen-tw", "name": "深圳", "parent": "台湾", "aliases": [], "source": "GEONAMES"},
        {"kind": "province", "key": "gn:taiwan", "name": "台湾", "parent": "", "aliases": [], "source": "GEONAMES"},
    ]
    result = parse_place_query("台湾的深圳", choices)
    assert result["state"] == "recognized"
    assert result["choices"][0]["key"] == "gn:shenzhen-tw"


def test_osm_fallback_and_distinct_osm_duplicates() -> None:
    geonames = [{"kind": "city", "key": "gn:tokyo", "name": "东京", "parent": "日本", "aliases": [], "source": "GEONAMES"}]
    result = parse_place_query("东京", geonames)
    assert result["state"] == "recognized"
    assert result["choices"][0]["key"] == "gn:tokyo"

    duplicate_osm = [
        {"kind": "city", "key": "osm:1", "name": "朝阳", "parent": "甲省", "aliases": [], "source": "OSM"},
        {"kind": "district", "key": "osm:2", "name": "朝阳", "parent": "乙省", "aliases": [], "source": "OSM"},
    ]
    result = parse_place_query("朝阳", duplicate_osm)
    assert result["state"] == "ambiguous"
    assert {choice["key"] for choice in result["choices"]} == {"osm:1", "osm:2"}


def test_same_named_verified_parent_and_child_prefers_the_broader_parent() -> None:
    choices = [
        {"kind": "country", "key": "hk-country", "name": "香港", "parent": "", "aliases": [],
         "source": "GEONAMES", "dataset_id": "geo-1"},
        {"kind": "city", "key": "hk-city", "name": "香港", "parent": "香港", "aliases": [],
         "source": "GEONAMES", "dataset_id": "geo-1", "_parent_key": "hk-country"},
    ]
    result = parse_place_query("香港的照片", choices)
    assert result["state"] == "recognized"
    assert result["choices"][0]["key"] == "hk-country"
    assert "_parent_key" not in result["choices"][0]

    unrelated = [choices[0], {**choices[1], "_parent_key": "other-country"}]
    result = parse_place_query("香港的照片", unrelated)
    assert result["state"] == "ambiguous"
    assert {choice["key"] for choice in result["choices"]} == {"hk-country", "hk-city"}

    city_parent = [
        {**choices[0], "kind": "city", "key": "parent-city"},
        {**choices[1], "_parent_key": "parent-city"},
    ]
    result = parse_place_query("香港的照片", city_parent)
    assert result["state"] == "ambiguous"


def test_unavailable_parent_data_does_not_invent_a_hierarchy() -> None:
    result = parse_place_query(
        "山东省济南市拍的日落",
        [{"kind": "city", "key": "jn", "name": "济南", "parent": "", "aliases": ["济南市"]}],
    )
    assert result["state"] == "unresolved"
    assert result["choices"] == []
    assert "无法确认" in result["notice"]


def test_missing_place_directory_keeps_an_explicit_place_phrase_as_unresolved() -> None:
    result = parse_place_query("在未知地点拍的照片", [])
    assert result["state"] == "unresolved"
    assert result["choices"] == []
    assert "没有可确认的地点" in result["notice"]


def test_place_cancel_and_ambiguous_choice_use_the_current_content() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the search-page interaction check"
    script = r"""
const fs = require("fs"), vm = require("vm"), assert = require("assert/strict");
class Element {
  constructor(tag) { this.tag=tag; this.children=[]; this.events={}; this.hidden=false; this.value=""; this.className=""; }
  set textContent(value) { this.text=String(value); this.children=[]; }
  get textContent() { return this.text || this.children.map(child=>child.textContent).join(""); }
  get firstChild() { return this.children[0] || null; }
  appendChild(child) { this.children.push(child); return child; }
  removeChild(child) { this.children.splice(this.children.indexOf(child),1); }
  addEventListener(name, callback) { this.events[name]=callback; }
  fire(name) { if(this.events[name]) this.events[name]({preventDefault(){}}); }
}
const elements=new Map(), get=id=>{ if(!elements.has(id)) elements.set(id,new Element("div")); return elements.get(id); };
const sent=[];
const context={URLSearchParams,encodeURIComponent,isFinite,console,
  document:{readyState:"loading",cookie:"ps_csrf=synthetic",addEventListener(){},getElementById:get,createElement:tag=>new Element(tag)},
  window:{addEventListener(){},location:{href:"/search",search:""},sessionStorage:{removeItem(){},setItem(){},getItem(){return null;}}},
  fetch:async(_url,options)=>{if(options&&options.body) sent.push(JSON.parse(options.body)); return {ok:true,status:200,headers:{get:()=>"application/json"},json:async()=>({unsupported_intent:true,notice:"test"})};}};
let code=fs.readFileSync("src/photo_search/web/search.js","utf8");
code=code.replace('  if (document.readyState === "loading")',
 '  globalThis.ui={state,showPlaceChoices,renderIntentChips,cancelAutomaticPlace,ordinaryContentAfterPlaceCancel,sendSearch,setElements(value){els=value;}};\n  if (document.readyState === "loading")');
vm.createContext(context); vm.runInContext(code,context);
const ui=context.ui, query=get("query-text"), grid=get("grid");
  ui.setElements({resultPlaceScope:get("scope"),queryStatus:get("status"),queryText:query,resultGrid:grid,
    resultIntentScope:get("intent"),resultNotices:get("notices"),loadMore:get("more"),candidateTimeToggle:get("candidate-time"),queryActions:get("actions"),
    querySource:get("source"),queryAvailability:get("availability"),queryPlaceState:get("place-state"),
    queryPlaceIds:get("place-ids"),queryDescendants:get("descendants"),queryCaptureFrom:get("capture-from"),
    queryCaptureTo:get("capture-to"),queryPageSize:get("page-size"),queryPersonMatch:get("person-match"),
    personEcho:get("person-echo"),captureEcho:get("capture-echo"),captureState:get("capture-state"),
    searchPreviewNav:get("preview-nav"),searchPreviewPosition:get("preview-position"),
    searchPreviewPrev:get("preview-prev"),searchPreviewNext:get("preview-next")});
  get("page-size").value="20";get("availability").value="any";get("place-state").value="any";
  get("descendants").value="true";get("person-match").value="any";
ui.state.search.loading=true;
ui.showPlaceChoices({original_query:"西湖的日落",content_query:"日落",place_choices:[
 {kind:"city",key:"local:1",name:"西湖",parent:"杭州",content_query:"日落"},
 {kind:"district",key:"cn:2",name:"西湖",parent:"浙江省 · 杭州市",content_query:"日落"}]});
assert.equal(get("scope").children.length,3);
get("scope").children[1].fire("click");
assert.equal(ui.state.search.placeAware.content_query,"日落");
assert.equal(ui.state.search.placeAware.place_key,"local:1");
query.value="在济南拍的照片";
ui.state.search.sessionId="old-session"; ui.state.search.cursor="old-cursor"; ui.state.search.binding="old-binding";
ui.state.search.intentPlan={original_query:query.value,content_query:"",place_kind:"city",place_key:"jn",place_name:"济南",place_selection:{kind:"city",key:"jn",source:"OSM"},person_ids:[]};
ui.state.search.placeAware={original_query:query.value,content_query:"",place_kind:"city",place_key:"jn",place_name:"济南"};
get("scope").textContent="";
ui.renderIntentChips(ui.state.search.intentPlan);
assert.equal(get("intent").children.length,2,"plan and place echo render as one cancelable chip");
assert.equal(get("scope").children.length,0,"confirmed place echo is not rendered in a second container");
get("intent").children[1].fire("click");
assert.equal(query.value,"");
assert.equal(ui.state.search.placeAware.place_cancelled,true);
assert.equal(ui.state.search.sessionId,null); assert.equal(ui.state.search.cursor,null); assert.equal(ui.state.search.binding,null);
assert.equal(ui.state.search.intentPlan,null,"a pure place plan is not retained after cancellation");
assert.equal(get("status").textContent,"地点条件已取消，请输入内容后搜索。");
assert.equal(ui.ordinaryContentAfterPlaceCancel({original_query:"太阳的照片",place_name:"太阳"}),"太阳");
assert.equal(ui.ordinaryContentAfterPlaceCancel({original_query:"在深圳拍的照片",place_name:"深圳"}),"");
query.value="太阳";
ui.state.search.placeAware={original_query:"太阳的照片",content_query:"",place_kind:"city",place_key:"gn:sun",place_name:"太阳"};
ui.state.search.sessionId="stale-session";ui.state.search.cursor="stale-cursor";ui.state.search.binding="stale-binding";
ui.cancelAutomaticPlace(ui.state.search.placeAware,null);
assert.equal(query.value,"太阳","cancelling a colliding place keeps the name as ordinary content");
assert.equal(ui.state.search.placeAware.place_cancelled,true);
assert.equal(ui.state.search.sessionId,null);assert.equal(ui.state.search.cursor,null);assert.notEqual(ui.state.search.binding,"stale-binding");
const cancelled=sent.find(body=>body.place_cancelled);
assert.ok(cancelled,"the retry payload carries the place-cancelled tombstone");
assert.equal(cancelled.content_query,"太阳");assert.equal(cancelled.text,"太阳");
;(async()=>{
  let resolveFetch;
  context.fetch=(_url,options)=>{
    if(options&&options.body) sent.push(JSON.parse(options.body));
    return new Promise(resolve=>{resolveFetch=resolve;});
  };
  query.value="在济南拍的照片";
  ui.state.search.placeAware={original_query:query.value,content_query:"",place_kind:"city",place_key:"jn",place_name:"济南"};
  ui.state.search.intentPlan={original_query:query.value,content_query:"",place_kind:"city",place_key:"jn",place_name:"济南",person_ids:[]};
  const oldResponse=ui.sendSearch(false);
  assert.equal(typeof resolveFetch,"function","the old request is held in flight");
  ui.cancelAutomaticPlace(ui.state.search.placeAware,ui.state.search.intentPlan);
  const cancelStatus=get("status").textContent;
  resolveFetch({ok:true,status:200,headers:{get:()=>"application/json"},
    json:async()=>({unsupported_intent:true,notice:"stale response must not replace cancellation state"})});
  await oldResponse;
  assert.equal(ui.state.search.placeAware.place_cancelled,true,"late response cannot restore the cancelled place");
  assert.equal(ui.state.search.intentPlan,null,"late response cannot restore the old intent plan");
  assert.equal(get("status").textContent,cancelStatus,"late success cannot replace the cancellation notice");
  assert.equal(ui.state.search.sessionId,null);assert.equal(ui.state.search.cursor,null);
  assert.equal(ui.state.search.hits.length,0);assert.equal(get("grid").children.length,0);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [node, "-e", script], cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=10
    )
    assert result.returncode == 0, result.stderr
