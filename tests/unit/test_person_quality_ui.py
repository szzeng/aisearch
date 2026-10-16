"""DOM regression for the non-persistent personal organization quality switch."""

import shutil
import subprocess
from pathlib import Path


def test_quality_switch_binds_requests_resets_cursors_and_clears_selections():
    node = shutil.which("node")
    assert node, "Node is required for the personal-page behavior check"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
// Keep real timer behavior during the scenario, then dispose its UI on completion.
const pendingTimers = new Set();
const testSetTimeout = (callback, delay, ...args) => {
  const timer = setTimeout(() => { pendingTimers.delete(timer); callback(...args); }, delay);
  pendingTimers.add(timer); return timer;
};
const testClearTimeout = timer => { clearTimeout(timer); pendingTimers.delete(timer); };
const disposeTimers = () => { for (const timer of pendingTimers) clearTimeout(timer); pendingTimers.clear(); };
class Element{constructor(){this.children=[];this.value="";this.hidden=false;this.disabled=false;this.checked=false;this.textContent="";this.dataset={};this.listeners={};this.attributes={};this.classList={add(){}};}append(...v){this.children.push(...v);}replaceChildren(...v){this.children=v;}setAttribute(k,v){this.attributes[k]=v;}addEventListener(k,f){this.listeners[k]=f;}querySelector(){return null;}focus(){} }
const els=new Map(),requests=[];const get=id=>{if(!els.has(id))els.set(id,new Element());return els.get(id);};
get("pa-inbox-kind").value="all";
const ctx={URLSearchParams,crypto:{randomUUID:()=>"id"},setTimeout:testSetTimeout,clearTimeout:testClearTimeout,window:{confirm:()=>true,addEventListener(){},location:{search:"",href:"http://local/persons"}},document:{readyState:"loading",cookie:"",addEventListener(){},getElementById:get,createElement:()=>new Element()},fetch:async(url)=>{requests.push(url);let value={persons:[],total:0,has_more:false,next_cursor:null,cards:[],faces:[],reasons:{},quality_hidden:3};return{ok:true,json:async()=>value};}};
let code=fs.readFileSync("src/photo_search/web/persons-albums.js","utf8");code=code.replace('  if (document.readyState === "loading")','  globalThis.ui={state,setQualityView,loadInbox,loadInboxGroup};\n  if (document.readyState === "loading")');vm.createContext(ctx);vm.runInContext(code,ctx);
(async()=>{const ui=ctx.ui;ui.state.organize=true;ui.state.selectedPeople.add(4);ui.state.selectedInboxFaces.add("old-face");ui.state.listCursor="old-people";ui.state.inboxCursor="old-inbox";
await ui.setQualityView("all");assert.equal(ui.state.qualityView,"all");assert.equal(ui.state.selectedPeople.size,0);assert.equal(ui.state.selectedInboxFaces.size,0);assert.equal(ui.state.listCursor,null);assert.equal(ui.state.inboxCursor,null);
assert(requests.some(u=>u.includes("/persons/browse?")&&u.includes("quality_view=all")));assert(requests.some(u=>u.includes("/faces/inbox/groups?")&&u.includes("quality_view=all")));assert(requests.some(u=>u.includes("/faces/inbox/summary?")&&u.includes("quality_view=all")));
const before=requests.length;await ui.setQualityView("clean");assert.equal(ui.state.qualityView,"clean");assert(requests.slice(before).some(u=>u.includes("quality_view=clean")));
let finish;ctx.fetch=()=>new Promise(resolve=>{finish=resolve;});const list=new Element();list.className="pa-inbox-group-faces";const more=new Element();more.className="pa-inbox-group-more";const details={querySelector:s=>s.includes("faces")?list:more};
const pending=ui.loadInboxGroup(details,"candidate:17","suggested",null,false);ui.state.inboxFilterSeq++;
finish({ok:true,json:async()=>({faces:[{face_id:"stale",asset_id:9,organization_reason:"NO_MATCH"}],has_more:false,next_cursor:null})});await pending;assert.equal(list.children.length,0);
console.log("quality switch and stale group response DOM contract passed");})().finally(disposeTimers).catch(e=>{console.error(e);process.exitCode=1;});
"""
    subprocess.run([node, "-e", script], check=True, cwd=Path.cwd())
