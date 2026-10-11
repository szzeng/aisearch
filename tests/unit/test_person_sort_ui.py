"""All personal people views request global count order and independent first pages."""

import shutil
import subprocess
from pathlib import Path


def test_person_sort_ui_sends_count_order_for_named_unnamed_and_hidden_pages() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the person sort interaction check"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.events={};this.value="";this.hidden=false;this.classList={add(){}};}
  set textContent(value){this.text=value;this.children=[];} get textContent(){return this.text||"";}
  append(...items){this.children.push(...items);} replaceChildren(...items){this.children=items;}
  replaceChild(next,old){const i=this.children.indexOf(old);assert(i>=0);this.children[i]=next;return old;}
  remove(){if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(x=>x!==this);}
  setAttribute(){} removeAttribute(){} focus(){} addEventListener(name,fn){this.events[name]=fn;}
  fire(name){return this.events[name]({preventDefault(){}});}
}
const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element("div"));return elements.get(id);};
const requests=[]; let page=0;
const context={URLSearchParams,Date,crypto:{randomUUID:()=>"synthetic"},setTimeout:()=>1,clearTimeout(){},
 window:{addEventListener(){},location:{search:""}},
 document:{readyState:"loading",cookie:"ps_csrf=csrf",addEventListener(name,fn){this.ready=fn;},getElementById:get,createElement:tag=>new Element(tag)},
 fetch:async(url)=>{requests.push(url);if(url.includes("/persons/browse")){page++;return {ok:true,json:async()=>({persons:[{person_id:page,name:"person"+page,revision:1,asset_count:page,thumbnail_url:null}],has_more:true,next_cursor:"cursor-"+page})};}return {ok:true,json:async()=>({persons:[],faces:[],has_more:false,next_cursor:null})};}};
let code=fs.readFileSync("src/photo_search/web/persons-albums.js","utf8");
vm.createContext(context);vm.runInContext(code,context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 context.document.ready();await flush();
 assert.match(requests[0],/sort=photos_recent_desc/);
 get("pa-organize").fire("click");await flush();
 const browse=()=>requests.filter(url=>url.includes('/persons/browse?'));
 assert.match(browse()[1],/sort=photos_recent_desc/);assert.match(browse()[1],/kind=unnamed/);assert.doesNotMatch(browse()[1],/cursor=/);
 await get("pa-more").fire("click");await flush();
 assert.match(browse()[2],/sort=photos_recent_desc/);assert.match(browse()[2],/cursor=cursor-2/);
 get("pa-hidden-toggle").fire("click");await flush();
 assert.match(browse()[3],/sort=photos_recent_desc/);assert.match(browse()[3],/visibility=hidden/);assert.doesNotMatch(browse()[3],/cursor=/);
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
