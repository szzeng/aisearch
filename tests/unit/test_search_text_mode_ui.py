"""Exercise literal-text mode through the real search-page event handlers."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_text_mode_switch_paging_and_validated_return_keep_the_query_contract() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for search interaction checks"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.events={};this.value="";this.hidden=false;this.dataset={};this.disabled=false;}
  set textContent(value){this.text=String(value);this.children=[];}
  get textContent(){return (this.text||"")+this.children.map(child=>child.textContent).join("");}
  get firstChild(){return this.children[0]||null;}
  appendChild(child){child.parentNode=this;this.children.push(child);return child;}
  append(...children){children.forEach(child=>this.appendChild(child));}
  removeChild(child){this.children.splice(this.children.indexOf(child),1);return child;}
  addEventListener(name,callback){(this.events[name]||=[]).push(callback);}
  setAttribute(name,value){this[name]=String(value);}
  querySelector(selector){
    const asset=selector.match(/^\[data-asset-id="(\d+)"\]$/);
    const matches=child=>asset?child.dataset.assetId===asset[1]:selector.startsWith(".")&&child.className===selector.slice(1);
    for(const child of this.children){if(matches(child)){if(child.tag==="button")return child;const nested=child.querySelector(selector);if(nested)return nested;return child;}const nested=child.querySelector(selector);if(nested)return nested;}
    return null;
  }
  focus(){}
  fire(name){for(const listener of this.events[name]||[])listener({preventDefault(){},target:this});}
}
const flush=async()=>{for(let i=0;i<3;i++)await new Promise(setImmediate);};
const ok=body=>({ok:true,status:200,headers:{get:()=>"application/json"},json:async()=>body});
const scope={kind:"collection",collection_id:9,instance_token:"fixture-instance",revision:4,name:"票据"};
function page(body,assetId,cursor="text-next"){
  return {retrieval_mode:body.retrieval_mode||"combined",query:{text:body.text},
    search_session_id:body.search_session_id||"session-"+(body.retrieval_mode||"combined"),
    next_cursor:cursor,has_more:Boolean(cursor),total_candidates:3,
    hits:[{asset_id:assetId,rank:body.cursor?2:1,hit_reason:"TEXT",matched_by:{fts:1},matched_text:["filename_text","ocr_text"]}],
    filters:{source_id:body.source_id,availability:body.availability,place_ids:body.place_ids,
      include_descendants:body.include_descendants,place_state:body.place_state,
      person_ids:body.person_ids,person_match:body.person_match,
      capture_from:body.capture_from,capture_to_exclusive:body.capture_to_exclusive},
    search_scope:body.search_scope,channels:{fts:{engaged:true},vector:{engaged:false}},
    search_projection:{state:"SYNCED"},state:"OK"};
}
function harness(storage=new Map()){
  const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element("div"));return elements.get(id);};
  const calls=[],viewers=[];let answer=body=>ok(page(body,body.cursor?202:201));
  for(const [id,value] of Object.entries({"query-retrieval-mode":"combined","query-availability":"any",
    "query-place-state":"any","query-descendants":"true","query-page-size":"1","query-person-match":"any"}))get(id).value=value;
  const context={URLSearchParams,URL,console,encodeURIComponent,isFinite,
    IntersectionObserver:class{observe(){}unobserve(){}disconnect(){}},
    document:{readyState:"loading",cookie:"ps_csrf=fixture-csrf",addEventListener(){},getElementById:get,createElement:tag=>new Element(tag)},
    window:{location:{href:"http://127.0.0.1/search",origin:"http://127.0.0.1",pathname:"/search",search:"",hash:"",
      replace(path){this.href=new URL(path,this.href).href;}},scrollY:87,
      requestAnimationFrame:fn=>fn(),scrollTo(){},
      sessionStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,String(v)),removeItem:k=>storage.delete(k)}},
    fetch:async(url,options)=>{
      calls.push({url,options,body:options&&options.body?JSON.parse(options.body):null});
      if(url.endsWith("/capabilities/page"))return ok({schema_version:27,schema_status:"ok",search:{query_encoder_available:false}});
      if(url.endsWith("/sources/summary"))return ok({sources:[{source_id:3,display_name:"合成票据"}]});
      if(url.includes("/persons?"))return ok({persons:[],has_more:false});
      if(url.endsWith("/search/place-aware"))return answer(JSON.parse(options.body));
      if(url.includes("/search/sessions/")&&url.includes("/time?limit=1"))return ok({taken_time_rules_version:1});
      if(/^\/api\/v1\/assets\/\d+$/.test(url))return ok({asset_id:Number(url.split("/").pop()),format:"jpeg"});
      throw new Error("Unexpected request: "+url);
    }};
  let code=fs.readFileSync("src/photo_search/web/search.js","utf8");
  code=code.replace('  if (document.readyState === "loading")',
    '  globalThis.ui={state,boot,sendSearch};\n  if (document.readyState === "loading")');
  vm.createContext(context);vm.runInContext(fs.readFileSync("src/photo_search/web/photo-info.js","utf8"),context);
  // The shared viewer owns its DOM; this test exercises the real search-page
  // callbacks and the shared full-detail navigation/storage helper.
  context.window.AIPhotoInfo.open=(id,options)=>{
    const viewer={assetId:id,dialog:{open:true},returnFocus:options.returnFocus,
      close(){this.dialog.open=false;options.onClose();},setAsset(value){this.assetId=value;},
      setPosition(value){this.position=value;},setNavigationEnabled(previous,next){this.previous=previous;this.next=next;},
      favoriteChanged(result){options.onFavoriteChanged(id,result);},
      nextPhoto(){options.navigation.next();},
      openDetails(){options.onOpenFullDetails(this.assetId);this.close();}};
    viewers.push(viewer);return viewer;
  };
  vm.runInContext(code,context);
  context.ui.boot();
  return {get,calls,storage,viewers,ui:context.ui,setAnswer:fn=>{answer=fn;},searchCalls:()=>calls.filter(call=>call.url.endsWith("/search/place-aware"))};
}
function assertLiteral(request){
  assert.equal(request.retrieval_mode,"text");
  assert.equal(request.text,"深圳 票据 001");assert.equal(request.original_query,"深圳 票据 001");
  for(const key of ["intent_plan","content_query","place_kind","place_key","place_cancelled"])
    assert.equal(Object.hasOwn(request,key),false,"literal text must not resend inferred condition "+key);
  assert.deepEqual(request.search_scope,scope,"explicit album scope is retained");
  assert.equal(request.source_id,3);assert.deepEqual(request.place_ids,[5]);
  assert.deepEqual(request.person_ids,[7]);assert.equal(request.person_match,"all");
  assert.equal(request.capture_from,"2026-01-01");assert.equal(request.capture_to_exclusive,"2026-02-01");
}
(async()=>{
  const ordinary=harness();await flush();
  ordinary.setAnswer(body=>ok({...page(body,303,null),hits:[{asset_id:303,rank:33,
    hit_reason:"SEMANTIC",semantic_ready:true,matched_by:{vector:33},matched_text:["ocr_text"]}]}));
  ordinary.get("search-form").fire("submit");await flush();
  assert.match(ordinary.get("result-grid").textContent,/第 33 名/,
    "ordinary semantic results retain the overall ranking");
  assert.match(ordinary.get("result-grid").textContent,/匹配位置：图片文字/);
  assert.doesNotMatch(ordinary.get("result-grid").textContent,/语义就绪|通道名次|向量通道名次/);

  const h=harness();await flush();
  assert.equal(h.ui.state.capabilities.search.query_encoder_available,false);
  h.get("query-text").value="  深圳 票据 001  ";h.get("query-source").value="3";
  h.get("query-place-ids").value="5";h.get("query-person-match").value="all";
  h.get("query-capture-from").value="2026-01-01";h.get("query-capture-to").value="2026-02-01";
  h.ui.state.person.selected["7"]={person_id:7,name:"合成人物"};h.ui.state.search.selectedScope=scope;
  let releaseCombined;
  h.setAnswer(body=>new Promise(resolve=>{releaseCombined=()=>resolve(ok({...page(body,101),
    intent_plan:{content_query:"旧查询"},place_scope:{kind:"city",key:"old-city"}}));}));
  h.get("search-form").fire("submit");
  assert.equal(h.searchCalls().length,1);assert.equal(h.searchCalls()[0].body.retrieval_mode,undefined,"combined remains the compatible default");
  assert.equal(h.ui.state.search.loading,true);
  h.setAnswer(body=>ok(page(body,body.cursor?202:201,body.cursor?"text-last":"text-next")));
  h.get("query-retrieval-mode").value="text";h.get("query-retrieval-mode").fire("change");await flush();
  assert.equal(h.searchCalls().length,2,"mode change starts a fresh search while the prior request is pending");
  const firstText=h.searchCalls()[1];assertLiteral(firstText.body);
  assert.equal(firstText.options.headers["X-CSRF-Token"],"fixture-csrf");
  assert.equal(firstText.options.method,"POST");
  assert.equal(Object.hasOwn(firstText.body,"cursor"),false);
  assert.equal(Object.hasOwn(firstText.body,"search_session_id"),false);
  releaseCombined();await flush();
  assert.equal(h.ui.state.search.sessionId,"session-text");
  assert.deepEqual(Array.from(h.ui.state.search.hits,hit=>hit.asset_id),[201],"late combined response cannot replace literal results");
  assert.equal(h.ui.state.search.intentPlan,null);assert.equal(h.ui.state.search.placeAware,null);
  assert.match(h.get("candidate-time-toggle").textContent,/文字结果/);
  assert.match(h.get("result-notices").textContent,/本次按文字找/);
  assert.doesNotMatch(h.get("result-grid").textContent,/语义就绪/,
    "literal-text results must not imply they need semantic vectors");
  assert.match(h.get("result-grid").textContent,/匹配位置：文件名、图片文字/);
  assert.doesNotMatch(h.get("result-grid").textContent,/filename_text|ocr_text|通道名次/);
  await h.ui.sendSearch(true);
  assertLiteral(h.searchCalls()[2].body);assert.equal(h.searchCalls()[2].body.cursor,"text-next");
  assert.equal(h.searchCalls()[2].body.search_session_id,"session-text");
  assert.deepEqual(Array.from(h.ui.state.search.hits,hit=>hit.asset_id),[201,202]);
  h.get("result-grid").querySelector('[data-asset-id="202"]').fire("click");await flush();
  assert.equal(h.viewers.at(-1).assetId,202);assert.equal(h.ui.state.search.preview.assetId,202);
  h.viewers.at(-1).openDetails();
  const saved=JSON.parse(h.storage.get("aisearch.search-return.v1"));
  assert.equal(saved.previewAssetId,202,"full-detail navigation must save the currently previewed asset");
  assert.equal(JSON.parse(h.storage.get("aisearch.photo-detail-return.v1")).source,"search");
  assert.equal(saved.form.retrievalMode,"text");assert.equal(saved.hits.length,2);
  h.get("query-retrieval-mode").value="combined";
  const count=h.searchCalls().length;
  assert.equal(await h.ui.sendSearch(true),false,"old cursor is refused if controls changed before their change event");
  assert.equal(h.searchCalls().length,count,"mode mismatch must not reach the backend");

  const returned=harness(new Map([["aisearch.search-return.v1",JSON.stringify(saved)]]));await flush();
  assert.equal(returned.get("query-retrieval-mode").value,"text");
  assert.equal(returned.get("query-text").value,"  深圳 票据 001  ");
  assert.deepEqual(Array.from(returned.ui.state.search.hits,hit=>hit.asset_id),[201,202]);
  assert.equal(returned.calls.filter(call=>call.url.includes("/time?limit=1")).length,1,"return validates the session before restoring results");
  assert.equal(returned.searchCalls().length,0,"validated return does not rerun the query");
  assert.equal(returned.viewers.length,1,"returning from full details reopens the previous preview");
  assert.equal(returned.viewers[0].assetId,202);assert.equal(returned.viewers[0].dialog.open,true);
  assert.equal(returned.viewers[0].returnFocus.className,"tile-link","restored viewer uses a real current result trigger");
  assert.match(returned.viewers[0].position,/第 2 张/);
  returned.setAnswer(body=>ok(page(body,203,null)));returned.viewers[0].nextPhoto();await flush();
  assertLiteral(returned.searchCalls()[0].body);assert.equal(returned.searchCalls()[0].body.cursor,"text-last");
  assert.deepEqual(Array.from(returned.ui.state.search.hits,hit=>hit.asset_id),[201,202,203]);
  assert.equal(returned.ui.state.search.cursor,null);
  assert.equal(returned.viewers[0].assetId,203,"next photo at the loaded boundary waits for successful continuation");
  assert.match(returned.viewers[0].position,/第 3 张/);

  returned.ui.state.search.mode="image";const beforeImageSwitch=returned.searchCalls().length;
  returned.get("query-retrieval-mode").value="combined";returned.get("query-retrieval-mode").fire("change");await flush();
  assert.equal(returned.searchCalls().length,beforeImageSwitch,"selecting a text mode does not resubmit an image query");
  assert.deepEqual(Array.from(returned.ui.state.search.hits,hit=>hit.asset_id),[201,202,203]);

  const unavailable=harness();await flush();unavailable.get("query-text").value="票据";
  for(const [code,reason] of [["VALIDATION_ERROR","SEARCH_PROFILE_REQUIRED"],
    ["RUNTIME_CAPABILITY_UNAVAILABLE","QUERY_ENCODER_UNAVAILABLE"]]){
    unavailable.setAnswer(()=>({ok:false,status:422,headers:{get:()=>"application/json"},
      json:async()=>({code,message:"Model unavailable",details:{reason}})}));
    unavailable.get("search-form").fire("submit");await flush();
    assert.match(unavailable.get("query-status").textContent,/按文字找/,
      "a missing semantic model must point to an available retrieval method");
    assert.doesNotMatch(unavailable.get("query-status").textContent,/筛选条件.*错误|Model unavailable/);
  }
  const favorites=harness();await flush();
  favorites.get("query-text").value="sunset";
  favorites.get("query-favorites-only").checked=true;
  const beforeToggle=favorites.searchCalls().length;
  favorites.get("query-favorites-only").fire("change");
  assert.equal(favorites.searchCalls().length,beforeToggle,"changing the favorites filter never searches automatically");
  assert.equal(favorites.ui.state.search.favoritesOnly,true);
  assert.match(favorites.get("result-intent-scope").textContent,/仅搜索收藏/);
  favorites.get("search-form").fire("submit");await flush();
  assert.equal(favorites.searchCalls().at(-1).body.favorites_only,true,
    "the checkbox is sent on the next explicit text search");
  favorites.get("query-favorites-only").checked=false;
  const beforeCancel=favorites.searchCalls().length;
  favorites.get("query-favorites-only").fire("change");
  assert.equal(favorites.searchCalls().length,beforeCancel,"cancelling favorites does not auto-expand to all photos");
  favorites.get("search-form").fire("submit");await flush();
  assert.equal(favorites.searchCalls().at(-1).body.favorites_only,false);

  const viewerFavorites=harness();await flush();
  viewerFavorites.get("query-text").value="sunset";
  viewerFavorites.get("query-favorites-only").checked=true;
  viewerFavorites.get("query-favorites-only").fire("change");
  viewerFavorites.get("search-form").fire("submit");await flush();
  assert.equal(viewerFavorites.ui.state.search.sessionId,"session-combined");
  assert.ok(viewerFavorites.ui.state.search.cursor);
  viewerFavorites.get("result-grid").querySelector('[data-asset-id="201"]').fire("click");await flush();
  viewerFavorites.viewers.at(-1).favoriteChanged({changed:1,is_favorite:false});
  assert.equal(viewerFavorites.ui.state.search.sessionId,null,
    "unfavoriting from the preview invalidates the active search session immediately");
  assert.equal(viewerFavorites.ui.state.search.cursor,null);
  assert.deepEqual(Array.from(viewerFavorites.ui.state.search.hits),[]);
  assert.equal(viewerFavorites.get("result-grid").children.length,0);
  assert.match(viewerFavorites.get("query-status").textContent,/旧收藏结果和分页已失效/);
  assert.equal(viewerFavorites.searchCalls().length,1,"preview favorite changes do not launch a search automatically");

  viewerFavorites.get("search-form").fire("submit");await flush();
  const refreshedSession=viewerFavorites.ui.state.search.sessionId;
  assert.ok(refreshedSession);
  viewerFavorites.setAnswer(()=>({ok:false,status:409,headers:{get:()=>"application/json"},
    json:async()=>({code:"FAVORITES_REVISION_STALE",message:"收藏已变化",details:{reason:"FAVORITES_REVISION_CHANGED"}})}));
  await viewerFavorites.ui.sendSearch(true);await flush();
  assert.equal(viewerFavorites.ui.state.search.sessionId,null,
    "a revision conflict from another writer clears the stale session");
  assert.equal(viewerFavorites.ui.state.search.cursor,null);
  assert.equal(viewerFavorites.ui.state.search.hits.length,0);
  assert.match(viewerFavorites.get("query-status").textContent,/旧收藏结果和分页已失效/);
  assert.equal(viewerFavorites.get("query-actions").hidden,false,
    "the user can explicitly rerun after a server revision conflict");
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [node, "-e", script], cwd=root, capture_output=True, text=True,
        encoding="utf-8", timeout=10,
    )
    assert result.returncode == 0, result.stderr
