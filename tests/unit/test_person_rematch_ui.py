"""Exercise actual personal-page filters, safe reason hints and explicit batch retry."""

import shutil
import subprocess
from pathlib import Path


def test_actual_js_batches_require_a_click_keep_retry_position_and_refresh_reason_filters():
    node = shutil.which("node")
    assert node, "Node is required for the personal-page behavior check"
    script = r"""
const fs = require("fs"), vm = require("vm"), assert = require("assert/strict");
// Keep real timer behavior during the scenario, then dispose its UI on completion.
const pendingTimers = new Set();
const testSetTimeout = (callback, delay, ...args) => {
  const timer = setTimeout(() => { pendingTimers.delete(timer); callback(...args); }, delay);
  pendingTimers.add(timer); return timer;
};
const testClearTimeout = timer => { clearTimeout(timer); pendingTimers.delete(timer); };
const disposeTimers = () => { for (const timer of pendingTimers) clearTimeout(timer); pendingTimers.clear(); };
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.value = ""; this.hidden = false;
    this.textContent = ""; this.dataset = {}; this.listeners = {}; this.classList = {add(){}}; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  setAttribute() {} addEventListener(name, callback) { this.listeners[name] = callback; }
  fire(name) { if (this.listeners[name]) return this.listeners[name](); }
  querySelector(selector) { return this.children.find(item => String(item.className || "").includes(selector.slice(1))); }
  focus() {}
}
const elements = new Map(), requests = [];
const get = id => { if (!elements.has(id)) elements.set(id, new Element("div")); return elements.get(id); };
get("pa-inbox-kind").value = "suggested";
let reply, fail = false, posts = 0;
const faces = [
  {face_id:"a".repeat(64),asset_id:1,control_revision:4,organization_reason:"BORDERLINE",
    suggestions:[{person_id:17,name:"妈妈"}]},
  {face_id:"b".repeat(64),asset_id:1,organization_reason:"SAME_IMAGE_CONFLICT",suggestions:[]},
];
const context = {
  URLSearchParams, crypto:{randomUUID:()=>"synthetic-request"}, setTimeout:testSetTimeout, clearTimeout:testClearTimeout,
  window:{scrollY:0,addEventListener(){},localStorage:{getItem:()=>null,setItem(){}}},
  document:{readyState:"loading",cookie:"ps_csrf=synthetic-csrf",addEventListener(){},
    getElementById:get,createElement:tag=>new Element(tag)},
  fetch:async (url, options) => {
    requests.push({url, options});
    let value;
    if (url === "/api/v1/faces/" + faces[0].face_id + "/assignment") {
      value = {changed:true,operation_id:"operation-1",operation_revision:1};
    } else if (url === "/api/v1/faces/rematch") {
      posts++;
      if (fail) return {ok:false,status:503,json:async()=>({code:"TEMPORARY"})};
      value = await new Promise(resolve=>{reply=resolve;});
    } else if (url.startsWith("/api/v1/faces/inbox/summary?")) {
      value = {total:2,reasons:{BORDERLINE:1,SAME_IMAGE_CONFLICT:1},held:1};
    } else if (url.startsWith("/api/v1/faces/inbox/groups/candidate%3A")) value = {faces:[faces[0]],has_more:false,next_cursor:null};
    else if (url.startsWith("/api/v1/faces/inbox/groups?")) value = {cards:[
      {type:"candidate_group",group_key:"candidate:1",candidate_name:"妈妈",photo_count:2,
        face:faces[0]},
      {type:"single_face",photo_count:1,face:faces[1]},
    ],has_more:false,next_cursor:null};
    else if (url.startsWith("/api/v1/faces/inbox?")) value = {faces,has_more:false,next_cursor:null};
    else value = {persons:[],has_more:false,next_cursor:null};
    return {ok:true,json:async()=>value};
  },
};
let code = fs.readFileSync("src/photo_search/web/persons-albums.js","utf8");
code = code.replace('  if (document.readyState === "loading")',
  '  globalThis.ui={state,loadInbox,rematchFaces,organizationText};\n  if (document.readyState === "loading")');
vm.createContext(context); vm.runInContext(code,context);
const ui = context.ui, flush = () => new Promise(setImmediate);
(async () => {
  await ui.loadInbox(false);
  assert.equal(posts,0); // Viewing the list never starts matching.
  assert(requests.some(r=>r.url.startsWith("/api/v1/faces/inbox/groups?") && r.url.includes("kind=suggested")));
  assert.equal(get("pa-inbox-cards").children[0].children[0].children[1].textContent,"可能是妈妈");
  assert.equal(get("pa-inbox-cards").children[0].children[0].children[2].textContent,"2 张待确认照片");
  const groupCard = get("pa-inbox-cards").children[0]; groupCard.open = true; groupCard.fire("toggle"); await flush();
  assert(requests.some(r=>r.url.startsWith("/api/v1/faces/inbox/groups/candidate%3A1?")));
  assert.equal(groupCard.querySelector(".pa-inbox-group-faces").children.length,1);
  const suggestedCard = groupCard.querySelector(".pa-inbox-group-faces").children[0];
  assert.equal(suggestedCard.confirmButton.textContent,"确认是妈妈");
  assert.equal(suggestedCard.otherPersonButton.textContent,"选其他人");
  await suggestedCard.confirmButton.fire("click");
  const confirm = requests.find(r=>r.url === "/api/v1/faces/" + faces[0].face_id + "/assignment");
  assert.equal(confirm.options.method,"PUT");
  assert.deepEqual(JSON.parse(confirm.options.body),{
    contract_version:"p3-face-correction-contracts.v1",request_id:"album-synthetic-request",
    action:"assign",target_person_id:17,expected_revision:4,
  });
  assert.equal(confirm.options.headers["X-CSRF-Token"],"synthetic-csrf");
  assert.equal(suggestedCard.confirmButton.textContent,"✓ 已确认是妈妈");
  assert.equal(suggestedCard.confirmButton.disabled,true);
  assert.equal(get("pa-inbox-cards").children[0],groupCard,"confirmation keeps the expanded group in place");
  assert(!get("pa-inbox-note").textContent.includes("#panel-mappings"));
  assert.equal(ui.organizationText({quality_reason:"SMALL"}),"脸太小，需确认");
  assert.equal(ui.organizationText({organization_reason:"BLURRY"}),"脸有些模糊，需确认");
  assert.equal(ui.organizationText({mode:"MANUAL_UNASSIGNED",organization_reason:"BORDERLINE"}),"已移出人物，等你指定");
  const first = ui.rematchFaces(); await ui.rematchFaces();
  assert.equal(posts,1); assert.equal(get("pa-rematch").disabled,true);
  const post = requests.find(r=>r.url === "/api/v1/faces/rematch");
  assert.deepEqual(JSON.parse(post.options.body),{limit:20,after_asset_id:0});
  assert.equal(post.options.headers["X-CSRF-Token"],"synthetic-csrf");
  reply({processed:2,merged:3,new_groups:0,deferred:0,has_more:true,next_after_asset_id:42});
  await first; await flush();
  assert.equal(ui.state.rematchAfter,42); assert.equal(get("pa-rematch").disabled,false);
  assert.equal(get("pa-rematch").textContent,"继续补匹配剩余人脸");
  assert(get("pa-status").textContent.includes("归入已有人物 3 张脸"));
  fail=true; await ui.rematchFaces();
  assert.equal(ui.state.rematchAfter,42); assert.equal(get("pa-rematch").disabled,false);
  assert(get("pa-status").textContent.startsWith("补匹配没有完成"));
  fail=false; const retry = ui.rematchFaces();
  assert.equal(JSON.parse(requests.at(-1).options.body).after_asset_id,42);
  reply({processed:1,merged:0,new_groups:0,deferred:1,has_more:false,next_after_asset_id:null});
  await retry; assert.equal(ui.state.rematchAfter,0);
  assert(get("pa-status").textContent.includes("还在等待分析结果"));
  ui.state.rematchAfter=55; get("pa-rematch").textContent="继续补匹配剩余人脸";
  const restarted = ui.rematchFaces(true);
  assert.equal(JSON.parse(requests.at(-1).options.body).after_asset_id,0);
  reply({processed:2,merged:1,new_groups:0,deferred:0,has_more:true,next_after_asset_id:31});
  await restarted;
  assert.equal(ui.state.rematchAfter,55); assert.equal(ui.state.rematchRestartAfter,31);
  assert.equal(get("pa-rematch").textContent,"继续补匹配剩余人脸");
  const continuedRestart = ui.rematchFaces(true);
  assert.equal(JSON.parse(requests.at(-1).options.body).after_asset_id,31);
  reply({processed:1,merged:0,new_groups:0,deferred:0,has_more:false,next_after_asset_id:null});
  await continuedRestart;
  assert.equal(ui.state.rematchAfter,55); assert.equal(ui.state.rematchRestartAfter,null);
  assert.equal(get("pa-rematch").textContent,"继续补匹配剩余人脸");
  ui.state.inboxCursor="old-filter-cursor"; get("pa-inbox-kind").value="quality";
  await ui.loadInbox(false);
  const filtered = requests.filter(r=>r.url.startsWith("/api/v1/faces/inbox?")).at(-1).url;
  assert(filtered.includes("kind=quality") && !filtered.includes("cursor="));
})().finally(disposeTimers).catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", script],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
