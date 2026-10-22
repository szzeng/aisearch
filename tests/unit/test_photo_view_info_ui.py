"""Exercise the shared photo viewer against synthetic protected API responses."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_photo_info_renders_paths_people_gps_and_releases_stale_maps() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the synthetic photo-viewer check"
    result = subprocess.run(
        [node, str(Path(__file__).parent / "fixtures" / "photo_view_info_probe.js")],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_all_photo_pages_load_the_versioned_shared_viewer() -> None:
    web = Path(__file__).resolve().parents[2] / "src" / "photo_search" / "web"
    for page in ("index.html", "search.html", "persons.html", "places.html", "map.html"):
        document = (web / page).read_text(encoding="utf-8")
        assert "/web/photo-info.js?v=p4-photo-ocr-display-a-1" in document
        assert "/web/photo-info.css?v=p4-photo-ocr-display-a-1" in document
        assert 'data-shell-page="favorites" href="/?view=favorites"' in document
    assert "search.js?v=p4-ram-ranking-1" in (web / "search.html").read_text(encoding="utf-8")
    assert "persons.js?v=p4-ui2-person-batch-ux-fix-a-1" in (web / "persons.html").read_text(encoding="utf-8")
    assert "persons-albums.js?v=p4-ui2-viewer-pan-avatar-fix-a-1" in (web / "persons.html").read_text(encoding="utf-8")
    assert "places-albums.js?v=p4-ui2-photo-detail-entry-a-1" in (web / "places.html").read_text(encoding="utf-8")
    assert "/web/app.js?v=p4-photo-smart-collection-ux-fix-2" in (web / "index.html").read_text(encoding="utf-8")
    assert "/web/map.js?v=" in (web / "map.html").read_text(encoding="utf-8")
    assert 'event.target === dialog' in (web / "places-albums.js").read_text(encoding="utf-8"), "the place viewer fallback closes on backdrop clicks"
    assert 'element("button", "thumb-frame")' in (web / "app.js").read_text(encoding="utf-8")
    assert 'element("button", "thumb-frame")' in (web / "search.js").read_text(encoding="utf-8")
    assert 'className = "map-photo-open"' in (web / "map.js").read_text(encoding="utf-8")
    assert 'el("button", "album-photo-open")' in (web / "places-albums.js").read_text(encoding="utf-8")
    assert 'ui.button("查看整张照片"' in (web / "persons.js").read_text(encoding="utf-8")
    style = (web / "photo-info.css").read_text(encoding="utf-8")
    assert ".photo-viewer-stage { position: relative; display: block;" in style
    assert "touch-action: none" in style


def test_return_snapshots_validate_map_bbox_and_search_revisions() -> None:
    web = Path(__file__).resolve().parents[2] / "src" / "photo_search" / "web"
    map_source = (web / "map.js").read_text(encoding="utf-8")
    search_source = (web / "search.js").read_text(encoding="utf-8")
    assert "restoreState.version === version && sameBounds(restoreState.bbox, b)" in map_source
    assert "sameCaptureAssets(restoreState.assets, assets.assets)" in map_source
    assert '"&order=capture_time_desc&cursor=" + encodeURIComponent(restoreState.cursor)' in map_source
    assert '"/search/sessions/" + encodeURIComponent(saved.search_session_id) + "/time?limit=1"' in search_source
    assert "currentRulesVersion !== saved.timeBrowseRulesVersion" in search_source
    assert "返回时未恢复旧结果" in search_source


def test_home_detail_back_restores_browse_and_source_filter() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the synthetic home-history check"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict"),values=new Map();
const location={hash:"",pathname:"/",search:"",replace(){}},sections={sources:{hidden:false},browse:{hidden:true},detail:{hidden:true}};
const timelineMounts=[];let pendingIntent=null,consumedIntents=0;
const window={location,scrollY:0,history:{replaceState:(state,title,url)=>timelineMounts.push({historyReplace:url}),pushState:(state,title,url)=>timelineMounts.push({historyPush:url})},PhotoTimeline:{mount:options=>{timelineMounts.push(options);options.adapter.onRestoreAsset({getBoundingClientRect:()=>({top:620})});return{setReturnAnchor(){},setScope(){}};}},sessionStorage:{getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)},
  requestAnimationFrame:fn=>fn(),scrollTo:(_x,y)=>{window.restoredScroll=y;}};
const document={readyState:"loading",addEventListener(){}};
let code=fs.readFileSync("src/photo_search/web/app.js","utf8");
code=code.replace('  if (document.readyState === "loading")',
  '  globalThis.ui={state,handleHash,restoreBrowseReturn,browseFingerprint,probeCapabilities,setElements(value){els=value;},setRenderDetail(fn){renderDetail=fn;}};\n  if (document.readyState === "loading")');
document.getElementById=()=>null;
const context={document,window,location,history:{replaceState(){}},URLSearchParams,URL,console,fetch:async()=>({status:200,headers:{get:()=>"application/json"},ok:true,json:async()=>({app_version:"test",schema_version:13,schema_status:"ok"})})};
vm.createContext(context);vm.runInContext(code,context);
const ui=context.ui,main={focus(){}},assetGrid={querySelector(){return null;}};
const stub={hidden:false,textContent:"",setAttribute(){},querySelector(){return null;}};
const appMetaStub={textContent:""};
ui.setElements({views:sections,navButtons:[],main,assetGrid,browseTimeline:stub,browseListView:stub,
  browseShowTimeline:stub,browseShowList:stub,browseCaptureState:stub,browseListFilters:stub,
  browseListTimeHint:stub,browseTimelineNote:stub,notice:stub,appMeta:appMetaStub,
  browseSource:{value:""},browseOrder:{value:""},browseCaptureFrom:{value:""},browseCaptureTo:{value:""}});
let routedDetail="";ui.setRenderDetail(id=>{routedDetail=String(id);});window.AIPhotoInfo={consumeDetailReturn:()=>null};location.hash="#/asset/123/details";ui.handleHash();
assert.equal(routedDetail,"123","the explicit details suffix routes to the independent details renderer");
assert.equal(ui.state.detailRouteActive,true);assert.equal(ui.state.detailAssetId,"123");
assert.equal(timelineMounts.find(item=>item.historyReplace).historyReplace,"/","direct details creates an in-app Back fallback");
assert.equal(timelineMounts.find(item=>item.historyPush).historyPush,"/#/asset/123/details","the fallback keeps the explicit details URL current");
window.AIPhotoInfo={consumeDetailReturn:id=>{consumedIntents++;const intent=pendingIntent&&pendingIntent.assetId===id?pendingIntent.value:null;pendingIntent=null;return intent;}};
timelineMounts.length=0;ui.state.detailRouteActive=false;ui.state.detailAssetId=null;ui.state.detailReturnIntent=null;
pendingIntent={assetId:"123",value:{source:"home",path:"/",requiresBackstop:true}};location.hash="#/asset/123/details";ui.handleHash();
assert.equal(consumedIntents,1,"a matching one-time intent is consumed when the explicit route is entered");
assert.equal(timelineMounts.find(item=>item.historyReplace).historyReplace,"/","the history:false legacy viewer intent still creates a safe in-app Back predecessor");
assert.equal(timelineMounts.find(item=>item.historyPush).historyPush,"/#/asset/123/details");
timelineMounts.length=0;ui.state.detailRouteActive=false;ui.state.detailAssetId=null;ui.state.detailReturnIntent=null;
pendingIntent={assetId:"123",value:{source:"search",path:"/search?q=old",requiresBackstop:false}};location.hash="#/asset/123/details";ui.handleHash();
assert.equal(consumedIntents,2,"same-asset stale intent cannot remain available for a later direct route");
assert.equal(timelineMounts.some(item=>item.historyReplace),false,"a validated viewer-owned transition keeps its legitimate predecessor");
assert.equal(timelineMounts.some(item=>item.historyPush),false);
timelineMounts.length=0;ui.state.detailRouteActive=false;ui.state.detailAssetId=null;ui.state.detailReturnIntent=null;location.hash="#/asset/123/details";ui.handleHash();
assert.equal(consumedIntents,3,"the prior same-asset intent is no longer replayable");
assert.equal(timelineMounts.find(item=>item.historyReplace).historyReplace,"/","a later direct route after the stale intent is consumed gets a safe Back predecessor");
timelineMounts.length=0;
ui.state.detailRouteActive=false;ui.state.detailAssetId=null;location.hash="";
const saved={assetId:"42",sourceId:"12",order:"time_asc",captureFrom:"2025-01-01",captureTo:"2025-02-01",scrollY:420,returnAssetId:"42",returnAssetTop:20,count:125};
window.sessionStorage.setItem("aisearch.home-browse-return.v1",JSON.stringify(saved));
assert.equal(ui.restoreBrowseReturn("42"),true);
assert.equal(ui.state.browse.sourceId,"12");assert.equal(ui.state.browse.order,"time_asc");
assert.equal(ui.state.browse.captureFrom,"2025-01-01");assert.equal(ui.state.browse.captureTo,"2025-02-01");
assert.equal(ui.state.browse.returnTargetCount,125,"the snapshot records how many browse results to reload");
ui.state.browse.loaded=true;ui.state.browse.count=125;ui.state.browse.binding=ui.browseFingerprint();ui.state.detailRouteActive=true;location.hash="";ui.handleHash();
assert.equal(sections.browse.hidden,false,"Back from an asset detail returns to the browse grid");
assert.equal(sections.sources.hidden,true);assert.equal(window.restoredScroll,420);
assert.equal(window.sessionStorage.getItem("aisearch.home-browse-return.v1"),null,"the return snapshot is consumed once");
const timelineReturn={assetId:"99",sourceId:"12",order:"time_desc",scrollY:500,returnAssetId:"99",returnAssetTop:20,count:30,mode:"timeline",timelineReturnAnchor:"time-anchor-99"};
window.sessionStorage.setItem("aisearch.home-browse-return.v1",JSON.stringify(timelineReturn));
assert.equal(ui.restoreBrowseReturn("99"),true);ui.state.detailRouteActive=true;location.hash="";ui.handleHash();
assert.equal(timelineMounts.length,1);assert.equal(timelineMounts[0].returnAnchor,"time-anchor-99");
assert.equal(timelineMounts[0].returnAssetId,"99");assert.equal(window.restoredScroll,600,"timeline return restores the asset's prior viewport position after detail reset the page scroll");
assert.equal(ui.state.browse.returnPending,false);
ui.state.detailRouteActive=false;ui.state.detailAssetId=null;ui.state.detailReturnIntent=null;window.AIPhotoInfo={consumeDetailReturn:()=>({invalid:true})};location.hash="#/asset/123/details";ui.handleHash();
assert.match(stub.textContent,/原来源状态已失效或不可用/,"an invalid or unavailable origin is explained while preserving the current asset details");
assert.equal(ui.state.detailReturnWarning,true);
ui.probeCapabilities();setTimeout(()=>{
  assert.match(stub.textContent,/原来源状态已失效或不可用/,"a successful startup capability probe does not clear the source warning");
  let legacyClose=null;window.AIPhotoInfo={consumeDetailReturn:()=>null,open:(_id,options)=>{legacyClose=options.onClose;return{dialog:{open:true},close(){}};}};
  location.hash="#/asset/123";ui.handleHash();assert.equal(typeof legacyClose,"function");legacyClose();
  assert.equal(ui.state.detailReturnWarning,false,"closing the legacy viewer clears the details-only warning state");
  ui.probeCapabilities();setTimeout(()=>{assert.doesNotMatch(stub.textContent,/仍可查看当前照片，关闭后会安全返回照片页/,"a later capability response does not revive the details-only warning after returning to the photo page");},0);
},0);
"""
    result = subprocess.run(
        [node, "-e", script],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("sources_first", [True, False])
def test_full_detail_return_synchronizes_loaded_browse_controls(sources_first: bool) -> None:
    """A fresh details document can finish loading sources before the return route."""
    node = shutil.which("node")
    assert node is not None, "Node is required for the browse-return interaction check"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
const values=new Map(), sourcesFirst=process.argv[1]==="true";
class Element {
  constructor(tag="div"){this.tagName=tag;this.children=[];this.dataset={};this.hidden=false;this._value="";}
  set textContent(v){this._text=String(v);this.children=[];if(this.tagName==="select")this._value="";}
  get textContent(){return this._text||"";}
  set value(v){v=String(v);this._value=this.tagName==="select"&&!this.children.some(n=>n.value===v)?"":v;}
  get value(){return this._value;}
  appendChild(n){this.children.push(n);return n;}
  setAttribute(){} removeAttribute(){} focus(){} querySelector(){return null;}
}
function page(url,referrer=""){
  const location={origin:"http://localhost",replace(next){const u=new URL(next,this.origin);Object.assign(this,{href:u.href,pathname:u.pathname,search:u.search,hash:u.hash});}};
  location.replace(url);
  const sections={sources:new Element(),browse:new Element(),detail:new Element()};sections.browse.hidden=true;
  const controls={browseSource:new Element("select"),browseOrder:new Element("select"),browseCaptureFrom:new Element("input"),browseCaptureTo:new Element("input")};
  for(const value of ["time_desc","capture_desc","asset_id"]){const option=new Element("option");option.value=value;controls.browseOrder.appendChild(option);}
  const els={...controls,views:sections,navButtons:[],main:new Element(),assetGrid:new Element(),sourcesStatus:new Element(),notice:new Element()};
  for(const name of ["browseTimeline","browseListView","browseShowTimeline","browseShowList","browseCaptureState","browseListFilters","browseListTimeHint","browseTimelineNote"])els[name]=new Element();
  let viewerOptions=null;
  const window={location,scrollY:420,sessionStorage:{getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)},
    requestAnimationFrame:fn=>fn(),scrollTo(){},history:{replaceState(_s,_t,u){location.replace(u);},pushState(_s,_t,u){location.replace(u);}},
    PhotoTimeline:{mount:()=>({setReturnAnchor(){},setScope(){}})}};
  const document={readyState:"loading",referrer,addEventListener(){},createElement:tag=>new Element(tag),querySelectorAll:()=>[]};
  const context={window,document,location,history:window.history,URL,URLSearchParams,console,
    fetch:async url=>{assert.equal(url,"/api/v1/sources/summary");return{status:200,ok:true,headers:{get:()=>"application/json"},json:async()=>({sources:[{source_id:1,display_name:"synthetic"}]})};}};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync("src/photo_search/web/photo-info.js","utf8"),context);
  // Keep the real navigation handoff, but avoid unrelated image/metadata rendering.
  window.AIPhotoInfo.open=(_id,options)=>{viewerOptions=options;return{dialog:{open:true},close(){options.onClose();}};};
  let code=fs.readFileSync("src/photo_search/web/app.js","utf8");
  code=code.replace('  if (document.readyState === "loading")',
    '  globalThis.ui={state,showDetail,handleHash,loadSources,setElements(value){els=value;renderSources=function(){};renderDetail=function(id){state.renderedDetail=id;};}};\n  if (document.readyState === "loading")');
  vm.runInContext(code,context);context.ui.setElements(els);
  return{ui:context.ui,window,location,controls,options:()=>viewerOptions};
}
(async()=>{
  const original=page("/?view=sources");
  Object.assign(original.ui.state.browse,{sourceId:"1",order:"capture_desc",captureFrom:"2025-01-01",captureTo:"2025-02-01",mode:"timeline",timelineReturnAnchor:"anchor-14"});
  await original.ui.loadSources();
  original.ui.showDetail(14);
  original.options().onOpenFullDetails("14");
  assert.equal(original.location.hash,"#/asset/14/details");
  // Dropping ?view=sources creates a new document with initially empty controls.
  const returned=page(original.location.href,"http://localhost/?view=sources");
  returned.ui.handleHash();
  assert.equal(returned.ui.state.renderedDetail,"14");
  assert.equal(returned.ui.state.detailReturnIntent.source,"home");
  if(sourcesFirst)await returned.ui.loadSources();
  // The explicit return action re-arms the validated handoff and opens the legacy viewer route.
  returned.window.AIPhotoInfo.restoreDetailReturn("14",returned.ui.state.detailReturnIntent);
  returned.location.replace("/#/asset/14");returned.ui.handleHash();
  assert.equal(returned.ui.state.browse.sourceId,"1");
  assert.equal(returned.ui.state.browse.timelineReturnAnchor,"anchor-14");
  if(!sourcesFirst){
    assert.equal(returned.controls.browseSource.value,"","a select cannot choose a source option that has not arrived");
    await returned.ui.loadSources();
  }
  const restored={browseSource:"1",browseOrder:"capture_desc",browseCaptureFrom:"2025-01-01",browseCaptureTo:"2025-02-01"};
  for(const [key,value] of Object.entries(restored))assert.equal(returned.controls[key].value,value,key+" must match the active browse scope");
  returned.options().onClose();
  assert.equal(returned.location.hash,"");
  assert.equal(returned.ui.state.browse.sourceId,"1");
  assert.equal(returned.controls.browseSource.value,"1");
  assert.equal(values.has("aisearch.home-browse-return.v1"),false);
  assert.equal(values.has("aisearch.photo-detail-return.v1"),false);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", script, str(sources_first).lower()],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
