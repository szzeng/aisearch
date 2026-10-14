"""Focused synthetic DOM coverage for the P4 UI 2.0 selection contracts."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _run_node(script: str) -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the UI selection checks"
    result = subprocess.run([node, "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_place_suggestion_shift_and_loaded_shortcuts_only_change_local_selection() -> None:
    _run_node(r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.events={};this.attributes={};this.className='';this.checked=false;this.hidden=false;this.disabled=false;this.value='';this.parentNode=null;this.text='';}
 set textContent(value){this.text=String(value);this.children.forEach(x=>x.parentNode=null);this.children=[];}
 get textContent(){return this.text+this.children.map(x=>x.textContent).join('');}
 appendChild(item){if(item.parentNode){const i=item.parentNode.children.indexOf(item);if(i>=0)item.parentNode.children.splice(i,1);}item.parentNode=this;this.children.push(item);return item;}
 append(...items){items.forEach(x=>this.appendChild(x));}
 setAttribute(k,v){this.attributes[k]=String(v);} getAttribute(k){return this.attributes[k]??null;}
 addEventListener(k,fn){this.events[k]=fn;}
 querySelectorAll(selector){const out=[];const walk=e=>{for(const x of e.children){if(selector==='input[type=checkbox]'&&x.tag==='input'&&x.type==='checkbox')out.push(x);walk(x);}};walk(this);return out;}
 fire(name,event={}){let prevented=false;return this.events[name]({...event,preventDefault(){prevented=true;},get defaultPrevented(){return prevented;},stopPropagation(){}});}
}
const context={URLSearchParams,Date,document:{readyState:'loading',addEventListener(){},createElement:t=>new Element(t)},fetch:async()=>{throw Error('selection must not call an API');}};
let source=fs.readFileSync('src/photo_search/web/places.js','utf8');
source=source.replace('  if (document.readyState === "loading") {','  globalThis.__probe={state,buildSuggestionRow,selectPage,invertLoadedSuggestions,clearSuggestionSelection,selectedIds,init(root,summary){els.suggestedList=root;els.selectionSummary=summary;}};\n  if (document.readyState === "loading") {');
vm.createContext(context);vm.runInContext(source,context);
const root=new Element('ul'),summary=new Element('span'),ui=context.__probe;ui.init(root,summary);
for(let id=1;id<=10;id++)root.appendChild(ui.buildSuggestionRow({asset_id:id,created_at:1,location_count:1,availability:'present'}));
const box=id=>root.children[id-1].querySelectorAll('input[type=checkbox]')[0];
box(2).fire('click');box(2).checked=true;box(2).fire('change');
box(6).fire('click',{shiftKey:true});
assert.deepEqual(Array.from(ui.selectedIds()),[2,3,4,5,6]);
assert.deepEqual(root.children.slice(1,6).map(row=>row.querySelectorAll('input[type=checkbox]')[0].checked),[true,true,true,true,true]);
box(4).fire('click');box(4).checked=false;box(4).fire('change');
assert.deepEqual(Array.from(ui.selectedIds()),[2,3,5,6]);
assert.match(summary.textContent,/已选择 4 个资产/);
ui.state.selection['99']=true;ui.selectPage();assert.equal(ui.selectedIds().length,11);
ui.invertLoadedSuggestions();assert.deepEqual(Array.from(ui.selectedIds()),[99]);
assert.equal(root.children[0].getAttribute('data-selected'),'false');
ui.clearSuggestionSelection();assert.deepEqual(Array.from(ui.selectedIds()),[]);
assert.equal(summary.textContent,'已选择 0 个资产');
assert.equal(ui.state.selectionAnchor,null);
""")


def test_collection_people_checkbox_shift_shortcuts_and_busy_guard() -> None:
    _run_node(r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
class Element {
 constructor(tag){this.tagName=tag.toUpperCase();this.tag=tag;this.children=[];this.events={};this.attributes={};this.className='';this.classList={add(){}};this.hidden=false;this.disabled=false;this.checked=false;this.value='';this.parentNode=null;this.text='';}
 set textContent(value){this.text=String(value);this.children.forEach(x=>x.parentNode=null);this.children=[];}
 get textContent(){return this.text+this.children.map(x=>x.textContent).join('');}
 appendChild(item){item.parentNode=this;this.children.push(item);return item;} append(...items){items.forEach(x=>this.appendChild(x));}
 replaceChildren(...items){this.children.forEach(x=>x.parentNode=null);this.text='';this.children=[];this.append(...items);}
 setAttribute(k,v){this.attributes[k]=String(v);} addEventListener(k,fn){this.events[k]=fn;}
 fire(name,event={}){return this.events[name]({...event,preventDefault(){this.prevented=true;},stopPropagation(){}});} click(){return this.fire('click');}
}
const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element('div'));return elements.get(id);},requests=[];
const context={crypto:{randomUUID:()=> 'req'},URLSearchParams,Date,Map,Set,WeakSet,document:{readyState:'loading',cookie:'ps_csrf=csrf',addEventListener(){},getElementById:get,createElement:t=>new Element(t)},window:{crypto:{randomUUID:()=> 'req'},location:{search:'',href:'http://local/persons'},addEventListener(){},confirm:()=>true},fetch:async(url,options={})=>{requests.push({url,options});return {ok:true,status:200,json:async()=>({people:Array.from({length:8},(_,i)=>({person_id:11+i,name:'人'+(11+i),asset_count:1,thumbnail_url:null})),has_more:false,next_cursor:null})};},setTimeout:()=>1,clearTimeout(){}};
let source=fs.readFileSync('src/photo_search/web/persons-albums.js','utf8');
source=source.replace('  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();','  globalThis.__probe={state,loadCollectionPeople,updateCollectionSelection,selectLoadedCollectionPeople,clearCollectionSelection,removeCollectionSelection,init(collection){state.collection=collection;state.collectionMode="people";}};\n  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();');
vm.createContext(context);vm.runInContext(source,context);
const ui=context.__probe,collection={collection_id:7,instance_token:'a'.repeat(32),revision:1};ui.init(collection);
(async()=>{
 await ui.loadCollectionPeople(false);
 const cards=get('pa-collection-people').children;assert.equal(cards.length,8);
 const beforeSelection=requests.length;
 const checkbox=i=>cards[i].selectionBox;
 assert.equal(checkbox(0).type,'checkbox');assert.match(checkbox(0).attributes['aria-label'],/移出合集/);
 checkbox(1).fire('click');checkbox(4).fire('click',{shiftKey:true});
 assert.deepEqual(Array.from(ui.state.collectionSelectedPeople),[12,13,14,15]);
 assert.equal(cards[2].attributes['data-selected'],'true');assert.match(get('pa-collection-people-selected').textContent,/已选 4 人/);
 ui.selectLoadedCollectionPeople(false);assert.equal(ui.state.collectionSelectedPeople.size,8);
 ui.selectLoadedCollectionPeople(true);assert.equal(ui.state.collectionSelectedPeople.size,0);
 ui.selectLoadedCollectionPeople(false);ui.clearCollectionSelection();assert.equal(ui.state.collectionSelectedPeople.size,0);
 checkbox(0).fire('keydown',{key:'Enter'});assert.deepEqual(Array.from(ui.state.collectionSelectedPeople),[11]);
 ui.clearCollectionSelection();
 ui.state.writing=true;ui.updateCollectionSelection();assert.equal(checkbox(0).disabled,true);
 checkbox(0).fire('click');assert.equal(ui.state.collectionSelectedPeople.size,0,'busy state blocks edits');
 ui.state.writing=false;
 assert.equal(requests.length,beforeSelection,'selection operations do not issue API requests');
})().catch(error=>{console.error(error);process.exitCode=1;});
""")
