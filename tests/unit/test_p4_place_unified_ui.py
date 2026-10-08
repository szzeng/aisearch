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
  constructor(tag){this.tag=tag;this.children=[];this.events={};this.value='';this.hidden=false;this.disabled=false;this.text='';}
  set textContent(value){this.text=String(value);this.children=[];}
  get textContent(){return this.text+this.children.map(item=>item.textContent).join('');}
  appendChild(item){this.children.push(item);return item;}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
  addEventListener(name,fn){this.events[name]=fn;}
  fire(name){return this.events[name]({preventDefault(){}});}
  setAttribute(){}
  scrollIntoView(){}
  querySelectorAll(selector){return selector==='.place-choice-list'?this.children.filter(item=>item.className===selector.slice(1)):[];}
  remove(){if(this.parent)this.parent.children.splice(this.parent.children.indexOf(this),1);}
}
const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element('div'));return elements.get(id);};
get('album-kind').value='city';
let assignmentBody=null;
const album={kind:'city',key:'77',name:'东京',parent:'日本',total:1,origin:'MANUAL',cover_asset_id:42};
const choice={kind:'district',key:'cn:3',name:'历下区',parent:'山东省 · 济南市',source:'OSM',selection_ref:'osm|fixture-dataset|relation/3',aliases:[]};
const response=body=>({ok:true,status:200,json:async()=>body});
const context={URLSearchParams,Date,Math,console,document:{cookie:'ps_csrf=fixture-csrf',getElementById:get,createElement:tag=>new Element(tag),body:new Element('body')},
  window:{location:{search:''},setTimeout:fn=>setImmediate(fn),clearTimeout(){},alert(message){throw Error(message);}},
  fetch:async(url,options={})=>{
    if(String(url).includes('/places/admin/capabilities'))return response({available:false});
    if(String(url).includes('/places/choices'))return response({choices:[choice]});
    if(String(url).includes('/assets/42/place')&&options.method!=='PUT')return response({assignment:{revision:0}});
    if(String(url).includes('/place-assignment')){assignmentBody=JSON.parse(options.body);return response({changed:true});}
    if(String(url).includes('/places/albums/city/77/assets'))return response({assets:[{asset_id:42}],next_cursor:null});
    if(String(url).includes('/places/albums'))return response({albums:[album]});
    throw Error('unexpected request '+url);
  }};
vm.createContext(context);vm.runInContext(fs.readFileSync('src/photo_search/web/places-albums.js','utf8'),context);
const settle=async()=>{for(let i=0;i<5;i++)await new Promise(setImmediate);};
(async()=>{
  await settle();
  const card=get('album-cards').children[0];assert.match(card.textContent,/东京/);
  card.fire('click');await settle();
  const photo=get('album-photos').children.find(item=>item.className==='album-photo');
  const correction=photo.children.find(item=>item.className==='album-correction');
  const input=correction.children.find(item=>item.tag==='input');input.value='历下区';
  correction.children.find(item=>item.textContent==='查找可选地点').fire('click');await settle();
  const options=correction.children.find(item=>item.className==='place-choice-list');
  assert.match(options.textContent,/历下区 · 山东省 · 济南市/);
  assert.match(options.textContent,/district · OSM/);
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
