"""Check that a late mapping page cannot cross the selected photo boundary."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_asset_mapping_request_switch_discards_old_response() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the personal-page interaction check"
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
class Element {
 constructor(){this.children=[];this.dataset={};this.hidden=false;this.textContent='';}
 appendChild(child){this.children.push(child);return child;} append(...children){this.children.push(...children);}
 replaceChildren(...children){this.children=children;} setAttribute(){} removeAttribute(){}
 addEventListener(){} focus(){} scrollIntoView(){}
}
const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
const pending=[],requests=[];
const context={URLSearchParams,URL,crypto:{randomUUID:()=>"synthetic"},setTimeout:()=>1,clearTimeout(){},
 window:{location:{href:'http://local/persons',search:''},addEventListener(){},history:{replaceState(){}},setTimeout(){},localStorage:{getItem(){return null;}}},
 document:{readyState:'loading',cookie:'ps_csrf=test',addEventListener(){},getElementById:get,createElement(){return new Element();}},
 fetch:async(url)=>{requests.push(url);return await new Promise(resolve=>pending.push({url,resolve}));}};
let code=fs.readFileSync('src/photo_search/web/persons.js','utf8');
code=code.replace('  var els = {};', '  var els = {mappingsList:{firstChild:null},mappingsMore:{},mappingsStatus:{},mappingsError:{}};');
code=code.replace('  function bootWhenOpened()', '  globalThis.ui={state,loadMappings};\n  function bootWhenOpened()');
vm.createContext(context);vm.runInContext(code,context);
const ui=context.ui,flush=()=>new Promise(setImmediate),response=body=>({ok:true,status:200,headers:{get:()=>"application/json"},json:async()=>body});
(async()=>{
 ui.state.mappings.hasSupport=true;ui.state.mappings.assetId=30;ui.loadMappings(true);await flush();
 ui.state.mappings.assetId=10;ui.loadMappings(true);await flush();
 assert.equal(requests.length,2,'the new asset request replaces an in-flight request');
 assert(requests[0].includes('/assets/30/'));assert(requests[1].includes('/assets/10/'));
 pending[1].resolve(response({groups:[],has_more:false,next_cursor:null}));await flush();
 pending[0].resolve(response({groups:[],has_more:true,next_cursor:'old-photo-cursor'}));await flush();
 assert.equal(ui.state.mappings.assetId,10);assert.equal(ui.state.mappings.cursor,null);
 assert.equal(ui.state.mappings.loading,false);
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
