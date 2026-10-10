"""Bounded result-window behavior of the current search-page script."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_filter_pages_replace_the_dom_and_saved_snapshot_stays_page_bounded() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the search-page interaction check"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.events={};this.hidden=false;this.value="";this.dataset={};this.disabled=false;}
  set textContent(value){this.text=String(value);this.children=[];}
  get textContent(){return this.text||this.children.map(child=>child.textContent).join("");}
  get firstChild(){return this.children[0]||null;}
  appendChild(child){this.children.push(child);return child;}
  removeChild(child){this.children.splice(this.children.indexOf(child),1);return child;}
  addEventListener(name,callback){this.events[name]=callback;}
  setAttribute(name,value){this[name]=String(value);}
  fire(name){if(this.events[name])this.events[name]({preventDefault(){}});}
}
const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element("div"));return elements.get(id);};
const storage=new Map();
const context={URLSearchParams,encodeURIComponent,isFinite,console,URL:{revokeObjectURL(){}},
 document:{readyState:"loading",cookie:"",addEventListener(){},getElementById:get,createElement:tag=>new Element(tag)},
 window:{sessionStorage:{setItem(k,v){storage.set(k,String(v));},getItem(k){return storage.get(k)||null;},removeItem(k){storage.delete(k);}},scrollY:0,location:{href:"/search",search:""},requestAnimationFrame(fn){fn();},scrollTo(){}},
 fetch:async()=>({ok:true,status:200,headers:{get:()=>"application/json"},json:async()=>({})})};
let code=fs.readFileSync("src/photo_search/web/search.js","utf8");
code=code.replace('  if (document.readyState === "loading")','  globalThis.ui={state,renderPage,renderIntentChips,submitSearch,setElements(value){els=value;}};\n  if (document.readyState === "loading")');
vm.createContext(context);vm.runInContext(code,context);
const ui=context.ui;
const ids=["resultPlaceScope","resultIntentScope","resultGrid","resultNotices","loadMore","queryActions","queryStatus","queryText","querySource","queryAvailability","queryPlaceState","queryPlaceIds","queryDescendants","queryCaptureFrom","queryCaptureTo","queryPageSize","queryPersonMatch","personEcho","captureEcho","captureState","searchPreviewNav","searchPreviewPosition","searchPreviewPrev","searchPreviewNext"];
const els={};ids.forEach(id=>els[id]=get(id));
els.queryPageSize.value="100";els.queryAvailability.value="any";els.queryPlaceState.value="any";els.queryDescendants.value="true";els.queryPersonMatch.value="any";
ui.setElements(els);ui.state.search.mode="text";ui.state.observer={observe(){}};
const makePage=(start,more)=>({search_session_id:"filter-only",page_offset:start,page_size:100,total_candidates:5005,has_more:more,next_cursor:more?"cursor-"+(start+100):null,
 hits:Array.from({length:Math.min(100,5005-start)},(_,i)=>({asset_id:start+i+1,rank:start+i+1,hit_reason:"FILTER_ONLY",matched_by:{filter:start+i+1}})),
 filters:{source_id:null,availability:"any",place_ids:[],include_descendants:true,place_state:"any",person_ids:[],person_match:"any",capture_from:null,capture_to_exclusive:null},
 channels:{fts:{},vector:{},authoritative:{order:"asset_id"}},search_projection:{state:"SYNCED"},pending_semantic:null,state:"OK"});
for(let page=0;page<51;page++){
 ui.renderPage(makePage(page*100,page<50),page>0);
 assert.ok(els.resultGrid.children.length<=100,"DOM must hold at most one bounded page");
 assert.ok(ui.state.search.hits.length<=100,"in-memory preview list must hold at most one bounded page");
 const snapshot=JSON.parse(storage.get("aisearch.search-return.v1"));
 assert.ok(snapshot.hits.length<=100,"return snapshot must not grow with total results");
}
assert.equal(ui.state.search.count,5005);assert.equal(ui.state.search.cursor,null);
assert.match(els.queryStatus.textContent,/符合条件共 5005 张/);assert.match(els.queryStatus.textContent,/已到末尾/);
"""
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [node, "-e", script], cwd=root, capture_output=True, text=True,
        encoding="utf-8", timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_intent_cancel_keeps_content_and_new_search_drops_stale_chips() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the search-page interaction check"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.events={};this.hidden=false;this.value="";this.dataset={};this.disabled=false;}
  set textContent(value){this.text=String(value);this.children=[];}
  get textContent(){return this.text||this.children.map(child=>child.textContent).join("");}
  get firstChild(){return this.children[0]||null;}
  appendChild(child){this.children.push(child);return child;}
  removeChild(child){this.children.splice(this.children.indexOf(child),1);return child;}
  addEventListener(name,callback){this.events[name]=callback;}
  setAttribute(name,value){this[name]=String(value);}
  fire(name){if(this.events[name])this.events[name]({preventDefault(){}});}
}
const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element("div"));return elements.get(id);};
const storage=new Map();
const context={URLSearchParams,encodeURIComponent,isFinite,console,URL:{revokeObjectURL(){}},
 document:{readyState:"loading",cookie:"",addEventListener(){},getElementById:get,createElement:tag=>new Element(tag)},
 window:{sessionStorage:{setItem(k,v){storage.set(k,String(v));},getItem(k){return storage.get(k)||null;},removeItem(k){storage.delete(k);}},scrollY:0,location:{href:"/search",search:""},requestAnimationFrame(fn){fn();},scrollTo(){}},
 fetch:()=>new Promise(()=>{})};
let code=fs.readFileSync("src/photo_search/web/search.js","utf8");
code=code.replace('  if (document.readyState === "loading")','  globalThis.ui={state,renderIntentChips,submitSearch,setElements(value){els=value;}};\n  if (document.readyState === "loading")');
vm.createContext(context);vm.runInContext(code,context);
const ui=context.ui;
const ids=["resultPlaceScope","resultIntentScope","resultGrid","resultNotices","loadMore","queryActions","queryStatus","queryText","querySource","queryAvailability","queryPlaceState","queryPlaceIds","queryDescendants","queryCaptureFrom","queryCaptureTo","queryPageSize","queryPersonMatch","personEcho","captureEcho","captureState","searchPreviewNav","searchPreviewPosition","searchPreviewPrev","searchPreviewNext"];
const els={};ids.forEach(id=>els[id]=get(id));
els.queryText.value="2026年爸爸日落照片";els.queryPageSize.value="20";els.queryAvailability.value="any";els.queryPlaceState.value="any";els.queryDescendants.value="true";els.queryPersonMatch.value="any";
ui.setElements(els);
ui.state.person.rows["19"]={person_id:19,name:"爸爸"};
ui.renderIntentChips({person_ids:[19],person_names:[],person_match:"any"});
assert.equal(els.resultIntentScope.children[0].textContent,"人物：爸爸 ","missing chip names should use the loaded catalog name, not the internal id");
const plan={collection_scope:{collection_id:7,instance_token:"token",revision:2,name:"同事"},capture_from:"2026-01-01",capture_to_exclusive:"2027-01-01",person_ids:[7],person_names:["爸爸"],person_match:"any",place_kind:"city",place_key:"3",place_name:"济南",place_selection:{kind:"city",key:"3"},source_id:4,source_name:"手机备份图库",content_query:"日落"};
ui.state.search.intentPlan=plan;ui.renderIntentChips(plan);
els.resultIntentScope.children[1].fire("click");
assert.equal(plan.collection_scope,null,"cancelling the collection must remove only its own scope");
assert.equal(plan.capture_from,"2026-01-01","cancelling collection keeps the date scope");
assert.equal(els.queryText.value,"日落","cancelling one condition must preserve the remaining content query");
ui.renderIntentChips(plan);els.resultIntentScope.children[3].fire("click");
ui.renderIntentChips(plan);els.resultIntentScope.children[3].fire("click");
assert.equal(plan.person_ids.length,0,"cancelling people keeps the other committed conditions");
ui.renderIntentChips(plan);els.resultIntentScope.children[3].fire("click");
assert.equal(plan.place_kind,null);assert.equal(plan.place_selection,null,"place identity must clear with its chip");
assert.equal(plan.source_id,null,"cancelling a source chip removes only its source filter");
ui.state.search.loading=false;els.queryText.value="2026年爸爸日落照片";ui.renderIntentChips(plan);ui.submitSearch();
assert.equal(els.resultIntentScope.children.length,0,"a new query must clear condition chips from the previous search");
"""
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [node, "-e", script], cwd=root, capture_output=True, text=True,
        encoding="utf-8", timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_same_name_person_scope_choices_have_person_heading_and_distinct_labels() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the search-page interaction check"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.events={};this.hidden=false;this.value="";this.dataset={};}
  set textContent(value){this.text=String(value);this.children=[];}
  get textContent(){return this.text||this.children.map(child=>child.textContent).join("");}
  get firstChild(){return this.children[0]||null;}
  appendChild(child){this.children.push(child);return child;}
  removeChild(child){this.children.splice(this.children.indexOf(child),1);return child;}
  addEventListener(name,callback){this.events[name]=callback;}
}
const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element("div"));return elements.get(id);};
const context={URLSearchParams,encodeURIComponent,isFinite,console,
 document:{readyState:"loading",cookie:"",addEventListener(){},getElementById:get,createElement:tag=>new Element(tag)},
 window:{location:{href:"/search",search:""}}};
let code=fs.readFileSync("src/photo_search/web/search.js","utf8");
code=code.replace('  if (document.readyState === "loading")',
 '  globalThis.ui={state,showScopeChoices,setElements(value){els=value;}};\n  if (document.readyState === "loading")');
vm.createContext(context);vm.runInContext(code,context);
const scope=get("scope"),status=get("status");
context.ui.setElements({resultIntentScope:scope,queryStatus:status});
context.ui.showScopeChoices({scope_choices:[
 {type:"person",label:"人物：SZ #1",intent_plan:{person_ids:[1]}},
 {type:"person",label:"人物：SZ #2",intent_plan:{person_ids:[2]}}
]});
assert.equal(scope.children[0].textContent,"该名称对应多个人物，请选择：");
assert.equal(scope.children[1].textContent,"人物：SZ #1");
assert.equal(scope.children[2].textContent,"人物：SZ #2");
context.ui.showScopeChoices({scope_choices:[
 {type:"place",label:"地点：香港（广东省 · 深圳市 · OSM）"},
 {type:"place",label:"地点：香港（香港特别行政区 · OSM）"}
]});
assert.equal(scope.children[0].textContent,"该名称对应多个地点，请选择：");
assert.equal(scope.children[1].textContent,"地点：香港（广东省 · 深圳市 · OSM）");
context.ui.showScopeChoices({scope_choices:[
 {type:"source",label:"图库来源：手机图库 #1"},
 {type:"source",label:"图库来源：手机图库 #2"}
]});
assert.equal(scope.children[0].textContent,"该名称对应多个图库来源，请选择：");
"""
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [node, "-e", script], cwd=root, capture_output=True, text=True,
        encoding="utf-8", timeout=10,
    )
    assert result.returncode == 0, result.stderr
