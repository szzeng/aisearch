from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_missing_tile_event_does_not_destroy_active_map_or_reset_view() -> None:
    node = shutil.which("node")
    assert node is not None
    root = Path(__file__).resolve().parents[2]
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
let timers=[], requests=[], destroyed=0, handlers={}, nodes={};
function el(id){return nodes[id] ||= {textContent:'',hidden:false,disabled:false,setAttribute(){},querySelectorAll(){return []}}}
const map={addEventListener:(n,fn)=>handlers[n]=fn,centerAndZoom(){},setMapType(){},getZoom:()=>8,destroy(){destroyed++}};
const engine={map,clear(){},bounds:()=>({west:113,south:22,east:115,north:24}),setData:()=>Promise.resolve(true)};
const window={AISEARCH_BAIDU:{load:async()=>{}},AISEARCH_BAIDU_ENGINE:{create:()=>engine},scrollTo(){}};
const c={window,document:{readyState:'loading',addEventListener(){},getElementById:el},
 BMap:{Point:function(lng,lat){this.lng=lng;this.lat=lat}},BMAP_NORMAL_MAP:1,BMAP_HYBRID_MAP:2,BMAP_SATELLITE_MAP:3,
 URLSearchParams,location:{search:''},sessionStorage:{getItem:()=>null,removeItem(){}},
 setTimeout:(fn,delay)=>{timers.push({fn,delay});return timers.length},clearTimeout(){},
 fetch:async url=>{requests.push(url);let body=url.endsWith('/summary')?{total:1,member_version:1,bounds:null}:
   url.endsWith('/baidu/provider-runtime')?{status:'CONFIGURED',ak:'fake-ak',default_layer:'normal'}:
   url.includes('/points?')?{member_version:1,total:1,features:[],mode:'grid'}:
   {member_version:1,total:1,assets:[],next_cursor:null};return {ok:true,json:async()=>body}}};
vm.createContext(c);let code=fs.readFileSync('src/photo_search/web/map.js','utf8');
code=code.replace('  if (document.readyState === "loading")','  globalThis.probe={init,schedule};\n  if (document.readyState === "loading")');
vm.runInContext(code,c);
const flush=()=>new Promise(setImmediate);
(async()=>{c.probe.init();for(let i=0;i<6;i++)await flush();
 assert(handlers.moveend);handlers.moveend();let zoomTimer=timers.filter(t=>t.delay===250).at(-1);zoomTimer.fn();
 for(let i=0;i<6;i++)await flush();let timeout=timers.find(t=>t.delay===12000);assert(timeout);timeout.fn();
 for(let i=0;i<4;i++)await flush();assert.equal(destroyed,0);
 assert.equal(el('map-connection-status').textContent,'SDK 已加载；底图状态未确认，请查看地图。');
 assert(!requests.some(u=>u==='/api/v1/map/assets?limit=48'));
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    subprocess.run([node, "-e", script], cwd=root, check=True, capture_output=True, text=True, timeout=20)
