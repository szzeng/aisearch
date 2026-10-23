"""Actual album JS: late responses and pause/resume, with deferred synthetic HTTP."""

import shutil
import subprocess
from pathlib import Path


def test_cn_album_races_and_update_pause_resume():
    node = shutil.which("node")
    assert node is not None
    script = r"""
const fs=require('fs'), vm=require('vm'), assert=require('assert/strict');
class Element {
  constructor(tag) { this.tag=tag; this.children=[]; this.events={}; this.value=''; this.hidden=false; this.disabled=false; this.className=''; this.dataset={}; }
  set textContent(value) { this.text=value; this.children=[]; } get textContent() { return this.text || ''; }
  appendChild(item) { this.children.push(item); } append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children=items; } setAttribute() {} scrollIntoView() {} focus(){}
  querySelectorAll(selector){const wanted=selector.slice(1);let found=[];function walk(n){n.children.forEach(c=>{if(c.className.split(/\s+/).includes(wanted))found.push(c);walk(c);});}walk(this);return found;}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  addEventListener(event,fn) { this.events[event]=fn; } fire(event) { return this.events[event](); }
}
const elements=new Map(), get=id=>{if(!elements.has(id))elements.set(id,new Element('div'));return elements.get(id);};
get('album-kind').value='city';
const pending=[], posts=[];
const response=body=>({ok:true,json:async()=>body});
const events={};let url='http://127.0.0.1/places';const stack=[{url,state:null}];
const location={get href(){return url;},get pathname(){return new URL(url).pathname;},get search(){return new URL(url).search;},get hash(){return new URL(url).hash;}};
const history={index:0,state:null,pushState(state,_t,next){stack.splice(this.index+1);url=new URL(next,url).href;this.state=state;stack.push({url,state});this.index++;},replaceState(state,_t,next){url=new URL(next,url).href;this.state=state;stack[this.index]={url,state};},back(){if(this.index){this.index--;const item=stack[this.index];url=item.url;this.state=item.state;(events.popstate||[]).forEach(fn=>fn({state:item.state}));}}};
const context={URLSearchParams,URL,Date,Math,document:{cookie:'ps_csrf=synthetic',getElementById:get,createElement:tag=>new Element(tag),querySelectorAll:()=>[],body:Object.assign(new Element('body'),{dataset:{}})},
    window:{location,history,addEventListener(name,fn){(events[name]||(events[name]=[])).push(fn);},scrollY:0,scrollTo(){},setTimeout:fn=>setImmediate(fn),clearTimeout(){},PhotoTimeline:{mount(){
        let dispose;const instance={snapshot(){return {timeBasis:'capture',groupBy:'day',bucket:'all',year:null,month:null,scope:{place_key:'cn:3'}};},dispose(){if(dispose)dispose();}};
      const item={url:'/api/v1/timeline/assets?kind=place',options:{},resolve(){}};pending.push(item);
      instance.dispose=()=>{item.disposed=true;};return instance;
    }}},
fetch:(url,options)=>{
  if(url.endsWith('/admin/capabilities'))return Promise.resolve(response({available:true,dataset_id:'fixture'}));
  return new Promise(resolve=>{const item={url,options,resolve:body=>resolve(response(body))};pending.push(item);if(options.method==='POST')posts.push(item);});
}};
vm.createContext(context);vm.runInContext(fs.readFileSync('src/photo_search/web/places-albums.js','utf8'),context);
const settle=async()=>{await new Promise(setImmediate);await new Promise(setImmediate);};
const album=(kind,name)=>({kind,name,key:'cn:3',parent:'浙江省 · 杭州市',total:107,origin:'GPS_ADMIN',cover_asset_id:1});
(async()=>{
  await settle();
  assert.match(pending[0].url,/places\/albums\?kind=city/);
  const old=pending[0];get('album-kind').value='district';get('album-kind').fire('change');
  pending[1].resolve({albums:[album('district','西湖区'),album('district','余杭区'),album('district','拱墅区'),album('district','滨江区'),album('district','上城区')]});await settle();
  assert.equal(get('album-cards').children.length,5);
  assert.equal(get('album-cards').children[0].children[0].loading,'eager');
  assert.equal(get('album-cards').children[3].children[0].loading,'eager');
  assert.equal(get('album-cards').children[4].children[0].loading,'lazy');
  old.resolve({albums:[album('city','杭州市')]});await settle();
  assert.equal(get('album-cards').children[0].children[1].textContent,'西湖区');
  get('album-cards').children[0].fire('click');
  const photo=pending.at(-1);get('album-current-title').children[0].fire('click');await settle();
  assert.equal(photo.disposed,true);get('album-kind').value='province';get('album-kind').fire('change');
  assert.equal(get('album-detail-view').hidden,true);assert.equal(get('album-photos').children.length,0);
  get('cn-update').fire('click');assert.equal(posts.length,1);assert.equal(get('cn-pause').hidden,false);
  get('cn-pause').fire('click');posts[0].resolve({complete:false,total_scanned:100,total_processed:100});await settle();
  assert.equal(posts.length,1);assert.match(get('cn-update-status').textContent,/已暂停/);
  assert.equal(get('cn-update').disabled,false);
  get('cn-update').fire('click');assert.equal(posts.length,2);assert.match(posts[1].url,/restart=false/);
  assert.equal(posts[1].options.headers['X-CSRF-Token'],'synthetic');
  posts[1].resolve({complete:true,total_scanned:107,total_processed:107});await settle();
  assert.match(get('cn-update-status').textContent,/更新完成/);
  get('cn-update').fire('click');assert.match(posts[2].url,/restart=true/);
  posts[2].resolve({complete:true,total_scanned:107,total_processed:0});await settle();
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


def test_place_page_defers_management_requests_and_supports_anchor_loading():
    root = Path(__file__).resolve().parents[2]
    page = (root / "src/photo_search/web/places.html").read_text(encoding="utf-8")
    script = (root / "src/photo_search/web/places.js").read_text(encoding="utf-8")
    assert page.index("places-albums.js") < page.index("places.js")
    assert "places.js?v=p4-ui2-copy-daily-ux-3" in page
    boot = script[script.index("function boot()") : script.index('if (document.readyState')]
    assert "setupDeferredPanels();" in boot
    assert "probeCapabilities();\n    loadDatasets();" not in boot
    assert "probeCapabilities();\n    loadDirectory(true);" not in boot
    assert "probeCapabilities();\n    loadOperations(true);" not in boot
    assert "IntersectionObserver" in script and "hashchange" in script
    node = shutil.which("node")
    assert node is not None
    harness = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync('src/photo_search/web/places.js','utf8').replace(/\r\n/g,'\n');
const start=source.indexOf('  function setupDeferredPanels()');
const end=source.indexOf('\n\n  if (document.readyState',start);
const extracted=source.slice(start,end);
const calls=[],observed=[];let hashHandler=null;
const panelLoaded={datasets:false,directory:false,operations:false};
const nodes=new Map(),document={body:{dataset:{placeAlbumView:'detail'}},getElementById:id=>{if(!nodes.has(id))nodes.set(id,{id,hidden:false});return nodes.get(id);}};
const window={location:{hash:''},addEventListener:(name,handler)=>{if(name==='hashchange')hashHandler=handler;},
  IntersectionObserver:function(callback){this.callback=callback;this.observe=node=>observed.push(node);}};
const context={document,window,panelLoaded,
  loadDatasets(){calls.push('datasets');panelLoaded.datasets=true;},
  loadDirectory(){calls.push('directory');panelLoaded.directory=true;},
  loadOperations(){calls.push('operations');panelLoaded.operations=true;}};
vm.runInNewContext(extracted+'\nsetupDeferredPanels();',context);
assert.deepEqual(calls,[]);assert.deepEqual(observed.map(node=>node.id),['panel-datasets','panel-operations']);
"""
    # Inject the constructed observer callback into the harness without evaluating the full page boot.
    harness = harness.replace(
        "IntersectionObserver:function(callback){this.callback=callback;this.observe=node=>observed.push(node);}",
        "IntersectionObserver:function(callback){this.callback=callback;window.__callback=callback;this.observe=node=>observed.push(node);}",
    )
    harness += r"""
window.__callback([{isIntersecting:true,target:nodes.get('panel-datasets')}]);assert.deepEqual(calls,[]);
document.body.dataset.placeAlbumView='list';nodes.get('panel-datasets').hidden=true;
window.__callback([{isIntersecting:true,target:nodes.get('panel-datasets')}]);assert.deepEqual(calls,[]);
nodes.get('panel-datasets').hidden=false;window.__callback([{isIntersecting:true,target:nodes.get('panel-datasets')}]);
window.__callback([{isIntersecting:true,target:nodes.get('panel-datasets')}]);assert.deepEqual(calls,['datasets']);
window.location.hash='#panel-directory';hashHandler();assert.deepEqual(calls,['datasets','directory']);
window.location.hash='#panel-operations';hashHandler();assert.deepEqual(calls,['datasets','directory','operations']);
const fallback={document,window:{location:{hash:''},addEventListener(){}},panelLoaded:{datasets:false,directory:false,operations:false},
  loadDatasets(){calls.push('fallback-datasets');},loadDirectory(){calls.push('fallback-directory');},loadOperations(){calls.push('fallback-operations');}};
vm.runInNewContext(extracted+'\nsetupDeferredPanels();',fallback);
assert.deepEqual(calls,['datasets','directory','operations']);
document.body.dataset.placeAlbumView='manage';fallback.window.location.hash='#panel-datasets';
vm.runInNewContext(extracted+'\nsetupDeferredPanels();',fallback);
assert.deepEqual(calls,['datasets','directory','operations','fallback-datasets']);
"""
    result = subprocess.run(
        [node, "-e", harness],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
