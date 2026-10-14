"""The real place-album script presents source-backed choices before assignment."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_album_correction_shows_full_source_choice_and_posts_controlled_ref() -> None:
    node = shutil.which("node")
    assert node is not None
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.events={};this.dataset={};this.value='';this.hidden=false;this.disabled=false;this.text='';this.className='';this.parent=null;this.classList={contains:n=>this.className.split(/\s+/).includes(n)};}
  set textContent(value){this.text=String(value);this.children=[];}
  get textContent(){return this.text+this.children.map(item=>item.textContent).join('');}
  appendChild(item){item.parent=this;this.children.push(item);return item;}
  append(...items){items.forEach(item=>this.appendChild(item));}
  replaceChildren(...items){this.children=[];this.children=items;items.forEach(item=>item.parent=this);}
  addEventListener(name,fn){this.events[name]=fn;}
  fire(name){return this.events[name]({preventDefault(){}});}
  setAttribute(){}
  scrollIntoView(){}
  querySelectorAll(selector){const wanted=selector.slice(1);let found=[];function walk(n){n.children.forEach(c=>{if(c.className.split(/\s+/).includes(wanted))found.push(c);walk(c);});}walk(this);return found;}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  focus(){}
  scrollIntoView(){}
  remove(){if(this.parent)this.parent.children.splice(this.parent.children.indexOf(this),1);}
}
const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element('div'));return elements.get(id);};
get('album-kind').value='city';
let assignmentBody=null;
let timelineOptions=null;
const album={kind:'city',key:'77',name:'东京',parent:'日本',total:1,origin:'MANUAL',cover_asset_id:42};
const choice={kind:'district',key:'cn:3',name:'历下区',parent:'山东省 · 济南市',source:'OSM',selection_ref:'osm|fixture-dataset|relation/3',aliases:[]};
const response=body=>({ok:true,status:200,json:async()=>body});
const context={URLSearchParams,URL,Date,Math,console,document:{cookie:'ps_csrf=fixture-csrf',getElementById:get,createElement:tag=>new Element(tag),querySelectorAll:()=>[],body:Object.assign(new Element('body'),{dataset:{}})},
  window:{location:{search:'',pathname:'/places',hash:'',href:'http://127.0.0.1/places'},history:{state:null,pushState(state,_title,url){this.state=state;this.url=url;},replaceState(state,_title,url){this.state=state;this.url=url;},back(){}},addEventListener(){},scrollY:0,scrollTo(){},setTimeout:fn=>setImmediate(fn),clearTimeout(){},alert(message){throw Error(message);},
    PhotoTimeline:{mount(options){timelineOptions=options;return {dispose(){}};}}},
  fetch:async(url,options={})=>{
    if(String(url).includes('/places/admin/capabilities'))return response({available:false});
    if(String(url).includes('/places/choices'))return response({choices:[choice]});
    if(String(url).includes('/assets/42/place')&&options.method!=='PUT')return response({assignment:{revision:0}});
    if(String(url).includes('/place-assignment')){assignmentBody=JSON.parse(options.body);return response({changed:true});}
    if(String(url).includes('/places/albums'))return response({albums:[album]});
    throw Error('unexpected request '+url);
  }};
vm.createContext(context);vm.runInContext(fs.readFileSync('src/photo_search/web/places-albums.js','utf8'),context);
const settle=async()=>{for(let i=0;i<5;i++)await new Promise(setImmediate);};
(async()=>{
  await settle();
  const card=get('album-cards').children[0];assert.match(card.textContent,/东京/);
  card.fire('click');await settle();
  assert.equal(timelineOptions.scope.kind,'place');
  assert.equal(timelineOptions.scope.place_kind,'city');
  assert.equal(timelineOptions.scope.place_key,'77');
  const photo=timelineOptions.adapter.renderAsset({asset_id:42,date_state:'unknown',time_label:'日期未知'});
  get('album-photos').appendChild(photo);
  const correction=photo.children.find(item=>item.className==='album-correction');assert.equal(correction.children[0].textContent,'更多操作');
  const body=correction.children.find(item=>item.className==='album-correction__body');
  const assign=body.children.find(item=>item.className==='album-correction__assign');
  const input=assign.children.find(item=>item.tag==='input');input.value='历下区';
  assign.children.find(item=>item.tag==='button').fire('click');await settle();
  const options=body.children.find(item=>item.className==='place-choice-list');
  assert.match(options.textContent,/历下区 · 上级 山东省 · 济南市/);
  assert.match(options.textContent,/来源：OSM/);
  options.children[1].fire('click');await settle();
  assert.equal(assignmentBody.action,'assign');
  assert.equal(assignmentBody.target_place_ref,choice.selection_ref);
  assert.equal(Object.hasOwn(assignmentBody,'target_place_id'),false);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", script],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
