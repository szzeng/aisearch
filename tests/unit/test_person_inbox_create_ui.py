"""Small DOM harness for the one-face create action and ambiguous retry contract."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_create_action_from_both_inbox_entry_points_and_reuses_unknown_request_id():
    node = shutil.which("node")
    assert node, "Node is required for the personal-page behavior check"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.value="";this.hidden=false;this.disabled=false;
    this.textContent="";this.dataset={};this.listeners={};this.attributes={};this.open=false;this.className="";
    this.classList={add(){}};}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
  setAttribute(k,v){this.attributes[k]=v;}
  removeAttribute(k){delete this.attributes[k];}
  addEventListener(n,fn){this.listeners[n]=fn;}
  fire(n){return this.listeners[n]&&this.listeners[n]();}
  querySelectorAll(){return [];}
  querySelector(){return null;}
  focus(){}
  showModal(){this.open=true;}
  close(){this.open=false;}
}
const elements=new Map(),calls=[];let uuid=0,createCount=0,releaseSecond;
const get=id=>{if(!id.startsWith("pa-"))id="pa-"+id;if(!elements.has(id))elements.set(id,new Element("div"));return elements.get(id);};
const context={URLSearchParams,crypto:{randomUUID:()=>"intent-"+(++uuid)},setTimeout,clearTimeout,
  window:{addEventListener(){},location:{search:"",href:"http://local/persons"}},
  document:{readyState:"loading",cookie:"ps_csrf=csrf",addEventListener(){},getElementById:get,
    createElement:t=>new Element(t)},
  fetch:async(url,options={})=>{
    const record={url,options,body:options.body?JSON.parse(options.body):null};calls.push(record);
    if(url.includes("/create-person")){
      createCount++;
      if(createCount===1)throw new Error("connection reset after request");
      if(createCount===2)return await new Promise(resolve=>{releaseSecond=()=>resolve({ok:true,json:async()=>({})});});
      return {ok:false,status:409,json:async()=>({code:"FACE_ASSIGNMENT_CONFLICT"})};
    }
    if(url.startsWith("/api/v1/persons/browse"))return {ok:true,json:async()=>({persons:[],has_more:false,next_cursor:null})};
        if(url.startsWith("/api/v1/faces/inbox/groups?"))return {ok:true,json:async()=>({cards:[],has_more:false,next_cursor:null})};
        if(url.startsWith("/api/v1/faces/inbox/summary?"))return {ok:true,json:async()=>({total:0,reasons:{},held:0})};
    if(url.startsWith("/api/v1/persons?"))return {ok:true,json:async()=>({persons:[],has_more:false,next_cursor:null})};
    return {ok:true,json:async()=>({})};
  }};
let code=fs.readFileSync("src/photo_search/web/persons-albums.js","utf8");
code=code.replace('  if (document.readyState === "loading")',
  '  globalThis.ui={state,renderInboxFace,createPersonFromFace}; $("create-person").addEventListener("click",createPersonFromFace);\n  if (document.readyState === "loading")');
vm.createContext(context);vm.runInContext(code,context);const ui=context.ui;
const flush=()=>new Promise(r=>setImmediate(r));
const face=(id,suggestion)=>({face_id:id.repeat(64),asset_id:1,thumbnail_url:"/api/v1/faces/"+id.repeat(64)+"/thumbnail",
  control_revision:0,assigned_person_id:null,organization_reason:suggestion?"BORDERLINE":"NO_MATCH",
  suggestions:suggestion?[{person_id:9,name:"候选",thumbnail_url:null}]:[]});
(async()=>{
  ui.state.operation={operation_id:"old",operation_revision:1,changed:true};
  ui.state.organize=true;
  ui.state.undoTimer=setTimeout(()=>{},10000);get("undo-bar").hidden=false;
  for(const f of [face("a",false),face("b",true)]){
    const host=new Element("div");ui.renderInboxFace(host,f,false);
    const entry=host.children[0].children.filter(x=>x.tag==="button").at(-1);
    assert(entry,"expected the inbox entry button");entry.fire("click");await flush();
    assert.equal(get("edit").open,true);
    assert.equal(get("create-person").hidden,false);
    assert.equal(typeof get("create-person").listeners.click,"function");
    if(f.face_id.startsWith("a")){
      const first=get("create-person").fire("click");await first;
      const firstRequest=calls.filter(x=>x.url.includes("/create-person")).at(-1);
      assert.equal(firstRequest.url,"/api/v1/faces/"+f.face_id+"/create-person");
      assert.deepEqual(Object.keys(firstRequest.body).sort(),["contract_version","expected_control_revision","request_id"]);
      const retry=get("create-person").fire("click");
      await flush();
      const duplicate=get("create-person").fire("click");await duplicate;
      assert.equal(calls.filter(x=>x.url.includes("/create-person")).length,2,"double click sends only one retry");
      const secondRequest=calls.filter(x=>x.url.includes("/create-person")).at(-1);
      assert.equal(secondRequest.body.request_id,firstRequest.body.request_id,"unknown outcome retries same intent id");
      releaseSecond();await retry;
      assert.equal(ui.state.operation,null);assert.equal(ui.state.undoTimer,null);assert.equal(get("undo-bar").hidden,true);
      assert.equal(get("edit").open,false);assert.notEqual(get("status").textContent,"");
    assert(calls.some(x=>x.url.startsWith("/api/v1/persons/browse?")),"people list refreshes after creation");
      assert(calls.some(x=>x.url.startsWith("/api/v1/faces/inbox/groups?")),"pending inbox refreshes after creation");
    }else{
      const denied=get("create-person").fire("click");await denied;
      assert.equal(get("edit").open,true,"409 leaves the dialog open");
      assert.notEqual(get("edit-error").textContent,"");
    }
    get("edit").close();
  }
  const history=fs.readFileSync("src/photo_search/web/persons.js","utf8");
  assert(history.includes("CREATE_PERSON_FROM_FACE:"));
  assert(history.includes('if (operation.operation_type === "CREATE_PERSON_FROM_FACE")'));
  assert(history.includes('undoOperation(operation);'),"existing operation undo remains wired");
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", script],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
