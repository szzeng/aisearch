"""Actual JS regression for retained pages, fresh cursors and library return position."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_people_and_inbox_refresh_preserve_loaded_extent_and_position() -> None:
    node = shutil.which("node")
    assert node, "Node is required for the personal-page behavior check"
    page = Path(__file__).resolve().parents[2] / "src/photo_search/web/persons.html"
    assert 'persons-albums.js?v=p4-person-inbox-compare-a-1' in page.read_text(encoding="utf-8")
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
class HTMLCollectionMock extends Array { static get [Symbol.species](){return Array;} constructor(...items){super(...items);this.find=undefined;} }
class Element {
 constructor(tag){this.tag=tag;this.tagName=String(tag).toUpperCase();this.children=new HTMLCollectionMock();this.value='';this.text='';this.hidden=false;this.disabled=false;
  this.events={};this.attributes={};this.dataset={};this.open=false;this.classList={add(){}};}
 set textContent(v){this.text=String(v);this.children=new HTMLCollectionMock();}get textContent(){return this.text+this.children.map(c=>c.textContent).join('');}
 append(...items){items.forEach(c=>{if(c.parent)c.parent.children=c.parent.children.filter(x=>x!==c);c.parent=this;this.children.push(c);});}
 replaceChildren(...items){this.children.forEach(c=>c.parent=null);this.children=new HTMLCollectionMock();this.text='';this.append(...items);}
 replaceChild(next,old){const index=this.children.indexOf(old);if(index<0)throw Error('missing child');old.parent=null;if(next.parent)next.parent.children=next.parent.children.filter(x=>x!==next);next.parent=this;this.children[index]=next;return old;}
 remove(){if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this);this.parent=null;}
 setAttribute(k,v){this.attributes[k]=v;}removeAttribute(k){delete this.attributes[k];}
 addEventListener(n,fn){this.events[n]=fn;}fire(n){return this.events[n]?.();}
 querySelector(s){return Array.from(this.children).find(c=>String(c.className||'').split(' ').includes(s.slice(1)));}
 querySelectorAll(){return [];}focus(options){this.focusOptions=options;}
 showModal(){this.open=true;}close(){this.open=false;}
 getBoundingClientRect(){const index=this.parent?.children.indexOf(this)||0;
  const top=(this.parent===get('inbox-cards')?1800:0)+index*100-context.window.scrollY;return {top,bottom:top+100};}
}
// Model browser replacement/move semantics, including avatar reuse.
if (!Element.prototype.replaceChild) Element.prototype.replaceChild=function(next,old){const i=this.children.indexOf(old);assert(i>=0);this.children[i]=next;next.parentNode=this;return old;};
if (!Element.prototype.remove) Element.prototype.remove=function(){if(this.parentNode){this.parentNode.children=this.parentNode.children.filter(x=>x!==this);this.parentNode=null;}};
const elements=new Map(),get=id=>{if(!id.startsWith('pa-'))id='pa-'+id;if(!elements.has(id))elements.set(id,new Element('div'));return elements.get(id);};
const calls=[];let revision=1,uuid=0,failSecond=false,delayed=null,summaryGate=null,releaseSummary=null;
const collections=Array.from({length:45},(_,i)=>({collection_id:i+1,name:'合集'+i,member_count:1,photo_count:2,instance_token:'token'+i}));
let people=[1,2,3,4].map(id=>{const face=(id).toString(16).padStart(64,'0');return {person_id:id,name:null,revision:1,asset_count:5,avatar_face_id:face,thumbnail_url:`/api/v1/faces/${face}/thumbnail`};});
let groups=[11,22,33,44];const groupFaces=new Map(groups.map(id=>[id,Array.from({length:5},(_,i)=>({
 face_id:(id*10+i).toString(16).padStart(64,'0'),asset_id:id*10+i,control_revision:0,thumbnail_url:null,
 organization_reason:'NEAR_TIE',suggestions:[{person_id:id,name:'候选'+id}]}))]));
const ok=v=>({ok:true,json:async()=>v}),flush=()=>new Promise(setImmediate);
function page(items,params,key,cap=1){let index=0;
 if(params.has('cursor')){const [v,n]=params.get('cursor').split(':');assert.equal(Number(v),revision,'never reuse pre-write cursors');index=Number(n);}
 const has_more=index+cap<items.length;return {[key]:items.slice(index,index+cap),total:items.length,has_more,
  next_cursor:has_more?revision+':'+(index+cap):null};}
const context={URLSearchParams,URL,crypto:{randomUUID:()=>String(++uuid)},setTimeout:()=>1,clearTimeout(){},
 window:{scrollY:0,scrollBy(x,y){this.scrollY+=y;},scrollTo(x,y){this.scrollY=y;},addEventListener(){},confirm:()=>true,
  location:{href:'http://local/persons',search:''},localStorage:{getItem:()=>null,setItem(){}}},
 document:{readyState:'loading',cookie:'ps_csrf=test',addEventListener(){},getElementById:get,createElement:t=>new Element(t)},
 fetch:async(url,options={})=>{calls.push({url,options});const u=new URL(url,'http://local');
  if(url.endsWith('/assignment')){const id=url.split('/')[4];groupFaces.set(33,groupFaces.get(33).filter(f=>f.face_id!==id));revision++;
   return ok({changed:true,operation_id:'confirmed',operation_revision:1});}
  if(url.includes('/persons/browse?')){
   if(delayed&&!u.searchParams.has('q'))return delayed;
   if(failSecond&&u.searchParams.has('cursor'))return {ok:false,status:503,json:async()=>({code:'TEMPORARY'})};
   if(delayed&&u.searchParams.get('q')==='slow')return delayed;
   return ok(page(u.searchParams.get('q')?people.slice(0,1):people,u.searchParams,'persons'));
  }
  if(url.includes('/faces/inbox/groups/candidate%3A')){
   const id=Number(decodeURIComponent(u.pathname).split('candidate:')[1]);return ok(page(groupFaces.get(id),u.searchParams,'faces',2));
  }
  if(url.includes('/faces/inbox/groups?'))return ok(page(groups.map(id=>({type:'candidate_group',group_key:'candidate:'+id,
   candidate_name:'候选'+id,photo_count:groupFaces.get(id).length,face:groupFaces.get(id)[0]})),u.searchParams,'cards'));
  if(url.includes('/faces/inbox/summary?'))return summaryGate||ok({total:20,reasons:{},held:0});
  if(url.includes('/persons/collections/')&&url.endsWith('/members'))return ok({changed:1});
  if(url.includes('/faces/inbox?'))return ok(page(groupFaces.get(22),u.searchParams,'faces',2));
  if(url.includes('/persons/collections?')){const index=Number(u.searchParams.get('cursor')||0),items=collections.slice(index,index+40),next=index+40<collections.length?String(index+40):null;return ok({collections:items,has_more:!!next,next_cursor:next});}
  if(url==='/api/v1/persons/3')return ok({...people[0],person_id:3,name:'已命名'});
  if(url.includes('/persons/3/photos?'))return ok({assets:[],has_more:false});
  return ok({faces:[],cards:[],has_more:false});
 }};
let code=fs.readFileSync('src/photo_search/web/persons-albums.js','utf8');
code=code.replace('  if (document.readyState === "loading")','  globalThis.ui={state,loadPeople,loadInbox,loadInboxSummary,loadCollections,ensureAllCollectionsLoaded,refreshPeopleViews,refreshChangedPeople,refreshAfterWrite,backToLibrary,setView,renderPeopleManagement,addSelectionToCollection,boot};\n  if (document.readyState === "loading")');
if (!context.window) context.window={location:{search:"",hash:"",href:"http://local/persons"},scrollY:0,addEventListener(){},localStorage:{getItem(){return null;},setItem(){}}};
vm.createContext(context);vm.runInContext(code,context);const ui=context.ui;
const ids=()=>get('cards').children.map(c=>c.personId),keys=()=>get('inbox-cards').children.map(c=>c.groupKey);
(async()=>{
 ui.boot();await flush();
 assert(!calls.some(c=>c.url.includes('/persons/collections')),'collapsed collections do not load on entry');
 assert(!calls.some(c=>c.url.includes('/faces/source-detail-preview')||c.url==='/api/v1/sources'),'unused rematch preview and source list are deferred');
 assert(calls.find(c=>c.url.includes('/persons/browse?')).url.includes('sort=photos_recent_desc'));
 await ui.setView(true);await flush();
 assert(calls.some(c=>c.url.includes('/faces/source-detail-batches/active')),'current active rematch status remains visible');
 summaryGate=new Promise(resolve=>{releaseSummary=resolve;});
 await ui.loadInbox(false);
 assert(get('inbox-cards').children.length>0,'candidate cards render while summary is still pending');
 assert.equal(get('inbox-note').textContent,'统计中……');
 releaseSummary({ok:false,status:503,json:async()=>({code:'TEMPORARY'})});await flush();await flush();
 assert.equal(get('inbox-note').textContent,'统计暂不可用。');
 assert(get('inbox-cards').children.length>0,'summary failure preserves loaded candidate cards');
 summaryGate=null;
 const collectionCalls=calls.filter(c=>c.url.includes('/persons/collections')).length;
 assert(await ui.ensureAllCollectionsLoaded());
 assert.equal(calls.filter(c=>c.url.includes('/persons/collections')).length-collectionCalls,2,'batch chooser loads every collection page on demand');
 assert.equal(get('collection-choice').children.length,46);
 ui.state.selectedPeople.add(1);ui.state.batchMode=true;ui.renderPeopleManagement();get('collection-choice').value='1';
 await ui.addSelectionToCollection();
 assert(calls.some(c=>c.url.includes('/persons/collections/1/token0/members')),'batch add remains connected to the selected collection after lazy loading');
 ui.state.batchMode=false;ui.renderPeopleManagement();
 ui.state.organize=true;get('inbox-kind').value='all';
 await ui.loadPeople(false);await ui.loadPeople(true);await ui.loadPeople(true);
 await ui.loadInbox(false);await ui.loadInbox(true);await ui.loadInbox(true);
 assert.deepEqual(ids(),[1,2,3]);assert.equal(ui.state.peoplePages,3);assert.equal(ui.state.inboxPages,3);
 for(const g of get('inbox-cards').children.slice(1)){g.open=true;g.fire('toggle');await flush();await g.children[2].fire('click');}
 const group22=get('inbox-cards').children[1],group33=get('inbox-cards').children[2];
 assert.equal(group22.children[1].children.length,4);assert.equal(group33.children[1].children.length,4);
 // Refresh reconciles cards by person id and keeps the loaded image object.
 const originalCard=get('cards').children[0],originalImage=originalCard.avatarImage;
 people=[people[1],{...people[0],name:'最新人物名',revision:9,asset_count:42,collections:[{collection_id:4,name:'旅行'}]},...people.slice(2)];
 await ui.loadPeople(false);assert.deepEqual(ids(),[2,1,3]);
 const refreshed=get('cards').children[1];assert.equal(refreshed,originalCard);assert.equal(refreshed.avatarImage,originalImage);
 assert.equal(refreshed.personName.textContent,'最新人物名');assert.equal(refreshed.personCount.textContent,'42 张照片');
 assert.equal(refreshed.collectionLabels.children[0].textContent,'旅行');
 refreshed.fire('click');await flush();assert.equal(ui.state.person.revision,9,'click reads the refreshed person object');
 const browseBeforeReturn=calls.filter(c=>c.url.includes('/persons/browse?')).length;
 ui.backToLibrary();await flush();ui.backToLibrary();await flush();
 assert.equal(calls.filter(c=>c.url.includes('/persons/browse?')).length,browseBeforeReturn,'two fresh returns do not refetch people');
 const imageBeforeRevalidate=refreshed.avatarImage;await ui.loadPeople(false);
 assert.equal(get('cards').children[1].avatarImage,imageBeforeRevalidate,'explicit refresh revalidates without replacing the img element');
 const browseBeforeExpiry=calls.filter(c=>c.url.includes('/persons/browse?')).length;
 ui.state.peopleLastLoadedAt-=61000;ui.refreshChangedPeople();await flush();await flush();await flush();
 assert(calls.filter(c=>c.url.includes('/persons/browse?')).length>browseBeforeExpiry,'stale list is checked on focus without a token change');
 const beforeAvatarChange=get('cards').children[1].avatarImage,changedFace='f'.repeat(64);
 people=people.map(p=>p.person_id===1?{...p,avatar_face_id:changedFace,thumbnail_url:`/api/v1/faces/${changedFace}/thumbnail`}:p);
 await ui.loadPeople(false);assert.notEqual(get('cards').children[1].avatarImage,beforeAvatarChange,'a changed avatar identity replaces the image');
 const queryBeforeRepeat=get('query').value;ui.state.selectedPeople.add(1);const browseBeforeMode=calls.filter(c=>c.url.includes('/persons/browse?')).length;
 await ui.setView(true);assert.equal(get('query').value,queryBeforeRepeat);assert(ui.state.selectedPeople.has(1));
 assert.equal(calls.filter(c=>c.url.includes('/persons/browse?')).length,browseBeforeMode,'same mode click preserves state and does not reload');
 ui.state.selectedPeople.clear();people=[people[1],people[0],...people.slice(2)];await ui.loadPeople(false);
 context.window.scrollY=1900;const atConfirm=calls.length;
 await group33.children[1].children[0].confirmButton.fire('click');assert.equal(calls.length,atConfirm+1);
 groups=groups.filter(id=>id!==11);group33.open=false;group33.fire('toggle');await flush();await flush();await flush();
 assert.deepEqual(keys(),['candidate:22','candidate:33','candidate:44'],'collapse restores all three loaded outer pages');
 assert.deepEqual(ids(),[1,2,3]);assert.equal(context.window.scrollY,1800,'surviving visible card stays at the same viewport offset');
 const reopened=get('inbox-cards').children[0];assert.equal(reopened.open,true);
 assert.equal(reopened.children[1].children.length,4,'other expanded group retains both loaded face pages');
 assert.equal(ui.state.inboxCursor,null);assert.equal(ui.state.listCursor,revision+':3');
 await ui.loadPeople(true);assert.deepEqual(ids(),[1,2,3,4],'load more continues with refreshed cursors');
 // Naming from a later unnamed card refreshes its loaded extent and preserves album return scroll.
 context.window.scrollY=1850;get('cards').children[2].fire('click');await flush();
 assert.equal(ui.state.libraryPosition.y,1850);assert.equal(get('library').hidden,true);
 people=people.filter(p=>p.person_id!==3);revision++;
 await ui.refreshAfterWrite({changed:true,operation_id:'rename',operation_revision:1},'已命名');await flush();await flush();
 assert.deepEqual(ids(),[1,2,4]);assert.equal(get('library').hidden,true);
 context.window.scrollY=0;ui.backToLibrary();await flush();await flush();await flush();
 assert.equal(get('library').hidden,false);assert.equal(context.window.scrollY,1850);
 assert.equal(get('people').focusOptions.preventScroll,true);assert.deepEqual(ids(),[1,2,4]);
  people.push({person_id:3,name:null,revision:3,asset_count:5,thumbnail_url:null});revision++;
  await ui.refreshPeopleViews();assert.equal(ids().length,4,'undo can refill the previously loaded extent after temporary exhaustion');
 // Search/quality/kind changes intentionally start a new first page.
 get('query').value='new';const queryStart=calls.length;await ui.loadPeople(false);
 assert.equal(ui.state.peoplePages,1);assert.equal(ids().length,1);
 assert(!calls.slice(queryStart).some(c=>c.url.includes('cursor=')));
 get('query').value='';await ui.loadPeople(false);await ui.loadPeople(true);
 ui.state.qualityView='all';await ui.loadPeople(false);assert.equal(ui.state.peoplePages,1);
 get('inbox-kind').value='quality';await ui.loadInbox(false);await ui.loadInbox(true);
 assert.equal(ui.state.inboxPages,2);revision++;await ui.loadInbox(false);
 assert.equal(get('inbox-cards').children.length,4,'flat reason lists retain loaded pages too');
 get('inbox-kind').value='all';await ui.loadInbox(false);assert.equal(ui.state.inboxPages,1);
 // A failed later page must leave the complete previously rendered list intact.
  await ui.loadPeople(false);await ui.loadPeople(true);const oldCards=get('cards').children.slice();failSecond=true;revision++;
 await ui.loadPeople(false);assert.equal(get('cards').children.length,2);assert.equal(get('cards').children[0],oldCards[0]);
 failSecond=false;await ui.loadPeople(false);assert.equal(get('cards').children.length,2);
 // An old request cannot overwrite a newer query or its scroll position.
 let resolve;delayed=new Promise(r=>{resolve=r;});get('query').value='slow';const pending=ui.loadPeople(false);
 get('query').value='fast';await ui.loadPeople(false);context.window.scrollY=321;
 resolve(ok({persons:[{person_id:999,name:'stale',asset_count:1}],has_more:false}));await pending;
 assert(!ids().includes(999));assert.equal(context.window.scrollY,321);
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", script], cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True, encoding="utf-8", timeout=15,
    )
    assert result.returncode == 0, result.stderr
