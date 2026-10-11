"""Real person-preview navigation over a tiny DOM and synthetic paged HTTP responses."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("scenario", ["order", "failure", "search", "close", "stale_faces"])
def test_person_preview_navigation(scenario: str) -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the personal preview interaction check"
    script = r"""
const fs = require("fs"), vm = require("vm"), assert = require("assert/strict");
class Element {
  constructor(tag) {
    this.tag = tag; this.children = []; this.events = {}; this.value = ""; this.hidden = false;
    this.classList = {add() {}}; this.modalCalls = 0;
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
const person = {person_id:7,name:"爸爸",revision:1,asset_count:4,thumbnail_url:null};
const requests = [];
let failPage = false, holdPage = false, releasePage, holdFace = false, releaseFace;
const faceId = id => id.toString(16).padStart(64,"0");
const result = data => ({ok:true,json:async () => data});
const context = {
  URLSearchParams, crypto:{randomUUID:() => "synthetic-request"}, setTimeout:() => 1, clearTimeout() {},
  document:{readyState:"loading",cookie:"ps_csrf=synthetic-csrf",addEventListener() {},
    getElementById:get,createElement:tag => new Element(tag)},
  fetch:async (url,options) => {
    requests.push({url,options});
    if (url.includes("/face-review")) return result({groups:[],has_more:false});
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
      return result({person,assets:(next ? [10,70] : [30,10]).map(asset_id => ({asset_id})),
        next_cursor:next ? null : "album-next",has_more:!next});
    }
    return result({persons:[],has_more:false,next_cursor:null});
  },
};
let code = fs.readFileSync("src/photo_search/web/persons-albums.js","utf8");
code = code.replace('  if (document.readyState === "loading")',
  '  globalThis.ui = {state, boot, openAlbum, preview, loadPhotos};\n  if (document.readyState === "loading")');
if (!context.window) context.window={location:{search:"",hash:"",href:"http://local/persons"},scrollY:0,addEventListener(){},localStorage:{getItem(){return null;},setItem(){}}};
vm.createContext(context); vm.runInContext(code,context);
const ui = context.ui, flush = () => new Promise(setImmediate);
function current(id) {
  assert.match(get("pa-preview-image").src,new RegExp("assets/"+id+"/preview"));
  assert.equal(ui.state.previewAssetId,id);
  const crop = get("pa-preview-faces").children[0].children.find(c => c.className === "pa-picture pa-avatar");
  assert.match(crop.children[1].src,new RegExp(faceId(id)));
}
const pages = () => requests.filter(r => r.url.includes("/photos") && r.url.includes("cursor="));
(async () => {
  ui.boot(); ui.openAlbum(person); await flush();
  const scenario = process.argv[1];
  if (scenario === "order") {
    get("pa-photos").children[0].fire("click"); await flush(); current(30);
    assert.equal(get("pa-preview-prev").disabled,true); assert.equal(get("pa-preview-next").disabled,false);
    assert.equal(get("pa-preview-position").textContent,"第 1 张");
    get("pa-preview-next").fire("click"); await flush(); current(10);
    get("pa-preview-next").fire("click"); get("pa-preview-next").fire("click"); await flush(); current(70);
    assert.equal(pages().length,1); assert.deepEqual(Array.from(ui.state.photoIds),[30,10,70]);
    assert.equal(get("pa-preview-next").disabled,true); assert.equal(get("pa-preview-position").textContent,"第 3 张");
    assert.equal(get("pa-preview").modalCalls,1);
    get("pa-preview-prev").fire("click"); await flush(); current(10);
    get("pa-preview-prev").fire("click"); await flush(); current(30);
    get("pa-preview-prev").fire("click"); await flush(); current(30);
    await ui.preview(999); assert.equal(get("pa-preview-nav").hidden,true);
  } else if (scenario === "failure") {
    await ui.preview(10); failPage=true;
    get("pa-preview-next").fire("click"); await flush(); current(10);
    assert.match(get("pa-preview-note").textContent,/重试/);
    assert.equal(get("pa-preview-next").disabled,false);
    assert.deepEqual(Array.from(ui.state.photoIds),[30,10]);
    failPage=false; get("pa-preview-next").fire("click"); await flush(); current(70);
    assert.equal(pages().length,2);
  } else if (scenario === "search") {
    get("pa-photo-query").value="海边"; get("pa-search-photos").fire("submit"); await flush();
    assert.deepEqual(Array.from(ui.state.photoIds),[80,10]);
    await ui.preview(10); get("pa-preview-next").fire("click"); await flush(); current(50);
    const query = requests.filter(r => r.url === "/api/v1/search");
    assert.equal(query.length,2);
    const body = JSON.parse(query[1].options.body);
    assert.deepEqual(body.person_ids,[7]); assert.equal(body.text,"海边");
    assert.equal(body.cursor,"search-next"); assert.equal(body.search_session_id,"synthetic-search");
    assert.equal(pages().length,0);
    get("pa-preview-prev").fire("click"); await flush(); current(10);
    get("pa-preview-close").fire("click"); get("pa-all-photos").fire("click"); await flush();
    assert.deepEqual(Array.from(ui.state.photoIds),[30,10]);
  } else if (scenario === "close") {
    await ui.preview(10); holdPage=true; get("pa-preview-next").fire("click");
    assert.equal(get("pa-preview-next").disabled,true);
    get("pa-preview-close").fire("click"); releasePage(); await flush();
    assert.equal(get("pa-preview").open,false); assert.equal(get("pa-preview-image").src,undefined);
    assert.equal(ui.state.previewMoving,false); assert.equal(get("pa-preview-nav").hidden,true);
    assert.equal(requests.filter(r => r.url.includes("/assets/70/faces")).length,0);
    holdPage=false; await ui.preview(70); current(70);
    // Changing the list while a continuation is pending must not advance an old viewer.
    get("pa-preview-close").fire("click"); await ui.loadPhotos(false); await ui.preview(10);
    holdPage=true; get("pa-preview-next").fire("click");
    await ui.loadPhotos(false); releasePage(); await flush();
    assert.equal(ui.state.previewAssetId,10); assert.equal(get("pa-preview-nav").hidden,true);
  } else if (scenario === "stale_faces") {
    holdFace=true; const earlier = ui.preview(30);
    await ui.preview(10); current(10); releaseFace(); await earlier; current(10);
    assert.equal(get("pa-preview-position").textContent,"第 2 张");
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
