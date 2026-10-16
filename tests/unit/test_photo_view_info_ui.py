"""Exercise the shared photo viewer against synthetic protected API responses."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_photo_info_renders_paths_people_gps_and_releases_stale_maps() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the synthetic photo-viewer check"
    script = r"""
const fs = require("fs"), vm = require("vm"), assert = require("assert/strict");
class Element {
  constructor(tag) { this.tagName=tag; this.children=[]; this.events={}; this.attributes={}; this.dataset={}; this.style={}; this.hidden=false; this.value=""; this.parentNode=null; this.classList={names:new Set(),add(name){this.names.add(name);},remove(name){this.names.delete(name);},contains(name){return this.names.has(name);},toggle(name){if(this.names.has(name)){this.names.delete(name);return false;}this.names.add(name);return true;}}; }
  set textContent(value) { this.text=String(value); this.children.forEach(c=>c.parentNode=null); this.children=[]; }
  get textContent() { return (this.text||"")+this.children.map(c=>c.textContent).join(""); }
  append(...items) { items.forEach(c=>{ if(c && typeof c==="object"){c.parentNode=this;} this.children.push(c); }); }
  appendChild(item) { this.append(item); return item; }
  replaceChildren(...items) { this.text=""; this.children.forEach(c=>c.parentNode=null); this.children=[]; this.append(...items); }
  after(item) { if(!this.parentNode)return; const a=this.parentNode.children,i=a.indexOf(this); item.parentNode=this.parentNode; a.splice(i+1,0,item); }
  setAttribute(key,value) { this.attributes[key]=String(value); }
  removeAttribute(key) { delete this[key]; }
  querySelector(selector) { return selector===".maplibregl-ctrl-attrib" ? (this.attributionControl||null) : selector===".maplibregl-ctrl-attrib-button" ? (this.attributionButton||null) : null; }
  addEventListener(event,callback) { (this.events[event] ||= []).push(callback); }
  fire(event,extra={}) { (this.events[event]||[]).forEach(fn=>fn(Object.assign({target:this,key:""},extra))); }
  getBoundingClientRect() { return {left:0,top:0,width:this.clientWidth||800,height:this.clientHeight||600}; }
  focus() { this.focused=true; } select() { this.selected=true; }
  remove() { if(this.parentNode){const a=this.parentNode.children;a.splice(a.indexOf(this),1);this.parentNode=null;} }
  get isConnected() { let n=this; while(n.parentNode)n=n.parentNode; return n===document.body || n===document.head; }
  showModal() { this.open=true; }
  close() { this.open=false; this.fire("close"); }
}
const document={cookie:"ps_csrf=synthetic-token",referrer:"",createElement:t=>new Element(t),querySelector:s=>s==="dialog[open]"?(document.body.children.find(n=>n.tagName==="dialog"&&n.open)||null):null};
document.body=new Element("body"); document.head=new Element("head");
const maps=[];
class FakeMap { constructor(options){this.options=options;this.events={};this.fired=[];this.removed=false;const summary={attributes:{},setAttribute(key,value){this.attributes[key]=String(value);}};const control={open:true,attributes:{open:""},attributionButton:summary,classList:{classes:new Set(["maplibregl-compact","maplibregl-compact-show"]),remove(name){this.classes.delete(name);}},querySelector(selector){return selector===".maplibregl-ctrl-attrib-button"?summary:null;},setAttribute(key,value){this.attributes[key]=String(value);},removeAttribute(name){delete this.attributes[name];}};this.options.container.attributionControl=control;maps.push(this);} on(k,fn){this.events[k]=fn;} fire(k){this.fired.push(k);if(this.events[k])this.events[k]({type:k});} addSource(id,source){this.source={id,source};} addLayer(layer){this.layer=layer;} remove(){this.removed=true;} }
const window={location:{href:"/"},maplibregl:{Map:FakeMap},AISEARCH_MAP_CONFIG:{sourceTemplates:key=>({"td-vec":{key},"td-cva":{key},"td-img":{key},"td-cia":{key}}),layerIds:layer=>layer==="imagery"?["td-img-layer","td-cia-layer"]:["td-vec-layer","td-cva-layer"]}};
const fetches=[]; let releaseDetail;
const ok=body=>({ok:true,status:200,json:async()=>body});
const detail=id=>({asset_id:id,sha256:"sha-"+id,byte_size:2048,width:1200,height:800,format:"jpeg",availability:"present",taken_time:{label:"file_time",taken_at:"2020-01-02T03:04:05.000",camera:"Camera X"},locations:[
  {location_id:11,source_id:1,source_display_name:"旅行",relative_path:"照片/海边.jpg",availability:"PRESENT"},
  {location_id:12,source_id:2,source_display_name:"备份",relative_path:"2020\\海边.jpg",availability:"MISSING"},
  {location_id:13,source_id:3,source_display_name:"盘符根目录",relative_path:"根目录照片.jpg",availability:"PRESENT"},
  {location_id:14,source_id:4,source_display_name:"非法位置",relative_path:"../escape.jpg",availability:"PRESENT"},
  {location_id:15,source_id:4,source_display_name:"绝对位置",relative_path:"\\rooted.jpg",availability:"PRESENT"},
  {location_id:16,source_id:4,source_display_name:"盘符相对位置",relative_path:"E:drive-relative.jpg",availability:"PRESENT"}]});
const postCalls=[]; let deferLocal=false,releaseLocal;
const context={document,window,navigator:{clipboard:{writeText:()=>Promise.reject(new Error("clipboard denied"))}},URL,fetch:async (url,options)=>{
  if(url.includes("/local-action")) { postCalls.push({url,options}); const body={status:"accepted",action:JSON.parse(options.body).action}; return deferLocal?new Promise(resolve=>{releaseLocal=()=>resolve(ok(body));}):ok(body); }
  fetches.push(url);
  if(url==="/api/v1/assets/201") { await new Promise(r=>{releaseDetail=r;}); return ok(detail(201)); }
  if(/^\/api\/v1\/assets\/\d+$/.test(url)) return ok(detail(Number(url.split("/").pop())));
  if(url.includes("/sources/1")) return ok({root_path:"D:\\相册\\旅行"});
  if(url.includes("/sources/2")) return ok({root_path:"\\\\server\\share\\archive"});
  if(url.includes("/sources/3")) return ok({root_path:"D:\\"});
  if(url.includes("/sources/4")) return ok({root_path:"D:\\library"});
  if(url.endsWith("/assets/302/faces")) return ok({has_more:true,faces:[]});
  if(url.endsWith("/assets/401/faces")) return ok({faces:[
    {assigned_person_id:7,assigned_person_name:"爸爸",thumbnail_url:"/face/current"},
    {assigned_person_id:9,assigned_person_name:"妈妈",thumbnail_url:"/face/mom"}]});
  if(url.endsWith("/faces")) return ok({has_more:true,faces:[
    {assigned_person_id:7,assigned_person_name:"爸爸",thumbnail_url:"/face/current"},
    {assigned_person_id:7,assigned_person_name:"爸爸",thumbnail_url:"/face/duplicate"},
    {assigned_person_id:8,assigned_person_name:"爸爸",thumbnail_url:"/face/merged"}]});
  if(url.endsWith("/persons/7")) return ok({person_id:7,canonical_person_id:7,name:"爸爸",avatar_face_id:"a".repeat(64),asset_count:2});
  if(url.endsWith("/persons/9")) return ok({person_id:9,canonical_person_id:9,name:"妈妈",avatar_face_id:"b".repeat(64),asset_count:20});
  if(url.endsWith("/persons/8")) return ok({person_id:8,canonical_person_id:7,name:"爸爸",avatar_face_id:"a".repeat(64)});
  if(url.includes("/map/assets")) return ok({total:url.includes("asset_id=301")||url.includes("asset_id=302")?0:1});
  if(url.endsWith("/geo")) return ok({status:"VALID",input_revision:"sha-101",datum_status:"WGS84",normalized_wgs84:{latitude:0,longitude:0}});
  if(url==="/api/v1/map/provider-runtime") return ok({status:"CONFIGURED",key:"fake-map-key",default_layer:"vector"});
  throw new Error("unexpected request: "+url);
}};
vm.createContext(context); vm.runInContext(fs.readFileSync("src/photo_search/web/photo-info.js","utf8"),context);
const flush=()=>new Promise(setImmediate), all=(root,fn)=>{const out=[];function walk(n){if(fn(n))out.push(n);(n.children||[]).forEach(walk);}walk(root);return out;};
(async()=>{
  const host=new Element("aside"); document.body.append(host); let chosen=0;
  let first=context.window.AIPhotoInfo.render(host,101,{onPerson:id=>{chosen=id;}});
  await flush(); await flush();
  assert.match(host.textContent,/分辨率1200 × 800 像素/,"photo facts show source pixel resolution");
  assert.equal(all(host,n=>n.tagName==="details"&&n.className==="photo-info-details").length,1,"the information panel can be folded without removing its contents");
  assert.equal(all(host,n=>n.tagName==="summary"&&n.textContent==="照片信息").length,1);
  assert.match(host.textContent,/D:\\相册\\旅行\\照片\\海边\.jpg/);
  assert.match(host.textContent,/\\\\server\\share\\archive\\2020\\海边\.jpg/);
  assert.match(host.textContent,/D:\\根目录照片\.jpg/,"a drive root keeps its root separator when joined");
  assert.ok(!host.textContent.includes("D:\\library\\escape.jpg"),"parent traversal is not shown as a complete path");
  assert.ok(!host.textContent.includes("D:\\library\\rooted.jpg"),"rooted relative paths are rejected");
  assert.ok(!host.textContent.includes("D:\\library\\E:drive-relative.jpg"),"drive-relative paths are rejected");
  assert.match(host.textContent,/文件时间 2020-01-02 03:04:05\.000/);
  assert.equal(maps[0].options.center[0],0); assert.equal(maps[0].options.center[1],0);
  assert.equal(maps[0].options.attributionControl.compact,true,"detail map uses compact attribution mode");
  maps[0].events.load();
  assert.equal(maps[0].options.container.attributionControl.open,false,"the detail map closes the compact attribution disclosure on initial load");
  assert.ok(!maps[0].options.container.attributionControl.classList.classes.has("maplibregl-compact-show"),"the long attribution text is hidden until deliberately opened");
  const mapBlock=all(host,n=>n.className==="photo-info-map")[0], attributionSummary={attributes:{},closest:selector=>selector===".maplibregl-ctrl-attrib-button"?attributionSummary:null,setAttribute(key,value){this.attributes[key]=String(value);}};
  let prevented=false; mapBlock.fire("click",{target:attributionSummary,preventDefault(){prevented=true;}});
  assert.ok(mapBlock.classList.names.has("photo-info-map-attribution-open"),"the attribution button can deliberately expand its text");
  assert.equal(maps[0].options.container.attributionControl.open,true); assert.equal(attributionSummary.attributes["aria-expanded"],"true"); assert.ok(prevented);
  mapBlock.fire("click",{target:attributionSummary,preventDefault(){prevented=true;}});
  assert.ok(!mapBlock.classList.names.has("photo-info-map-attribution-open"),"the attribution button can collapse its text again");
  assert.equal(maps[0].options.container.attributionControl.open,false); assert.equal(attributionSummary.attributes["aria-expanded"],"false");
  assert.equal(JSON.stringify(maps[0].source.source.data.geometry.coordinates),"[0,0]");
  assert.equal(all(host,n=>n.tagName==="button"&&n.textContent==="复制完整路径").length,3);
  const localButtons=all(host,n=>n.className==="photo-info-local-action");
  assert.equal(localButtons.length,10,"each PRESENT location has both local actions; missing locations do not");
  const openButton=localButtons.find(n=>n.textContent==="系统打开原图");
  openButton.fire("click"); assert.equal(openButton.disabled,true,"the clicked action disables immediately");
  openButton.fire("click"); assert.equal(postCalls.length,1,"duplicate clicks are blocked while the request is pending");
  await flush(); assert.equal(openButton.disabled,false);
  assert.match(postCalls[0].url,/assets\/101\/local-action$/);
  assert.equal(postCalls[0].options.method,"POST");
  assert.equal(postCalls[0].options.headers["X-CSRF-Token"],"synthetic-token");
  assert.deepEqual(JSON.parse(postCalls[0].options.body),{action:"open",location_id:11});
  const copy=all(host,n=>n.tagName==="button"&&n.textContent==="复制完整路径")[0]; copy.fire("click"); await flush();
  assert.ok(all(host,n=>n.tagName==="textarea"&&n.selected).length===1);
  all(host,n=>n.tagName==="button"&&n.className==="photo-info-person")[0].fire("click"); assert.equal(chosen,7);
  assert.equal(all(host,n=>n.className==="photo-info-person").length,1,"different person IDs merged to one canonical ID render once");
  assert.equal((fetches.filter(url=>url.endsWith("/faces"))).length,1,"the face list is fetched once");
  first.dispose(); assert.equal(maps[0].removed,true);

  const ordered=context.window.AIPhotoInfo.render(host,401);
  await flush();await flush();
  const orderedPeople=all(host,n=>n.className==="photo-info-person");
  assert.deepEqual(orderedPeople.map(n=>n.textContent),["妈妈","爸爸"],"all shared photo viewers put the most matched photos first");
  ordered.dispose();

  const stale=context.window.AIPhotoInfo.render(host,201);
  const current=context.window.AIPhotoInfo.render(host,102);
  releaseDetail(); await flush(); await flush();
  assert.match(host.textContent,/海边\.jpg/); assert.equal(maps.length,1,"late A response must not create a map for B");
  stale.dispose(); current.dispose();

  const invalidHost=new Element("aside"); document.body.append(invalidHost);
  const noGps=context.window.AIPhotoInfo.render(invalidHost,301);
  await flush(); await flush(); noGps.dispose();
  assert.ok(!invalidHost.textContent.includes("拍摄位置"),"an asset outside the current GPS member set has no map");

  const partialPeopleHost=new Element("aside"); document.body.append(partialPeopleHost);
  const partialPeople=context.window.AIPhotoInfo.render(partialPeopleHost,302);
  await flush(); await flush();
  assert.match(partialPeopleHost.textContent,/前 200 张人脸中还没有已归属人物/);
  assert.match(partialPeopleHost.textContent,/还有更多人脸待检查/);
  partialPeople.dispose();

  const closedHost=new Element("aside"); document.body.append(closedHost);
  const pending=context.window.AIPhotoInfo.render(closedHost,201); await flush();
  pending.dispose(); releaseDetail(); await flush();
  assert.ok(!closedHost.textContent.includes("2020"),"late detail after close cannot revive the panel");

  const photoReturn=context.window.AIPhotoInfo.render(host,501);
  await flush(); await flush();
  all(host,n=>n.className==="photo-info-person")[0].fire("click");
  assert.equal(window.location.href,"/persons?person_id=7&from_photo=photo","shared photo viewers mark their person-navigation return context");
  photoReturn.dispose();

  const trigger=new Element("button"); document.body.append(trigger);
  let previous=0,next=0;
  const viewer=context.window.AIPhotoInfo.open(101,{returnFocus:trigger,navigation:{previous:()=>previous++,next:()=>next++}});
  await flush(); await flush();
  const dialog=document.body.children.find(n=>n.className==="photo-viewer-dialog");
  assert.ok(dialog.attributes["aria-labelledby"],"the shared viewer has an accessible title");
  const closeButton=all(dialog,n=>n.className==="photo-viewer-close")[0];
  assert.equal(closeButton.focused,true,"opening the viewer places keyboard focus on its visible close button");
  const viewerImage=all(dialog,n=>n.tagName==="img")[0];
  const stage=all(dialog,n=>n.className==="photo-viewer-stage")[0]; stage.clientWidth=800; stage.clientHeight=600;
  viewerImage.naturalWidth=1600; viewerImage.naturalHeight=1200;
  viewerImage.onerror();
  assert.match(viewerImage.src,/assets\/101\/thumbnail$/,"a missing preview falls back to the available thumbnail");
  viewerImage.onload();
  assert.match(all(dialog,n=>n.className==="photo-viewer-image-note")[0].textContent,/当前显示缩略图/);
  const zoom=all(dialog,n=>n.className==="photo-viewer-zoom-label")[0];
  assert.equal(zoom.textContent,"50%","opening a 1600x1200 preview fits the 800x600 stage without cropping");
  all(dialog,n=>n.tagName==="button"&&n.textContent==="+")[0].fire("click");
  assert.equal(zoom.textContent,"63%","zoom buttons use the 1.25 step and report the actual scale");
  all(dialog,n=>n.tagName==="button"&&n.textContent==="适合窗口")[0].fire("click");
  stage.fire("dblclick",{target:viewerImage}); assert.equal(zoom.textContent,"100%","double click switches from fit to preview-pixel scale");
  stage.fire("dblclick",{target:viewerImage}); assert.equal(zoom.textContent,"50%","double click switches back to fit");
  stage.fire("dblclick",{target:viewerImage});
  stage.fire("wheel",{target:viewerImage,ctrlKey:true,deltaY:-100,clientX:600,clientY:300,preventDefault(){this.prevented=true;}});
  assert.equal(zoom.textContent,"125%","Ctrl-wheel zooms while the pointer is on the image stage");
  stage.fire("pointerdown",{target:viewerImage,pointerId:1,clientX:100,clientY:100});
  stage.fire("pointermove",{target:viewerImage,pointerId:1,clientX:1200,clientY:100});
  assert.match(viewerImage.style.transform,/translate\(600px,0px\)/,"drag pan is clamped at the image boundary");
  viewer.setNavigationEnabled(true,true);
  viewer.setPosition("第 2 张");
  assert.equal(viewer.controls.children[5].textContent,"← 上一张","setting position does not overwrite the previous button label");
  assert.equal(viewer.controls.children[6].textContent,"第 2 张","the position status is rendered in its own navigation slot");
  dialog.fire("keydown",{target:closeButton,key:"ArrowRight",preventDefault(){this.prevented=true;}});
  assert.equal(next,1,"right arrow advances the viewer sequence");
  const input=new Element("INPUT"); dialog.fire("keydown",{target:input,key:"ArrowRight",preventDefault(){}});
  assert.equal(next,1,"arrow keys in text inputs do not hijack their editing behavior");
  dialog.fire("click",{target:dialog.children[0]});
  assert.equal(dialog.open,true,"clicks inside the photo viewer do not dismiss it");
  dialog.fire("click",{target:dialog});
  assert.equal(dialog.open,false,"clicking the backdrop dismisses the shared viewer");
  assert.equal(trigger.focused,true,"closing the viewer restores focus to its opener");
  await flush();
  assert.equal(maps.at(-1).removed,true,"closing the viewer removes its map instance");

  const staleActionHost=new Element("aside"); document.body.append(staleActionHost);
  const pendingAction=context.window.AIPhotoInfo.render(staleActionHost,101); await flush(); await flush();
  deferLocal=true;
  all(staleActionHost,n=>n.className==="photo-info-local-action"&&n.textContent==="定位文件位置")[0].fire("click");
  await flush(); pendingAction.dispose(); staleActionHost.replaceChildren(new Element("p")); releaseLocal(); await flush();
  assert.ok(!staleActionHost.textContent.includes("已请求资源管理器定位"),"a response after dispose cannot update the replacement panel");

  const savedValues=new Map(); let historyPushes=0, historyBacks=0; const replacements=[];
  window.location={href:"http://localhost/search?q=lake",origin:"http://localhost",replace:path=>replacements.push(path)};
  window.sessionStorage={getItem:key=>savedValues.get(key)||null,setItem:(key,value)=>savedValues.set(key,value),removeItem:key=>savedValues.delete(key)};
  window.history={pushState(){historyPushes++;},back(){historyBacks++;}};
  window.addEventListener=()=>{}; window.removeEventListener=()=>{};
  const blocked=context.window.AIPhotoInfo.open(101,{beforeClose:()=>false,onOpenFullDetails:()=>assert.fail("busy viewer must not navigate")});
  const blockedDetails=all(blocked.dialog,n=>n.className==="photo-viewer-details")[0];
  blockedDetails.fire("click"); assert.equal(replacements.length,0,"beforeClose blocks full-details navigation");
  blocked.close(); assert.equal(historyBacks,0,"a rejected close preserves its owned history entry");
  blocked.dialog.close(); await flush();
  assert.equal(historyBacks,1,"ordinary close keeps the viewer's existing history behavior");
  const navigated=[];
  const changing=context.window.AIPhotoInfo.open(101,{onOpenFullDetails:id=>{navigated.push(id);context.window.AIPhotoInfo.navigateToDetails(id,"search");}});
  changing.setAsset(202);
  const detailsButton=all(changing.dialog,n=>n.className==="photo-viewer-details")[0];
  detailsButton.fire("click"); detailsButton.fire("click");
  assert.deepEqual(navigated,["202"],"the details entry uses the live setAsset identity and rejects a second trigger");
  assert.equal(changing.dialog.open,false,"successful details navigation releases the current modal");
  assert.equal(historyPushes,2,"each viewer owns one modal history entry");
  assert.deepEqual(replacements,["/#/asset/202/details"],"navigation replaces the modal entry with the explicit details route");
  const returnIntent=context.window.AIPhotoInfo.consumeDetailReturn("202");
  assert.equal(returnIntent.source,"search"); assert.equal(returnIntent.path,"/search?q=lake","a one-time same-origin source path is available for safe return");
  assert.equal(returnIntent.requiresBackstop,false,"a viewer-owned history transition already has an in-app predecessor");
  assert.equal(context.window.AIPhotoInfo.consumeDetailReturn("202"),null,"the source return intent is consumed once");
  assert.equal(context.window.AIPhotoInfo.restoreDetailReturn("202",returnIntent),true,"return-to-viewer can re-arm the validated source intent");
  assert.equal(context.window.AIPhotoInfo.consumeDetailReturn("202").path,"/search?q=lake");
  window.__aiPhotoDetailHandoff=null; document.referrer="http://localhost/elsewhere";
  savedValues.set("aisearch.photo-detail-return.v1",JSON.stringify({assetId:"202",source:"search",path:"/search?q=lake",requiresBackstop:false,handoffKey:"stale",savedAt:Date.now()}));
  assert.equal(context.window.AIPhotoInfo.consumeDetailReturn("202").invalid,true,"a stale same-asset session intent without the current transition or matching referrer is rejected");
  assert.equal(savedValues.has("aisearch.photo-detail-return.v1"),false,"rejected stale intents are cleared once");
  window.sessionStorage={getItem(){throw new Error("storage unavailable");},removeItem(){throw new Error("storage unavailable");},setItem(){throw new Error("storage unavailable");}};
  assert.equal(context.window.AIPhotoInfo.consumeDetailReturn("202").invalid,true,"storage denial is distinguishable from a direct route with no source intent");
  changing.dialog.close(); assert.equal(historyBacks,1,"details navigation does not leave a modal history callback behind");
})().catch(error=>{console.error(error);process.exitCode=1;});
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


def test_all_photo_pages_load_the_versioned_shared_viewer() -> None:
    web = Path(__file__).resolve().parents[2] / "src" / "photo_search" / "web"
    for page in ("index.html", "search.html", "persons.html", "places.html", "map.html"):
        document = (web / page).read_text(encoding="utf-8")
        assert "/web/photo-info.js?v=p4-photo-detail-attribution-collapse-b-1" in document
        assert "/web/photo-info.css?v=p4-photo-detail-attribution-collapse-b-1" in document
    assert "search.js?v=p4-ui2-photo-detail-entry-a-1" in (web / "search.html").read_text(encoding="utf-8")
    assert "persons.js?v=p4-ui2-person-batch-ux-fix-a-1" in (web / "persons.html").read_text(encoding="utf-8")
    assert "persons-albums.js?v=p4-person-avatar-click-a-3" in (web / "persons.html").read_text(encoding="utf-8")
    assert "places-albums.js?v=p4-ui2-photo-detail-entry-a-1" in (web / "places.html").read_text(encoding="utf-8")
    assert "/web/map.js?v=" in (web / "map.html").read_text(encoding="utf-8")
    assert 'event.target === dialog' in (web / "places-albums.js").read_text(encoding="utf-8"), "the place viewer fallback closes on backdrop clicks"
    assert 'element("button", "thumb-frame")' in (web / "app.js").read_text(encoding="utf-8")
    assert 'element("button", "thumb-frame")' in (web / "search.js").read_text(encoding="utf-8")
    assert 'className = "map-photo-open"' in (web / "map.js").read_text(encoding="utf-8")
    assert 'el("button", "album-photo-open")' in (web / "places-albums.js").read_text(encoding="utf-8")
    assert 'ui.button("查看整张照片"' in (web / "persons.js").read_text(encoding="utf-8")


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
  browseListTimeHint:stub,browseTimelineNote:stub,notice:stub,appMeta:appMetaStub});
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
