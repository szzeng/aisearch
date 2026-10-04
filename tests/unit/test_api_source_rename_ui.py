"""Run the real home-page rename interaction with a small DOM and HTTP substitute."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_home_source_rename_save_cancel_validation_and_error() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed")
    script = r'''
const fs = require("fs"), vm = require("vm"), assert = require("assert/strict");
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.events = {}; this.attrs = {}; }
  appendChild(child) { this.children.push(child); return child; }
  setAttribute(key, value) { this.attrs[key] = value; }
  addEventListener(event, callback) { this.events[event] = callback; }
  focus() {} select() {}
  fire(event) { this.events[event]({preventDefault() {}}); }
  set textContent(value) { this.content = value; this.children = []; }
  get textContent() { return this.content || ""; }
}
const requests = [];
let rejectSave = false;
const context = {
  document: {readyState: "loading", cookie: "ps_csrf=synthetic-csrf", addEventListener() {},
    createElement: tag => new Element(tag)},
  fetch: async (url, options) => {
    requests.push({url, options});
    return {status: rejectSave ? 422 : 200, ok: !rejectSave,
      headers: {get: () => "application/json"},
      json: async () => rejectSave ? {code:"INVALID_PATH",message:"synthetic rejection"} :
        {source_id:1,display_name:JSON.parse(options.body).display_name}};
  },
};
let code = fs.readFileSync("src/photo_search/web/app.js", "utf8");
code = code.replace('  if (document.readyState === "loading") {',
  '  globalThis.renameUI = {state, els, addSourceRename};\n  if (document.readyState === "loading") {');
vm.createContext(context);
vm.runInContext(code, context);
const ui = context.renameUI;
const source = {source_id:1,display_name:"原名字"};
ui.state.sources = [source];
ui.state.sourceDetails["1"] = {display_name:"原名字"};
ui.state.browse.sourceId = "1";
ui.state.refreshRunning = true; // Metadata editing stays usable during a build.
ui.els.browseSource = new Element("select"); ui.els.browseSource.value = "1";
ui.els.sourcesStatus = new Element("p");
const title = new Element("h3"), card = new Element("li"); title.textContent = "原名字";
ui.els.sourcesList = {querySelectorAll: () => [title]};
ui.addSourceRename(card, source, title);
const [edit, form] = card.children;
const [label, save, cancel, feedback] = form.children;
const input = label.children[0];
const flush = () => new Promise(setImmediate);
(async () => {
  edit.fire("click");
  assert.equal(form.hidden, false); assert.equal(input.value,"原名字");
  input.value = "取消的名字"; cancel.fire("click");
  assert.equal(form.hidden, true); assert.equal(requests.length, 0);
  edit.fire("click"); input.value = "   "; form.fire("submit");
  assert.match(feedback.textContent,/不能为空/); assert.equal(requests.length,0);
  input.value = "  新名字  "; form.fire("submit");
  assert.equal(save.disabled,true); assert.equal(input.disabled,true);
  form.fire("submit"); assert.equal(requests.length,1);
  await flush();
  assert.equal(requests[0].url,"/api/v1/sources/1");
  assert.equal(requests[0].options.method,"PATCH");
  assert.equal(requests[0].options.headers["X-CSRF-Token"],"synthetic-csrf");
  assert.deepEqual(JSON.parse(requests[0].options.body),{display_name:"新名字"});
  assert.equal(source.display_name,"新名字"); assert.equal(title.textContent,"新名字");
  assert.equal(ui.state.sourceDetails["1"].display_name,"新名字");
  assert.equal(ui.els.browseSource.value,"1");
  assert.equal(ui.els.browseSource.children[1].textContent,"新名字 · #1");
  assert.equal(form.hidden,true); assert.equal(save.disabled,false);
  rejectSave = true; edit.fire("click"); input.value = "失败的名字"; form.fire("submit");
  await flush();
  assert.equal(form.hidden,false); assert.equal(save.disabled,false);
  assert.equal(cancel.disabled,false); assert.equal(input.disabled,false);
  assert.match(feedback.textContent,/保存失败/); assert.equal(source.display_name,"新名字");
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
    result = subprocess.run(
        [node, "-e", script], cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
