"""Keep expanded person pages mounted while the user manages visibility selection."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_person_visibility_selection_keeps_loaded_pages_and_state() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the person-selection interaction check"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.events={};this.attributes={};this.value="";this.hidden=false;this.disabled=false;this.scrollTop=0;this.parentNode=null;this.classList={add(){},remove(){}};}
  set textContent(value){this.text=String(value);this.children.forEach(child=>child.parentNode=null);this.children=[];}
  get textContent(){return this.text||"";}
  append(...items){items.forEach(item=>{if(item&&typeof item==="object")item.parentNode=this;this.children.push(item);});}
  replaceChildren(...items){this.text="";this.children.forEach(child=>child.parentNode=null);this.children=[];this.append(...items);}
  setAttribute(name,value){this.attributes[name]=String(value);}
  getAttribute(name){return this.attributes[name]??null;}
  removeAttribute(name){delete this.attributes[name];}
  focus(){} close(){this.open=false;}
  addEventListener(name,fn){this.events[name]=fn;}
  fire(name){return this.events[name]({preventDefault(){}});}
}
const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element("div"));return elements.get(id);};
const requests=[],hiddenRemoved=new Set();let confirmResult=false,visibilityFailure=true;
const context={URLSearchParams,Date,crypto:{randomUUID:()=>"synthetic"},setTimeout:()=>1,clearTimeout(){},
 window:{location:{search:"",href:"http://local/persons"},scrollY:700,confirm:()=>confirmResult,addEventListener(){}},
 document:{readyState:"loading",cookie:"ps_csrf=csrf",addEventListener(name,fn){this.ready=fn;},getElementById:get,createElement:tag=>new Element(tag)},
 fetch:async(url,options={})=>{
   requests.push({url,options});
   if(url.includes("/persons/browse")){
     const values=new URLSearchParams(url.split("?")[1]||""),visibility=values.get("visibility")||"visible";
     const page=values.has('cursor')?Number(values.get('cursor').split('-')[1])+1:1,start=(page-1)*40;
     const ids=Array.from({length:200},(_,i)=>i+1).filter(id=>visibility!=="hidden"||!hiddenRemoved.has(id));
     return {ok:true,json:async()=>({persons:ids.slice(start,start+40).map(id=>({person_id:id,name:"person"+id,revision:1,asset_count:1,thumbnail_url:null})),has_more:true,next_cursor:visibility+"-"+page})};
   }
   if(url==="/api/v1/persons/visibility"){
     if(visibilityFailure)return {ok:false,json:async()=>({code:"SYNTHETIC_FAILURE"})};
     JSON.parse(options.body).person_ids.forEach(id=>hiddenRemoved.add(id));
     return {ok:true,json:async()=>({hidden:false,person_count:1,changed:1})};
   }
   return {ok:true,json:async()=>({persons:[],faces:[],has_more:false,next_cursor:null})};
 }};
let code=fs.readFileSync("src/photo_search/web/persons-albums.js","utf8");
code=code.replace('  if (document.readyState === "loading")','  globalThis.ui={state};\n  if (document.readyState === "loading")');
vm.createContext(context);vm.runInContext(code,context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const browseCount=()=>requests.filter(request=>request.url.includes("/persons/browse")).length;
const selectedCount=()=>Number(get("pa-selection-count").textContent.match(/\d+/)[0]);
const clickPerson=async id=>{await get("pa-cards").children[id-1].fire("click");};
(async()=>{
 context.document.ready();await flush();
 const cards=get("pa-cards"),mountedCards=cards,initialSeq=context.ui.state.listSeq;
 assert.equal(cards.children.length,40);
 await get("pa-batch-toggle").fire("click");
 assert.equal(cards.children.length,40);assert.equal(get("pa-cards"),mountedCards);assert.equal(browseCount(),1);
 assert.equal(context.ui.state.listSeq,initialSeq);assert.equal(context.ui.state.listCursor,"visible-1");
 await get("pa-more").fire("click");await flush();await get("pa-more").fire("click");await flush();
 assert.equal(cards.children.length,120);
 const expandedSeq=context.ui.state.listSeq,expandedCursor=context.ui.state.listCursor,expandedScroll=context.window.scrollY;
 await clickPerson(85);await clickPerson(20);await clickPerson(110);await clickPerson(20);
 assert.equal(cards.children.length,120);assert.equal(browseCount(),3);
 assert.equal(context.ui.state.listSeq,expandedSeq);assert.equal(context.ui.state.listCursor,expandedCursor);
 assert.equal(context.window.scrollY,expandedScroll);assert.equal(selectedCount(),2);
 assert.equal(cards.children[84].getAttribute("aria-pressed"),"true");
 assert.equal(cards.children[19].getAttribute("aria-pressed"),"false");
 assert.equal(cards.children[109].selectionHint.textContent,"已选择");
 await get("pa-batch-toggle").fire("click");
 assert.equal(cards.children.length,120);assert.equal(browseCount(),3);assert.equal(selectedCount(),0);
 assert.equal(cards.children[84].getAttribute("aria-pressed"),"false");
 await get("pa-batch-toggle").fire("click");
 await get("pa-more").fire("click");await flush();
 assert.equal(cards.children.length,160);assert.equal(cards.children[159].getAttribute("aria-pressed"),"false");
 await clickPerson(120);assert.equal(selectedCount(),1);
 await get("pa-batch-cancel").fire("click");
 assert.equal(cards.children.length,160);assert.equal(browseCount(),4);assert.equal(selectedCount(),0);
 assert.equal(cards.children[119].getAttribute("aria-pressed"),"false");
 await get("pa-hidden-toggle").fire("click");await flush();
 await get("pa-batch-toggle").fire("click");
 await get("pa-more").fire("click");await flush();await get("pa-more").fire("click");await flush();
 assert.equal(cards.children.length,120);
 await clickPerson(101);const hiddenSeq=context.ui.state.listSeq,hiddenCursor=context.ui.state.listCursor,hiddenBrowseCount=browseCount();
 confirmResult=false;await get("pa-visibility-submit").fire("click");
 assert.equal(selectedCount(),1);assert.equal(cards.children.length,120);assert.equal(browseCount(),hiddenBrowseCount);
 confirmResult=true;await get("pa-visibility-submit").fire("click");await flush();
 assert.equal(selectedCount(),1);assert.equal(cards.children.length,120);assert.equal(browseCount(),hiddenBrowseCount);
 assert.equal(context.ui.state.listSeq,hiddenSeq);assert.equal(context.ui.state.listCursor,hiddenCursor);
 visibilityFailure=false;await get("pa-visibility-submit").fire("click");await flush();
 const write=requests.filter(request=>request.url==="/api/v1/persons/visibility").at(-1);
 assert.deepEqual(JSON.parse(write.options.body),{person_ids:[101],hidden:false});
 assert.equal(selectedCount(),0);assert.equal(cards.children.length,120);
 assert.equal(browseCount(),hiddenBrowseCount+3);assert.equal(context.ui.state.listCursor,"hidden-3");
 assert(!cards.children.some(card=>card.personId===101),"restored person leaves the retained hidden pages");
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
