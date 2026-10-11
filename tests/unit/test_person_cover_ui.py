"""Run the real cover-selection flow using a small DOM and synthetic HTTP responses."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_cover_selects_own_face_saves_updates_cards_and_handles_failure() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the personal cover interaction check"
    script = r"""
const fs = require("fs"), vm = require("vm"), assert = require("assert/strict");
class Element {
  constructor(tag) {
    this.tag = tag; this.children = []; this.events = {}; this.value = ""; this.hidden = false;
    this.classList = {add() {}};
  }
  set textContent(value) { this.text = value; this.children = []; }
  get textContent() { return this.text || ""; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute() {} removeAttribute(key) { delete this[key]; } focus() {}
  addEventListener(event, callback) { this.events[event] = callback; }
  showModal() { this.open = true; }
  close() { this.open = false; if (this.events.close) this.fire("close"); }
  fire(event) { this.events[event]({preventDefault() {}}); }
}
// Model browser replacement/move semantics, including avatar reuse.
if (!Element.prototype.replaceChild) Element.prototype.replaceChild=function(next,old){const i=this.children.indexOf(old);assert(i>=0);this.children[i]=next;next.parentNode=this;return old;};
if (!Element.prototype.remove) Element.prototype.remove=function(){if(this.parentNode){this.parentNode.children=this.parentNode.children.filter(x=>x!==this);this.parentNode=null;}};
const elements = new Map();
const get = id => { if (!elements.has(id)) elements.set(id,new Element("div")); return elements.get(id); };
const first = "a".repeat(64), chosen = "b".repeat(64), other = "c".repeat(64);
let person = {person_id:7, name:"爸爸", revision:1, asset_count:1,
  avatar_face_id:first, thumbnail_url:"/api/v1/faces/"+first+"/thumbnail"};
const requests = [];
let finishSave, failSave = false;
const context = {
  URLSearchParams, crypto:{randomUUID:() => "synthetic-request"},
  setTimeout:() => 1, clearTimeout() {},
  document:{readyState:"loading",cookie:"ps_csrf=synthetic-csrf",addEventListener() {},
    getElementById:get,createElement:tag => new Element(tag)},
  fetch:async (url, options) => {
    requests.push({url,options});
    if (url.endsWith("/cover")) {
      if (failSave) return {ok:false,status:409,json:async () => ({code:"PERSON_REVISION_CONFLICT"})};
      await new Promise(resolve => { finishSave = resolve; });
      person = {...person,revision:2,avatar_face_id:chosen,
        thumbnail_url:"/api/v1/faces/"+chosen+"/thumbnail"};
      return {ok:true,json:async () => ({changed:true,person})};
    }
    if (url.includes("/assets/")) return {ok:true,json:async () => ({has_more:false,faces:[
      {face_id:chosen,assigned_person_id:7,assigned_person_name:"爸爸",thumbnail_url:"/api/v1/faces/"+chosen+"/thumbnail"},
      {face_id:other,assigned_person_id:8,assigned_person_name:"妈妈",thumbnail_url:"/api/v1/faces/"+other+"/thumbnail"}]})};
    if (url.includes("/photos")) return {ok:true,json:async () => ({person,assets:[],has_more:false,next_cursor:null})};
    return {ok:true,json:async () => ({persons:[person],has_more:false,next_cursor:null})};
  },
};
let code = fs.readFileSync("src/photo_search/web/persons-albums.js","utf8");
code = code.replace('  if (document.readyState === "loading")',
  '  globalThis.ui = {state, boot, openAlbum, preview};\n  if (document.readyState === "loading")');
if (!context.window) context.window={location:{search:"",hash:"",href:"http://local/persons"},scrollY:0,addEventListener(){},localStorage:{getItem(){return null;},setItem(){}}};
vm.createContext(context); vm.runInContext(code,context);
const ui = context.ui, flush = () => new Promise(setImmediate);
const coverButtons = card => card.children.filter(child => child.textContent === "设为封面");
(async () => {
  ui.boot(); await flush(); ui.openAlbum(person); await flush();
  get("pa-cover").fire("click"); await flush();
  assert.equal(ui.state.coverMode,true); assert.equal(get("pa-cover").textContent,"取消选择封面");
  await ui.preview(99);
  assert.equal(get("pa-preview").open,true);
  assert.equal(get("pa-preview-faces").children.length,1); // The neighbour is not a cover candidate.
  let save = coverButtons(get("pa-preview-faces").children[0])[0];
  assert.ok(save); save.fire("click"); save.fire("click");
  assert.equal(save.disabled,true);
  assert.equal(requests.filter(r => r.url.endsWith("/cover")).length,1);
  const request = requests.find(r => r.url.endsWith("/cover"));
  assert.equal(request.url,"/api/v1/persons/7/cover");
  assert.equal(request.options.method,"PUT");
  assert.equal(request.options.headers["X-CSRF-Token"],"synthetic-csrf");
  assert.deepEqual(JSON.parse(request.options.body),{face_id:chosen,expected_revision:1});
  finishSave(); await flush();
  assert.equal(ui.state.person.avatar_face_id,chosen);
  assert.equal(ui.state.coverMode,false); assert.equal(get("pa-cover").textContent,"更换封面");
  assert.equal(get("pa-preview").open,false);
  assert.equal(get("pa-status").textContent,"已更换人物封面。");
  assert.match(get("pa-album-avatar").children[0].children[1].src,new RegExp(chosen));
  assert.match(get("pa-cards").children[0].children[0].children[1].src,new RegExp(chosen));
  // Normal photo viewing also exposes cover for this person, never for a neighbour.
  await ui.preview(99);
  const cards = get("pa-preview-faces").children;
  assert.equal(cards.length,2);
  assert.equal(cards.reduce((n,c) => n+coverButtons(c).length,0),1);
  failSave = true; save = coverButtons(cards[0])[0]; save.fire("click"); await flush();
  assert.equal(get("pa-preview").open,true); assert.equal(save.disabled,false);
  assert.match(get("pa-preview-note").textContent,/刷新后再试/);
  assert.equal(ui.state.person.avatar_face_id,chosen);
})().catch(error => {console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", script],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
