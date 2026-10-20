from __future__ import annotations

import shutil
import subprocess


def test_smart_collection_reports_refresh_failure_even_with_current_cache() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the smart collection status check"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
const document={readyState:"loading",addEventListener(){}},window={};
let code=fs.readFileSync("src/photo_search/web/app.js","utf8");
code=code.replace('  if (document.readyState === "loading")',
  '  globalThis.ui={photoSmartRefreshStatus};\n  if (document.readyState === "loading")');
const context={document,window};vm.createContext(context);vm.runInContext(code,context);
const current={freshness:"CURRENT",execution_kind:"COMBINED",last_error_code:"PHOTO_SMART_REFRESH_FAILED"};
const body={coverage:"INDEXING",assets:[{asset_id:7}]};
const message=context.ui.photoSmartRefreshStatus(current,body);
assert.match(message,/上次刷新失败（PHOTO_SMART_REFRESH_FAILED），可重试。/);
assert.match(message,/索引处理中/);
assert.equal(context.ui.photoSmartRefreshStatus({...current,last_error_code:null},body),
  "索引处理中；已发布候选仍可浏览并标记为待更新。");
"""
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_smart_collection_frontend_pagination_uses_independent_snapshot_cursors() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for smart collection pagination checks"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
class Node {
  constructor(tag){this.tagName=tag;this.children=[];this.dataset={};this.attributes={};this.hidden=false;this.options=tag==="select"?[{value:"rank"},{value:"capture_desc"}]:undefined;}
  appendChild(node){this.children.push(node);return node;}
  replaceChildren(){this.children=[];}
  addEventListener(name,fn){this["on"+name]=fn;}
  setAttribute(name,value){this.attributes[name]=String(value);}
  querySelectorAll(){return this.children.flatMap(x=>[...(x.dataset.assetId?[x]:[]),...x.querySelectorAll()]);}
}
const document={readyState:"loading",documentElement:{scrollTop:0},addEventListener(){},querySelector(){return null;},createElement(tag){return new Node(tag);}};
const window={setTimeout(){return 1;},clearTimeout(){},scrollY:0};
let source=fs.readFileSync("src/photo_search/web/app.js","utf8");
source=source.replace("function request(path, options) {","function request(path, options) { if(window.__request) return window.__request(path, options);");
source=source.replace('  if (document.readyState === "loading")',
  '  globalThis.ui={loadPhotoSmartAssets,resetPhotoSmartView,photoSmartThumbUrl,photoSmartCollectionStateLabel,requestPhotoSmartRefresh,observePhotoSmartRefresh,enterPhotoSmartCollection,photoSmartContextKey,photoSmartAuthorityKey,state,els};\n  if (document.readyState === "loading")');
const context={document,window};vm.createContext(context);vm.runInContext(source,context);
const ui=context.ui, collection={smart_id:5,instance_token:"instance-5",revision:2,rule_revision:3,
  exclusions_revision:7,build_id:"build-a",freshness:"CURRENT",execution_kind:"TEXT",refresh_status:"IDLE",name:"Trip"};
Object.assign(ui.state,{currentView:"photoSmartCollection",detailRouteActive:false});
Object.assign(ui.state.photoSmart,{smartId:"5",collection,rule:{display_query:"mountain"},view:"results",order:"rank"});
const ids=["photoSmartAssets","photoSmartMore","photoSmartReloadResults","photoSmartDetailStatus","photoSmartDetailTitle","photoSmartRuleSummary","photoSmartOrder"];
for(const id of ids) ui.els[id]=new Node(id==="photoSmartOrder"?"select":"div");
ui.els.photoSmartAssets.querySelectorAll=Node.prototype.querySelectorAll;
const calls=[];
let holdAppend=false, failAppend=false, rejectAppend409=false, releaseAppend, holdMetadata=false, releaseMetadata;
window.__request=(path)=>{
  calls.push(path);
  if(path==="/photo-smart-collections/5" && holdMetadata) return new Promise(resolve=>{releaseMetadata=resolve;});
  if(path.includes("/exclusions") && path.includes("cursor=")) return Promise.resolve({body:{assets:[{asset_id:21,thumbnail_url:"/api/v1/assets/21/thumbnail"}],next_cursor:"excluded-after",has_more:true,collection}});
  if(path.includes("/exclusions")) return Promise.resolve({body:{assets:[{asset_id:20,thumbnail_url:"/api/v1/assets/20/thumbnail"}],next_cursor:"excluded-next",has_more:true,collection}});
  if(path.includes("/assets") && path.includes("cursor=")) {
    if(rejectAppend409) return Promise.reject({status:409,code:"CURSOR_STALE"});
    if(holdAppend) return new Promise(resolve=>{releaseAppend=resolve;});
    return Promise.resolve({body:{assets:[{asset_id:11,smart_rank:2}],next_cursor:"after-next",has_more:true,collection}});
  }
  if(path.includes("/assets")) return Promise.resolve({body:{assets:[{asset_id:10,smart_rank:1}],next_cursor:"results-next",has_more:true,collection}});
  return Promise.resolve({body:{collection,rule:{display_query:"mountain"}}});
};
(async()=>{
  assert.equal(await ui.loadPhotoSmartAssets(false),true);
  assert.equal(ui.state.photoSmart.assetCursor,"results-next");
  window.__request=(path,options)=>{
    calls.push(path);
    if(path.endsWith("/exclusions/10")) return Promise.resolve({body:{revision:3,exclusions_revision:8}});
    if(path.includes("/assets")) return Promise.resolve({body:{assets:[{asset_id:10,smart_rank:1}],next_cursor:"results-next",has_more:true,collection:{...collection,revision:3,exclusions_revision:8}}});
    return Promise.resolve({body:{collection:{...collection,revision:3,exclusions_revision:8},rule:{display_query:"mountain"}}});
  };
  ui.els.photoSmartAssets.children[0].children[1].onclick();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(ui.state.photoSmart.mutationInFlight,false,"a successful exclusion must release the mutation lock after it resets the view epoch");
  ui.state.photoSmart.collection=collection;
  ui.state.photoSmart.smartId="5";
  window.__request=(path)=>{
    calls.push(path);
    if(path==="/photo-smart-collections/5" && holdMetadata) return new Promise(resolve=>{releaseMetadata=resolve;});
    if(path.includes("/exclusions") && path.includes("cursor=")) return Promise.resolve({body:{assets:[{asset_id:21,thumbnail_url:"/api/v1/assets/21/thumbnail"}],next_cursor:"excluded-after",has_more:true,collection}});
    if(path.includes("/exclusions")) return Promise.resolve({body:{assets:[{asset_id:20,thumbnail_url:"/api/v1/assets/20/thumbnail"}],next_cursor:"excluded-next",has_more:true,collection}});
    if(path.includes("/assets") && path.includes("cursor=")) {
      if(rejectAppend409) return Promise.reject({status:409,code:"CURSOR_STALE"});
      if(failAppend) return Promise.reject({status:503,code:"TEMPORARY_FAILURE"});
      if(holdAppend) return new Promise(resolve=>{releaseAppend=resolve;});
      return Promise.resolve({body:{assets:[{asset_id:11,smart_rank:2}],next_cursor:"after-next",has_more:true,collection}});
    }
    if(path.includes("/assets")) return Promise.resolve({body:{assets:[{asset_id:10,smart_rank:1}],next_cursor:"results-next",has_more:true,collection}});
    return Promise.resolve({body:{collection,rule:{display_query:"mountain"}}});
  };
  ui.resetPhotoSmartView("excluded","rank");
  assert.equal(await ui.loadPhotoSmartAssets(false),true);
  assert.equal(ui.state.photoSmart.exclusionCursor,"excluded-next");
  assert.equal(ui.state.photoSmart.assetCursor,null,"switching to exclusions must not reuse the result cursor");
  assert.equal(await ui.loadPhotoSmartAssets(true),true);
  assert.match(calls.at(-1),/cursor=excluded-next/);
  assert.equal(ui.photoSmartThumbUrl("/api/v1/assets/20/thumbnail",20),"/api/v1/assets/20/thumbnail");
  assert.equal(ui.photoSmartThumbUrl("/assets/20/thumbnail",20),"/api/v1/assets/20/thumbnail");
  assert.equal(ui.photoSmartCollectionStateLabel({freshness:"NO_CACHE"}),"尚无缓存");
  assert.equal(ui.photoSmartCollectionStateLabel({freshness:"CHECK_ON_OPEN"}),"打开后检查");
  const refreshCalls=calls.filter(path=>path.endsWith("/refresh")).length;
  const refreshContext=ui.photoSmartContextKey();
  await ui.requestPhotoSmartRefresh(collection,false,refreshContext);
  await ui.requestPhotoSmartRefresh(collection,false,refreshContext);
  assert.equal(calls.filter(path=>path.endsWith("/refresh")).length,refreshCalls+1,"same rule must enqueue at most one automatic refresh");
  ui.resetPhotoSmartView("excluded","capture_desc");
  await ui.requestPhotoSmartRefresh(collection,false,ui.photoSmartContextKey());
  assert.equal(calls.filter(path=>path.endsWith("/refresh")).length,refreshCalls+1,
    "changing the result order or tab must not enqueue another automatic refresh");
  const entryEpoch=ui.state.photoSmart.entryEpoch;
  ui.enterPhotoSmartCollection("5");
  assert.equal(ui.state.photoSmart.entryEpoch,entryEpoch+1);
  assert.equal(Object.keys(ui.state.photoSmart.autoRefreshKeys).length,0,
    "a new visit to the collection starts a fresh automatic-refresh budget");
  ui.state.photoSmart.smartId="5";
  ui.resetPhotoSmartView("results","rank");
  assert.equal(await ui.loadPhotoSmartAssets(false),true);
  holdAppend=true;
  const callsBefore=calls.length, first=ui.loadPhotoSmartAssets(true);
  await Promise.resolve();
  assert.equal(await ui.loadPhotoSmartAssets(true),false,"second trigger must not start another page request");
  assert.equal(calls.length,callsBefore+1,"one request may use the active cursor");
  holdAppend=false;
  releaseAppend({body:{assets:[{asset_id:11,smart_rank:2}],next_cursor:"after-next",has_more:true,collection}});
  assert.equal(await first,true);
  failAppend=true;
  const beforeFailure=ui.els.photoSmartAssets.children.length;
  assert.equal(await ui.loadPhotoSmartAssets(true),false,"network failure must stop automatic pagination and expose retry");
  assert.equal(ui.els.photoSmartAssets.children.length,beforeFailure,"network failure retains prior cards");
  failAppend=false;
  rejectAppend409=true;
  const visibleBefore=ui.els.photoSmartAssets.children.length;
  assert.equal(await ui.loadPhotoSmartAssets(true),false,"a stale cursor must stop automatic pagination");
  assert.equal(ui.state.photoSmart.pageNeedsReload,true);
  assert.equal(ui.els.photoSmartAssets.children.length,visibleBefore,"visible old results remain intact on 409");
  ui.state.photoSmart.collection=null;
  ui.state.photoSmart.smartId="5";
  holdMetadata=true;
  const staleMetadata=ui.loadPhotoSmartAssets(false);
  ui.resetPhotoSmartView("excluded","rank");
  releaseMetadata({body:{collection,rule:{display_query:"late"}}});
  assert.equal(await staleMetadata,false,"metadata from an inactive view must be discarded");
  assert.equal(ui.state.photoSmart.collection,null,"late metadata cannot replace the active view");
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_smart_collection_refresh_observer_distinguishes_terminal_states_and_cleans_up() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for smart collection refresh lifecycle checks"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
function make(scrollTop=600){
  const timers=[];
  const document={readyState:"loading",documentElement:{scrollTop},addEventListener(){}};
  const window={scrollY:scrollTop,setTimeout(fn,delay){const timer={fn,delay,canceled:false};timers.push(timer);return timer;},
    clearTimeout(timer){if(timer)timer.canceled=true;}};
  let source=fs.readFileSync("src/photo_search/web/app.js","utf8");
  source=source.replace("function request(path, options) {","function request(path, options) { if(window.__request) return window.__request(path, options);");
  source=source.replace('  if (document.readyState === "loading")',
    '  globalThis.ui={observePhotoSmartRefresh,requestPhotoSmartRefresh,resetPhotoSmartView,enterPhotoSmartCollection,photoSmartAuthorityKey,state,els};\n  if (document.readyState === "loading")');
  const context={document,window};vm.createContext(context);vm.runInContext(source,context);
  const ui=context.ui;
  ui.els.photoSmartRefresh={disabled:false};
  ui.els.photoSmartReloadResults={hidden:true};
  ui.els.photoSmartDetailStatus={textContent:""};
  Object.assign(ui.state,{currentView:"photoSmartCollection",detailRouteActive:false});
  ui.state.photoSmart.smartId="5";
  const pending=timers;
  return {ui,window,pending,async tick(){
    const timer=pending.find(item=>!item.canceled);
    assert.ok(timer,"a poll timer should be scheduled");timer.canceled=true;timer.fn();
    await new Promise(resolve=>setImmediate(resolve));
  }};
}
const collection={smart_id:5,instance_token:"instance-5",rule_revision:3,build_id:"build-a"};
(async()=>{
  const failed=make(),statuses=[
    {body:{collection:{...collection,refresh_status:"RUNNING"},build_id:"build-a"}},
    {body:{collection:{...collection,refresh_status:"FAILED",last_error_code:"PHOTO_SMART_REFRESH_FAILED"},build_id:"build-a"}}
  ];
  failed.window.__request=()=>Promise.resolve(statuses.shift());
  failed.ui.state.photoSmart.collection=collection;
  let key=failed.ui.photoSmartAuthorityKey(collection);
  failed.ui.observePhotoSmartRefresh(collection,key,0);
  assert.equal(failed.ui.els.photoSmartRefresh.disabled,true);
  await failed.tick();assert.equal(failed.ui.state.photoSmart.refreshObserverKey,key,"RUNNING must continue observation");
  await failed.tick();
  assert.match(failed.ui.els.photoSmartDetailStatus.textContent,/刷新失败/);
  assert.doesNotMatch(failed.ui.els.photoSmartDetailStatus.textContent,/新缓存已发布/);
  assert.equal(failed.ui.els.photoSmartReloadResults.hidden,true,"failure must not offer a false new-results action");
  assert.equal(failed.ui.state.photoSmart.refreshObserverKey,null);
  assert.equal(failed.ui.state.photoSmart.refreshTimer,null);
  assert.equal(failed.ui.els.photoSmartRefresh.disabled,false,"terminal failure releases the refresh button");

  const published=make();published.window.__request=()=>Promise.resolve({body:{
    collection:{...collection,refresh_status:"NO_CACHE",last_error_code:null},build_id:"build-b"}});
  published.ui.state.photoSmart.collection=collection;
  key=published.ui.photoSmartAuthorityKey(collection);
  published.ui.els.photoSmartRefresh.disabled=true;
  published.ui.observePhotoSmartRefresh(collection,key,0);
  await published.tick();
  assert.match(published.ui.els.photoSmartDetailStatus.textContent,/新缓存已发布/);
  assert.equal(published.ui.state.photoSmart.pageNeedsReload,true);
  assert.equal(published.ui.els.photoSmartReloadResults.hidden,false);
  assert.equal(published.ui.els.photoSmartRefresh.disabled,false);

  const firstEntry=make();
  firstEntry.window.__request=path=>{
    if(path.endsWith("/refresh")) return Promise.resolve({body:{status:"QUEUED"}});
    return Promise.reject(new Error("refresh observer should not poll before timer tick"));
  };
  const firstContext=firstEntry.ui.photoSmartAuthorityKey(collection);
  const postResult=await firstEntry.ui.requestPhotoSmartRefresh(collection,false,firstContext);
  assert.equal(postResult,true,"queued automatic refresh response remains valid before first assets populate state.collection");
  assert.equal(firstEntry.ui.state.photoSmart.refreshObserverKey,firstContext,
    "first-entry refresh response starts an observer even while collection state is not populated");
  assert.equal(firstEntry.ui.els.photoSmartRefresh.disabled,true);

  const unknown=make();unknown.window.__request=()=>Promise.resolve({body:{
    collection:{...collection,refresh_status:"DEFERRED"},build_id:"build-a"}});
  unknown.ui.state.photoSmart.collection=collection;
  key=unknown.ui.photoSmartAuthorityKey(collection);unknown.ui.observePhotoSmartRefresh(collection,key,0);
  await unknown.tick();
  assert.match(unknown.ui.els.photoSmartDetailStatus.textContent,/无法确认/);
  assert.equal(unknown.ui.state.photoSmart.pageNeedsReload,false);
  assert.equal(unknown.ui.els.photoSmartRefresh.disabled,false);

  const network=make();let networkCalls=0;
  network.window.__request=()=>{networkCalls+=1;return Promise.reject({status:503});};
  network.ui.state.photoSmart.collection=collection;
  key=network.ui.photoSmartAuthorityKey(collection);network.ui.observePhotoSmartRefresh(collection,key,0);
  for(let index=0;index<60;index+=1) await network.tick();
  assert.equal(networkCalls,60,"network retries must respect the finite poll budget");
  assert.equal(network.ui.state.photoSmart.refreshObserverKey,null);
  assert.equal(network.ui.state.photoSmart.refreshTimer,null);
  assert.equal(network.ui.els.photoSmartRefresh.disabled,false,"poll exhaustion releases the refresh button");
  assert.match(network.ui.els.photoSmartDetailStatus.textContent,/无法确认/);

  const sameVisit=make();sameVisit.ui.state.photoSmart.collection=collection;
  const sameVisitKey=sameVisit.ui.photoSmartAuthorityKey(collection);
  sameVisit.ui.observePhotoSmartRefresh(collection,sameVisitKey,0);
  const activeTimer=sameVisit.ui.state.photoSmart.refreshTimer;
  sameVisit.ui.resetPhotoSmartView("excluded","capture_desc");
  assert.equal(sameVisit.ui.state.photoSmart.refreshObserverKey,sameVisitKey,
    "sorting or switching result/exclusion tabs must preserve the active collection refresh observer");
  assert.equal(sameVisit.ui.state.photoSmart.refreshTimer,activeTimer);

  const switched=make();switched.window.__request=()=>new Promise(()=>{});
  switched.ui.state.photoSmart.collection=collection;
  key=switched.ui.photoSmartAuthorityKey(collection);switched.ui.els.photoSmartRefresh.disabled=true;
  switched.ui.observePhotoSmartRefresh(collection,key,0);
  switched.ui.state.photoSmart.autoRefreshKeys.old=true;
  switched.ui.enterPhotoSmartCollection("6");
  assert.equal(switched.ui.state.photoSmart.refreshObserverKey,null,"entering another collection cancels the old observer");
  assert.equal(switched.ui.state.photoSmart.refreshTimer,null);
  assert.equal(switched.ui.els.photoSmartRefresh.disabled,false);
  assert.deepEqual(Object.keys(switched.ui.state.photoSmart.autoRefreshKeys),[]);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_smart_collection_failed_first_page_reload_retains_previous_photos() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for smart collection first-page reload checks"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
class Node {constructor(tag){this.tagName=tag;this.children=[];this.dataset={};this.hidden=false;this.options=tag==="select"?[]:undefined;}
  appendChild(node){this.children.push(node);return node;} replaceChildren(){this.children=[];} addEventListener(name,fn){this["on"+name]=fn;}
  setAttribute(){} querySelectorAll(){return this.children;}}
const document={readyState:"loading",addEventListener(){},querySelector(){return null;},createElement(tag){return new Node(tag);}};
const window={setTimeout(){return 1;},clearTimeout(){},scrollY:0};
let opened=[];window.AIPhotoInfo={open(id){opened.push(id);}};
let source=fs.readFileSync("src/photo_search/web/app.js","utf8");
source=source.replace("function request(path, options) {","function request(path, options) { if(window.__request) return window.__request(path, options);");
source=source.replace('  if (document.readyState === "loading")',
  '  globalThis.ui={loadPhotoSmartAssets,state,els};\n  if (document.readyState === "loading")');
const context={document,window};vm.createContext(context);vm.runInContext(source,context);
const ui=context.ui, collection={smart_id:5,instance_token:"instance-5",revision:2,rule_revision:3,
  exclusions_revision:7,build_id:"build-a",freshness:"CURRENT",execution_kind:"TEXT",refresh_status:"IDLE",name:"Trip"};
const nextCollection={...collection,build_id:"build-b",freshness:"CURRENT"};
Object.assign(ui.state,{currentView:"photoSmartCollection",detailRouteActive:false});
Object.assign(ui.state.photoSmart,{smartId:"5",view:"results",order:"rank"});
for(const id of ["photoSmartAssets","photoSmartMore","photoSmartReloadResults","photoSmartDetailStatus",
  "photoSmartDetailTitle","photoSmartRuleSummary"]) ui.els[id]=new Node("div");
ui.els.photoSmartOrder=new Node("select");
window.__request=path=>{
  if(path==="/photo-smart-collections/5") return Promise.resolve({body:{collection,rule:{display_query:"trip"}}});
  assert.match(path,/\/assets\?/);return Promise.resolve({body:{assets:[{asset_id:"old-photo"}],has_more:false,collection}});
};
(async()=>{
  assert.equal(await ui.loadPhotoSmartAssets(false),true,"initial published page should load");
  const previous=ui.els.photoSmartAssets.children[0];
  assert.ok(previous,"initial published card should be rendered");
  let failAssets=false;
  window.__request=path=>{
    if(path.includes("/photo-smart-collections/5?instance_token=")) return Promise.resolve({body:{collection:nextCollection,build_id:"build-b",rule:{display_query:"trip"}}});
    assert.match(path,/\/assets\?/);
    return failAssets ? Promise.reject({status:503,code:"TEMPORARY_FAILURE"})
      : Promise.resolve({body:{assets:[{asset_id:"old-photo"}],has_more:false,collection}});
  };
  ui.state.photoSmart.pageNeedsReload=true;
  failAssets=true;
  assert.equal(await ui.loadPhotoSmartAssets(false,true),false);
  assert.equal(ui.els.photoSmartAssets.children.length,1,"failed reload keeps the published first-page cards visible");
  assert.equal(ui.els.photoSmartAssets.children[0],previous);
  assert.equal(ui.state.photoSmart.collection.build_id,"build-a","failed reload must not replace the active snapshot identity");
  previous.children[0].onclick();
  assert.deepEqual(opened,["old-photo"],"cards from the retained snapshot remain interactive after reload failure");
  assert.equal(ui.state.photoSmart.pageInFlight,false,"failed reload releases the page lock");
  assert.equal(ui.state.photoSmart.pageNeedsReload,true,"failed reload keeps the stale-page guard active");
  failAssets=false;
  window.__request=path=>{
    if(path.includes("/photo-smart-collections/5?instance_token=")) return Promise.resolve({body:{collection:nextCollection,build_id:"build-b",rule:{display_query:"trip"}}});
    assert.match(path,/\/assets\?/);
    return Promise.resolve({body:{assets:[{asset_id:"new-photo"}],has_more:false,collection:nextCollection}});
  };
  assert.equal(await ui.loadPhotoSmartAssets(false,true),true,"a later successful first page should complete the reload");
  assert.equal(ui.state.photoSmart.collection.build_id,"build-b");
  assert.equal(ui.state.photoSmart.pageNeedsReload,false,"a verified first page clears the stale-page guard");
  assert.equal(ui.els.photoSmartAssets.children.length,1);
  assert.equal(ui.els.photoSmartAssets.children[0].dataset.assetId,"new-photo","verified new snapshot replaces old cards");
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_smart_collection_scroll_pager_has_manual_fallback_without_intersection_observer() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the scroll pager fallback check"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
const listeners={};
const button={hidden:true,textContent:"",addEventListener(name,fn){listeners[name]=fn;},removeEventListener(){}};
const window={addEventListener(){},removeEventListener(){}};
vm.runInNewContext(fs.readFileSync("src/photo_search/web/scroll-pager.js","utf8"),{window});
let result=false;
const pager=window.PhotoScrollPager.create({sentinel:{},button,canLoad:()=>true,load:()=>result,loadLabel:"load more",retryLabel:"retry load"});
(async()=>{
  assert.equal(button.hidden,false,"without IntersectionObserver the visible manual fallback is required");
  result=false;
  await pager.retry();
  assert.equal(button.hidden,false,"failed pages remain manually retryable");
  assert.equal(button.textContent,"retry load");
  result=true;
  await pager.retry();
  assert.equal(button.textContent,"load more");
  pager.dispose();
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
