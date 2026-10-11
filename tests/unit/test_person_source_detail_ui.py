"""The person-photo button queues one face-detail run and refreshes its result."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_person_photo_source_detail_button_queues_and_refreshes() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the personal photo interaction check"
    script = r"""
const fs = require("fs"), vm = require("vm"), assert = require("assert/strict");
class Element {
  constructor(tag) { this.tag=tag; this.children=[]; this.events={}; this.value=""; this.hidden=false; this.disabled=false; this.classList={add(){}}; }
  set textContent(value) { this.text=value; this.children=[]; } get textContent() { return this.text || ""; }
  append(...items) { this.children.push(...items); } replaceChildren(...items) { this.children=items; }
  setAttribute() {} removeAttribute(key) { delete this[key]; } focus() {}
  addEventListener(event, callback) { this.events[event]=callback; }
  showModal() { this.open=true; } close() { this.open=false; if(this.events.close) this.fire("close"); }
  fire(event) { return this.events[event]({preventDefault(){}}); }
}
const elements=new Map(), get=id=>{ if(!elements.has(id)) elements.set(id,new Element("div")); return elements.get(id); };
const requests=[]; let detailReady=false, attempts=0;
const response=data=>({ok:true,json:async()=>data});
const context={URLSearchParams,crypto:{randomUUID:()=>"synthetic"},Date,
  setTimeout:fn=>{fn();return 1;},clearTimeout(){},
  document:{readyState:"loading",cookie:"ps_csrf=csrf",addEventListener(){},getElementById:get,createElement:tag=>new Element(tag)},
  fetch:async(url,options)=>{
    requests.push({url,options});
    if(url.endsWith("/face-detail")) { attempts++; return response({job_id:43+attempts,state:"QUEUED",already_available:false}); }
    if(url.endsWith("/jobs/44")) return response({job_id:44,state:"FAILED"});
    if(url.endsWith("/jobs/45")) { detailReady=true; return response({job_id:45,state:"SUCCEEDED"}); }
    if(url.endsWith("/faces")) return response({faces:detailReady?[{face_id:"a".repeat(64),source_detail:true,assigned_person_id:null,thumbnail_url:"/api/v1/faces/"+"a".repeat(64)+"/thumbnail"}]:[],has_more:false});
    return response({persons:[],has_more:false,next_cursor:null});
  }};
let code=fs.readFileSync("src/photo_search/web/persons-albums.js","utf8");
code=code.replace('  if (document.readyState === "loading")','  globalThis.ui={boot,preview,state};\n  if (document.readyState === "loading")');
context.window={location:{search:"",hash:""},addEventListener(){}};
vm.createContext(context); vm.runInContext(code,context);
(async()=>{
  context.ui.boot(); await context.ui.preview(5);
  assert.equal(get("pa-face-detail").hidden,false);
  await get("pa-face-detail").fire("click");
  const post=requests.find(item=>item.url.endsWith("/face-detail"));
  assert.equal(post.options.method,"POST");
  assert.equal(post.options.headers["X-CSRF-Token"],"csrf");
  assert.equal(requests.filter(item=>item.url.endsWith("/face-detail")).length,1);
  assert.equal(requests.filter(item=>item.url==="/api/v1/jobs/44").length,1);
  assert.equal(get("pa-face-detail").disabled,false);
  assert.match(get("pa-preview-note").textContent,/可重试/);
  await get("pa-face-detail").fire("click");
  assert.equal(requests.filter(item=>item.url.endsWith("/face-detail")).length,2);
  assert.equal(requests.filter(item=>item.url==="/api/v1/jobs/45").length,1);
  assert.equal(get("pa-preview-faces").children.length,1);
  assert.ok(get("pa-preview-faces").children[0].children.some(item=>item.textContent.includes("原图细节结果")));
  assert.equal(get("pa-face-detail").hidden,true);
  assert.match(get("pa-preview-note").textContent,/不会自动归组/);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", script],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
