"""Real person-preview navigation over a tiny DOM and synthetic paged HTTP responses."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("scenario", ["order", "failure", "search", "close", "stale_faces", "shared_write", "shared_write_keep", "shared_write_timeline", "shared_write_stale", "shared_write_avatar"])
def test_person_preview_navigation(scenario: str) -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the personal preview interaction check"
    script = r"""
const fs = require("fs"), vm = require("vm"), assert = require("assert/strict");
class Element {
  constructor(tag) {
    this.tag = tag; this.children = []; this.events = {}; this.value = ""; this.hidden = false;
    this.classList = {add() {}}; this.modalCalls = 0; this.dataset = {};
  }
  set textContent(value) { this.text = value; this.children = []; }
  get textContent() { return this.text || ""; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute() {} removeAttribute(key) { delete this[key]; } focus() {}
  addEventListener(event, callback) { this.events[event] = callback; }
  showModal() { assert.equal(!!this.open,false); this.open = true; this.modalCalls++; }
  close() { this.open = false; if (this.events.close) this.fire("close"); }
  fire(event) { return this.events[event]({preventDefault() {}}); }
}
// Model browser replacement/move semantics, including avatar reuse.
if (!Element.prototype.replaceChild) Element.prototype.replaceChild=function(next,old){const i=this.children.indexOf(old);assert(i>=0);this.children[i]=next;next.parentNode=this;return old;};
if (!Element.prototype.remove) Element.prototype.remove=function(){if(this.parentNode){this.parentNode.children=this.parentNode.children.filter(x=>x!==this);this.parentNode=null;}};
const elements = new Map();
const get = id => { if (!elements.has(id)) elements.set(id,new Element("div")); return elements.get(id); };
let person = {person_id:7,name:"爸爸",revision:1,asset_count:4,thumbnail_url:null};
const requests = [];
const changeTokens=[]; let sharedOptions, sharedHandle, removedFromAlbum=false;
let failPage = false, holdPage = false, releasePage, holdFace = false, releaseFace, holdPerson=false, releasePerson;
let timelineOptions, timelineResets=0, timelineRefreshes=0;
const faceId = id => id.toString(16).padStart(64,"0");
const result = data => ({ok:true,json:async () => data});
const context = {
  URLSearchParams, crypto:{randomUUID:() => "synthetic-request"}, setTimeout:() => 1, clearTimeout() {},
  document:{readyState:"loading",cookie:"ps_csrf=synthetic-csrf",addEventListener() {},
    getElementById:get,createElement:tag => new Element(tag)},
  fetch:async (url,options) => {
    requests.push({url,options});
    if(url==="/api/v1/persons/7") {
      const {thumbnail_url,...record}=person;
      return result({...record,avatar_face_id:null}); // The detail contract has no display-avatar URL.
    }
    if(url==="/api/v1/persons/7/photos?limit=1") {
      const snapshot=person;
      if(holdPerson) await new Promise(resolve=>{releasePerson=resolve;});
      return result({person:snapshot,assets:[],has_more:false});
    }
    if (url.includes("/face-review")) return result({groups:[],has_more:false});
    if (/^\/api\/v1\/assets\/\d+$/.test(url)) return result({asset_id:Number(url.split("/").pop()),locations:[]});
    if (url.includes("/assets/")) {
      const id = Number(url.match(/assets\/(\d+)/)[1]);
      if (holdFace && id === 30) await new Promise(resolve => {releaseFace=resolve;});
      return result({faces:[{face_id:faceId(id),assigned_person_id:7,assigned_person_name:"爸爸",
        thumbnail_url:"/api/v1/faces/"+faceId(id)+"/thumbnail"}],has_more:false});
    }
    if (url === "/api/v1/search") {
      const body = JSON.parse(options.body);
      const next = !!body.cursor;
      return result({hits:(next ? [50] : [80,10]).map(asset_id => ({asset_id})),
        next_cursor:next ? null : "search-next",has_more:!next,search_session_id:"synthetic-search"});
    }
    if (url.includes("/photos")) {
      const next = new URLSearchParams(url.split("?")[1]).has("cursor");
      if (next && holdPage) await new Promise(resolve => {releasePage=resolve;});
      if (next && failPage) return {ok:false,status:500,json:async () => ({code:"SYNTHETIC_FAILURE"})};
      return result({person,assets:(removedFromAlbum ? [] : next ? [10,70] : [30,10]).map(asset_id => ({asset_id})),
        next_cursor:next ? null : "album-next",has_more:!next});
    }
    return result({persons:[],has_more:false,next_cursor:null});
  },
};
let code = fs.readFileSync("src/photo_search/web/persons-albums.js","utf8");
code = code.replace('  if (document.readyState === "loading")',
  '  globalThis.ui = {state, boot, openAlbum, preview, loadPhotos};\n  if (document.readyState === "loading")');
if (!context.window) context.window={location:{search:"",hash:"",href:"http://local/persons"},scrollY:0,addEventListener(){},localStorage:{getItem(){return null;},setItem(key,value){changeTokens.push({key,value});}}};
if(process.argv[1].startsWith("shared_write")) context.window.AIPhotoInfo={open(id,options){
  sharedOptions=options; sharedHandle={dialog:{open:true},assetId:id,setAsset(id){this.assetId=id;},setPosition(value){this.position=value;},setNavigationEnabled(previous,next){this.previous=previous;this.next=next;},close(){this.dialog.open=false;options.onClose();}};
  return sharedHandle;
}};
function emitTimeline(){timelineOptions.adapter.onPage({assets:(removedFromAlbum?[]:[30,10]).map(asset_id=>({asset_id})),next_cursor:null},null,{kind:"person",person_id:7});}
if(["shared_write_timeline","shared_write_avatar"].includes(process.argv[1])) context.window.PhotoTimeline={mount(options){
  timelineOptions=options; Promise.resolve().then(emitTimeline);
  return {reset(){timelineResets++;},refresh(){timelineRefreshes++;},setScope(){},append:async()=>null};
}};
const viewerDom=require("./tests/unit/fixtures/shared_photo_viewer_dom.js");
viewerDom.install(context,Element);
vm.createContext(context);
if(!process.argv[1].startsWith("shared_write")) vm.runInContext(fs.readFileSync("src/photo_search/web/photo-info.js","utf8"),context);
vm.runInContext(code,context);
const ui = context.ui, flush = () => new Promise(setImmediate);
let lastViewer;
function viewer() { if(ui.state.previewHandle) lastViewer=ui.state.previewHandle; return lastViewer; }
function current(id) {
  lastViewer=ui.state.previewHandle;
  assert.match(viewer().image.src,new RegExp("assets/"+id+"/preview"));
  assert.equal(ui.state.previewAssetId,id);
  assert.ok(viewerDom.all(viewer().panel,n=>n.tag==="img"&&String(n.src).includes(faceId(id))).length,
    "the real shared viewer renders the current photo's person crop");
}
function control(name) {
  if(name==="previous") return viewer().controls.children[5];
  if(name==="next") return viewer().controls.children[7];
  if(name==="position") return viewer().controls.children[6];
  return viewerDom.all(viewer().dialog,n=>n.className==="photo-viewer-close")[0];
}

const pages = () => requests.filter(r => r.url.includes("/photos") && r.url.includes("cursor="));
(async () => {
  if(process.argv[1]==="shared_write_avatar") person={...person,avatar_face_id:faceId(30),thumbnail_url:"/api/v1/faces/"+faceId(30)+"/thumbnail"};
  ui.boot(); ui.openAlbum(person); await flush();
  const scenario = process.argv[1];
  if (scenario === "shared_write") {
    await ui.preview(30); await flush(); const originalHandle=ui.state.previewHandle;
    const initialReads=requests.filter(r=>r.url.includes("/photos?limit=60")).length;
    person={...person,revision:2,asset_count:0,avatar_face_id:null,thumbnail_url:null};
    removedFromAlbum=true;
    assert.equal(typeof sharedOptions.onFacesChanged,"function","shared viewer writes have an album refresh callback");
    await sharedOptions.onFacesChanged(30,{changed:true});
    assert.equal(changeTokens.length,1,"reuse the established person-change notification");
    assert.equal(changeTokens[0].key,"aisearch.persons.changed");
    assert.equal(ui.state.person.asset_count,0,"refresh the current person's authoritative count");
    assert.match(get("pa-album-note").textContent,/0 张照片/);
    assert.deepEqual(Array.from(ui.state.photoIds),[],"the removed member photo leaves the album behind the viewer");
    assert.equal(get("pa-photos").children.length,0);
    assert.equal(requests.filter(r=>r.url.includes("/photos?limit=60")).length,initialReads+1);
    assert.equal(ui.state.previewHandle,originalHandle,"refresh must not recreate the viewer");
    assert.equal(sharedHandle.dialog.open,true,"keep the current photo open after membership changes");
    assert.equal(ui.state.previewAssetId,30);
    assert.equal(ui.state.peopleRefreshPending,true,"other person-list rows refresh when revisited");
  } else if (scenario === "shared_write_avatar") {
    const avatar=()=>viewerDom.all(get("pa-album-avatar"),node=>node.tag==="img")[0];
    assert.match(avatar().src,new RegExp(faceId(30)));
    await ui.preview(30);await flush();const handle=ui.state.previewHandle;
    await sharedOptions.onFacesChanged(30,{changed:true});emitTimeline();
    assert.ok(avatar(),"ignoring another unassigned face must not erase this person's automatic display avatar");
    assert.match(avatar().src,new RegExp(faceId(30)));
    person={...person,revision:2,avatar_face_id:faceId(10),thumbnail_url:"/api/v1/faces/"+faceId(10)+"/thumbnail"};
    await sharedOptions.onFacesChanged(30,{changed:true});emitTimeline();
    assert.match(avatar().src,new RegExp(faceId(10)),"a removed cover is replaced by the current effective member, not a stale cached image");
    person={...person,revision:3,asset_count:0,avatar_face_id:null,thumbnail_url:null};removedFromAlbum=true;
    await sharedOptions.onFacesChanged(30,{changed:true});emitTimeline();
    assert.equal(avatar(),undefined,"an empty album must not retain a stale face");
    assert.match(get("pa-album-note").textContent,/0 张照片/);
    assert.equal(ui.state.previewHandle,handle);assert.equal(handle.dialog.open,true);
  } else if (scenario === "shared_write_keep" || scenario === "shared_write_timeline") {
    await ui.preview(30); await flush(); const originalHandle=ui.state.previewHandle;
    assert.equal(sharedHandle.next,true);
    person={...person,revision:2};
    await sharedOptions.onFacesChanged(30,{changed:true});
    if(scenario==="shared_write_timeline") {assert.equal(timelineRefreshes,1,"writes refresh photos and date counts together");assert.equal(timelineResets,0);emitTimeline();}
    assert.equal(ui.state.previewHandle,originalHandle);
    assert.equal(sharedHandle.next,true,"a retained member must remain navigable after list or async timeline refresh");
    assert.equal(sharedHandle.position,"第 1 张");
    await sharedOptions.navigation.next();
    assert.equal(ui.state.previewAssetId,10);
    assert.equal(sharedHandle.assetId,10);
    if(scenario==="shared_write_timeline") {
      await sharedOptions.onFacesChanged(10,{changed:true});
      sharedHandle.close(); await ui.preview(30); await flush();
      emitTimeline();
      assert.equal(sharedHandle.next,false,"a late timeline refresh must not rebind a replacement viewer");
    }
  } else if (scenario === "shared_write_stale") {
    await ui.preview(30); await flush(); const oldCallback=sharedOptions.onFacesChanged;
    holdPerson=true;
    const pending=oldCallback(30,{changed:true});
    sharedHandle.close(); person={...person,name:"新人物资料",revision:9};
    ui.openAlbum(person); await flush(); await ui.preview(30); await flush();
    const reads=requests.filter(r=>r.url.includes("/photos?limit=60")).length;
    releasePerson(); await pending;
    assert.equal(ui.state.person.revision,9,"a delayed summary cannot roll a reopened same-person album back");
    assert.equal(get("pa-title").textContent,"新人物资料");
    assert.equal(requests.filter(r=>r.url.includes("/photos?limit=60")).length,reads);
    holdPerson=false; await oldCallback(30,{changed:true});
    assert.equal(requests.filter(r=>r.url.includes("/photos?limit=60")).length,reads,"a write finishing after leaving its album only invalidates directory caches");
    assert.equal(changeTokens.length,2);
  } else if (scenario === "order") {
    get("pa-photos").children[0].fire("click"); await flush(); current(30);
    assert.equal(control("previous").disabled,true); assert.equal(control("next").disabled,false);
    assert.equal(control("position").textContent,"第 1 张");
    control("next").fire("click"); await flush(); current(10);
    control("next").fire("click"); control("next").fire("click"); await flush(); current(70);
    assert.equal(pages().length,1); assert.deepEqual(Array.from(ui.state.photoIds),[30,10,70]);
    assert.equal(control("next").disabled,true); assert.equal(control("position").textContent,"第 3 张");
    assert.equal(viewer().dialog.modalCalls,1);
    control("previous").fire("click"); await flush(); current(10);
    control("previous").fire("click"); await flush(); current(30);
    control("previous").fire("click"); await flush(); current(30);
    await ui.preview(999); await flush(); assert.equal(control("position").textContent,"");
  } else if (scenario === "failure") {
    await ui.preview(10); await flush(); failPage=true;
    control("next").fire("click"); await flush(); current(10);
    assert.match(ui.state.previewUI.note.textContent,/重试/);
    assert.equal(control("next").disabled,false);
    assert.deepEqual(Array.from(ui.state.photoIds),[30,10]);
    failPage=false; control("next").fire("click"); await flush(); current(70);
    assert.equal(pages().length,2);
  } else if (scenario === "search") {
    get("pa-photo-query").value="海边"; get("pa-search-photos").fire("submit"); await flush();
    assert.deepEqual(Array.from(ui.state.photoIds),[80,10]);
    await ui.preview(10); await flush(); control("next").fire("click"); await flush(); current(50);
    const query = requests.filter(r => r.url === "/api/v1/search");
    assert.equal(query.length,2);
    const body = JSON.parse(query[1].options.body);
    assert.deepEqual(body.person_ids,[7]); assert.equal(body.text,"海边");
    assert.equal(body.cursor,"search-next"); assert.equal(body.search_session_id,"synthetic-search");
    assert.equal(pages().length,0);
    control("previous").fire("click"); await flush(); current(10);
    control("close").fire("click"); get("pa-all-photos").fire("click"); await flush();
    assert.deepEqual(Array.from(ui.state.photoIds),[30,10]);
  } else if (scenario === "close") {
    await ui.preview(10); await flush(); holdPage=true; control("next").fire("click");
    assert.equal(control("next").disabled,true);
    control("close").fire("click"); releasePage(); await flush();
    assert.equal(viewer().dialog.open,false); assert.equal(viewer().image.src,undefined);
    assert.equal(ui.state.previewMoving,false); assert.equal(viewer().dialog.isConnected,false,"closed viewer navigation is removed from the document");
    assert.equal(requests.filter(r => r.url.includes("/assets/70/faces")).length,0);
    holdPage=false; await ui.preview(70); await flush(); current(70);
    // Changing the list while a continuation is pending must not advance an old viewer.
    control("close").fire("click"); await ui.loadPhotos(false); await ui.preview(10); await flush();
    holdPage=true; control("next").fire("click");
    await ui.loadPhotos(false); releasePage(); await flush();
    assert.equal(ui.state.previewAssetId,10); assert.equal(control("position").textContent,"");
  } else if (scenario === "stale_faces") {
    holdFace=true; const earlier = ui.preview(30); await flush();
    await ui.preview(10); await flush(); current(10); releaseFace(); await earlier; await flush(); current(10);
    assert.equal(control("position").textContent,"第 2 张");
  }
})().catch(error => {console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", script, scenario],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
