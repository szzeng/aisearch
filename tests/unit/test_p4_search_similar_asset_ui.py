"""Focused browserless contract checks for the P4 asset-image search mode."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_similar_asset_entry_validation_and_request_body() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for search interaction checks"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
const location={search:"",pathname:"/search",hash:"",href:"http://127.0.0.1/search"};
const window={addEventListener(){},location,crypto:{randomUUID:()=>"test-request-id"},history:{state:null,replaceState(){}}};
const document={readyState:"loading",addEventListener(){}};
const context={window,document,URLSearchParams,URL,WeakSet,Set,Map,Date,Math,Number,String,Boolean,
  Array,Object,JSON,Promise,RegExp,isFinite,encodeURIComponent,decodeURIComponent,console};
let code=fs.readFileSync("src/photo_search/web/search.js","utf8").replace(
  '  if (document.readyState === "loading")',
  '  globalThis.ui={similarAssetFromUrl,sendSimilarAssetSearch,setSimilarQueryControls,successNotices,pruneSearchSnapshots,setEls(v){els=v;},setRequest(fn){request=fn;},getState(){return state;}};\n  if (document.readyState === "loading")');
vm.createContext(context);vm.runInContext(code,context);const ui=context.ui;
location.search="?similar_asset=42";assert.deepEqual(JSON.parse(JSON.stringify(ui.similarAssetFromUrl())),{present:true,valid:true,assetId:42,conflicts:false});
location.search="?similar_asset=0";assert.equal(ui.similarAssetFromUrl().valid,false);
location.search="?similar_asset=42&scope_entry=abc";assert.equal(ui.similarAssetFromUrl().conflicts,true);
location.search="?similar_asset=42&q=old";assert.equal(ui.similarAssetFromUrl().conflicts,true);
const notices=ui.successNotices({asset_query:{asset_id:42},search_projection:{state:"LAGGING"}});
assert.equal(notices.some(line=>/文本投影仍在追赶|没有查询文本/.test(line.text)),false);
const snapshotValues=new Map();for(let i=0;i<8;i++)snapshotValues.set("aisearch.search-return.v1:old-"+i,JSON.stringify({saved_at:i}));
window.sessionStorage={get length(){return snapshotValues.size;},key(i){return [...snapshotValues.keys()][i]||null;},
  getItem(k){return snapshotValues.get(k)||null;},removeItem(k){snapshotValues.delete(k);}};
ui.pruneSearchSnapshots("aisearch.search-return.v1:new",5);
assert.equal(snapshotValues.size,5);assert.equal(snapshotValues.has("aisearch.search-return.v1:old-0"),false);
const filter={inert:false,attrs:{},setAttribute(k,v){this.attrs[k]=v;}};
const submit={textContent:""};ui.setEls({searchFilterControls:filter,searchSubmit:submit,queryPageSize:{value:"20"}});
ui.getState().search.mode="asset_image";ui.setSimilarQueryControls(true);
assert.equal(filter.inert,true);assert.equal(filter.attrs["aria-disabled"],"true");
assert.match(submit.textContent,/结束相似搜索/);
ui.getState().search.assetQueryId=42;let sent=null;
ui.setRequest((path,options)=>{sent={path,body:options.body};return Promise.resolve({body:{ok:true}});});
ui.sendSimilarAssetSearch(false).then(()=>{
  assert.equal(sent.path,"/search/by-asset");assert.equal(sent.body.asset_id,42);
  assert.equal(sent.body.page_size,20);assert.equal("cursor" in sent.body,false);
  ui.getState().search.sessionId="session-1";ui.getState().search.cursor="cursor-1";
  return ui.sendSimilarAssetSearch(true);
}).then(()=>{
  assert.equal(sent.body.search_session_id,"session-1");assert.equal(sent.body.cursor,"cursor-1");
  assert.deepEqual(Object.keys(sent.body).sort(),["asset_id","contract_version","cursor","page_size","request_id","search_session_id"].sort());
}).catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=10
    )
    assert result.returncode == 0, result.stderr


def test_similar_asset_route_and_reference_summary_are_visible_in_search_page() -> None:
    shell = (ROOT / "src/photo_search/web/search.html").read_text(encoding="utf-8")
    script = (ROOT / "src/photo_search/web/search.js").read_text(encoding="utf-8")
    viewer = (ROOT / "src/photo_search/web/photo-info.js").read_text(encoding="utf-8")
    assert "id=\"asset-query-summary\"" in shell
    assert "搜索范围：全部图库" in shell
    assert '"/search/by-asset"' in script
    assert "similar_asset=" in script
    assert 'body.asset_query && state.search.mode === "asset_image"' in script
    assert 'if (state.search.mode !== "asset_image")' in script
    assert "onFindSimilar" in viewer
    assert "photo-viewer-similar" in viewer
