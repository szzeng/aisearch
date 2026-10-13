const fs=require('fs'), vm=require('vm'), assert=require('assert/strict');
const scenario=process.argv[2];
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.events={};this.attributes={};this.value='';this.hidden=false;this.disabled=false;this.classList={add(){},remove(){}};}
  set textContent(v){this.text=String(v);this.replaceChildren();}
  get textContent(){return (this.text||'')+this.children.map(c=>c.textContent||'').join(' ');}
  append(...items){items.forEach(c=>{if(c.parentNode)c.remove();c.parentNode=this;this.children.push(c);});}
  prepend(...items){items.reverse().forEach(c=>{c.parentNode=this;this.children.unshift(c);});}
  replaceChildren(...items){this.children.forEach(c=>c.parentNode=null);this.children=[];this.append(...items);}
  replaceChild(next,old){const i=this.children.indexOf(old);assert(i>=0);this.children[i]=next;next.parentNode=this;old.parentNode=null;}
  remove(){if(this.parentNode){this.parentNode.children=this.parentNode.children.filter(c=>c!==this);this.parentNode=null;}}
  get lastElementChild(){return this.children.at(-1);}
  querySelectorAll(selector){return descendants(this).filter(c=>selector.startsWith('.')&&(c.className||'').split(' ').includes(selector.slice(1)));}
  setAttribute(k,v){this.attributes[k]=String(v);} getAttribute(k){return this.attributes[k]??null;}
  removeAttribute(k){delete this.attributes[k];} focus(){} scrollIntoView(){}
  addEventListener(k,fn){this.events[k]=fn;}
  fire(k,extra={}){return this.events[k]?.({preventDefault(){this.defaultPrevented=true;},stopPropagation(){},key:'Enter',...extra});}
  showModal(){this.open=true;} close(){this.open=false;this.fire('close');}
}
const descendants=e=>e.children.flatMap(c=>[c,...descendants(c)]);
const elements=new Map(), get=id=>{if(!elements.has(id))elements.set(id,new Element('div'));return elements.get(id);};
get("pt-faces").hidden=true;
const requests=[], mutations=[];let holdSave=false, releaseSave;let personRevision=7, failSave=false, holdReview=false, releaseReview, noCandidates=false, conflict=false, reviewAvailable=true;
const f=id=>({face_id:String(id).padStart(64,'0'),asset_id:id,control_revision:3,assignment_revision:4,mode:'AUTO',thumbnail_url:null});
const people=Array.from({length:45},(_,i)=>({person_id:i+1,name:'人物'+(i+1),revision:7,asset_count:1,thumbnail_url:null}));
const old='a'.repeat(64), group={old_face_id:old,asset_id:3,control_mode:'MANUAL_ASSIGNED',revision:6,has_active_exclusions:true};
const response=(data,status=200)=>({ok:status<400,status,json:async()=>data});
const windowEvents={};
const context={URLSearchParams,Date,Map,Set,crypto:{randomUUID:()=>String(requests.length)},setTimeout:()=>1,clearTimeout(){},
 window:{location:{search:'',hash:'',href:'http://local/persons'},scrollY:0,addEventListener(k,fn){windowEvents[k]=fn;},confirm:()=>true},
 document:{readyState:'loading',cookie:'ps_csrf=synthetic',addEventListener(k,fn){this.ready=fn;},getElementById:get,createElement:tag=>new Element(tag),querySelector(selector){return selector==='dialog[open]'?[...elements.values()].find(e=>e.tag==='dialog'&&e.open)||null:null;}},
 fetch:async(url,options={})=>{
  requests.push({url,options});
  if(options.method && options.method!=='GET'){
    mutations.push({url,body:JSON.parse(options.body),options});
    if(holdSave)await new Promise(r=>releaseSave=r);
    if(url.endsWith("/regroup"))return response({operation_id:"regroup1",operation_revision:8,operation_type:"FACE_REGROUP",status:"COMMITTED",grouped_faces:1,new_persons:1,remaining_faces:1});
    if(failSave){failSave=false;return response({code:'TEMPORARY'},503);}
    if(conflict)return response({code:'PERSON_REVISION_CONFLICT'},409);
    return response({operation_id:'op1',operation_revision:8,operation_type:'BATCH_FACE_EDIT',changed:1});
  }
  if(url.includes('/persons/browse')){const q=new URLSearchParams(url.split('?')[1]);const second=q.has('cursor');return response({persons:q.get('limit')==='20'?people.slice(0,20):people.slice(second?40:0,second?45:40),has_more:!second,next_cursor:second?null:'next'});}
  if(url.includes('/persons/collections/1/tok/people'))return response({people:people.slice(0,2),has_more:false,next_cursor:null});
  if(url==='/api/v1/persons/1')return response({...people[0],revision:personRevision});
  if(url.includes('/photos'))return response({person:people[0],assets:[],has_more:false});
  if(url.includes('/faces?')){const q=new URLSearchParams(url.split('?')[1]);return response({faces:q.has('cursor')?[f(41),f(42)]:[f(1),f(2)],canonical_person_id:1,person_revision:personRevision,has_more:!q.has('cursor'),next_cursor:'faces2'});}
  if(url.includes('/face-review')){const groups=url.includes('/assets/3/')&&reviewAvailable?[group]:[];if(holdReview&&url.includes('/assets/3/'))await new Promise(r=>releaseReview=r);return response({groups,has_more:false});}
  if(url==='/api/v1/face-mappings/'+old)return response({...group,state:'OPEN',assigned_person_id:1,candidates:noCandidates?[]:[{new_face_id:'b'.repeat(64)},{new_face_id:'c'.repeat(64)}]});
  if(url.startsWith('/api/v1/person-operations/retention-preview'))return response({candidate_operation_count:2,safe_operation_count:1,skipped_operation_count:1,estimated_net_savings_bytes:300});
  if(url.startsWith('/api/v1/person-operations?'))return response({operations:['CREATE_PERSON_FROM_FACE','UNDO','BATCH_MERGE','SPLIT','FACE_MAPPING_CONFIRM'].map((type,i)=>({operation_type:type,operation_id:'op'+i,operation_revision:i+1,status:'COMMITTED',expired:i===3,committed_at:123456,face_ids:[],person_ids:[],item_count:1})),has_more:false});
  if(url==='/api/v1/capabilities')return response({faces:{open_face_mappings:1}});
  return response({persons:[],faces:[],groups:[],has_more:false});
 }};
vm.createContext(context);
let personCode=fs.readFileSync('src/photo_search/web/persons.js','utf8').replace('window.AIPersonTools = { install,','window.AIPersonTools = { _state: state, install,');
vm.runInContext(personCode,context);
let code=fs.readFileSync('src/photo_search/web/persons-albums.js','utf8');
code=code.replace('  if (document.readyState === "loading")','  globalThis.album={state,mutate,preview,loadPreviewReviews,loadChoices,submitEdit,renderInboxFace,loadCollectionPeople,openAlbum,backToLibrary};\n  if (document.readyState === "loading")');
vm.runInContext(code,context);
const flush=async()=>{await new Promise(setImmediate);await new Promise(setImmediate);};
const choose=async(card)=>{card.pick.checked=true;card.pick.fire('change');await flush();};
const button=(root,text)=>{const found=descendants(root).find(e=>e.tag==='button'&&e.textContent.endsWith(text));assert(found,text);return found;};
(async()=>{
 context.document.ready();await flush();
 const tools=context.window.AIPersonTools, album=context.album;
 assert(!requests.some(r=>/face-mappings|person-operations|\/faces\?/.test(r.url)), 'normal first page stays bounded');
 if(scenario==='mode'){
  album.openAlbum(people[0]);await flush();assert.equal(get('pt-organize').textContent,'整理照片');
  await get('pt-organize').fire('click');await flush();assert.equal(get('pt-organize').textContent,'完成整理');
  await choose(get('pt-face-list').children[0]);get('pt-face-clear').fire('click');
  assert.equal(get('pt-face-count').textContent,'已选 0 / 500 张脸');assert.equal(get('pt-organize').textContent,'完成整理');
  await choose(get('pt-face-list').children[0]);get('pt-organize').fire('click');
  assert(get('pt-faces').hidden);assert.equal(get('pt-organize').textContent,'整理照片');assert.equal(mutations.length,0);
  await get('pt-organize').fire('click');await flush();
  await choose(get('pt-face-list').children[0]);await choose(get('pt-face-list').children[1]);
  holdSave=true;const saving=get('pt-face-regroup').fire('click');await flush();
  let prevented=false;const exitEvent={preventDefault(){prevented=true;}};windowEvents.beforeunload(exitEvent);
  assert(prevented&&exitEvent.returnValue==='','busy browser reload requests confirmation');
  assert(get('pt-organize').disabled&&get('pt-face-clear').disabled&&get('pt-face-retry').disabled);
  get('pt-organize').fire('click');get('pt-face-clear').fire('click');get('pt-face-regroup').fire('click');
  album.openAlbum(people[1]);album.backToLibrary();assert.equal(album.state.person.person_id,1);
  assert(!get('pt-faces').hidden);assert.equal(get('pt-face-count').textContent,'已选 2 / 500 张脸');assert.equal(mutations.length,1);
  releaseSave();await saving;await flush();holdSave=false;
  assert.equal(mutations[0].url,'/api/v1/persons/1/regroup');assert.equal(mutations[0].body.contract_version,'p4-face-regroup.v1');
  assert.equal(mutations[0].body.faces.length,2);assert.equal(get('pt-face-count').textContent,'已选 0 / 500 张脸');
  assert(get('pa-status').textContent.includes('已归组 1 张脸 · 新增 1 个人物 · 1 张仍需整理'));
  assert(!get('pt-organize').disabled);assert.equal(get('pt-organize').textContent,'完成整理');
  prevented=false;windowEvents.beforeunload({preventDefault(){prevented=true;}});assert(!prevented);
  get('pt-organize').fire('click');assert.equal(get('pt-organize').textContent,'整理照片');assert.equal(mutations.length,1);
  await get('pt-organize').fire('click');await flush();album.openAlbum(people[1]);await flush();
  assert(get('pt-faces').hidden);assert.equal(get('pt-organize').textContent,'整理照片');
  tools.openFaces(people[1]);await flush();get('pt-face-retry').fire('click');await flush();
  assert.equal(get('pt-organize').textContent,'完成整理');assert.equal(get('pt-face-count').textContent,'已选 0 / 500 张脸');
 }
 if(scenario==='faces'){
  tools.openFaces(people[0]);await flush();
  const cards=get('pt-face-list');assert.equal(cards.children[0].children[0].tag,'label');assert.equal(cards.children[0].pick.className,'pt-avatar-checkbox');
  await choose(cards.children[0]);await get('pt-face-more').fire('click');await flush();await choose(cards.children[2]);
  assert.equal(get('pt-face-count').textContent,'已选 2 / 500 张脸');
  get('pt-face-assign').fire('click');await flush();
  button(get('pa-choices'),'人物2 · 1 张').fire('click');await get('pa-edit-form').fire('submit');await flush();
  assert.equal(mutations.length,1);assert.deepEqual(mutations[0].body.items.map(i=>i.face_id),[f(1).face_id,f(41).face_id]);
  assert(mutations[0].body.items.every(i=>i.expected_revision===3&&i.target_person_id===2));
  assert(cards.children.every(c=>!c.pick.disabled),'a successful write releases avatar checkboxes');
  await choose(cards.children[0]);personRevision=8;await get('pt-face-more').fire('click');await flush();
  assert.equal(get('pt-face-count').textContent,'已选 0 / 500 张脸');assert(get('pt-face-status').textContent.includes('PERSON_REVISION_CONFLICT'));
  tools.openFaces({...people[0],revision:8});await flush();await choose(cards.children[0]);
  await get('pt-face-split').fire('click');await flush();
  assert.equal(mutations.at(-1).url,'/api/v1/persons/1/split');assert.equal(mutations.at(-1).body.source_expected_revision,8);
  assert.equal(mutations.at(-1).body.faces[0].expected_assignment_revision,4);
 }
 if(scenario==='selection'){
  tools.openFaces(people[0]);await flush();const faces=get('pt-face-list');
  await choose(faces.children[0]);await get('pt-face-more').fire('click');await flush();faces.children[3].pick.fire('click',{shiftKey:true});
  assert.equal(tools._state.selected.size,4,'Shift selects the inclusive range across loaded pages');
  assert.equal(mutations.length,0,'selection is UI-only');
  get('pt-face-clear-all').fire('click');assert.equal(tools._state.selected.size,0);

  get('pa-batch-toggle').fire('click');const peopleCards=get('pa-cards');
  peopleCards.children[0].fire('click');peopleCards.children[2].fire('click',{shiftKey:true});
  assert.equal(album.state.selectedPeople.size,3,'person range uses canonical person ids');
  assert.equal(album.state.selectedPeopleSnapshots.get(3).revision,7,'range selection keeps snapshots');
  get('pa-people-clear').fire('click');assert.equal(album.state.selectedPeople.size,0);

  album.state.batchMode=true;const inbox=get('pa-inbox-cards'), group={groupKey:'g1'};
  album.renderInboxFace(inbox,f(3),false,group);album.renderInboxFace(inbox,f(4),false,group);
  const a=inbox.children[0],b=inbox.children[1];a.selectionButton.checked=true;a.selectionButton.fire('change');b.selectionButton.fire('click',{shiftKey:true});
  assert.equal(album.state.selectedInboxFaces.size,2,'same candidate group supports range selection');
  const hidden=get('pa-inbox-hidden-cards');album.renderInboxFace(hidden,f(5),true,{groupKey:'g1'});
  assert.equal(album.state.selectedInboxFaces.size,2,'selection remains explicitly user driven');
  album.renderInboxFace(inbox,f(6),false,{groupKey:'g2'});inbox.children[2].selectionButton.fire('click',{shiftKey:true});
  inbox.children[2].selectionButton.checked=true;inbox.children[2].selectionButton.fire('change');
  assert.equal(album.state.selectedInboxFaces.size,3,'a different group does not extend the previous range');
  get('pa-inbox-clear').fire('click');assert.equal(album.state.selectedInboxFaces.size+album.state.selectedHiddenFaces.size,0,'clear removes hidden cross-area selections');
  assert.equal(mutations.length,0,'all selection actions avoid write requests');

  album.state.collection={collection_id:1,instance_token:'tok'};await album.loadCollectionPeople(false);await flush();
  const collectionCards=get('pa-collection-people');collectionCards.children[0].children[0].fire('click');
  collectionCards.children[1].children[0].fire('click',{shiftKey:true});
  assert.equal(album.state.collectionSelectedPeople.size,2,'collection members support Shift selection');
  get('pa-collection-selection-clear').fire('click');assert.equal(album.state.collectionSelectedPeople.size,0);
  assert.equal(mutations.length,0,'collection selection remains read-only');

  tools._state.selected=new Map(Array.from({length:499},(_,i)=>['old'+i,{}]));tools._state.faceAnchor=faces.children[0].face.face_id;
  faces.children[3].pick.fire('click',{shiftKey:true});
  assert.equal(tools._state.selected.size,499,'over-limit ranges preserve the old selection atomically');
  assert(get('pt-face-status').textContent.includes('超过 500'));
 }
 if(scenario==='merge'){
  get('pa-batch-toggle').fire('click');const cards=get('pa-cards');cards.children[0].selectionBox.checked=true;cards.children[0].selectionBox.fire('change');
  await get('pa-more').fire('click');await flush();cards.children[40].selectionBox.checked=true;cards.children[40].selectionBox.fire('change');
  get('pa-batch-merge').fire('click');await flush();
  assert(!get('pa-choices').children.some(c=>c.textContent==='人物1 · 1 张'));button(get('pa-choices'),'人物2 · 1 张').fire('click');
  assert(get('pa-edit-note').textContent.includes('人物41'));await get('pa-edit-form').fire('submit');await flush();
  assert.equal(mutations.length,1);assert.equal(mutations[0].url,'/api/v1/persons/merge');
  assert.deepEqual(mutations[0].body.sources,[{person_id:1,expected_revision:7},{person_id:41,expected_revision:7}]);
  assert.equal(mutations[0].body.target_expected_revision,7);assert.equal(album.state.selectedPeople.size,0);
 }
 if(scenario==='inbox'){
  album.state.batchMode=true;const cards=get('pa-inbox-cards');album.renderInboxFace(cards,f(3),false);album.renderInboxFace(cards,f(4),false);
  assert(descendants(cards.children[0]).some(child=>child.tag==='label'));assert.equal(cards.children[0].selectionButton.className,'pt-avatar-checkbox');
  cards.children.forEach(card=>{card.selectionButton.checked=true;card.selectionButton.fire('change');});
  assert.equal(get('pa-inbox-selection-count').textContent,'已选 2 / 500 张脸');get('pa-inbox-assign').fire('click');await flush();
  button(get('pa-choices'),'人物2 · 1 张').fire('click');await get('pa-edit-form').fire('submit');await flush();
  assert.equal(mutations.length,1);assert.deepEqual(mutations[0].body.items.map(i=>i.face_id),[f(3).face_id,f(4).face_id]);
  assert.equal(album.state.selectedInboxFaces.size,0);
  album.state.batchMode=false;const ordinary=new Element('div');album.renderInboxFace(ordinary,f(5),false);
  await descendants(ordinary).find(child=>child.tag==='label').fire('click');await flush();assert.equal(album.state.previewAssetId,5);
  assert.equal(album.state.selectedInboxFaces.size,0,'ordinary avatar click opens the photo without selecting');
 }
 if(scenario==='mapping'){
  tools.openReview(3);await flush();const list=get('pt-review-list');
  assert(list.textContent.includes('人工排除'));button(list,'是同一张脸，确认所选候选').fire('click');assert.equal(mutations.length,0);
  const checks=descendants(list).filter(e=>e.tag==='input');assert(checks.every(c=>!c.checked),'no highest-candidate default');
  checks.forEach(c=>{c.checked=true;c.fire('change');});button(list,'是同一张脸，确认所选候选').fire('click');assert.equal(mutations.length,0,'multiple inheritance is refused');
  checks[3].checked=false;checks[3].fire('change');await button(list,'是同一张脸，确认所选候选').fire('click');await flush();
  assert.equal(mutations.length,1);assert.equal(mutations[0].body.decisions[0].expected_review_revision,6);
  assert.deepEqual(mutations[0].body.decisions[0].targets,[{new_face_id:'b'.repeat(64),inherits_person:true},{new_face_id:'c'.repeat(64),inherits_person:false}]);
  noCandidates=true;get('pt-review-retry').fire('click');await flush();assert(list.textContent.includes('重新检测没有找到对应人脸'));
  assert(!descendants(list).some(e=>e.tag==='button'&&e.textContent==='是同一张脸，确认所选候选'));
  button(list,'暂不处理').fire('click');assert.equal(mutations.length,1);
  await button(list,'不是同一张脸，拒绝这条映射').fire('click');await flush();assert.deepEqual(mutations.at(-1).body.decisions[0].targets,[]);
 }
 if(scenario==='history'){
  tools.openHistory();await flush();const list=get('pt-history-list');assert(list.textContent.includes('批量合并人物'));
  assert.equal(descendants(list).filter(e=>e.tag==='button').length,2,'create, UNDO, expiry have no undo action');
  conflict=true;await button(list,'撤销此操作').fire('click');await flush();assert(get('pt-history-status').textContent.includes('撤销未完成'));
  get('pt-retention-confirm').fire('click');assert.equal(mutations.length,1,'cleanup without a preview is refused');
  await get('pt-retention-preview').fire('click');await flush();assert.equal(get('pt-retention-confirm').disabled,false);
  conflict=false;await get('pt-retention-confirm').fire('click');await flush();assert.equal(mutations.at(-1).url,'/api/v1/person-operations/retention-cleanup');
 }
 if(scenario==='retry'){
  failSave=true;const body={contract_version:'p3-face-correction-contracts.v1',items:[{face_id:f(1).face_id,expected_revision:3,action:'unassign',target_person_id:null}]};
  await assert.rejects(album.mutate('/face-assignments/batch',body,'POST','saved'));assert(album.state.pendingWrite);
  await assert.rejects(album.mutate('/persons/merge',{sources:[]},'POST','other'),e=>e.code==='PENDING_WRITE');assert.equal(mutations.length,1);
  await get('pa-write-retry').fire('click');await flush();assert.equal(mutations.length,2);assert.deepEqual(mutations[0].body,mutations[1].body);assert.equal(album.state.pendingWrite,null);
 }
 if(scenario==='stale'){
  holdReview=true;tools.openReview(3);await flush();tools.openReview(4);await flush();releaseReview();await flush();assert.equal(get('pt-review-list').children.length,0);
  holdReview=true;album.preview(3);await flush();album.preview(4);await flush();releaseReview();await flush();assert.equal(get('pa-review').hidden,true);
  assert(!requests.some(r=>r.url.includes('/face-mappings?')),'asset review never scans the global list');
  holdReview=false;await album.preview(3);await flush();assert.equal(get('pa-review').hidden,false);
  holdReview=true;album.loadPreviewReviews(3,album.state.previewSeq);await flush();const staleReply=releaseReview;
  holdReview=false;reviewAvailable=false;await album.loadPreviewReviews(3,album.state.previewSeq);await flush();staleReply();await flush();
  assert.equal(get('pa-review').hidden,true,'older retry of the same photo cannot re-show a decided mapping');
 }
 if(scenario==='escape'){
  const dispatchEscape=(extra={})=>windowEvents.keydown({key:'Escape',target:get('pa-batch-toggle'),repeat:false,defaultPrevented:false,isComposing:false,preventDefault(){this.defaultPrevented=true;},...extra});
  get('pa-batch-toggle').fire('click');
  const pageCards=get('pa-cards').children.slice(), pageMore=get('pa-more').hidden;
  album.state.selectedPeople.add(1);album.state.selectedPeopleSnapshots.set(1,{...people[0]});album.state.selectAnchors.set('people:anchor',1);
  get('pa-people-clear').fire('click');assert.equal(album.state.batchMode,true,'clearing checks stays in batch mode');
  album.state.selectedPeople.add(1);album.state.selectedPeopleSnapshots.set(1,{...people[0]});album.state.selectAnchors.set('people:anchor',1);
  dispatchEscape({repeat:true});assert.equal(album.state.batchMode,true,'held Escape does not repeat the exit action');
  dispatchEscape({target:{tagName:'INPUT'}});assert.equal(album.state.batchMode,true,'Escape consumed by an input does not exit batch mode');
  const preview=get('pa-preview');preview.tag='dialog';preview.open=true;dispatchEscape();
  assert.equal(album.state.batchMode,true,'an open native dialog keeps Escape for its own cancel behavior');
  preview.open=false;dispatchEscape();
  assert.equal(album.state.batchMode,false);assert.equal(album.state.selectedPeople.size,0);assert.equal(album.state.selectedPeopleSnapshots.size,0);assert.equal(album.state.selectAnchors.size,0);
  assert.deepEqual(get('pa-cards').children,pageCards,'exit preserves already loaded cards');assert.equal(get('pa-more').hidden,pageMore,'exit preserves pagination');assert.equal(mutations.length,0,'Escape only changes temporary selection state');
  album.openAlbum(people[0]);await flush();get('pt-organize').fire('click');await flush();await choose(get('pt-face-list').children[0]);
  assert.equal(tools._state.selected.size,1);dispatchEscape();
  assert.equal(get('pt-faces').hidden,true,'Escape completes face organization');assert.equal(tools._state.selected.size,0);assert.equal(mutations.length,0,'closing organization does not undo or write');
  get('pt-organize').fire('click');await flush();await choose(get('pt-face-list').children[0]);
  album.state.writing=true;dispatchEscape();assert.equal(get('pt-faces').hidden,false,'busy writes block Escape exit');assert.equal(tools._state.selected.size,1);
  album.state.writing=false;get('pa-edit').tag='dialog';get('pa-edit').open=true;album.state.batchMode=true;dispatchEscape();
  assert.equal(get('pa-edit').open,true);assert.equal(album.state.batchMode,true,'an edit modal never lets Escape reach batch mode');
 }
})().catch(error=>{console.error(error);process.exitCode=1;});
