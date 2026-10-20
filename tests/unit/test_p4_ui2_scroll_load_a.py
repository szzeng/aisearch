from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "src" / "photo_search" / "web"


def test_scroll_pager_serializes_requests_limits_initial_fill_and_retries() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for scroll-pager behavior checks"
    script = r'''
const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const listeners = new Map();
const window = {
  addEventListener(name, fn) { listeners.set(name, fn); },
  removeEventListener(name) { listeners.delete(name); },
};
let intersection;
let disconnected = false;
class IO {
  constructor(fn) { intersection = fn; }
  observe(target) { this.target = target; }
  disconnect() { disconnected = true; }
}
const document = { createElement() { return {}; } };
const context = { window, document, IntersectionObserver: IO, Promise, Number, Array, Boolean, Math, parseFloat };
vm.runInNewContext(fs.readFileSync("src/photo_search/web/scroll-pager.js", "utf8"), context);
const sentinel = {};
let handler;
const button = {
  hidden: true, textContent: "加载下一页", listeners: {},
  addEventListener(name, fn) { this.listeners[name] = fn; },
  removeEventListener(name) { delete this.listeners[name]; },
  click() { this.listeners.click(); },
};
let calls = 0;
let active = 0;
let maximumActive = 0;
let pending = [];
let allowed = true;
const pager = window.PhotoScrollPager.create({
  sentinel, button,
  canLoad: () => allowed,
  load: () => {
    calls += 1; active += 1; maximumActive = Math.max(maximumActive, active);
    return new Promise(resolve => pending.push(value => { active -= 1; resolve(value); }));
  },
});
'''
    script += r'''
async function settle() {
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
}
async function main() {
  /* Initial short-page fill is finite and repeated observer events stay mutually exclusive. */
  intersection([{ target: sentinel, isIntersecting: true }]);
  intersection([{ target: sentinel, isIntersecting: true }]);
  assert.equal(calls, 1);
  assert.equal(maximumActive, 1);
  pending.shift()(true); await settle();
  assert.equal(calls, 2);
  pending.shift()(true); await settle();
  assert.equal(calls, 2, "short initial result must not drain every page");
  listeners.get("scroll")();
  assert.equal(calls, 3, "real scroll authorizes one more page");
  pending.shift()(false); await settle();
  assert.equal(button.hidden, false, "failed continuation exposes a manual retry");
  button.click();
  assert.equal(calls, 4, "retry button reuses the same single-flight path");
  pending.shift()(true); await settle();
  assert.equal(maximumActive, 1);
  pager.dispose();
  assert.equal(disconnected, true);
  assert.equal(listeners.has("scroll"), false);
  assert.equal(button.listeners.click, undefined);
  context.IntersectionObserver = undefined;
  const fallbackButton = {
    hidden: true, textContent: "加载下一页", listeners: {},
    addEventListener(name, fn) { this.listeners[name] = fn; },
    removeEventListener(name) { delete this.listeners[name]; },
    click() { this.listeners.click(); },
  };
  const fallback = window.PhotoScrollPager.create({ sentinel: {}, button: fallbackButton,
    canLoad: () => allowed, load: () => Promise.resolve(true) });
  assert.equal(fallbackButton.hidden, false, "without IntersectionObserver, manual paging stays available");
  fallbackButton.click(); await settle();
  fallback.dispose();
}
main().catch(error => { console.error(error); process.exitCode = 1; });
'''
    completed = subprocess.run(
        [node, "-e", script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=10
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_scroll_load_is_wired_to_search_and_plain_browse_with_bounded_grids() -> None:
    search = (WEB / "search.js").read_text(encoding="utf-8")
    app = (WEB / "app.js").read_text(encoding="utf-8")
    home_html = (WEB / "index.html").read_text(encoding="utf-8")
    search_html = (WEB / "search.html").read_text(encoding="utf-8")
    assert 'trimGrid(els.resultGrid, 500)' in search
    assert 'trimGrid(els.assetGrid, 500)' in app
    assert 'state.search.timeBrowse.active ? state.search.timeBrowse.cursor : state.search.cursor' in search
    assert 'state.browse.mode === "list"' in app
    assert 'windowRestore.spacerHeight' in search
    assert 'requestSeq !== browse.requestSeq' in search
    assert 'windowOffset: state.search.windowOffset' in search
    assert 'spacerHeight: window.PhotoScrollPager ? window.PhotoScrollPager.getSpacerHeight(els.resultGrid) : 0' in search
    browse_catch = app.index('.catch(function (failure)', app.index('function loadBrowse'))
    stale_check = app.index('if (requestVersion !== state.browse.requestVersion)', browse_catch)
    assert 'return true;' in app[stale_check:stale_check + 350]
    assert 'windowRestore.spacerHeight !== undefined' in search
    assert 'id="grid-sentinel"' in home_html
    assert 'id="search-grid-sentinel"' in search_html
    assert re.search(r'scroll-pager\.js\?v=[^"\'&#]+', home_html)
    assert re.search(r'scroll-pager\.js\?v=[^"\'&#]+', search_html)
    assert (WEB / "scroll-pager.js").read_text(encoding="utf-8").strip()


def test_grid_window_releases_old_cards_and_holds_a_height_placeholder() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for bounded-grid behavior checks"
    script = r'''
const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
class Tile {
  constructor(index) { this.index = index; this.style = {}; this.parent = null; }
  hasAttribute(name) { return name === "data-asset-id"; }
  getBoundingClientRect() { return { top: this.index * 100, bottom: this.index * 100 + 80 }; }
  querySelectorAll() { return []; }
  remove() { this.parent.children.splice(this.parent.children.indexOf(this), 1); }
}
class List {
  constructor() { this.children = []; this.spacer = null; }
  insertBefore(node, before) { node.parent = this; this.children.splice(this.children.indexOf(before), 0, node); this.spacer = node; }
  querySelector(selector) { return selector.includes("scroll-pager-spacer") ? this.spacer : null; }
}
const document = { createElement() { return { style: {}, setAttribute() {}, remove() {} }; } };
const window = {};
vm.runInNewContext(fs.readFileSync("src/photo_search/web/scroll-pager.js", "utf8"), { window, document, Array, Number, Math, parseFloat });
const list = new List();
for (let i = 0; i < 650; i += 1) { const tile = new Tile(i); tile.parent = list; list.children.push(tile); }
const removed = window.PhotoScrollPager.trimGrid(list, 500);
assert.equal(removed, 150);
assert.equal(list.children.filter(node => node.hasAttribute && node.hasAttribute("data-asset-id")).length, 500);
assert.equal(list.children[0], list.spacer);
assert.equal(Number.parseFloat(list.spacer.style.height), 14980);
const longList = new List();
let peakRendered = 0;
for (let page = 0; page < 30; page += 1) {
  for (let offset = 0; offset < 50; offset += 1) {
    const tile = new Tile(page * 50 + offset); tile.parent = longList; longList.children.push(tile);
  }
  window.PhotoScrollPager.trimGrid(longList, 500);
  const rendered = longList.children.filter(node => node.hasAttribute && node.hasAttribute("data-asset-id")).length;
  peakRendered = Math.max(peakRendered, rendered);
}
assert.equal(peakRendered, 500);
const retained = longList.children.filter(node => node.hasAttribute && node.hasAttribute("data-asset-id"));
assert.equal(retained[0].index, 1000);
assert.equal(retained[retained.length - 1].index, 1499);
assert.ok(Number.parseFloat(longList.spacer.style.height) > 0);
const savedSpacerHeight = window.PhotoScrollPager.getSpacerHeight(longList);
window.PhotoScrollPager.setSpacerHeight(longList, 12345);
assert.equal(window.PhotoScrollPager.getSpacerHeight(longList), 12345);
window.PhotoScrollPager.setSpacerHeight(longList, savedSpacerHeight);
assert.equal(window.PhotoScrollPager.getSpacerHeight(longList), savedSpacerHeight);
'''
    completed = subprocess.run(
        [node, "-e", script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=10
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_candidate_time_toggle_invalidates_inflight_page_and_restores_pager() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for candidate-time stale-response behavior checks"
    script = r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
let resolveFetch;
const pendingFetch = new Promise(resolve => { resolveFetch = resolve; });
class Element {
  constructor() { this.textContent = ""; this.value = "20"; this.hidden = false; this.disabled = false; this.children = []; }
  appendChild(node) { this.children.push(node); return node; }
  removeChild(node) { this.children.splice(this.children.indexOf(node), 1); return node; }
  get firstChild() { return this.children[0] || null; }
  querySelector() { return null; }
}
const els = new Map(), get = id => { if (!els.has(id)) els.set(id, new Element()); return els.get(id); };
const context = {
  URLSearchParams, encodeURIComponent, isFinite, Number, Object, Array, String, Math, Promise, WeakSet, console,
  document: { readyState: "loading", cookie: "", addEventListener() {}, getElementById: get, createElement: () => new Element() },
  fetch: () => pendingFetch,
  window: { addEventListener() {}, location: { href: "http://localhost/search", search: "", pathname: "/search", hash: "" },
    sessionStorage: { setItem() {}, getItem() { return null; }, removeItem() {} },
    requestAnimationFrame(fn) { fn(); }, PhotoScrollPager: { getSpacerHeight() { return 0; }, setSpacerHeight() {} } },
};
let code = fs.readFileSync("src/photo_search/web/search.js", "utf8").replace(
  '  if (document.readyState === "loading")',
  '  globalThis.ui={toggleCandidateTimeBrowse,loadCandidateTimeBrowse,setEls(value){els=value;},getState(){return state;}};\n  if (document.readyState === "loading")');
vm.createContext(context); vm.runInContext(code, context);
const ui = context.ui, pagerCalls = [];
const state = ui.getState().search;
state.sessionId = "session-1"; state.requestGeneration = 4; state.loading = false;
state.cursor = "rank-cursor"; state.hits = []; state.count = 20; state.total = 90;
state.lastBody = { query: "forest" }; state.timeBrowse.active = true; state.timeBrowse.cursor = "time-cursor";
state.timeBrowse.previous = { hits: [], cursor: "rank-cursor", count: 20, total: 90,
  windowOffset: 0, recycledCount: 0, spacerHeight: 0 };
state.pager = { reset() { pagerCalls.push("reset"); }, update() { pagerCalls.push("update"); } };
ui.setEls({ loadMore: get("loadMore"), gridSentinel: get("gridSentinel"), queryPageSize: get("queryPageSize"), resultGrid: get("resultGrid"),
  candidateTimeToggle: get("candidateTimeToggle"), queryStatus: get("queryStatus") });
const page = ui.loadCandidateTimeBrowse(true);
assert.equal(state.loading, true);
ui.toggleCandidateTimeBrowse();
assert.equal(state.loading, false, "switching back releases candidate request loading state");
assert.equal(state.timeBrowse.active, false);
assert.equal(get("loadMore").disabled, false);
assert.equal(get("loadMore").hidden, false, "the ordinary cursor restores its continuation button");
assert.equal(get("gridSentinel").hidden, false, "the ordinary cursor restores its sentinel");
assert.deepEqual(pagerCalls, ["reset", "update"]);
resolveFetch({ status: 200, ok: true, headers: { get() { return "application/json"; } }, json() {
  return Promise.resolve({ assets: [{ asset_id: 999 }], next_cursor: "late-time-cursor", total_candidates: 1 });
} });
page.then(() => {
  assert.equal(state.loading, false);
  assert.equal(state.cursor, "rank-cursor", "late candidate response cannot replace relevance cursor");
  assert.equal(state.hits.length, 0, "late candidate response cannot append into relevance results");
}).catch(error => { console.error(error); process.exitCode = 1; });
'''
    completed = subprocess.run(
        [node, "-e", script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=10
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
