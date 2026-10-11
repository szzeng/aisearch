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
  '  globalThis.queueUI = {state, els, renderRefresh, renderSourceDetail, renderSources, sourceActionMode, ocrProgressText, refreshLibrary, setRequest(fn) { request = fn; }};\n  if (document.readyState === "loading") {');
function node(tag) {
  return {tag, children: [], attrs: {}, listeners: {}, disabled: false, _text: "",
    get textContent() { return this._text + this.children.map(child => child.textContent || "").join(""); },
    set textContent(value) { this._text = String(value); this.children = []; },
    appendChild(child) { this.children.push(child); return child; },
    addEventListener(name, fn) { this.listeners[name] = fn; },
    setAttribute(name, value) { this.attrs[name] = String(value); },
    getAttribute(name) { return this.attrs[name] ?? null; },
    hasAttribute(name) { return name in this.attrs; },
    querySelectorAll(selector) {
      const names = selector.match(/data-[a-z-]+/g) || [];
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
ui.state.sources = [{source_id:1}];
ui.els.refreshLibrary = {};
ui.els.buildAllLibrary = {};
ui.els.refreshStatus = node("div");
ui.renderRefresh({state:"SCANNING",running:true,mode:"build",source_ids:[1],pending_builds:[]});
assert.equal(ui.els.buildAllLibrary.disabled, false);
assert.equal(ui.els.refreshLibrary.disabled, true);
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
assert.equal(ui.els.buildAllLibrary.disabled, false);
ui.renderRefresh({state:"SCANNING",running:true,mode:"refresh",source_ids:null,pending_builds:[]});
assert.equal(ui.els.buildAllLibrary.disabled, false);
assert.equal(ui.els.refreshLibrary.disabled, true);

const progress = {built:true, stages:{semantic:{state:"SEARCHABLE"}, face:{state:"PENDING"}}};
assert.equal(ui.sourceActionMode(progress), "refresh");
assert.equal(ui.sourceActionMode({...progress, stages:{semantic:{state:"BUILDING"}}}), "continue");
const failedOcr = {state:"COMPLETE", succeeded:0, failed:3, blocked:0, pending:0,
  unattempted:0, total:3, runtime_available:true};
assert.equal(ui.ocrProgressText(failedOcr), "已处理（3 张失败）");
assert.equal(ui.sourceActionMode({...progress, stages:{semantic:{state:"SEARCHABLE"}, ocr:failedOcr}}), "refresh");
const waitingOcr = {...failedOcr, state:"PENDING", failed:1, unattempted:2};
assert.match(ui.ocrProgressText(waitingOcr), /待处理/);
assert.equal(ui.sourceActionMode({...progress, stages:{semantic:{state:"SEARCHABLE"}, ocr:waitingOcr}}), "continue");
const unavailableOcr = {...waitingOcr, runtime_available:false};
assert.equal(ui.sourceActionMode({...progress, stages:{semantic:{state:"SEARCHABLE"}, ocr:unavailableOcr}}), "refresh");
const detail = {root_path:"C:/synthetic", counts:{assets:1,locations_present:1,locations_missing:0},
  progress};
const card = node("section");
ui.renderSourceDetail({source_id:42,status:"ACTIVE"}, detail, card);
const buttons = card.querySelectorAll("[data-build], [data-refresh], [data-scan], [data-delete]");
const build = buttons.find(x => x.getAttribute("data-build") === "42");
assert.ok(build);
ui.els.sourcesList = card;
assert.equal(build.textContent, "检查并补齐建库");
const requests = [];
ui.state.sources = [{source_id:42}];
ui.els.buildAllLibrary = {};
ui.els.refreshLibrary = {};
ui.els.refreshStatus = node("div");
ui.els.faceProfile = {value:""};
ui.els.sourcesList = {querySelectorAll: () => []};
ui.setRequest((path, options) => {
  requests.push({path, body:options && options.body});
  return Promise.resolve({body:{accepted:true,running:false,mode:"build",state:"COMPLETE",pending_builds:[]}});
});
const summary = node("ul");
ui.els.sourcesList = summary;
ui.state.sourceDetails = {};
ui.renderSources();
assert.equal(requests.length, 0);
const primary = summary.querySelectorAll("[data-primary-build], [data-primary-refresh]");
assert.equal(primary.find(x => x.hasAttribute("data-primary-build")).textContent, "建库/补齐");
primary.find(x => x.hasAttribute("data-primary-build")).listeners.click();
primary.find(x => x.hasAttribute("data-primary-refresh")).listeners.click();
build.listeners.click();
ui.refreshLibrary(null, "build");
ui.refreshLibrary(null, "refresh");
setImmediate(() => {
  const globalBodies = requests.filter(x => x.body && !x.body.source_ids);
  assert.deepEqual(JSON.parse(JSON.stringify(globalBodies.map(x => [x.body.mode, x.body.source_ids]))), [
    ["build", null], ["refresh", null]
  ]);
  assert.ok(requests.some(x => x.body && x.body.mode === "build" && JSON.stringify(x.body.source_ids) === "[42]"));
  assert.ok(requests.some(x => x.body && x.body.mode === "refresh" && JSON.stringify(x.body.source_ids) === "[42]"));
  ui.state.sources = [];
  const before = requests.length;
  ui.refreshLibrary(null, "build");
  assert.equal(requests.length, before);
});
ui.state.refreshStatus = {pending_builds:[[42]], mode:"build"};
ui.state.refreshRunning = true;
ui.els.sourcesList = card;
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
    result = subprocess.run(
        [node, "-e", script],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_refresh_summary_reports_real_deltas_and_source_failures_safely() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed")
    script = r'''
const fs = require("fs"), vm = require("vm"), assert = require("assert/strict");
let source = fs.readFileSync("src/photo_search/web/app.js", "utf8");
source = source.replace('  if (document.readyState === "loading") {',
  '  globalThis.summaryUI = {state, els, renderRefresh};\n  if (document.readyState === "loading") {');
function node(tag) {
  return {tag, children: [], attrs: {}, listeners: {}, disabled: false, hidden: false, _text: "",
    get textContent() { return this._text + this.children.map(child => child.textContent || "").join(""); },
    set textContent(value) { this._text = String(value); this.children = []; },
    appendChild(child) { this.children.push(child); return child; },
    addEventListener(name, fn) { this.listeners[name] = fn; },
    setAttribute(name, value) { this.attrs[name] = String(value); },
    getAttribute(name) { return this.attrs[name] ?? null; },
    hasAttribute(name) { return name in this.attrs; }};
}
const context = {document: {readyState: "loading", addEventListener() {}, createElement: node}};
vm.createContext(context);
vm.runInContext(source, context);
const ui = context.summaryUI;
function flatten(item) {
  return [item.textContent || "", ...(item.children || []).map(flatten)].join(" ");
}
function render(status, sources) {
  ui.state.sources = sources;
  ui.els.sourcesList = {querySelectorAll: () => []};
  ui.els.refreshLibrary = node("button");
  ui.els.buildAllLibrary = node("button");
  ui.els.refreshStatus = node("div");
  ui.renderRefresh(status);
  return flatten(ui.els.refreshStatus);
}
const unchanged = render({state:"COMPLETE",running:false,mode:"refresh",search_ready:true,
  sources:[{source_id:1,state:"SCANNED",enumerated:4,unchanged_reused:4,hash_targets:0,
    new_assets:0,rebound_assets:0,locations_added:0,locations_missing:0,elapsed_ms:12}],
  counters:{sources_requested:1,sources_scanned:1,sources_failed:0,enumerated:4,
    unchanged_reused:4,hash_targets:0,new_assets:0,rebound_assets:0,locations_added:0,
    locations_missing:0}}, [{source_id:1,display_name:"图库 A"}]);
assert.match(unchanged, /未发现文件变化/);
assert.match(unchanged, /检查了 4 个文件位置/);
assert.match(unchanged, /未变化并跳过内容校验的位置 4/);
assert.match(unchanged, /扫描耗时 ms 12/);
assert.doesNotMatch(unchanged, /照片|AI 向量/);

const partial = render({state:"COMPLETE",running:false,mode:"refresh",search_ready:true,
  sources:[{source_id:1,state:"SCANNED",enumerated:1,elapsed_ms:8},
    {source_id:2,state:"FAILED",code:"SOURCE_OFFLINE",message:"C:\\private\\library"}],
  counters:{sources_requested:2,sources_scanned:1,sources_failed:1,enumerated:1,
    unchanged_reused:1,hash_targets:0,new_assets:0,rebound_assets:0,locations_added:0,
    locations_missing:0}}, [{source_id:1,display_name:"图库 A"},{source_id:2,display_name:"图库 B"}]);
assert.match(partial, /已同步成功 1\/2 个图库，另有 1 个图库同步失败/);
assert.match(partial, /已成功同步的图库未发现文件变化/);
assert.match(partial, /部分来源扫描失败/);
assert.doesNotMatch(partial, /刷新完成/);
assert.match(partial, /图库 B.*扫描失败（来源暂时不可用，可重试）/);
assert.doesNotMatch(partial, /C:\\private/);

const runningPartial = render({state:"ENRICHING",running:true,mode:"refresh",
  sources:[{source_id:2,state:"FAILED",code:"SOURCE_OFFLINE"}],
  counters:{sources_requested:1,sources_scanned:0,sources_failed:1}},
  [{source_id:2,display_name:"图库 B"}]);
assert.match(runningPartial, /部分来源扫描失败，本轮仍在进行/);

const changed = render({state:"COMPLETE",running:false,mode:"refresh",search_ready:true,
  sources:[{source_id:1,state:"SCANNED",enumerated:5,unchanged_reused:1,hash_targets:3,
    new_assets:1,rebound_assets:2,locations_added:2,locations_missing:1,elapsed_ms:25}],
  counters:{sources_requested:1,sources_scanned:1,sources_failed:0,enumerated:5,
    unchanged_reused:1,hash_targets:3,new_assets:1,rebound_assets:2,locations_added:2,
    locations_missing:1}}, [{source_id:1,display_name:"图库 A"}]);
assert.match(changed, /新增文件位置 2 个/);
assert.match(changed, /变为缺失的位置 1 个/);
assert.match(changed, /真正新内容 1 个/);
assert.match(changed, /重新关联已有内容 2 个/);
assert.doesNotMatch(changed, /移动 2/);
'''
    result = subprocess.run(
        [node, "-e", script],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


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
        all_scope = refresher.start(None, mode="build")
        assert all_scope["pending_all_build"] is True
        refresher.close(timeout=0)
        assert refresher.status()["pending_builds"] == []
        assert refresher.status()["pending_all_build"] is False
        with pytest.raises(RuntimeError, match="closed"):
            refresher.start([3], mode="build")
        gate.set()
        assert refresher.wait_idle(timeout=5)
        assert calls == [[1]]
    finally:
        gate.set()
        refresher.close()


def test_all_scope_build_is_queued_once_and_runs_after_active_build() -> None:
    gate = threading.Event()
    entered = threading.Event()
    calls = []

    class Service:
        def close(self):
            pass

    def executor(service, status, source_ids):
        calls.append((source_ids, status.snapshot()["mode"]))
        if len(calls) == 1:
            entered.set()
            assert gate.wait(5)
        status.finish(state="COMPLETE", elapsed_ms=0)

    refresher = LibraryRefresher(Service, executor=executor)
    try:
        refresher.start([1], mode="build")
        assert entered.wait(5)
        queued = refresher.start(None, mode="build")
        assert queued["accepted"] is True and queued["queued"] is True
        assert queued["pending_all_build"] is True
        assert queued["pending_builds"] == []
        duplicate = refresher.start(None, mode="build")
        assert duplicate["accepted"] is False and duplicate["queued"] is True
        assert refresher.status()["pending_all_build"] is True
        gate.set()
        assert refresher.wait_idle(timeout=5)
        assert calls == [([1], "build"), (None, "build")]
        assert refresher.status()["pending_all_build"] is False
    finally:
        gate.set()
        refresher.close()


def test_global_build_queues_behind_refresh_and_skips_if_library_became_empty() -> None:
    gate = threading.Event()
    entered = threading.Event()
    calls = []

    class Service:
        def __init__(self, source_count):
            self.source_count = source_count

        def list_sources(self):
            return [1] if self.source_count == 1 else []

        def close(self):
            pass

    def executor(service, status, source_ids):
        calls.append((source_ids, status.snapshot()["mode"]))
        entered.set()
        assert gate.wait(5)
        status.finish(state="COMPLETE", elapsed_ms=0)

    service_count = []

    def service_factory():
        service_count.append(len(service_count) + 1)
        return Service(service_count[-1])

    refresher = LibraryRefresher(service_factory, executor=executor)
    try:
        refresher.start(None, mode="refresh")
        assert entered.wait(5)
        assert refresher.start(None, mode="refresh")["accepted"] is False
        queued = refresher.start(None, mode="build")
        assert queued["accepted"] is True and queued["pending_all_build"] is True
        gate.set()
        assert refresher.wait_idle(timeout=5)
        assert calls == [(None, "refresh")]
        assert refresher.status()["state"] == "FAILED"
        assert refresher.status()["errors"][0]["code"] == "EMPTY_LIBRARY"
    finally:
        gate.set()
        refresher.close()


def test_global_build_keeps_all_65_sources_out_of_the_request_id_list() -> None:
    started = threading.Event()
    scopes = []

    class Service:
        def list_sources(self):
            return [{"source_id": value} for value in range(1, 66)]

        def close(self):
            pass

    def executor(service, status, source_ids):
        scopes.append((len(service.list_sources()), source_ids))
        status.finish(state="COMPLETE", elapsed_ms=0)
        started.set()

    refresher = LibraryRefresher(Service, executor=executor)
    try:
        accepted = refresher.start(None, mode="build")
        assert accepted["accepted"] is True
        assert started.wait(5)
        assert refresher.wait_idle(timeout=5)
        assert scopes == [(65, None)]
    finally:
        refresher.close()
