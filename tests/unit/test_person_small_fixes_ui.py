"""Run the real UI through rename, cross-window updates and collection removal."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_person_writes_refresh_candidates_choices_and_collection_returns() -> None:
    node = shutil.which("node")
    assert node, "Node is required for the personal UI regression"
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.value='';this.text='';this.hidden=false;
 this.events={};this.attributes={};this.dataset={};this.classList={add(){},remove(){}};this.open=false;}
 set textContent(v){this.text=String(v);this.children=[];}get textContent(){return this.text+this.children.map(x=>x.textContent).join('');}
 append(...items){items.forEach(x=>{x.parent=this;this.children.push(x);});}
 replaceChildren(...items){this.text='';this.children=[];this.append(...items);}
 addEventListener(n,fn){this.events[n]=fn;}fire(n,e={}){return this.events[n]?.({preventDefault(){},...e});}
 setAttribute(n,v){this.attributes[n]=v;}removeAttribute(n){delete this.attributes[n];}
 querySelectorAll(s){return this.children.filter(x=>s==='.pa-choice'&&x.className==='pa-choice');}
 remove(){this.parent.children=this.parent.children.filter(x=>x!==this);}focus(){}
 showModal(){this.open=true;}close(){this.open=false;this.fire('close');}
}
// Model browser replacement/move semantics, including avatar reuse.
if (!Element.prototype.replaceChild) Element.prototype.replaceChild=function(next,old){const i=this.children.indexOf(old);assert(i>=0);this.children[i]=next;next.parentNode=this;return old;};
if (!Element.prototype.remove) Element.prototype.remove=function(){if(this.parentNode){this.parentNode.children=this.parentNode.children.filter(x=>x!==this);this.parentNode=null;}};
const elements=new Map(),get=id=>{if(!id.startsWith('pa-'))id='pa-'+id;if(!elements.has(id))elements.set(id,new Element('div'));return elements.get(id);};
 const calls=[],events={},storage=new Map();let display=null,revision=1,uuid=0,assetCount=5,collectionPeople=null,merged=false,delayedPeople=null;
const target={person_id:8,name:'合并目标',revision:1,asset_count:6,thumbnail_url:null};
const person=()=>({person_id:7,name:display,revision,asset_count:assetCount,thumbnail_url:null});
const collection={collection_id:2,instance_token:'a'.repeat(32),name:'合集',revision:1};
const ok=body=>({ok:true,json:async()=>body});
const context={URLSearchParams,URL,setTimeout:()=>1,clearTimeout(){},crypto:{randomUUID:()=>String(++uuid)},
 window:{location:{href:'http://local/persons',search:''},confirm:()=>true,history:{replaceState(){}},
 addEventListener(n,fn){const prior=events[n];events[n]=prior?(...args)=>{prior(...args);fn(...args);}:fn;},localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)}},
 document:{readyState:'loading',cookie:'ps_csrf=csrf',getElementById:get,createElement:t=>new Element(t),addEventListener(){}},
 fetch:async(url,opts={})=>{calls.push({url,opts});
  if(url.endsWith('/label')){display=JSON.parse(opts.body).name;revision++;return ok({changed:true,operation_id:'rename',operation_revision:1});}
  if(url.endsWith('/assignment')){assetCount=4;collectionPeople=[person(),target];return ok({changed:true,operation_id:'correct',operation_revision:2});}
  if(url==='/api/v1/persons/merge'){merged=true;collectionPeople=[target];return ok({changed:true,operation_id:'merge',operation_revision:3});}
   if(url.endsWith('/undo')){merged=false;collectionPeople=[person(),target];return ok({changed:true});}
  if(url.includes('/persons/browse?'))return ok({persons:collectionPeople||[person()],total:1,has_more:false,next_cursor:null});
  if(url==='/api/v1/persons/7')return ok(merged?target:person());
   if(url==='/api/v1/persons/8')return ok(target);
  if(url.includes('/persons/7/photos?')||url.includes('/persons/8/photos?'))return ok({person:merged?target:person(),assets:[],has_more:false});
  if(url.includes('/persons/collections?'))return ok({collections:[collection],has_more:false});
  if(url.endsWith('/members'))return ok({changed:1});
   if(url.includes('/people?')){if(delayedPeople)return delayedPeople;return ok({collection,people:collectionPeople||[person()],has_more:false});}
   if(url.includes('/collections/')&&url.includes('/photos?'))return ok({collection,assets:[{asset_id:99}],has_more:false});
  if(url.includes('/faces/inbox/hidden/groups?'))return ok({cards:[{type:'candidate_group',group_key:'candidate:7',candidate_name:display,photo_count:2,face:{thumbnail_url:null}}],has_more:false});
  if(url.includes('/faces/inbox/groups?'))return ok({cards:[{type:'candidate_group',group_key:'candidate:7',candidate_name:display,photo_count:4,face:{thumbnail_url:null}}],has_more:false});
  if(url.includes('/faces/inbox/summary?'))return ok({total:4,reasons:{},held:0});
  return ok({faces:[],sources:[],has_more:false});
 }};
let code=fs.readFileSync('src/photo_search/web/persons-albums.js','utf8');
 code=code.replace('  if (document.readyState === "loading")','  globalThis.ui={state,boot,setLibraryView,editBase,editFace,editMerge,writeFace,undo,refreshAfterWrite,backToLibrary,loadCollectionPeople,removeCollectionSelection};\n  if (document.readyState === "loading")');
if (!context.window) context.window={location:{search:"",hash:"",href:"http://local/persons"},scrollY:0,addEventListener(){},localStorage:{getItem(){return null;},setItem(){}}};
vm.createContext(context);vm.runInContext(code,context);const ui=context.ui,flush=()=>new Promise(setImmediate);
(async()=>{
 // Candidate and ignored-face directories are mutually exclusive in the real UI.
 ui.boot();await flush();ui.setLibraryView('faces',{history:false});await flush();
 ui.state.person=person();ui.editBase({mode:'name',person:person()},'改名','');get('edit-query').value='Alice';
 await get('edit-form').fire('submit');await flush();await flush();
 assert(get('inbox-cards').textContent.includes('Alice'),'visible groups show saved name');
 ui.setLibraryView('ignored',{history:false});await flush();await flush();
 assert(get('inbox-hidden-cards').textContent.includes('Alice'),'hidden groups show saved name when opened');
 assert.equal(storage.has('aisearch.persons.changed'),true,'successful rename notifies other tabs');
 ui.editFace({face_id:'f'.repeat(64),control_revision:0,assigned_person_id:null,thumbnail_url:null});await flush();
 assert(get('choices').textContent.includes('Alice'));assert.equal(get('choices').children.filter(x=>x.className==='pa-choice').length,1);
 const picker=calls.filter(c=>c.url.includes('/persons/browse?')&&c.url.includes('limit=20')).at(-1);
 assert(picker.url.includes('sort=photos_desc'),'face and merge choices request globally sorted pages');
 display='Bob';revision++;storage.set('aisearch.persons.changed','another-window');
 events.storage({key:'aisearch.persons.changed'});await flush();await flush();
 assert(get('inbox-hidden-cards').textContent.includes('Bob'),'active ignored groups refresh across windows');
 assert(get('choices').textContent.includes('Bob'));assert(!get('choices').textContent.includes('Alice'));
 assert.equal(get('edit-submit').disabled,true,'stale chosen person requires a fresh selection');
 get('edit').close();ui.setLibraryView('faces',{history:false});await flush();await flush();
 assert(get('inbox-cards').textContent.includes('Bob'),'visible groups refresh when revisited');
 // The freshness assertion belongs to the person directory, whose cache is being checked.
 ui.setLibraryView('people',{history:false});await flush();await flush();
 const unchanged=calls.length;events.focus();await flush();assert.equal(calls.length,unchanged,'unchanged focus keeps loaded pages');
 ui.setLibraryView('faces',{history:false});await flush();await flush();
 ui.editBase({mode:'name',person:person()},'改名','');get('edit-query').value='尚未提交';
 display='Carol';storage.set('aisearch.persons.changed','third-window');events.focus();await flush();
 assert.equal(get('edit-query').value,'尚未提交','refresh does not overwrite an unfinished name');
 display='Dora';storage.set('aisearch.persons.changed','browser-back');events.pageshow({persisted:true});await flush();
 assert(get('inbox-cards').textContent.includes('Dora'),'restored browser-back pages refresh current candidate names');
 assert.equal(get('edit-query').value,'尚未提交');
 get('edit').close();ui.setLibraryView('people',{history:false});await flush();
 ui.state.returnCollection=collection;ui.backToLibrary();await flush();
 assert(get('collection-people').textContent.includes('Dora'),'returning to collection rebuilds stale member labels');
 ui.state.collectionPeopleManage=true;ui.state.collectionSelectedPeople.add(7);const before=calls.length;await ui.removeCollectionSelection();await flush();
 const recent=calls.slice(before);assert(recent.some(c=>c.url.endsWith('/members')));
 assert(recent.some(c=>c.url.includes('/persons/browse?')),'removal refreshes ordinary named/unnamed lists');
 assert(get('status').textContent.includes('人物忽略状态保持不变'));
 // Enter an actual collection card; writes must refresh its suspended parent list.
 collectionPeople=[person(),target];ui.state.collection=collection;ui.state.collectionMode='people';
 await ui.loadCollectionPeople(false);assert.equal(get('collection-people').children.length,2);
 get('collection-people').children[0].fire('click');await flush();
 assert.equal(ui.state.collection,null);assert.equal(ui.state.returnCollection.collection_id,2);
 assert.equal(get('collection-people-actions').hidden,true,'parent controls stay hidden inside one person');
 ui.state.collectionSelectedPeople.add(7);
 ui.editFace({face_id:'c'.repeat(64),control_revision:0,assigned_person_id:7,thumbnail_url:null});await flush();
 ui.writeFace('unassign');await flush();await flush();await flush();
 assert(get('collection-people').children[0].textContent.includes('4 张照片'),'face correction updates the parent before returning');
 assert.equal(ui.state.collectionSelectedPeople.size,0,'writes invalidate old collection selections');
 assert.equal(get('collection-people-actions').hidden,true);
 ui.editMerge(ui.state.person);await flush();
 const choice=get('choices').querySelectorAll('.pa-choice')[0];assert(choice);choice.fire('click');
 get('edit-form').fire('submit');await flush();await flush();await flush();
 assert.equal(get('collection-people').children.length,1,'merge removes the old source avatar from the parent list');
 assert(get('collection-people').textContent.includes('合并目标'));assert(get('collection-people').textContent.includes('6 张照片'));
 assert.equal(ui.state.person.person_id,8,'single-person view follows the merged canonical person');
 ui.backToLibrary();await flush();
 assert.equal(get('collection-people').children.length,1);assert.equal(get('collection-people-actions').hidden,false);
 assert.equal(get('collection-people-tab').attributes['aria-pressed'],'true');
 await ui.undo();await flush();
 assert.equal(get('collection-people').children.length,2,'undo restores both collection avatars');
 assert(get('collection-people').textContent.includes('4 张照片'));
 get('collection-photos-tab').fire('click');await flush();
 ui.state.collectionSelectedPeople.add(7);
 const beforeFocus=calls.length;storage.set('aisearch.persons.changed','external-correction');events.focus();await flush();
 assert(calls.slice(beforeFocus).some(c=>c.url.includes('/collections/')&&c.url.includes('/photos?')),'external corrections refresh collection photos');
 assert(calls.slice(beforeFocus).some(c=>c.url.includes('/people?')),'external corrections refresh collection people in photo mode');
 assert.equal(ui.state.photoIds[0],99);assert.equal(get('collection-people-actions').hidden,true);
 assert.equal(ui.state.collectionSelectedPeople.size,0,'external writes also invalidate old member selections');
 // A pending response from the previous collection instance must not replace the current view.
 let resolvePeople;delayedPeople=new Promise(resolve=>{resolvePeople=resolve;});
 const pending=ui.loadCollectionPeople(false);
 ui.state.collection={...collection,instance_token:'b'.repeat(32)};get('collection-people').textContent='新合集';
 resolvePeople(ok({people:[person()],has_more:false}));await pending;
 assert.equal(get('collection-people').textContent,'新合集','stale parent instance response is ignored');
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", script], cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True, encoding="utf-8", timeout=15,
    )
    assert result.returncode == 0, result.stderr
