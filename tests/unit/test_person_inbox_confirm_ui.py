"""Exercise saved confirmation ticks and refresh after closing candidate groups."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_candidate_confirmation_stays_in_place_until_collapse() -> None:
    node = shutil.which("node")
    assert node, "Node is required for the personal-page behavior check"
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.value='';this.hidden=false;this.disabled=false;
  this.text='';this.events={};this.attributes={};this.dataset={};this.open=false;this.classList={add(){}};}
 set textContent(v){this.text=String(v);this.children=[];}get textContent(){return this.text+this.children.map(c=>c.textContent).join('');}
 append(...items){this.children.push(...items);}replaceChildren(...items){this.text='';this.children=items;}
 setAttribute(k,v){this.attributes[k]=v;}addEventListener(n,fn){this.events[n]=fn;}fire(n){return this.events[n]?.();}
 querySelector(s){return this.children.find(c=>String(c.className||'').split(' ').includes(s.slice(1)));}
 querySelectorAll(){return [];}focus(){}showModal(){this.open=true;}close(){this.open=false;}
}
const elements=new Map(),get=id=>{if(!id.startsWith('pa-'))id='pa-'+id;if(!elements.has(id))elements.set(id,new Element('div'));return elements.get(id);};
const calls=[],saved=new Set(),storage=new Map();let uuid=0,fail=null,release=null,delay=false;
const candidate={person_id:17,name:'妈妈',thumbnail_url:'/api/v1/faces/'+ '1'.repeat(64) + '/thumbnail'},other={person_id:18,name:'爸爸',thumbnail_url:'/api/v1/faces/'+ '2'.repeat(64) + '/thumbnail'};
const face=(id,reason,person=candidate)=>({face_id:id.repeat(64),asset_id:id.charCodeAt(0),thumbnail_url:null,
 control_revision:4,organization_reason:reason,mode:'AUTO_ALLOWED',suggestions:[person]});
const faces=[face('a','NEAR_TIE'),face('b','SAME_IMAGE_CONFLICT'),face('c','OTHER_SUGGESTION',other)];
const group=(p,f)=>({type:'candidate_group',group_key:'candidate:'+p.person_id,candidate_name:p.name,photo_count:2,face:f});
const ok=value=>({ok:true,json:async()=>value}),flush=()=>new Promise(setImmediate);
const context={URLSearchParams,URL,crypto:{randomUUID:()=>String(++uuid)},setTimeout:()=>1,clearTimeout(){},
 window:{location:{href:'http://local/persons',search:''},addEventListener(){},localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)}},
 document:{readyState:'loading',cookie:'ps_csrf=test',addEventListener(){},getElementById:get,createElement:t=>new Element(t)},
 fetch:async(url,options={})=>{calls.push({url,options});
  if(url.endsWith('/assignment')){
   if(fail==='network')throw new Error('lost response');
   if(fail)return {ok:false,status:409,json:async()=>({code:'FACE_ASSIGNMENT_CONFLICT'})};
   if(delay)await new Promise(resolve=>{release=resolve;});
   saved.add(url.split('/')[4]);return ok({changed:true,operation_id:'op-'+uuid,operation_revision:1});
  }
  if(url.includes('/person-operations/')){saved.clear();return ok({changed:true});}
  if(url.includes('/faces/inbox/groups/candidate%3A')){
   const id=url.includes('candidate%3A17')?17:18;
   return ok({faces:faces.filter(f=>f.suggestions[0].person_id===id&&!saved.has(f.face_id)),has_more:id===17,next_cursor:'next'});
  }
  if(url.includes('/faces/inbox/groups?'))return ok({cards:[group(candidate,faces[0]),group(other,faces[2])],has_more:false});
  if(url.includes('/faces/inbox/summary?'))return ok({total:3-saved.size,reasons:{},held:0});
  if(url.includes('/persons/browse?'))return ok({persons:[other],has_more:false,total:1});
  if(url.includes('/persons/collections?'))return ok({collections:[],has_more:false});
  return ok({faces:[],cards:[],has_more:false});
 }};
let code=fs.readFileSync('src/photo_search/web/persons-albums.js','utf8');
code=code.replace('  if (document.readyState === "loading")','  globalThis.ui={state,renderInboxFace,renderInboxGroup,loadInbox,undo,submitEdit};\n  if (document.readyState === "loading")');
vm.createContext(context);vm.runInContext(code,context);const ui=context.ui;
const writes=()=>calls.filter(c=>c.url.endsWith('/assignment'));
const descendants=e=>[e,...e.children.flatMap(descendants)];
(async()=>{
 // Group reference avatars follow the candidate id in the group key, even when
 // the representative face ranks a different person first.
 const representative={...faces[0],suggestions:[other,candidate]};
 let host=new Element('div');
 let details=ui.renderInboxGroup(group(candidate,representative),'all',false);
 let comparison=details.children[0].children[0];
 assert(descendants(comparison.children[1]).some(e=>e.src==='/api/v1/faces/'+ '1'.repeat(64) + '/thumbnail?v=person-media2'));
 const mismatch=ui.renderInboxGroup(group({person_id:99,name:'错位'},representative),'all',false);
 comparison=mismatch.children[0].children[0];
 assert.equal(comparison.children[1].children[0].children[0].textContent,'暂无参考头像');
 const noAvatar=ui.renderInboxGroup(group(candidate,{...representative,suggestions:[{...candidate,thumbnail_url:null}]}),'all',false);
 assert.equal(noAvatar.children[0].children[0].children[1].children[0].children[0].textContent,'暂无参考头像');
 // Face comparison, visible label, and confirmation action share one candidate.
 host=new Element('div');ui.renderInboxFace(host,{...faces[0],suggestions:[candidate,other]},false);
 let card=host.children[0];comparison=card.children[0];
 assert.equal(comparison.children[1].children[1].textContent,'可能是妈妈');
 assert(descendants(comparison.children[1]).some(e=>e.src===candidate.thumbnail_url+'?v=person-media2'));
 assert.equal(card.confirmButton.textContent,'确认是妈妈');
 host=new Element('div');ui.renderInboxFace(host,{...faces[0],suggestions:[{...candidate,thumbnail_url:null}]},false);
 assert.equal(host.children[0].children[0].children[1].children[0].children[0].textContent,'暂无参考头像');
 assert.equal(host.children[0].confirmButton.textContent,'确认是妈妈','missing reference image does not disable the action');
 host=new Element('div');ui.renderInboxFace(host,{...faces[0],suggestions:[]},false);
 assert.equal(host.children[0].children[0].children.length,1,'no candidate retains the single-face display');
 host=new Element('div');ui.renderInboxFace(host,faces[0],true);
 card=host.children[0];comparison=card.children[0];
 assert.equal(comparison.children.length,2,'ignored faces show the existing candidate comparison');
 assert.equal(comparison.children[1].children[1].textContent,'可能是妈妈');
 assert.equal(card.confirmButton.textContent,'确认是妈妈','ignored candidate can use the existing confirmation flow');
 assert.equal(card.otherPersonButton.textContent,'选其他人','ignored candidate can open the existing person picker');
 await card.otherPersonButton.fire('click');await flush();
 assert.equal(get('edit').open,true,'other-person action opens the existing assignment dialog');
 assert.equal(get('edit-label').textContent,'搜索并选择人物');
 assert(calls.some(c=>c.url.includes('/persons/browse?kind=all')),'the existing dialog loads searchable people');
 get('edit').close();
 host=new Element('div');ui.renderInboxFace(host,{...faces[0],suggestions:[]},true);
 assert.equal(host.children[0].children[3].textContent,'选择人物','ignored face without a suggestion still has a manual assignment entry');
 // The quick action depends on an actual candidate, including quality reasons and ties.
 for(const reason of ['BORDERLINE','NEAR_TIE','SAME_IMAGE_CONFLICT','OTHER_SUGGESTION','SMALL','LOW_QUALITY','MANUAL_UNASSIGNED']){
  const host=new Element('div');ui.renderInboxFace(host,face('e',reason),false);
  assert.equal(host.children[0].confirmButton.textContent,'确认是妈妈',reason);
 }
 for(const f of [{...face('e','MANUAL_UNASSIGNED'),mode:'MANUAL_UNASSIGNED',suggestions:[]},
                 {...face('f','NO_MATCH'),suggestions:[]}]){
  const host=new Element('div');ui.renderInboxFace(host,f,false);assert(!host.children[0].confirmButton);
 }
 ui.state.batchMode=true;host=new Element('div');ui.renderInboxFace(host,faces[0],true);
 assert(!host.children[0].confirmButton,'batch selection keeps its dedicated controls');
 ui.state.batchMode=false;ui.state.organize=true;get('inbox-kind').value='all';await ui.loadInbox(false);
 const groups=get('inbox-cards').children.slice();
 for(const g of groups){g.open=true;g.fire('toggle');}await flush();
 const first=groups[0].children[1].children[0],second=groups[0].children[1].children[1];
 const baseline=calls.length;delay=true;const saving=first.confirmButton.fire('click');await flush();
 assert.equal(first.confirmButton.disabled,true);assert(!first.confirmButton.textContent.includes('✓'));
 await first.confirmButton.fire('click');assert.equal(writes().length,1,'inflight clicks cannot submit twice');
 delay=false;release();await saving;
 assert.equal(first.confirmButton.textContent,'✓ 已确认是妈妈');assert.equal(first.confirmButton.attributes['aria-pressed'],'true');
 assert.equal(first.otherPersonButton.disabled,true);assert.equal(get('inbox-cards').children[0],groups[0]);
 assert.equal(groups[0].children[1].children[0],first);assert.equal(calls.length,baseline+1,'confirmation triggers no list GETs');
 assert.equal(storage.has('aisearch.persons.changed'),true);assert(ui.state.operation,'undo is retained');
 assert.equal(get('inbox-more').disabled,true,'outer stale cursor waits for collapse');
 await ui.loadInbox(true);assert.equal(calls.length,baseline+1);
 const request=writes()[0],body=JSON.parse(request.options.body);
 assert.equal(request.options.method,'PUT');assert.equal(request.options.headers['X-CSRF-Token'],'test');
 assert.equal(body.target_person_id,17);assert.equal(body.expected_revision,4);assert.equal(body.action,'assign');
 assert.equal(body.contract_version,'p3-face-correction-contracts.v1');
 await first.confirmButton.fire('click');assert.equal(writes().length,1,'confirmed card cannot submit twice');
 // Restart invalidated paging without discarding ticks or duplicating retained cards.
 await groups[0].children[2].fire('click');
 assert(!calls.at(-1).url.includes('cursor='));assert.equal(groups[0].children[1].children.length,2);
 assert.equal(groups[0].children[1].children[0],first);
 fail='conflict';await second.confirmButton.fire('click');
 assert.equal(second.confirmButton.disabled,false);assert.equal(second.confirmButton.textContent,'确认是妈妈');
 assert.equal(second.confirmButton.attributes['aria-pressed'],'false');assert(!second.confirmed);
 fail='network';await second.confirmButton.fire('click');const retryId=JSON.parse(writes().at(-1).options.body).request_id;
 fail=null;await second.confirmButton.fire('click');
 assert.equal(JSON.parse(writes().at(-1).options.body).request_id,retryId,'unknown result reuses its write intent');
 assert(second.confirmButton.textContent.includes('✓'));assert.equal(groups[0].children[1].children.length,2);
 await groups[1].children[1].children[0].confirmButton.fire('click');
 const beforeClose=calls.length;groups[0].open=false;groups[0].fire('toggle');await flush();
 assert.equal(calls.length,beforeClose,'another open confirmed group is not interrupted');
 groups[1].open=false;groups[1].fire('toggle');await flush();await flush();
 assert(calls.slice(beforeClose).some(c=>c.url.includes('/faces/inbox/groups?')),'collapse refreshes the inbox');
 assert(calls.slice(beforeClose).some(c=>c.url.includes('/persons/browse?')),'collapse updates person counts');
 assert.equal(ui.state.inboxConfirmationsPending,false);
 await ui.undo();await flush();
 const restored=get('inbox-cards').children[0];restored.open=true;restored.fire('toggle');await flush();
 assert.equal(restored.children[1].children[0].confirmButton.disabled,false,'undo gets fresh unconfirmed controls');
 // A collapse while saving must wait until the authoritative save succeeds.
 delay=true;const last=restored.children[1].children[0].confirmButton.fire('click');await flush();
 const whileSaving=calls.length;restored.open=false;restored.fire('toggle');await flush();
 assert.equal(calls.length,whileSaving);delay=false;release();await last;await flush();
 assert(calls.slice(whileSaving).some(c=>c.url.includes('/faces/inbox/groups?')),'closed group refreshes after its save resolves');
 // Flat reason filters retain the tick until an explicit refresh/view change.
 const flatHost=new Element('div');ui.renderInboxFace(flatHost,face('z','LOW_QUALITY'),false);
 const flat=flatHost.children[0],flatBefore=calls.length;await flat.confirmButton.fire('click');
 assert(flat.confirmButton.textContent.includes('✓'));assert.equal(calls.length,flatBefore+1);
 await ui.loadInbox(false);assert.equal(ui.state.inboxConfirmationsPending,false);
 ui.state.inboxHiddenVisible=true;
 const hiddenHost=new Element('div');ui.renderInboxFace(hiddenHost,faces[0],true);
 const hidden=hiddenHost.children[0],hiddenBefore=calls.length;await hidden.confirmButton.fire('click');await flush();await flush();
 assert(hidden.confirmButton.textContent.includes('✓'));
 assert(calls.slice(hiddenBefore).some(c=>c.url.includes('/faces/inbox/hidden/groups?')),'ignored confirmation refreshes its candidate list');
 assert.equal(ui.state.inboxConfirmationsPending,false,'ignored confirmation does not block the visible inbox cursor');
 const manualHost=new Element('div');ui.renderInboxFace(manualHost,{...faces[0],face_id:'d'.repeat(64),suggestions:[]},true);
 await manualHost.children[0].children[3].fire('click');await flush();
 const choice=get('choices').children.find(e=>String(e.className||'').includes('pa-choice'));
 assert(choice,'manual assignment offers an existing person with its avatar');choice.fire('click');
 assert.equal(get('edit-submit').disabled,false);ui.submitEdit({preventDefault(){}});await flush();
 assert.equal(JSON.parse(writes().at(-1).options.body).target_person_id,18,'manual ignored-face assignment uses the selected person');
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", script], cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True, encoding="utf-8", timeout=15,
    )
    assert result.returncode == 0, result.stderr
