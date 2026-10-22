"""Exercise literal-text mode through the real search-page event handlers."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_text_mode_switch_paging_and_validated_return_keep_the_query_contract(tmp_path: Path) -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for search interaction checks"
    search_page = Path(__file__).resolve().parents[2] / "src" / "photo_search" / "web" / "search.html"
    assert '/web/search.js?v=p4-ram-ranking-1' in search_page.read_text(encoding="utf-8")
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
      removeAttribute(name){delete this[name];}
  querySelector(selector){
    const asset=selector.match(/^\[data-asset-id="(\d+)"\]$/);
    const matches=child=>asset?child.dataset.assetId===asset[1]:selector.startsWith(".")&&child.className===selector.slice(1);
    for(const child of this.children){if(matches(child)){if(child.tag==="button")return child;const nested=child.querySelector(selector);if(nested)return nested;return child;}const nested=child.querySelector(selector);if(nested)return nested;}
    return null;
  }
  querySelectorAll(){return [];}
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
function harness(storage=new Map(),harnessOptions={}){
  const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element("div"));return elements.get(id);};
  const calls=[],viewers=[],scrolls=[],historyWrites=[],dialogs=[];let answer=harnessOptions.answer||(body=>ok(page(body,body.cursor?202:201)));
  let collectionSaveAnswer=()=>ok({collection:{smart_id:1}});
  let dialogAnswer=()=>Promise.resolve("合成智能合集");
  let timeAnswer=harnessOptions.timeAnswer||(()=>ok({assets:[{asset_id:404,semantic_rank:1}],next_cursor:"time-next",
    total_candidates:2,candidates_truncated:false,taken_time_rules_version:1}));
  for(const [id,value] of Object.entries({"query-retrieval-mode":"combined","query-availability":"any",
    "query-place-state":"any","query-descendants":"true","query-page-size":"1","query-person-match":"any"}))get(id).value=value;
  const context={URLSearchParams,URL,console,encodeURIComponent,isFinite,
    IntersectionObserver:class{observe(){}unobserve(){}disconnect(){}},
    document:{readyState:"loading",cookie:"ps_csrf=fixture-csrf",addEventListener(){},getElementById:get,createElement:tag=>new Element(tag)},
    window:{location:{href:"http://127.0.0.1/search"+(harnessOptions.search||""),origin:"http://127.0.0.1",pathname:"/search",search:harnessOptions.search||"",hash:"",
      replace(path){this.href=new URL(path,this.href).href;}},scrollY:87,
          addEventListener(){},
      history:{state:harnessOptions.historyState||null,replaceState(value,title,url){this.state=value;historyWrites.push(url);if(url){const next=new URL(url,"http://127.0.0.1");context.window.location.href=next.href;context.window.location.pathname=next.pathname;context.window.location.search=next.search;context.window.location.hash=next.hash;}}},
      requestAnimationFrame:fn=>fn(),scrollTo(...args){scrolls.push(args);},
      PhotoBatchSelect:{create(){return {selected:new Set(harnessOptions.selectedAssets||[]),reset(){this.selected.clear();}};}},
      sessionStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,String(v)),removeItem:k=>storage.delete(k)}},
    fetch:async(url,options)=>{
      calls.push({url,options,body:options&&options.body?JSON.parse(options.body):null});
      if(url.endsWith("/capabilities/page"))return ok({schema_version:27,schema_status:"ok",search:{query_encoder_available:false}});
      if(url.endsWith("/sources/summary"))return ok({sources:[{source_id:3,display_name:"合成票据"}]});
      if(url.includes("/persons?"))return ok({persons:[],has_more:false});
      if(url.endsWith("/search/place-aware"))return answer(JSON.parse(options.body));
      if(url.endsWith("/search/by-asset"))return ok({search_session_id:"fresh-asset-session",next_cursor:null,
        has_more:false,total_candidates:1,hits:[{asset_id:202,rank:1,hit_reason:"SEMANTIC",matched_by:{vector:1},matched_text:[]}],
        asset_query:{asset_id:101,filename:"ref.jpg",thumbnail_url:"/thumb/101"}});
      if(url.endsWith("/search/by-asset/validate")){
        const body=JSON.parse(options.body);
        if(harnessOptions.validationResponse)return ok(harnessOptions.validationResponse);
        return ok({valid:true,asset_id:body.asset_id,search_session_id:body.search_session_id});
      }
      if(url.endsWith("/photo-smart-collections/from-search"))return collectionSaveAnswer(url,options);
      if(url.includes("/search/sessions/")&&url.includes("/time?"))return timeAnswer(url,options);
      if(/^\/api\/v1\/assets\/\d+$/.test(url))return ok({asset_id:Number(url.split("/").pop()),format:"jpeg"});
      throw new Error("Unexpected request: "+url);
    }};
  let code=fs.readFileSync("src/photo_search/web/search.js","utf8");
  code=code.replace('  if (document.readyState === "loading")',
    '  globalThis.ui={state,boot,sendSearch,continueSearch,toggleCandidateTimeBrowse,saveSearchSnapshot,get photoSelection(){return photoSelection;}};\n  if (document.readyState === "loading")');
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
  context.window.AIPhotoInfo.actionDialog=options=>{dialogs.push(options);return dialogAnswer(options);};
  vm.runInContext(code,context);
  context.ui.boot();
  return {get,calls,storage,viewers,scrolls,historyWrites,dialogs,ui:context.ui,setAnswer:fn=>{answer=fn;},
    setTimeAnswer:fn=>{timeAnswer=fn;},setCollectionSaveAnswer:fn=>{collectionSaveAnswer=fn;},
    setDialogAnswer:fn=>{dialogAnswer=fn;},
    searchCalls:()=>calls.filter(call=>call.url.endsWith("/search/place-aware"))};
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
  const category=harness();await flush();category.get("query-text").value="猫";
  const categoryPage=body=>({...page(body,body.cursor?502:501,"category-next"),
    search_session_id:body.category_rank?"category-session":"ordinary-session",
    photo_collection_source_token:body.category_rank?"category-token":"ordinary-token",
    photo_collection_source_mode:"ranked_candidates",
    category_rank:{requested:body.category_rank||"off",applied:Boolean(body.category_rank),
      available:true,identity:body.category_rank?"head-sha":null,class_id:"cat"},
    channels:{vector:{engaged:true,strong_match_available:!body.category_rank,strong_match_count:2}}});
  category.setAnswer(body=>ok(categoryPage(body)));
  category.get("search-form").fire("submit");await flush();
  assert.equal(category.searchCalls()[0].body.category_rank,undefined,"ordinary search stays the default");
  assert.equal(category.get("category-rank-control").hidden,false);
  assert.match(category.get("query-status").textContent,/相似度参考/);
  category.get("category-rank-toggle").fire("click");await flush();
  assert.equal(category.searchCalls().at(-1).body.category_rank,"album-qwen512-v1");
  assert.equal(category.searchCalls().at(-1).body.search_session_id,undefined,"mode switch starts a new session");
  assert.equal(category.ui.state.search.confirmedSearch.sourceToken,"category-token");
  assert.equal(category.get("search-save-smart-collection").hidden,true);
  assert.match(category.get("query-status").textContent,/类别优先/);
  assert.doesNotMatch(category.get("query-status").textContent,/高度匹配/);
  category.get("search-save-smart-collection").fire("click");await flush();
  assert.equal(category.calls.filter(call=>call.url.endsWith("/photo-smart-collections/from-search")).length,0);
  await category.ui.sendSearch(true);
  assert.equal(category.searchCalls().at(-1).body.search_session_id,"category-session");
  assert.equal(category.searchCalls().at(-1).body.category_rank,"album-qwen512-v1");
  category.ui.toggleCandidateTimeBrowse();await flush();
  assert.match(category.get("query-status").textContent,/类别优先/);
  assert.equal(category.get("search-save-smart-collection").hidden,true);
  category.ui.toggleCandidateTimeBrowse();await flush();
  assert.equal(category.ui.saveSearchSnapshot(),true);
  const categoryReturn=harness(new Map(category.storage),{answer:body=>ok(categoryPage(body))});await flush();
  assert.equal(categoryReturn.ui.state.search.categoryRank,"album-qwen512-v1");
  assert.equal(categoryReturn.get("search-save-smart-collection").hidden,true);
  assert.match(categoryReturn.get("category-rank-toggle").textContent,/切回普通搜索/);
  categoryReturn.get("category-rank-toggle").fire("click");await flush();
  assert.equal(categoryReturn.searchCalls().at(-1).body.category_rank,undefined);
  assert.equal(categoryReturn.searchCalls().at(-1).body.search_session_id,undefined);
  assert.equal(categoryReturn.get("search-save-smart-collection").hidden,false);
  category.get("category-rank-toggle").fire("click");await flush();
  category.setAnswer(()=>Promise.reject({code:"CATEGORY_RANK_UNAVAILABLE",message:"类别包不可用"}));
  category.get("category-rank-toggle").fire("click");await flush();
  assert.match(category.get("query-status").textContent,/没有成功/);
  assert.equal(category.get("category-rank-toggle").disabled,false,"failed optional mode can be switched off");
  category.setAnswer(body=>ok(categoryPage(body)));
  category.get("category-rank-toggle").fire("click");await flush();
  assert.equal(category.searchCalls().at(-1).body.category_rank,undefined);
  category.get("query-text").value="海边";category.get("query-text").fire("input");
  assert.equal(category.get("category-rank-control").hidden,true,"edited query cannot reuse the category offer");
  category.get("query-retrieval-mode").value="text";category.get("query-retrieval-mode").fire("change");
  assert.equal(category.ui.state.search.categoryRank,"off");
  category.get("search-form").fire("submit");await flush();
  assert.equal(category.searchCalls().at(-1).body.category_rank,undefined);

  // A new ordinary query may supersede an optional category request, a time page,
  // or a return-snapshot validation. None of those old responses may restore results.
  const categoryRace=harness();await flush();categoryRace.get("query-text").value="猫";
  categoryRace.setAnswer(body=>ok(categoryPage(body)));
  categoryRace.get("search-form").fire("submit");await flush();
  let finishOldCategory;
  categoryRace.setAnswer(body=>new Promise(resolve=>{finishOldCategory=()=>resolve(ok(categoryPage(body)));}));
  categoryRace.get("category-rank-toggle").fire("click");await flush();
  assert.equal(categoryRace.get("result-grid").children.length,0);
  assert.equal(categoryRace.ui.state.search.confirmedSearch,null);
  assert.equal(categoryRace.get("category-rank-toggle").disabled,true);
  categoryRace.get("query-text").value="海边";
  categoryRace.setAnswer(body=>ok({...categoryPage(body),hits:[{asset_id:703,rank:1}]}));
  categoryRace.get("search-form").fire("submit");await flush();
  finishOldCategory();await flush();
  assert.equal(categoryRace.ui.state.search.hits[0].asset_id,703);
  assert.equal(categoryRace.ui.state.search.categoryRank,"off");
  assert.equal(categoryRace.ui.state.search.confirmedSearch.sourceToken,"ordinary-token");
  categoryRace.get("query-text").value="猫";categoryRace.get("search-form").fire("submit");await flush();
  categoryRace.get("category-rank-toggle").fire("click");await flush();
  let finishOldCategoryTime;
  categoryRace.setTimeAnswer(()=>new Promise(resolve=>{finishOldCategoryTime=()=>resolve(ok({
    assets:[{asset_id:704,semantic_rank:1}],total_candidates:1,taken_time_rules_version:1}));}));
  categoryRace.ui.toggleCandidateTimeBrowse();await flush();
  categoryRace.get("query-text").value="狗";
  categoryRace.setAnswer(body=>ok({...categoryPage(body),hits:[{asset_id:705,rank:1}]}));
  categoryRace.get("search-form").fire("submit");await flush();
  finishOldCategoryTime();await flush();
  assert.equal(categoryRace.ui.state.search.hits[0].asset_id,705);
  assert.equal(categoryRace.ui.state.search.timeBrowse.active,false);
  let finishOldCategoryReturn;
  const categoryRaceReturn=harness(new Map(categoryRace.storage),{
    answer:body=>ok({...categoryPage(body),hits:[{asset_id:706,rank:1}]}),
    timeAnswer:()=>new Promise(resolve=>{finishOldCategoryReturn=()=>resolve(ok({
      assets:[],total_candidates:1,taken_time_rules_version:1}));})});
  await flush();assert.equal(typeof finishOldCategoryReturn,"function");
  categoryRaceReturn.get("query-text").value="新查询";
  categoryRaceReturn.get("search-form").fire("submit");await flush();
  finishOldCategoryReturn();await flush();
  assert.equal(categoryRaceReturn.ui.state.search.hits[0].asset_id,706);

  const ordinary=harness();await flush();
  ordinary.setAnswer(body=>ok({...page(body,303,null),hits:[{asset_id:303,rank:33,
    hit_reason:"SEMANTIC",semantic_ready:true,matched_by:{vector:33},matched_text:["ocr_text"]}]}));
  ordinary.get("search-form").fire("submit");await flush();
  assert.match(ordinary.get("result-grid").textContent,/第 33 名/,
    "ordinary semantic results retain the overall ranking");
  assert.match(ordinary.get("result-grid").textContent,/匹配位置：图片文字/);
  assert.doesNotMatch(ordinary.get("result-grid").textContent,/语义就绪|通道名次|向量通道名次/);
  assert.doesNotMatch(ordinary.get("result-grid").textContent,/查看详情/,
    "result cards no longer render a separate detail button");

  const reproduced=harness();await flush();reproduced.get("query-text").value="新鲜查询";
  reproduced.setAnswer(body=>ok({...page(body,400,null),profile_id:"response-profile",
    image_generation_id:"response-generation",photo_collection_source_token:"fresh-server-token",
    photo_collection_source_mode:"ranked_candidates"}));
  reproduced.get("search-form").fire("submit");await flush();
  assert.equal(reproduced.ui.state.capabilities.search.active_profile_id,undefined);
  assert.equal(reproduced.ui.state.search.confirmedSearch.sourceToken,"fresh-server-token");
  assert.equal(reproduced.get("search-save-smart-collection").disabled,false,
    "a cached capability omission does not invalidate a freshly issued server token");
  reproduced.get("search-save-smart-collection").fire("click");await flush();
  assert.equal(reproduced.calls.filter(call=>call.url.endsWith("/photo-smart-collections/from-search"))[0].body.source_token,
    "fresh-server-token","the save request uses the token from this confirmed search");

  const confirmed=harness();await flush();
  confirmed.get("query-text").value="海边";
  confirmed.setAnswer(body=>ok({...page(body,401,"page-2"),
    photo_collection_source_token:"first-search-token",photo_collection_source_mode:"ranked_candidates",
    channels:{fts:{engaged:true},vector:{engaged:true,strong_match_available:true,strong_match_count:17,
      strong_match_more_possible:false}}}));
  confirmed.get("search-form").fire("submit");await flush();
  assert.equal(confirmed.get("search-save-smart-collection").hidden,false,
    "a successful eligible first page exposes smart collection save outside photo selection");
  assert.equal(confirmed.get("search-photo-selection-toolbar").children.some(node=>node.id==="search-save-smart-collection"),false);
  assert.match(confirmed.get("query-status").textContent,/约 17 张高度匹配/);
  confirmed.setAnswer(body=>ok(page(body,402,null)));
  await confirmed.ui.sendSearch(true);
  assert.equal(confirmed.get("search-save-smart-collection").hidden,false,
    "paging does not remove the stable first-search save credential");
  assert.equal(confirmed.ui.state.search.confirmedSearch.sourceToken,"first-search-token");
  assert.match(confirmed.get("query-status").textContent,/约 17 张高度匹配/,
    "paging preserves the first page semantic statistic");
  confirmed.get("search-save-smart-collection").fire("click");await flush();
  assert.equal(confirmed.calls.filter(call=>call.url.endsWith("/photo-smart-collections/from-search"))[0].body.source_token,
    "first-search-token","automatic paging does not replace the first-page save credential");
  confirmed.ui.toggleCandidateTimeBrowse();await flush();
  assert.match(confirmed.get("query-status").textContent,/按拍摄时间查看当前候选/);
  assert.match(confirmed.get("query-status").textContent,/原始语义搜索/);
  confirmed.get("search-save-smart-collection").fire("click");await flush();
  assert.equal(confirmed.calls.filter(call=>call.url.endsWith("/photo-smart-collections/from-search"))[0].body.source_token,
    "first-search-token","time browsing saves the source token for the original search rules");
  confirmed.ui.toggleCandidateTimeBrowse();
  assert.match(confirmed.get("query-status").textContent,/约 17 张高度匹配/,
    "return to relevance restores the confirmed statistic without retrieval");
  assert.equal(confirmed.searchCalls().length,2,"sorting switches do not rerun semantic retrieval");

  const timeRetry=harness();await flush();timeRetry.get("query-text").value="时间重试";
  timeRetry.setAnswer(body=>ok({...page(body,405,null),channels:{vector:{engaged:true,
    strong_match_available:true,strong_match_count:6,strong_match_more_possible:false}}}));
  timeRetry.get("search-form").fire("submit");await flush();
  timeRetry.ui.toggleCandidateTimeBrowse();await flush();
  assert.equal(timeRetry.ui.state.search.timeBrowse.cursor,"time-next");
  timeRetry.setTimeAnswer(()=>Promise.reject(new Error("synthetic time-page failure")));
  await timeRetry.ui.continueSearch();
  assert.equal(timeRetry.ui.state.search.timeBrowse.retryCursor,"time-next");
  assert.equal(timeRetry.ui.state.search.timeBrowse.cursor,null);
  await timeRetry.ui.continueSearch();
  assert.equal(timeRetry.ui.state.search.timeBrowse.retryCursor,"time-next",
    "a second failed attempt keeps the actual time cursor available for retry");
  assert.equal(timeRetry.calls.filter(call=>call.url.includes("time-next")).length,2,
    JSON.stringify(timeRetry.calls.map(call=>call.url)));
  const timeSnapshotSaved=timeRetry.ui.saveSearchSnapshot();assert.equal(timeSnapshotSaved,true);
  const timeSnapshot=JSON.parse(timeRetry.storage.get("aisearch.search-return.v1"));
  timeSnapshot.cursor=null;timeSnapshot.hasMore=false;
  const timeReturn=harness(new Map([["aisearch.search-return.v1",JSON.stringify(timeSnapshot)]]));await flush();
  assert.equal(timeReturn.get("load-more").hidden,false,
    "time snapshot cursor controls the more-results affordance independently of relevance pagination");
  assert.equal(timeReturn.ui.state.search.timeBrowse.retryCursor,"time-next");
  timeReturn.setTimeAnswer(()=>Promise.reject(new Error("synthetic time-page failure")));
  await timeReturn.ui.continueSearch();
  assert.equal(timeReturn.calls.filter(call=>call.url.includes("time-next")).length,1,
    "restored time mode retries its own cursor rather than the relevance cursor");
  timeReturn.ui.toggleCandidateTimeBrowse();
  assert.equal(timeReturn.get("load-more").hidden,true,
    "leaving time mode hides its temporary paging affordance when relevance has no next page");

  confirmed.get("query-text").value="新查询";
  confirmed.get("search-form").fire("input");
  assert.equal(confirmed.get("search-save-smart-collection").disabled,true,
    "edited but unsubmitted conditions cannot use the old source token");

  confirmed.get("query-text").value="海边";
  const snapshotSaved=confirmed.ui.saveSearchSnapshot();
  assert.equal(snapshotSaved,true);
  const confirmedSnapshot=JSON.parse(confirmed.storage.get("aisearch.search-return.v1"));
  assert.equal(confirmedSnapshot.confirmedSearch.sourceToken,"first-search-token");
  const confirmedReturn=harness(new Map([["aisearch.search-return.v1",JSON.stringify(confirmedSnapshot)]]));await flush();
  assert.equal(confirmedReturn.get("search-save-smart-collection").hidden,false,
    "validated return preserves the result operation area");
  assert.equal(confirmedReturn.get("search-save-smart-collection").disabled,true,
    "snapshot return does not treat a cached token as server-validated");
  assert.match(confirmedReturn.get("search-result-operation-hint").textContent,/请重新搜索确认后保存/);

  const filterSnapshotSource=harness();await flush();
  filterSnapshotSource.setAnswer(body=>ok({...page(body,412,null),search_session_id:"filter-only",
    retrieval_mode:"filter_only",photo_collection_source_token:"old-filter-token",
    photo_collection_source_mode:"all_filter_matches"}));
  filterSnapshotSource.get("search-form").fire("submit");await flush();
  assert.equal(filterSnapshotSource.ui.saveSearchSnapshot(),true);
  const filterSnapshot=JSON.parse(filterSnapshotSource.storage.get("aisearch.search-return.v1"));
  const filterReturn=harness(new Map([["aisearch.search-return.v1",JSON.stringify(filterSnapshot)]]),{
    answer:body=>ok({...page(body,413,null),search_session_id:"filter-only",
      retrieval_mode:"filter_only",photo_collection_source_token:"fresh-after-return",
      photo_collection_source_mode:"all_filter_matches"})});
  await flush();
  assert.equal(filterReturn.ui.state.search.confirmedSearch.sourceToken,"fresh-after-return");
  assert.equal(filterReturn.ui.state.search.saveReconfirmRequired,false,
    "a new server-confirmed filter search clears the snapshot reconfirmation gate");
  assert.equal(filterReturn.get("search-save-smart-collection").disabled,false,
    "a refreshed filter snapshot can save using its newly issued credential");

  const expiredSave=harness();await flush();expiredSave.get("query-text").value="过期凭据";
  expiredSave.setAnswer(body=>ok({...page(body,406,null),photo_collection_source_token:"expired-token",
    photo_collection_source_mode:"ranked_candidates"}));
  expiredSave.get("search-form").fire("submit");await flush();
  expiredSave.setCollectionSaveAnswer(()=>({ok:false,status:409,headers:{get:()=>"application/json"},
    json:async()=>({code:"INVALID_CURSOR",message:"保存凭据已过期或不属于当前应用会话"})}));
  expiredSave.get("search-save-smart-collection").fire("click");await flush();
  const rejectedSaveCalls=expiredSave.calls.filter(call=>call.url.endsWith("/photo-smart-collections/from-search"));
  assert.equal(rejectedSaveCalls.length,1,"an expired credential is rejected once without an automatic retry");
  assert.equal(rejectedSaveCalls[0].body.source_token,"expired-token");
  assert.equal(expiredSave.ui.state.search.confirmedSearch,null);
  assert.equal(expiredSave.get("search-save-smart-collection").disabled,true,
    "an expired token disables later attempts until the user reruns the search");
  assert.match(expiredSave.get("search-result-operation-hint").textContent,/请重新搜索确认后保存/);
  expiredSave.get("search-save-smart-collection").fire("click");await flush();
  assert.equal(expiredSave.calls.filter(call=>call.url.endsWith("/photo-smart-collections/from-search")).length,1);

  const invalidSession=harness();await flush();invalidSession.get("query-text").value="会话失效";
  invalidSession.setAnswer(body=>ok({...page(body,409,null),photo_collection_source_token:"session-token",
    photo_collection_source_mode:"ranked_candidates"}));
  invalidSession.get("search-form").fire("submit");await flush();
  invalidSession.setCollectionSaveAnswer(()=>({ok:false,status:401,headers:{get:()=>"application/json"},
    json:async()=>({code:"SESSION_INVALID",message:"应用会话已失效"})}));
  invalidSession.get("search-save-smart-collection").fire("click");await flush();
  assert.equal(invalidSession.ui.state.search.confirmedSearch,null);
  assert.equal(invalidSession.get("search-save-smart-collection").hidden,true);
  assert.match(invalidSession.get("notice").textContent,/重新打开启动器/);
  invalidSession.get("search-save-smart-collection").fire("click");await flush();
  assert.equal(invalidSession.calls.filter(call=>call.url.endsWith("/photo-smart-collections/from-search")).length,1,
    "an invalid app session cannot reuse the cached source token");

  const delayedSave=harness();await flush();delayedSave.get("query-text").value="等待中的凭据";
  delayedSave.setAnswer(body=>ok({...page(body,407,null),photo_collection_source_token:"delayed-token",
    photo_collection_source_mode:"ranked_candidates"}));
  delayedSave.get("search-form").fire("submit");await flush();
  let releaseSaveName;
  delayedSave.setDialogAnswer(()=>new Promise(resolve=>{releaseSaveName=resolve;}));
  delayedSave.get("search-save-smart-collection").fire("click");await flush();
  delayedSave.ui.state.search.sessionId="different-session";
  releaseSaveName("不应保存");await flush();
  assert.equal(delayedSave.calls.filter(call=>call.url.endsWith("/photo-smart-collections/from-search")).length,0,
    "the captured token is not sent after the active search session changes while the dialog is open");
  assert.match(delayedSave.get("query-status").textContent,/当前保存凭据已过期或搜索条件\/模型已变化/);

  const expiredSnapshot=JSON.parse(JSON.stringify(confirmedSnapshot));
  const expiredReturn=harness(new Map([["aisearch.search-return.v1",JSON.stringify(expiredSnapshot)]]));await flush();
  assert.equal(expiredReturn.get("search-save-smart-collection").disabled,true,
    "a stored token older than the server lifetime cannot become usable on return");

  const failedNew=harness();await flush();
  failedNew.get("query-text").value="旧查询";
  failedNew.setAnswer(body=>ok({...page(body,420,null),photo_collection_source_token:"old-token",
    photo_collection_source_mode:"ranked_candidates"}));
  failedNew.get("search-form").fire("submit");await flush();
  failedNew.setAnswer(()=>({ok:false,status:503,headers:{get:()=>"application/json"},json:async()=>({code:"UNAVAILABLE",message:"synthetic failure"})}));
  failedNew.get("query-text").value="新查询";
  failedNew.get("search-form").fire("submit");await flush();
  assert.equal(failedNew.get("search-save-smart-collection").hidden,true,
    "a failed new search cannot keep the previous search token visible");

  for(const [model,index] of [["chinese-clip",1],["chinese-clip-exif-v2",2],
      ["qwen3-vl-embedding-2b",3],["qwen3-vl-embedding-2b-exif-v2",4]]){
    const modelCase=harness();await flush();modelCase.get("query-text").value="模型统计";
    modelCase.setAnswer(body=>ok({...page(body,430+index,null),profile_id:model,image_generation_id:"generation-"+index,
      channels:{vector:{engaged:true,strong_match_available:true,strong_match_count:index,
        strong_match_more_possible:false}}}));
    modelCase.get("search-form").fire("submit");await flush();
    assert.equal(modelCase.ui.state.search.confirmedSearch.profileId,model);
    assert.equal(modelCase.ui.state.search.confirmedSearch.generationId,"generation-"+index);
    assert.match(modelCase.get("query-status").textContent,new RegExp("约 "+index+" 张高度匹配"));
  }

  for(const lostIdentity of ["profile","generation"]){
    const identityCase=harness();await flush();identityCase.get("query-text").value="身份失效";
    identityCase.ui.state.capabilities.search.active_profile_id="profile-bound";
    identityCase.ui.state.capabilities.search.active_image_generation_id="generation-bound";
    identityCase.setAnswer(body=>ok({...page(body,408,null),profile_id:"profile-bound",
      image_generation_id:"generation-bound",photo_collection_source_token:"identity-token",
      photo_collection_source_mode:"ranked_candidates"}));
    identityCase.get("search-form").fire("submit");await flush();
    assert.equal(identityCase.get("search-save-smart-collection").disabled,false);
    if(lostIdentity==="profile")identityCase.ui.state.capabilities.search.active_profile_id=null;
    else identityCase.ui.state.capabilities.search.active_image_generation_id=null;
    identityCase.get("search-form").fire("input");
    assert.equal(identityCase.get("search-save-smart-collection").disabled,false,
      "missing cached profile or generation data is not an authoritative token rejection");
    identityCase.setCollectionSaveAnswer(()=>({ok:false,status:409,headers:{get:()=>"application/json"},
      json:async()=>({code:"PROFILE_GENERATION_MISMATCH",message:"活动搜索模型已变化，请重新搜索"})}));
    identityCase.get("search-save-smart-collection").fire("click");await flush();
    assert.equal(identityCase.calls.some(call=>call.url.endsWith("/photo-smart-collections/from-search")),true,
      "the server makes the authoritative model-binding decision");
    assert.equal(identityCase.ui.state.search.confirmedSearch,null);
  }

  const smartDependencyChanged=harness();await flush();smartDependencyChanged.get("query-text").value="智能规则变化";
  smartDependencyChanged.setAnswer(body=>ok({...page(body,414,null),photo_collection_source_token:"dependency-token",
    photo_collection_source_mode:"ranked_candidates"}));
  smartDependencyChanged.get("search-form").fire("submit");await flush();
  smartDependencyChanged.setCollectionSaveAnswer(()=>({ok:false,status:409,headers:{get:()=>"application/json"},
    json:async()=>({code:"PHOTO_SMART_DEPENDENCY_CHANGED",message:"搜索依赖规则已变化，请重新搜索确认"})}));
  smartDependencyChanged.get("search-save-smart-collection").fire("click");await flush();
  assert.equal(smartDependencyChanged.ui.state.search.confirmedSearch,null,
    "a changed smart dependency invalidates the source credential");
  assert.equal(smartDependencyChanged.get("search-save-smart-collection").disabled,true);
  assert.match(smartDependencyChanged.get("search-result-operation-hint").textContent,/请重新搜索确认后保存/);

  const staleSessionWhileBrowsing=harness();await flush();staleSessionWhileBrowsing.get("query-text").value="时间会话失效";
  staleSessionWhileBrowsing.setAnswer(body=>ok({...page(body,415,"relevance-next"),
    photo_collection_source_token:"time-session-token",photo_collection_source_mode:"ranked_candidates"}));
  staleSessionWhileBrowsing.get("search-form").fire("submit");await flush();
  staleSessionWhileBrowsing.ui.toggleCandidateTimeBrowse();await flush();
  assert.equal(staleSessionWhileBrowsing.ui.state.search.timeBrowse.previous.cursor,"relevance-next");
  staleSessionWhileBrowsing.setCollectionSaveAnswer(()=>({ok:false,status:409,headers:{get:()=>"application/json"},
    json:async()=>({code:"SEARCH_SESSION_EXPIRED",message:"搜索会话已过期"})}));
  staleSessionWhileBrowsing.get("search-save-smart-collection").fire("click");await flush();
  assert.equal(staleSessionWhileBrowsing.ui.state.search.timeBrowse.previous.cursor,null,
    "session invalidation clears the saved relevance cursor too");
  staleSessionWhileBrowsing.ui.toggleCandidateTimeBrowse();
  assert.equal(staleSessionWhileBrowsing.get("load-more").hidden,true,
    "returning to relevance cannot expose a cursor from an expired search session");
  assert.equal(staleSessionWhileBrowsing.ui.state.search.sessionId,null);

  const truncated=harness();await flush();
  truncated.get("query-text").value="城市";
  truncated.setAnswer(body=>ok({...page(body,410,null),photo_collection_source_token:"token-5000",
    photo_collection_source_mode:"ranked_candidates",candidates_truncated:true,
    channels:{vector:{engaged:true,strong_match_available:true,strong_match_count:5000,strong_match_more_possible:true}}}));
  truncated.get("search-form").fire("submit");await flush();
  assert.match(truncated.get("query-status").textContent,/5000\+ 张高度匹配/);
  assert.match(truncated.get("query-status").textContent,/候选可能已截断/);

  const unavailableStats=harness();await flush();
  unavailableStats.get("query-text").value="树";
  unavailableStats.setAnswer(body=>ok({...page(body,411,null),
    channels:{vector:{engaged:true,strong_match_available:false}}}));
  unavailableStats.get("search-form").fire("submit");await flush();
  assert.match(unavailableStats.get("query-status").textContent,/高度匹配统计不可用/);
  assert.doesNotMatch(unavailableStats.get("query-status").textContent,/0 张高度匹配/);

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
  assert.equal(returned.viewers[0].returnFocus.className,"thumb-frame","restored viewer uses the photo preview trigger");
  assert.match(returned.viewers[0].position,/第 2 张/);
  returned.setAnswer(body=>ok(page(body,203,null)));returned.viewers[0].nextPhoto();await flush();
  assertLiteral(returned.searchCalls()[0].body);assert.equal(returned.searchCalls()[0].body.cursor,"text-last");
  assert.deepEqual(Array.from(returned.ui.state.search.hits,hit=>hit.asset_id),[201,202,203]);
  assert.equal(returned.ui.state.search.cursor,null);
  assert.equal(returned.viewers[0].assetId,203,"next photo at the loaded boundary waits for successful continuation");
  assert.match(returned.viewers[0].position,/第 3 张/);

  const similarHits=[
    {asset_id:202,rank:1,hit_reason:"SEMANTIC",matched_by:{vector:1},matched_text:[]},
    {asset_id:203,rank:2,hit_reason:"SEMANTIC",matched_by:{vector:2},matched_text:[]},
  ];
  const similarBody={...page({retrieval_mode:"combined",top_k:3,page_size:1},202,"asset-next"),
    search_session_id:"asset-session-A",hits:similarHits,asset_query:{asset_id:101,filename:"ref.jpg",thumbnail_url:"/thumb/101"}};
  const similarSnapshot={mode:"asset_image",assetQueryId:101,assetQuery:similarBody.asset_query,
    form:{pageSize:"1"},hits:similarHits,windowOffset:0,recycledCount:0,spacerHeight:0,
    search_session_id:"asset-session-A",next_cursor:"asset-next",count:2,total_candidates:3,
    lastBody:similarBody,lastRequestBody:{asset_id:101,top_k:3,page_size:1},scrollY:347};
  const token="return-token-A";
  const similarReturn=harness(new Map([["aisearch.search-return.v1:"+token,JSON.stringify(similarSnapshot)]]),
    {search:"?similar_asset=101",historyState:{searchSnapshotToken:token}});
  await flush();
  const validationCall=similarReturn.calls.find(call=>call.url.endsWith("/search/by-asset/validate"));
  assert.ok(validationCall,"a historical similar entry validates its saved session");
  assert.equal(validationCall.body.asset_id,101);
  assert.equal(validationCall.body.search_session_id,"asset-session-A");
  assert.equal(Object.hasOwn(validationCall.body,"cursor"),false,
    "session validation is distinct from paging and does not construct an offset cursor");
  assert.equal(similarReturn.calls.some(call=>call.url.endsWith("/search/by-asset")),false,
    "return restores the frozen ranking without rerunning retrieval");
  assert.equal(similarReturn.ui.state.search.mode,"asset_image");
  assert.equal(similarReturn.ui.state.search.sessionId,"asset-session-A");
  assert.equal(similarReturn.ui.state.search.cursor,"asset-next");
  assert.deepEqual(Array.from(similarReturn.ui.state.search.hits,hit=>hit.asset_id),[202,203]);
  assert.match(similarReturn.get("result-grid").textContent,/第 2 名/);
  assert.deepEqual(similarReturn.scrolls.at(-1),[0,347],"history restore reinstates the scroll position");
  assert.equal(similarReturn.get("asset-query-summary").hidden,false,"a restored similar search shows its reference summary");
  similarReturn.get("asset-query-image").src="/thumb/101";
  similarReturn.get("asset-query-image").hidden=false;
  similarReturn.get("asset-query-filename").textContent="ref.jpg";
  similarReturn.get("asset-query-scope").textContent="搜索范围：全部图库";
  similarReturn.setAnswer(body=>new Promise(resolve=>{globalThis.releaseTextSearch=()=>resolve(ok(page(body,303,null)));}));
  similarReturn.get("search-form").fire("submit");
  assert.equal(similarReturn.get("asset-query-summary").hidden,true,"text-mode submit hides the reference summary synchronously");
  assert.equal(similarReturn.get("asset-query-image").hidden,true);
  assert.equal(similarReturn.get("asset-query-image").src,undefined,"text-mode submit removes the reference thumbnail source immediately");
  assert.equal(similarReturn.get("asset-query-filename").textContent,"");
  assert.equal(similarReturn.get("asset-query-scope").textContent,"");
  assert.equal(similarReturn.ui.state.search.assetQueryId,null);
  assert.equal(similarReturn.ui.state.search.assetQuery,null);
  assert.equal(new URL("http://127.0.0.1"+similarReturn.historyWrites.at(-1)).searchParams.has("similar_asset"),false);
  globalThis.releaseTextSearch();await flush();
  assert.equal(similarReturn.get("asset-query-summary").hidden,true,"text results keep the reference summary cleared");

  const freshSimilar=harness(new Map(),{search:"?similar_asset=101"});await flush();
  assert.equal(freshSimilar.get("asset-query-summary").hidden,false,"starting a new similar search shows the new reference summary");
  assert.equal(freshSimilar.get("asset-query-filename").textContent,"ref.jpg");
  assert.equal(freshSimilar.get("asset-query-image").src,"/thumb/101");
  freshSimilar.setAnswer(()=>({ok:false,status:503,headers:{get:()=>"application/json"},json:async()=>({code:"UNAVAILABLE",message:"synthetic failure"})}));
  freshSimilar.get("search-form").fire("submit");
  assert.equal(freshSimilar.get("asset-query-summary").hidden,true,"switching away from a fresh similar search hides its reference before a failed request");
  await flush();
  assert.equal(freshSimilar.get("asset-query-summary").hidden,true,"a failed text search does not restore stale reference details");

  const legacySnapshot=JSON.parse(JSON.stringify(similarSnapshot));
  delete legacySnapshot.lastRequestBody.top_k;
  const legacySimilarReturn=harness(new Map([[
    "aisearch.search-return.v1:"+token,JSON.stringify(legacySnapshot)]]),
    {search:"?similar_asset=101",historyState:{searchSnapshotToken:token}});
  await flush();
  const legacyValidation=legacySimilarReturn.calls.find(call=>call.url.endsWith("/search/by-asset/validate"));
  assert.equal(legacyValidation.body.top_k,5000,
    "legacy snapshots without top_k validate with the API's unchanged default");
  assert.deepEqual(Array.from(legacySimilarReturn.ui.state.search.hits,hit=>hit.asset_id),[202,203]);

  const wrongReference=harness(new Map([["aisearch.search-return.v1:"+token,JSON.stringify(similarSnapshot)]]),
    {search:"?similar_asset=999",historyState:{searchSnapshotToken:token}});
  await flush();
  assert.equal(wrongReference.calls.some(call=>call.url.endsWith("/search/by-asset/validate")),false,
    "a snapshot for another reference photo is never validated or rendered");
  assert.equal(wrongReference.calls.some(call=>call.url.endsWith("/search/by-asset")),false);
  assert.match(wrongReference.get("query-status").textContent,/与当前参考照片不匹配/);

  const mismatchedSession=harness(new Map([[
    "aisearch.search-return.v1:"+token,JSON.stringify(similarSnapshot)]]),
    {search:"?similar_asset=101",historyState:{searchSnapshotToken:token},
      validationResponse:{valid:true,asset_id:101,search_session_id:"different-session"}});
  await flush();
  assert.deepEqual(Array.from(mismatchedSession.ui.state.search.hits,hit=>hit.asset_id),[],
    "a validation response for another session cannot restore the saved ranking");
  assert.match(mismatchedSession.get("query-status").textContent,/会话已过期或参考向量已变化/);

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

  const selectedCollectionName=harness();await flush();
  selectedCollectionName.get("query-text").value="西湖 夜景";
  selectedCollectionName.setAnswer(body=>ok({...page(body,301,null),photo_collection_source_token:"confirmed-search-token",
    photo_collection_source_mode:"ranked_candidates"}));
  selectedCollectionName.get("search-form").fire("submit");await flush();
  selectedCollectionName.ui.photoSelection.selected.add(201);
  selectedCollectionName.setDialogAnswer(()=>Promise.resolve(null));
  selectedCollectionName.get("search-photo-save-collection").fire("click");await flush();
  assert.equal(selectedCollectionName.dialogs.at(-1).value,"西湖 夜景",
    "saving selected photos defaults to the confirmed search keyword");

  const selectedCollectionLong=harness();await flush();
  const selectedLongKeyword="山🌄".repeat(45);
  selectedCollectionLong.get("query-text").value=selectedLongKeyword;
  selectedCollectionLong.setAnswer(body=>ok({...page(body,304,null),photo_collection_source_token:"confirmed-search-token",
    photo_collection_source_mode:"ranked_candidates"}));
  selectedCollectionLong.get("search-form").fire("submit");await flush();
  selectedCollectionLong.ui.photoSelection.selected.add(201);
  selectedCollectionLong.setDialogAnswer(()=>Promise.resolve(null));
  selectedCollectionLong.get("search-photo-save-collection").fire("click");await flush();
  assert.equal(selectedCollectionLong.dialogs.at(-1).value,Array.from(selectedLongKeyword).slice(0,80).join(""),
    "a long selected-collection default is truncated at 80 complete Unicode characters");

  const selectedCollectionNoKeyword=harness();await flush();selectedCollectionNoKeyword.ui.photoSelection.selected.add(201);
  selectedCollectionNoKeyword.setDialogAnswer(()=>Promise.resolve(null));
  selectedCollectionNoKeyword.get("search-photo-save-collection").fire("click");await flush();
  assert.equal(selectedCollectionNoKeyword.dialogs.at(-1).value,"",
    "saving selected photos without a confirmed search keyword keeps the name blank");

  const smartName=harness();await flush();
  smartName.get("query-text").value="西湖 夜景";
  smartName.setAnswer(body=>ok({...page(body,301),photo_collection_source_token:"confirmed-search-token",
    photo_collection_source_mode:"ranked_candidates"}));
  smartName.get("search-form").fire("submit");await flush();
  smartName.setDialogAnswer(()=>Promise.resolve(null));
  smartName.get("search-save-smart-collection").fire("click");await flush();
  assert.equal(smartName.dialogs.at(-1).value,"西湖 夜景",
    "a new smart collection name defaults to the confirmed search keyword");
  const longKeyword="山🌄".repeat(45);
  const smartNameLong=harness();await flush();
  smartNameLong.get("query-text").value=longKeyword;
  smartNameLong.setAnswer(body=>ok({...page(body,303),photo_collection_source_token:"confirmed-search-token",
    photo_collection_source_mode:"ranked_candidates"}));
  smartNameLong.get("search-form").fire("submit");await flush();
  smartNameLong.setDialogAnswer(()=>Promise.resolve(null));
  smartNameLong.get("search-save-smart-collection").fire("click");await flush();
  assert.equal(smartNameLong.dialogs.at(-1).value,Array.from(longKeyword).slice(0,80).join(""),
    "a long keyword is truncated at 80 complete Unicode characters");
  const replaceStorage=new Map([["photo-smart-replace-target",JSON.stringify({smart_id:8,
    instance_token:"replace-instance",expected_revision:4,name:"已有合集名称"})]]);
  const smartReplace=harness(replaceStorage);await flush();
  smartReplace.get("query-text").value="雪山";
  smartReplace.setAnswer(body=>ok({...page(body,302),photo_collection_source_token:"confirmed-search-token",
    photo_collection_source_mode:"ranked_candidates"}));
  smartReplace.get("search-form").fire("submit");await flush();
  smartReplace.setDialogAnswer(()=>Promise.resolve(null));
  smartReplace.get("search-save-smart-collection").fire("click");await flush();
  assert.equal(smartReplace.dialogs.at(-1).value,"已有合集名称",
    "replacing an existing smart collection keeps its current name as the default");

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
    script_path = tmp_path / "search-ui-harness.js"
    script_path.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [node, str(script_path)], cwd=root, capture_output=True, text=True,
        encoding="utf-8", timeout=10,
    )
    assert result.returncode == 0, result.stderr
