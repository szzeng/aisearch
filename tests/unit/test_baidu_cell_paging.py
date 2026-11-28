from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_selected_cell_displays_total_and_pages_and_rejects_stale_selection() -> None:
    node = shutil.which("node")
    assert node is not None
    root = Path(__file__).resolve().parents[2]
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
let nodes={},pending=[];
function el(id){return nodes[id] ||= {textContent:'',hidden:false,disabled:false,children:[],
 appendChild(x){this.children.push(x)},setAttribute(){},querySelectorAll(){return []}}}
const c={window:{},document:{readyState:'loading',addEventListener(){},getElementById:el,
 createElement:()=>({appendChild(){},setAttribute(){},className:'',dataset:{}})},
 URLSearchParams,Promise,Number,Math,location:{href:''},fetch:url=>new Promise(resolve=>pending.push({url,resolve}))};
let code=fs.readFileSync('src/photo_search/web/map.js','utf8');
code=code.replace('  if (document.readyState === "loading")',
 '  globalThis.probe={openCell,loadMore,setState(){bbox={west:1,south:2,east:3,north:4};sequence=7;version=9;}};\n  if (document.readyState === "loading")');
vm.createContext(c);vm.runInContext(code,c);const flush=()=>new Promise(setImmediate);
const respond=(req,body)=>req.resolve({ok:true,json:async()=>body});
(async()=>{c.probe.setState(); c.probe.openCell('1:2',4,50);
 assert.equal(el('grid-title').textContent,'正在读取聚合照片…');
 respond(pending.shift(),{member_version:9,total:50,assets:Array.from({length:48},(_,i)=>({asset_id:i+1})),next_cursor:'next'});
 for(let i=0;i<4;i++)await flush();assert.equal(el('grid-title').textContent,'聚合区域 · 共 50 张，已显示 48 张');
 assert.equal(el('more').hidden,false);c.probe.loadMore();let p=pending.shift();assert(p.url.includes('cell_id=1%3A2'));
 respond(p,{member_version:9,total:50,assets:[{asset_id:49},{asset_id:50}],next_cursor:null});
 for(let i=0;i<4;i++)await flush();assert.equal(el('grid-title').textContent,'聚合区域 · 共 50 张，已显示 50 张');assert(el('more').hidden);
 c.probe.openCell('2:2',4,2);let old=pending.shift();c.probe.openCell('3:2',4,1);let current=pending.shift();
 respond(current,{member_version:9,total:1,assets:[{asset_id:70}],next_cursor:null});for(let i=0;i<4;i++)await flush();
 respond(old,{member_version:9,total:2,assets:[{asset_id:80},{asset_id:81}],next_cursor:null});for(let i=0;i<4;i++)await flush();
 assert.equal(el('grid-title').textContent,'聚合区域 · 共 1 张，已显示 1 张');
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    subprocess.run([node, "-e", script], cwd=root, check=True, capture_output=True, text=True, timeout=20)
