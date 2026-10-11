"""Exercise the real personal-page write and notice lifecycle with a synthetic clock."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_assignment_notice_expires_preserves_undo_and_does_not_clear_new_message() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the personal-page interaction check"
    script = r'''
const fs = require("fs"), vm = require("vm"), assert = require("assert/strict");
class Element {
  constructor() { this.children = []; this.value = ""; this.hidden = false; }
  append(child) { this.children.push(child); }
  replaceChildren(...children) { this.children = children; }
  setAttribute() {} addEventListener() {}
  close() { this.open = false; }
}
const elements = new Map();
const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
let now = 0, nextTimer = 0;
const timers = new Map();
function advance(ms) {
  now += ms;
  for (const [id, timer] of [...timers]) if (timer.at <= now) {
    timers.delete(id); timer.callback();
  }
}
const requests = [];
const context = {
  URLSearchParams, crypto: {randomUUID: () => "synthetic-request"},
  document: {readyState: "loading", cookie: "ps_csrf=synthetic-csrf", addEventListener() {},
    getElementById: get, createElement: () => new Element()},
  setTimeout: (callback, ms) => { const id = ++nextTimer; timers.set(id,{callback, at: now+ms}); return id; },
  clearTimeout: id => timers.delete(id),
  fetch: async (url, options) => {
    requests.push({url, options});
    return {ok:true, json: async () => options.method === "PUT" ?
      {changed:true, operation_id:"synthetic-op", operation_revision:1} :
      {persons:[], has_more:false, next_cursor:null}};
  },
};
let code = fs.readFileSync("src/photo_search/web/persons-albums.js", "utf8");
code = code.replace('  if (document.readyState === "loading")',
  '  globalThis.ui = {state, writeFace, message};\n  if (document.readyState === "loading")');
vm.createContext(context); vm.runInContext(code, context);
const ui = context.ui;
const flush = () => new Promise(setImmediate);
function assign() {
  ui.state.edit = {mode:"face",face:{face_id:"a".repeat(64),control_revision:0}};
  ui.state.chosen = {person_id:7,name:"有力气"}; get("pa-edit").open = true;
  ui.writeFace("assign");
}
(async () => {
  assign(); await flush();
  assert.equal(requests[0].options.method,"PUT");
  assert.equal(JSON.parse(requests[0].options.body).target_person_id,7);
  assert.equal(get("pa-edit").open,false);
  assert.equal(get("pa-status").textContent,'已归到“有力气”');
  assert.equal(get("pa-status").hidden,false);
  assert.equal(get("pa-undo-bar").hidden,false);
  advance(2999); assert.equal(get("pa-status").hidden,false);
  advance(1); assert.equal(get("pa-status").hidden,true);
  assert.equal(get("pa-status").textContent,"");
  assert.equal(get("pa-undo-bar").hidden,false); assert.equal(ui.state.operation.operation_id,"synthetic-op");
  advance(7000); assert.equal(get("pa-undo-bar").hidden,true);
  assign(); await flush(); advance(2000);
  assign(); await flush(); advance(1000);
  assert.equal(get("pa-status").hidden,false); // Previous timer cannot clear the new success.
  advance(2000); assert.equal(get("pa-status").hidden,true);
  assign(); await flush();
  ui.message("无法刷新，请重试。"); advance(10000);
  assert.equal(get("pa-status").textContent,"无法刷新，请重试。");
  assert.equal(get("pa-status").hidden,false); // Errors remain visible.
})().catch(error => {console.error(error); process.exitCode=1;});
'''
    result = subprocess.run(
        [node, "-e", script], cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
