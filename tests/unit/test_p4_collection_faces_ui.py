from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCENARIOS = (
    "candidate_pages",
    "suspended_parent_revision",
    "member_late_response",
    "member_late_error",
    "member_changed_revision",
    "click_and_shift",
    "switch_collection",
    "late_response",
    "late_error",
    "revision_refresh",
    "replaced_instance",
    "remove_counts",
    "saved_refresh_failure",
    "shared_face_write",
)

NODE_SCRIPT = r"""
const fs = require("fs"), vm = require("vm"), assert = require("assert/strict");
class Element {
  constructor(tag = "div") {
    this.tagName = tag; this.children = []; this.events = {}; this.attributes = {};
    this.dataset = {}; this.value = ""; this.hidden = false; this.disabled = false;
    this.open = false; this.classList = { add() {}, remove() {}, toggle() {} };
  }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return this.text || ""; }
  append(...items) { items.forEach(item => { item.parentNode = this; this.children.push(item); }); }
  replaceChildren(...items) { this.text = ""; this.children = []; this.append(...items); }
  addEventListener(name, fn) { this.events[name] = fn; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  focus() {}
}
const base = { collection_id: 9, instance_token: "b".repeat(32), name: "旅行", revision: 3,
  member_count: 1, face_member_count: 3, photo_count: 7 };
const other = { ...base, collection_id: 10, instance_token: "c".repeat(32), name: "家人" };
const face = (id, candidate = null) => ({ face_id: String(id), asset_id: id, status: "CURRENT",
  thumbnail_url: null, candidate, ignore_state: "EXCLUDED" });
// The real /faces response has an identity binding, not the summary counts.
const facePage = (collection, faces, next = null) => ({ collection: {
  collection_id: collection.collection_id, instance_token: collection.instance_token,
  revision: collection.revision }, faces, has_more: Boolean(next), next_cursor: next });
const response = (body, status = 200) => ({ ok: status === 200, status, json: async () => body });
const flush = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
function harness(handler) {
  const elements = new Map(), requests = [];
  const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const context = { URLSearchParams, URL, Date, crypto: { randomUUID: () => "synthetic" },
    setTimeout: () => 1, clearTimeout() {},
    window: { location: { search: "", href: "http://local/persons", hash: "" },
      confirm: () => true, history: { replaceState() {} }, addEventListener() {} },
    document: { readyState: "loading", cookie: "ps_csrf=csrf", addEventListener() {},
      getElementById: get, createElement: tag => new Element(tag) },
    fetch: async (url, options = {}) => {
      const request = { url, method: options.method || "GET", body: options.body && JSON.parse(options.body) };
      requests.push(request);
      if (url.includes("/photos?")) return response({ assets: [], has_more: false, next_cursor: null });
      const result = await handler(request);
      assert.ok(result, "unexpected request: " + url);
      return result;
    } };
  let code = fs.readFileSync("src/photo_search/web/persons-albums.js", "utf8");
  code = code.replace('  if (document.readyState === "loading")',
    '  globalThis.ui = {state, openCollection, openAlbum, loadCollections, loadCollectionFaces, loadCollectionPeople, loadPhotos, backToLibrary, reloadCollectionFaces, removeCollectionFaceSelection, refreshAfterPhotoFaceWrite};\n  if (document.readyState === "loading")');
  vm.createContext(context); vm.runInContext(code, context);
  return { ui: context.ui, get, requests };
}
async function opened(handler, collection = base) {
  const h = harness(handler); h.ui.openCollection({ ...collection }); await flush();
  h.ui.state.collectionMode = "people"; return h;
}
function click(card, shiftKey = false) {
  card.events.click({ target: card, currentTarget: card, shiftKey, preventDefault() {}, stopPropagation() {} });
}
const scenarios = {
  async suspended_parent_revision() {
    const current = { ...base, revision: 4 };
    const h = await opened(() => response({ collection: current, people: [], has_more: false }));
    const parent = h.ui.state.collection, person = {person_id: 55, name: "成员", asset_count: 1};
    h.ui.state.returnCollection = parent; h.ui.openAlbum(person); await flush();
    assert.equal(await h.ui.loadCollectionPeople(false, parent), false);
    assert.equal(h.ui.state.collection, null, "a changed suspended parent must not replace the active person scope");
    assert.equal(h.ui.state.person.person_id, 55);
    assert.equal(h.ui.state.returnCollection.revision, 4);
    await h.ui.loadPhotos(false);
    assert.ok(h.requests.at(-1).url.includes("/persons/55/photos?"), "the member still browses only their own photos");
    h.ui.backToLibrary(); await flush();
    assert.equal(h.ui.state.collection.revision, 4, "return uses the refreshed parent version");
  },
  async member_late_response() { await lateMemberRequest(false); },
  async member_late_error() { await lateMemberRequest(true); },
  async member_changed_revision() {
    const pending = deferred();
    const h = await opened(() => pending.promise);
    const loading = h.ui.loadCollectionPeople(false);
    Object.assign(h.ui.state.collection, { revision: 4 });
    pending.resolve(response({ collection: base, people: [{person_id: 8, name: "旧成员", asset_count: 1}], has_more: false }));
    assert.equal(await loading, false);
    assert.equal(h.ui.state.collection.revision, 4, "an older response cannot roll the same cached instance back");
    assert.equal(h.get("pa-collection-people").children.length, 0);
  },
  async candidate_pages() {
    const candidate = { person_id: 4, name: "参考人物" };
    const h = await opened(({ url }) => response(facePage(base,
      url.includes("cursor=") ? [face(4, candidate)] : [face(1, candidate), face(2, candidate), face(3)],
      url.includes("cursor=") ? null : "page2")));
    assert.equal(await h.ui.loadCollectionFaces(false), true);
    assert.equal(await h.ui.loadCollectionFaces(true), true);
    const cards = h.get("pa-collection-faces").children;
    assert.equal(cards.length, 4, "candidate and unassigned faces share the flat gallery");
    assert.ok(cards.every(card => card.tagName === "article"));
    assert.deepEqual(cards.map(card => card.faceId), ["1", "2", "3", "4"], "next-page faces preserve server order without duplicate cards");
    assert.match(cards[0].title, /参考人物/, "candidate metadata remains available on each face");
    assert.match(cards[3].title, /参考人物/);
    assert.match(h.get("pa-collection-faces-note").textContent, /^3 张脸/);
  },
  async click_and_shift() {
    const h = await opened(() => response(facePage(base, [face(1), face(2), face(3)])));
    assert.equal(await h.ui.loadCollectionFaces(false), true);
    h.ui.state.collectionFacesManage = true;
    const cards = h.get("pa-collection-faces").children;
    click(cards[0]); click(cards[2]);
    assert.deepEqual([...h.ui.state.selectedCollectionFaces], ["1", "3"], "ordinary clicks must not select the intervening face");
    click(cards[2]); assert.deepEqual([...h.ui.state.selectedCollectionFaces], ["1"], "click again deselects");
    click(cards[0]); click(cards[0]); click(cards[2], true);
    assert.deepEqual([...h.ui.state.selectedCollectionFaces], ["1", "2", "3"]);
    h.ui.state.selectedCollectionFaces = new Set(Array.from({ length: 500 }, (_, i) => "existing-" + i));
    click(cards[0]); assert.equal(h.ui.state.selectedCollectionFaces.size, 500);
  },
  async switch_collection() {
    const h = await opened(({ url }) => response(facePage(url.includes("/10/") ? other : base,
      [face(url.includes("/10/") ? 10 : 9)])));
    await h.ui.loadCollectionFaces(false); h.get("pa-collection-face-section").open = true;
    h.ui.openCollection({ ...other }); await flush();
    assert.equal(h.get("pa-collection-face-section").open, false);
    assert.equal(h.get("pa-collection-faces").children.length, 0);
    assert.equal(h.ui.state.collectionFaceCards.size, 0);
    h.ui.state.collectionMode = "people"; await h.ui.loadCollectionFaces(false);
    assert.deepEqual(h.get("pa-collection-faces").children.map(card => card.faceId), ["10"]);
    h.get("pa-collection-face-section").hidden = false;
    h.ui.openAlbum({ person_id: 5, name: "人物", asset_count: 1 }); await flush();
    assert.equal(h.get("pa-collection-face-section").hidden, true);
    assert.equal(h.get("pa-collection-faces").children.length, 0);
  },
  async late_response() { await staleRequest(false); },
  async late_error() { await staleRequest(true); },
  async revision_refresh() {
    const current = { ...base, revision: 4, face_member_count: 1, photo_count: 6 };
    const h = await opened(({ url }) => url.includes("/faces?")
      ? response(facePage(current, [face(2)]))
      : response({ collection: current }));
    assert.equal(await h.ui.loadCollectionFaces(false), false);
    assert.equal(h.ui.state.collectionFacesStale, true);
    assert.equal(await h.ui.reloadCollectionFaces(), true);
    assert.equal(h.ui.state.collection.revision, 4);
    assert.equal(h.ui.state.collectionFacesStale, false);
    assert.deepEqual(h.get("pa-collection-faces").children.map(card => card.faceId), ["2"]);
    assert.match(h.get("pa-album-note").textContent, /1 张单独收录的人脸 · 6 张去重照片/);
    assert.equal(h.requests.filter(request => !request.url.includes("/faces?") && !request.url.includes("/photos?")).length, 1);
    assert.equal(h.requests.some(request => /collections\?/.test(request.url)), false, "refresh must not recount the collection directory");
  },
  async replaced_instance() {
    const h = await opened(() => response({ collection: { ...base, instance_token: "d".repeat(32) } }));
    h.ui.state.collectionFacesStale = true;
    assert.equal(await h.ui.reloadCollectionFaces(), false);
    assert.equal(h.ui.state.collectionFacesStale, true);
    assert.equal(h.ui.state.collection.instance_token, base.instance_token);
    assert.equal(h.requests.filter(request => request.url.includes("/faces?")).length, 0);
  },
  async remove_counts() { await removeScenario(false); },
  async saved_refresh_failure() { await removeScenario(true); },
  async shared_face_write() {
    const oldPeople=deferred(), oldFaces=deferred(); let peopleReads=0, faceReads=0;
    const updatedFace={...face(1),assigned_person:{person_id:8,name:"新的归属"},ignore_state:"ACTIVE"};
    const h=await opened(({url})=>{
      if(url.includes("/people?")) return ++peopleReads===1 ? oldPeople.promise
        : response({collection:base,people:[{person_id:8,name:"新的归属",asset_count:2}],has_more:false});
      if(url.includes("/faces?")) return ++faceReads===1 ? oldFaces.promise : response(facePage(base,[updatedFace]));
      return response({collection:base});
    });
    const oldPeopleLoad=h.ui.loadCollectionPeople(false), oldFacesLoad=h.ui.loadCollectionFaces(false);
    await h.ui.refreshAfterPhotoFaceWrite(h.ui.state.albumViewSeq);
    assert.equal(peopleReads,2,"an in-flight member read cannot swallow the post-write refresh");
    assert.equal(faceReads,2,"refresh static face metadata even when a pre-write read is busy");
    assert.deepEqual(h.get("pa-collection-faces").children.map(card=>card.faceId),["1"],"assignment changes do not remove a static collection member");
    assert.match(h.get("pa-collection-faces").children[0].title,/新的归属/);
    assert.equal(h.ui.state.collection.revision,base.revision,"metadata refresh does not write membership or revision");
    oldPeople.resolve(response({collection:base,people:[{person_id:7,name:"旧归属",asset_count:3}],has_more:false}));
    oldFaces.resolve(response(facePage(base,[face(2)])));
    assert.equal(await oldPeopleLoad,false); assert.equal(await oldFacesLoad,false);
    assert.deepEqual(h.get("pa-collection-people").children.map(card=>card.personId),[8]);
    assert.deepEqual(h.get("pa-collection-faces").children.map(card=>card.faceId),["1"]);
    assert.ok(h.requests.every(request=>request.method==="GET"),"a notification only reads current authoritative state");
  },
};
async function lateMemberRequest(fail) {
  const oldRequest = deferred(), newRequest = deferred();
  const h = await opened(({ url }) => url.includes("/9/") ? oldRequest.promise : newRequest.promise);
  const oldLoad = h.ui.loadCollectionPeople(false);
  h.ui.openCollection({ ...other }); await flush(); h.ui.state.collectionMode = "people";
  const newLoad = h.ui.loadCollectionPeople(false);
  assert.equal(h.requests.filter(r => r.url.includes("/10/") && r.url.includes("/people?")).length, 1,
    "switching collections starts the new member request while the old request is pending");
  oldRequest.resolve(fail ? response({ code: "OLD_FAILURE" }, 500) : response({ collection: base, people: [], has_more: false }));
  assert.equal(await oldLoad, false);
  assert.equal(h.ui.state.busy.has("collectionPeopleLoad"), true, "an old request cannot release the new request's busy state");
  newRequest.resolve(response({ collection: other, people: [{person_id: 10, name: "新成员", asset_count: 1}], has_more: false }));
  assert.equal(await newLoad, true);
  assert.deepEqual(h.get("pa-collection-people").children.map(card => card.personId), [10]);
}
async function staleRequest(fail) {
  const oldRequest = deferred(), newRequest = deferred();
  const h = await opened(({ url }) => url.includes("/9/") ? oldRequest.promise : newRequest.promise);
  const oldLoad = h.ui.loadCollectionFaces(false);
  h.ui.openCollection({ ...other }); await flush(); h.ui.state.collectionMode = "people";
  const newLoad = h.ui.loadCollectionFaces(false);
  oldRequest.resolve(fail ? response({ code: "OLD_FAILURE" }, 500) : response(facePage(base, [face(9)])));
  assert.equal(await oldLoad, false);
  assert.equal(h.ui.state.busy.has("collectionFacesLoad"), true, "old finally must not release the new request's busy state");
  assert.equal(h.get("pa-collection-faces").children.length, 0);
  assert.doesNotMatch(h.get("pa-collection-faces-note").textContent, /失败/);
  newRequest.resolve(response(facePage(other, [face(10)])));
  assert.equal(await newLoad, true);
  assert.deepEqual(h.get("pa-collection-faces").children.map(card => card.faceId), ["10"]);
}
async function removeScenario(failRefresh) {
  let current = { ...base }, saved = false;
  const remaining = face(3);
  const h = await opened(request => {
    if (request.method === "POST") {
      assert.equal(request.body.action, "remove"); assert.deepEqual(request.body.face_ids, ["1", "2"]);
      assert.equal(request.body.expected_revision, 3);
      saved = true; current = { ...base, revision: 4, face_member_count: 1, photo_count: 6 };
      return response({ changed: 2, revision: 4 });
    }
    if (request.url.includes("/faces?")) return response(facePage(current, saved ? [remaining] : [face(1), face(2), remaining]));
    if (/collections\?/.test(request.url)) return response({ collections: [current], has_more: false });
    if (failRefresh) return response({ code: "REFRESH_FAILURE" }, 503);
    return response({ collection: current });
  });
  await h.ui.loadCollections(false);
  const cached = h.ui.state.collectionById.get(String(base.collection_id));
  await h.ui.loadCollectionFaces(false); h.ui.state.collectionFacesManage = true;
  h.ui.state.selectedCollectionFaces.add("1"); h.ui.state.selectedCollectionFaces.add("2");
  await h.ui.removeCollectionFaceSelection();
  assert.equal(saved, true); assert.equal(h.ui.state.selectedCollectionFaces.size, 0);
  assert.equal(h.requests.filter(request => request.method === "POST").length, 1);
  assert.equal(h.requests.some(request => /hidden|assignment|visibility/.test(request.url)), false);
  if (failRefresh) {
    assert.equal(h.ui.state.collectionFacesStale, true);
    assert.match(h.get("pa-status").textContent, /人脸已移出.*刷新失败/);
  } else {
    assert.equal(h.ui.state.collectionFacesStale, false);
    assert.match(h.get("pa-album-note").textContent, /1 张单独收录的人脸 · 6 张去重照片/);
    assert.match(h.get("pa-collections").children[0].children[0].textContent, /1 张脸 · 6 张照片/);
    assert.equal(cached.revision, 4, "the already-rendered card's action must use the fresh revision");
    assert.equal(h.requests.filter(request => /collections\?/.test(request.url)).length, 1, "removing a face must not reload unrelated collections");
    assert.equal(remaining.ignore_state, "EXCLUDED");
    assert.deepEqual(h.get("pa-collection-faces").children.map(card => card.faceId), ["3"]);
  }
}
const scenario = process.argv[1];
assert.ok(scenarios[scenario], "unknown scenario: " + scenario);
scenarios[scenario]().catch(error => { console.error(error); process.exitCode = 1; });
"""


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_collection_face_interactions(scenario: str) -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for collection face interaction checks"
    result = subprocess.run(
        [node, "-e", NODE_SCRIPT, scenario],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
