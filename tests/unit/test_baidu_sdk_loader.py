from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_loader_shares_sdk_and_rejects_changed_credential() -> None:
    node = shutil.which("node")
    assert node is not None
    root = Path(__file__).resolve().parents[2]
    script = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const scripts = [], w = {};
const ctx = {window:w, setTimeout, clearTimeout, encodeURIComponent,
  document:{createElement(){ return {remove(){}}; }, head:{appendChild(s){scripts.push(s);}}}};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('src/photo_search/web/map-config.js','utf8'),ctx);
(async () => {
  const a=w.AISEARCH_BAIDU.load('fake-ak'), b=w.AISEARCH_BAIDU.load('fake-ak');
  assert.strictEqual(a,b); assert.strictEqual(scripts.length,1);
  assert(scripts[0].src.startsWith('https://api.map.baidu.com/api?v=4.0&'));
  w.BMap={}; w.BMAP_COORD_WGS84='WGS84'; w.__aisearchBaiduReady();
  assert.strictEqual(await a,w.BMap); assert.strictEqual(w.BMap.coordType,'WGS84');
  await assert.rejects(w.AISEARCH_BAIDU.load('different-fake-ak'));
})().catch(e => { console.error(e); process.exitCode=1; });
"""
    subprocess.run([node, "-e", script], cwd=root, check=True, capture_output=True, text=True, timeout=20)


def test_conversion_is_bounded_ordered_and_fails_closed() -> None:
    node = shutil.which("node")
    assert node is not None
    root = Path(__file__).resolve().parents[2]
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
let calls=[],fail=false;
const w={BMap:{Point:function(lng,lat){this.lng=lng;this.lat=lat;},Convertor:function(){
  this.translate=(pts,from,to,cb)=>{calls.push([pts.length,from,to]);
    cb(fail?{status:1}:{status:0,points:pts.map(p=>({lng:p.lng+0.1,lat:p.lat+0.1}))});};
}}};
const c={window:w,setTimeout,clearTimeout,Number,Promise,Array,Math};vm.createContext(c);
vm.runInContext(fs.readFileSync('src/photo_search/web/map-config.js','utf8'),c);
(async()=>{
  const input=Array.from({length:23},(_,i)=>[i,10]);
  const result=await w.AISEARCH_BAIDU.convertPoints(input);
  assert.deepStrictEqual(calls,[[10,1,5],[10,1,5],[3,1,5]]);
  assert.strictEqual(result.length,23); assert.strictEqual(result[22][0],22.1);
  fail=true; await assert.rejects(w.AISEARCH_BAIDU.convertPoints([[101,2]]));
  await assert.rejects(w.AISEARCH_BAIDU.convertPoints([[181,0]]));
  await assert.rejects(w.AISEARCH_BAIDU.convertPoints(Array(1001).fill([1,2])));
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    subprocess.run([node, "-e", script], cwd=root, check=True, capture_output=True, text=True, timeout=20)


def test_overlapping_views_serialize_and_skip_obsolete_work() -> None:
    node = shutil.which("node")
    assert node is not None
    root = Path(__file__).resolve().parents[2]
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
let callbacks=[],active=0,maxActive=0,calls=[];
const w={BMap:{Point:function(lng,lat){this.lng=lng;this.lat=lat},Convertor:function(){
  this.translate=(points,from,to,callback)=>{active++;maxActive=Math.max(maxActive,active);calls.push(points[0].lng);
    callbacks.push(()=>{active--;callback({status:0,points})})};
}}};
const c={window:w,setTimeout,clearTimeout,Number,Promise,Array,Math};vm.createContext(c);
vm.runInContext(fs.readFileSync('src/photo_search/web/map-config.js','utf8'),c);
(async()=>{
  let valid=true;
  const first=w.AISEARCH_BAIDU.convertPoints(Array(20).fill([1,2]),()=>valid).catch(()=>null);
  await new Promise(setImmediate); assert.deepStrictEqual(calls,[1]);
  valid=false;
  const obsolete=w.AISEARCH_BAIDU.convertPoints([[2,2]],()=>false).catch(()=>null);
  const latest=w.AISEARCH_BAIDU.convertPoints([[3,2]],()=>true);
  await new Promise(setImmediate); assert.deepStrictEqual(calls,[1]);
  callbacks.shift()(); await new Promise(setImmediate);
  assert.deepStrictEqual(calls,[1,3]); assert.equal(maxActive,1);
  callbacks.shift()(); assert.equal((await latest)[0][0],3);
  assert.equal(await first,null); assert.equal(await obsolete,null);
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    subprocess.run([node, "-e", script], cwd=root, check=True, capture_output=True, text=True, timeout=20)


def test_conversion_failure_exposes_only_safe_classification() -> None:
    node = shutil.which("node")
    assert node is not None
    root = Path(__file__).resolve().parents[2]
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
let mode='status';const w={BMap:{Point:function(lng,lat){this.lng=lng;this.lat=lat},Convertor:function(){
 this.translate=(_p,_f,_t,cb)=>{if(mode==='status')cb({status:5});else if(mode==='invalid')cb({status:0,points:[]});else throw new Error('private');};
}}};
const c={window:w,setTimeout,clearTimeout,Number,Promise,Array,Math};vm.createContext(c);
vm.runInContext(fs.readFileSync('src/photo_search/web/map-config.js','utf8'),c);
(async()=>{for(const [m,code] of [['status','status_5'],['invalid','invalid_result_count'],['throw','exception']]){
 mode=m; await assert.rejects(w.AISEARCH_BAIDU.convertPoints([[1,2]]),e=>e.code===code && !e.message.includes('private'));
}})().catch(e=>{console.error(e);process.exitCode=1});
"""
    subprocess.run([node, "-e", script], cwd=root, check=True, capture_output=True, text=True, timeout=20)


def test_invalid_result_retries_once_and_obsolete_view_stops() -> None:
    node = shutil.which("node")
    assert node is not None
    root = Path(__file__).resolve().parents[2]
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
let calls=0,mode='transient',valid=true,firstResult;
const w={BMap:{Point:function(lng,lat){this.lng=lng;this.lat=lat},Convertor:function(){
 this.translate=(points,_f,_t,cb)=>{calls++;
  if(mode==='transient' && calls===1) return cb({status:0,points:[]});
  if(mode==='persistent') return cb({status:0,points:[{lng:NaN,lat:2}]});
  if(mode==='obsolete') { firstResult=()=>cb({status:0,points:[]}); return; }
  cb({status:0,points});};
}}};
const c={window:w,setTimeout,clearTimeout,Number,Promise,Array,Math};vm.createContext(c);
vm.runInContext(fs.readFileSync('src/photo_search/web/map-config.js','utf8'),c);
(async()=>{
 let result=await w.AISEARCH_BAIDU.convertPoints([[1,2]]); assert.equal(calls,2); assert.equal(result[0][0],1);
 calls=0; mode='persistent'; await assert.rejects(w.AISEARCH_BAIDU.convertPoints([[2,2]]),e=>e.code==='invalid_result_coordinate'); assert.equal(calls,2);
 calls=0; mode='obsolete';let pending=w.AISEARCH_BAIDU.convertPoints([[3,2]],()=>valid);
 await new Promise(setImmediate); firstResult(); valid=false;
 await assert.rejects(pending); assert.equal(calls,1);
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    subprocess.run([node, "-e", script], cwd=root, check=True, capture_output=True, text=True, timeout=20)


def test_conversion_cache_reuses_success_and_preserves_order() -> None:
    node = shutil.which("node")
    assert node is not None
    root = Path(__file__).resolve().parents[2]
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
let calls=[];
const w={BMap:{Point:function(lng,lat){this.lng=lng;this.lat=lat},Convertor:function(){
 this.translate=(points,_f,_t,cb)=>{calls.push(points.map(p=>p.lng));
 cb({status:0,points:points.map(p=>({lng:p.lng+0.1,lat:p.lat+0.1}))});};
}}};
const c={window:w,setTimeout,clearTimeout,Number,Promise,Array,Math,Map};vm.createContext(c);
vm.runInContext(fs.readFileSync('src/photo_search/web/map-config.js','utf8'),c);
(async()=>{
 await w.AISEARCH_BAIDU.convertPoints([[1,2],[2,2]]);
 let result=await w.AISEARCH_BAIDU.convertPoints([[2,2],[3,2],[1,2]]);
 assert.equal(calls.length,2);assert.deepStrictEqual(Array.from(calls[1]),[3]);
 assert.deepStrictEqual(Array.from(result,p=>p[0]),[2.1,3.1,1.1]);
 result[0][0]=999; result=await w.AISEARCH_BAIDU.convertPoints([[2,2]]);
 assert.equal(result[0][0],2.1); assert.equal(calls.length,2);
 const points=Array.from({length:2001},(_,i)=>[-100+i*0.01,0]);
 await w.AISEARCH_BAIDU.convertPoints(points.slice(0,1000));
 await w.AISEARCH_BAIDU.convertPoints(points.slice(1000,2000));
 await w.AISEARCH_BAIDU.convertPoints(points.slice(2000));
 const before=calls.length; await w.AISEARCH_BAIDU.convertPoints([points[0]]);
 assert.equal(calls.length,before+1);
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    subprocess.run([node, "-e", script], cwd=root, check=True, capture_output=True, text=True, timeout=20)
