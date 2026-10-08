"""Actual album JS: late responses and pause/resume, with deferred synthetic HTTP."""

import shutil
import subprocess
from pathlib import Path


def test_cn_album_races_and_update_pause_resume():
    node = shutil.which("node")
    assert node is not None
    script = r"""
const fs=require('fs'), vm=require('vm'), assert=require('assert/strict');
class Element {
  constructor(tag) { this.tag=tag; this.children=[]; this.events={}; this.value=''; this.hidden=false; this.disabled=false; }
  set textContent(value) { this.text=value; this.children=[]; } get textContent() { return this.text || ''; }
  appendChild(item) { this.children.push(item); } append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children=items; } setAttribute() {} scrollIntoView() {}
  addEventListener(event,fn) { this.events[event]=fn; } fire(event) { return this.events[event](); }
}
const elements=new Map(), get=id=>{if(!elements.has(id))elements.set(id,new Element('div'));return elements.get(id);};
get('album-kind').value='city';
const pending=[], posts=[];
const response=body=>({ok:true,json:async()=>body});
const context={URLSearchParams,Date,Math,document:{cookie:'ps_csrf=synthetic',getElementById:get,createElement:tag=>new Element(tag)},
window:{location:{search:''},setTimeout:fn=>setImmediate(fn),clearTimeout(){}},
fetch:(url,options)=>{
  if(url.endsWith('/admin/capabilities'))return Promise.resolve(response({available:true,dataset_id:'fixture'}));
  return new Promise(resolve=>{const item={url,options,resolve:body=>resolve(response(body))};pending.push(item);if(options.method==='POST')posts.push(item);});
}};
vm.createContext(context);vm.runInContext(fs.readFileSync('src/photo_search/web/places-albums.js','utf8'),context);
const settle=async()=>{await new Promise(setImmediate);await new Promise(setImmediate);};
const album=(kind,name)=>({kind,name,key:'cn:3',parent:'浙江省 · 杭州市',total:107,origin:'GPS_ADMIN',cover_asset_id:1});
(async()=>{
  await settle();
  const old=pending[0];get('album-kind').value='district';get('album-kind').fire('change');
  pending[1].resolve({albums:[album('district','西湖区')]});await settle();
  old.resolve({albums:[album('city','杭州市')]});await settle();
  assert.equal(get('album-cards').children[0].children[1].textContent,'西湖区');
  get('album-cards').children[0].fire('click');
  const photo=pending.at(-1);get('album-kind').value='province';get('album-kind').fire('change');
  photo.resolve({assets:[{asset_id:1}],next_cursor:'old'});await settle();
  assert.equal(get('album-photos').children.length,0);assert.equal(get('album-photos').hidden,true);
  get('cn-update').fire('click');assert.equal(posts.length,1);assert.equal(get('cn-pause').hidden,false);
  get('cn-pause').fire('click');posts[0].resolve({complete:false,total_scanned:100,total_processed:100});await settle();
  assert.equal(posts.length,1);assert.match(get('cn-update-status').textContent,/已暂停/);
  assert.equal(get('cn-update').disabled,false);
  get('cn-update').fire('click');assert.equal(posts.length,2);assert.match(posts[1].url,/restart=false/);
  assert.equal(posts[1].options.headers['X-CSRF-Token'],'synthetic');
  posts[1].resolve({complete:true,total_scanned:107,total_processed:107});await settle();
  assert.match(get('cn-update-status').textContent,/更新完成/);
  get('cn-update').fire('click');assert.match(posts[2].url,/restart=true/);
  posts[2].resolve({complete:true,total_scanned:107,total_processed:0});await settle();
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
