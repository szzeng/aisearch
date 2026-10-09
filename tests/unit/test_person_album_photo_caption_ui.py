"""DOM regression for the redundant person-album photo caption."""

import shutil
import subprocess
from pathlib import Path


def test_person_album_keeps_face_thumbnails_without_redundant_caption():
    node = shutil.which("node")
    assert node, "Node is required for the personal-page behavior check"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
class Element{constructor(){this.children=[];this.attributes={};this.className="";this.classList={add(){}};}append(...items){this.children.push(...items);}addEventListener(){}setAttribute(k,v){this.attributes[k]=v;}}
const els=new Map(),get=id=>{if(!els.has(id))els.set(id,new Element());return els.get(id);};
const ctx={URLSearchParams,crypto:{randomUUID:()=>"id"},setTimeout,clearTimeout,window:{location:{search:"",href:"http://local/persons"},addEventListener(){},confirm:()=>true},document:{readyState:"loading",cookie:"",addEventListener(){},getElementById:get,createElement:()=>new Element()},fetch:async()=>({ok:true,json:async()=>({})})};
let code=fs.readFileSync("src/photo_search/web/persons-albums.js","utf8");code=code.replace('  if (document.readyState === "loading")','  globalThis.testUi={state,renderPhotos};\n  if (document.readyState === "loading")');vm.createContext(ctx);vm.runInContext(code,ctx);
ctx.testUi.state.collection=null;
ctx.testUi.renderPhotos([{asset_id:1,person_faces:[{thumbnail_url:"/api/v1/faces/"+"a".repeat(64)+"/thumbnail"}]}]);
const card=get("pa-photos").children[0],faces=card.children[1];
assert.equal(faces.children.length,1,"the person-album face row should contain only its thumbnail");
assert.equal(faces.children[0].className,"pa-picture pa-choice-avatar");
assert(!JSON.stringify(faces.children).includes("这张照片里的这个人"));
console.log("person-album photo caption DOM check passed");
"""
    subprocess.run([node, "-e", script], check=True, cwd=Path.cwd())
