from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_collection_list_and_photo_page_show_total_distinct_photo_count() -> None:
    html = Path(__file__).resolve().parents[2] / "src/photo_search/web/persons.html"
    assert "persons-albums.js?v=p4-person-avatar-click-a-3" in html.read_text(encoding="utf-8")
    assert "persons.css?v=p4-person-avatar-click-a-3" in html.read_text(encoding="utf-8")
    script_source = html.with_name("persons-albums.js").read_text(encoding="utf-8")
    assert "人物成员显示在“人物”页；这里仅显示单独选中的人脸" in script_source
    assert '$("collection-face-section").open = true' in script_source
    assert "new Set([...state.selectedInboxFaces, ...state.selectedHiddenFaces]).size" in script_source
    assert "preview(Number(face.asset_id), event.currentTarget, face)" in script_source
    assert 'focusFace ? "已打开原照片，并标出本次单独收录的人脸。"' in script_source
    node = shutil.which("node")
    assert node is not None, "Node is required for the person collection count UI check"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
class Element {
  constructor(tag="div"){this.tagName=tag.toUpperCase();this.children=[];this.events={};this.attributes={};this.dataset={};this.value="";this.hidden=false;this.disabled=false;this.classList={add(){},remove(){}};}
  set textContent(value){this.text=String(value);this.children=[];}
  get textContent(){return this.text||"";}
  append(...items){items.forEach(item=>{if(item&&typeof item==="object")item.parentNode=this;this.children.push(item);});}
  replaceChildren(...items){this.text="";this.children=[];this.append(...items);}
  addEventListener(name,fn){this.events[name]=fn;}
  setAttribute(name,value){this.attributes[name]=String(value);}
  focus(){}
}
const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
const collection={collection_id:9,instance_token:"b".repeat(32),name:"旅行",revision:2,member_count:6,photo_count:24};
const context={URLSearchParams,URL,Date,crypto:{randomUUID:()=>"synthetic"},setTimeout:()=>1,clearTimeout(){},
 window:{location:{search:"",href:"http://local/persons"},confirm:()=>true,prompt:()=>"旅行新名",history:{replaceState(){}},addEventListener(){}},
 document:{readyState:"loading",cookie:"ps_csrf=csrf",addEventListener(){},getElementById:get,createElement:tag=>new Element(tag)},
 fetch:async(url,options={})=>({ok:true,status:200,headers:{get:()=>null},json:async()=>options.method==="PATCH"
   ?{collection:{collection_id:9,instance_token:collection.instance_token,name:"旅行新名",revision:3,member_count:6}}
   :url.includes("/photos?")
     ?{assets:[],has_more:false,next_cursor:null,collection:{...collection}}
     :{collections:[{...collection}],has_more:false,next_cursor:null}})};
let code=fs.readFileSync("src/photo_search/web/persons-albums.js","utf8");
code=code.replace('  if (document.readyState === "loading")','  globalThis.ui={loadCollections,openCollection,renameCollection,loadCollectionFaces,openAlbum,backToLibrary,state,api,errorText};\n  if (document.readyState === "loading")');
vm.createContext(context);vm.runInContext(code,context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 await context.ui.loadCollections(false);
 assert.match(get("pa-collections").children[0].children[0].textContent,/6 个人物 · 0 张脸 · 24 张照片/);
 const loaded=context.ui.state.collectionById.get("9");context.ui.openCollection(loaded);await flush();
 assert.match(get("pa-album-note").textContent,/24 张去重照片/);
 await context.ui.renameCollection(context.ui.state.collection);await flush();
 assert.match(get("pa-album-note").textContent,/24 张去重照片/,"renaming a collection preserves its photo count");
 const candidateFace=id=>({face_id:id.repeat(64),asset_id:id.charCodeAt(0),status:"CURRENT",thumbnail_url:null,
   candidate:{person_id:id==="a"?17:18,name:"未命名的人"},assigned_person:null});
 context.ui.state.collection={...collection,face_member_count:2};
 context.fetch=async()=>({ok:true,status:200,headers:{get:()=>null},json:async()=>({
   collection:{...context.ui.state.collection},faces:[candidateFace("a"),candidateFace("b")],has_more:false,next_cursor:null})});
 assert.equal(await context.ui.loadCollectionFaces(false),true,"candidate faces load without a client error");
 const gallery=get("pa-collection-faces");
 assert.equal(gallery.children.length,2,"candidate faces render directly in one gallery");
 assert.ok(gallery.children.every(card=>card.tagName==="ARTICLE"),"candidate person IDs do not create full-width groups");
 assert.equal(gallery.children[0].title,"建议归入 未命名的人");
 assert.equal(gallery.children[0].attributes["aria-label"],"建议归入 未命名的人");
 assert.equal(gallery.children[0].children.filter(child=>child.tagName==="STRONG").length,0,"candidate name is not repeated visibly on every face card");
 assert.equal(gallery.children[0].children.filter(child=>child.tagName==="SPAN"&&child.className==="hint").length,0,"current faces do not repeat the per-photo explanation on every card");
 assert.match(get("pa-collection-faces-note").textContent,/2 张脸/);
 assert.match(get("pa-collection-faces-note").textContent,/不会更改人脸归属/);
 assert.ok(fs.readFileSync("src/photo_search/web/persons.css","utf8").includes("#pa-collection-faces .pa-collection-face-card { min-height: 0; padding: 0.45rem; cursor: pointer; }"),"face cards use a pointer cursor");
 assert.ok(!fs.readFileSync("src/photo_search/web/persons.css","utf8").includes("#pa-collection-faces .pa-collection-face-card:hover"),"hover does not add a frame or background change");
 context.fetch=async()=>({ok:true,status:200,headers:{get:()=>null},json:async()=>({
   collection:{...context.ui.state.collection,face_member_count:1},faces:[{...candidateFace("e"),candidate:null}],has_more:false,next_cursor:null})});
 assert.equal(await context.ui.loadCollectionFaces(false),true,"a separately archived face without a suggestion remains visible");
 const unmatchedFace=get("pa-collection-faces").children.find(card=>card.dataset.faceId==="e".repeat(64));
 assert.equal(unmatchedFace.children.filter(child=>child.tagName==="STRONG").length,0,"an unassigned face does not show a redundant title");
 assert.equal(unmatchedFace.children.filter(child=>child.tagName==="SPAN"&&child.className==="hint").length,0,"an unassigned current face does not show the redundant per-photo hint");
 context.fetch=async()=>({ok:true,status:200,headers:{get:()=>null},json:async()=>({
   collection:{...context.ui.state.collection,face_member_count:1},faces:[{...candidateFace("f"),candidate:null,assigned_person:{person_id:23,name:"已命名人物"}}],has_more:false,next_cursor:null})});
 assert.equal(await context.ui.loadCollectionFaces(false),true,"an assigned face loads without a suggestion group");
 const assignedFace=get("pa-collection-faces").children.find(card=>card.dataset.faceId==="f".repeat(64));
 assert.equal(assignedFace.children.find(child=>child.tagName==="STRONG").textContent,"已归属 已命名人物","actual assignment remains labeled");
 assert.equal(assignedFace.children.filter(child=>child.tagName==="SPAN"&&child.className==="hint").length,0,"current assigned faces do not repeat the per-photo hint");
 const collectionA={...collection,collection_id:9,instance_token:"b".repeat(32),revision:4,face_member_count:1};
 const collectionB={...collection,collection_id:10,instance_token:"c".repeat(32),revision:1,face_member_count:1};
 context.ui.state.collection=collectionA;
 let resolveOldFaces;
 const oldFace={...candidateFace("c"),candidate:null};
 context.fetch=url=>url.includes("/photos?")
   ?Promise.resolve({ok:true,status:200,headers:{get:()=>null},json:async()=>({assets:[],has_more:false,next_cursor:null,collection:context.ui.state.collection})})
   :new Promise(resolve=>{resolveOldFaces=()=>resolve({ok:true,status:200,headers:{get:()=>null},json:async()=>({collection:collectionA,faces:[oldFace],has_more:false,next_cursor:null})});});
 const oldLoad=context.ui.loadCollectionFaces(false);
 context.ui.state.collectionActionBusy=false; context.ui.state.collectionPeopleBusy=false; context.ui.state.writing=false; context.ui.state.pendingWrite=null;
 context.ui.openCollection(collectionB);
 assert.equal(get("pa-collection-faces").children.length,0,"switching collections clears the previous face cards immediately");
 context.fetch=async(url)=>({ok:true,status:200,headers:{get:()=>null},json:async()=>url.includes("/photos?")
   ?{assets:[],has_more:false,next_cursor:null,collection:collectionB}
   :{collection:collectionB,faces:[{...candidateFace("d"),candidate:null}],has_more:false,next_cursor:null}});
 assert.equal(await context.ui.loadCollectionFaces(false),true,"the new collection can load while the old request is in flight");
 resolveOldFaces(); await oldLoad;
 assert.deepEqual(get("pa-collection-faces").children.map(card=>card.dataset.faceId),["d".repeat(64)],"a late response from another collection cannot repopulate the view");
 context.fetch=async()=>({ok:false,status:404,headers:{get:()=>"request-404"},json:async()=>({detail:"Not Found"})});
 let missingRoute;try{await context.ui.api("/persons/collections/9/"+collection.instance_token+"/faces");}catch(error){missingRoute=error;}
 assert.equal(missingRoute.code,"HTTP_404");assert.equal(missingRoute.status,404);
 assert.match(context.ui.errorText(missingRoute),/接口.*HTTP 404/);
 assert.match(context.ui.errorText(missingRoute),/request-404/);
 context.fetch=async()=>({ok:false,status:401,headers:{get:()=>"request-401"},json:async()=>({code:"SESSION_REQUIRED",request_id:"request-401"})});
 let expiredSession;try{await context.ui.api("/persons/collections/9/"+collection.instance_token+"/faces");}catch(error){expiredSession=error;}
 assert.equal(expiredSession.code,"SESSION_REQUIRED");assert.equal(expiredSession.status,401);
 assert.match(context.ui.errorText(expiredSession),/重启本地图库/);
 assert.match(context.ui.errorText(expiredSession),/request-401/);
 context.fetch=async()=>({ok:false,status:403,headers:{get:()=>"request-403"},json:async()=>({code:"CSRF_INVALID",request_id:"request-403"})});
 let forbidden;try{await context.ui.api("/persons/collections/9/"+collection.instance_token+"/faces");}catch(error){forbidden=error;}
 assert.equal(forbidden.code,"CSRF_INVALID");assert.equal(forbidden.status,403);
 assert.match(context.ui.errorText(forbidden),/拒绝了此请求.*CSRF_INVALID/);
 assert.doesNotMatch(context.ui.errorText(forbidden),/重启本地图库/);
 context.fetch=async()=>({ok:false,status:502,headers:{get:()=>"request-html"},json:async()=>{throw new SyntaxError("<html>upstream failure</html>");}});
 let invalidResponse;try{await context.ui.api("/persons/collections/9/"+collection.instance_token+"/faces");}catch(error){invalidResponse=error;}
 assert.equal(invalidResponse.code,"NON_JSON_RESPONSE");assert.equal(invalidResponse.status,502);
 assert.match(context.ui.errorText(invalidResponse),/无法识别的错误.*HTTP 502/);
 assert.doesNotMatch(context.ui.errorText(invalidResponse),/upstream failure/);
 context.fetch=async()=>{throw new TypeError("Failed to fetch");};
 let unavailable;try{await context.ui.api("/persons/collections/9/"+collection.instance_token+"/faces");}catch(error){unavailable=error;}
 assert.equal(unavailable.code,"NETWORK_ERROR");
 assert.match(context.ui.errorText(unavailable),/无法连接本地服务/);
 assert.doesNotMatch(context.ui.errorText(unavailable),/Failed to fetch/);
 assert.match(context.ui.errorText(new TypeError("internal render detail")),/CLIENT_ERROR/);
 assert.doesNotMatch(context.ui.errorText(new TypeError("internal render detail")),/internal render detail/);
 get("pa-collection-face-section").hidden=false; context.ui.state.returnCollection=collectionB;
 context.ui.state.writing=false; context.ui.state.pendingWrite=null; context.ui.state.collectionActionBusy=false; context.ui.state.collectionPeopleBusy=false;
 context.fetch=async()=>({ok:true,status:200,headers:{get:()=>null},json:async()=>({assets:[],has_more:false,next_cursor:null})});
 context.ui.openAlbum({person_id:77,name:"合集人物",asset_count:1,thumbnail_url:null});
 assert.equal(get("pa-collection-face-section").hidden,true,"opening a collection member hides the collection-only face menu");
 context.ui.backToLibrary();
 assert.equal(get("pa-collection-face-section").hidden,false,"returning to the collection restores its face menu");
 assert.equal(get("pa-collection-face-section").open,true,"returning to the collection keeps the face menu expanded");
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", script], cwd=Path(__file__).resolve().parents[2], capture_output=True,
        text=True, encoding="utf-8", timeout=10,
    )
    assert result.returncode == 0, result.stderr
