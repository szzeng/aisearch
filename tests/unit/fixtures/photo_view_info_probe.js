
const fs = require("fs"), vm = require("vm"), assert = require("assert/strict");
class Element {
  constructor(tag) { this.tagName=tag; this.children=[]; this.events={}; this.attributes={}; this.dataset={}; this.style={}; this.hidden=false; this.value=""; this.parentNode=null; this.classList={names:new Set(),add(name){this.names.add(name);},remove(name){this.names.delete(name);},contains(name){return this.names.has(name);},toggle(name){if(this.names.has(name)){this.names.delete(name);return false;}this.names.add(name);return true;}}; }
  set textContent(value) { this.text=String(value); this.children.forEach(c=>c.parentNode=null); this.children=[]; }
  get textContent() { return (this.text||"")+this.children.map(c=>c.textContent).join(""); }
  append(...items) { items.forEach(c=>{ if(c && typeof c==="object"){c.parentNode=this;} this.children.push(c); }); }
  appendChild(item) { this.append(item); return item; }
  replaceChildren(...items) { this.text=""; this.children.forEach(c=>c.parentNode=null); this.children=[]; this.append(...items); }
  after(item) { if(!this.parentNode)return; const a=this.parentNode.children,i=a.indexOf(this); item.parentNode=this.parentNode; a.splice(i+1,0,item); }
  setAttribute(key,value) { this.attributes[key]=String(value); }
  removeAttribute(key) { delete this[key]; }
  querySelector(selector) { return selector===".maplibregl-ctrl-attrib" ? (this.attributionControl||null) : selector===".maplibregl-ctrl-attrib-button" ? (this.attributionButton||null) : null; }
  querySelectorAll(selector) { const wanted=selector.split(",").map(s=>s.trim().replace(/^\./,"")); const out=[]; const walk=n=>{(n.children||[]).forEach(c=>{if(wanted.some(v=>c.className===v))out.push(c);walk(c);});};walk(this);return out; }
  addEventListener(event,callback) { (this.events[event] ||= []).push(callback); }
  setPointerCapture(id) { (this.captured ||= new Set()).add(id); }
  hasPointerCapture(id) { return !!this.captured && this.captured.has(id); }
  releasePointerCapture(id) { if(this.captured)this.captured.delete(id); }
  fire(event,extra={}) { (this.events[event]||[]).forEach(fn=>fn(Object.assign({target:this,key:""},extra))); }
  getBoundingClientRect() { return {left:0,top:0,width:this.clientWidth||800,height:this.clientHeight||600}; }
  focus() { this.focused=true; } select() { this.selected=true; }
  remove() { if(this.parentNode){const a=this.parentNode.children;a.splice(a.indexOf(this),1);this.parentNode=null;} }
  get isConnected() { let n=this; while(n.parentNode)n=n.parentNode; return n===document.body || n===document.head; }
  showModal() { this.open=true; }
  close() { this.open=false; this.fire("close"); }
}
const document={cookie:"ps_csrf=synthetic-token",referrer:"",createElement:t=>new Element(t),createElementNS:(_ns,t)=>new Element(t),querySelector:s=>s==="dialog[open]"?(document.body.children.find(n=>n.tagName==="dialog"&&n.open)||null):null};
document.body=new Element("body"); document.head=new Element("head");
const maps=[];
class FakeMap { constructor(options){this.options=options;this.events={};this.fired=[];this.removed=false;const summary={attributes:{},setAttribute(key,value){this.attributes[key]=String(value);}};const control={open:true,attributes:{open:""},attributionButton:summary,classList:{classes:new Set(["maplibregl-compact","maplibregl-compact-show"]),remove(name){this.classes.delete(name);}},querySelector(selector){return selector===".maplibregl-ctrl-attrib-button"?summary:null;},setAttribute(key,value){this.attributes[key]=String(value);},removeAttribute(name){delete this.attributes[name];}};this.options.container.attributionControl=control;maps.push(this);} on(k,fn){this.events[k]=fn;} fire(k){this.fired.push(k);if(this.events[k])this.events[k]({type:k});} addSource(id,source){this.source={id,source};} addLayer(layer){this.layer=layer;} remove(){this.removed=true;} }
const resizeObservers=[];
class FakeResizeObserver { constructor(callback){this.callback=callback;this.disconnected=false;resizeObservers.push(this);} observe(){} disconnect(){this.disconnected=true;} }
const window={location:{href:"/"},maplibregl:{Map:FakeMap},AISEARCH_MAP_CONFIG:{sourceTemplates:key=>({"td-vec":{key},"td-cva":{key},"td-img":{key},"td-cia":{key}}),layerIds:layer=>layer==="imagery"?["td-img-layer","td-cia-layer"]:["td-vec-layer","td-cva-layer"]}};
const fetches=[]; const faceWrites=[]; const changeTokens=[]; let releaseDetail,releaseFavoriteState,releaseOcr,faceWriteStatus=200;
window.localStorage={setItem:(key,value)=>changeTokens.push({key,value}),getItem:()=>null};
const ok=body=>({ok:true,status:200,json:async()=>body});
const detail=id=>({asset_id:id,sha256:"sha-"+id,byte_size:2048,width:1200,height:800,format:"jpeg",availability:"present",taken_time:{label:"file_time",taken_at:"2020-01-02T03:04:05.000",camera:"Camera X"},locations:[
  {location_id:11,source_id:1,source_display_name:"旅行",relative_path:"照片/海边.jpg",availability:"PRESENT"},
  {location_id:12,source_id:2,source_display_name:"备份",relative_path:"2020\\海边.jpg",availability:"MISSING"},
  {location_id:13,source_id:3,source_display_name:"盘符根目录",relative_path:"根目录照片.jpg",availability:"PRESENT"},
  {location_id:14,source_id:4,source_display_name:"非法位置",relative_path:"../escape.jpg",availability:"PRESENT"},
  {location_id:15,source_id:4,source_display_name:"绝对位置",relative_path:"\\rooted.jpg",availability:"PRESENT"},
  {location_id:16,source_id:4,source_display_name:"盘符相对位置",relative_path:"E:drive-relative.jpg",availability:"PRESENT"}]});
const postCalls=[]; let deferLocal=false,releaseLocal;
const context={document,window,navigator:{clipboard:{writeText:()=>Promise.reject(new Error("clipboard denied"))}},ResizeObserver:FakeResizeObserver,URL,fetch:async (url,options)=>{
  if(url.includes("/favorites/states")) {
    if(url.includes("asset_ids=701")) return new Promise(resolve=>{releaseFavoriteState=()=>resolve(ok({favorite_asset_ids:[701],favorites_revision:1}));});
    return ok({favorite_asset_ids:[],favorites_revision:0});
  }
  if(url.includes("/local-action")) { postCalls.push({url,options}); const body={status:"accepted",action:JSON.parse(options.body).action}; return deferLocal?new Promise(resolve=>{releaseLocal=()=>resolve(ok(body));}):ok(body); }
  if(options && options.method && options.method!="GET") {
    if(url.endsWith("/favorite")) return ok({asset_id:Number(url.split("/").slice(-2,-1)[0]),is_favorite:options.method==="PUT",changed:true,favorites_revision:1});
    if(url.endsWith("/assignment")) assert.ok(["assign","unassign","restore_auto"].includes(JSON.parse(options.body).action),"the real assignment API rejects unknown action names");
    faceWrites.push({url,options}); return faceWriteStatus===200 ? ok({changed:1,operation_type:"FACE_MAPPING"}) : {ok:false,status:faceWriteStatus,json:async()=>({code:"SYNTHETIC_WRITE_FAILURE"})};
  }
  fetches.push(url);
  if(url==="/api/v1/assets/201") { await new Promise(r=>{releaseDetail=r;}); return ok(detail(201)); }
  if(/\/api\/v1\/assets\/\d+\/ocr-text$/.test(url)) {
    const id=Number(url.split("/").at(-2));
    if(id===901) return new Promise(resolve=>{releaseOcr=()=>resolve(ok({status:"available",text:"late OCR leak",truncated:false}));});
    return ok(id===101?{status:"available",text:"发票 1280 元\n<script>alert(1)</script>",truncated:false}:{status:"none",text:"",truncated:false});
  }
  if(/^\/api\/v1\/assets\/\d+$/.test(url)) return ok(detail(Number(url.split("/").pop())));
  if(url.includes("/sources/1")) return ok({root_path:"D:\\相册\\旅行"});
  if(url.includes("/sources/2")) return ok({root_path:"\\\\server\\share\\archive"});
  if(url.includes("/sources/3")) return ok({root_path:"D:\\"});
  if(url.includes("/sources/4")) return ok({root_path:"D:\\library"});
  if(url.endsWith("/assets/302/faces")) return ok({has_more:true,faces:[]});
  if(url.endsWith("/assets/501/faces")) return ok({faces:[
    {face_id:"1".repeat(64),assigned_person_id:7,assigned_person_name:"爸爸",assigned_person_asset_count:2,control_revision:1,source_detail:true,thumbnail_url:"/face/assigned"},
    {face_id:"2".repeat(64),assigned_person_id:null,control_revision:2,source_detail:true,thumbnail_url:"/face/pending"},
    {face_id:"3".repeat(64),assigned_person_id:null,ignore_state:"TEMPORARY",control_revision:0,source_detail:true,thumbnail_url:"/face/temporary"},
    {face_id:"4".repeat(64),assigned_person_id:null,ignore_state:"EXCLUDED",control_revision:0,source_detail:true,thumbnail_url:"/face/excluded"}]});
  if(url.includes("/persons/browse")) return ok({persons:[{person_id:12,name:"妈妈",asset_count:4}],has_more:false});
  if(url.endsWith("/assets/401/faces")) return ok({faces:[
    {assigned_person_id:7,assigned_person_name:"爸爸",assigned_person_asset_count:2,assigned_person_avatar_face_id:"a".repeat(64),thumbnail_url:"/face/current"},
    {assigned_person_id:9,assigned_person_name:"妈妈",assigned_person_asset_count:20,assigned_person_avatar_face_id:"b".repeat(64),thumbnail_url:"/face/mom"}]});
  if(url.endsWith("/faces")) return ok({has_more:true,faces:[
    {assigned_person_id:7,assigned_person_name:"爸爸",assigned_person_asset_count:2,assigned_person_avatar_face_id:"a".repeat(64),thumbnail_url:"/face/current"},
    {assigned_person_id:7,assigned_person_name:"爸爸",assigned_person_asset_count:2,assigned_person_avatar_face_id:"a".repeat(64),thumbnail_url:"/face/duplicate"},
    {assigned_person_id:7,assigned_person_name:"爸爸",assigned_person_asset_count:2,assigned_person_avatar_face_id:"a".repeat(64),thumbnail_url:"/face/merged"}]});
  if(url.includes("/map/assets")) return ok({total:url.includes("asset_id=301")||url.includes("asset_id=302")?0:1});
  if(url.endsWith("/geo")) return ok({status:"VALID",input_revision:"sha-101",datum_status:"WGS84",normalized_wgs84:{latitude:0,longitude:0}});
  if(url==="/api/v1/map/provider-runtime") return ok({status:"CONFIGURED",key:"fake-map-key",default_layer:"vector"});
  throw new Error("unexpected request: "+url);
}};
vm.createContext(context); vm.runInContext(fs.readFileSync("src/photo_search/web/photo-info.js","utf8"),context);
const flush=()=>new Promise(setImmediate), all=(root,fn)=>{const out=[];function walk(n){if(fn(n))out.push(n);(n.children||[]).forEach(walk);}walk(root);return out;};
(async()=>{
  const host=new Element("aside"); document.body.append(host); let chosen=0;
  let first=context.window.AIPhotoInfo.render(host,101,{onPerson:id=>{chosen=id;}});
  await flush(); await flush();
  assert.match(host.textContent,/分辨率1200 × 800 像素/,"photo facts show source pixel resolution");
  assert.equal(fetches.filter(url=>url==="/api/v1/assets/101/ocr-text").length,1,"the shared photo-info panel requests OCR once for the viewed asset");
  const ocrSection=all(host,n=>n.className==="photo-info-ocr")[0];
  assert.ok(ocrSection,"non-empty OCR is shown in the shared information panel");
  const ocrText=all(ocrSection,n=>n.className==="photo-info-ocr-text")[0];
  assert.equal(ocrText.textContent,"发票 1280 元\n<script>alert(1)</script>","OCR preserves line breaks and treats markup as plain text");
  const ocrCopy=all(ocrSection,n=>n.tagName==="button"&&n.textContent==="复制文字")[0];
  ocrCopy.fire("click"); await flush(); await flush();
  assert.equal(all(ocrSection,n=>n.className==="photo-info-copy-fallback")[0].value,"发票 1280 元\n<script>alert(1)</script>","clipboard fallback selects the exact returned OCR string");
  assert.equal(all(host,n=>n.tagName==="details"&&n.className==="photo-info-details").length,1,"the information panel can be folded without removing its contents");
  assert.equal(all(host,n=>n.tagName==="summary"&&n.textContent==="照片信息").length,1);
  assert.match(host.textContent,/D:\\相册\\旅行\\照片\\海边\.jpg/);
  assert.match(host.textContent,/\\\\server\\share\\archive\\2020\\海边\.jpg/);
  assert.match(host.textContent,/D:\\根目录照片\.jpg/,"a drive root keeps its root separator when joined");
  assert.ok(!host.textContent.includes("D:\\library\\escape.jpg"),"parent traversal is not shown as a complete path");
  assert.ok(!host.textContent.includes("D:\\library\\rooted.jpg"),"rooted relative paths are rejected");
  assert.ok(!host.textContent.includes("D:\\library\\E:drive-relative.jpg"),"drive-relative paths are rejected");
  assert.match(host.textContent,/文件时间 2020-01-02 03:04:05\.000/);
  assert.equal(maps[0].options.center[0],0); assert.equal(maps[0].options.center[1],0);
  assert.equal(maps[0].options.attributionControl.compact,true,"detail map uses compact attribution mode");
  maps[0].events.load();
  assert.equal(maps[0].options.container.attributionControl.open,false,"the detail map closes the compact attribution disclosure on initial load");
  assert.ok(!maps[0].options.container.attributionControl.classList.classes.has("maplibregl-compact-show"),"the long attribution text is hidden until deliberately opened");
  const mapBlock=all(host,n=>n.className==="photo-info-map")[0], attributionSummary={attributes:{},closest:selector=>selector===".maplibregl-ctrl-attrib-button"?attributionSummary:null,setAttribute(key,value){this.attributes[key]=String(value);}};
  let prevented=false; mapBlock.fire("click",{target:attributionSummary,preventDefault(){prevented=true;}});
  assert.ok(mapBlock.classList.names.has("photo-info-map-attribution-open"),"the attribution button can deliberately expand its text");
  assert.equal(maps[0].options.container.attributionControl.open,true); assert.equal(attributionSummary.attributes["aria-expanded"],"true"); assert.ok(prevented);
  mapBlock.fire("click",{target:attributionSummary,preventDefault(){prevented=true;}});
  assert.ok(!mapBlock.classList.names.has("photo-info-map-attribution-open"),"the attribution button can collapse its text again");
  assert.equal(maps[0].options.container.attributionControl.open,false); assert.equal(attributionSummary.attributes["aria-expanded"],"false");
  assert.equal(JSON.stringify(maps[0].source.source.data.geometry.coordinates),"[0,0]");
  assert.equal(all(host,n=>n.tagName==="button"&&n.textContent==="复制完整路径").length,3);
  const localButtons=all(host,n=>n.className==="photo-info-local-action");
  assert.equal(localButtons.length,10,"each PRESENT location has both local actions; missing locations do not");
  const openButton=localButtons.find(n=>n.textContent==="系统打开原图");
  openButton.fire("click"); assert.equal(openButton.disabled,true,"the clicked action disables immediately");
  openButton.fire("click"); assert.equal(postCalls.length,1,"duplicate clicks are blocked while the request is pending");
  await flush(); assert.equal(openButton.disabled,false);
  assert.match(postCalls[0].url,/assets\/101\/local-action$/);
  assert.equal(postCalls[0].options.method,"POST");
  assert.equal(postCalls[0].options.headers["X-CSRF-Token"],"synthetic-token");
  assert.deepEqual(JSON.parse(postCalls[0].options.body),{action:"open",location_id:11});
  const copy=all(host,n=>n.tagName==="button"&&n.textContent==="复制完整路径")[0]; copy.fire("click"); await flush();
  assert.ok(all(host,n=>n.tagName==="textarea"&&n.selected&&n.attributes["aria-label"]==="选择并复制完整路径").length===1);
  all(host,n=>n.tagName==="button"&&n.className==="photo-info-person")[0].fire("click"); assert.equal(chosen,7);
  assert.equal(all(host,n=>n.className==="photo-info-person").length,1,"different person IDs merged to one canonical ID render once");
  assert.equal((fetches.filter(url=>url.endsWith("/faces"))).length,1,"the face list is fetched once");
  assert.equal(fetches.filter(url=>/\/persons\/\d+$/.test(url)).length,0,"canonical person names and avatars do not trigger per-person requests");
  first.dispose(); assert.equal(maps[0].removed,true);
  const ocrRaceHost=new Element("aside"); document.body.append(ocrRaceHost);
  context.window.AIPhotoInfo.render(ocrRaceHost,901); await flush(); await flush();
  assert.equal(typeof releaseOcr,"function","the first photo's OCR response is pending after its base information loads");
  context.window.AIPhotoInfo.render(ocrRaceHost,902); await flush(); await flush();
  releaseOcr(); await flush(); await flush();
  assert.ok(!ocrRaceHost.textContent.includes("late OCR leak"),"a late OCR response cannot cross a photo switch");

  let sharedFacePage=null; const changedFaces=[];
  const manager=context.window.AIPhotoInfo.open(501,{people:false,onFaces:(id,page)=>{sharedFacePage={id,page};},onFacesChanged:(id,result)=>changedFaces.push({id,result})}); await flush(); await flush();
  assert.equal(sharedFacePage.id,501,"people:false extensions receive the shared asset face result");
  assert.ok(sharedFacePage.page.faces.length>=3);
  const managerToggle=all(manager.panel,n=>n.className==="photo-face-manager-toggle")[0];
  assert.ok(managerToggle,"people:false viewer entries still expose the shared face manager");
  managerToggle.fire("click");
  const batchToggle=all(manager.panel,n=>n.tagName==="button"&&n.textContent==="批量整理")[0];
  const batchActions=all(manager.panel,n=>n.className==="photo-face-batch-actions")[0];
  assert.ok(batchActions.hidden,"batch actions remain hidden until batch mode is selected");
  const pendingGroup=all(manager.panel,n=>n.tagName==="details"&&n.className==="photo-face-group"&&n.textContent.includes("待整理 · 1"))[0];
  assert.ok(pendingGroup,"expanding the shared manager adds the pending-face group: "+manager.panel.textContent);
  assert.equal(pendingGroup.events.toggle.length,1,"pending group has a lazy-load listener");
  pendingGroup.open=true; pendingGroup.fire("toggle");
  assert.equal(pendingGroup.dataset.loaded,"true","opening pending group loads face cards lazily");
  const faceCheckbox=all(pendingGroup,n=>n.className==="photo-face-select")[0];
  assert.ok(faceCheckbox.hidden,"face selection is available only after batch mode is selected");
  batchToggle.fire("click");
  assert.equal(batchActions.hidden,false);
  assert.equal(faceCheckbox.hidden,false);
  const createFace=all(pendingGroup,n=>n.tagName==="button"&&n.textContent==="新建未命名人物")[0];
  assert.ok(createFace,"pending faces expose create-person action: "+pendingGroup.textContent);
  createFace.fire("click"); await flush(); await flush();
  assert.equal(faceWrites.length,1,"new-person action posts only for the selected face");
  assert.match(faceWrites[0].url,/faces\/2{64}\/create-person$/);
  assert.equal(faceWrites[0].options.headers["X-CSRF-Token"],"synthetic-token");
  assert.equal(JSON.parse(faceWrites[0].options.body).expected_control_revision,2);
  assert.ok(JSON.parse(faceWrites[0].options.body).request_id,"creation uses a stable idempotency key");
  assert.equal(changedFaces.length,1,"confirmed face writes notify the containing person album");
  assert.equal(changedFaces[0].id,501);
  assert.equal(changedFaces[0].result.changed,1);
  assert.equal(fetches.filter(url=>/faces\/[34]{64}\/thumbnail/.test(url)).length,0,"collapsed ignored faces do not trigger thumbnail requests");
  manager.close();

  function pendingFace(view) {
    all(view.panel,n=>n.className==="photo-face-manager-toggle")[0].fire("click");
    const group=all(view.panel,n=>n.tagName==="details"&&n.className==="photo-face-group"&&n.textContent.includes("待整理 · 1"))[0];
    group.open=true; group.fire("toggle"); return group;
  }
  for(const code of [409,500]) {
    faceWriteStatus=code;
    const failed=context.window.AIPhotoInfo.open(501,{onFacesChanged:()=>changedFaces.push("unexpected")});
    await flush(); await flush();
    all(pendingFace(failed),n=>n.tagName==="button"&&n.textContent==="新建未命名人物")[0].fire("click");
    await flush(); await flush();
    assert.equal(changedFaces.length,1,"rejected and unknown writes do not publish success notifications");
    failed.close();
  }
  faceWriteStatus=200;
  const ordinary=context.window.AIPhotoInfo.open(501);
  await flush(); await flush();
  all(pendingFace(ordinary),n=>n.tagName==="button"&&n.textContent==="暂时忽略")[0].fire("click");
  await flush(); await flush();
  assert.equal(changeTokens.length,1,"ordinary photo pages notify existing person views after a confirmed write");
  assert.equal(changeTokens[0].key,"aisearch.persons.changed");
  ordinary.close();

  let rejectRefresh;
  const refreshFailure=context.window.AIPhotoInfo.open(501,{onFacesChanged:()=>new Promise((resolve,reject)=>{rejectRefresh=reject;})});
  await flush(); await flush();
  all(pendingFace(refreshFailure),n=>n.tagName==="button"&&n.textContent==="新建未命名人物")[0].fire("click");
  await flush(); await flush();
  const successfulWrites=faceWrites.length;
  rejectRefresh(new Error("synthetic refresh failure")); await flush();
  assert.match(refreshFailure.panel.textContent,/人脸已保存；人物列表刷新失败/,"a delayed refresh failure remains visible after the manager is repainted");
  assert.doesNotMatch(refreshFailure.panel.textContent,/新建结果暂时无法确认/,"confirmed writes remain confirmed if a dependent read fails");
  assert.equal(faceWrites.length,successfulWrites,"a failed refresh does not resend the write");
  refreshFailure.close();

  const removeViewer=context.window.AIPhotoInfo.open(501); await flush(); await flush();
  all(removeViewer.panel,n=>n.className==="photo-face-manager-toggle")[0].fire("click");
  const assignedGroup=all(removeViewer.panel,n=>n.className==="photo-face-group"&&n.textContent.includes("已归属 · 1"))[0];
  assignedGroup.open=true; assignedGroup.fire("toggle");
  all(assignedGroup,n=>n.tagName==="button"&&n.textContent==="移出人物")[0].fire("click");
  await flush(); await flush();
  const unassign=faceWrites.at(-1);
  assert.match(unassign.url,/faces\/1{64}\/assignment$/);
  assert.equal(unassign.options.method,"PUT");
  assert.equal(JSON.parse(unassign.options.body).action,"unassign","removal uses the backend's authoritative action contract");
  assert.equal(JSON.parse(unassign.options.body).expected_revision,1);
  removeViewer.close();

  const ordered=context.window.AIPhotoInfo.render(host,401);
  await flush();await flush();
  const orderedPeople=all(host,n=>n.className==="photo-info-person");
  assert.deepEqual(orderedPeople.map(n=>n.textContent),["妈妈","爸爸"],"all shared photo viewers put the most matched photos first");
  ordered.dispose();

  const stale=context.window.AIPhotoInfo.render(host,201);
  const current=context.window.AIPhotoInfo.render(host,102);
  releaseDetail(); await flush(); await flush();
  assert.match(host.textContent,/海边\.jpg/); assert.equal(maps.length,1,"late A response must not create a map for B");
  stale.dispose(); current.dispose();

  const invalidHost=new Element("aside"); document.body.append(invalidHost);
  const noGps=context.window.AIPhotoInfo.render(invalidHost,301);
  await flush(); await flush(); noGps.dispose();
  assert.ok(!invalidHost.textContent.includes("拍摄位置"),"an asset outside the current GPS member set has no map");

  const partialPeopleHost=new Element("aside"); document.body.append(partialPeopleHost);
  const partialPeople=context.window.AIPhotoInfo.render(partialPeopleHost,302);
  await flush(); await flush();
  assert.match(partialPeopleHost.textContent,/前 200 张人脸中还没有已归属人物/);
  assert.match(partialPeopleHost.textContent,/还有更多人脸待检查/);
  partialPeople.dispose();

  const closedHost=new Element("aside"); document.body.append(closedHost);
  const pending=context.window.AIPhotoInfo.render(closedHost,201); await flush();
  pending.dispose(); releaseDetail(); await flush();
  assert.ok(!closedHost.textContent.includes("2020"),"late detail after close cannot revive the panel");

  const photoReturn=context.window.AIPhotoInfo.render(host,501);
  await flush(); await flush();
  all(host,n=>n.className==="photo-info-person")[0].fire("click");
  assert.equal(window.location.href,"/persons?person_id=7&from_photo=photo","shared photo viewers mark their person-navigation return context");
  photoReturn.dispose();

  const trigger=new Element("button"); document.body.append(trigger);
  let previous=0,next=0;
  const viewer=context.window.AIPhotoInfo.open(101,{returnFocus:trigger,navigation:{previous:()=>previous++,next:()=>next++}});
  await flush(); await flush();
  const dialog=document.body.children.find(n=>n.className==="photo-viewer-dialog");
  assert.ok(dialog.attributes["aria-labelledby"],"the shared viewer has an accessible title");
  const closeButton=all(dialog,n=>n.className==="photo-viewer-close")[0];
  assert.equal(closeButton.focused,true,"opening the viewer places keyboard focus on its visible close button");
  const viewerImage=all(dialog,n=>n.tagName==="img")[0];
  const stage=all(dialog,n=>n.className==="photo-viewer-stage")[0]; stage.clientWidth=800; stage.clientHeight=600;
  viewerImage.naturalWidth=1600; viewerImage.naturalHeight=1200;
  viewerImage.onerror();
  assert.match(viewerImage.src,/assets\/101\/thumbnail$/,"a missing preview falls back to the available thumbnail");
  viewerImage.onload();
  assert.match(all(dialog,n=>n.className==="photo-viewer-image-note")[0].textContent,/当前显示缩略图/);
  const zoom=all(dialog,n=>n.className==="photo-viewer-zoom-label")[0];
  assert.equal(zoom.textContent,"50%","opening a 1600x1200 preview fits the 800x600 stage without cropping");
  all(dialog,n=>n.tagName==="button"&&n.textContent==="+")[0].fire("click");
  assert.equal(zoom.textContent,"63%","zoom buttons use the 1.25 step and report the actual scale");
  all(dialog,n=>n.tagName==="button"&&n.textContent==="适合窗口")[0].fire("click");
  function nativeDoubleClick(id, firstDetail=1) {
    const clickTargets=[];
    for (let click=0; click<2; click++) {
      const pointerId=id+click;
      stage.fire("pointerdown",{target:viewerImage,pointerId,pointerType:"mouse",button:0,isPrimary:true,clientX:400,clientY:300});
      assert.equal(stage.hasPointerCapture(pointerId),false,"a click does not capture the pointer before a drag starts");
      stage.fire("pointerup",{target:viewerImage,pointerId,type:"pointerup"});
      const target=stage.hasPointerCapture(pointerId)?stage:viewerImage;
      clickTargets.push(target);
      stage.fire("click",{target,detail:firstDetail+click});
    }
    assert.equal(clickTargets[1],viewerImage,"the native second click remains targeted at the visible image");
    stage.fire("dblclick",{target:clickTargets[1],detail:2});
  }
  all(dialog,n=>n.tagName==="button"&&n.textContent==="适合窗口")[0].fire("click");
  nativeDoubleClick(20); assert.equal(zoom.textContent,"100%","two image click sequences switch fit to preview-pixel scale");
  nativeDoubleClick(22); assert.equal(zoom.textContent,"50%","the next image double click returns to fit");
  all(dialog,n=>n.tagName==="button"&&n.textContent==="预览 100%")[0].fire("click");
  nativeDoubleClick(24); assert.equal(zoom.textContent,"50%","a click entered at 100 percent also returns to fit on double click");
  stage.fire("dblclick",{target:stage,detail:2}); assert.equal(zoom.textContent,"50%","double clicking stage blank does not change zoom");
  all(dialog,n=>n.tagName==="button"&&n.textContent==="预览 100%")[0].fire("click");
  stage.fire("wheel",{target:viewerImage,ctrlKey:true,deltaY:-100,clientX:600,clientY:300,preventDefault(){this.prevented=true;}});
  assert.equal(zoom.textContent,"125%","Ctrl-wheel zooms while the pointer is on the image stage");
  const dragStart={target:viewerImage,pointerId:1,pointerType:"mouse",button:0,isPrimary:true,clientX:100,clientY:100};
  stage.fire("pointerdown",dragStart);
  assert.equal(stage.hasPointerCapture(1),false,"pointer capture waits until movement crosses the drag threshold");
  stage.fire("pointermove",{target:viewerImage,pointerId:1,clientX:102,clientY:100});
  assert.equal(stage.hasPointerCapture(1),false,"sub-threshold movement stays a click");
  stage.fire("pointermove",{target:viewerImage,pointerId:1,clientX:1200,clientY:100});
  assert.equal(stage.hasPointerCapture(1),true,"a real drag captures the pointer after crossing the threshold");
  assert.match(viewerImage.style.transform,/translate\(600px,0px\)/,"drag pan is clamped at the image boundary");
  stage.fire("pointerup",{target:stage,pointerId:1,type:"pointerup"});
  assert.equal(stage.hasPointerCapture(1),false,"pointer capture is released when dragging ends");
  stage.fire("click",{target:stage,detail:1});
  stage.fire("pointerdown",{target:viewerImage,pointerId:2,pointerType:"mouse",button:0,isPrimary:true,clientX:400,clientY:300});
  stage.fire("pointerup",{target:viewerImage,pointerId:2,type:"pointerup"});
  stage.fire("click",{target:viewerImage,detail:2});
  stage.fire("dblclick",{target:stage,detail:2});
  assert.equal(zoom.textContent,"125%","a drag followed by a click pair cannot synthesize a zoom toggle, even when capture retargets it to the stage");
  nativeDoubleClick(30); assert.equal(zoom.textContent,"50%","a clean native double click works again after a drag");
  viewer.setNavigationEnabled(true,true);
  viewer.setPosition("第 2 张");
  assert.equal(viewer.controls.children[5].textContent,"← 上一张","setting position does not overwrite the previous button label");
  assert.equal(viewer.controls.children[6].textContent,"第 2 张","the position status is rendered in its own navigation slot");
  dialog.fire("keydown",{target:closeButton,key:"ArrowRight",preventDefault(){this.prevented=true;}});
  assert.equal(next,1,"right arrow advances the viewer sequence");
  const input=new Element("INPUT"); dialog.fire("keydown",{target:input,key:"ArrowRight",preventDefault(){}});
  assert.equal(next,1,"arrow keys in text inputs do not hijack their editing behavior");
  dialog.fire("click",{target:dialog.children[0]});
  assert.equal(dialog.open,true,"clicks inside the photo viewer do not dismiss it");
  dialog.fire("click",{target:dialog});
  assert.equal(dialog.open,false,"clicking the backdrop dismisses the shared viewer");
  assert.equal(trigger.focused,true,"closing the viewer restores focus to its opener");
  await flush();
  assert.equal(maps.at(-1).removed,true,"closing the viewer removes its map instance");

  const staleActionHost=new Element("aside"); document.body.append(staleActionHost);
  const pendingAction=context.window.AIPhotoInfo.render(staleActionHost,101); await flush(); await flush();
  deferLocal=true;
  all(staleActionHost,n=>n.className==="photo-info-local-action"&&n.textContent==="定位文件位置")[0].fire("click");
  await flush(); pendingAction.dispose(); staleActionHost.replaceChildren(new Element("p")); releaseLocal(); await flush();
  assert.ok(!staleActionHost.textContent.includes("已请求资源管理器定位"),"a response after dispose cannot update the replacement panel");

  const savedValues=new Map(); let historyPushes=0, historyBacks=0; const replacements=[];
  window.location={href:"http://localhost/search?q=lake",origin:"http://localhost",replace:path=>replacements.push(path)};
  window.sessionStorage={getItem:key=>savedValues.get(key)||null,setItem:(key,value)=>savedValues.set(key,value),removeItem:key=>savedValues.delete(key)};
  const historyStack=[{state:null}], popstateListeners=new Set(); let historyIndex=0;
  window.history={
    get state(){return historyStack[historyIndex].state;},
    pushState(state){historyPushes++;historyStack.splice(historyIndex+1);historyStack.push({state});historyIndex++;},
    back(){historyBacks++;if(historyIndex){historyIndex--;popstateListeners.forEach(listener=>listener({state:this.state}));}},
    forward(){if(historyIndex+1<historyStack.length){historyIndex++;popstateListeners.forEach(listener=>listener({state:this.state}));}},
  };
  window.addEventListener=(name,listener)=>{if(name==="popstate")popstateListeners.add(listener);};
  window.removeEventListener=(name,listener)=>{if(name==="popstate")popstateListeners.delete(listener);};
  const blocked=context.window.AIPhotoInfo.open(101,{beforeClose:()=>false,onOpenFullDetails:()=>assert.fail("busy viewer must not navigate")});
  const blockedDetails=all(blocked.dialog,n=>n.className==="photo-viewer-details")[0];
  blockedDetails.fire("click"); assert.equal(replacements.length,0,"beforeClose blocks full-details navigation");
  blocked.close(); assert.equal(historyBacks,0,"a rejected close preserves its owned history entry");
  blocked.dialog.close(); await flush();
  assert.equal(historyBacks,1,"ordinary close keeps the viewer's existing history behavior");
  historyStack[historyIndex].state={searchHistoryEntry:true,searchHistoryEntryId:"search-entry-test",searchSnapshotToken:"search-snapshot-test"};
  const backForwardViewer=context.window.AIPhotoInfo.open(105);
  assert.equal(window.history.state.searchHistoryEntryId,"search-entry-test","the viewer marker preserves the owning search history entry");
  assert.equal(window.history.state.searchSnapshotToken,"search-snapshot-test","the viewer marker preserves the owning search snapshot");
  assert.equal(popstateListeners.size,1,"opening a new viewer releases the closed viewer listener whose Forward entry was replaced");
  window.history.back();
  assert.equal(backForwardViewer.dialog.open,false,"Back closes the viewer and returns to its prior page state");
  window.history.forward();
  assert.equal(backForwardViewer.dialog.open,true,"Forward reopens the viewer state without adding another history entry");
  assert.equal(historyPushes,2,"restoring a viewer through Forward reuses its existing history entry");
  backForwardViewer.close();
  assert.equal(historyBacks,3,"closing a Forward-restored viewer returns to its prior page state");
  historyStack.splice(historyIndex+1); historyStack.push({state:{searchHistoryEntry:true,searchHistoryEntryId:"search-entry-restored",searchSnapshotToken:"search-snapshot-restored",photoViewerKey:"restored-search-viewer"}}); historyIndex++;
  const beforeRestoredViewerPushes=historyPushes;
  const restoredSearchViewer=context.window.AIPhotoInfo.open(106,{historyEntryKey:"restored-search-viewer"});
  assert.equal(historyPushes,beforeRestoredViewerPushes,"restoring a viewer from a browser history entry reuses its marker");
  window.history.back();
  assert.equal(restoredSearchViewer.dialog.open,false,"Back from a restored viewer closes it to the saved search state");
  window.history.forward();
  assert.equal(restoredSearchViewer.dialog.open,true,"Forward reopens the restored viewer from the same history marker");
  assert.equal(historyPushes,beforeRestoredViewerPushes,"reopening the restored viewer does not add a duplicate history entry");
  restoredSearchViewer.close();
  const detailNavigationBacks=historyBacks;
  const navigated=[];
  const changing=context.window.AIPhotoInfo.open(101,{onOpenFullDetails:id=>{navigated.push(id);context.window.AIPhotoInfo.navigateToDetails(id,"search");}});
  changing.setAsset(202);
  const detailsButton=all(changing.dialog,n=>n.className==="photo-viewer-details")[0];
  detailsButton.fire("click"); detailsButton.fire("click");
  assert.equal(historyBacks,detailNavigationBacks,"full-details navigation replaces the viewer marker without going Back");
  assert.deepEqual(navigated,["202"],"the details entry uses the live setAsset identity and rejects a second trigger");
  assert.equal(changing.dialog.open,false,"successful details navigation releases the current modal");
  assert.equal(historyPushes,3,"each viewer owns one modal history entry");
  assert.deepEqual(replacements,["/#/asset/202/details"],"navigation replaces the modal entry with the explicit details route");
  const returnIntent=context.window.AIPhotoInfo.consumeDetailReturn("202");
  assert.equal(returnIntent.source,"search"); assert.equal(returnIntent.path,"/search?q=lake","a one-time same-origin source path is available for safe return");
  assert.equal(returnIntent.requiresBackstop,false,"a viewer-owned history transition already has an in-app predecessor");
  assert.equal(context.window.AIPhotoInfo.consumeDetailReturn("202"),null,"the source return intent is consumed once");
  assert.equal(context.window.AIPhotoInfo.restoreDetailReturn("202",returnIntent),true,"return-to-viewer can re-arm the validated source intent");
  assert.equal(context.window.AIPhotoInfo.consumeDetailReturn("202").path,"/search?q=lake");
  let favoriteChanges=0;
  const favoriteViewer=context.window.AIPhotoInfo.open(601,{history:false,onFavoriteChanged:()=>favoriteChanges++});
  await flush(); await flush();
  const favoriteButton=all(favoriteViewer.dialog,n=>n.className==="photo-viewer-favorite")[0];
  assert.equal(favoriteButton.attributes["aria-pressed"],"false");
  assert.equal(favoriteButton.disabled,false,"authoritative state enables the favorite action");
  favoriteButton.onclick(); await flush(); await flush();
  assert.equal(favoriteButton.attributes["aria-pressed"],"true","successful write updates accessible state");
  assert.equal(favoriteChanges,1,"the active asset write notifies its owning page once");
  favoriteViewer.close();
  const racingViewer=context.window.AIPhotoInfo.open(701,{history:false});
  racingViewer.setAsset(702);
  await flush(); await flush();
  releaseFavoriteState(); await flush(); await flush();
  const racingButton=all(racingViewer.dialog,n=>n.className==="photo-viewer-favorite")[0];
  assert.equal(racingButton.attributes["aria-pressed"],"false","a late favorite state for the prior asset cannot repaint the current asset");
  racingViewer.close();
  const panViewer=context.window.AIPhotoInfo.open(801,{history:false});
  const panStage=all(panViewer.dialog,n=>n.className==="photo-viewer-stage")[0];
  panStage.clientWidth=800; panStage.clientHeight=600;
  panViewer.image.naturalWidth=1200; panViewer.image.naturalHeight=1600; panViewer.image.onload();
  panViewer.controls.children[2].fire("click");
  assert.match(panViewer.image.style.transform,/translate\(-50%,-50%\)/,"image position uses an explicit stage-center origin");
  panStage.fire("pointerdown",{target:panViewer.image,pointerId:2,pointerType:"mouse",button:2,isPrimary:true,clientX:0,clientY:0});
  assert.equal(panStage.hasPointerCapture(2),false,"secondary mouse buttons do not start a drag");
  panStage.fire("pointerdown",{target:panViewer.image,pointerId:2,pointerType:"touch",isPrimary:false,clientX:0,clientY:0});
  assert.equal(panStage.hasPointerCapture(2),false,"secondary touch pointers do not start a drag");
  panStage.fire("pointerdown",{target:panViewer.image,pointerId:3,pointerType:"mouse",button:0,isPrimary:true,clientX:0,clientY:0});
  panStage.fire("pointermove",{pointerId:3,clientX:0,clientY:-1000});
  assert.match(panViewer.image.style.transform,/translate\(0px,-500px\)$/,"vertical pan reaches the lower photo edge without extra blank space");
  panStage.fire("pointermove",{pointerId:3,clientX:0,clientY:1000});
  assert.match(panViewer.image.style.transform,/translate\(0px,500px\)$/,"vertical pan reaches the upper photo edge without extra blank space");
  assert.equal(panStage.hasPointerCapture(3),true);
  panStage.fire("pointercancel",{pointerId:3});
  assert.equal(panStage.hasPointerCapture(3),false,"pointer cancellation releases capture");
  panViewer.setAsset(802);
  panViewer.image.naturalWidth=600; panViewer.image.naturalHeight=2400; panViewer.image.onload();
  assert.equal(panViewer.image.style.width,"150px","long portrait fits the stage in fit mode");
  panStage.clientWidth=400; panStage.clientHeight=300; resizeObservers.at(-1).callback();
  assert.equal(panViewer.image.style.width,"75px","fit mode recomputes after the available stage shrinks");
  panStage.clientWidth=800; panStage.clientHeight=600; resizeObservers.at(-1).callback();
  assert.equal(panViewer.image.style.width,"150px");
  panViewer.controls.children[2].fire("click");
  panStage.fire("pointerdown",{target:panViewer.image,pointerId:4,pointerType:"touch",isPrimary:true,clientX:0,clientY:0});
  panStage.fire("pointermove",{pointerId:4,clientX:0,clientY:-2000});
  assert.match(panViewer.image.style.transform,/translate\(0px,-900px\)$/,"long portrait can pan to its lower edge");
  panStage.fire("pointerdown",{target:panViewer.image,pointerId:5,pointerType:"mouse",button:0,isPrimary:true,clientX:0,clientY:0});
  assert.equal(panStage.hasPointerCapture(5),false,"a second pointer cannot steal an active drag");
  panViewer.setAsset(803);
  assert.equal(panStage.hasPointerCapture(4),false,"changing assets releases the old pointer capture");
  panViewer.image.naturalWidth=1600; panViewer.image.naturalHeight=1200; panViewer.image.onload();
  panViewer.controls.children[2].fire("click");
  panStage.fire("pointerdown",{target:panViewer.image,pointerId:6,pointerType:"mouse",button:0,isPrimary:true,clientX:0,clientY:0});
  panStage.fire("pointermove",{pointerId:6,clientX:2000,clientY:2000});
  assert.match(panViewer.image.style.transform,/translate\(400px,300px\)$/,"landscape pan reaches the opposite horizontal and vertical edges");
  panStage.clientWidth=1200; panStage.clientHeight=900; resizeObservers.at(-1).callback();
  assert.equal(panViewer.image.style.width,"1600px","zoom mode preserves the selected scale after resize");
  assert.match(panViewer.image.style.transform,/translate\(200px,150px\)$/,"zoom mode clamps to the new stage edges");
  assert.equal(panViewer.image.style.width,"1600px","zoom mode preserves the selected scale after resize");
  panStage.fire("pointerup",{pointerId:6});
  assert.equal(panStage.hasPointerCapture(6),false,"pointer release clears capture");
  panViewer.setAsset(804);
  panViewer.image.naturalWidth=120; panViewer.image.naturalHeight=80; panViewer.image.onload();
  assert.equal(panViewer.image.style.width,"120px","small images remain at 100% in fit mode");
  const panObserver=resizeObservers.at(-1); panViewer.close();
  assert.equal(panObserver.disconnected,true,"closing the viewer disconnects its resize observer");
  window.__aiPhotoDetailHandoff=null; document.referrer="http://localhost/elsewhere";
  savedValues.set("aisearch.photo-detail-return.v1",JSON.stringify({assetId:"202",source:"search",path:"/search?q=lake",requiresBackstop:false,handoffKey:"stale",savedAt:Date.now()}));
  assert.equal(context.window.AIPhotoInfo.consumeDetailReturn("202").invalid,true,"a stale same-asset session intent without the current transition or matching referrer is rejected");
  assert.equal(savedValues.has("aisearch.photo-detail-return.v1"),false,"rejected stale intents are cleared once");
  window.sessionStorage={getItem(){throw new Error("storage unavailable");},removeItem(){throw new Error("storage unavailable");},setItem(){throw new Error("storage unavailable");}};
  assert.equal(context.window.AIPhotoInfo.consumeDetailReturn("202").invalid,true,"storage denial is distinguishable from a direct route with no source intent");
  changing.dialog.close(); assert.equal(historyBacks,detailNavigationBacks,"details navigation does not leave a modal history callback behind");
})().catch(error=>{console.error(error);process.exitCode=1;});
