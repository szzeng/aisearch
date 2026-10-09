"""Execute the real people-list UI and verify sort changes reset pagination."""

import shutil
import subprocess
from pathlib import Path


def test_person_sort_ui_sends_selection_and_restarts_keyset() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the person sort interaction check"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.events={};this.value="";this.hidden=false;this.classList={add(){}};}
  set textContent(value){this.text=value;this.children=[];} get textContent(){return this.text||"";}
  append(...items){this.children.push(...items);} replaceChildren(...items){this.children=items;}
  setAttribute(){} removeAttribute(){} focus(){} addEventListener(name,fn){this.events[name]=fn;}
  fire(name){return this.events[name]({preventDefault(){}});}
}
const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element("div"));return elements.get(id);};
const requests=[]; let page=0;
const context={URLSearchParams,Date,crypto:{randomUUID:()=>"synthetic"},setTimeout:()=>1,clearTimeout(){},
 document:{readyState:"loading",cookie:"ps_csrf=csrf",addEventListener(name,fn){this.ready=fn;},getElementById:get,createElement:tag=>new Element(tag)},
 fetch:async(url)=>{requests.push(url);if(url.includes("/persons/browse")){page++;return {ok:true,json:async()=>({persons:[{person_id:page,name:"person"+page,revision:1,asset_count:page,thumbnail_url:null}],has_more:true,next_cursor:"cursor-"+page})};}return {ok:true,json:async()=>({persons:[],faces:[],has_more:false,next_cursor:null})};}};
let code=fs.readFileSync("src/photo_search/web/persons-albums.js","utf8");
vm.createContext(context);vm.runInContext(code,context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 get("pa-person-sort").value="photos_desc";context.document.ready();await flush();
 assert.match(requests[0],/sort=photos_desc/);
 get("pa-person-sort").value="person_id";get("pa-person-sort").fire("change");await flush();
 assert.match(requests[1],/sort=person_id/);assert.doesNotMatch(requests[1],/cursor=/);
 await get("pa-more").fire("click");await flush();
 assert.match(requests[2],/sort=person_id/);assert.match(requests[2],/cursor=cursor-2/);
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
