"""Bounded DOM regression for per-face hiding in the personal inbox."""

import shutil
import subprocess
from pathlib import Path


def test_inbox_face_selection_paging_preview_and_hide_restore_keep_explicit_ids():
    node = shutil.which("node")
    assert node, "Node is required for the personal-page behavior check"
    script = r"""
const fs = require("fs"), vm = require("vm"), assert = require("assert/strict");
class Element {
  constructor(tag) { this.tag=tag; this.children=[]; this.value=""; this.hidden=false; this.disabled=false;
    this.textContent=""; this.dataset={}; this.listeners={}; this.attributes={}; this.open=false;
    this.classList={add(){}}; }
  append(...items) { items.forEach(item=>{if(item&&typeof item==="object")item.parentNode=this;this.children.push(item);}); }
  replaceChildren(...items) { this.children.forEach(item=>item.parentNode=null);this.children=[];this.append(...items); }
  contains(item) { for(let current=item;current;current=current.parentNode)if(current===this)return true;return false; }
  setAttribute(key,value) { this.attributes[key]=value; }
  addEventListener(name,callback) { const prior=this.listeners[name];this.listeners[name]=prior?event=>{prior(event);return callback(event);}:callback; }
  fire(name,event={target:this,currentTarget:this,preventDefault(){},stopPropagation(){}}) { if(this.listeners[name]) return this.listeners[name](event); }
  querySelector(selector) { return this.children.find(item=>String(item.className||"").includes(selector.slice(1))); }
  focus() {} showModal() { this.open=true; } close() { this.open=false; }
}
const elements=new Map(), requests=[];
const get=id=>{if(!elements.has(id))elements.set(id,new Element("div"));return elements.get(id);};
get("pa-inbox-kind").value="all";
const faceA={face_id:"a".repeat(64),asset_id:1,organization_reason:"BORDERLINE",suggestions:[{person_id:17,name:"候选甲",thumbnail_url:null}]};
const faceB={face_id:"b".repeat(64),asset_id:2,organization_reason:"NO_MATCH",suggestions:[]};
const hiddenA={face_id:"c".repeat(64),asset_id:3,organization_reason:"BLURRY",suggestions:[]};
const hiddenB={face_id:"d".repeat(64),asset_id:4,organization_reason:"SMALL",suggestions:[]};
let groupPage=0, hiddenPage=0, confirmResult=true, failVisibility=false, restored=false;
const context={
  URLSearchParams, crypto:{randomUUID:()=>"synthetic-request"}, setTimeout, clearTimeout,
  window:{confirm:()=>confirmResult,addEventListener(){},location:{search:"",href:"http://local/persons"},AIPhotoInfo:{open(id){return {dialog:{open:true},setAsset(){},setPosition(){},setNavigationEnabled(){},close(){}};}}},
  document:{readyState:"loading",cookie:"ps_csrf=synthetic-csrf",addEventListener(){},
    getElementById:get,createElement:tag=>new Element(tag)},
  fetch:async(url,options)=>{
    requests.push({url,options}); let value={};
    if(url.startsWith("/api/v1/faces/inbox/groups/candidate%3A")) {
      groupPage++;
      const cursor=new URL("http://local"+url).searchParams.get("cursor");
      value=cursor ? {faces:[faceB],has_more:false,next_cursor:null}
        : {faces:[faceA],has_more:true,next_cursor:"page-two"};
    } else if(url.startsWith("/api/v1/faces/inbox/groups?")) value={cards:[
      {type:"candidate_group",group_key:"candidate:17",candidate_name:"候选甲",photo_count:2,face:faceA},
    ],has_more:false,next_cursor:null};
    else if(url==="/api/v1/faces/inbox/summary") value={total:2,reasons:{BORDERLINE:1,NO_MATCH:1},held:0};
    else if(url.startsWith("/api/v1/faces/inbox/hidden/groups/candidate%3A")) {
      hiddenPage++;
      const cursor=new URL("http://local"+url).searchParams.get("cursor");
      value=cursor ? {faces:[hiddenB],has_more:false,next_cursor:null}
        : {faces:[hiddenA],has_more:true,next_cursor:"hidden-two"};
    } else if(url.startsWith("/api/v1/faces/inbox/hidden/groups?")) value={cards:restored?[]:[
      {type:"candidate_group",group_key:"candidate:17",candidate_name:"候选甲",photo_count:2,face:hiddenA},
    ],has_more:false,next_cursor:null};
    else if(url==="/api/v1/faces/inbox/visibility" && failVisibility) {
      return {ok:false,status:503,json:async()=>({code:"TEMPORARY"})};
    } else if(url==="/api/v1/faces/inbox/visibility") {
      const body=JSON.parse(options.body); if(!body.hidden) restored=true;
      value={hidden:body.hidden,face_count:body.face_ids.length,changed:body.face_ids.length};
    }
    else if(url.startsWith("/api/v1/assets/")) value={faces:[]};
    else value={persons:[],has_more:false,next_cursor:null};
    return {ok:true,json:async()=>value};
  },
};
let code=fs.readFileSync("src/photo_search/web/persons-albums.js","utf8");
code=code.replace('  if (document.readyState === "loading")',
  '  globalThis.ui={state,loadInbox,loadHiddenFaces,renderPeopleManagement,renderFaceManagement,submitInboxVisibility};\n  if (document.readyState === "loading")');
vm.createContext(context);vm.runInContext(code,context);
const ui=context.ui,flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  ui.state.batchMode=true;ui.state.inboxBatchMode=true;ui.renderPeopleManagement();ui.renderFaceManagement();
  await ui.loadInbox(false);const group=get("pa-inbox-cards").children[0];
  group.open=true;group.fire("toggle");await flush();
  const firstCard=group.children[2].children[0];
  const comparison=firstCard.children[0];assert(comparison,"face card has comparison layout");
  const avatar=comparison.children[0]&&comparison.children[0].children[0];assert(avatar,"face comparison has clickable avatar wrapper");
  const facePicture=avatar.children[0];assert(facePicture,"avatar wrapper contains the photo");
  assert.equal(firstCard.selectionButton.parentNode,firstCard,"checkbox is outside the clickable avatar wrapper");
  avatar.fire("click",{target:facePicture.children[0],currentTarget:avatar,preventDefault(){},stopPropagation(){}});
  assert.equal(ui.state.previewAssetId,faceA.asset_id,"avatar opens its photo in batch mode");
  assert.equal(ui.state.selectedInboxFaces.size,0,"opening a face photo does not select it");
  facePicture.fire("keydown",{target:facePicture,key:"Enter",preventDefault(){},stopPropagation(){}});
  assert.equal(ui.state.previewAssetId,faceA.asset_id,"keyboard activation also opens the face photo");
  const reference=comparison.children[1].children[0];
  firstCard.fire("click",{target:reference.children[0],shiftKey:false});
  assert.equal(ui.state.selectedInboxFaces.size,0,"reference avatar does not select its candidate face");
  firstCard.fire("click",{target:firstCard,shiftKey:false});
  assert.equal(ui.state.selectedInboxFaces.has(faceA.face_id),true,"the card body toggles face selection");
  ui.state.selectedInboxFaces.clear();
  firstCard.selectionButton.checked=true;firstCard.selectionButton.fire("change");
  assert.equal(ui.state.selectedInboxFaces.has(faceA.face_id),true);
  assert.equal(get("pa-inbox-cards").children[0],group);
  const more=group.children[3];assert.equal(more.hidden,false);await more.fire("click");
  const secondCard=group.children[2].children[1];
  secondCard.selectionButton.checked=true;secondCard.selectionButton.fire("change");
  assert.deepEqual(Array.from(ui.state.selectedInboxFaces),[faceA.face_id,faceB.face_id]);
  assert.equal(get("pa-inbox-selection-count").textContent,"已选 2 / 500 张脸");
  const groupCalls=()=>requests.filter(r=>r.url.startsWith("/api/v1/faces/inbox/groups?")).length;
  const inboxCalls=groupCalls();
  group.open=false;group.fire("toggle");group.open=true;group.fire("toggle");await flush();
  assert.equal(groupCalls(),inboxCalls);assert.equal(ui.state.selectedInboxFaces.size,2);
  await secondCard.children[2].fire("click");
  assert.equal(ui.state.selectedInboxFaces.size,2); // Previewing the photo never selects a face.

  confirmResult=false;await ui.submitInboxVisibility(true);
  assert.equal(requests.filter(r=>r.url==="/api/v1/faces/inbox/visibility").length,0);
  assert.equal(ui.state.selectedInboxFaces.size,2);
  get("pa-inbox-ignore-state").value="EXCLUDED";
  confirmResult=true;failVisibility=true;await ui.submitInboxVisibility(true);
  assert.equal(ui.state.selectedInboxFaces.size,2);
  failVisibility=false;await ui.submitInboxVisibility(true);
  const hideRequest=requests.filter(r=>r.url==="/api/v1/faces/inbox/visibility").at(-1);
  assert.deepEqual(JSON.parse(hideRequest.options.body),{face_ids:[faceA.face_id,faceB.face_id],hidden:true,ignore_state:"EXCLUDED"});
  assert.equal(hideRequest.options.headers["X-CSRF-Token"],"synthetic-csrf");
  assert.equal(ui.state.selectedInboxFaces.size,0);
  assert(requests.some(r=>r.url.startsWith("/api/v1/faces/inbox/groups?limit=30&kind=all&quality_view=clean")));

  ui.state.inboxHiddenVisible=true;ui.state.hiddenBatchMode=true;ui.renderFaceManagement();await ui.loadHiddenFaces(false);
  const hiddenCards=get("pa-inbox-hidden-cards");
  const hiddenGroup=hiddenCards.children[0];
  assert.equal(hiddenGroup.groupKey,"candidate:17");
  hiddenGroup.children[0].fire("click"); // The group heading never selects every face.
  assert.equal(ui.state.selectedHiddenFaces.size,0);
  hiddenGroup.open=true;hiddenGroup.fire("toggle");await flush();
  hiddenGroup.children[2].children[0].selectionButton.checked=true;hiddenGroup.children[2].children[0].selectionButton.fire("change");
  const hiddenMore=hiddenGroup.children[3];assert.equal(hiddenMore.hidden,false);await hiddenMore.fire("click");
  hiddenGroup.children[2].children[1].selectionButton.checked=true;hiddenGroup.children[2].children[1].selectionButton.fire("change");
  assert.deepEqual(Array.from(ui.state.selectedHiddenFaces),[hiddenA.face_id,hiddenB.face_id]);
  const ignoredCard=hiddenGroup.children[2].children[0],ignoredAvatar=ignoredCard.children[0].children[0].children[0];
  ui.state.hiddenBatchMode=false;
  ignoredAvatar.fire("click",{target:ignoredAvatar.children[0],currentTarget:ignoredAvatar,preventDefault(){},stopPropagation(){}});
  assert.equal(ui.state.previewAssetId,hiddenA.asset_id,"ignored-face avatar still previews its source in ordinary browsing");
  ui.state.hiddenBatchMode=true;
  assert.equal(ignoredCard.selectionButton.parentNode,ignoredCard,"ignored-face checkbox sits outside the avatar");
  const selectedBeforeAvatar=Array.from(ui.state.selectedHiddenFaces);
  const anchorBeforeAvatar=ui.state.selectAnchors.get("faces:hidden:list");
  ignoredAvatar.fire("click",{target:ignoredAvatar.children[0],currentTarget:ignoredAvatar,shiftKey:false,preventDefault(){},stopPropagation(){}});
  assert.equal(ui.state.previewAssetId,hiddenA.asset_id,"ignored-face avatar previews while managing");
  assert.deepEqual(Array.from(ui.state.selectedHiddenFaces),selectedBeforeAvatar,"avatar preview leaves the hidden-face selection intact");
  assert.equal(ui.state.selectAnchors.get("faces:hidden:list"),anchorBeforeAvatar,"avatar preview leaves the hidden-face range anchor intact");
  const secondIgnoredCard=hiddenGroup.children[2].children[1];
  secondIgnoredCard.fire("click",{target:secondIgnoredCard,shiftKey:false});
  assert.equal(ui.state.selectedHiddenFaces.has(hiddenB.face_id),false,"the ignored-face card body toggles selection");
  secondIgnoredCard.fire("click",{target:secondIgnoredCard,shiftKey:false});
  assert.equal(ui.state.selectedHiddenFaces.has(hiddenB.face_id),true);
  await ui.submitInboxVisibility(false);
  const restoreRequest=requests.filter(r=>r.url==="/api/v1/faces/inbox/visibility").at(-1);
  assert.deepEqual(JSON.parse(restoreRequest.options.body),{face_ids:[hiddenA.face_id,hiddenB.face_id],hidden:false,ignore_state:"TEMPORARY"});
  assert.equal(ui.state.selectedHiddenFaces.size,0);
  assert(hiddenPage>=2);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", script],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
