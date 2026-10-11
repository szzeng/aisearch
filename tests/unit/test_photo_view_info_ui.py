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
  constructor(tag) { this.tagName=tag; this.children=[]; this.events={}; this.attributes={}; this.dataset={}; this.style={}; this.hidden=false; this.value=""; this.parentNode=null; this.classList={add(){},remove(){}}; }
  set textContent(value) { this.text=String(value); this.children.forEach(c=>c.parentNode=null); this.children=[]; }
  get textContent() { return (this.text||"")+this.children.map(c=>c.textContent).join(""); }
  append(...items) { items.forEach(c=>{ if(c && typeof c==="object"){c.parentNode=this;} this.children.push(c); }); }
  appendChild(item) { this.append(item); return item; }
  replaceChildren(...items) { this.text=""; this.children.forEach(c=>c.parentNode=null); this.children=[]; this.append(...items); }
  after(item) { if(!this.parentNode)return; const a=this.parentNode.children,i=a.indexOf(this); item.parentNode=this.parentNode; a.splice(i+1,0,item); }
  setAttribute(key,value) { this.attributes[key]=String(value); }
  removeAttribute(key) { delete this[key]; }
  addEventListener(event,callback) { (this.events[event] ||= []).push(callback); }
  fire(event,extra={}) { (this.events[event]||[]).forEach(fn=>fn(Object.assign({target:this,key:""},extra))); }
  focus() { this.focused=true; } select() { this.selected=true; }
  remove() { if(this.parentNode){const a=this.parentNode.children;a.splice(a.indexOf(this),1);this.parentNode=null;} }
  get isConnected() { let n=this; while(n.parentNode)n=n.parentNode; return n===document.body || n===document.head; }
  showModal() { this.open=true; }
  close() { this.open=false; this.fire("close"); }
}
const document={cookie:"ps_csrf=synthetic-token",createElement:t=>new Element(t),querySelector:()=>null};
document.body=new Element("body"); document.head=new Element("head");
const maps=[];
class FakeMap { constructor(options){this.options=options;this.events={};this.removed=false;maps.push(this);} on(k,fn){this.events[k]=fn;} addSource(id,source){this.source={id,source};} addLayer(layer){this.layer=layer;} remove(){this.removed=true;} }
const window={location:{href:"/"},maplibregl:{Map:FakeMap}};
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
const context={document,window,navigator:{clipboard:{writeText:()=>Promise.reject(new Error("clipboard denied"))}},fetch:async (url,options)=>{
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
  throw new Error("unexpected request: "+url);
}};
vm.createContext(context); vm.runInContext(fs.readFileSync("src/photo_search/web/photo-info.js","utf8"),context);
const flush=()=>new Promise(setImmediate), all=(root,fn)=>{const out=[];function walk(n){if(fn(n))out.push(n);(n.children||[]).forEach(walk);}walk(root);return out;};
(async()=>{
  const host=new Element("aside"); document.body.append(host); let chosen=0;
  let first=context.window.AIPhotoInfo.render(host,101,{onPerson:id=>{chosen=id;}});
  await flush(); await flush();
  assert.match(host.textContent,/分辨率1200 × 800 像素/,"photo facts show source pixel resolution");
  assert.match(host.textContent,/D:\\相册\\旅行\\照片\\海边\.jpg/);
  assert.match(host.textContent,/\\\\server\\share\\archive\\2020\\海边\.jpg/);
  assert.match(host.textContent,/D:\\根目录照片\.jpg/,"a drive root keeps its root separator when joined");
  assert.ok(!host.textContent.includes("D:\\library\\escape.jpg"),"parent traversal is not shown as a complete path");
  assert.ok(!host.textContent.includes("D:\\library\\rooted.jpg"),"rooted relative paths are rejected");
  assert.ok(!host.textContent.includes("D:\\library\\E:drive-relative.jpg"),"drive-relative paths are rejected");
  assert.match(host.textContent,/文件时间 2020-01-02 03:04:05\.000/);
  assert.equal(maps[0].options.center[0],0); assert.equal(maps[0].options.center[1],0);
  maps[0].events.load(); assert.equal(JSON.stringify(maps[0].source.source.data.geometry.coordinates),"[0,0]");
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

  const viewer=context.window.AIPhotoInfo.open(101,{returnFocus:null});
  await flush(); await flush();
  const dialog=document.body.children.find(n=>n.className==="photo-viewer-dialog");
  dialog.fire("click",{target:dialog.children[0]});
  assert.equal(dialog.open,true,"clicks inside the photo viewer do not dismiss it");
  dialog.fire("click",{target:dialog});
  assert.equal(dialog.open,false,"clicking the backdrop dismisses the shared viewer");
  await flush();
  assert.equal(maps.at(-1).removed,true,"closing the viewer removes its map instance");

  const staleActionHost=new Element("aside"); document.body.append(staleActionHost);
  const pendingAction=context.window.AIPhotoInfo.render(staleActionHost,101); await flush(); await flush();
  deferLocal=true;
  all(staleActionHost,n=>n.className==="photo-info-local-action"&&n.textContent==="定位文件位置")[0].fire("click");
  await flush(); pendingAction.dispose(); staleActionHost.replaceChildren(new Element("p")); releaseLocal(); await flush();
  assert.ok(!staleActionHost.textContent.includes("已请求资源管理器定位"),"a response after dispose cannot update the replacement panel");
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
        assert "/web/photo-info.js?v=p4-person-ux-simplify-a-1" in document
        assert "/web/photo-info.css?v=p4-photo-local-open-a-1" in document
    assert "search.js?v=p4-photo-view-close-return-a-1" in (web / "search.html").read_text(encoding="utf-8")
    assert "persons-albums.js?v=p4-person-regroup-a-1" in (web / "persons.html").read_text(encoding="utf-8")
    assert "places-albums.js?v=p4-place-album-perf-a-2" in (web / "places.html").read_text(encoding="utf-8")
    assert 'event.target === dialog' in (web / "places-albums.js").read_text(encoding="utf-8"), "the place viewer fallback closes on backdrop clicks"
    assert 'element("button", "thumb-frame")' in (web / "app.js").read_text(encoding="utf-8")
    assert 'element("button", "thumb-frame")' in (web / "search.js").read_text(encoding="utf-8")
    assert 'className = "map-photo-open"' in (web / "map.js").read_text(encoding="utf-8")
    assert 'el("button", "album-photo-open")' in (web / "places-albums.js").read_text(encoding="utf-8")
    assert 'ui.button("查看整张照片"' in (web / "persons.js").read_text(encoding="utf-8")


def test_home_detail_back_restores_browse_and_source_filter() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the synthetic home-history check"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict"),values=new Map();
const location={hash:"",pathname:"/",search:""},sections={sources:{hidden:false},browse:{hidden:true},detail:{hidden:true}};
const window={location,sessionStorage:{getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)},
  requestAnimationFrame:fn=>fn(),scrollTo:(_x,y)=>{window.restoredScroll=y;}};
const document={readyState:"loading",addEventListener(){}};
let code=fs.readFileSync("src/photo_search/web/app.js","utf8");
code=code.replace('  if (document.readyState === "loading")',
  '  globalThis.ui={state,handleHash,restoreBrowseReturn,setElements(value){els=value;}};\n  if (document.readyState === "loading")');
const context={document,window,location,history:{replaceState(){}},URLSearchParams,URL,console};
vm.createContext(context);vm.runInContext(code,context);
const ui=context.ui,main={focus(){}},assetGrid={querySelector(){return null;}};
ui.setElements({views:sections,navButtons:[],main,assetGrid});
const saved={assetId:"42",sourceId:"12",order:"time_asc",captureFrom:"2025-01-01",captureTo:"2025-02-01",scrollY:420,returnAssetId:"42",returnAssetTop:20,count:125};
window.sessionStorage.setItem("aisearch.home-browse-return.v1",JSON.stringify(saved));
assert.equal(ui.restoreBrowseReturn("42"),true);
assert.equal(ui.state.browse.sourceId,"12");assert.equal(ui.state.browse.order,"time_asc");
assert.equal(ui.state.browse.captureFrom,"2025-01-01");assert.equal(ui.state.browse.captureTo,"2025-02-01");
assert.equal(ui.state.browse.returnTargetCount,125,"the snapshot records how many browse results to reload");
ui.state.browse.loaded=true;ui.state.browse.count=125;ui.state.detailRouteActive=true;location.hash="";ui.handleHash();
assert.equal(sections.browse.hidden,false,"Back from an asset detail returns to the browse grid");
assert.equal(sections.sources.hidden,true);assert.equal(window.restoredScroll,420);
assert.equal(window.sessionStorage.getItem("aisearch.home-browse-return.v1"),null,"the return snapshot is consumed once");
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
