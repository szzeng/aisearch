"""Focused photo timeline controls and scope-local state checks for PHOTOS-B."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "src" / "photo_search" / "web"


def test_photos_default_to_timeline_and_keep_list_and_management_actions() -> None:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    app = (WEB / "app.js").read_text(encoding="utf-8")
    styles = (WEB / "styles.css").read_text(encoding="utf-8")
    timeline_styles = (WEB / "photo-timeline.css").read_text(encoding="utf-8")

    assert 'class="view photos-page"' in html
    assert 'id="browse-show-list" aria-pressed="false"' in html
    assert 'id="browse-show-timeline" aria-pressed="true" hidden' in html
    assert 'id="browse-list-filters" hidden' in html
    assert 'id="browse-list-time-hint" class="hint" hidden' in html
    assert ".browse-controls #browse-list-filters[hidden] {\n  display: none;\n}" in timeline_styles
    assert 'photo-timeline.css?v=p4-ui2-timeline-controls-fix-a-1' in html
    assert 'class="view gallery-management-page"' in html
    assert '查看完整校验扫描' not in html  # Management stays in the per-source details.
    assert 'mode: "timeline"' in app
    assert "onRequest: function ()" in app and "timelineEmptyParams = null" in app
    assert 'id="browse-show-list"' in html and "browseCaptureFrom" in app
    assert "source-primary-actions" in app and "source-primary" in app
    assert 'method: "PATCH"' in app and 'body: { display_name: name }' in app
    assert 'request("/library/refresh"' in app
    assert 'request("/sources/" + encodeURIComponent(sourceId) + "/scan", { method: "POST" })' in app
    assert 'item.filename || "文件信息不可用"' in app
    assert 'card.title = directory + filename' in app
    assert ".photo-timeline__filename" in timeline_styles and "text-overflow: ellipsis" in timeline_styles
    assert ".photos-page .browse-mode-controls" in styles
    assert ".gallery-management-page" in styles
    assert ".photo-timeline__buckets" in timeline_styles
    assert 'element("button", "photo-timeline__summary-retry"' in (WEB / "photo-timeline.js").read_text(encoding="utf-8")
    assert "按加入图库日期" in (WEB / "photo-timeline.js").read_text(encoding="utf-8")


def test_timeline_preferences_are_global_and_date_state_stays_with_source_scope() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the synthetic timeline behavior check"
    script = r"""
const fs = require("fs"), vm = require("vm"), assert = require("assert/strict");
class Element {
  constructor(tag) {
    this.tagName=tag; this.children=[]; this.events={}; this.attrs={}; this.dataset={}; this.style={};
    this.value=""; this.hidden=false; this.textContent=""; this.className="";
    this.classList={add:n=>{if(!this.className.split(" ").includes(n))this.className=(this.className+" "+n).trim();},
      remove:n=>{this.className=this.className.split(" ").filter(v=>v!==n).join(" ");},
      contains:n=>this.className.split(" ").includes(n),
      toggle:(n,on)=>{if(on)this.classList.add(n);else this.classList.remove(n);}};
  }
  append(...nodes){nodes.forEach(n=>{if(n){this.children.push(n);n.parent=this;}});}
  appendChild(n){this.append(n);return n;}
  replaceChildren(...nodes){this.children=[];this.textContent="";this.append(...nodes);}
  replaceChild(next,old){const i=this.children.indexOf(old);if(i>=0){this.children[i]=next;next.parent=this;}return old;}
  addEventListener(name,fn){this.events[name]=fn;}
  fire(name){if(this.events[name])this.events[name]({target:this,currentTarget:this,preventDefault(){}});}
  setAttribute(k,v){this.attrs[k]=String(v);}
  getAttribute(k){return this.attrs[k]||null;}
  getBoundingClientRect(){return {top:10,bottom:150,height:140};}
  scrollIntoView(){}
  querySelectorAll(selector){
    const cls=selector.startsWith(".")?selector.slice(1):null, found=[];
    const visit=n=>{for(const child of n.children){if(cls&&child.classList.contains(cls))found.push(child);visit(child);}};
    visit(this);return found;
  }
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
}
const storage=new Map([["photo-timeline.preferences.v1",JSON.stringify({timeBasis:"library",groupBy:"month"})]]);
const requests=[];
const opened=[];
const window={innerHeight:900,localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)},
  addEventListener(){},removeEventListener(){},scrollBy(){},requestAnimationFrame:fn=>fn()};
const document={cookie:"",createElement:tag=>new Element(tag)};
const pageItem=(id,anchor,key,state="trusted")=>({asset_id:id,anchor,group_key:key,date_state:state,
  display_time:key||"加入图库日期",time_label:"照片日期",thumbnail_url:"data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'/>"});
const fetch=async path=>{
  requests.push(String(path));
  const url=new URL(String(path),"http://local");
  if(url.pathname.endsWith("/timeline/periods"))return{ok:true,json:async()=>({years:[{year:2022,count:2}],months:[{month:2,count:2}],counts:{effective_total:2,trusted:2,unknown:0,pending:0},catalog_filter_revision:1})};
  const kind=url.searchParams.get("kind"),source=url.searchParams.get("source_id"),bucket=url.searchParams.get("bucket");
  const anchor=source?"source-asset":"all-asset";
  const row=bucket==="unknown"?pageItem(2,"unknown-asset",null,"unknown"):pageItem(source?3:1,anchor,"2022-02-28");
  return{ok:true,json:async()=>({assets:[row],next_cursor:null,catalog_filter_revision:1})};
};
const context={window,document,fetch,URLSearchParams,AbortController,URL,Promise,console,CSS:{escape:String},setTimeout,clearTimeout};
vm.createContext(context);vm.runInContext(fs.readFileSync("src/photo_search/web/photo-timeline.js","utf8"),context);
const host=new Element("div"),mount=context.window.PhotoTimeline.mount({host,scope:{kind:"all"},adapter:{
  renderAsset:item=>{const card=new Element("button");card.className="photo-timeline__card";card.dataset.assetId=String(item.asset_id);return card;},
  onOpen:item=>opened.push(item.asset_id)
}});
const settle=async()=>{for(let i=0;i<8;i++)await Promise.resolve();await new Promise(resolve=>setTimeout(resolve,0));};
const find=(selector)=>host.querySelector(selector);
(async()=>{
  await settle();
  host.querySelectorAll(".photo-timeline__card")[0].fire("click");
  assert.deepEqual(opened,[1],"custom timeline cards forward exactly one open event");
  assert.equal(mount.snapshot().timeBasis,"library");
  assert.equal(mount.snapshot().groupBy,"month");
  assert.match(requests[0],/time_basis=library/);
  const unknown=find(".photo-timeline__bucket").parent.children.find(b=>b.dataset.bucket==="unknown");
  unknown.fire("click");await settle();
  assert.equal(mount.snapshot().timeBasis,"capture");
  assert.equal(mount.snapshot().bucket,"unknown");
  assert.equal(JSON.parse(storage.get("photo-timeline.preferences.v1")).timeBasis,"capture");
  const pending=find(".photo-timeline__bucket").parent.children.find(b=>b.dataset.bucket==="pending");
  pending.fire("click");await settle();assert.equal(mount.snapshot().bucket,"pending");
  unknown.fire("click");await settle();
  const grouping=find(".photo-timeline__group");grouping.value="year";grouping.fire("change");await settle();
  assert.equal(mount.snapshot().groupBy,"year");
  assert.equal(JSON.parse(storage.get("photo-timeline.preferences.v1")).groupBy,"year");
  const year=find(".photo-timeline__year"),month=find(".photo-timeline__month");
  year.value="2022";year.fire("change");await settle();month.value="2";
  find(".photo-timeline__controls").children.find(b=>b.textContent==="\u7b5b\u9009\u5e74\u6708").fire("click");await settle();
  assert.equal(mount.snapshot().year,2022);assert.equal(mount.snapshot().month,2);
  const allAnchor=host.querySelectorAll(".photo-timeline__card")[0].dataset.anchor;
  await mount.setScope({kind:"source",source_id:7});await settle();
  assert.equal(mount.snapshot().scope.source_id,7);
  assert.equal(mount.snapshot().year,null,"all-photo year filter does not leak into a source");
  const sourceRequest=requests.filter(path=>path.includes("/timeline/assets")&&path.includes("source_id=7")).at(-1);
  assert.doesNotMatch(sourceRequest,/year=2022|month=2/);
  await mount.setScope({kind:"all"});await settle();
  assert.equal(mount.snapshot().year,2022);assert.equal(mount.snapshot().month,2);
  const restored=requests.filter(path=>path.includes("/timeline/assets")&&!path.includes("source_id=")).at(-1);
  assert.match(restored,/anchor=unknown-asset/);
  assert.equal(allAnchor,"unknown-asset");
  const unknownHeading=host.querySelectorAll(".photo-timeline__heading").find(h=>h.textContent.includes("\u65e5\u671f\u672a\u77e5"));
  assert.ok(unknownHeading,"unknown dates are headed as library-date groups");
  year.value="2022";year.fire("change");await settle();month.value="2";
  find(".photo-timeline__controls").children.find(b=>b.textContent==="\u5b9a\u4f4d\u5230\u5e74\u6708").fire("click");await settle();
  const seek=requests.filter(path=>path.includes("/timeline/assets?")).at(-1);
  assert.match(seek,/seek_year=2022/);assert.match(seek,/seek_month=2/);assert.doesNotMatch(seek,/\b(?:year|month)=2022/);
  find(".photo-timeline__bucket").parent.children.find(b=>b.dataset.bucket==="library").fire("click");await settle();
  assert.equal(mount.snapshot().timeBasis,"library");assert.equal(mount.snapshot().bucket,"all");
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=10
    )
    assert result.returncode == 0, result.stderr


def test_timeline_keeps_a_bounded_window_after_thirty_one_pages() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the synthetic page-window check"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
class Element {
  constructor(tag){this.tagName=tag;this.children=[];this.events={};this.attrs={};this.dataset={};this.style={height:""};this.className="";this.textContent="";this.hidden=false;this.value="";
    this.classList={add:n=>{if(!this.className.split(" ").includes(n))this.className=(this.className+" "+n).trim();},remove:n=>{this.className=this.className.split(" ").filter(v=>v!==n).join(" ");},contains:n=>this.className.split(" ").includes(n),toggle:(n,on)=>on?this.classList.add(n):this.classList.remove(n)};}
  get firstChild(){return this.children[0]||null;}
  get nextElementSibling(){if(!this.parent)return null;return this.parent.children[this.parent.children.indexOf(this)+1]||null;}
  append(...nodes){nodes.forEach(n=>{if(n){this.children.push(n);n.parent=this;}});}
  appendChild(n){this.append(n);return n;}
  replaceChildren(...nodes){this.children=[];this.textContent="";this.append(...nodes);}
  replaceChild(next,old){const i=this.children.indexOf(old);if(i>=0){this.children[i]=next;next.parent=this;}return old;}
  insertBefore(next,old){const i=this.children.indexOf(old);if(i<0)this.append(next);else{this.children.splice(i,0,next);next.parent=this;}return next;}
  remove(){if(this.parent){this.parent.children=this.parent.children.filter(n=>n!==this);this.parent=null;}}
  contains(){return false;}
  addEventListener(n,fn){this.events[n]=fn;}
  setAttribute(k,v){this.attrs[k]=String(v);}
  getBoundingClientRect(){return{top:10,bottom:30,height:20};}
  querySelectorAll(selector){const cls=selector.startsWith(".")?selector.slice(1):null,found=[];const visit=n=>{for(const c of n.children){if(cls&&c.classList.contains(cls))found.push(c);visit(c);}};visit(this);return found;}
  querySelector(s){return this.querySelectorAll(s)[0]||null;}
}
let page=0;
const window={innerHeight:800,localStorage:{getItem(){return null;},setItem(){}},addEventListener(){},removeEventListener(){},scrollBy(){},requestAnimationFrame:fn=>fn()};
const document={cookie:"",activeElement:null,createElement:t=>new Element(t)};
const fetch=async path=>({ok:true,json:async()=>String(path).includes("/periods")?{years:[],months:[],counts:{effective_total:31,trusted:31,unknown:0,pending:0},catalog_filter_revision:1}:{assets:[],catalog_filter_revision:1}});
const context={window,document,fetch,URLSearchParams,AbortController,URL,Promise,console,CSS:{escape:String},setTimeout,clearTimeout};
vm.createContext(context);vm.runInContext(fs.readFileSync("src/photo_search/web/photo-timeline.js","utf8"),context);
const host=new Element("div"),mount=context.window.PhotoTimeline.mount({host,scope:{kind:"all"},adapter:{fetchPage:async()=>{page++;return{assets:[{asset_id:page,anchor:"a"+page,group_key:"2022-01-01",date_state:"trusted",display_time:"2022-01-01"}],next_cursor:page<31?"c"+page:null,catalog_filter_revision:1};},renderAsset:item=>{const card=new Element("button");card.className="photo-timeline__card";card.dataset.assetId=String(item.asset_id);return card;}}});
const settle=async()=>{for(let i=0;i<8;i++)await Promise.resolve();await new Promise(resolve=>setTimeout(resolve,0));};
(async()=>{await settle();for(let i=1;i<31;i++){await mount.append();await settle();}
  const state=mount.snapshot(),groups=host.querySelector(".photo-timeline__groups");
  assert.equal(page,31);assert.ok(state.activePages<=8);assert.ok(state.detailPages<=10);assert.ok(state.anchorPages<=64);
  assert.ok(groups.querySelectorAll(".photo-timeline__page").length<=8);
  assert.ok(groups.querySelectorAll(".photo-timeline__spacer").length<=1);
  assert.ok(groups.querySelectorAll(".photo-timeline__card").length<=8);
  assert.equal(groups.querySelectorAll(".photo-timeline__heading").length,1,"one same-day heading spans all 31 pages");
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=10
    )
    assert result.returncode == 0, result.stderr


def test_timeline_discards_a_late_response_after_scope_change() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the synthetic stale-response check"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
class Element {
  constructor(){this.children=[];this.events={};this.attrs={};this.dataset={};this.style={};this.className="";this.textContent="";this.hidden=false;this.value="";this.classList={add:n=>{this.className=(this.className+" "+n).trim();},remove:n=>{this.className=this.className.split(" ").filter(v=>v!==n).join(" ");},contains:n=>this.className.split(" ").includes(n),toggle:(n,on)=>on?this.classList.add(n):this.classList.remove(n)};}
  append(...nodes){nodes.forEach(n=>{if(n){this.children.push(n);n.parent=this;}});}
  replaceChildren(...nodes){this.children=[];this.textContent="";this.append(...nodes);}
  replaceChild(next,old){const i=this.children.indexOf(old);if(i>=0){this.children[i]=next;next.parent=this;}return old;}
  addEventListener(n,fn){this.events[n]=fn;}
  fire(n){if(this.events[n])this.events[n]({target:this,currentTarget:this,preventDefault(){}});}
  setAttribute(k,v){this.attrs[k]=String(v);}
  getBoundingClientRect(){return{top:0,bottom:10,height:10};}
  querySelectorAll(selector){const cls=selector.startsWith(".")?selector.slice(1):null,out=[];const visit=n=>{for(const c of n.children){if(cls&&c.classList.contains(cls))out.push(c);visit(c);}};visit(this);return out;}
  querySelector(s){return this.querySelectorAll(s)[0]||null;}
}
const window={innerHeight:800,localStorage:{getItem(){return null;}},addEventListener(){},removeEventListener(){},scrollBy(){},requestAnimationFrame:fn=>fn()};
const document={cookie:"",createElement:()=>new Element()};
const fetch=async()=>({ok:true,json:async()=>({years:[],months:[],counts:{effective_total:1,trusted:1,unknown:0,pending:0},catalog_filter_revision:1})});
const context={window,document,fetch,URLSearchParams,AbortController,URL,Promise,console,CSS:{escape:String},setTimeout,clearTimeout};
vm.createContext(context);vm.runInContext(fs.readFileSync("src/photo_search/web/photo-timeline.js","utf8"),context);
let releaseFirst,requestCount=0;
const host=new Element(),mount=context.window.PhotoTimeline.mount({host,scope:{kind:"all"},adapter:{fetchPage:params=>{requestCount++;const id=params.bucket==="unknown"?2:(params.kind==="source"?3:1);const item={asset_id:id,anchor:params.kind==="source"?"source":"all",group_key:params.bucket==="unknown"?null:"2022-01-01",date_state:params.bucket==="unknown"?"unknown":"trusted"};const body={assets:[item],next_cursor:null,catalog_filter_revision:1};return requestCount===1?new Promise(resolve=>{releaseFirst=()=>resolve(body);}):Promise.resolve(body);},renderAsset:item=>{const card=new Element();card.className="photo-timeline__card";card.dataset.assetId=String(item.asset_id);return card;}}});
(async()=>{const unknown=host.querySelectorAll(".photo-timeline__bucket").find(button=>button.dataset.bucket==="unknown");unknown.fire("click");await new Promise(resolve=>setTimeout(resolve,0));releaseFirst();await new Promise(resolve=>setTimeout(resolve,0));
  let ids=host.querySelectorAll(".photo-timeline__card").map(card=>card.dataset.assetId);
  assert.deepEqual(ids,["2"],"a late all-photo response cannot replace the newly selected date bucket");
  await mount.setScope({kind:"source",source_id:4});ids=host.querySelectorAll(".photo-timeline__card").map(card=>card.dataset.assetId);
  assert.deepEqual(ids,["3"]);assert.equal(mount.snapshot().scope.source_id,4);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=10
    )
    assert result.returncode == 0, result.stderr


def test_timeline_keeps_summary_failure_visible_and_retryable() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the synthetic summary failure check"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
class Element {
  constructor(){this.children=[];this.events={};this.attrs={};this.dataset={};this.style={};this.className="";this.textContent="";this.hidden=false;this.value="";this.classList={add:n=>{if(!this.className.split(" ").includes(n))this.className=(this.className+" "+n).trim();},remove:n=>{this.className=this.className.split(" ").filter(v=>v!==n).join(" ");},contains:n=>this.className.split(" ").includes(n),toggle:(n,on)=>on?this.classList.add(n):this.classList.remove(n)};}
  append(...nodes){nodes.forEach(n=>{if(n){this.children.push(n);n.parent=this;}});}
  replaceChildren(...nodes){this.children=[];this.textContent="";this.append(...nodes);}
  addEventListener(n,fn){this.events[n]=fn;}
  fire(n){if(this.events[n])this.events[n]({target:this,currentTarget:this,preventDefault(){}});}
  setAttribute(k,v){this.attrs[k]=String(v);}
  getBoundingClientRect(){return{height:100,top:0,bottom:100};}
  querySelectorAll(selector){const cls=selector.startsWith(".")?selector.slice(1):null,out=[];const visit=n=>{for(const c of n.children){if(cls&&c.classList.contains(cls))out.push(c);visit(c);}};visit(this);return out;}
  querySelector(s){return this.querySelectorAll(s)[0]||null;}
}
let summaryCalls=0;
const window={innerHeight:800,localStorage:{getItem(){return null;}},addEventListener(){},removeEventListener(){},scrollBy(){},requestAnimationFrame:fn=>fn()};
const document={cookie:"",createElement:()=>new Element()};
const fetch=async path=>{if(String(path).includes("/periods")){summaryCalls++;return{ok:false,json:async()=>({message:"offline"})};}return{ok:true,json:async()=>({assets:[],next_cursor:null,catalog_filter_revision:1})};};
const context={window,document,fetch,URLSearchParams,AbortController,URL,Promise,console,CSS:{escape:String},setTimeout,clearTimeout};
vm.createContext(context);vm.runInContext(fs.readFileSync("src/photo_search/web/photo-timeline.js","utf8"),context);
const host=new Element();context.window.PhotoTimeline.mount({host,scope:{kind:"all"}});
const settle=async()=>{for(let i=0;i<8;i++)await Promise.resolve();await new Promise(resolve=>setTimeout(resolve,0));};
(async()=>{await settle();const retry=host.querySelector(".photo-timeline__summary-retry"),status=host.querySelector(".photo-timeline__summary-status");
  assert.equal(summaryCalls,1);assert.equal(retry.hidden,false);assert.match(status.textContent,/日期摘要暂不可用/);
  const all=host.querySelectorAll(".photo-timeline__bucket").find(button=>button.dataset.bucket==="all");assert.doesNotMatch(all.textContent,/· 0$/);
  retry.fire("click");await settle();assert.equal(summaryCalls,2);assert.equal(retry.hidden,false);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=10
    )
    assert result.returncode == 0, result.stderr


def test_year_summary_refresh_preserves_photo_page_error() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the synthetic timeline error-state check"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
class Element {
  constructor(){this.children=[];this.events={};this.attrs={};this.dataset={};this.style={};this.className="";this.textContent="";this.hidden=false;this.value="";this.classList={add:n=>{if(!this.className.split(" ").includes(n))this.className=(this.className+" "+n).trim();},remove:n=>{this.className=this.className.split(" ").filter(v=>v!==n).join(" ");},contains:n=>this.className.split(" ").includes(n),toggle:(n,on)=>on?this.classList.add(n):this.classList.remove(n)};}
  append(...nodes){nodes.forEach(n=>{if(n){this.children.push(n);n.parent=this;}});}
  replaceChildren(...nodes){this.children=[];this.textContent="";this.append(...nodes);}
  addEventListener(n,fn){this.events[n]=fn;}
  fire(n){if(this.events[n])this.events[n]({target:this,currentTarget:this,preventDefault(){}});}
  setAttribute(k,v){this.attrs[k]=String(v);}
  querySelectorAll(selector){const cls=selector.startsWith(".")?selector.slice(1):null,out=[];const visit=n=>{for(const c of n.children){if(cls&&c.classList.contains(cls))out.push(c);visit(c);}};visit(this);return out;}
  querySelector(s){return this.querySelectorAll(s)[0]||null;}
}
let periodCalls=0;
const window={innerHeight:800,localStorage:{getItem(){return null;}},addEventListener(){},removeEventListener(){},scrollBy(){},requestAnimationFrame:fn=>fn()};
const document={cookie:"",createElement:()=>new Element()};
const fetch=async path=>{if(String(path).includes("/periods")){periodCalls++;return{ok:true,json:async()=>({years:[{year:2022,count:1}],months:[],counts:{effective_total:1,trusted:1,unknown:0,pending:0},catalog_filter_revision:1})};}return{ok:false,json:async()=>({message:"page offline"})};};
const context={window,document,fetch,URLSearchParams,AbortController,URL,Promise,console,CSS:{escape:String},setTimeout,clearTimeout};
vm.createContext(context);vm.runInContext(fs.readFileSync("src/photo_search/web/photo-timeline.js","utf8"),context);
const host=new Element();context.window.PhotoTimeline.mount({host,scope:{kind:"all"}});
const settle=async()=>{for(let i=0;i<8;i++)await Promise.resolve();await new Promise(resolve=>setTimeout(resolve,0));};
(async()=>{await settle();const status=host.querySelector(".photo-timeline__status"),retry=host.querySelector(".photo-timeline__retry"),year=host.querySelector(".photo-timeline__year");
  assert.match(status.textContent,/无法打开时间轴：page offline/);assert.equal(status.classList.contains("is-error"),true);assert.equal(retry.hidden,false);
  year.value="2022";year.fire("change");await settle();
  assert.equal(periodCalls,2);assert.match(status.textContent,/无法打开时间轴：page offline/);assert.equal(status.classList.contains("is-error"),true);assert.equal(retry.hidden,false,"year summary refresh keeps the photo page retry available");
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=10
    )
    assert result.returncode == 0, result.stderr


def test_summary_revision_mismatch_has_summary_error_and_retry() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the synthetic timeline revision check"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
class Element {
  constructor(){this.children=[];this.events={};this.attrs={};this.dataset={};this.style={};this.className="";this.textContent="";this.hidden=false;this.value="";this.classList={add:n=>{if(!this.className.split(" ").includes(n))this.className=(this.className+" "+n).trim();},remove:n=>{this.className=this.className.split(" ").filter(v=>v!==n).join(" ");},contains:n=>this.className.split(" ").includes(n),toggle:(n,on)=>on?this.classList.add(n):this.classList.remove(n)};}
  append(...nodes){nodes.forEach(n=>{if(n){this.children.push(n);n.parent=this;}});}
  replaceChildren(...nodes){this.children=[];this.textContent="";this.append(...nodes);}
  addEventListener(n,fn){this.events[n]=fn;}
  fire(n){if(this.events[n])this.events[n]({target:this,currentTarget:this,preventDefault(){}});}
  setAttribute(k,v){this.attrs[k]=String(v);}
  getBoundingClientRect(){return{height:100,top:0,bottom:100};}
  querySelectorAll(selector){const cls=selector.startsWith(".")?selector.slice(1):null,out=[];const visit=n=>{for(const c of n.children){if(cls&&c.classList.contains(cls))out.push(c);visit(c);}};visit(this);return out;}
  querySelector(s){return this.querySelectorAll(s)[0]||null;}
}
const window={innerHeight:800,localStorage:{getItem(){return null;}},addEventListener(){},removeEventListener(){},scrollBy(){},requestAnimationFrame:fn=>fn()};
const document={cookie:"",createElement:()=>new Element()};
let releaseSummary,periodCalls=0,pageCalls=0,pageRevision=1,periodRevision=2;const fetch=async path=>String(path).includes("/periods")
  ? {ok:true,json:()=>{periodCalls++;return periodCalls===1
    ? new Promise(resolve=>{releaseSummary=()=>resolve({years:[],months:[],counts:{effective_total:1,trusted:1,unknown:0,pending:0},catalog_filter_revision:periodRevision});})
    : Promise.resolve({years:[],months:[],counts:{effective_total:1,trusted:1,unknown:0,pending:0},catalog_filter_revision:periodRevision});}}
  : {ok:true,json:async()=>{pageCalls++;return{assets:[{asset_id:1,anchor:"a",group_key:"2022-01-01",date_state:"trusted"}],next_cursor:null,catalog_filter_revision:pageRevision};}};
const context={window,document,fetch,URLSearchParams,AbortController,URL,Promise,console,CSS:{escape:String},setTimeout,clearTimeout};
vm.createContext(context);vm.runInContext(fs.readFileSync("src/photo_search/web/photo-timeline.js","utf8"),context);
const host=new Element();context.window.PhotoTimeline.mount({host,scope:{kind:"all"}});
const settle=async()=>{for(let i=0;i<8;i++)await Promise.resolve();await new Promise(resolve=>setTimeout(resolve,0));};
(async()=>{await settle();assert.equal(typeof releaseSummary,"function");releaseSummary();await settle();const summary=host.querySelector(".photo-timeline__summary-status"),summaryRetry=host.querySelector(".photo-timeline__summary-retry"),pageStatus=host.querySelector(".photo-timeline__status");
  assert.ok(summary.textContent.length>0);assert.equal(summaryRetry.hidden,false,"revision mismatch leaves summary retry available");
  assert.equal(pageStatus.classList.contains("is-error"),false,"summary revision mismatch does not masquerade as a photo page error");
  const all=host.querySelectorAll(".photo-timeline__bucket").find(button=>button.dataset.bucket==="all");assert.doesNotMatch(all.textContent,/· 1$/,"mismatched counts are removed from scope controls");
  pageRevision=2;periodRevision=2;summaryRetry.fire("click");await settle();
  assert.equal(pageCalls,2,"revision mismatch retry refreshes the photo page");assert.equal(periodCalls,2,"revision mismatch retry refreshes the summary");
  assert.equal(summaryRetry.hidden,true,"aligned retry clears the summary retry state");assert.match(all.textContent,/· 1$/,"aligned revision restores trusted counts");
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=10
    )
    assert result.returncode == 0, result.stderr


def test_summary_revision_mismatch_after_summary_response_clears_counts() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the summary-first revision race check"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
class Element {
  constructor(){this.children=[];this.events={};this.attrs={};this.dataset={};this.style={};this.className="";this.textContent="";this.hidden=false;this.value="";this.classList={add:n=>{if(!this.className.split(" ").includes(n))this.className=(this.className+" "+n).trim();},remove:n=>{this.className=this.className.split(" ").filter(v=>v!==n).join(" ");},contains:n=>this.className.split(" ").includes(n),toggle:(n,on)=>on?this.classList.add(n):this.classList.remove(n)};}
  append(...nodes){nodes.forEach(n=>{if(n){this.children.push(n);n.parent=this;}});}
  replaceChildren(...nodes){this.children=[];this.textContent="";this.append(...nodes);}
  addEventListener(n,fn){this.events[n]=fn;}
  fire(n){if(this.events[n])this.events[n]({target:this,currentTarget:this,preventDefault(){}});}
  setAttribute(k,v){this.attrs[k]=String(v);}
  getBoundingClientRect(){return{height:100,top:0,bottom:100};}
  querySelectorAll(selector){const cls=selector.startsWith(".")?selector.slice(1):null,out=[];const visit=n=>{for(const c of n.children){if(cls&&c.classList.contains(cls))out.push(c);visit(c);}};visit(this);return out;}
  querySelector(s){return this.querySelectorAll(s)[0]||null;}
}
const window={innerHeight:800,localStorage:{getItem(){return null;}},addEventListener(){},removeEventListener(){},scrollBy(){},requestAnimationFrame:fn=>fn()};
const document={cookie:"",createElement:()=>new Element()};
const fetch=async()=>({ok:true,json:async()=>({years:[],months:[],counts:{effective_total:1,trusted:1,unknown:0,pending:0},catalog_filter_revision:2})});
const context={window,document,fetch,URLSearchParams,AbortController,URL,Promise,console,CSS:{escape:String},setTimeout,clearTimeout};
vm.createContext(context);vm.runInContext(fs.readFileSync("src/photo_search/web/photo-timeline.js","utf8"),context);
let releasePage;const host=new Element();context.window.PhotoTimeline.mount({host,scope:{kind:"all"},adapter:{
  fetchPage:()=>new Promise(resolve=>{releasePage=()=>resolve({assets:[{asset_id:1,anchor:"a",group_key:"2022-01-01",date_state:"trusted"}],next_cursor:null,catalog_filter_revision:1});}),
  onPeriods:()=>releasePage()
}});
const settle=async()=>{for(let i=0;i<8;i++)await Promise.resolve();await new Promise(resolve=>setTimeout(resolve,0));};
(async()=>{await settle();const summaryRetry=host.querySelector(".photo-timeline__summary-retry"),pageStatus=host.querySelector(".photo-timeline__status"),all=host.querySelectorAll(".photo-timeline__bucket").find(button=>button.dataset.bucket==="all");
  assert.equal(summaryRetry.hidden,false,"page response detects a conflict after summary has returned");assert.equal(pageStatus.classList.contains("is-error"),false);
  assert.doesNotMatch(all.textContent,/· 1$/,"late conflicting page clears already rendered summary counts");
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=10
    )
    assert result.returncode == 0, result.stderr
