"""Behavioral UI checks for explicit embedding index checks."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_embedding_check_feedback_identity_and_retry_behaviors() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the embedding-check interaction check"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
class Element {
  constructor(tag="div"){this.tag=tag;this.children=[];this.handlers={};this.attributes={};this.textContent="";this.disabled=false;this.className="";this.dataset={};}
  append(...nodes){for(const node of nodes)this.appendChild(node);}
  appendChild(node){this.children.push(node);return node;}
  replaceChildren(...nodes){this.children=[];this.append(...nodes);}
  addEventListener(name,fn){this.handlers[name]=fn;}
  setAttribute(name,value){this.attributes[name]=String(value);}
  fire(name){if(this.handlers[name])this.handlers[name]({preventDefault(){}});}
  findAll(test){const result=[];const visit=node=>{if(test(node))result.push(node);node.children.forEach(visit);};visit(this);return result;}
}
const nodes=new Map(),get=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
const identity=()=>({provider_id:"qwen",display_name:"Qwen",installed:true,index_state:"CHECK_REQUIRED",processor_id:"proc1",space_id:"space1",
 generation_id:"gen1",profile_id:"profile1",is_active:false,generation:{generation_id:"gen1",profile_id:"profile1",confirmed:4}});
let status={active_profile:null,providers:[identity()],candidate_task:null,offline_transition:{}};
let checks=0,switches=[],confirms=[],confirmResult=false,nextCheck=null;
const response=body=>Promise.resolve({status:200,ok:true,headers:{get:()=>"application/json"},json:()=>Promise.resolve(body)});
const fetch=(url,options)=>{
  if(url.endsWith("/embedding-management/status"))return response(status);
  if(url.endsWith("/qwen/check")){checks++;const result=nextCheck;nextCheck=null;return result.promise;}
  if(url.endsWith("/qwen/switch")){switches.push(JSON.parse(options.body));return response({status:"scheduled"});}
  throw new Error("unexpected request "+url+" "+options.method);
};
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const document={readyState:"loading",cookie:"",hidden:false,addEventListener(){},getElementById:get,createElement:tag=>new Element(tag)};
const window={clearTimeout(){},setTimeout(){return 1;},crypto:{randomUUID:()=>"request"},confirm:message=>{confirms.push(message);return confirmResult;}};
const context={document,window,fetch,Promise,Date,Math,Number,String,Boolean,Object,Array,JSON,RegExp,isFinite,encodeURIComponent,console};
let code=fs.readFileSync("src/photo_search/web/app.js","utf8").replace(
  '  if (document.readyState === "loading")',
  '  globalThis.testUi={load:loadEmbeddingManagementStatus,checks:embeddingChecks,enter:enterEmbeddingManagementView};\n  if (document.readyState === "loading")');
vm.createContext(context);vm.runInContext(code,context);const ui=context.testUi;
const flush=async()=>{for(let i=0;i<8;i++)await new Promise(setImmediate);};
const card=()=>get("embedding-provider-list").children[0];
const buttons=()=>card().findAll(node=>node.tag==="button");
const statusText=()=>card().findAll(node=>node.className.split(/\s+/).includes("embedding-check-status"))[0].textContent;
const checkButton=()=>buttons().find(button=>button.textContent==="检查"||button.textContent==="重新检查"||button.textContent==="检查中…");
const checkBody=(eligible,covered=4,required=4,offline=0)=>({status:"ok",provider_id:"qwen",generation_id:"gen1",profile_id:"profile1",switch_eligible:eligible,
 coverage:{covered,required,percent:required?covered*100/required:null,missing:required-covered,offline_assets:offline},
 selection_error:eligible?null:{code:"SELECTION_NOT_READY",details:{reason:"PENDING_REPAIR"}}});
(async()=>{
  await ui.load();
  assert.match(statusText(),/点击检查/);
  assert.doesNotMatch(card().findAll(node=>node.tag==="p")[2].textContent,/可切换/,"status API default false is never presented as a checked result");

  nextCheck=deferred();const pending=nextCheck;checkButton().fire("click");
  assert.equal(checks,1);assert.equal(checkButton().disabled,true);assert.match(statusText(),/正在检查索引/);
  checkButton().fire("click");assert.equal(checks,1,"repeat click while pending sends no duplicate request");
  await ui.load();assert.equal(checkButton().disabled,true,"rerender preserves busy state");
  pending.resolve({status:200,ok:true,headers:{get:()=>"application/json"},json:()=>Promise.resolve(checkBody(true,4,4,2))});
  await flush();
  assert.match(statusText(),/检查通过/);assert.match(statusText(),/4\/4/);assert.match(statusText(),/2 条资产当前没有可用位置/);
  assert.match(statusText(),/不计入上述在线覆盖率/);assert.match(statusText(),/恢复原图后需增量建库/);
  assert.match(card().children[2].textContent,/在线可索引图片覆盖：4\/4/);
  assert.equal(buttons().filter(b=>b.textContent==="安排切换").length,1);
  buttons().find(button=>button.textContent==="安排切换").fire("click");
  assert.match(confirms.at(-1),/2 条资产当前没有可用位置/);assert.equal(switches.length,0,"declining warning does not schedule a switch");
  confirmResult=true;buttons().find(button=>button.textContent==="安排切换").fire("click");await flush();
  assert.equal(switches.length,1);assert.equal(switches[0].profile_id,"profile1");assert.equal(switches[0].confirmed,true);

  nextCheck={promise:response(checkBody(false,3,4))};checkButton().fire("click");await flush();
  assert.match(statusText(),/暂不能切换/);assert.match(statusText(),/3\/4/);assert.match(statusText(),/SELECTION_NOT_READY/);
  assert.equal(buttons().filter(b=>b.textContent==="安排切换").length,0);

  nextCheck={promise:response({status:"not_ready",provider_id:"qwen",reason:"NO_GENERATION",switch_eligible:false})};checkButton().fire("click");await flush();
  assert.match(statusText(),/尚无可检查的索引/);assert.equal(buttons().filter(b=>b.textContent==="安排切换").length,0);

  nextCheck={promise:response(checkBody(true,0,0))};checkButton().fire("click");await flush();
  assert.match(statusText(),/0\/0/);assert.doesNotMatch(statusText(),/0\.0%|100\.0%/);
  assert.match(statusText(),/不能据此确认切换资格/);assert.equal(buttons().filter(b=>b.textContent==="安排切换").length,0);

  nextCheck={promise:Promise.reject({code:"CATALOG_UNREADABLE",message:"D:\\private\\library\\db"})};checkButton().fire("click");await flush();
  assert.match(statusText(),/检查失败/);assert.match(statusText(),/CATALOG_UNREADABLE/);assert.doesNotMatch(statusText(),/private/);
  assert.equal(checkButton().disabled,false,"failed request can be retried");

  const checkCountBeforeRace=checks;nextCheck=deferred();const taskRace=nextCheck;checkButton().fire("click");
  status.candidate_task={task_id:"task-race",provider_id:"qwen",status:"RUNNING"};await ui.load();
  assert.equal(checkButton().disabled,true,"task change keeps the in-flight request locked");
  assert.match(statusText(),/结果将作废/);checkButton().fire("click");assert.equal(checks,checkCountBeforeRace+1,"task change does not dispatch a second request");
  taskRace.resolve({status:200,ok:true,headers:{get:()=>"application/json"},json:()=>Promise.resolve(checkBody(true))});await flush();
  assert.match(statusText(),/点击检查/,"obsolete in-flight result is discarded when its request ends");
  status.candidate_task=null;await ui.load();

  nextCheck=deferred();const stale=nextCheck;checkButton().fire("click");
  status.providers[0]=Object.assign(identity(),{generation_id:"gen2",profile_id:"profile2",generation:{generation_id:"gen2",profile_id:"profile2",confirmed:1}});
  await ui.load();assert.match(statusText(),/结果将作废/);assert.equal(checkButton().disabled,true);assert.equal(buttons().filter(b=>b.textContent==="安排切换").length,0);
  stale.resolve({status:200,ok:true,headers:{get:()=>"application/json"},json:()=>Promise.resolve(checkBody(true))});await flush();
  assert.match(statusText(),/点击检查/,"late response from an old generation cannot replace current card state");

  status.providers[0]=Object.assign(identity(),{is_active:true});await ui.load();
  nextCheck={promise:response(checkBody(true))};checkButton().fire("click");await flush();
  assert.match(statusText(),/当前模型正在使用/);assert.equal(buttons().filter(b=>b.textContent==="安排切换").length,0);
  status.candidate_task={task_id:"task1",provider_id:"qwen",status:"RUNNING"};await ui.load();
  assert.match(statusText(),/点击检查/,"candidate-build start invalidates the previous check");
  nextCheck={promise:response(checkBody(true))};checkButton().fire("click");await flush();
  status.candidate_task={task_id:"task1",provider_id:"qwen",status:"PAUSE_REQUESTED"};await ui.load();
  assert.match(statusText(),/点击检查/,"candidate-build state changes invalidate the previous check");
  status.candidate_task=null;await ui.load();
  assert.match(statusText(),/点击检查/,"candidate-build terminal transition leaves the card unchecked");
  ui.enter("browse");assert.equal(Object.keys(ui.checks).length,0,"entering the page again clears transient conclusions");

  status.providers=[Object.assign(identity(),{generation:null,generation_id:"gen1",profile_id:null,index_state:"NO_GENERATION"})];
  await ui.load();nextCheck={promise:response({status:"not_ready",provider_id:"qwen",reason:"NO_GENERATION",switch_eligible:false})};
  checkButton().fire("click");await flush();assert.match(statusText(),/尚无可检查的索引/);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=20
    )
    assert result.returncode == 0, result.stderr
