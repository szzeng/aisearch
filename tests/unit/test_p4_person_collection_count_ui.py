from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_collection_list_and_photo_page_show_total_distinct_photo_count() -> None:
    html = Path(__file__).resolve().parents[2] / "src/photo_search/web/persons.html"
    assert "persons-albums.js?v=p4-person-fast-organize-a-2" in html.read_text(encoding="utf-8")
    script_source = html.with_name("persons-albums.js").read_text(encoding="utf-8")
    assert "new Set([...state.selectedInboxFaces, ...state.selectedHiddenFaces]).size" in script_source
    assert "preview(Number(face.asset_id), event.currentTarget, face)" in script_source
    assert 'focusFace ? "已打开原照片，并标出本次单独收录的人脸。"' in script_source
    node = shutil.which("node")
    assert node is not None, "Node is required for the person collection count UI check"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
class Element {
  constructor(){this.children=[];this.events={};this.attributes={};this.dataset={};this.value="";this.hidden=false;this.disabled=false;this.classList={add(){},remove(){}};}
  set textContent(value){this.text=String(value);this.children=[];}
  get textContent(){return this.text||"";}
  append(...items){items.forEach(item=>{if(item&&typeof item==="object")item.parentNode=this;this.children.push(item);});}
  replaceChildren(...items){this.text="";this.children=[];this.append(...items);}
  addEventListener(name,fn){this.events[name]=fn;}
  setAttribute(name,value){this.attributes[name]=String(value);}
  focus(){}
}
const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
const collection={collection_id:9,instance_token:"b".repeat(32),name:"旅行",revision:2,member_count:6,photo_count:24};
const context={URLSearchParams,URL,Date,crypto:{randomUUID:()=>"synthetic"},setTimeout:()=>1,clearTimeout(){},
 window:{location:{search:"",href:"http://local/persons"},confirm:()=>true,prompt:()=>"旅行新名",history:{replaceState(){}},addEventListener(){}},
 document:{readyState:"loading",cookie:"ps_csrf=csrf",addEventListener(){},getElementById:get,createElement:()=>new Element()},
 fetch:async(url,options={})=>({ok:true,json:async()=>options.method==="PATCH"
   ?{collection:{collection_id:9,instance_token:collection.instance_token,name:"旅行新名",revision:3,member_count:6}}
   :url.includes("/photos?")
     ?{assets:[],has_more:false,next_cursor:null,collection:{...collection}}
     :{collections:[{...collection}],has_more:false,next_cursor:null}})};
let code=fs.readFileSync("src/photo_search/web/persons-albums.js","utf8");
code=code.replace('  if (document.readyState === "loading")','  globalThis.ui={loadCollections,openCollection,renameCollection,state};\n  if (document.readyState === "loading")');
vm.createContext(context);vm.runInContext(code,context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 await context.ui.loadCollections(false);
 assert.match(get("pa-collections").children[0].children[0].textContent,/6 个人物 · 0 张脸 · 24 张照片/);
 const loaded=context.ui.state.collectionById.get("9");context.ui.openCollection(loaded);await flush();
 assert.match(get("pa-album-note").textContent,/24 张去重照片/);
 await context.ui.renameCollection(context.ui.state.collection);await flush();
 assert.match(get("pa-album-note").textContent,/24 张去重照片/,"renaming a collection preserves its photo count");
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", script], cwd=Path(__file__).resolve().parents[2], capture_output=True,
        text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
