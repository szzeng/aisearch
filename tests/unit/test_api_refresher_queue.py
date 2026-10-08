"""Small deterministic checks for source builds waiting behind a single worker."""

from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from photo_search.api.refresher import LibraryRefresher


def test_home_build_buttons_follow_the_queue() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed")
    # Execute the shipped script; suppress boot and expose its source-card UI to this test.
    script = r'''
const fs = require("fs"), vm = require("vm"), assert = require("assert/strict");
let source = fs.readFileSync("src/photo_search/web/app.js", "utf8");
source = source.replace('  if (document.readyState === "loading") {',
  '  globalThis.queueUI = {state, els, renderRefresh, renderSourceDetail, sourceActionMode, setRequest(fn) { request = fn; }};\n  if (document.readyState === "loading") {');
function node(tag) {
  return {tag, children: [], attrs: {}, listeners: {}, disabled: false, textContent: "",
    appendChild(child) { this.children.push(child); return child; },
    addEventListener(name, fn) { this.listeners[name] = fn; },
    setAttribute(name, value) { this.attrs[name] = String(value); },
    getAttribute(name) { return this.attrs[name] ?? null; },
    hasAttribute(name) { return name in this.attrs; },
    querySelectorAll(selector) {
      const names = selector.match(/data-[a-z]+/g) || [];
      const found = [];
      function visit(current) {
        if (names.some(name => name in current.attrs)) found.push(current);
        current.children.forEach(visit);
      }
      this.children.forEach(visit);
      return found;
    }};
}
const context = {document: {readyState: "loading", addEventListener() {}, createElement: node}};
vm.createContext(context);
vm.runInContext(source, context);
const ui = context.queueUI;
function button(id, kind = "build") {
  const attrs = {[`data-${kind}`]: String(id), "data-build-label": "开始建库"};
  return {disabled: false, textContent: "开始建库", getAttribute: k => attrs[k] ?? null,
    hasAttribute: k => k in attrs};
}
const a = button(1), b = button(2), c = button(3), remove = button(2, "delete");
ui.els.sourcesList = {querySelectorAll: () => [a, b, c, remove]};
ui.els.refreshLibrary = {};
ui.els.refreshStatus = {};
ui.renderRefresh({state:"SCANNING",running:true,mode:"build",source_ids:[1],pending_builds:[]});
assert.equal(a.disabled, true);
assert.equal(b.disabled, false);
assert.equal(remove.disabled, true);
ui.renderRefresh({state:"SCANNING",running:true,mode:"build",source_ids:[1],
  pending_builds:[[2]],queued:true});
assert.equal(b.disabled, true);
assert.equal(b.textContent, "已排队");
assert.equal(c.disabled, false);
assert.match(ui.els.refreshStatus.textContent, /自动开始/);
ui.renderRefresh({state:"SCANNING",running:true,mode:"build",source_ids:[2],pending_builds:[]});
assert.equal(b.textContent, "正在建库");
assert.equal(a.disabled, false);
ui.renderRefresh({state:"COMPLETE",running:false,mode:"build",source_ids:[2],pending_builds:[]});
assert.equal(b.disabled, false);
assert.equal(b.textContent, "开始建库");
assert.equal(remove.disabled, false);
assert.equal(ui.els.refreshLibrary.disabled, false);

const progress = {built:true, stages:{semantic:{state:"SEARCHABLE"}, face:{state:"PENDING"}}};
assert.equal(ui.sourceActionMode(progress), "refresh");
assert.equal(ui.sourceActionMode({...progress, stages:{semantic:{state:"BUILDING"}}}), "continue");
const detail = {root_path:"C:/synthetic", counts:{assets:1,locations_present:1,locations_missing:0},
  progress};
const card = node("section");
ui.renderSourceDetail({source_id:42,status:"ACTIVE"}, detail, card);
const buttons = card.querySelectorAll("[data-build], [data-refresh], [data-scan], [data-delete]");
const fast = buttons.find(x => x.getAttribute("data-refresh") === "42");
const build = buttons.find(x => x.getAttribute("data-build") === "42");
assert.ok(fast);
assert.ok(build);
ui.els.sourcesList = card;
assert.equal(fast.textContent, "快速刷新此来源");
assert.equal(build.textContent, "检查并补齐建库");
const requests = [];
ui.setRequest((path, options) => { requests.push({path, body:options.body}); return Promise.resolve({body:{}}); });
fast.listeners.click();
build.listeners.click();
assert.deepEqual(JSON.parse(JSON.stringify(requests.map(x => [x.path, x.body.mode, x.body.source_ids]))), [
  ["/library/refresh", "refresh", [42]], ["/library/refresh", "build", [42]]
]);
ui.state.refreshStatus = {pending_builds:[[42]], mode:"build"};
ui.state.refreshRunning = true;
ui.renderRefresh({state:"SCANNING",running:true,mode:"build",source_ids:[42],pending_builds:[]});
assert.equal(build.disabled, true);
assert.equal(build.textContent, "正在建库");
ui.renderRefresh({state:"SCANNING",running:true,mode:"build",source_ids:[7],pending_builds:[[42]]});
assert.equal(build.disabled, true);
assert.equal(build.textContent, "已排队");
ui.renderRefresh({state:"COMPLETE",running:false,mode:"build",source_ids:[42],pending_builds:[]});
assert.equal(build.disabled, false);
assert.equal(build.textContent, "检查并补齐建库");
'''
    subprocess.run(
        [node, "-e", script],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.parametrize("fail_first", [False, True])
def test_builds_run_in_fifo_order_without_duplicates_or_overlap(fail_first: bool) -> None:
    gate = threading.Event()
    entered = threading.Event()
    calls = []
    closed = []

    class Service:
        def close(self):
            closed.append(threading.get_ident())

    def executor(service, status, source_ids):
        # Each predecessor's service must close before the next task starts.
        assert len(closed) == len(calls)
        snapshot = status.snapshot()
        calls.append((source_ids, snapshot["mode"], snapshot["face_profile"], threading.get_ident()))
        if len(calls) == 1:
            entered.set()
            assert gate.wait(5)
            if fail_first:
                raise RuntimeError("synthetic failure")
        status.finish(state="COMPLETE", elapsed_ms=0)

    refresher = LibraryRefresher(Service, executor=executor)
    try:
        first = refresher.start([1], mode="build", face_profile="lightweight")
        assert entered.wait(5)
        assert refresher.start([1, 1], mode="build", face_profile="lightweight")["accepted"] is False
        # Omitting the profile means keep the running selection, rather than queue it twice.
        assert refresher.start([1, 1], mode="build")["accepted"] is False
        second = refresher.start([2], mode="build")
        assert second["accepted"] is True and second["queued"] is True
        assert second["refresh_id"] == first["refresh_id"]
        duplicate = refresher.start([2, 2], mode="build")
        assert duplicate["accepted"] is False and duplicate["queued"] is True
        refresher.start([3], mode="build")
        assert refresher.start()["accepted"] is False
        assert refresher.status()["pending_builds"] == [[2], [3]]
        assert len(calls) == 1
        assert refresher.wait_idle(timeout=0.01) is False
        gate.set()
        assert refresher.wait_idle(timeout=5)
        assert [call[:3] for call in calls] == [
            ([1], "build", "lightweight"),
            ([2], "build", "source_detail"),
            ([3], "build", "source_detail"),
        ]
        assert len({call[3] for call in calls}) == 1
        assert len(closed) == 3
        assert refresher.status()["source_ids"] == [3]
        assert refresher.status()["pending_builds"] == []
        assert refresher.status()["running"] is False
        assert refresher.status()["state"] == "COMPLETE"
    finally:
        gate.set()
        refresher.close()


def test_shutdown_discards_waiting_builds_and_finishes_the_active_service() -> None:
    gate = threading.Event()
    calls = []

    class Service:
        def close(self):
            pass

    def executor(service, status, source_ids):
        calls.append(source_ids)
        assert gate.wait(5)
        status.finish(state="COMPLETE", elapsed_ms=0)

    refresher = LibraryRefresher(Service, executor=executor)
    try:
        refresher.start([1], mode="build")
        refresher.start([2], mode="build")
        refresher.close(timeout=0)
        assert refresher.status()["pending_builds"] == []
        with pytest.raises(RuntimeError, match="closed"):
            refresher.start([3], mode="build")
        gate.set()
        assert refresher.wait_idle(timeout=5)
        assert calls == [[1]]
    finally:
        gate.set()
        refresher.close()
