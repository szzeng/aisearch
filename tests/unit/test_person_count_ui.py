"""Show authoritative person totals for named, unnamed, and ignored lists."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_person_count_ui_tracks_named_unnamed_and_hidden_views() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the person-count UI check"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.events={};this.attributes={};this.value="";this.hidden=false;this.disabled=false;this.dataset={};this.classList={add(){},remove(){}};}
  set textContent(value){this.text=String(value);this.children=[];} get textContent(){return this.text||"";}
  append(...items){this.children.push(...items);} replaceChildren(...items){this.text="";this.children=items;}
  setAttribute(name,value){this.attributes[name]=String(value);} removeAttribute(name){delete this.attributes[name];}
  addEventListener(name,fn){this.events[name]=fn;} fire(name){return this.events[name]({preventDefault(){}});}
  focus(){} close(){}
}
const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element("div"));return elements.get(id);};
const requests=[];
const context={URL,URLSearchParams,Date,crypto:{randomUUID:()=>"synthetic"},setTimeout:()=>1,clearTimeout(){},
 window:{location:{search:"",href:"http://local/persons"},confirm:()=>false,addEventListener(){}},
 document:{readyState:"loading",cookie:"ps_csrf=csrf",addEventListener(name,fn){this.ready=fn;},getElementById:get,createElement:tag=>new Element(tag)},
 fetch:async(url)=>{requests.push(url);let body={};
   if(url.includes("/persons/browse")){const q=new URLSearchParams(url.split("?")[1]),hidden=q.get("visibility")==="hidden",unnamed=q.get("kind")==="unnamed";body={persons:[],total:hidden?2:unnamed?3:8,has_more:false,next_cursor:null};}
   else if(url.includes("/faces/inbox/summary"))body={total:0,reasons:{},held:0};
   else if(url.includes("/faces/inbox/groups"))body={cards:[],has_more:false,next_cursor:null};
   else if(url==="/api/v1/sources")body={sources:[]};
   else if(url.includes("/faces/source-detail-batches/active"))body={state:"IDLE",batch_id:null};
   else if(url.includes("/faces/source-detail-preview"))body={total:0};
   return {ok:true,json:async()=>body};}};
const navigate=(_state,_title,path)=>{const url=new URL(path,context.window.location.href);Object.assign(context.window.location,{href:url.href,search:url.search,hash:url.hash});};
context.window.history={pushState:navigate,replaceState:navigate};
vm.createContext(context);vm.runInContext(fs.readFileSync("src/photo_search/web/persons-albums.js","utf8"),context);
const flush=async()=>{await new Promise(resolve=>setImmediate(resolve));await new Promise(resolve=>setImmediate(resolve));};
(async()=>{
 context.document.ready();await flush();assert.equal(get("pa-list-count").textContent,"人物 8 人");
 get("pa-organize").fire("click");await flush();assert.equal(get("pa-list-count").textContent,"未命名人物 3 人");
 get("pa-hidden-toggle").fire("click");await flush();assert.equal(get("pa-list-count").textContent,"暂时忽略 2 人");
 const browse=requests.filter(url=>url.includes("/persons/browse"));
 assert(browse[0].includes("kind=named")&&browse[0].includes("visibility=visible"));
 assert(browse[1].includes("kind=unnamed")&&browse[1].includes("visibility=visible"));
 assert(browse[2].includes("kind=all")&&browse[2].includes("visibility=hidden"));
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
