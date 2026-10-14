"""Behavioral DOM checks for the UI 2.0 place-album navigation state machine."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _run_node(source: str) -> None:
    node = shutil.which("node")
    assert node is not None
    result = subprocess.run(
        [node, "-e", source], cwd=ROOT, capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stderr


def test_places_page_versions_assets_and_scopes_hidden_views() -> None:
    web = ROOT / "src" / "photo_search" / "web"
    page = (web / "places.html").read_text(encoding="utf-8")
    styles = (web / "places.css").read_text(encoding="utf-8")
    assert "places-albums.js?v=p4-ui2-places-simplify-a-1" in page
    assert "places.js?v=p4-ui2-places-simplify-a-1" in page
    assert "places.css?v=p4-ui2-places-simplify-a-1" in page
    assert 'id="open-place-management"' in page and "整理地点</button>" in page
    assert 'id="place-management" class="panel place-management" hidden' in page
    assert 'data-place-panel="panel-suggestions"' in page and 'data-place-panel="panel-directory"' in page
    assert 'data-place-panel="panel-create"' in page
    assert 'id="place-advanced-tools"' in page and 'id="panel-limits"' not in page
    assert "#album-list-view[hidden]" in styles and "#album-detail-view[hidden]" in styles
    assert "#panel-directory[hidden]" in styles and "#panel-operations[hidden]" in styles


DOM = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
class Element {
  constructor(tag='div'){this.tag=tag;this.children=[];this.events={};this.dataset={};this.attributes={};this.value='';this.hidden=false;this.disabled=false;this.text='';this.className='';this.parent=null;this.classList={contains:n=>this.className.split(/\s+/).includes(n)};}
  set textContent(value){this.text=String(value);this.children.forEach(n=>n.parent=null);this.children=[];}
  get textContent(){return this.text+this.children.map(item=>item.textContent).join('');}
  appendChild(item){item.parent=this;this.children.push(item);return item;}
  append(...items){items.forEach(item=>this.appendChild(item));}
  replaceChildren(...items){this.children.forEach(n=>n.parent=null);this.text='';this.children=[];items.forEach(item=>this.appendChild(item));}
  addEventListener(name,fn){this.events[name]=fn;}
  fire(name,event={}){return this.events[name]&&this.events[name](Object.assign({target:this,preventDefault(){}},event));}
  setAttribute(name,value){this.attributes[name]=String(value);}
  getAttribute(name){return this.attributes[name]||null;}
  focus(){document.activeElement=this;}
  scrollIntoView(){}
  querySelectorAll(selector){let wanted=selector[0]==='.'?selector.slice(1):null;let found=[];function walk(n){n.children.forEach(c=>{if(wanted&&c.className.split(/\s+/).includes(wanted))found.push(c);walk(c);});}walk(this);return found;}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  remove(){if(this.parent)this.parent.children.splice(this.parent.children.indexOf(this),1);}
}
const elements=new Map();
function get(id){if(!elements.has(id)){const e=new Element();e.id=id;elements.set(id,e);}return elements.get(id);}
['album-kind','album-name','album-cards','album-list-view','album-detail-view','album-photos','album-current-title','album-detail-status','album-backfill','album-backfill-status','cn-update','cn-pause','cn-update-status','place-management','place-advanced-tools','open-place-management','back-to-place-albums','place-page-nav','place-intro','panel-albums','panel-datasets','panel-directory','panel-create','panel-edit','panel-suggestions','panel-operations','global-search-scope','global-search-query'].forEach(get);
const menuButtons=['panel-suggestions','panel-directory','panel-create','panel-datasets','panel-operations'].map((id,i)=>{const e=get('manage-'+i);e.dataset.placePanel=id;return e;});
get('album-kind').value='city';
const listeners={};
let href='http://127.0.0.1/places?keep=yes';
const location={get href(){return href;},get pathname(){return new URL(href).pathname;},get search(){return new URL(href).search;},get hash(){return new URL(href).hash;}};
const stack=[{url:href,state:null}], history={index:0,state:null,
  set(index,url,state){this.index=index;href=url.startsWith('http')?url:new URL(url,href).href;this.state=state;stack[index]={url:href,state};},
  pushState(state,_title,url){stack.splice(this.index+1);this.set(++this.index,url,state);},
  replaceState(state,_title,url){this.set(this.index,url,state);},
  back(){if(this.index>0){this.index--;const item=stack[this.index];href=item.url;this.state=item.state;(listeners.popstate||[]).forEach(fn=>fn({state:item.state}));}},
  forward(){if(this.index+1<stack.length){this.index++;const item=stack[this.index];href=item.url;this.state=item.state;(listeners.popstate||[]).forEach(fn=>fn({state:item.state}));}}
};
const document={cookie:'ps_csrf=test',activeElement:null,body:new Element('body'),getElementById:get,createElement:tag=>new Element(tag),querySelectorAll(selector){return selector==='[data-place-panel]'?menuButtons:[];}};
const window={location,history,scrollY:123,PhotoTimeline:null,scopes:[],delayAlbums:location.search.includes('race='),pendingAlbums:[],SearchScopeEntry:{configure(){return function(scope){window.scopes.push(scope);}}},
  addEventListener(name,fn){(listeners[name]||(listeners[name]=[])).push(fn);},dispatchEvent(event){(listeners[event.type]||[]).forEach(fn=>fn(event));},CustomEvent:function(type,options){this.type=type;this.detail=options&&options.detail;},setTimeout(fn){setImmediate(fn);},clearTimeout(){},
  scrollTo(_x,y){this.scrollY=typeof y==='number'?y:((y&&y.top)||0);},alert(message){throw Error(message);}};
let requests=[],mounts=[],resets=[];
const album={kind:'city',key:'cn:3',name:'济南市',parent:'山东省',total:2,origin:'OSM',cover_asset_id:'asset-1'};
const response=(body,status=200)=>({ok:status<400,status,json:async()=>body});
window.PhotoTimeline={mount(options){mounts.push(options);let snap={catalogFilterRevision:8,timeBasis:'capture',groupBy:'month',bucket:'unknown',year:2024,month:2,anchor:'anchor-x',assetId:'asset-x',scope:{place_key:options.scope.place_key}};return {snapshot(){return snap;},dispose(){},reset(anchor){resets.push(anchor);snap.anchor=anchor;}};}};
const context={URLSearchParams,URL,Date,Math,console,document,window,fetch:async(url,options={})=>{requests.push({url:String(url),options});
  if(String(url).includes('/places/admin/capabilities'))return response({available:false});
  if(String(url).includes('/places/albums')){if(window.delayAlbums)return new Promise(resolve=>window.pendingAlbums.push(body=>resolve(response(body))));return response({albums:[album]});}
  if(String(url).includes('/places/choices'))return response({choices:[{kind:'district',key:'cn:44',name:'历下区',parent:'山东省 · 济南市',source:'OSM',selection_ref:'osm|fixture|relation/44',aliases:[]}]});
  if(String(url).includes('/assets/asset-1/place')&&options.method!=='PUT')return response({assignment:{revision:4}});
  if(String(url).includes('/place-assignment'))return response({changed:true});
  throw Error('unexpected request '+url);
}};
vm.createContext(context);vm.runInContext(fs.readFileSync('src/photo_search/web/places-albums.js','utf8'),context);
const settle=async()=>{for(let i=0;i<8;i++)await new Promise(setImmediate);};
"""


def test_list_detail_back_forward_preserves_url_scope_and_light_timeline_view() -> None:
    _run_node(
        DOM
        + r"""
(async()=>{
  await settle();
  assert.equal(get('album-list-view').hidden,false);assert.equal(get('place-management').hidden,true);assert.equal(get('panel-suggestions').hidden,true);
  window.scrollY=451;
  get('album-cards').children[0].fire('click'); await settle();
  assert.equal(new URL(window.location.href).searchParams.get('album_key'),'cn:3');
  assert.equal(new URL(window.location.href).searchParams.get('keep'),'yes');
  assert.equal(get('album-list-view').hidden,true); assert.equal(get('album-detail-view').hidden,false);
  assert.equal(get('panel-directory').hidden,true); assert.equal(get('panel-albums').hidden,false);
  assert.equal(window.scopes.at(-1).place_key,'cn:3');
  assert.equal(mounts.length,1); assert.equal(mounts[0].scope.place_key,'cn:3');
  window.history.back(); await settle();
  assert.equal(get('album-detail-view').hidden,true); assert.equal(get('album-list-view').hidden,false);
  assert.equal(window.scopes.at(-1),null);
  assert.equal(window.scrollY,451); assert.equal(get('album-kind').value,'city');
  window.history.forward(); await settle();
  assert.equal(get('album-detail-view').hidden,false); assert.equal(mounts.length,2);
  assert.equal(mounts[1].restoreView.anchor,'anchor-x'); assert.equal(mounts[1].restoreView.assetId,'asset-x');
  assert.equal(mounts[1].restoreView.bucket,'unknown'); assert.equal(mounts[1].restoreView.year,2024);
  assert.equal(requests.filter(r=>r.url.includes('/places/albums')).length,2);
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    )

def test_direct_cn_deep_link_uses_one_exact_kind_lookup_and_shows_invalid_scope() -> None:
    _run_node(
        DOM.replace("let href='http://127.0.0.1/places?keep=yes';", "let href='http://127.0.0.1/places?album_kind=city&album_key=cn%3A3&keep=yes';")
        + r"""
(async()=>{
  await settle();
  assert.equal(get('album-list-view').hidden,true); assert.equal(get('album-detail-view').hidden,false);
  assert.equal(mounts.length,1); assert.equal(mounts[0].scope.place_key,'cn:3');
  const calls=requests.filter(r=>r.url.includes('/places/albums'));
  assert.equal(calls.length,1); assert.match(calls[0].url,/kind=city/); assert.doesNotMatch(calls[0].url,/name=/);
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    )


def test_late_deep_link_response_cannot_replace_newer_popstate_scope() -> None:
    source = DOM.replace(
        "let href='http://127.0.0.1/places?keep=yes';",
        "let href='http://127.0.0.1/places?album_kind=city&album_key=cn%3A3&race=1';",
    )
    _run_node(
        source
        + r"""
(async()=>{
  await settle();assert.equal(window.pendingAlbums.length,1);
  const next=new URL(window.location.href);next.searchParams.set('album_kind','province');next.searchParams.set('album_key','prov:8');
  window.history.pushState({__placeAlbumReturn:true},'',next.pathname+next.search+next.hash);
  (listeners.popstate||[]).forEach(fn=>fn({state:window.history.state}));await settle();assert.equal(window.pendingAlbums.length,2);
  window.pendingAlbums[1]({albums:[{kind:'province',key:'prov:8',name:'浙江省',parent:'中国',total:1,cover_asset_id:'asset-p'}]});await settle();
  window.pendingAlbums[0]({albums:[album]});await settle();
  assert.equal(mounts.length,1);assert.equal(mounts[0].scope.place_kind,'province');assert.equal(mounts[0].scope.place_key,'prov:8');
  assert.match(get('album-current-title').textContent,/浙江省/);
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    )
    invalid = DOM.replace(
        "let href='http://127.0.0.1/places?keep=yes';",
        "let href='http://127.0.0.1/places?album_kind=city&album_key=cn%3A404';",
    ).replace("key:'cn:3'", "key:'3'")
    _run_node(
        invalid
        + r"""
(async()=>{await settle();assert.equal(mounts.length,0);assert.match(get('album-detail-status').textContent,/已不存在|有效照片/);assert.equal(get('album-list-view').hidden,true);})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    )


def test_management_hash_wins_over_album_deep_link_and_only_clears_album_params() -> None:
    source = DOM.replace(
        "let href='http://127.0.0.1/places?keep=yes';",
        "let href='http://127.0.0.1/places?album_kind=city&album_key=cn%3A3&keep=yes#panel-directory';",
    )
    _run_node(
        source
        + r"""
(async()=>{await settle();assert.equal(get('album-list-view').hidden,true);assert.equal(get('panel-albums').hidden,true);assert.equal(get('place-management').hidden,false);assert.equal(get('panel-directory').hidden,false);
  const url=new URL(window.location.href);assert.equal(url.searchParams.has('album_kind'),false);assert.equal(url.searchParams.get('keep'),'yes');assert.equal(url.hash,'#panel-directory');assert.equal(mounts.length,0);
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    )


def test_place_correction_refreshes_current_timeline_without_remount_or_url_change() -> None:
    _run_node(
        DOM
        + r"""
(async()=>{
  await settle();get('album-cards').children[0].fire('click');await settle();
  const route=window.location.href, options=mounts[0];
  const photo=options.adapter.renderAsset({asset_id:'asset-1',date_state:'unknown',time_label:'日期未知'});
  const correction=photo.children.find(node=>node.className==='album-correction');
  const body=correction.children.find(node=>node.className==='album-correction__body');
  const input=body.children.find(node=>node.tag==='div').children.find(node=>node.tag==='input');input.value='历下区';
  body.children.find(node=>node.tag==='div').children.find(node=>node.tag==='button').fire('click');await settle();
  const choices=correction.querySelector('.place-choice-list');assert.ok(choices);
  choices.children[1].fire('click');await settle();
  assert.equal(mounts.length,1);assert.deepEqual(resets,['anchor-x']);
  assert.equal(window.location.href,route);assert.equal(new URL(route).searchParams.get('album_key'),'cn:3');
  assert.ok(requests.some(r=>r.url.includes('/place-assignment')));
  assert.ok(requests.some(r=>r.url.includes('/places/albums?kind=city')));
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    )


def test_manage_mode_is_opt_in_and_switches_one_tool_panel_at_a_time() -> None:
    _run_node(
        DOM
        + r"""
(async()=>{
  await settle();get('open-place-management').fire('click');await settle();
  assert.equal(get('place-management').hidden,false);assert.equal(get('panel-suggestions').hidden,false);
  assert.equal(get('panel-directory').hidden,true);assert.equal(get('place-advanced-tools').hidden,true);
  get('manage-2').fire('click');await settle();
  assert.equal(get('panel-create').hidden,false);assert.equal(get('panel-datasets').hidden,true);assert.equal(get('place-advanced-tools').hidden,true);
  window.dispatchEvent(new window.CustomEvent('place-management-panel-requested',{detail:{id:'panel-edit'}}));await settle();
  assert.equal(get('panel-edit').hidden,false);assert.equal(get('panel-create').hidden,true);
  get('manage-3').fire('click');await settle();
  assert.equal(get('panel-datasets').hidden,false);assert.equal(get('place-advanced-tools').hidden,false);
  assert.equal(get('panel-suggestions').hidden,true);assert.match(window.location.href,/place_view=manage/);
  get('back-to-place-albums').fire('click');await settle();
  assert.equal(get('place-management').hidden,true);assert.equal(get('album-list-view').hidden,false);
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    )


def test_place_correction_actions_keep_explicit_assignment_contracts() -> None:
    _run_node(
        DOM
        + r"""
(async()=>{
  await settle();get('album-cards').children[0].fire('click');await settle();
  const photo=mounts[0].adapter.renderAsset({asset_id:'asset-1',date_state:'unknown'});
  const details=photo.children.find(node=>node.className==='album-correction');
  const body=details.children.find(node=>node.className==='album-correction__body');
  assert.match(body.textContent,/排除后会阻止自动重新归入此地点/);
  const actions=body.children.filter(node=>node.tag==='button');
  assert.equal(actions[0].textContent,'排除此照片的地点归属');
  assert.equal(actions[1].textContent,'恢复自动识别');
  actions[0].fire('click');await settle();actions[1].fire('click');await settle();
  const writes=requests.filter(request=>request.url.includes('/place-assignment'));
  assert.equal(writes.length,2);
  assert.equal(JSON.parse(writes[0].options.body).action,'unassign');
  assert.equal(JSON.parse(writes[1].options.body).action,'restore_auto');
  writes.forEach(write=>assert.equal(write.options.headers['X-CSRF-Token'],'test'));
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    )


def test_detail_url_wins_over_manage_query_and_clears_conflicting_query() -> None:
    source = DOM.replace(
        "let href='http://127.0.0.1/places?keep=yes';",
        "let href='http://127.0.0.1/places?album_kind=city&album_key=cn%3A3&place_view=manage&keep=yes';",
    )
    _run_node(
        source
        + r"""
(async()=>{await settle();const url=new URL(window.location.href);
  assert.equal(get('album-detail-view').hidden,false);assert.equal(get('place-management').hidden,true);
  assert.equal(url.searchParams.get('album_key'),'cn:3');assert.equal(url.searchParams.has('place_view'),false);
  assert.equal(url.searchParams.get('keep'),'yes');assert.equal(mounts.length,1);
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    )
