"""Synthetic UI checks for people shown in search-photo preview and album deep links."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("scenario", ["avatars", "no_assigned_people", "no_assigned_people_more"])
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
  fire(event) { if (this.events[event]) this.events[event]({preventDefault() {}}); }
  showModal() { this.open = true; }
  close() { this.open = false; }
}
const elements = new Map();
const get = id => { if (!elements.has(id)) elements.set(id, new Element("div")); return elements.get(id); };
const avatar = "a".repeat(64), fallbackFace = "b".repeat(64);
const location = {href:"/search",search:""};
const response = body => ({ok:true,status:200,headers:{get:() => "application/json"},json:async () => body});
const context = {
  URLSearchParams, encodeURIComponent, isFinite,
  document:{readyState:"loading",cookie:"ps_csrf=synthetic",addEventListener() {},getElementById:get,
    createElement:tag => new Element(tag)},
  window:{location},
  fetch:async url => {
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
    if (url === "/api/v1/persons/7") return response({person_id:7,name:"\u5988\u5988",avatar_face_id:avatar});
    if (url === "/api/v1/persons/8") return response({person_id:8,name:"\u7238\u7238",avatar_face_id:null});
    if (url === "/api/v1/persons/9") return response({person_id:9,canonical_person_id:7,name:"\u5988\u5988",avatar_face_id:avatar});
    throw new Error("unexpected URL " + url);
  },
};
let code = fs.readFileSync("src/photo_search/web/search.js", "utf8");
code = code.replace('  if (document.readyState === "loading")',
  '  globalThis.ui = {state, openSearchPreview, setElements(value) { els = value; }};\n  if (document.readyState === "loading")');
vm.createContext(context); vm.runInContext(code, context);
const ui = context.ui, flush = () => new Promise(setImmediate);
(async () => {
  const modal = get("modal"); modal.open = true;
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
  if (process.argv[1].startsWith("no_assigned_people")) {
    assert.equal(get("people").hidden, false);
    assert.ok(get("note").textContent.length > 0);
    assert.equal(get("list").children.length, 0);
    if (process.argv[1] === "no_assigned_people_more") {
      assert.match(get("note").textContent, /前 200 张人脸/);
      assert.match(get("note").textContent, /更多人脸待检查/);
    } else {
      assert.match(get("note").textContent, /还没有已归属的人物/);
    }
    return;
  }
  const cards = get("list").children;
  assert.equal(cards.length, 2); // Duplicate faces and merged IDs become one canonical avatar.
  assert.equal(cards[0].children[1].textContent, "\u5988\u5988");
  assert.equal(cards[0].children[0].src, "/api/v1/faces/"+avatar+"/thumbnail?v=p4-search-person-link-a-1");
  assert.equal(cards[1].children[1].textContent, "\u7238\u7238\uff08\u6682\u7528\u8fd9\u5f20\u7167\u7247\u7684\u4eba\u8138\uff09");
  assert.equal(cards[1].children[0].src, "/api/v1/faces/"+fallbackFace+"/thumbnail?v=p4-search-person-link-a-1");
  cards[0].fire("click");
  assert.equal(modal.open, true, "keep the source photo open for browser-back restoration");
  assert.equal(location.href, "/persons?person_id=7");
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


def test_person_page_opens_requested_album_and_back_clears_route() -> None:
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
  fire(event) { if(this.events[event]) this.events[event]({preventDefault(){}}); }
}
const elements=new Map(), get=id => { if(!elements.has(id)) elements.set(id,new Element("div")); return elements.get(id); };
const avatar="f".repeat(64), location={search:"?person_id=7",href:"http://localhost/persons?person_id=7",pathname:"/persons",hash:""};
const calls=[];
const response=body=>({ok:true,status:200,headers:{get:()=>"application/json"},json:async()=>body});
const context={
  URLSearchParams, URL, encodeURIComponent, crypto:{randomUUID:()=>"synthetic"}, setTimeout:()=>1, clearTimeout(){},
  document:{readyState:"loading",cookie:"ps_csrf=synthetic",addEventListener(){},getElementById:get,createElement:tag=>new Element(tag)},
  window:{location,history:{replaceState(_state,_title,url){const parsed=new URL(url,"http://localhost"); location.href=parsed.href; location.search=parsed.search;}}},
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
  '  globalThis.ui = {state, boot, requestedPersonId};\n  if (document.readyState === "loading")');
vm.createContext(context); vm.runInContext(code,context);
const ui=context.ui, flush=()=>new Promise(setImmediate);
(async()=>{
  assert.equal(ui.requestedPersonId(),7);
  ui.boot(); await flush(); await flush();
  assert.equal(ui.state.person.person_id,7);
  assert.equal(get("pa-library").hidden,true); assert.equal(get("pa-album").hidden,false);
  assert.equal(get("pa-title").textContent,"\u7238\u7238");
  assert.equal(get("pa-photos").children.length,1);
  assert.ok(calls.includes("/api/v1/persons/7"));
  assert.ok(calls.some(url=>url.startsWith("/api/v1/persons/7/photos")));
  get("pa-back").fire("click"); await flush();
  assert.equal(get("pa-library").hidden,false); assert.equal(get("pa-album").hidden,true);
  assert.equal(location.search,"");
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
