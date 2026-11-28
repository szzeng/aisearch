from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_candidate_bounds_and_stale_layer_updates() -> None:
    node = shutil.which("node")
    assert node is not None
    root = Path(__file__).resolve().parents[2]
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
let pending=[], layer, selected;
function PointLayer(opts){this.opts=opts;this.clearData=()=>{this.data=null};this.setData=x=>{this.data=x};
  this.addEventListener=(name,fn)=>{this.click=fn};layer=this;}
function FakeMap(){this.addLayer=()=>{};this.getBounds=()=>({getSouthWest:()=>({lng:179.8,lat:10}),getNorthEast:()=>({lng:-179.8,lat:20})})}
const w={BMap:{Map:FakeMap,PointLayer},AISEARCH_BAIDU:{convertPoints:(p,valid)=>new Promise(resolve=>pending.push({p,valid,resolve}))}};
const c={window:w,Map,Number,Math,Promise,document:{createElement:()=>({getContext:()=>({beginPath(){},arc(){},fill(){},stroke(){},fillText(){}})})}};vm.createContext(c);
vm.runInContext(fs.readFileSync('src/photo_search/web/baidu-map-engine.js','utf8'),c);
(async()=>{let e=w.AISEARCH_BAIDU_ENGINE.create('map',p=>selected=p);
  let b=e.bounds(); assert(b.west>179 && b.east< -179 && b.south===9.9 && b.north===20.1);
  let two=layer.opts.icon({asset_count:2}), ten=layer.opts.icon({asset_count:10});
  assert.equal(two.id,'map-count-2'); assert.equal(ten.id,'map-count-10'); assert.notEqual(two.id,ten.id);
  assert.strictEqual(two,layer.opts.icon({asset_count:2}));
  let feature=id=>({type:'Feature',geometry:{type:'Point',coordinates:[1,2]},properties:{asset_id:id}});
  let first=e.setData({features:[feature(1)]}); let second=e.setData({features:[feature(2)]});
  assert(!pending[0].valid() && pending[1].valid()); pending[1].resolve([[1,2]]); assert(await second);
  pending[0].resolve([[1,2]]); assert.strictEqual(await first,false);
  assert.strictEqual(layer.data.features[0].properties.asset_id,2); layer.click({value:{properties:{asset_id:2}}});
  assert.strictEqual(selected.asset_id,2); e.clear(); assert.strictEqual(layer.data,null);
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    subprocess.run([node, "-e", script], cwd=root, check=True, capture_output=True, text=True, timeout=20)


def test_deep_link_refreshes_list_after_recentering() -> None:
    node = shutil.which("node")
    assert node is not None
    root = Path(__file__).resolve().parents[2]
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
let source=fs.readFileSync('src/photo_search/web/map.js','utf8');
source=source.slice(0,source.lastIndexOf('})();')) +
 ';globalThis.probe={applyFocusIntent,setState(m,i,v){map=m;focusIntent=i;version=v;},setSchedule(fn){schedule=fn}};' +
 source.slice(source.lastIndexOf('})();'));
const c={window:{},document:{readyState:'loading',addEventListener(){}},BMap:{Point:class{constructor(lng,lat){this.lng=lng;this.lat=lat;}}}};
vm.createContext(c);vm.runInContext(source,c);
let center,refreshes=0;c.probe.setSchedule(()=>refreshes++);
c.probe.setState({centerAndZoom:(p,z)=>{center=[p.lng,p.lat,z]}},{version:7,asset:{longitude:0,latitude:0}},7);
c.probe.applyFocusIntent();assert.deepStrictEqual(center,[0,0,14]);assert.equal(refreshes,1);
"""
    subprocess.run([node, "-e", script], cwd=root, check=True, capture_output=True, text=True, timeout=20)
