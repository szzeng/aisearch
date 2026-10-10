"""Actual JS regression for retained pages, fresh cursors and library return position."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_people_and_inbox_refresh_preserve_loaded_extent_and_position() -> None:
    node = shutil.which("node")
    assert node, "Node is required for the personal-page behavior check"
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.value='';this.text='';this.hidden=false;this.disabled=false;
  this.events={};this.attributes={};this.dataset={};this.open=false;this.classList={add(){}};}
 set textContent(v){this.text=String(v);this.children=[];}get textContent(){return this.text+this.children.map(c=>c.textContent).join('');}
 append(...items){items.forEach(c=>{c.parent=this;this.children.push(c);});}
 replaceChildren(...items){this.children=[];this.text='';this.append(...items);}
 setAttribute(k,v){this.attributes[k]=v;}removeAttribute(k){delete this.attributes[k];}
 addEventListener(n,fn){this.events[n]=fn;}fire(n){return this.events[n]?.();}
 querySelector(s){return this.children.find(c=>String(c.className||'').split(' ').includes(s.slice(1)));}
 querySelectorAll(){return [];}focus(options){this.focusOptions=options;}
 showModal(){this.open=true;}close(){this.open=false;}
 getBoundingClientRect(){const index=this.parent?.children.indexOf(this)||0;
  const top=(this.parent===get('inbox-cards')?1800:0)+index*100-context.window.scrollY;return {top,bottom:top+100};}
}
const elements=new Map(),get=id=>{if(!id.startsWith('pa-'))id='pa-'+id;if(!elements.has(id))elements.set(id,new Element('div'));return elements.get(id);};
const calls=[];let revision=1,uuid=0,failSecond=false,delayed=null;
let people=[1,2,3,4].map(id=>({person_id:id,name:null,revision:1,asset_count:5,thumbnail_url:null}));
let groups=[11,22,33,44];const groupFaces=new Map(groups.map(id=>[id,Array.from({length:5},(_,i)=>({
 face_id:(id*10+i).toString(16).padStart(64,'0'),asset_id:id*10+i,control_revision:0,thumbnail_url:null,
 organization_reason:'NEAR_TIE',suggestions:[{person_id:id,name:'候选'+id}]}))]));
const ok=v=>({ok:true,json:async()=>v}),flush=()=>new Promise(setImmediate);
function page(items,params,key,cap=1){let index=0;
 if(params.has('cursor')){const [v,n]=params.get('cursor').split(':');assert.equal(Number(v),revision,'never reuse pre-write cursors');index=Number(n);}
 const has_more=index+cap<items.length;return {[key]:items.slice(index,index+cap),total:items.length,has_more,
  next_cursor:has_more?revision+':'+(index+cap):null};}
const context={URLSearchParams,URL,crypto:{randomUUID:()=>String(++uuid)},setTimeout:()=>1,clearTimeout(){},
 window:{scrollY:0,scrollBy(x,y){this.scrollY+=y;},scrollTo(x,y){this.scrollY=y;},addEventListener(){},
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
  if(url.includes('/faces/inbox/summary?'))return ok({total:20,reasons:{},held:0});
  if(url.includes('/faces/inbox?'))return ok(page(groupFaces.get(22),u.searchParams,'faces',2));
  if(url.includes('/persons/collections?'))return ok({collections:[],has_more:false});
  if(url==='/api/v1/persons/3')return ok({...people[0],person_id:3,name:'已命名'});
  if(url.includes('/persons/3/photos?'))return ok({assets:[],has_more:false});
  return ok({faces:[],cards:[],has_more:false});
 }};
let code=fs.readFileSync('src/photo_search/web/persons-albums.js','utf8');
code=code.replace('  if (document.readyState === "loading")','  globalThis.ui={state,loadPeople,loadInbox,refreshPeopleViews,refreshAfterWrite,backToLibrary};\n  if (document.readyState === "loading")');
vm.createContext(context);vm.runInContext(code,context);const ui=context.ui;
const ids=()=>get('cards').children.map(c=>c.personId),keys=()=>get('inbox-cards').children.map(c=>c.groupKey);
(async()=>{
 ui.state.organize=true;get('inbox-kind').value='all';
 await ui.loadPeople(false);await ui.loadPeople(true);await ui.loadPeople(true);
 await ui.loadInbox(false);await ui.loadInbox(true);await ui.loadInbox(true);
 assert.deepEqual(ids(),[1,2,3]);assert.equal(ui.state.peoplePages,3);assert.equal(ui.state.inboxPages,3);
 for(const g of get('inbox-cards').children.slice(1)){g.open=true;g.fire('toggle');await flush();await g.children[2].fire('click');}
 const group22=get('inbox-cards').children[1],group33=get('inbox-cards').children[2];
 assert.equal(group22.children[1].children.length,4);assert.equal(group33.children[1].children.length,4);
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
