"""DOM behavior for adding a multi-page person selection to a collection."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_120_selected_people_submit_as_one_collection_write_without_selection_reload() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the person collection interaction check"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.events={};this.attributes={};this.value="";this.checked=false;this.hidden=false;this.disabled=false;this.classList={add(){},remove(){}};}
  set textContent(value){this.text=String(value);this.children=[];}
  get textContent(){return this.text||"";}
  append(...items){items.forEach(item=>{if(item&&typeof item==="object")item.parentNode=this;this.children.push(item);});}
  replaceChildren(...items){this.text="";this.children=[];this.append(...items);}
  setAttribute(name,value){this.attributes[name]=String(value);}
  getAttribute(name){return this.attributes[name]??null;}
  removeAttribute(name){delete this.attributes[name];}
  focus(){} close(){this.open=false;} showModal(){this.open=true;}
  addEventListener(name,fn){this.events[name]=fn;}
  async fire(name){return await this.events[name]({preventDefault(){}});}
}
const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element("div"));return elements.get(id);};
const requests=[];let page=0;
const context={URLSearchParams,URL,Date,crypto:{randomUUID:()=>"synthetic"},setTimeout:()=>1,clearTimeout(){},
 window:{location:{search:"",href:"http://local/persons"},confirm:()=>true,prompt:()=>null,history:{replaceState(){}},addEventListener(){}},
 document:{readyState:"loading",cookie:"ps_csrf=csrf",addEventListener(name,fn){this.ready=fn;},getElementById:get,createElement:tag=>new Element(tag)},
 fetch:async(url,options={})=>{requests.push({url,options});
   if(url.includes("/persons/browse")){const current=++page,start=(current-1)*40+1;return {ok:true,json:async()=>({persons:Array.from({length:40},(_,i)=>({person_id:start+i,name:"person"+(start+i),revision:1,asset_count:1,thumbnail_url:null})),has_more:current<3,next_cursor:current<3?"cursor-"+current:null})};}
   if(url.startsWith("/api/v1/persons/collections?")||url==="/api/v1/persons/collections"){return {ok:true,json:async()=>({collections:[{collection_id:7,instance_token:"a".repeat(32),name:"路人",member_count:0}],has_more:false,next_cursor:null})};}
   return {ok:true,json:async()=>({changed:120,has_more:false,next_cursor:null})};
 }};
let code=fs.readFileSync("src/photo_search/web/persons-albums.js","utf8");
code=code.replace('  if (document.readyState === "loading")','  globalThis.ui={state};\n  if (document.readyState === "loading")');
vm.createContext(context);vm.runInContext(code,context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 context.document.ready();await flush();
 await get("pa-batch-toggle").fire("click");
 await get("pa-more").fire("click");await flush();await get("pa-more").fire("click");await flush();
 const cards=get("pa-cards"),sequence=context.ui.state.listSeq,calls=requests.filter(r=>r.url.includes("/persons/browse")).length;
 assert.equal(cards.children.length,120);
 for(let index=0;index<120;index++) await cards.children[index].fire("click");
 assert.equal(cards.children.length,120);assert.equal(context.ui.state.listSeq,sequence);
 assert.equal(requests.filter(r=>r.url.includes("/persons/browse")).length,calls);
 get("pa-collection-choice").value="7";get("pa-collection-hide").checked=true;
 await get("pa-collection-add").fire("click");await flush();
 const write=requests.find(r=>r.url==="/api/v1/persons/collections/7/"+"a".repeat(32)+"/members");
 assert.ok(write);assert.deepEqual(JSON.parse(write.options.body),{action:"add",person_ids:Array.from({length:120},(_,i)=>i+1),hide_after_add:true});
 assert.equal(context.ui.state.selectedPeople.size,0);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", script], cwd=Path(__file__).resolve().parents[2], capture_output=True,
        text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
