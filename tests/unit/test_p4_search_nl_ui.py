"""Bounded result-window behavior of the current search-page script."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_filter_pages_append_and_saved_snapshot_keeps_loaded_results() -> None:
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
code=code.replace('  if (document.readyState === "loading")','  globalThis.ui={state,renderPage,renderIntentChips,submitSearch,loadCandidateTimeBrowse,setElements(value){els=value;}};\n  if (document.readyState === "loading")');
vm.createContext(context);vm.runInContext(code,context);
const ui=context.ui;
const ids=["resultPlaceScope","resultIntentScope","resultGrid","resultNotices","loadMore","candidateTimeToggle","queryActions","queryStatus","queryText","querySource","queryAvailability","queryPlaceState","queryPlaceIds","queryDescendants","queryCaptureFrom","queryCaptureTo","queryPageSize","queryPersonMatch","personEcho","captureEcho","captureState","searchPreviewNav","searchPreviewPosition","searchPreviewPrev","searchPreviewNext"];
const els={};ids.forEach(id=>els[id]=get(id));
els.queryPageSize.value="100";els.queryAvailability.value="any";els.queryPlaceState.value="any";els.queryDescendants.value="true";els.queryPersonMatch.value="any";
ui.setElements(els);ui.state.search.mode="text";ui.state.observer={observe(){}};
const makePage=(start,more,session)=>({search_session_id:session,page_offset:start,page_size:100,total_candidates:205,has_more:more,next_cursor:more?"cursor-"+(start+100):null,
 hits:Array.from({length:Math.min(100,205-start)},(_,i)=>({asset_id:start+i+1,rank:start+i+1,hit_reason:"FILTER_ONLY",matched_by:{filter:start+i+1}})),
 filters:{source_id:null,availability:"any",place_ids:[],include_descendants:true,place_state:"any",person_ids:[],person_match:"any",capture_from:null,capture_to_exclusive:null},
 channels:{fts:{},vector:{},authoritative:{order:"asset_id"}},search_projection:{state:"SYNCED"},pending_semantic:null,state:"OK"});
for(const session of ["filter-only","place-album:test"]){
 for(let page=0;page<3;page++){
  const loaded=Math.min((page+1)*100,205);
  ui.renderPage(makePage(page*100,page<2,session),page>0);
  assert.equal(els.resultGrid.children.length,loaded,"a continued search must keep previous result cards");
  assert.equal(ui.state.search.hits.length,loaded,"preview navigation must include every loaded page");
  const snapshot=JSON.parse(storage.get("aisearch.search-return.v1"));
  assert.equal(snapshot.hits.length,loaded,"return snapshot must retain all loaded result pages");
 }
 assert.equal(ui.state.search.count,205);assert.equal(ui.state.search.cursor,null);
 assert.match(els.queryStatus.textContent,/符合条件共 205 张/);assert.match(els.queryStatus.textContent,/已加载 205 张/);
}
const semantic=makePage(0,true,"semantic-session");semantic.query={text:"coast"};
ui.renderPage(semantic,false);
assert.equal(els.candidateTimeToggle.hidden,false,"semantic sessions expose the bounded time-order view");
let resolveTimeRequest;
context.fetch=()=>new Promise(resolve=>{resolveTimeRequest=resolve;});
ui.state.search.timeBrowse.active=true;
ui.state.search.requestGeneration=4;
const staleTimeRequest=ui.loadCandidateTimeBrowse(false);
assert.equal(typeof resolveTimeRequest,"function");
ui.state.search.requestGeneration=5;
ui.state.search.sessionId="new-search-session";
ui.state.search.timeBrowse.active=false;
ui.state.search.hits=[{asset_id:999}];
resolveTimeRequest({ok:true,status:200,headers:{get:()=>"application/json"},json:async()=>({assets:[{asset_id:1,semantic_rank:1}],total_candidates:1})});
staleTimeRequest.then(()=>{
 assert.equal(ui.state.search.hits[0].asset_id,999,"a late time-page response cannot replace a newer search");
}).catch(error=>{console.error(error);process.exitCode=1;});
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
const ids=["resultPlaceScope","resultIntentScope","resultGrid","resultNotices","loadMore","candidateTimeToggle","queryActions","queryStatus","queryText","querySource","queryAvailability","queryPlaceState","queryPlaceIds","queryDescendants","queryCaptureFrom","queryCaptureTo","queryPageSize","queryPersonMatch","personEcho","captureEcho","captureState","searchPreviewNav","searchPreviewPosition","searchPreviewPrev","searchPreviewNext"];
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


def test_new_search_generation_ignores_stale_success_choice_and_error() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the search-page interaction check"
    script = r"""
(async()=>{
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
}
const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element("div"));return elements.get(id);};
const storage=new Map(),pending=[],rendered=[],failures=[];
const context={URLSearchParams,encodeURIComponent,isFinite,console,URL:{revokeObjectURL(){}},
 document:{readyState:"loading",cookie:"ps_csrf=synthetic",addEventListener(){},getElementById:get,createElement:tag=>new Element(tag)},
 window:{sessionStorage:{setItem(k,v){storage.set(k,String(v));},getItem(k){return storage.get(k)||null;},removeItem(k){storage.delete(k);}},scrollY:0,location:{href:"/search",search:""},requestAnimationFrame(fn){fn();},scrollTo(){}},
 fetch:(url,options)=>new Promise((resolve,reject)=>pending.push({url,options,resolve,reject}))};
let code=fs.readFileSync("src/photo_search/web/search.js","utf8");
code=code.replace('  if (document.readyState === "loading")',
 '  globalThis.ui={state,submitSearch,setElements(value){els=value;}};\n  if (document.readyState === "loading")');
code=code.replace('        renderPage(body, continuation);','        globalThis.rendered.push(body);');
code=code.replace('        onSearchFailure(failure);','        globalThis.failures.push(failure);');
context.rendered=rendered;context.failures=failures;
vm.createContext(context);vm.runInContext(code,context);
const ui=context.ui;
const ids=["resultPlaceScope","resultIntentScope","resultGrid","resultNotices","loadMore","candidateTimeToggle","queryActions","queryStatus","queryText","querySource","queryAvailability","queryPlaceState","queryPlaceIds","queryDescendants","queryCaptureFrom","queryCaptureTo","queryPageSize","queryPersonMatch","personEcho","captureEcho","captureState","searchPreviewNav","searchPreviewPosition","searchPreviewPrev","searchPreviewNext"];
const els={};ids.forEach(id=>els[id]=get(id));
els.queryPageSize.value="20";els.queryAvailability.value="any";els.queryPlaceState.value="any";els.queryDescendants.value="true";els.queryPersonMatch.value="any";
ui.setElements(els);
function search(query){els.queryText.value=query;ui.submitSearch();}
function reply(index,body){pending[index].resolve({ok:true,status:200,headers:{get:()=>"application/json"},json:async()=>body});}
search("A");assert.equal(pending.length,1);
search("B");assert.equal(pending.length,2,"a new query must start while A is still loading");
reply(0,{intent_plan:{original_query:"A",content_query:"A"},hits:[]});
reply(1,{intent_plan:{original_query:"B",content_query:"B"},hits:[]});
 await new Promise(resolve=>setTimeout(resolve,0));
assert.equal(ui.state.search.intentPlan.original_query,"B");
assert.deepEqual(rendered.map(body=>body.intent_plan.original_query),["B"]);

search("C");search("D");
reply(3,{intent_plan:{original_query:"D",content_query:"D"},hits:[]});
pending[2].reject({code:"OLD_FAILURE",message:"stale failure"});
 await new Promise(resolve=>setTimeout(resolve,0));
assert.equal(ui.state.search.intentPlan.original_query,"D");
assert.equal(failures.length,0,"an older error must not replace the current search state");
assert.deepEqual(rendered.map(body=>body.intent_plan.original_query),["B","D"]);

search("E");search("F");
reply(4,{needs_scope_choice:true,scope_choices:[{type:"person",label:"old choice"}]});
reply(5,{intent_plan:{original_query:"F",content_query:"F"},hits:[]});
 await new Promise(resolve=>setTimeout(resolve,0));
assert.equal(ui.state.search.intentPlan.original_query,"F");
assert.deepEqual(rendered.map(body=>body.intent_plan.original_query),["B","D","F"]);
assert.equal(els.resultIntentScope.children.length,0,"a stale disambiguation must not replace current results");
})().catch(error=>{console.error(error);process.exitCode=1;});
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
