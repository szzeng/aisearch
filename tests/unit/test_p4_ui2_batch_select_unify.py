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
 fire(name,event={}){let prevented=false;const dispatched={...event,preventDefault(){prevented=true;},get defaultPrevented(){return prevented;},stopPropagation(){}};this.events[name](dispatched);return dispatched;}
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
    html = (ROOT / "src/photo_search/web/persons.html").read_text(encoding="utf-8")
    assert "<summary>合集整理</summary>" not in html
    toolbar = html.split('id="pa-collection-view-controls"', 1)[1].split('id="pa-collection-people-actions"', 1)[0]
    assert 'id="pa-collection-rename"' in toolbar and 'id="pa-collection-delete"' in toolbar
    _run_node(r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
class Element {
 constructor(tag){this.tagName=tag.toUpperCase();this.tag=tag;this.children=[];this.events={};this.attributes={};this.className='';this.classList={add(){}};this.hidden=false;this.disabled=false;this.checked=false;this.value='';this.parentNode=null;this.text='';}
 set textContent(value){this.text=String(value);this.children.forEach(x=>x.parentNode=null);this.children=[];}
 get textContent(){return this.text+this.children.map(x=>x.textContent).join('');}
 appendChild(item){item.parentNode=this;this.children.push(item);return item;} append(...items){items.forEach(x=>this.appendChild(x));}
 replaceChildren(...items){this.children.forEach(x=>x.parentNode=null);this.text='';this.children=[];this.append(...items);}
 setAttribute(k,v){this.attributes[k]=String(v);} addEventListener(k,fn){this.events[k]=fn;}
 fire(name,event={}){let prevented=false;const dispatched={...event,preventDefault(){prevented=true;},get defaultPrevented(){return prevented;},stopPropagation(){}};this.events[name](dispatched);return dispatched;} click(){return this.fire('click');}
}
const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element('div'));return elements.get(id);},requests=[];
const context={crypto:{randomUUID:()=> 'req'},URLSearchParams,Date,Map,Set,WeakSet,document:{readyState:'loading',cookie:'ps_csrf=csrf',addEventListener(){},getElementById:get,createElement:t=>new Element(t)},window:{crypto:{randomUUID:()=> 'req'},location:{search:'',href:'http://local/persons'},addEventListener(){},confirm:()=>true},fetch:async(url,options={})=>{requests.push({url,options});return {ok:true,status:200,json:async()=>({collection:{collection_id:7,instance_token:'a'.repeat(32),revision:1},people:Array.from({length:8},(_,i)=>({person_id:11+i,name:'人'+(11+i),asset_count:1,thumbnail_url:null})),cards:[],faces:[],has_more:false,next_cursor:null})};},setTimeout:()=>1,clearTimeout(){}};
let source=fs.readFileSync('src/photo_search/web/persons-albums.js','utf8');
source=source.replace('  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();','  globalThis.__probe={state,loadCollectionPeople,updateCollectionSelection,selectLoadedCollectionPeople,clearCollectionSelection,removeCollectionSelection,init(collection){state.collection=collection;state.collectionMode="people";}};\n  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();');
vm.createContext(context);vm.runInContext(source,context);
const ui=context.__probe,collection={collection_id:7,instance_token:'a'.repeat(32),revision:1};ui.init(collection);
const deferred=[];context.setTimeout=fn=>{deferred.push(fn);return deferred.length;};
const clickBox=(box,event={})=>{const before=box.checked;box.checked=!before;const dispatched=box.fire('click',event);if(dispatched.defaultPrevented)box.checked=before;else box.fire('change');deferred.splice(0).forEach(fn=>fn());return dispatched;};
(async()=>{
 await ui.loadCollectionPeople(false);
 const cards=get('pa-collection-people').children;assert.equal(cards.length,8);
 const beforeSelection=requests.length;
 const checkbox=i=>cards[i].selectionBox;
 assert.equal(checkbox(0).type,'checkbox');assert.match(checkbox(0).attributes['aria-label'],/移出合集/);
 assert.equal(checkbox(0).hidden,true,'collection checkboxes are hidden during normal browsing');
 assert.equal(get('pa-collection-remove').hidden,true,'remove controls are hidden before entering management');
 assert.equal(cards[0].className,'pa-person-card','collection member uses the normal people-card root');
 assert.equal(cards[0].attributes.role,'button');assert.equal(cards[0].tabIndex,0);
 assert.equal(cards[0].children[0].className,'pa-picture pa-avatar');
 assert.equal(cards[0].children[3].className,'pa-person-collections','collection member matches the normal people-card structure');
 assert.equal(checkbox(0).className,'pt-person-checkbox');
 ui.state.collectionPeopleManage=true;ui.updateCollectionSelection();
 clickBox(checkbox(1));clickBox(checkbox(4),{shiftKey:true});
 assert.deepEqual(Array.from(ui.state.collectionSelectedPeople),[12,13,14,15]);
 assert.equal(cards[2].attributes['data-selected'],'true');assert.match(get('pa-collection-people-selected').textContent,/已选 4 人/);
 ui.state.collectionSelectedPeople=new Set([11,12,13,14]);ui.selectLoadedCollectionPeople(true);
 assert.deepEqual(Array.from(ui.state.collectionSelectedPeople),[15,16,17,18],'invert selects the unselected loaded members and clears the selected ones');
 ui.state.collectionSelectedPeople.add(99);ui.selectLoadedCollectionPeople(true);
 assert.deepEqual(Array.from(ui.state.collectionSelectedPeople),[99,11,12,13,14],'invert preserves selections outside the loaded page');
 ui.selectLoadedCollectionPeople(false);assert.equal(ui.state.collectionSelectedPeople.size,9);
 ui.selectLoadedCollectionPeople(true);assert.deepEqual(Array.from(ui.state.collectionSelectedPeople),[99]);
 ui.clearCollectionSelection();assert.equal(ui.state.collectionSelectedPeople.size,0);
 clickBox(checkbox(0));assert.deepEqual(Array.from(ui.state.collectionSelectedPeople),[11]);assert.equal(checkbox(0).checked,true,'normal checkbox activation stays synchronized with selection');
 ui.clearCollectionSelection();
 ui.state.writing=true;ui.updateCollectionSelection();assert.equal(checkbox(0).disabled,true);
 clickBox(checkbox(0));assert.equal(ui.state.collectionSelectedPeople.size,0,'busy state blocks edits');
 ui.state.writing=false;
 assert.equal(requests.length,beforeSelection,'selection operations do not issue API requests');
})().catch(error=>{console.error(error);process.exitCode=1;});
""")


def test_inbox_faces_have_independent_management_and_cross_group_shift() -> None:
    _run_node(r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
class Element {
 constructor(tag){this.tagName=tag.toUpperCase();this.tag=tag;this.children=[];this.events={};this.attributes={};this.className='';this.classList={add(){}};this.hidden=false;this.disabled=false;this.checked=false;this.open=false;this.value='';this.parentNode=null;this.text='';}
 set textContent(value){this.text=String(value);this.children.forEach(x=>x.parentNode=null);this.children=[];}
 get textContent(){return this.text+this.children.map(x=>x.textContent).join('');}
 appendChild(item){item.parentNode=this;this.children.push(item);return item;} append(...items){items.forEach(x=>this.appendChild(x));}
 replaceChildren(...items){this.children.forEach(x=>x.parentNode=null);this.text='';this.children=[];this.append(...items);}
 setAttribute(k,v){this.attributes[k]=String(v);} addEventListener(k,fn){this.events[k]=fn;}
 fire(name,event={}){let prevented=false;const dispatched={...event,preventDefault(){prevented=true;},get defaultPrevented(){return prevented;},stopPropagation(){}};this.events[name](dispatched);return dispatched;}
}
const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element('div'));return elements.get(id);},writes=[];
const context={URLSearchParams,Date,Map,Set,WeakSet,document:{readyState:'loading',cookie:'ps_csrf=csrf',addEventListener(){},getElementById:get,createElement:t=>new Element(t)},window:{location:{search:'',href:'http://local/persons'},addEventListener(){},confirm:()=>true},fetch:async(url,options={})=>{if(options.method&&options.method!=='GET'){writes.push({url,body:JSON.parse(options.body)});return {ok:true,status:200,json:async()=>({changed:writes.at(-1).body.face_ids.length})};}return {ok:true,status:200,json:async()=>({cards:[],faces:[],has_more:false,next_cursor:null})};},setTimeout:()=>1,clearTimeout(){}};
let source=fs.readFileSync('src/photo_search/web/persons-albums.js','utf8');
source=source.replace('  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();','  globalThis.__probe={state,renderInboxFace,renderInboxGroup,renderFaceManagement,loadedInboxCards,selectInboxRange,clearInboxSelection,submitInboxVisibility,setPreview(fn){preview=fn;}};\n  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();');
vm.createContext(context);vm.runInContext(source,context);
const ui=context.__probe,face=id=>({face_id:id,asset_id:id,thumbnail_url:null,suggestions:[{person_id:1,name:'参考人物',thumbnail_url:null}],control_revision:1});
// This test owns selection; the shared viewer has its own modal/navigation tests.
const previews=[];ui.setPreview((assetId,returnFocus)=>previews.push({assetId,returnFocus}));
const deferred=[];context.setTimeout=fn=>{deferred.push(fn);return deferred.length;};
const clickBox=(box,event={})=>{const before=box.checked;box.checked=!before;const dispatched=box.fire('click',event);if(dispatched.defaultPrevented)box.checked=before;else box.fire('change');deferred.splice(0).forEach(fn=>fn());return dispatched;};
(async()=>{
 ui.state.inboxBatchMode=true;ui.renderFaceManagement();
 const inbox=get('pa-inbox-cards'),g1=new Element('details'),g2=new Element('details');g1.open=true;g2.open=true;g1.groupKey='candidate:a';g2.groupKey='candidate:b';inbox.append(g1,g2);
 ui.renderInboxFace(g1,face(101),false,g1);ui.renderInboxFace(g1,face(102),false,g1);ui.renderInboxFace(g2,face(103),false,g2);
 const htmlCollection=items=>Object.assign({length:items.length,[Symbol.iterator]:function*(){yield* items;}},items);
 g1.children=htmlCollection(g1.children);g2.children=htmlCollection(g2.children);
 assert.equal(ui.loadedInboxCards(false).length,3,'visible face traversal supports DOM HTMLCollection children');
 const boxes=[g1.children[0].selectionButton,g1.children[1].selectionButton,g2.children[0].selectionButton];
 assert.equal(boxes[0].hidden,false,'the inbox-specific management mode reveals checkboxes');
 const faceCard=g1.children[0],avatar=faceCard.children[0].children[0].children[0];
 ui.state.selectedInboxFaces.add('102');ui.state.selectAnchors.set('faces:inbox:list','102');
 let stopped=false;avatar.events.click({target:avatar.children[0],currentTarget:avatar,stopPropagation(){stopped=true;},preventDefault(){}});
 assert.equal(stopped,true,'opening a face photo stops bubbling into card selection');
 assert.equal(previews.length,1);assert.equal(previews[0].assetId,101);assert.equal(previews[0].returnFocus,avatar);
 faceCard.fire('click',{target:avatar.children[0],shiftKey:true});
 assert.deepEqual(Array.from(ui.state.selectedInboxFaces),['102'],'avatar preview and the parent guard preserve the selection even in batch mode');
 assert.equal(ui.state.selectAnchors.get('faces:inbox:list'),'102','avatar preview does not move the range anchor');
 ui.state.writing=true;avatar.events.click({target:avatar.children[0],currentTarget:avatar,stopPropagation(){},preventDefault(){}});
 assert.equal(previews.length,1,'a pending write blocks avatar navigation');ui.state.writing=false;
 ui.state.selectedInboxFaces.clear();ui.state.selectAnchors.clear();
 const firstClick=clickBox(boxes[0]);assert.equal(firstClick.defaultPrevented,false,'ordinary selection uses native checkbox change');
 const shift=clickBox(boxes[2],{shiftKey:true});assert.equal(shift.defaultPrevented,true,'range selection suppresses the native endpoint toggle');
 assert.deepEqual(Array.from(ui.state.selectedInboxFaces),['101','102','103'],'Shift spans expanded candidate groups in visible order');
 assert.equal(boxes[1].checked,true);assert.equal(boxes[2].checked,true);
 clickBox(boxes[1]);assert.deepEqual(Array.from(ui.state.selectedInboxFaces),['101','103'],'unchecking one face preserves the other selected faces');assert.equal(boxes[1].checked,false);
 ui.state.selectAnchors.set('faces:inbox:list','102');
 const collapsed=ui.renderInboxGroup({group_key:'candidate:77',face:face(301),candidate_name:'候选',photo_count:2},'candidate',false);
 collapsed.open=false;collapsed.fire('toggle');
 assert.equal(ui.state.selectAnchors.get('faces:inbox:list'),'102','collapsing a different group preserves a still-visible range anchor');
 const anchorGroup=ui.renderInboxGroup({group_key:'candidate:78',face:face(302),candidate_name:'锚点组',photo_count:2},'candidate',false);
 ui.renderInboxFace(anchorGroup,face(102),false,anchorGroup);anchorGroup.open=false;anchorGroup.fire('toggle');
 assert.equal(ui.state.selectAnchors.has('faces:inbox:list'),false,'collapsing the group containing the shared inbox anchor clears it');
 ui.state.hiddenBatchMode=true;ui.renderFaceManagement();
 const hidden=get('pa-inbox-hidden-cards');ui.renderInboxFace(hidden,face(201),true);clickBox(hidden.children[0].selectionButton);
 assert.equal(writes.length,0,'selection alone does not issue a business request');
 assert.deepEqual(Array.from(ui.state.selectedInboxFaces),['101','103']);assert.deepEqual(Array.from(ui.state.selectedHiddenFaces),['201'],'pending and ignored faces retain independent selection domains');
 ui.clearInboxSelection(false);assert.deepEqual(Array.from(ui.state.selectedInboxFaces),[]);assert.deepEqual(Array.from(ui.state.selectedHiddenFaces),['201'],'pending clear does not clear the ignored-face selection');
 ui.clearInboxSelection(true);assert.deepEqual(Array.from(ui.state.selectedHiddenFaces),[]);
 assert.equal(ui.state.batchMode,false,'face management does not depend on the people-list batch mode');
 ui.state.selectedInboxFaces=new Set(['101','103']);ui.state.inboxBatchMode=true;get('pa-inbox-ignore-state').value='TEMPORARY';
 await ui.submitInboxVisibility(true);
 assert.equal(writes.length,1);assert.equal(writes[0].url,'/api/v1/faces/inbox/visibility');
 assert.deepEqual(writes[0].body,{face_ids:['101','103'],hidden:true,ignore_state:'TEMPORARY'},'ignore request submits only the selected face IDs and selected ignore mode');
})().catch(error=>{console.error(error);process.exitCode=1;});
""")
