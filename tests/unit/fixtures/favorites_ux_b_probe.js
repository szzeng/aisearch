const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class FakeNode {
  constructor(tagName) {
    this.tagName = tagName;
    this.dataset = {};
    this.handlers = {};
    this.attributes = {};
    this.isConnected = true;
    this.classList = { toggle() {} };
  }
  addEventListener(name, handler) { (this.handlers[name] ||= []).push(handler); }
  setAttribute(name, value) { this.attributes[name] = value; }
  appendChild(child) { child.parentNode = this; this.children ||= []; this.children.push(child); }
  querySelector() { return (this.children || []).find((child) => child.className === "photo-select-checkbox") || null; }
  closest(selector) {
    if (selector === "[data-asset-id]") return this.dataset.assetId ? this : null;
    return null;
  }
}

class FakeRoot {
  constructor(document) {
    this.ownerDocument = document;
    this.children = [];
    this.handlers = {};
    this.classList = { toggle() {} };
  }
  addEventListener(name, handler) { (this.handlers[name] ||= []).push(handler); }
  appendChild(child) { child.parentNode = this; this.children.push(child); }
  contains(child) { return this.children.includes(child) || this.children.some((item) => item.children?.includes(child)); }
  querySelectorAll(selector) {
    return selector === ".favorite-star" ? this.children.filter((child) => child.className === "favorite-star") : [];
  }
}

function fakeControls() {
  const make = () => new FakeNode("button");
  return { toggle: make(), toolbar: { hidden: true }, count: { textContent: "" }, all: make(), invert: make(), clear: make(), add: make(), remove: make() };
}

function loadHelper({ document, fetch }) {
  const observers = [];
  class FakeMutationObserver {
    constructor(callback) { this.callback = callback; observers.push(this); }
    observe() {}
  }
  const window = {};
  const source = fs.readFileSync("src/photo_search/web/favorites-ui.js", "utf8");
  vm.runInNewContext(source, { window, document, fetch, MutationObserver: FakeMutationObserver, Promise, WeakMap, Set, Map, Array, String, Number, Boolean, Object, JSON, Error });
  return { api: window, observers };
}

async function selectionLimitProbe() {
  const document = { addEventListener() {}, querySelector() { return null; }, createElement: (name) => new FakeNode(name) };
  const { api } = loadHelper({ document, fetch: async () => { throw new Error("unexpected fetch"); } });
  const root = new FakeRoot(document);
  for (let id = 1; id <= 501; id += 1) { const item = new FakeNode("div"); item.dataset.assetId = String(id); root.appendChild(item); }
  const controls = fakeControls();
  const selection = api.PhotoBatchSelect.create(root, controls);
  controls.toggle.handlers.click[0]();
  const clickItem = (item) => root.handlers.click[0]({
    target: item, shiftKey: false, preventDefault() {}, stopPropagation() {}, stopImmediatePropagation() {}
  });
  for (let index = 0; index < 500; index += 1) clickItem(root.children[index]);
  assert.equal(selection.selected.size, 500);
  const recycled = root.children.shift();
  recycled.isConnected = false;
  assert.equal(selection.selected.size, 500, "selection survives DOM recycling of an explicitly selected item");
  const selectedBeforeOverflow = selection.selected;
  assert.equal(selectedBeforeOverflow.has("1"), true);
  clickItem(root.children[499]);
  assert.equal(selection.selected.size, 500, "the 501st selection leaves the prior 500 unchanged");
  assert.deepEqual(selection.selected, selectedBeforeOverflow, "the overflow attempt preserves every previously selected ID");
  assert.match(controls.count.textContent, /最多选择 500 张/);
}

async function recycledLateStateProbe() {
  const document = { cookie: "", createElement: (name) => new FakeNode(name) };
  let resolveFirst;
  let stateReads = 0;
  const fetch = () => {
    stateReads += 1;
    if (stateReads === 1) return new Promise((resolve) => { resolveFirst = resolve; });
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ favorites_revision: 5, favorite_asset_ids: [9] }) });
  };
  const { api, observers } = loadHelper({ document, fetch });
  const root = new FakeRoot(document);
  const oldButton = api.PhotoFavorites.button(9);
  root.appendChild(oldButton);
  api.PhotoFavorites.mount(root);
  assert.equal(stateReads, 1);

  root.children.splice(root.children.indexOf(oldButton), 1);
  oldButton.isConnected = false;
  observers[0].callback([]);
  await Promise.resolve();
  await Promise.resolve();
  resolveFirst({ ok: true, json: () => Promise.resolve({ favorites_revision: 5, favorite_asset_ids: [9] }) });
  await new Promise((resolve) => setImmediate(resolve));

  const freshButton = api.PhotoFavorites.button(9);
  root.appendChild(freshButton);
  api.PhotoFavorites.mount(root);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(stateReads, 2, "a recycled ID is reread instead of retained in a stale cache");
  assert.equal(freshButton.favoriteState, true);
}

async function boundedStatusBatchProbe() {
  const document = { cookie: "", createElement: (name) => new FakeNode(name) };
  const requests = [];
  const fetch = (url) => {
    requests.push(url);
    const ids = Array.from(url.matchAll(/asset_ids=(\d+)/g), (match) => Number(match[1]));
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ favorites_revision: 6, favorite_asset_ids: ids.filter((id) => id % 2 === 0) }) });
  };
  const { api } = loadHelper({ document, fetch });
  const root = new FakeRoot(document);
  for (let id = 1; id <= 201; id += 1) root.appendChild(api.PhotoFavorites.button(id));
  api.PhotoFavorites.mount(root);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(requests.length, 3, "201 visible IDs are fetched in three bounded requests");
  assert.deepEqual(requests.map((url) => Array.from(url.matchAll(/asset_ids=(\d+)/g)).length), [100, 100, 1]);
  const requestedIds = requests.map((url) => Array.from(url.matchAll(/asset_ids=(\d+)/g), (match) => Number(match[1])));
  assert.deepEqual(requestedIds, [Array.from({ length: 100 }, (_, i) => i + 1), Array.from({ length: 100 }, (_, i) => i + 101), [201]]);
  root.children.forEach((button, index) => assert.equal(button.favoriteState, (index + 1) % 2 === 0));
  assert.match(root.children[0].innerHTML, /favorite-star__icon/);
  assert.equal(root.children[0].attributes["aria-pressed"], "false");
  assert.match(root.children[1].innerHTML, /fill="currentColor"/);
  assert.equal(root.children[1].attributes["aria-pressed"], "true");
}

(async () => {
  await selectionLimitProbe();
  await recycledLateStateProbe();
  await boundedStatusBatchProbe();
  process.stdout.write("favorites UX B behavior probes passed\n");
})().catch((error) => { console.error(error); process.exitCode = 1; });
