"""Execute the actual batch UI: scope race, progress, pause/resume and retry."""

import shutil
import subprocess
from pathlib import Path


def test_detail_batch_ui_scope_progress_and_retry():
    node = shutil.which("node")
    assert node is not None
    script = r"""
const fs=require("fs"), vm=require("vm"), assert=require("assert/strict");
class Element {
  constructor(){this.children=[];this.events={};this.value="";this.classList={add(){}};}
  set textContent(v){this.text=v;this.children=[];} get textContent(){return this.text||"";}
  append(...v){this.children.push(...v);} replaceChildren(...v){this.children=v;}
  setAttribute(){} focus(){} addEventListener(k,v){this.events[k]=v;}
  fire(k){return this.events[k]({preventDefault(){}});}
}
const elements=new Map(), get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
const requests=[], timers=[]; let oldResolve, stage="IDLE", polls=0;
const result=()=>({batch_id:stage==="IDLE"?null:"batch-2",source_id:2,state:stage,total:3,
 processed:stage==="COMPLETE"?3:stage==="PARTIAL_FAILED"?3:0,
 succeeded:stage==="COMPLETE"?3:stage==="PARTIAL_FAILED"?2:0,
 skipped:0,failed:stage==="PARTIAL_FAILED"?1:0,needs_review:0});
const response=body=>({ok:true,json:async()=>body});
const context={URLSearchParams,Date,crypto:{randomUUID:()=>"fake"},
 setTimeout:fn=>{const t={fn};timers.push(t);return t;},clearTimeout:t=>{if(t)t.cancelled=true;},
 document:{readyState:"loading",cookie:"ps_csrf=csrf",addEventListener(){},getElementById:get,createElement:()=>new Element()},
 fetch:async(url,options)=>{
   requests.push({url,options});
   if(url==="/api/v1/sources")return response({sources:[{source_id:1,display_name:"一"},{source_id:2,display_name:"二"}]});
   if(url.includes("source-detail-preview"))return response({total:3});
   if(url.includes("/active?source_id=1"))return new Promise(resolve=>{oldResolve=()=>resolve(response({batch_id:"stale",state:"RUNNING"}));});
   if(url.endsWith("source-detail-batches") && options.method==="POST"){stage="RUNNING";return response(result());}
   if(url.endsWith("/pause")){stage="PAUSED";return response(result());}
   if(url.endsWith("/resume")){stage="RUNNING";return response(result());}
   if(url.endsWith("/retry")){stage="RUNNING";return response(result());}
   if(url.includes("/active?source_id=2") && stage==="RUNNING"){
      stage=++polls===1?"PARTIAL_FAILED":"COMPLETE";return response(result());
   }
   if(url.includes("/active"))return response(result());
   return response({persons:[],faces:[],has_more:false});
 }};
let code=fs.readFileSync("src/photo_search/web/persons-albums.js","utf8");
code=code.replace('  if (document.readyState === "loading")','  globalThis.ui={boot,state,loadDetailBatch};\n  if (document.readyState === "loading")');
vm.createContext(context);vm.runInContext(code,context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const tick=async()=>{const t=timers.find(x=>!x.cancelled&&!x.ran);assert.ok(t);t.ran=true;await t.fn();await flush();};
(async()=>{
 context.ui.boot();await flush();
 get("pa-detail-source").value="1";const old=context.ui.loadDetailBatch();await flush();
 get("pa-detail-source").value="2";await context.ui.loadDetailBatch();oldResolve();await old;
 assert.equal(context.ui.state.detailBatch,null);
 assert.match(get("pa-detail-batch").textContent,/3 张/);
 await get("pa-detail-batch").fire("click");
 const post=requests.find(x=>x.url.endsWith("source-detail-batches")&&x.options.method==="POST");
 assert.deepEqual(JSON.parse(post.options.body),{source_id:2});
 assert.equal(post.options.headers["X-CSRF-Token"],"csrf");
 await get("pa-detail-pause").fire("click");assert.equal(context.ui.state.detailBatch.state,"PAUSED");
 assert.equal(get("pa-detail-batch").disabled,true);
 await get("pa-detail-resume").fire("click");await tick();
 assert.equal(context.ui.state.detailBatch.state,"PARTIAL_FAILED");assert.equal(get("pa-detail-retry").hidden,false);
 await get("pa-detail-retry").fire("click");await tick();
 assert.equal(context.ui.state.detailBatch.state,"COMPLETE");
 assert.match(get("pa-detail-batch-note").textContent,/3\/3/);
 assert.equal(requests.filter(x=>x.url.endsWith("source-detail-batches")&&x.options.method==="POST").length,1);
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
