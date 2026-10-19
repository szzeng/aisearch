"""Synthetic checks that search previews reuse the shared photo people surface."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("scenario", ["avatars", "count_order", "no_assigned_people", "no_assigned_people_more"])
def test_search_preview_people(scenario: str) -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the search-preview interaction check"
    script = r"""
const fs = require("fs"), vm = require("vm"), assert = require("assert/strict");
class Element {
  constructor(tag) {
    this.tag = tag; this.children = []; this.events = {}; this.hidden = false; this.attributes = {}; this.value = "";
  }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return this.text || this.children.map(c => c.textContent).join(""); }
  get firstChild() { return this.children[0] || null; }
  appendChild(child) { this.children.push(child); return child; }
  removeChild(child) { this.children.splice(this.children.indexOf(child), 1); }
  setAttribute(key, value) { this.attributes[key] = value; }
  removeAttribute(key) { delete this[key]; }
  addEventListener(event, callback) { this.events[event] = callback; }
  fire(event, extra={}) { if (this.events[event]) this.events[event](Object.assign({target:this,preventDefault() {}},extra)); }
  showModal() { this.open = true; }
  close() { this.open = false; }
}
const elements = new Map();
const get = id => { if (!elements.has(id)) elements.set(id, new Element("div")); return elements.get(id); };
const avatar = "a".repeat(64), fallbackFace = "b".repeat(64);
const location = {href:"/search",search:""};
const response = body => ({ok:true,status:200,headers:{get:() => "application/json"},json:async () => body});
const calls=[];
const context = {
  URLSearchParams, encodeURIComponent, isFinite,
  document:{readyState:"loading",cookie:"ps_csrf=synthetic",addEventListener() {},getElementById:get,
    createElement:tag => new Element(tag)},
  window:{location},
  fetch:async url => {
    calls.push(url);
    if (url === "/api/v1/assets/15/faces") return response({has_more:process.argv[1] === "no_assigned_people_more",faces:
      process.argv[1].startsWith("no_assigned_people") ? [{face_id:fallbackFace,assigned_person_id:null}] : [
        {face_id:"c".repeat(64),assigned_person_id:7,assigned_person_name:"\u5988\u5988",thumbnail_url:"/api/v1/faces/"+"c".repeat(64)+"/thumbnail"},
        {face_id:"d".repeat(64),assigned_person_id:7,assigned_person_name:"\u5988\u5988",thumbnail_url:"/api/v1/faces/"+"d".repeat(64)+"/thumbnail"},
        {face_id:fallbackFace,assigned_person_id:8,assigned_person_name:"\u7238\u7238",thumbnail_url:"/api/v1/faces/"+fallbackFace+"/thumbnail"},
        {face_id:"e".repeat(64),assigned_person_id:9,assigned_person_name:"\u5988\u5988",thumbnail_url:"/api/v1/faces/"+"e".repeat(64)+"/thumbnail"},
        {face_id:"e".repeat(64),assigned_person_id:null}
      ]});
    if (url === "/api/v1/assets/15") return response({asset_id:15,width:400,height:300,format:"jpeg",
      byte_size:1536,availability:"present",taken_time:{taken_at:"2025-01-02T03:04:05.000",label:"capture",camera:null}});
    if (url === "/api/v1/persons/7") return response({person_id:7,name:"\u5988\u5988",avatar_face_id:avatar,asset_count:2});
    if (url === "/api/v1/persons/8") return response({person_id:8,name:"\u7238\u7238",avatar_face_id:null,asset_count:process.argv[1]==="count_order"?20:2});
    if (url === "/api/v1/persons/9") return response({person_id:9,canonical_person_id:7,name:"\u5988\u5988",avatar_face_id:avatar,asset_count:2});
    throw new Error("unexpected URL " + url);
  },
};
let code = fs.readFileSync("src/photo_search/web/search.js", "utf8");
code = code.replace('  if (document.readyState === "loading")',
  '  globalThis.ui = {state, openSearchPreview, closeOnBackdrop, setElements(value) { els = value; }};\n  if (document.readyState === "loading")');
vm.createContext(context); vm.runInContext(code, context);
const ui = context.ui, flush = () => new Promise(setImmediate);
(async () => {
  const modal = get("modal"); modal.open = true;
  ui.closeOnBackdrop(modal);
  ui.state.search.preview.seq = 1;
  ui.state.search.hits = [{asset_id:15,rank:3}];
  ui.setElements({
    searchPreview:modal,
    searchPreviewImage:get("preview-image"),
    searchPreviewNote:get("preview-note"),
    searchPreviewInfo:get("preview-info"),
    searchPreviewNav:get("preview-nav"),
    searchPreviewPosition:get("preview-position"),
    searchPreviewPrev:get("preview-prev"),
    searchPreviewNext:get("preview-next"),
    searchPreviewPeople:get("people"),
    searchPreviewPeopleNote:get("note"),
    searchPreviewPeopleList:get("list"),
  });
  ui.openSearchPreview(15);
  await flush(); await flush(); await flush();
  assert.match(get("preview-image").src, /assets\/15\/preview/);
  assert.match(get("preview-info").textContent, /400 × 300.*JPEG.*1\.50 KB.*2025-01-02 03:04:05/);
  assert.equal(calls.filter(url=>/\/assets\/15\/faces$/.test(url)).length, 0, "search preview does not issue a duplicate face-list request");
  assert.equal(calls.filter(url=>/\/persons\/\d+$/.test(url)).length, 0, "search preview does not fetch person details one at a time");
  assert.equal(get("list").children.length, 0, "search preview leaves the people summary to the shared photo viewer");
  modal.fire("click",{target:modal});
  assert.equal(modal.open,false,"clicking the search viewer backdrop dismisses it");
})().catch(error => { console.error(error); process.exitCode=1; });
"""
    result = subprocess.run(
        [node, "-e", script, scenario],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("from_photo", [False, True])
def test_person_page_opens_requested_album_and_contextual_back(from_photo: bool) -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the people-album route check"
    script = r"""
const fs = require("fs"), vm = require("vm"), assert = require("assert/strict");
class Element {
  constructor(tag) { this.tag=tag; this.children=[]; this.events={}; this.hidden=false; this.value=""; this.classList={add(){}}; }
  set textContent(value) { this.text=String(value); this.children=[]; }
  get textContent() { return this.text || this.children.map(c => c.textContent).join(""); }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children=items; }
  setAttribute() {} removeAttribute(key) { delete this[key]; } focus() {}
  addEventListener(event, callback) { this.events[event]=callback; }
  showModal() { this.open=true; } close() { this.open=false; }
  fire(event,extra={}) { if(this.events[event]) this.events[event](Object.assign({target:this,preventDefault(){}},extra)); }
}
const fromPhoto=process.argv[1]==="from_photo", elements=new Map(), get=id => { if(!elements.has(id)) elements.set(id,new Element("div")); return elements.get(id); };
const avatar="f".repeat(64), search="?person_id=7"+(fromPhoto?"&from_photo=photo":""), location={search,href:"http://localhost/persons"+search,pathname:"/persons",hash:""};
let backCalls=0;
const calls=[];
const response=body=>({ok:true,status:200,headers:{get:()=>"application/json"},json:async()=>body});
const context={
  URLSearchParams, URL, encodeURIComponent, crypto:{randomUUID:()=>"synthetic"}, setTimeout:()=>1, clearTimeout(){},
  document:{readyState:"loading",cookie:"ps_csrf=synthetic",addEventListener(){},getElementById:get,createElement:tag=>new Element(tag)},
      window:{addEventListener(){},location,history:{length:2,back(){backCalls++;},replaceState(_state,_title,url){const parsed=new URL(url,"http://localhost"); location.href=parsed.href; location.search=parsed.search;}}},
  fetch:async url=>{
    calls.push(url);
    if(url==="/api/v1/persons/7") return response({person_id:7,canonical_person_id:7,name:"\u7238\u7238",revision:2,asset_count:3,face_count:4,avatar_face_id:avatar});
    if(url.startsWith("/api/v1/persons/7/photos")) return response({person:{person_id:7,name:"\u7238\u7238",asset_count:3,thumbnail_url:"/api/v1/faces/"+avatar+"/thumbnail"},assets:[{asset_id:31}],has_more:false,next_cursor:null});
    if(url.startsWith("/api/v1/persons/browse")) return response({persons:[],has_more:false,next_cursor:null});
    throw new Error("unexpected URL "+url);
  },
};
let code=fs.readFileSync("src/photo_search/web/persons-albums.js","utf8");
code=code.replace('  if (document.readyState === "loading")',
  '  globalThis.ui = {state, boot, requestedPersonId, photoReturnRequested, closeOnBackdrop};\n  if (document.readyState === "loading")');
vm.createContext(context); vm.runInContext(code,context);
const ui=context.ui, flush=()=>new Promise(setImmediate);
(async()=>{
  assert.equal(ui.requestedPersonId(),7);
  assert.equal(ui.photoReturnRequested(),fromPhoto);
  get("pa-back").textContent="\u2190 \u6240\u6709\u4eba\u7269";
  ui.boot(); await flush(); await flush();
  assert.equal(ui.state.person.person_id,7);
  assert.equal(get("pa-library").hidden,true); assert.equal(get("pa-album").hidden,false);
  assert.equal(get("pa-title").textContent,"\u7238\u7238");
  assert.equal(get("pa-photos").children.length,1);
  assert.equal(get("pa-back").textContent,fromPhoto?"\u2190 \u8fd4\u56de\u67e5\u770b\u7167\u7247":"\u2190 \u6240\u6709\u4eba\u7269");
  const preview=get("pa-preview"); preview.open=true;
  preview.fire("click",{target:get("pa-preview-image")}); assert.equal(preview.open,true,"clicks inside the person viewer do not dismiss it");
  preview.fire("click",{target:preview}); assert.equal(preview.open,false,"clicking the person viewer backdrop dismisses it");
      assert.equal(calls.includes("/api/v1/persons/7"),false,"the photo-origin deep link does not make a redundant detail request");
      assert.equal(calls.filter(url=>url==="/api/v1/persons/7/photos?limit=1").length,1,"the album summary provides the canonical person and effective avatar in one bounded request");
      assert.ok(calls.some(url=>url.startsWith("/api/v1/persons/7/photos?limit=60")));
  get("pa-back").fire("click"); await flush();
  if(fromPhoto) { assert.equal(backCalls,1,"photo-origin people routes return through browser history"); return; }
  assert.equal(get("pa-library").hidden,false); assert.equal(get("pa-album").hidden,true);
  assert.equal(location.search,"");
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", script, "from_photo" if from_photo else "direct"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
