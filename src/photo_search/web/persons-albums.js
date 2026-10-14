/* Personal albums reuse the protected API and its authoritative person operations. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById("pa-" + id);
  const inboxFaceSnapshots = new Map();
  const backdropBoundDialogs = new WeakSet();
  let setSearchScopeAction = function () {};
  const state = { organize: false, qualityView: "clean", hiddenView: false, ignoreState: "TEMPORARY", batchMode: false, inboxBatchMode: false, hiddenBatchMode: false, collectionPeopleManage: false, collectionPeopleBusy: false, collectionActionBusy: false, collectionRemoveUnknown: false, collectionPeopleStale: false, selectedPeople: new Set(), selectedPeopleSnapshots: new Map(), pendingWrite: null, collectionSelectedPeople: new Set(), collectionById: new Map(), selectedInboxFaces: new Set(), selectedHiddenFaces: new Set(), selectAnchors: new Map(), visibilityBusy: false, faceVisibilityBusy: false, inboxHiddenVisible: false, inboxHiddenCursor: null, person: null, collection: null, collectionMode: "photos", collectionCursor: null, collectionPeopleCursor: null, collectionListCursor: null, collectionListSeq: 0, collectionSeq: 0, listCursor: null, inboxCursor: null,
    photoCursor: null, photoHasMore: false, photoPager: null, photoIds: [], photoListSeq: 0, previewAssetId: null, previewListSeq: 0, previewHandle: null, previewUI: null,
    previewMoving: false, listSeq: 0, inboxSeq: 0, inboxFilterSeq: 0, photoSeq: 0, previewSeq: 0, editSeq: 0,
    choiceSeq: 0, choiceCursor: null, choiceQuery: "", rematchAfter: 0, rematchRestartAfter: null, hiddenFaceSeq: 0, edit: null, chosen: null,
    detailBusyAssets: new Set(),
    operation: null, undoTimer: null, messageTimer: null, detailBatchTimer: null, detailBatchSeq: 0,
    detailBatch: null, detailControlsLoaded: false, detailControlsPromise: null, writing: false, createPersonIntent: null, coverMode: false, searching: false, searchBody: null, returnCollection: null,
    deepLinked: false, linkSeq: 0, peopleChangeToken: null, inboxConfirmationsPending: false,
    peoplePages: 0, peopleBinding: null, peopleLastLoadedAt: 0, peopleRefreshPending: true,
    peopleLoadPromise: null, peopleLoadBinding: null, inboxPages: 0, inboxBinding: null, inboxSummarySeq: 0,
    collectionListLoaded: false, collectionListPromise: null, libraryPosition: null, timeline: null, timelineScopeKey: "",
    peoplePager: null, collectionPager: null, collectionPeoplePager: null, peopleHasMore: false, collectionsHasMore: false,
    collectionPeopleHasMore: false, busy: new Set() };
  function node(tag, value, className) {
    const item = document.createElement(tag);
    if (value !== undefined) item.textContent = value;
    if (className) item.className = className;
    return item;
  }
  function button(label, action, className) {
    const item = node("button", label, className); item.type = "button";
    item.addEventListener("click", action); return item;
  }
  function closeOnBackdrop(dialog) {
    if (!dialog || backdropBoundDialogs.has(dialog)) return;
    backdropBoundDialogs.add(dialog);
    dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
  }
  function name(person) { return person.name || "未命名的人"; }
  function refreshSearchScopeAction() {
    if ($("album").hidden) { setSearchScopeAction(null, ""); return; }
    if (state.collection) {
      setSearchScopeAction({ kind: "collection", collection_id: Number(state.collection.collection_id),
        instance_token: String(state.collection.instance_token), revision: Number(state.collection.revision),
        name: String(state.collection.name || "人物合集") }, "合集");
    } else if (state.person) {
      setSearchScopeAction({ kind: "person", person_id: Number(state.person.person_id),
        name: String(name(state.person)) }, "相册");
    } else setSearchScopeAction(null, "");
  }
  function photoId(item) { return Number(item.asset_id); }
  function emptySearchNote(page) {
    const channels = page && page.channels || {};
    const vector = channels.vector || {};
    const fts = channels.fts || {};
    const authoritative = channels.authoritative || {};
    if (Number(authoritative.total) > 0 && vector.engaged &&
        Number(vector.engine_blocks) === 0 && Number(fts.candidates) === 0) {
      return "此人物的语义索引尚未就绪，可先查看全部照片。";
    }
    return "没有找到匹配的照片。";
  }
  function photoURL(id, kind) { return "/api/v1/assets/" + id + "/" + kind; }
  function collectionPath(collection, suffix) {
    return "/persons/collections/" + collection.collection_id + "/" + collection.instance_token + (suffix || "");
  }
  // Old responses declared these reusable id URLs immutable for a year. A new
  // URL bypasses those already-fresh browser entries; new responses revalidate.
  function mediaURL(url) { return url + "?v=person-media2"; }
  function picture(url, label, className, priority) {
    const wrap = node("span", undefined, "pa-picture " + (className || ""));
    const fallback = node("span", url ? "正在加载图片…" : "没有可用人脸", "pa-image-fallback");
    wrap.append(fallback);
    if (url && /^\/api\/v1\/(faces\/[a-f0-9]{64}\/thumbnail|assets\/\d+\/(thumbnail|preview))$/.test(url)) {
      const img = node("img"); img.alt = label; img.loading = priority ? "eager" : "lazy";
      if (priority) img.fetchPriority = "high";
      img.addEventListener("load", () => wrap.classList.add("pa-image-ready"));
      img.addEventListener("error", () => { img.hidden = true; fallback.textContent = className && className.includes("reference-avatar") ? "暂无参考头像" : className && className.includes("avatar") ? "头像暂时无法加载" : "图片暂时无法加载"; }); img.src = mediaURL(url); wrap.append(img);
    }
    return wrap;
  }
  function requestId() { return "album-" + crypto.randomUUID(); }
  function errorText(error) {
    if (error.status === 401 || error.status === 403) return "会话已失效，请从启动器的一次性链接重新打开。";
    if (error.code === "PENDING_WRITE") return "上次保存结果尚未确认，请先点“重试上次保存”。";
    if (error.status === 409) return "整理状态有更新，请刷新后再试。" + (error.details && error.details.reason ? "（" + error.details.reason + "）" : "");
    if (error.code && /MODEL|RUNTIME|PROFILE|SPACE|GENERATION/.test(error.code)) return "搜索模型尚未就绪，仍可点“全部照片”浏览相册。";
    return "这次操作没有完成，请重试。" + (error.code ? "（" + error.code + "）" : "");
  }
  async function api(path, body, method) {
    const headers = { Accept: "application/json" };
    if (body) {
      headers["Content-Type"] = "application/json";
      const csrf = document.cookie.split(";").map((part) => part.trim()).find((part) => part.startsWith("ps_csrf="));
      if (!csrf) { const e = new Error(); e.status = 401; throw e; }
      headers["X-CSRF-Token"] = decodeURIComponent(csrf.slice(8));
    }
    const response = await fetch("/api/v1" + path, { method: method || (body ? "POST" : "GET"),
      credentials: "same-origin", cache: "no-store", headers, body: body ? JSON.stringify(body) : undefined });
    const result = await response.json();
    if (!response.ok) {
      const e = new Error(); e.status = response.status;
      e.code = result.code; e.details = result.details; throw e;
    }
    return result;
  }
  function message(value, transient) {
    clearTimeout(state.messageTimer); state.messageTimer = null;
    $("status").textContent = value; $("status").hidden = !value;
    if (transient) state.messageTimer = setTimeout(() => {
      state.messageTimer = null; $("status").textContent = ""; $("status").hidden = true;
    }, 3000);
  }
  function params(values) { return "?" + new URLSearchParams(values); }
  // Replay only the pages the user already opened, using fresh revision-bound cursors.
  async function readLoadedPages(path, values, key, count, current) {
    const items = []; let page, pages = 0;
    for (let index = 0; index < count; index++) {
      page = await api(path + params(values));
      if (!current()) return null;
      items.push(...(page[key] || [])); pages++;
      if (!page.has_more || !page.next_cursor) break;
      values = { ...values, cursor: page.next_cursor };
    }
    return { ...page, [key]: items, loadedPages: pages };
  }
  function libraryItems() { return [...$("cards").children, ...$("inbox-cards").children]; }
  function itemKey(item) { return item.groupKey || (item.personId ? "person:" + item.personId : item.faceId ? "face:" + item.faceId : null); }
  function captureLibraryPosition() {
    if ($("library").hidden) return state.libraryPosition;
    const position = { y: window.scrollY || 0, anchors: [] };
    libraryItems().forEach((item) => {
      if (!item.getBoundingClientRect || !itemKey(item)) return;
      const rect = item.getBoundingClientRect();
      if (rect.bottom > 0 && position.anchors.length < 4) position.anchors.push({ key: itemKey(item), top: rect.top });
    });
    return position;
  }
  function restoreLibraryPosition(position) {
    if (!position || $("library").hidden) return;
    for (const anchor of position.anchors) {
      const item = libraryItems().find((entry) => itemKey(entry) === anchor.key);
      if (item && item.getBoundingClientRect && window.scrollBy) {
        window.scrollBy(0, item.getBoundingClientRect().top - anchor.top); return;
      }
    }
    if (window.scrollTo) window.scrollTo(0, position.y);
  }
  async function refreshPeopleViews(position) {
    // DOM events pass an Event, whereas returning from an album passes a saved position.
    position = position && position.anchors ? position : captureLibraryPosition();
    state.peopleRefreshPending = true;
    // A write/explicit refresh takes precedence over a request started before it.
    if (state.peopleLoadPromise) {
      state.listSeq++; state.peopleLoadPromise = null; state.peopleLoadBinding = null; state.busy.delete("people");
    }
    const loads = [loadPeople(false)];
    if (!$("collections-panel").hidden || state.collectionListLoaded) loads.push(reloadCollections());
    const openGroups = (host) => Array.from(host.children).filter((item) => item.groupKey && item.open).map((item) => item.groupKey);
    if (state.organize) loads.push(loadInbox(false, openGroups($("inbox-cards"))));
    if (state.inboxHiddenVisible) loads.push(loadHiddenFaces(false, openGroups($("inbox-hidden-cards"))));
    // Entering a member album suspends the parent collection rather than closing it.
    const collection = state.collection || state.returnCollection;
    state.collectionSelectedPeople.clear(); updateCollectionSelection();
    if (collection) loads.push(loadCollectionPeople(false, collection));
    if (state.collection && state.collectionMode === "photos") loads.push(loadPhotos(false));
    if ($("edit").open && state.edit && state.edit.mode !== "name") loads.push(loadChoices(false));
    const listSeq = state.listSeq, inboxSeq = state.inboxSeq;
    await Promise.all(loads);
    if (listSeq === state.listSeq && inboxSeq === state.inboxSeq) restoreLibraryPosition(position);
  }
  function notifyPeopleChanged() {
    state.peopleChangeToken = requestId();
    state.peopleRefreshPending = true;
    try { window.localStorage.setItem("aisearch.persons.changed", state.peopleChangeToken); }
    catch (error) { /* Focus/refresh still works if browser storage is unavailable. */ }
  }
  function refreshChangedPeople() {
    if (state.writing) return;
    let changed = false;
    try {
      const token = window.localStorage.getItem("aisearch.persons.changed");
      if (token === state.peopleChangeToken) {
        if (peopleListIsFresh() || (state.busy.has("people") && state.peopleLoadBinding === JSON.stringify(peopleValues()))) return;
      } else changed = true;
      state.peopleChangeToken = token;
    } catch (error) { /* Refresh when returning to a window without browser storage. */ }
    if (!changed && peopleListIsFresh()) return;
    state.peopleRefreshPending = true;
    refreshPeopleViews();
  }
  function peopleValues() {
    const values = { kind: state.hiddenView ? "all" : (state.organize ? "unnamed" : "named"),
      visibility: state.hiddenView ? "hidden" : "visible", q: $("query").value.trim(), sort: "photos_recent_desc", limit: 40,
      collection_state: $("collection-state").checked ? "none" : "all" };
    if (state.hiddenView) values.ignore_state = state.ignoreState;
    if (state.organize && !state.hiddenView) values.quality_view = state.qualityView;
    return values;
  }
  function peopleListIsFresh() {
    return state.peoplePages > 0 && state.peopleBinding === JSON.stringify(peopleValues()) &&
      !state.peopleRefreshPending && !state.busy.has("people") &&
      Date.now() - state.peopleLastLoadedAt < 60000;
  }
  function syncDirectoryPagers(reset) {
    if (reset) [state.peoplePager, state.collectionPager, state.collectionPeoplePager, state.photoPager].forEach((pager) => { if (pager) pager.reset(); });
    if (state.peoplePager) { state.peoplePager.setEnabled(!$('library').hidden); state.peoplePager.update(); }
    if (state.collectionPager) { state.collectionPager.setEnabled(!$('library').hidden && !$('collections-panel').hidden); state.collectionPager.update(); }
    if (state.collectionPeoplePager) {
      state.collectionPeoplePager.setEnabled(!$('album').hidden && Boolean(state.collection) && state.collectionMode === 'people'
        && !state.collectionPeopleBusy && !state.collectionRemoveUnknown && !state.collectionPeopleStale
        && !state.writing && !state.pendingWrite);
      state.collectionPeoplePager.update();
    }
    if (state.photoPager) {
      const ordinaryGrid = state.searching || state.coverMode || !window.PhotoTimeline;
      state.photoPager.setEnabled(!$('album').hidden && ordinaryGrid && Boolean(state.person || (state.collection && state.collectionMode === 'photos')));
      state.photoPager.update();
    }
  }
  function installDirectoryPagers() {
    if (!window.PhotoScrollPager) return;
    state.peoplePager = window.PhotoScrollPager.create({ sentinel: $('people-sentinel'), button: $('more'),
      load: () => loadPeople(true), canLoad: () => !$('library').hidden && state.peopleHasMore && Boolean(state.listCursor) && !state.busy.has('people') });
    state.collectionPager = window.PhotoScrollPager.create({ sentinel: $('collections-sentinel'), button: $('collections-more'),
      load: () => loadCollections(true), canLoad: () => !$('library').hidden && !$('collections-panel').hidden && state.collectionsHasMore && Boolean(state.collectionListCursor) && !state.collectionListPromise });
    state.collectionPeoplePager = window.PhotoScrollPager.create({ sentinel: $('collection-people-sentinel'), button: $('collection-people-more'),
      load: () => loadCollectionPeople(true), canLoad: () => !$('album').hidden && Boolean(state.collection) && state.collectionMode === 'people'
        && state.collectionPeopleHasMore && Boolean(state.collectionPeopleCursor) && !state.busy.has('collectionPeopleLoad') && !state.collectionPeopleBusy && !state.collectionRemoveUnknown
        && !state.collectionPeopleStale && !state.writing && !state.pendingWrite });
    state.photoPager = window.PhotoScrollPager.create({ sentinel: $('photos-sentinel'), button: $('photos-more'),
      load: () => loadPhotos(true), canLoad: () => !$('album').hidden && (state.searching || state.coverMode || !window.PhotoTimeline)
        && Boolean(state.person || (state.collection && state.collectionMode === 'photos')) && state.photoHasMore && Boolean(state.photoCursor) && !state.busy.has('photos') });
    syncDirectoryPagers();
  }
  function updatePersonCard(card, person, index, refreshAvatar) {
    const personId = Number(person.person_id), thumbnailURL = person.thumbnail_url || null;
    const avatarFaceId = person.avatar_face_id || null;
    const avatarChanged = card.thumbnailURL !== thumbnailURL || card.avatarFaceId !== avatarFaceId;
    if (avatarChanged) {
      const replacement = picture(thumbnailURL, name(person), "pa-avatar", index < 4);
      card.replaceChild(replacement, card.avatarPicture); card.avatarPicture = replacement;
      card.thumbnailURL = thumbnailURL; card.avatarFaceId = avatarFaceId;
    }
    const image = Array.from(card.avatarPicture.children).find((child) => child.tagName === "IMG" || child.tag === "img");
    card.avatarImage = image || null;
    if (refreshAvatar && !avatarChanged && card.avatarImage && thumbnailURL) {
      // The media endpoint revalidates with ETag/304. Reassigning the current URL
      // asks the browser to validate without replacing a successfully loaded img.
      card.avatarImage.src = mediaURL(thumbnailURL);
    }
    if (card.avatarImage) {
      card.avatarImage.loading = index < 4 ? "eager" : "lazy";
      card.avatarImage.fetchPriority = index < 4 ? "high" : "auto";
    }
    card.personId = personId; card.latestPerson = person;
    card.setAttribute("data-person-id", String(personId));
    card.avatarImage && (card.avatarImage.alt = name(person));
    card.personName.textContent = name(person);
    card.personCount.textContent = person.asset_count + " 张照片";
    card.collectionLabels.replaceChildren();
    (Array.isArray(person.collections) ? person.collections : []).forEach((collection) => {
      const label = node("span", collection.name, "pa-person-collection");
      label.title = collection.name; label.setAttribute("aria-label", "所属合集：" + collection.name);
      card.collectionLabels.append(label);
    });
    card.collectionLabels.hidden = !card.collectionLabels.children.length;
  }
  function choosePersonRange(target, event) {
    if (!event || !event.shiftKey || !state.batchMode || state.visibilityBusy || state.writing) return false;
    const cards = Array.from($("cards").children).filter((item) => Number.isSafeInteger(item.personId));
    const key = "people:" + String(state.hiddenView) + ":" + JSON.stringify(peopleValues());
    const from = cards.findIndex((item) => item.personId === state.selectAnchors.get(key)), to = cards.indexOf(target);
    if (from < 0 || to < 0) { state.selectAnchors.set(key, target.personId); return false; }
    const range = cards.slice(Math.min(from, to), Math.max(from, to) + 1);
    if (state.selectedPeople.size + range.filter((item) => !state.selectedPeople.has(item.personId)).length > 500) { message("这次连选会超过 500 个人物，未更改已有选择。"); syncPeopleCards(); return true; }
    range.forEach((card) => { state.selectedPeople.add(card.personId); state.selectedPeopleSnapshots.set(card.personId, { ...card.latestPerson }); });
    state.selectAnchors.set(key, target.personId); renderPeopleManagement(); return true;
  }
  function createPersonCard(person, index) {
    const card = node("div", undefined, "pa-person-card"); card.tabIndex = 0; card.setAttribute("role", "button");
    const activate = (event) => { if (state.visibilityBusy || state.writing) return; if (state.batchMode) { if (!choosePersonRange(card, event)) togglePersonSelection(card.personId); } else openAlbum(card.latestPerson); };
    card.addEventListener("click", activate);
    card.addEventListener("keydown", (event) => { if (event.target === card && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); activate(); } });
    card.selectionBox = node("input"); card.selectionBox.type = "checkbox"; card.selectionBox.className = "pt-person-checkbox";
    card.selectionBox.setAttribute("aria-label", "选择这个人物"); card.selectionBox.hidden = true;
    card.selectionBox.addEventListener("click", (event) => {
      event.stopPropagation();
      if (event.shiftKey && choosePersonRange(card, event)) {
        card.rangeChangePending = true;
        setTimeout(() => { card.rangeChangePending = false; syncPeopleCards(); }, 0);
      }
    });
    card.selectionBox.addEventListener("change", () => {
      if (card.rangeChangePending) { card.rangeChangePending = false; syncPeopleCards(); return; }
      if (!state.visibilityBusy && !state.writing) togglePersonSelection(card.personId); syncPeopleCards();
    });
    card.avatarPicture = picture(person.thumbnail_url, name(person), "pa-avatar", index < 4);
    card.personName = node("strong", ""); card.personCount = node("span", "", "hint");
    card.collectionLabels = node("span", undefined, "pa-person-collections");
    card.selectionHint = node("span", "", "hint pa-person-selection");
    card.append(card.avatarPicture, card.personName, card.personCount, card.collectionLabels, card.selectionHint, card.selectionBox);
    updatePersonCard(card, person, index, false);
    return card;
  }
  function reconcilePeopleCards(persons, seq) {
    const host = $("cards"), oldCards = new Map();
    Array.from(host.children).forEach((card) => { if (card.personId && !oldCards.has(Number(card.personId))) oldCards.set(Number(card.personId), card); });
    const kept = new Set(), ordered = [];
    persons.forEach((person, index) => {
      const id = Number(person.person_id);
      if (!Number.isSafeInteger(id) || id <= 0 || kept.has(id)) return;
      kept.add(id);
      const card = oldCards.get(id) || createPersonCard(person, index);
      updatePersonCard(card, person, index, Boolean(oldCards.has(id)));
      ordered.push(card);
    });
    if (seq !== state.listSeq) return;
    ordered.forEach((card) => host.append(card));
    Array.from(host.children).forEach((card) => { if (!ordered.includes(card)) card.remove(); });
  }
  function syncPeopleCards() {
    Array.from($("cards").children).forEach((card) => {
      const personId = Number(card.personId);
      const selected = state.batchMode && state.selectedPeople.has(personId);
      card.setAttribute("aria-pressed", String(selected));
      if (card.selectionBox) { card.selectionBox.hidden = !state.batchMode; card.selectionBox.checked = selected; card.selectionBox.disabled = state.visibilityBusy || state.writing; }
      if (card.selectionHint) {
        card.selectionHint.hidden = !state.batchMode;
        card.selectionHint.textContent = selected ? "已选择" : "点按选择";
      }
    });
  }
  function renderPeopleManagement() {
    $("batch-toggle").setAttribute("aria-pressed", String(state.batchMode));
    $("batch-toggle").textContent = state.batchMode ? "退出批量管理" : "批量管理";
    $("hidden-toggle").textContent = state.hiddenView ? "返回人物列表" : "查看已忽略";
    $("ignore-state-filter").hidden = !state.hiddenView;
    $("ignore-temporary").setAttribute("aria-pressed", String(state.ignoreState === "TEMPORARY"));
    $("ignore-excluded").setAttribute("aria-pressed", String(state.ignoreState === "EXCLUDED"));
    $("ignore-state-label").hidden = !state.batchMode || state.hiddenView;
    $("ignore-state").disabled = state.visibilityBusy || state.writing;
    $("selection-count").hidden = !state.batchMode;
    $("selection-count").textContent = "已选 " + state.selectedPeople.size + " 个人物";
    $("batch-merge").hidden = !state.batchMode;
    $("batch-merge").disabled = state.visibilityBusy || state.writing || state.hiddenView || state.selectedPeople.size === 0;
    $("visibility-submit").hidden = !state.batchMode;
    $("visibility-submit").disabled = state.visibilityBusy || state.selectedPeople.size === 0;
    $("visibility-submit").textContent = state.hiddenView ? "恢复所选" : "忽略所选";
    $("collection-add").hidden = !state.batchMode;
    $("collection-add").disabled = state.visibilityBusy || state.selectedPeople.size === 0;
    $("collection-choice").hidden = !state.batchMode;
    $("collection-hide-label").hidden = !state.batchMode;
    $("people-clear").hidden = !state.batchMode; $("people-clear").disabled = state.visibilityBusy || state.writing;
    $("batch-toggle").disabled = state.visibilityBusy || state.faceVisibilityBusy || state.writing || Boolean(state.pendingWrite);
    $("hidden-toggle").disabled = state.visibilityBusy;
    syncPeopleCards();
    renderFaceManagement();
  }
  function clearPeopleSelection() {
    state.selectedPeople.clear(); state.selectedPeopleSnapshots.clear();
    state.selectAnchors.forEach((_, key) => { if (key.startsWith("people:")) state.selectAnchors.delete(key); });
    state.batchMode = false; renderPeopleManagement();
  }
  function clearSelectedPeople() { if (state.visibilityBusy || state.writing) return; state.selectedPeople.clear(); state.selectedPeopleSnapshots.clear(); renderPeopleManagement(); }
  function togglePersonSelection(personId) {
    state.selectAnchors.set("people:" + String(state.hiddenView) + ":" + JSON.stringify(peopleValues()), personId);
    if (state.selectedPeople.has(personId)) state.selectedPeople.delete(personId);
    else if (state.selectedPeople.size >= 500) { message("一次最多选择 500 人，请先提交当前选择。"); return; }
    else {
      state.selectedPeople.add(personId);
      const card = Array.from($("cards").children).find((item) => item.personId === personId);
      if (card) state.selectedPeopleSnapshots.set(personId, { ...card.latestPerson });
    }
    renderPeopleManagement();
  }
  async function submitVisibility() {
    if (state.visibilityBusy || !state.selectedPeople.size) return;
    if (state.selectedPeople.size > 500) { message("一次最多操作 500 人。"); return; }
    const ids = Array.from(state.selectedPeople);
    const ignoreState = $("ignore-state").value;
    const action = state.hiddenView ? "恢复" : ignoreState === "EXCLUDED" ? "明确排除" : "暂时忽略";
    if (!window.confirm("确定" + action + "所选 " + ids.length + " 个人物吗？")) return;
    state.visibilityBusy = true; renderPeopleManagement();
    try {
      const requestBody = { person_ids: ids, hidden: !state.hiddenView };
      if (!state.hiddenView) requestBody.ignore_state = ignoreState;
      const result = await api("/persons/visibility", requestBody);
      state.selectedPeople.clear(); state.listCursor = null; renderPeopleManagement();
      const refreshes = [loadPeople(false)];
      if (state.organize) refreshes.push(loadInbox(false));
      await Promise.all(refreshes);
      message("已" + action + " " + result.changed + " 个人物。", true);
    } catch (error) { message(action + "没有完成：" + errorText(error)); }
    finally { state.visibilityBusy = false; renderPeopleManagement(); }
  }
  function syncInboxFaceCards(container) {
    if (!container) return;
    Array.from(container.children || []).forEach((item) => {
      if (item.faceId && item.selectionButton) {
        const selected = (item.hiddenFace ? state.selectedHiddenFaces : state.selectedInboxFaces).has(item.faceId);
        const managing = item.hiddenFace ? state.hiddenBatchMode : state.inboxBatchMode;
        item.selectionButton.hidden = !managing;
        item.selectionButton.checked = selected;
        item.selectionButton.disabled = !managing || state.writing || state.pendingWrite || state.faceVisibilityBusy;
        if (item.viewButton) item.viewButton.hidden = managing;
        [item.confirmButton, item.otherPersonButton, item.choosePersonButton].filter(Boolean).forEach((action) => {
          action.hidden = managing; action.disabled = managing || state.writing || Boolean(state.pendingWrite);
        });
        item.setAttribute("data-selected", String(selected));
      }
      syncInboxFaceCards(item);
    });
  }
  function renderFaceManagement() {
    const busy = state.faceVisibilityBusy || state.writing || Boolean(state.pendingWrite);
    const selected = state.selectedInboxFaces.size;
    $("inbox-batch-toggle").setAttribute("aria-pressed", String(state.inboxBatchMode));
    $("inbox-batch-toggle").textContent = state.inboxBatchMode ? "完成整理" : "批量整理";
    $("inbox-batch-toggle").disabled = busy;
    $("inbox-hidden-batch-toggle").setAttribute("aria-pressed", String(state.hiddenBatchMode));
    $("inbox-hidden-batch-toggle").textContent = state.hiddenBatchMode ? "完成管理" : "管理已忽略人脸";
    $("inbox-hidden-batch-toggle").hidden = !state.inboxHiddenVisible;
    $("inbox-hidden-batch-toggle").disabled = busy;
    $("inbox-selection-count").hidden = !state.inboxBatchMode;
    $("inbox-selection-count").textContent = "已选 " + selected + " / 500 张脸";
    $("inbox-hide-submit").hidden = !state.inboxBatchMode;
    $("inbox-hide-submit").disabled = busy || selected === 0 || selected > 500;
    $("inbox-ignore-state-label").hidden = !state.inboxBatchMode;
    $("inbox-ignore-state").disabled = busy;
    $("inbox-restore-submit").hidden = !state.hiddenBatchMode || !state.inboxHiddenVisible;
    $("inbox-restore-submit").disabled = busy || state.selectedHiddenFaces.size === 0 || state.selectedHiddenFaces.size > 500;
    $("inbox-hidden-selection-count").hidden = !state.hiddenBatchMode || !state.inboxHiddenVisible;
    $("inbox-hidden-selection-count").textContent = "已选 " + state.selectedHiddenFaces.size + " 张已忽略人脸";
    $("inbox-clear").hidden = !state.inboxBatchMode; $("inbox-clear").disabled = busy;
    $("inbox-hidden-clear").hidden = !state.hiddenBatchMode || !state.inboxHiddenVisible; $("inbox-hidden-clear").disabled = busy;
    $("inbox-hidden-toggle").setAttribute("aria-expanded", String(state.inboxHiddenVisible));
    $("inbox-hidden-toggle").textContent = state.inboxHiddenVisible ? "收起已忽略人脸" : "查看已忽略人脸";
    $("inbox-hidden-content").hidden = !state.inboxHiddenVisible;
    ["inbox-assign", "inbox-unassign", "inbox-auto"].forEach(id => {
      $(id).hidden = !state.inboxBatchMode;
      $(id).disabled = busy || !selected || selected > 500;
    });
    syncInboxFaceCards($("inbox-cards")); syncInboxFaceCards($("inbox-hidden-cards"));
  }
  function toggleInboxFace(faceId, hidden) {
    if (!(hidden ? state.hiddenBatchMode : state.inboxBatchMode) || state.writing || state.pendingWrite || state.faceVisibilityBusy) { renderFaceManagement(); return; }
    const selected = hidden ? state.selectedHiddenFaces : state.selectedInboxFaces;
    state.selectAnchors.set("faces:" + (hidden ? "hidden:list" : "inbox:list"), faceId);
    if (selected.has(faceId)) selected.delete(faceId);
    else if (selected.size >= 500) { message("一次最多选择 500 张脸，请先提交当前选择。"); renderFaceManagement(); return; }
    else selected.add(faceId);
    renderFaceManagement();
  }
  function loadedInboxCards(hidden) {
    const root = hidden ? $("inbox-hidden-cards") : $("inbox-cards");
    const found = [];
    const isVisible = (item) => { let parent = item.parentNode; while (parent && parent !== root) { if ((parent.tagName === "DETAILS" || parent.tag === "details") && !parent.open) return false; parent = parent.parentNode; } return parent === root; };
    const walk = (item) => { if (item.faceId && item.selectionButton && item.hiddenFace === hidden && isVisible(item)) found.push(item); Array.from(item.children || []).forEach(walk); };
    Array.from(root.children || []).forEach(walk); return found;
  }
  function selectInboxRange(card, event) {
    if (!event.shiftKey || !(card.hiddenFace ? state.hiddenBatchMode : state.inboxBatchMode) || state.writing || state.pendingWrite || state.faceVisibilityBusy) return false;
    const hidden = card.hiddenFace, set = hidden ? state.selectedHiddenFaces : state.selectedInboxFaces;
    const cards = loadedInboxCards(hidden);
    const key = "faces:" + (hidden ? "hidden:list" : "inbox:list"), from = cards.findIndex((item) => item.faceId === state.selectAnchors.get(key)), to = cards.indexOf(card);
    if (from < 0 || to < 0) { state.selectAnchors.set(key, card.faceId); return false; }
    const range = cards.slice(Math.min(from, to), Math.max(from, to) + 1);
    if (set.size + range.filter((item) => !set.has(item.faceId)).length > 500) { message("这次连选会超过 500 张脸，未更改已有选择。"); return true; }
    range.forEach((item) => set.add(item.faceId)); state.selectAnchors.set(key, card.faceId); renderFaceManagement(); return true;
  }
  function clearInboxSelection() {
    const hidden = Boolean(arguments[0]);
    if (!(hidden ? state.hiddenBatchMode : state.inboxBatchMode) || state.writing || state.pendingWrite || state.faceVisibilityBusy) return;
    const selected = hidden ? state.selectedHiddenFaces : state.selectedInboxFaces;
    selected.forEach((id) => inboxFaceSnapshots.delete(id)); selected.clear();
    state.selectAnchors.delete("faces:" + (hidden ? "hidden:list" : "inbox:list")); renderFaceManagement();
  }
  async function editSelectedInbox(action) {
    if (state.writing || state.faceVisibilityBusy) return;
    const faces = Array.from(state.selectedInboxFaces).map(id => inboxFaceSnapshots.get(id));
    if (!faces.length || faces.length > 500 || faces.some(face => !face)) return;
    if (action === "assign") { editBatchFaces(faces); return; }
    if (!window.confirm(action === "unassign" ? "将所选脸移出人物？之后仍需整理。" : "恢复所选脸的自动判断？人工排除保留，不会立即重新检测。")) return;
    try { await mutate("/face-assignments/batch", { contract_version: "p3-face-correction-contracts.v1",
      items: faces.map(face => ({face_id:face.face_id, action, target_person_id:null, expected_revision:face.control_revision})) }, "POST", "已更正所选人脸"); }
    catch (error) { message(errorText(error)); }
  }
  async function submitInboxVisibility(hidden) {
    if (state.faceVisibilityBusy) return;
    const selected = hidden ? state.selectedInboxFaces : state.selectedHiddenFaces;
    if (!selected.size) return;
    if (selected.size > 500) { message("一次最多操作 500 张脸。"); return; }
    const ids = Array.from(selected), ignoreState = hidden ? $("inbox-ignore-state").value : "TEMPORARY";
    const action = !hidden ? "恢复" : ignoreState === "EXCLUDED" ? "明确排除" : "暂时忽略";
    const consequence = ignoreState === "EXCLUDED" && hidden ? "；这些脸将不再参与任何人物推荐" : "";
    if (!window.confirm("确定" + action + "所选 " + ids.length + " 张人脸吗" + consequence + "？")) return;
    const expanded = Array.from($("inbox-cards").children || []).filter((item) => item.groupKey && item.open).map((item) => item.groupKey);
    const expandedHidden = Array.from($("inbox-hidden-cards").children || []).filter((item) => item.groupKey && item.open).map((item) => item.groupKey);
    state.faceVisibilityBusy = true; renderFaceManagement();
    try {
      const result = await api("/faces/inbox/visibility", { face_ids: ids, hidden, ignore_state: ignoreState });
      selected.clear();
      await loadInbox(false, expanded);
      if (state.inboxHiddenVisible) await loadHiddenFaces(false, expandedHidden);
      message("已" + action + " " + result.changed + " 张人脸。", true);
    } catch (error) { message(action + "人脸没有完成：" + errorText(error)); }
    finally { state.faceVisibilityBusy = false; renderFaceManagement(); }
  }
  function requestedPersonId() {
    const search = typeof window !== "undefined" && window.location ? window.location.search : "";
    const value = new URLSearchParams(search || "").get("person_id");
    if (!value || !/^[1-9]\d*$/.test(value)) return null;
    const id = Number(value);
    return Number.isSafeInteger(id) ? id : null;
  }
  function photoReturnRequested() {
    const search = typeof window !== "undefined" && window.location ? window.location.search : "";
    return new URLSearchParams(search || "").get("from_photo") === "photo";
  }
  async function openPersonById(personId) {
    const seq = ++state.linkSeq;
    state.deepLinked = true;
    $("list-note").textContent = "正在打开这个人的照片……";
    try {
      const detail = await api("/persons/" + encodeURIComponent(personId));
      if (seq !== state.linkSeq) return;
      const avatar = /^[a-f0-9]{64}$/.test(String(detail.avatar_face_id || ""))
        ? "/api/v1/faces/" + detail.avatar_face_id + "/thumbnail"
        : null;
      openAlbum({
        person_id: Number(detail.person_id || detail.canonical_person_id),
        name: detail.name,
        revision: detail.revision,
        asset_count: detail.asset_count,
        face_count: detail.face_count,
        thumbnail_url: avatar,
      });
    } catch (error) {
      if (seq !== state.linkSeq) return;
      state.deepLinked = false;
      $("library").hidden = false;
      $("album").hidden = true;
      loadPeople(false);
      message("无法打开人物照片：" + errorText(error));
    }
  }
  function clearPersonRoute() {
    const url = new URL(window.location.href);
    url.searchParams.delete("person_id");
    url.searchParams.delete("from_photo");
    window.history.replaceState(null, "", url.pathname + url.search + url.hash);
  }
  async function loadPeople(more) {
    if (more && state.busy.has("people")) return;
    const values = peopleValues(), binding = JSON.stringify(values);
    if (!more && state.peopleLoadPromise && state.peopleLoadBinding === binding) return state.peopleLoadPromise;
    const seq = ++state.listSeq; state.busy.add("people"); $("more").disabled = true;
    const sameView = binding === state.peopleBinding;
    const append = Boolean(more && state.listCursor && sameView);
    if (!append) state.selectAnchors.delete("people:" + String(state.hiddenView) + ":" + binding);
    const pageCount = append ? 1 : sameView ? Math.min(3, Math.max(1, state.peoplePages)) : 1;
    const resetDeepPosition = !append && sameView && state.peoplePages > pageCount;
    const position = sameView && !resetDeepPosition ? captureLibraryPosition() : null;
    if (resetDeepPosition && window.scrollTo) window.scrollTo(0, 0);
    if (!append && state.peoplePager) state.peoplePager.reset();
    $("people-retry").hidden = true;
    if (more && state.listCursor) values.cursor = state.listCursor;
    if (!sameView) { delete values.cursor; state.listCursor = null; }
    $("list-note").textContent = "正在找人物……";
    const loading = (async () => { try {
      const page = await readLoadedPages("/persons/browse", values, "persons", pageCount, () => seq === state.listSeq);
      if (!page) return false;
      $("people-retry").hidden = true;
      if (!append) reconcilePeopleCards(page.persons || [], seq);
      else {
        const existing = new Set(Array.from($("cards").children).map((card) => Number(card.personId)));
        page.persons.forEach((person, index) => {
          const id = Number(person.person_id);
          if (!Number.isSafeInteger(id) || id <= 0 || existing.has(id)) return;
          const card = createPersonCard(person, $("cards").children.length + index);
          $("cards").append(card); existing.add(id);
        });
      }
      state.peopleBinding = binding; state.peoplePages = append ? state.peoplePages + page.loadedPages : page.loadedPages;
      state.peopleLastLoadedAt = Date.now(); state.peopleRefreshPending = false;
      if (Number.isSafeInteger(page.total) && page.total >= 0) {
        const label = state.hiddenView ? (state.ignoreState === "EXCLUDED" ? "明确排除" : "暂时忽略") : state.organize ? "未命名人物" : "人物";
        $("list-count").textContent = label + " " + page.total + " 人";
        $("list-count").hidden = false;
      } else {
        $("list-count").textContent = "";
        $("list-count").hidden = true;
      }
      syncPeopleCards();
      state.listCursor = page.next_cursor; state.peopleHasMore = Boolean(page.has_more && page.next_cursor); $("more").hidden = !state.peopleHasMore;
      $("list-note").textContent = $("cards").children.length ? (resetDeepPosition ? "列表已刷新到可信首屏；继续向下滚动浏览后续人物。" : state.hiddenView ? (state.ignoreState === "EXCLUDED" ? "明确排除的人物；不会参与人物推荐，可多选后恢复。" : "暂时忽略的人物；可多选后恢复。") : state.organize ? "未命名的人：点开看看，给熟悉的人取名。" : "点一个人，查看全部照片。") : (values.collection_state === "none" ? "当前没有尚未加入合集的人物" : values.q ? "没有找到这个名字，试试“整理一下”。" : state.hiddenView ? (state.ignoreState === "EXCLUDED" ? "没有明确排除的人物。" : "没有暂时忽略的人物。") : state.organize ? "未命名分组已整理完。" : "还没有命名的人，点“整理一下”给熟悉的人取名。导入新照片后，请先完成人脸分析。" );
      restoreLibraryPosition(position); syncDirectoryPagers(); return true;
    } catch (error) { if (seq === state.listSeq) { $("list-note").textContent = "人物列表加载失败：" + errorText(error); $("people-retry").hidden = false; } return false; }
    finally { if (seq === state.listSeq) { state.busy.delete("people"); $("more").disabled = false; }
      syncDirectoryPagers();
      if (state.peopleLoadPromise === loading) { state.peopleLoadPromise = null; state.peopleLoadBinding = null; } }
    })();
    state.peopleLoadPromise = loading; state.peopleLoadBinding = binding;
    return loading;
  }
  async function loadCollections(more, force) {
    if (!more && !force && state.collectionListLoaded) return true;
    if (state.collectionListPromise) return state.collectionListPromise;
    if (more && (!state.collectionListCursor || !state.collectionsHasMore)) return true;
    const seq = ++state.collectionListSeq;
    if (!more) {
      state.collectionsHasMore = false; state.collectionListCursor = null;
      if (state.collectionPager) state.collectionPager.reset();
    }
    const values = { limit: 40 }; if (more) values.cursor = state.collectionListCursor;
    $("collections-note").textContent = "正在加载合集……";
    $("collections-retry").hidden = true;
    const loading = (async () => {
      try {
        const page = await api("/persons/collections" + params(values)); if (seq !== state.collectionListSeq) return false;
        const select = $("collection-choice"), old = select.value;
        if (!more) {
          $("collections").replaceChildren();
          select.replaceChildren(Object.assign(node("option", "选择合集"), { value: "" }));
          state.collectionById.clear();
        }
        const existing = new Set(Array.from($("collections").children).map((card) => String(card.dataset.collectionId || "")));
        (page.collections || []).forEach((collection) => {
          const id = String(collection.collection_id);
          state.collectionById.set(String(collection.collection_id), collection);
          if (existing.has(id)) return;
          existing.add(id);
          const option = node("option", collection.name + "（" + collection.member_count + " 人 · " + collection.photo_count + " 张照片）"); option.value = String(collection.collection_id); select.append(option);
          const card = node("article", undefined, "pa-collection-card");
          card.dataset.collectionId = id;
          card.append(button(collection.name + " · " + collection.member_count + " 人 · " + collection.photo_count + " 张照片", () => openCollection(collection), "pa-collection-open"));
          card.append(button("改名", () => renameCollection(collection), "pa-collection-action"));
          card.append(button("删除", () => deleteCollection(collection), "pa-collection-action"));
          $("collections").append(card);
        });
        if (old) select.value = old;
        state.collectionListCursor = page.next_cursor; state.collectionsHasMore = Boolean(page.has_more && page.next_cursor);
        state.collectionListLoaded = true;
        $("collections-more").hidden = !state.collectionsHasMore;
        $("collections-note").textContent = "";
        syncDirectoryPagers(); return true;
      } catch (error) {
        if (seq === state.collectionListSeq) {
          $("collections-note").textContent = "合集列表加载失败：" + errorText(error);
          $("collections-retry").hidden = false;
        }
        return false;
      } finally {
        if (state.collectionListPromise === loading) state.collectionListPromise = null;
        syncDirectoryPagers();
      }
    })();
    state.collectionListPromise = loading;
    return loading;
  }
  async function ensureAllCollectionsLoaded() {
    if (!state.collectionListLoaded && !(await loadCollections(false))) return false;
    while (state.collectionListCursor) if (!(await loadCollections(true))) return false;
    return true;
  }
  function reloadCollections() {
    state.collectionListSeq++;
    state.collectionListPromise = null;
    return loadCollections(false, true);
  }
  function refreshCollectionsIfUsed() {
    return state.collectionListLoaded || !$("collections-panel").hidden
      ? reloadCollections()
      : Promise.resolve(true);
  }
  async function createCollection(nameValue) {
    const name = nameValue === undefined ? window.prompt("合集名称（最多 80 个字符）") : nameValue;
    if (name === null || name === undefined) return null;
    try {
      const result = await api("/persons/collections", { name });
      await reloadCollections(); return result.collection;
    } catch (error) { message("新建合集失败：" + errorText(error)); return null; }
  }
  async function renameCollection(collection) {
    if (state.writing || state.pendingWrite || state.collectionPeopleBusy || state.collectionActionBusy) return;
    if (state.collectionPeopleManage && state.collection && collection && state.collection.collection_id === collection.collection_id) finishCollectionPeopleManagement();
    const name = window.prompt("合集名称", collection.name); if (name === null) return;
    state.collectionActionBusy = true; updateCollectionSelection();
    try {
      const result = await api(collectionPath(collection), { name }, "PATCH");
      await refreshCollectionsIfUsed();
      if (state.collection && state.collection.collection_id === collection.collection_id) { state.collection = { ...state.collection, ...result.collection }; collectionHeading(); }
      await loadPeople(false); notifyPeopleChanged();
    } catch (error) { message("改名失败：" + errorText(error)); }
    finally { state.collectionActionBusy = false; updateCollectionSelection(); }
  }
  async function deleteCollection(collection) {
    if (state.writing || state.pendingWrite || state.collectionPeopleBusy || state.collectionActionBusy) return;
    if (state.collectionPeopleManage && state.collection && collection && state.collection.collection_id === collection.collection_id) finishCollectionPeopleManagement();
    if (!window.confirm("删除合集“" + collection.name + "”？只删除合集和成员关系。")) return;
    state.collectionActionBusy = true; updateCollectionSelection();
    try { await api(collectionPath(collection), {}, "DELETE"); await refreshCollectionsIfUsed();
      if (state.collection && state.collection.collection_id === collection.collection_id) backToLibrary();
      else await loadPeople(false);
      notifyPeopleChanged();
    } catch (error) { message("删除合集失败：" + errorText(error)); }
    finally { state.collectionActionBusy = false; updateCollectionSelection(); }
  }
  function collectionHeading() {
    if (typeof window !== "undefined" && window.AIPersonTools) window.AIPersonTools.hideFaces();
    document.getElementById("pt-organize").hidden = true;
    $("person-tools-menu").hidden = true;
    $("title").textContent = state.collection.name;
    $("album-kind").textContent = "人物合集";
    $("album-avatar").replaceChildren(node("span", "人物合集", "pa-collection-badge"));
    $("album-note").textContent = Number(state.collection.photo_count || 0) + " 张照片 · 合集只是放在一起看照片；照片按拍摄时间排序，同一张照片只显示一次。";
    $("name").hidden = true; $("merge").hidden = true; $("cover").hidden = true;
    $("search-photos").hidden = true; $("collection-view-controls").hidden = false;
    refreshSearchScopeAction();
  }
  function finishCollectionPeopleManagement() {
    if (state.collectionPeopleBusy || state.writing || state.pendingWrite) return false;
    if (state.collection) state.selectAnchors.delete("collection:" + state.collection.collection_id + ":" + state.collection.instance_token);
    state.collectionPeopleManage = false; state.collectionSelectedPeople.clear(); updateCollectionSelection(); return true;
  }
  function openCollection(collection) {
    if (state.writing || state.pendingWrite || state.collectionActionBusy || state.collectionPeopleBusy) return;
    state.returnCollection = null; state.collectionSeq++;
    state.collection = collection; state.person = null; state.collectionMode = "photos"; state.collectionPeopleCursor = null; state.collectionSelectedPeople.clear(); state.collectionPeopleManage = false; state.collectionRemoveUnknown = false; state.collectionPeopleStale = false;
    state.coverMode = false; state.searching = false; $("library").hidden = true; $("album").hidden = false;
    $("collection-people").hidden = true; $("collection-people-actions").hidden = true; $("collection-people-more").hidden = true; $("photos").hidden = false; $("photos-more").hidden = false;
    $("collection-photos-tab").setAttribute("aria-pressed", "true"); $("collection-people-tab").setAttribute("aria-pressed", "false");
    collectionHeading(); loadPhotos(false); syncDirectoryPagers(); $("back").focus();
  }
  function selectCollectionPersonRange(card) {
    if (!state.collection || state.writing || state.pendingWrite || state.collectionPeopleBusy || state.collectionActionBusy || state.collectionRemoveUnknown || state.collectionPeopleStale) return true;
    const collection = state.collection;
    const key = "collection:" + collection.collection_id + ":" + collection.instance_token;
    const cards = Array.from($("collection-people").children).filter((item) => Number.isSafeInteger(item.personId));
    const from = cards.findIndex((item) => item.personId === state.selectAnchors.get(key)), to = cards.indexOf(card);
    if (to < 0) return true;
    const range = from < 0 ? [card] : cards.slice(Math.min(from, to), Math.max(from, to) + 1);
    if (state.collectionSelectedPeople.size + range.filter((item) => !state.collectionSelectedPeople.has(item.personId)).length > 500) {
      updateCollectionSelection(); message("这次连选会超过 500 个人物，未更改已有选择。"); return true;
    }
    range.forEach((item) => state.collectionSelectedPeople.add(item.personId));
    state.selectAnchors.set(key, card.personId); updateCollectionSelection(); return true;
  }
  function activateCollectionPerson(card, collection, event) {
    if (state.collectionPeopleBusy || state.collectionActionBusy || state.collectionRemoveUnknown || state.collectionPeopleStale || state.writing || state.pendingWrite) return;
    if (!state.collectionPeopleManage) { state.returnCollection = collection; openAlbum(card.latestPerson); return; }
    if (event && event.shiftKey) { selectCollectionPersonRange(card); return; }
    if (state.collectionSelectedPeople.has(card.personId)) state.collectionSelectedPeople.delete(card.personId);
    else if (state.collectionSelectedPeople.size >= 500) { message("一次最多选择 500 个人物。"); return; }
    else state.collectionSelectedPeople.add(card.personId);
    state.selectAnchors.set("collection:" + collection.collection_id + ":" + collection.instance_token, card.personId);
    updateCollectionSelection();
  }
  async function loadCollectionPeople(more, parentCollection) {
    const collection = parentCollection || state.collection;
    if (!collection || state.busy.has("collectionPeopleLoad") || (more && (!state.collectionPeopleCursor || !state.collectionPeopleHasMore))) return false;
    if (!more) state.selectAnchors.delete("collection:" + collection.collection_id + ":" + collection.instance_token);
    const seq = ++state.collectionSeq;
    const values = { limit: 40 }; if (more) values.cursor = state.collectionPeopleCursor;
    else { $("collection-people").replaceChildren(); $("collection-people-retry").hidden = true; state.collectionPeopleCursor = null; state.collectionPeopleHasMore = false; if (state.collectionPeoplePager) state.collectionPeoplePager.reset(); }
    state.busy.add("collectionPeopleLoad"); syncDirectoryPagers();
    try {
      const page = await api(collectionPath(collection, "/people") + params(values));
      const current = state.collection || state.returnCollection;
      if (seq !== state.collectionSeq || !current) return false;
      if (current.collection_id !== collection.collection_id || current.instance_token !== collection.instance_token) {
        state.collectionSelectedPeople.clear(); state.collectionPeopleManage = false; state.collectionPeopleStale = true;
        state.selectAnchors.delete("collection:" + collection.collection_id + ":" + collection.instance_token);
        state.collectionPeopleHasMore = false; updateCollectionSelection(); message("合集实例已变化，请刷新合集人物后重试。"); return false;
      }
      const returnedCollection = page.collection;
      if (!returnedCollection || Number(returnedCollection.collection_id) !== Number(collection.collection_id)
          || returnedCollection.instance_token !== collection.instance_token) {
        state.collectionSelectedPeople.clear(); state.collectionPeopleManage = false; state.collectionPeopleStale = true;
        state.selectAnchors.delete("collection:" + collection.collection_id + ":" + collection.instance_token);
        $("collection-people").replaceChildren(); state.collectionPeopleHasMore = false; message("合集实例已变化，请刷新合集人物后重试。"); updateCollectionSelection(); return false;
      }
      if (Number(returnedCollection.revision) !== Number(collection.revision)) {
        state.collection = { ...current, ...returnedCollection };
        state.collectionSelectedPeople.clear(); state.collectionPeopleManage = false;
        state.selectAnchors.delete("collection:" + collection.collection_id + ":" + collection.instance_token);
        state.collectionPeopleStale = true; state.collectionPeopleCursor = null; state.collectionPeopleHasMore = false;
        $("collection-people").replaceChildren();
        message("合集人物版本已变化，请刷新后重新选择。"); updateCollectionSelection(); return false;
      }
      state.collectionPeopleStale = false;
      const existingPeople = new Set(Array.from($("collection-people").children).map((card) => Number(card.personId)));
      (page.people || []).forEach((person) => {
        const card = node("div", undefined, "pa-person-card"); card.tabIndex = 0; card.setAttribute("role", "button");
        const id = Number(person.person_id), selected = state.collectionSelectedPeople.has(id);
        if (!Number.isSafeInteger(id) || id <= 0 || existingPeople.has(id)) return;
        existingPeople.add(id);
        card.personId = id; card.latestPerson = person;
        const choose = node("input"); choose.type = "checkbox"; choose.className = "pt-person-checkbox";
        choose.setAttribute("aria-label", "选择人物“" + name(person) + "”移出合集");
        choose.checked = selected; choose.hidden = !state.collectionPeopleManage;
        choose.disabled = !state.collectionPeopleManage || state.writing || Boolean(state.pendingWrite) || state.collectionPeopleBusy || state.collectionActionBusy || state.collectionRemoveUnknown || state.collectionPeopleStale;
        choose.addEventListener("click", (event) => {
          event.stopPropagation();
          if (state.writing || state.pendingWrite || state.collectionPeopleBusy || state.collectionActionBusy || state.collectionRemoveUnknown || state.collectionPeopleStale) {
            event.preventDefault(); setTimeout(updateCollectionSelection, 0); return;
          }
          if (event.shiftKey) {
            event.preventDefault(); selectCollectionPersonRange(card); setTimeout(updateCollectionSelection, 0);
          }
        });
        choose.addEventListener("change", () => {
          if (!state.collectionPeopleManage || state.writing || state.pendingWrite || state.collectionPeopleBusy || state.collectionActionBusy || state.collectionRemoveUnknown || state.collectionPeopleStale) { updateCollectionSelection(); return; }
          if (choose.checked) {
            if (!state.collectionSelectedPeople.has(id) && state.collectionSelectedPeople.size >= 500) { message("一次最多选择 500 个人物。"); updateCollectionSelection(); return; }
            state.collectionSelectedPeople.add(id);
          } else state.collectionSelectedPeople.delete(id);
          state.selectAnchors.set("collection:" + collection.collection_id + ":" + collection.instance_token, id); updateCollectionSelection();
        });
        card.selectionBox = choose;
        card.addEventListener("click", (event) => { if (event.target !== choose) activateCollectionPerson(card, collection, event); });
        card.addEventListener("keydown", (event) => {
          if (event.target === card && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); activateCollectionPerson(card, collection, event); }
        });
        card.avatarPicture = picture(person.thumbnail_url, name(person), "pa-avatar");
        card.personName = node("strong", name(person)); card.personCount = node("span", person.asset_count + " 张照片", "hint");
        card.collectionLabels = node("span", undefined, "pa-person-collections");
        card.selectionHint = node("span", state.collectionPeopleManage ? (selected ? "已选择" : "点按选择") : "", "hint pa-person-selection");
        card.selectionHint.hidden = !state.collectionPeopleManage;
        card.append(card.avatarPicture, card.personName, card.personCount, card.collectionLabels, card.selectionHint, choose);
        card.setAttribute("aria-pressed", String(state.collectionPeopleManage && selected));
        card.setAttribute("data-selected", String(selected));
        $("collection-people").append(card);
      });
      const showingPeople = Boolean(state.collection && state.collectionMode === "people");
      $("collection-people-actions").hidden = !showingPeople; updateCollectionSelection();
      state.collectionPeopleCursor = page.next_cursor; state.collectionPeopleHasMore = Boolean(page.has_more && page.next_cursor);
      $("collection-people-more").hidden = !showingPeople || !state.collectionPeopleHasMore; $("collection-people-retry").hidden = true; syncDirectoryPagers(); return true;
    } catch (error) {
      const current = state.collection || state.returnCollection;
      if (seq === state.collectionSeq && current && current.collection_id === collection.collection_id
        && current.instance_token === collection.instance_token) {
        if (error.status === 409 || error.code === "INVALID_CURSOR") {
          state.collectionPeopleStale = true; state.collectionSelectedPeople.clear(); state.collectionPeopleManage = false;
          state.selectAnchors.delete("collection:" + collection.collection_id + ":" + collection.instance_token);
          state.collectionPeopleCursor = null; state.collectionPeopleHasMore = false;
          $("collection-people").replaceChildren(); message("合集人物版本已变化，请刷新后重新选择。"); updateCollectionSelection();
        } else {
          if (!more && state.collectionMode === "people") $("collection-people-retry").hidden = false;
          message("合集人物加载失败：" + errorText(error));
        }
      }
      return false;
    } finally { state.busy.delete("collectionPeopleLoad"); syncDirectoryPagers(); }
  }
  function updateCollectionSelection() {
    const managing = state.collectionPeopleManage, busy = state.writing || Boolean(state.pendingWrite) || state.collectionPeopleBusy || state.collectionActionBusy || state.collectionRemoveUnknown || state.collectionPeopleStale;
    $("collection-manage-toggle").textContent = managing ? "完成管理" : "管理合集人物";
    $("collection-manage-toggle").setAttribute("aria-pressed", String(managing));
    $("collection-manage-toggle").disabled = busy;
    $("collection-rename").disabled = busy; $("collection-delete").disabled = busy;
    $("collection-people-selected").hidden = !managing; $("collection-people-selected").textContent = "已选 " + state.collectionSelectedPeople.size + " 人";
    ["collection-people-select-loaded", "collection-people-invert-loaded", "collection-remove", "collection-selection-clear"].forEach((id) => { $(id).hidden = !managing; });
    $("collection-people-reload").hidden = !state.collectionRemoveUnknown && !state.collectionPeopleStale;
    Array.from($("collection-people").children).forEach((card) => {
      if (card.selectionBox) {
        const selected = state.collectionSelectedPeople.has(card.personId);
        card.selectionBox.checked = selected; card.selectionBox.hidden = !managing; card.selectionBox.disabled = !managing || busy;
        card.setAttribute("data-selected", String(selected));
        card.setAttribute("aria-pressed", String(managing && selected));
        if (card.selectionHint) { card.selectionHint.hidden = !managing; card.selectionHint.textContent = selected ? "已选择" : "点按选择"; }
      }
    });
    $("collection-remove").disabled = busy || state.collectionSelectedPeople.size === 0;
    $("collection-people-select-loaded").disabled = busy;
    $("collection-people-invert-loaded").disabled = busy;
    ["collection-selection-clear", "collection-remove"].forEach((id) => { $(id).disabled = busy || (id === "collection-remove" && state.collectionSelectedPeople.size === 0); });
  }
  function selectLoadedCollectionPeople(invert) {
    if (!state.collectionPeopleManage || state.writing || state.pendingWrite || state.collectionPeopleBusy || state.collectionRemoveUnknown || state.collectionPeopleStale) return;
    const cards = Array.from($("collection-people").children).filter((item) => Number.isSafeInteger(item.personId));
    const selectedLoaded = cards.filter((card) => state.collectionSelectedPeople.has(card.personId)).length;
    const unselectedLoaded = cards.length - selectedLoaded;
    const projected = invert ? state.collectionSelectedPeople.size - selectedLoaded + unselectedLoaded : state.collectionSelectedPeople.size + unselectedLoaded;
    if (projected > 500) {
      message("全选已加载会超过 500 个人物，未更改已有选择。"); return;
    }
    cards.forEach((card) => {
      if (invert && state.collectionSelectedPeople.has(card.personId)) state.collectionSelectedPeople.delete(card.personId);
      else state.collectionSelectedPeople.add(card.personId);
    });
    updateCollectionSelection();
  }
  function clearCollectionSelection() {
    if (!state.collectionPeopleManage || state.writing || state.pendingWrite || state.collectionPeopleBusy || state.collectionRemoveUnknown || state.collectionPeopleStale) return;
    state.collectionSelectedPeople.clear(); updateCollectionSelection();
  }
  async function removeCollectionSelection() {
    if (state.writing || state.pendingWrite || state.collectionPeopleBusy || state.collectionRemoveUnknown || state.collectionPeopleStale || !state.collection || !state.collectionPeopleManage || !state.collectionSelectedPeople.size) return;
    if (state.collectionSelectedPeople.size > 500) { message("一次最多移出 500 个人物。"); return; }
    const ids = Array.from(state.collectionSelectedPeople);
    if (!window.confirm("从这个合集中移出所选 " + ids.length + " 人，并恢复到人物或未命名列表？")) return;
    const collection = state.collection;
    state.collectionPeopleBusy = true; updateCollectionSelection();
    try {
      await api(collectionPath(collection, "/members"), { action: "remove", person_ids: ids });
      if (!state.collection || state.collection.collection_id !== collection.collection_id || state.collection.instance_token !== collection.instance_token) return;
      state.collectionSelectedPeople.clear(); state.collectionPeopleManage = false; state.selectAnchors.delete("collection:" + collection.collection_id + ":" + collection.instance_token);
      state.collectionPeopleCursor = null; await loadCollectionPeople(false); await refreshCollectionsIfUsed();
      await loadPeople(false); notifyPeopleChanged();
      message("已从合集移出，可在人物或未命名列表找到。", true);
    } catch (error) {
      if (error.status === 409 || error.code === "INVALID_CURSOR") {
        state.collectionPeopleStale = true; state.collectionPeopleManage = false; state.collectionSelectedPeople.clear();
        state.selectAnchors.delete("collection:" + collection.collection_id + ":" + collection.instance_token);
      } else if (!error.status || error.status >= 500 || error.code === "NETWORK_ERROR") state.collectionRemoveUnknown = true;
      message(state.collectionRemoveUnknown ? "移出结果暂时无法确认，请刷新合集人物核对后再操作。" : "移出合集失败：" + errorText(error));
    } finally { state.collectionPeopleBusy = false; updateCollectionSelection(); }
  }
  async function reloadCollectionPeopleAfterUnknown() {
    if ((!state.collectionRemoveUnknown && !state.collectionPeopleStale) || state.collectionPeopleBusy || !state.collection) return;
    const collection = state.collection; state.collectionPeopleBusy = true; updateCollectionSelection();
    try {
      if (!(await reloadCollections()) || !(await ensureAllCollectionsLoaded())) throw new Error("合集列表刷新失败");
      const current = state.collectionById.get(String(collection.collection_id));
      if (!current || current.instance_token !== collection.instance_token) throw new Error("合集实例已变化，请返回后重新打开");
      state.collection = current; state.collectionRemoveUnknown = false; state.collectionPeopleStale = false; state.collectionPeopleManage = false;
      state.collectionSelectedPeople.clear(); state.collectionPeopleCursor = null;
      state.selectAnchors.delete("collection:" + collection.collection_id + ":" + collection.instance_token);
      await loadCollectionPeople(false, current);
      message("合集人物已刷新，请核对当前成员后再操作。", true);
    } catch (error) { message("刷新合集人物失败：" + errorText(error)); }
    finally { state.collectionPeopleBusy = false; updateCollectionSelection(); }
  }
  async function addSelectionToCollection() {
    if (!state.selectedPeople.size || state.visibilityBusy) return;
    if (!(await ensureAllCollectionsLoaded())) {
      $("list-note").textContent = "合集选项暂不可用，请先重试合集列表。";
      return;
    }
    const selected = $("collection-choice").value;
    let collection = selected ? state.collectionById.get(selected) : null;
    if (!selected) {
      collection = await createCollection(); if (!collection) return;
      state.collectionById.set(String(collection.collection_id), collection);
    }
    if (!collection) { message("合集已变化，请刷新后重试。"); return; }
    const ids = Array.from(state.selectedPeople);
    if (!window.confirm("将所选 " + ids.length + " 人加入合集？" + ($("collection-hide").checked ? "并同时忽略人物列表中的这些人。" : ""))) return;
    state.visibilityBusy = true; renderPeopleManagement();
    try {
      await api(collectionPath(collection, "/members"), { action: "add", person_ids: ids, hide_after_add: $("collection-hide").checked });
      state.selectedPeople.clear(); state.listCursor = null; await loadPeople(false); await refreshCollectionsIfUsed();
      notifyPeopleChanged();
      message("已加入合集。", true);
    } catch (error) { message("加入合集失败：" + errorText(error)); }
    finally { state.visibilityBusy = false; renderPeopleManagement(); }
  }
  function backToLibrary() {
    if (state.writing || state.pendingWrite || state.collectionPeopleBusy) return;
    if (typeof window !== "undefined" && window.AIPersonTools) window.AIPersonTools.hideFaces();
    if (state.returnCollection) {
      state.collection = state.returnCollection; state.returnCollection = null; state.person = null;
      state.collectionMode = "people"; state.collectionPeopleManage = false; state.collectionSelectedPeople.clear(); state.collectionSeq++; state.photoSeq++;
      $("album").hidden = false; $("library").hidden = true; $("collection-view-controls").hidden = false;
      $("collection-people").hidden = false; $("collection-people-actions").hidden = false; updateCollectionSelection();
      $("collection-photos-tab").setAttribute("aria-pressed", "false"); $("collection-people-tab").setAttribute("aria-pressed", "true");
      $("photos").hidden = true; $("photos-more").hidden = true; collectionHeading(); loadCollectionPeople(false); syncDirectoryPagers(); $("back").focus(); return;
    }
    state.person = null; state.collection = null; state.collectionSeq++; state.photoSeq++; state.busy.delete("photos");
    $("library").hidden = false; $("album").hidden = true; $("collection-view-controls").hidden = true;
    refreshSearchScopeAction();
    $("name").hidden = false; $("merge").hidden = false; $("cover").hidden = false; $("search-photos").hidden = false;
    restoreLibraryPosition(state.libraryPosition);
    syncDirectoryPagers();
    if (!peopleListIsFresh() && !(state.busy.has("people") && state.peopleLoadBinding === JSON.stringify(peopleValues()))) {
      refreshPeopleViews(state.libraryPosition);
    }
    $("people").focus({ preventScroll: true });
  }
  function organizationText(face) {
    if (face.display_quality_reason === "DISPLAY_LOW_QUALITY") return "疑似低质量，仅整理页自动收起";
    const reason = face.organization_reason || face.quality_reason;
    if (face.mode === "MANUAL_UNASSIGNED") return "已移出人物，等你指定";
    const labels = { SMALL: "脸太小，需确认", BLURRY: "脸有些模糊，需确认",
      LOW_DETECTION_SCORE: "人脸不够明确，需确认", LOW_QUALITY: "清晰度不足，需确认",
      NEAR_TIE: "像多个人，需确认", SAME_IMAGE_CONFLICT: "合照里的人选有冲突，需确认",
      WAITING_INDEX: "人脸结果正在整理", NO_MATCH: "还没有可靠的人物匹配" };
    if (labels[reason]) return labels[reason];
    if (reason === "BORDERLINE" && face.suggestions && face.suggestions.length) {
      return "像“" + name(face.suggestions[0]) + "”，需确认";
    }
    return "还没确定是谁";
  }
  function inboxSummaryText(summary) {
    const reasons = summary.reasons || {};
    const pieces = ["待整理 " + summary.total + " 张脸"];
    const labels = { SMALL: "小脸", BLURRY: "模糊", LOW_DETECTION_SCORE: "人脸不明确",
      LOW_QUALITY: "清晰度不足", BORDERLINE: "相似待确认", NEAR_TIE: "人选相近",
      SAME_IMAGE_CONFLICT: "合照冲突", MANUAL_UNASSIGNED: "已移出", WAITING_INDEX: "等待整理",
      NO_MATCH: "暂无匹配", OTHER_SUGGESTION: "其他建议" };
    Object.keys(labels).forEach((key) => { if (reasons[key]) pieces.push(labels[key] + " " + reasons[key]); });
    if (state.qualityView === "clean" && summary.quality_hidden) pieces.push("另有 " + summary.quality_hidden + " 张疑似低质量脸暂时收起");
    if (summary.held) pieces.push("另有 " + summary.held + " 张脸等待识别更新确认");
    return pieces.join(" · ");
  }
  function renderInboxFace(container, face, hiddenFace, groupDetails) {
    const card = node("div", undefined, "pa-person-card");
    card.faceId = String(face.face_id); card.face = face; card.hiddenFace = Boolean(hiddenFace);
    card.rangeScope = (hiddenFace ? "hidden:" : "inbox:") + (groupDetails ? "group:" + groupDetails.groupKey : "list");
    const selected = (card.hiddenFace ? state.selectedHiddenFaces : state.selectedInboxFaces).has(card.faceId);
    if (!selected) inboxFaceSnapshots.set(card.faceId, { ...face });
    const selection = node("input"); selection.type = "checkbox"; selection.className = "pt-avatar-checkbox";
    selection.checked = selected; selection.setAttribute("aria-label", hiddenFace ? "选择这张已忽略人脸" : "选择这张待整理人脸");
    selection.addEventListener("click", (event) => {
      event.stopPropagation();
      if (state.writing || state.pendingWrite || state.faceVisibilityBusy) {
        event.preventDefault(); setTimeout(renderFaceManagement, 0); return;
      }
      if (event.shiftKey && selectInboxRange(card, event)) {
        event.preventDefault(); setTimeout(renderFaceManagement, 0);
      }
    });
    selection.addEventListener("change", () => {
      if (!(card.hiddenFace ? state.hiddenBatchMode : state.inboxBatchMode) || state.writing || state.pendingWrite || state.faceVisibilityBusy) { renderFaceManagement(); return; }
      const set = card.hiddenFace ? state.selectedHiddenFaces : state.selectedInboxFaces;
      if (selection.checked) {
        if (!set.has(card.faceId) && set.size >= 500) { message("一次最多选择 500 张脸，请先提交当前选择。"); renderFaceManagement(); return; }
        set.add(card.faceId);
      } else set.delete(card.faceId);
      state.selectAnchors.set("faces:" + (card.hiddenFace ? "hidden:list" : "inbox:list"), card.faceId);
      renderFaceManagement();
    });
    selection.hidden = !(hiddenFace ? state.hiddenBatchMode : state.inboxBatchMode); card.selectionButton = selection;
    const suggestion = face.suggestions && face.suggestions[0];
    const comparison = node("div", undefined, "pa-inbox-face-comparison");
    const left = node("div", undefined, "pa-inbox-compare-side pa-inbox-compare-face");
    const avatar = node("div", undefined, "pt-avatar-select");
    avatar.append(picture(face.thumbnail_url, "待整理人脸", "pa-avatar"), selection);
    selection.disabled = !(hiddenFace ? state.hiddenBatchMode : state.inboxBatchMode) || state.writing || Boolean(state.pendingWrite) || state.faceVisibilityBusy;
    avatar.addEventListener("click", event => {
      if (event.target === selection) return;
      if (hiddenFace ? state.hiddenBatchMode : state.inboxBatchMode) {
        event.preventDefault(); event.stopPropagation();
        if (event.shiftKey && selectInboxRange(card, event)) return;
        toggleInboxFace(card.faceId, card.hiddenFace);
      }
      else { event.preventDefault(); preview(face.asset_id, event.currentTarget); }
    });
    left.append(avatar, node("span", hiddenFace
      ? (face.ignore_state === "EXCLUDED" ? "明确排除" : "暂时忽略")
      : "待整理", "hint"));
    comparison.append(left);
    if (suggestion) {
      const right = node("div", undefined, "pa-inbox-compare-side pa-inbox-compare-reference");
      const reference = picture(suggestion.thumbnail_url, "参考头像：" + name(suggestion), "pa-avatar pa-reference-avatar");
      if (!suggestion.thumbnail_url) reference.children[0].textContent = "暂无参考头像";
      right.append(reference, node("span", "可能是" + name(suggestion), "hint"));
      comparison.append(right);
    }
    card.append(comparison);
    card.viewButton = button("查看整张照片", event => preview(face.asset_id, event.currentTarget));
    card.viewButton.addEventListener("click", event => event.stopPropagation());
    card.append(card.viewButton, node("span", organizationText(face), "hint"));
    if (suggestion) {
      card.confirmButton = button("确认是" + name(suggestion), () => confirmSuggestion(face, suggestion, card, groupDetails));
      card.confirmButton.setAttribute("aria-pressed", "false");
      card.otherPersonButton = button("选其他人", () => editFace(face, { allowCreatePerson: true }));
      card.append(card.confirmButton, card.otherPersonButton);
    } else {
      card.choosePersonButton = button(hiddenFace ? "选择人物" : "这是谁？", () => editFace(face, { allowCreatePerson: true }));
      card.append(card.choosePersonButton);
    }
    card.addEventListener("click", event => {
      if ([selection, card.viewButton, card.confirmButton, card.otherPersonButton, card.choosePersonButton].includes(event.target)) return;
      if (hiddenFace ? state.hiddenBatchMode : state.inboxBatchMode) {
        if (event.shiftKey && selectInboxRange(card, event)) return;
        toggleInboxFace(card.faceId, card.hiddenFace);
      }
    });
    container.append(card);
  }
  function flushInboxConfirmations() {
    if (!state.inboxConfirmationsPending || state.writing) return;
    const openConfirmed = Array.from($("inbox-cards").children).some((item) => item.open && item.confirmationDirty);
    if (!openConfirmed) { state.inboxConfirmationsPending = false; refreshPeopleViews(); }
  }
  async function confirmSuggestion(face, person, card, groupDetails) {
    if (state.pendingWrite) { message(errorText({code:"PENDING_WRITE"})); return; }
    if (state.writing || card.confirmed) return;
    state.writing = true;
    const confirm = card.confirmButton, label = "确认是" + name(person);
    confirm.disabled = true; confirm.textContent = "正在确认……"; card.otherPersonButton.disabled = true;
    card.confirmRequestId = card.confirmRequestId || requestId();
    try {
      const result = await api("/faces/" + encodeURIComponent(face.face_id) + "/assignment", {
        contract_version: "p3-face-correction-contracts.v1", request_id: card.confirmRequestId,
        action: "assign", target_person_id: person.person_id,
        expected_revision: face.control_revision,
      }, "PUT");
      card.confirmed = true; confirm.textContent = "✓ 已确认是" + name(person); confirm.setAttribute("aria-pressed", "true");
      rememberOperation(result, "已确认是“" + name(person) + "”"); notifyPeopleChanged();
      if (card.hiddenFace) {
        refreshPeopleViews();
      } else {
        state.inboxConfirmationsPending = true;
        $("inbox-more").disabled = true;
        // Every group cursor shares the catalog revision changed by this confirmation.
        Array.from($("inbox-cards").children).filter((item) => item.groupKey).forEach((item) => {
          item.confirmationVersion = (item.confirmationVersion || 0) + 1;
          item.querySelector(".pa-inbox-group-more").dataset.cursor = "";
        });
        if (groupDetails) groupDetails.confirmationDirty = true;
      }
    } catch (error) {
      if (error.status && error.status < 500) card.confirmRequestId = null;
      confirm.textContent = label; message(errorText(error));
    }
    finally {
      state.writing = false; confirm.disabled = Boolean(card.confirmed); card.otherPersonButton.disabled = Boolean(card.confirmed);
      // Closing while the request is in flight also waits for the saved result.
      if (!card.hiddenFace && groupDetails && !groupDetails.open) flushInboxConfirmations();
    }
  }
  async function loadInboxGroup(details, groupKey, kind, cursor, hiddenFace) {
    const list = details.querySelector(".pa-inbox-group-faces");
    const more = details.querySelector(".pa-inbox-group-more");
    more.disabled = true;
    const filterSeq = state.inboxFilterSeq;
    const listSeq = hiddenFace ? state.hiddenFaceSeq : state.inboxSeq;
    const current = () => hiddenFace ? listSeq === state.hiddenFaceSeq : listSeq === state.inboxSeq && filterSeq === state.inboxFilterSeq;
    const confirmationVersion = details.confirmationVersion || 0;
    try {
      const values = { limit: 30, kind, quality_view: state.qualityView };
      if (cursor) values.cursor = cursor;
      const route = hiddenFace ? "/faces/inbox/hidden/groups/" : "/faces/inbox/groups/";
      const page = await readLoadedPages(route + encodeURIComponent(groupKey), values, "faces",
        cursor ? 1 : Math.max(1, details.loadedPages || 0), current);
      if (!page) return;
      const shown = new Set(Array.from(list.children).map((card) => card.faceId));
      page.faces.forEach((face) => {
        if (!shown.has(String(face.face_id))) { renderInboxFace(list, face, Boolean(hiddenFace), details); shown.add(String(face.face_id)); }
      });
      more.hidden = !page.has_more;
      details.loadedPages = cursor ? (details.loadedPages || 0) + page.loadedPages : Math.max(details.loadedPages || 0, page.loadedPages);
      more.dataset.cursor = confirmationVersion === (details.confirmationVersion || 0) ? page.next_cursor || "" : "";
    } catch (error) { message(errorText(error)); }
    finally { more.disabled = false; }
  }
  function renderInboxGroup(group, kind, hiddenFace) {
    const details = node("details", undefined, "pa-inbox-group");
    const summary = node("summary", undefined, "pa-person-card");
    const faceSide = node("div", undefined, "pa-inbox-compare-side pa-inbox-compare-face");
    faceSide.append(picture(group.face.thumbnail_url, "待整理人脸", "pa-avatar"), node("span", "待整理", "hint"));
    const keyMatch = /^candidate:([1-9]\d*)$/.exec(String(group.group_key || ""));
    const candidateId = keyMatch ? keyMatch[1] : null;
    const candidate = candidateId && group.face && Array.isArray(group.face.suggestions)
      ? group.face.suggestions.find((item) => String(item.person_id) === candidateId)
      : null;
    const referenceSide = node("div", undefined, "pa-inbox-compare-side pa-inbox-compare-reference");
    const reference = picture(candidate && candidate.thumbnail_url, "参考头像：" + (candidate ? name(candidate) : group.candidate_name || "候选人物"), "pa-avatar pa-reference-avatar");
    if (!candidate || !candidate.thumbnail_url) reference.children[0].textContent = "暂无参考头像";
    referenceSide.append(reference, node("span", "参考头像", "hint"));
    const comparison = node("div", undefined, "pa-inbox-face-comparison pa-inbox-group-comparison");
    comparison.append(faceSide, referenceSide);
    summary.append(comparison,
      node("strong", "可能是" + (group.candidate_name || "未命名的人")),
      node("span", group.photo_count + (hiddenFace ? " 张已忽略照片" : " 张待确认照片"), "hint"));
    const list = node("div", undefined, "pa-people-grid pa-inbox-group-faces");
    const more = button(hiddenFace ? "再看一些已忽略人脸" : "再看一些", () =>
      loadInboxGroup(details, group.group_key, kind, more.dataset.cursor || null, hiddenFace));
    more.className += " pa-inbox-group-more"; more.hidden = true;
    let loaded = false; details.groupKey = group.group_key;
    details.openGroup = () => {
      if (!loaded) { loaded = true; return loadInboxGroup(details, group.group_key, kind, null, hiddenFace); }
    };
    details.addEventListener("toggle", () => {
      if (details.open) details.openGroup();
      else {
        const anchorKey = "faces:" + (hiddenFace ? "hidden:list" : "inbox:list");
        const anchorFaceId = state.selectAnchors.get(anchorKey);
        const containsAnchor = (item) => String(item.faceId || "") === String(anchorFaceId || "") ||
          (item.children || []).some(containsAnchor);
        if (anchorFaceId && containsAnchor(details)) state.selectAnchors.delete(anchorKey);
        if (details.confirmationDirty) flushInboxConfirmations();
      }
    });
    details.append(summary, list, more);
    return details;
  }
  async function loadInboxSummary() {
    if (!state.organize) return;
    const seq = ++state.inboxSummarySeq;
    $("inbox-note").textContent = "统计中……";
    $("inbox-summary-retry").hidden = true;
    try {
      const summary = await api("/faces/inbox/summary" + params({ quality_view: state.qualityView }));
      if (seq === state.inboxSummarySeq && state.organize) $("inbox-note").textContent = inboxSummaryText(summary);
    } catch (error) {
      if (seq === state.inboxSummarySeq && state.organize) {
        $("inbox-note").textContent = "统计暂不可用。";
        $("inbox-summary-retry").hidden = false;
      }
    }
  }
  async function loadInbox(more, reopenGroups) {
    if (more && (state.busy.has("inbox") || state.inboxConfirmationsPending)) return;
    const seq = ++state.inboxSeq; state.busy.add("inbox"); $("inbox-more").disabled = true;
    const values = { limit: 30, kind: $("inbox-kind").value || "all", quality_view: state.qualityView };
    const grouped = values.kind === "all" || values.kind === "suggested";
    const binding = JSON.stringify(values), sameView = binding === state.inboxBinding;
    const append = Boolean(more && state.inboxCursor && sameView);
    if (!append) state.selectAnchors.forEach((_, key) => { if (key.startsWith("faces:inbox:")) state.selectAnchors.delete(key); });
    const pageCount = append ? 1 : sameView ? Math.max(1, state.inboxPages) : 1;
    const position = sameView ? captureLibraryPosition() : null;
    const oldGroups = new Map(Array.from($("inbox-cards").children).filter((item) => item.groupKey).map((item) => [item.groupKey, item]));
    const expanded = sameView ? reopenGroups || Array.from(oldGroups.values()).filter((item) => item.open).map((item) => item.groupKey) : [];
    if (more && state.inboxCursor) values.cursor = state.inboxCursor;
    if (!sameView) { delete values.cursor; $("inbox-cards").replaceChildren(); state.inboxCursor = null; }
    if (!append) loadInboxSummary();
    $("inbox-load-note").textContent = "正在加载待整理人脸……";
    $("inbox-retry").hidden = true;
    try {
      const page = await readLoadedPages(grouped ? "/faces/inbox/groups" : "/faces/inbox", values, grouped ? "cards" : "faces",
        pageCount, () => seq === state.inboxSeq);
      if (!page || seq !== state.inboxSeq) return;
      $("inbox-load-note").textContent = "";
      if (!append) { state.inboxConfirmationsPending = false; $("inbox-cards").replaceChildren(); }
      state.inboxBinding = binding; state.inboxPages = append ? state.inboxPages + page.loadedPages : pageCount;
      if (grouped) page.cards.forEach((card) => $("inbox-cards").append(
        card.type === "candidate_group" ? renderInboxGroup(card, values.kind) : (() => {
          const wrapper = node("div", undefined, "pa-inbox-single"); wrapper.faceId = String(card.face.face_id); renderInboxFace(wrapper, card.face, false); return wrapper;
        })()));
      else page.faces.forEach((face) => renderInboxFace($("inbox-cards"), face, false));
      const groupLoads = [];
      if (!append && expanded.length) Array.from($("inbox-cards").children).forEach((item) => {
        if (item.groupKey && expanded.includes(item.groupKey)) {
          item.loadedPages = oldGroups.get(item.groupKey)?.loadedPages || 0;
          item.open = true; groupLoads.push(item.openGroup());
        }
      });
      state.inboxCursor = page.next_cursor; $("inbox-more").hidden = !page.has_more;
      if (!$("inbox-cards").children.length) $("inbox-cards").append(node("p", "暂时没有待归组的人脸。", "hint"));
      await Promise.all(groupLoads);
      if (seq === state.inboxSeq) restoreLibraryPosition(position);
    } catch (error) { if (seq === state.inboxSeq) { $("inbox-load-note").textContent = "待整理人脸加载失败：" + errorText(error); $("inbox-retry").hidden = false; } }
    finally { if (seq === state.inboxSeq) { state.busy.delete("inbox"); $("inbox-more").disabled = false; } }
  }
  async function loadHiddenFaces(more, reopenGroups) {
    if (more && state.busy.has("hidden-faces")) return;
    const seq = (state.hiddenFaceSeq || 0) + 1; state.hiddenFaceSeq = seq;
    state.busy.add("hidden-faces"); $("inbox-hidden-more").disabled = true;
    const values = { limit: 30 };
    if (more && state.inboxHiddenCursor) values.cursor = state.inboxHiddenCursor;
    if (!more) { state.selectAnchors.forEach((_, key) => { if (key.startsWith("faces:hidden:")) state.selectAnchors.delete(key); }); $("inbox-hidden-cards").replaceChildren(); state.inboxHiddenCursor = null; }
    try {
      const page = await api("/faces/inbox/hidden/groups" + params(values));
      if (seq !== state.hiddenFaceSeq) return;
      page.cards.forEach((card) => $("inbox-hidden-cards").append(
        card.type === "candidate_group" ? renderInboxGroup(card, "all", true) : (() => {
          const wrapper = node("div", undefined, "pa-inbox-single");
          renderInboxFace(wrapper, card.face, true); return wrapper;
        })()));
      if (reopenGroups && reopenGroups.length) Array.from($("inbox-hidden-cards").children).forEach((item) => {
        if (item.groupKey && reopenGroups.includes(item.groupKey)) { item.open = true; item.openGroup(); }
      });
      state.inboxHiddenCursor = page.next_cursor; $("inbox-hidden-more").hidden = !page.has_more;
      if (!$("inbox-hidden-cards").children.length) $("inbox-hidden-cards").append(node("p", "暂时没有已忽略的人脸。", "hint"));
    } catch (error) { if (seq === state.hiddenFaceSeq) message(errorText(error)); }
    finally { if (seq === state.hiddenFaceSeq) { state.busy.delete("hidden-faces"); $("inbox-hidden-more").disabled = false; } }
  }
  async function rematchFaces(restart) {
    if (state.writing || state.busy.has("rematch")) return;
    state.writing = true; state.busy.add("rematch"); $("rematch").disabled = true; $("rematch-restart").disabled = true;
    message("正在利用已认出的人补匹配，请稍候……");
    try {
      const afterAssetId = restart ? (state.rematchRestartAfter || 0) : state.rematchAfter;
      const result = await api("/faces/rematch", { limit: 20, after_asset_id: afterAssetId });
      if (restart) {
        state.rematchRestartAfter = result.has_more ? (result.next_after_asset_id || 0) : null;
        $("rematch-restart").textContent = state.rematchRestartAfter === null ? "从头重新检查旧照片" : "继续从头检查旧照片";
      } else {
        state.rematchAfter = result.next_after_asset_id || 0;
        $("rematch").textContent = result.has_more ? "继续补匹配剩余人脸" : "补匹配未归类人脸";
      }
      await Promise.all([loadPeople(false), loadInbox(false), refreshCollectionsIfUsed()]);
      message("这批处理了 " + result.processed + " 张照片，归入已有人物 " + result.merged
        + " 张脸，新增未命名人物 " + result.new_groups + " 个。"
        + (result.has_more ? "可继续处理剩余人脸。" : "仍未归类的脸可按原因整理。")
        + (result.deferred ? "有 " + result.deferred + " 张照片还在等待分析结果。" : ""));
    } catch (error) { message("补匹配没有完成：" + errorText(error)); }
    finally { state.writing = false; state.busy.delete("rematch"); $("rematch").disabled = false; $("rematch-restart").disabled = false; }
  }
  function detailBatchNote(batch) {
    if (!batch || batch.state === "IDLE") return "一键处理当前全部图库中机器待归类、有人物建议或原图像素更充分的小脸照片；合照按照片去重。没有检测到脸的照片不在此批次内。";
    const labels = { QUEUED: "排队中", RUNNING: "正在处理", PAUSED: "已暂停", COMPLETE: "已完成", PARTIAL_FAILED: "已完成（有失败可重试）" };
    return (labels[batch.state] || batch.state) + " · 照片 " + batch.processed + "/" + batch.total
      + " · 成功 " + batch.succeeded + " · 跳过 " + batch.skipped + " · 失败 " + batch.failed
      + " · 待确认人脸 " + batch.needs_review;
  }
  function updateDetailBatchControls(batch) {
    const previous = state.detailBatch;
    state.detailBatch = batch && batch.batch_id ? batch : null;
    $("detail-batch-note").textContent = detailBatchNote(batch);
    const stateName = batch && batch.state;
    const active = stateName === "QUEUED" || stateName === "RUNNING";
    const paused = stateName === "PAUSED";
    $("detail-batch").disabled = active || paused;
    if (previous && ["QUEUED", "RUNNING"].includes(previous.state)
        && ["COMPLETE", "PARTIAL_FAILED"].includes(stateName)) {
      loadPeople(false); if (state.organize) loadInbox(false); refreshCollectionsIfUsed();
    }
    $("detail-pause").hidden = !active;
    $("detail-resume").hidden = stateName !== "PAUSED";
    $("detail-retry").hidden = !(stateName === "PARTIAL_FAILED" && Number(batch.failed) > 0);
    if (active) scheduleDetailBatchPoll();
    else if (state.detailBatchTimer) { clearTimeout(state.detailBatchTimer); state.detailBatchTimer = null; }
  }
  function scheduleDetailBatchPoll() {
    if (state.detailBatchTimer) return;
    state.detailBatchTimer = setTimeout(async () => {
      state.detailBatchTimer = null;
      const seq = state.detailBatchSeq;
      try {
        const result = await api(detailBatchURL());
        if (seq === state.detailBatchSeq) updateDetailBatchControls(result);
      } catch (error) {
        if (seq === state.detailBatchSeq) {
          $("detail-batch-note").textContent = "队列状态暂时无法读取；任务仍保存在本机，正在重试。";
          scheduleDetailBatchPoll();
        }
      }
    }, 1800);
  }
  async function loadDetailBatch(loadPreview) {
    if (loadPreview === undefined) loadPreview = true;
    const seq = ++state.detailBatchSeq;
    try {
      const batch = await api(detailBatchURL());
      if (seq === state.detailBatchSeq) {
        updateDetailBatchControls(batch);
        if (loadPreview && (!batch.batch_id || ["COMPLETE", "PARTIAL_FAILED"].includes(batch.state))) {
          const count = await api("/faces/source-detail-preview" + detailScopeQuery());
          if (seq === state.detailBatchSeq) $("detail-batch").textContent = "一键原图补识别（" + count.total + " 张）";
        }
      }
    } catch (error) { if (seq === state.detailBatchSeq) $("detail-batch-note").textContent = "队列状态暂时无法读取。"; }
  }
  async function startDetailBatch() {
    await loadDetailControls();
    const seq = ++state.detailBatchSeq;
    $("detail-batch").disabled = true;
    try {
      const source = $("detail-source").value;
      const result = await api("/faces/source-detail-batches", {source_id: source ? Number(source) : null});
      if (seq === state.detailBatchSeq) updateDetailBatchControls(result);
      if (state.detailBatch) scheduleDetailBatchPoll();
    } catch (error) { $("detail-batch").disabled = false; message("原图补识别没有启动：" + errorText(error)); }
  }
  async function changeDetailBatch(action) {
    if (!state.detailBatch || !state.detailBatch.batch_id) return;
    const id = encodeURIComponent(state.detailBatch.batch_id);
    const seq = ++state.detailBatchSeq;
    try {
      const result = await api("/faces/source-detail-batches/" + id + "/" + action, {});
      if (seq === state.detailBatchSeq) updateDetailBatchControls(result);
    } catch (error) { message("队列操作没有完成：" + errorText(error)); }
  }
  function detailScopeQuery() {
    const source = $("detail-source").value;
    return source ? "?source_id=" + encodeURIComponent(source) : "";
  }
  function detailBatchURL() { return "/faces/source-detail-batches/active" + detailScopeQuery(); }
  async function loadDetailSources() {
    try {
      const response = await api("/sources");
      for (const source of response.sources || []) {
        const option = node("option", source.display_name || "图库 " + source.source_id);
        option.value = String(source.source_id); $("detail-source").append(option);
      }
      return true;
    } catch (error) { /* The all-library scope remains available. */ return false; }
  }
  async function loadDetailControls() {
    if (!state.organize) return;
    if (state.detailControlsPromise) return state.detailControlsPromise;
    state.detailControlsPromise = (async () => {
      if (!state.detailControlsLoaded) state.detailControlsLoaded = await loadDetailSources();
      if (state.organize) await loadDetailBatch();
    })().finally(() => { state.detailControlsPromise = null; });
    return state.detailControlsPromise;
  }
  function setQualityView(view) {
    if (!state.organize || !["clean", "all"].includes(view)) return Promise.resolve();
    if (state.qualityView === view) return Promise.resolve();
    const cleared = state.selectedPeople.size + state.selectedInboxFaces.size;
    state.qualityView = view;
    $("quality-toggle").checked = view === "all";
    state.selectedPeople.clear(); state.selectedInboxFaces.clear();
    state.inboxSeq++; state.inboxFilterSeq++; state.listSeq++; state.listCursor = null; state.inboxCursor = null;
    renderPeopleManagement(); renderFaceManagement();
    if (cleared) message("切换整理范围，已清除 " + cleared + " 项选择。", true);
    return Promise.all([loadPeople(false), loadInbox(false)]);
  }
  function setIgnoreState(ignoreState) {
    if (!state.hiddenView || !["TEMPORARY", "EXCLUDED"].includes(ignoreState) || state.ignoreState === ignoreState) return;
    clearPeopleSelection(); state.ignoreState = ignoreState; state.listSeq++; state.listCursor = null; loadPeople(false);
  }
  function setView(organize) {
    if (state.organize === organize && !state.hiddenView) return;
    clearPeopleSelection(); state.hiddenView = false;
    state.organize = organize;
    if (organize) {
      state.qualityView = "clean"; $("quality-toggle").checked = false;
      $("inbox-kind").value = "all";
    }
    state.inboxSeq++; state.inboxFilterSeq++; state.inboxSummarySeq++; state.listSeq++;
    if (!organize) {
      state.detailBatchSeq++;
      if (state.detailBatchTimer) { clearTimeout(state.detailBatchTimer); state.detailBatchTimer = null; }
    }
    $("people").setAttribute("aria-pressed", String(!organize));
    $("organize").setAttribute("aria-pressed", String(organize)); $("inbox").hidden = !organize;
    $("query").value = ""; loadPeople(false); if (organize) loadInbox(false);
    if (organize) loadDetailBatch(false);
    if (organize && window.AIPersonTools) window.AIPersonTools.summary();
  }
  function albumHeading() {
    $("collection-view-controls").hidden = true; $("search-photos").hidden = false;
    $("person-tools-menu").hidden = false;
    $("album-kind").textContent = "人物相册";
    document.getElementById("pt-organize").hidden = false;
    $("name").hidden = false; $("merge").hidden = false; $("cover").hidden = false;
    $("album-avatar").replaceChildren(picture(state.person.thumbnail_url, "当前相册的人", "pa-avatar"));
    $("title").textContent = name(state.person); $("name").textContent = state.person.name ? "改名字" : "取名字";
    $("cover").textContent = state.coverMode ? "取消选择封面" : "更换封面";
    refreshSearchScopeAction();
    $("album-note").textContent = state.person.asset_count + " 张照片 · 合照可出现在不同人的相册，下面的小头像标出这次看的是谁";
  }
  function openAlbum(person) {
    if (state.writing || state.pendingWrite || state.collectionActionBusy || state.collectionPeopleBusy) return;
    if (!$("library").hidden) state.libraryPosition = captureLibraryPosition();
    if (typeof window !== "undefined" && window.AIPersonTools) window.AIPersonTools.hideFaces();
    state.collection = null; state.collectionSeq++; state.person = person; state.coverMode = false; $("library").hidden = true; $("album").hidden = false;
    $("collection-people").hidden = true; $("photos").hidden = false; $("photos-more").hidden = false;
    $("collection-people-actions").hidden = true; $("collection-people-more").hidden = true;
    $("photo-query").value = ""; state.searching = false; albumHeading(); loadPhotos(false); syncDirectoryPagers();
    $("back").focus();
    if (window.AIPersonTools && window.location.hash === "#panel-faces") window.AIPersonTools.openFaces(person);
  }
  function renderPhotos(items) {
    items.forEach((item) => {
      const id = photoId(item); if (state.photoIds.includes(id)) return;
      state.photoIds.push(id);
      const card = button("", event => preview(id, event.currentTarget), "pa-photo-card");
      card.setAttribute("aria-label", "查看照片 " + id);
      card.append(picture(photoURL(id, "thumbnail"), "照片 " + id, "pa-photo", state.photoIds.length < 3));
      if (item.person_faces && item.person_faces.length) {
        const faces = node("span", undefined, "pa-photo-person");
        if (state.collection) faces.append(node("span", "这张照片包含合集成员"));
        item.person_faces.forEach((face) => faces.append(picture(face.thumbnail_url, state.collection ? "合集成员的人脸" : "本相册人物的人脸", "pa-choice-avatar")));
        if (item.person_faces_has_more) faces.append(node("span", "更多人脸请点开查看"));
        card.append(faces);
      }
      const time = item.taken_time;
      if (time && time.taken_at) card.append(node("span", time.taken_at.slice(0, 10) + (time.label === "capture" ? "" : " · 文件时间"), "pa-photo-date"));
      $("photos").append(card);
    });
  }
  function timelineScope() {
    return state.collection && state.collectionMode === "photos"
      ? { kind: "collection", collection_id: Number(state.collection.collection_id) }
      : { kind: "person", person_id: Number(state.person.person_id) };
  }
  function timelineKey(scope) {
    return scope.kind === "collection"
      ? [scope.kind, scope.collection_id, state.collection.instance_token, state.collection.revision].join(":")
      : [scope.kind, scope.person_id].join(":");
  }
  function rememberTimelineAsset(id) {
    id = Number(id);
    if (!Number.isSafeInteger(id) || id < 1) return;
    if (state.photoIds.includes(id)) return;
    state.photoIds.push(id);
    if (state.photoIds.length > 400) state.photoIds.splice(0, state.photoIds.length - 400);
  }
  function showTimeline(more, preferences) {
    const scope = timelineScope();
    $("timeline").hidden = false; $("photos").hidden = true; $("photos-more").hidden = true;
    syncDirectoryPagers();
    const key = timelineKey(scope);
    if (more) return state.timeline ? state.timeline.append() : Promise.resolve(null);
    if (!state.timeline) {
      state.timeline = window.PhotoTimeline.mount({ host: $("timeline"), scope,
        timeBasis: preferences && preferences.timeBasis, groupBy: preferences && preferences.groupBy,
        adapter: {
        fetchPage(values, signal) {
          const query = new URLSearchParams(values);
          return fetch("/api/v1/timeline/assets?" + query.toString(), { credentials: "same-origin", signal,
            headers: { Accept: "application/json" } }).then(async response => {
            const body = await response.json();
            if (!response.ok) {
              if (values.kind === "collection" && response.status === 409 && body.code === "INVALID_CURSOR") {
                $("timeline").dataset.collectionExpired = "true";
                $("timeline-refresh").hidden = false;
                $("photo-note").textContent = "合集范围或成员版本已变化，旧日期游标已失效。重新加载会读取当前合集，并清除年份、月份和日期分组筛选。";
              } else if (values.kind === "collection" && response.status === 404 && body.code === "PERSON_COLLECTION_NOT_FOUND") {
                $("timeline").dataset.collectionGone = "true";
                $("photo-note").textContent = "这个合集已不存在或已被重建。请返回合集列表后重新打开。";
              }
              const error = new Error(body.message || "时间轴请求失败"); error.code = body.code; error.status = response.status; throw error;
            }
            if (values.kind === "collection") {
              const responseScope = body.scope || {};
              if (Number(responseScope.collection_id) !== Number(state.collection.collection_id)
                  || responseScope.collection_instance !== state.collection.instance_token
                  || Number(responseScope.collection_revision) !== Number(state.collection.revision)) {
                $("timeline").dataset.collectionExpired = "true";
                $("timeline-refresh").hidden = false;
                $("photo-note").textContent = "合集范围或成员版本已变化，当前结果未显示。重新加载会读取当前合集，并清除年份、月份和日期分组筛选。";
                const error = new Error("合集范围已变化，请重新加载当前合集。"); error.code = "COLLECTION_SCOPE_CHANGED"; throw error;
              }
              delete $("timeline").dataset.collectionExpired;
              delete $("timeline").dataset.collectionGone;
              $("timeline-refresh").hidden = true;
            }
            return body;
          });
        },
        renderAsset(item) {
          const id = Number(item.asset_id);
          const card = button("", event => preview(id, event.currentTarget), "pa-photo-card");
          card.setAttribute("aria-label", "查看照片 " + id);
          card.append(picture(photoURL(id, "thumbnail"), "照片 " + id, "pa-photo"));
          const sourceLabel = item.time_basis === "library" ? "加入图库时间"
            : item.time_label === "capture" ? "可信拍摄时间"
              : item.time_label === "file_time" ? "文件时间" : item.time_label;
          const dateLabel = item.display_time || (item.date_state === "unknown"
            ? "日期未知 · 按加入图库日期" : item.date_state === "pending"
              ? "日期待就绪 · 按加入图库日期" : item.time_label || "查看照片");
          card.append(node("span", dateLabel, "pa-photo-date"));
          if (item.display_time && sourceLabel) card.append(node("small", sourceLabel, "pa-photo-source"));
          return card;
        },
        onOpen(item, card) { preview(Number(item.asset_id), card); },
        onPage(body, page, values) {
          if (!values.cursor) {
            state.photoIds = [];
            (body.assets || []).forEach(item => rememberTimelineAsset(item.asset_id));
            state.photoListSeq++;
          } else (body.assets || []).forEach(item => rememberTimelineAsset(item.asset_id));
          state.photoCursor = body.next_cursor || null;
          updatePreviewNavigation();
          $("photo-note").textContent = state.collection
            ? "按日期浏览合集照片；同一张照片只显示一次。"
            : "按日期浏览这个人物的照片；合照中的其他人物保持各自归属。";
        },
        onRestoreAsset(card) { if (card) card.scrollIntoView({ block: "center" }); },
      } });
    } else if (key !== state.timelineScopeKey) {
      state.photoIds = []; state.photoListSeq++; state.timeline.setScope(scope);
      delete $("timeline").dataset.collectionExpired;
      delete $("timeline").dataset.collectionGone;
      $("timeline-refresh").hidden = true;
    } else {
      state.timeline.reset();
    }
    state.timelineScopeKey = key;
    return Promise.resolve(true);
  }
  async function loadPhotos(more) {
    if ((!state.person && !(state.collection && state.collectionMode === "photos")) || (more && state.busy.has("photos"))) return false;
    if (!state.searching && !state.coverMode && window.PhotoTimeline) return showTimeline(more);
    const seq = ++state.photoSeq; state.busy.add("photos"); $("photos-more").disabled = true;
    $("timeline").hidden = true; $("photos").hidden = false;
    const personId = state.person && state.person.person_id;
    const collectionId = state.collection && state.collectionMode === "photos" ? state.collection.collection_id : null;
    if (!more) {
      $("photos").replaceChildren(); state.photoCursor = null; state.photoIds = []; state.photoListSeq++;
      state.photoHasMore = false; if (state.photoPager) state.photoPager.reset();
      updatePreviewNavigation();
    }
    $("photo-note").textContent = "正在加载照片……";
    try {
      let page;
      if (state.searching) {
        const body = { ...state.searchBody };
        if (more && state.photoCursor) { body.cursor = state.photoCursor; body.search_session_id = state.searchSession; }
        page = await api("/search", body);
      } else {
        const values = { limit: 60 }; if (more && state.photoCursor) values.cursor = state.photoCursor;
        page = collectionId
          ? await api(collectionPath(state.collection, "/photos") + params(values))
          : await api("/persons/" + personId + "/photos" + params(values));
      }
      if (seq !== state.photoSeq || (collectionId ? !state.collection || collectionId !== state.collection.collection_id : !state.person || personId !== state.person.person_id)) return false;
      if (state.searching) state.searchSession = page.search_session_id;
      if (page.person) { state.person = page.person; albumHeading(); }
      if (page.collection) { state.collection = { ...state.collection, ...page.collection }; collectionHeading(); }
      renderPhotos(page.assets || page.hits || []); state.photoCursor = page.next_cursor || null;
      state.photoHasMore = Boolean(page.has_more && page.next_cursor);
      $("photos-more").hidden = !state.photoHasMore; syncDirectoryPagers();
      $("photo-note").textContent = $("photos").children.length ? (collectionId ? "点照片查看大图；合集只把人物放在一起浏览。" : state.coverMode ? "点一张喜欢的照片，再选这个人的脸设为封面。" : state.searching ? "搜索结果仅包含这个人的照片。点“全部照片”返回相册。" + (page.candidates_truncated ? " 搜索结果较多，请缩小查询范围。" : "") : "点照片查看大图；认错了可选择对应的人脸更正。") : (state.searching ? emptySearchNote(page) : collectionId ? "这个合集还没有照片。" : "这个人物还没有照片。");
      return true;
    } catch (error) { if (seq === state.photoSeq) { $("photo-note").textContent = errorText(error); $("photos-more").hidden = !state.photoHasMore; } return false; }
    finally { if (seq === state.photoSeq) { state.busy.delete("photos"); $("photos-more").disabled = false; updatePreviewNavigation(); syncDirectoryPagers(); } }
  }
  function previewPosition() {
    if ((!state.person && !state.collection) || state.previewListSeq !== state.photoListSeq) return -1;
    return state.photoIds.indexOf(state.previewAssetId);
  }
  function previewNode(name) { return state.previewUI && state.previewUI[name]; }
  function createPreviewUI(assetId, returnFocus) {
    if (!window.AIPhotoInfo) {
      const dialog = $("preview"), image = $("preview-image");
      state.previewUI = { dialog, image, note: $("preview-note"), faces: $("preview-faces"),
        faceDetail: $("face-detail"), review: $("review"), nav: $("preview-nav"),
        position: $("preview-position"), previous: $("preview-prev"), next: $("preview-next") };
      state.previewHandle = { dialog, image, close: () => dialog.close(),
        setAsset(id) { image.hidden = false; image.src = mediaURL(photoURL(id, "preview")); },
        setPosition(value) { state.previewUI.position.textContent = value; state.previewUI.nav.hidden = !value; },
        setNavigationEnabled(previous, next) { state.previewUI.previous.disabled = !previous; state.previewUI.next.disabled = !next; } };
      image.src = mediaURL(photoURL(assetId, "preview"));
      if (typeof window.AIPhotoInfo === "undefined") state.previewInfoMount = null;
      dialog.showModal(); closeOnBackdrop(dialog);
      $("preview-close").addEventListener("click", () => dialog.close());
      state.previewUI.faceDetail.addEventListener("click", sourceDetail);
      state.previewUI.previous.addEventListener("click", () => movePreview(-1));
      state.previewUI.next.addEventListener("click", () => movePreview(1));
      dialog.addEventListener("close", () => { state.previewSeq++; state.previewAssetId = null; image.removeAttribute("src"); state.previewUI.nav.hidden = true; state.previewHandle = null; state.previewUI = null; updatePreviewNavigation(); });
      return;
    }
    const extension = node("section", undefined, "pa-preview-extension");
    const note = node("p", "正在读取这张照片的人脸……", "hint"); note.setAttribute("role", "status");
    const faces = node("div", undefined, "pa-face-row");
    const faceDetail = button("尝试原图细节识别", sourceDetail); faceDetail.hidden = true;
    const review = button("查看识别更新待确认", () => {
      const id = state.previewAssetId;
      if (state.previewReviewFailed) { loadPreviewReviews(id, state.previewSeq); return; }
      state.previewHandle.close(); if (window.AIPersonTools) window.AIPersonTools.openReview(id);
    }); review.hidden = true;
    extension.append(note, faces, faceDetail, review);
    state.previewUI = { note, faces, faceDetail, review };
    state.previewHandle = window.AIPhotoInfo.open(assetId, {
      title: "人物照片", alt: "人物相册照片", extension: extension, returnFocus: returnFocus, people: false,
      beforeClose: () => !state.writing && !state.pendingWrite,
      navigation: { previous: () => movePreview(-1), next: () => movePreview(1) },
      onClose: () => {
        state.previewSeq++; state.previewAssetId = null; state.previewHandle = null; state.previewUI = null;
        updatePreviewNavigation();
      }
    });
    state.previewUI.dialog = state.previewHandle.dialog;
  }
  function updatePreviewNavigation() {
    if (!state.previewHandle) return;
    const index = previewPosition(); const enabled = index >= 0 && state.previewHandle.dialog.open;
    state.previewHandle.setPosition(enabled ? "第 " + (index + 1) + " 张" : "");
    state.previewHandle.setNavigationEnabled(enabled && index > 0 && !state.previewMoving && !state.writing,
      enabled && !state.previewMoving && !state.writing && (index < state.photoIds.length - 1 || Boolean(state.photoCursor)));
  }
  async function movePreview(step) {
    if (state.previewMoving || state.writing || !state.previewHandle || !state.previewHandle.dialog.open) return;
    const index = previewPosition(); if (index < 0 || index + step < 0) return;
    const listSeq = state.photoListSeq; const previewSeq = state.previewSeq;
    state.previewMoving = true; updatePreviewNavigation();
    try {
      if (index + step >= state.photoIds.length) {
        if (!state.photoCursor) return;
        const loaded = await loadPhotos(true);
        if (previewSeq !== state.previewSeq || listSeq !== state.photoListSeq || !state.previewHandle || !state.previewHandle.dialog.open) return;
        if (!loaded) { previewNode("note").textContent = "下一批照片没有加载成功，请再点“下一张”重试。"; return; }
      }
      if (listSeq !== state.photoListSeq || !state.previewHandle || !state.previewHandle.dialog.open) return;
      const next = state.photoIds[index + step];
      if (next !== undefined) await preview(next);
    } finally { state.previewMoving = false; updatePreviewNavigation(); }
  }
  async function preview(id, returnFocus) {
    const seq = ++state.previewSeq;
    state.previewAssetId = id; state.previewListSeq = state.photoListSeq;
    if (!state.previewHandle) createPreviewUI(id, returnFocus);
    else state.previewHandle.setAsset(id);
    previewNode("faces").replaceChildren();
    previewNode("review").hidden = true;
    previewNode("note").textContent = "正在读取这张照片的人脸……";
    updatePreviewNavigation();
    loadPreviewReviews(id, seq);
    try {
      const page = await api("/assets/" + id + "/faces"); if (seq !== state.previewSeq || !state.previewHandle || !state.previewHandle.dialog.open) return;
      if (state.collection) {
        previewNode("faces").replaceChildren(); previewNode("faceDetail").hidden = true; previewNode("review").hidden = true;
        previewNode("note").textContent = "人物合集只用于集中浏览照片。";
        return;
      }
      const selectedId = state.person && state.person.person_id;
      page.faces.sort((a, b) => Number(b.assigned_person_id === selectedId) - Number(a.assigned_person_id === selectedId));
      page.faces.forEach((face) => {
        if (state.coverMode && face.assigned_person_id !== selectedId) return;
        const card = node("div", undefined, "pa-person-card");
        if (face.assigned_person_id === selectedId) {
          card.classList.add("pa-selected-person"); card.append(node("strong", "本相册的人"));
        }
        card.append(picture(face.thumbnail_url, "选择这张脸", "pa-avatar"), node("span", face.assigned_person_name ? "当前：" + face.assigned_person_name : face.assigned_person_id ? "当前：未命名的人" : "还没确定是谁"));
        if (face.source_detail) card.append(node("span", "原图细节结果 · "
          + (face.assigned_person_id ? "已归入人物" : face.automatic_detail ? "待可靠匹配" : "待人工确认"), "hint"));
        if (state.person && face.assigned_person_id === selectedId) {
          const cover = button("设为封面", () => saveCover(face.face_id, cover));
          card.append(cover);
        }
        if (!state.coverMode) card.append(button("更正这张脸", () => { state.previewHandle.close(); editFace(face); }));
        previewNode("faces").append(card);
      });
      const hasDetail = page.faces.some((face) => face.source_detail);
      previewNode("faceDetail").hidden = state.coverMode || hasDetail;
      previewNode("faceDetail").disabled = state.detailBusyAssets.has(Number(id));
      previewNode("note").textContent = state.coverMode ? (previewNode("faces").children.length ? "选择喜欢的人脸，设为这个人物的封面。" : "这里没有当前人物的可用人脸，请换一张照片。") : page.faces.some(face => face.automatic_detail) ? "已按原图完成识别；未可靠归类的人脸可继续更正。" : hasDetail ? "原图细节结果只供人工检查；请用“更正这张脸”手动指定人物，不会自动归组。" : page.faces.length ? "可为本相册的人设置封面；认错了也可更正对应人脸。" + (page.has_more ? "此处仅展示前 200 张脸。" : "") : "没有可整理的人脸；尚未分析、没有检测到或识别更新待确认时会出现这种情况。";

    } catch (error) { if (seq === state.previewSeq) previewNode("note").textContent = errorText(error); }
  }
  async function loadPreviewReviews(id, seq) {
    const reviewSeq = (state.previewReviewSeq || 0) + 1; state.previewReviewSeq = reviewSeq;
    state.previewReviewFailed = false;
    try {
      const page = await api("/assets/" + id + "/face-review?limit=1");
      if (reviewSeq !== state.previewReviewSeq || seq !== state.previewSeq || id !== state.previewAssetId || !state.previewHandle || !state.previewHandle.dialog.open || state.collection) return;
      previewNode("review").hidden = !page.groups.length; previewNode("review").textContent = "查看这张照片的识别更新待确认";
    } catch (error) {
      if (reviewSeq !== state.previewReviewSeq || seq !== state.previewSeq || id !== state.previewAssetId || !state.previewHandle || !state.previewHandle.dialog.open || state.collection) return;
      state.previewReviewFailed = true; previewNode("review").hidden = false; previewNode("review").textContent = "重试读取识别更新提示";
    }
  }
  async function sourceDetail() {
    const assetId = state.previewAssetId;
    const previewSeq = state.previewSeq;
    if (!assetId || state.detailBusyAssets.has(Number(assetId)) || !state.previewHandle || !state.previewHandle.dialog.open) return;
    state.detailBusyAssets.add(Number(assetId));
    const control = previewNode("faceDetail"); control.disabled = true;
    control.textContent = "已排队，等待原图处理……";
    previewNode("note").textContent = "正在排队处理这张照片。原图细节结果会单独显示，需你手动指定人物。";
    const stillCurrent = () => previewSeq === state.previewSeq && assetId === state.previewAssetId && state.previewHandle && state.previewHandle.dialog.open;
    try {
      const queued = await api("/assets/" + assetId + "/face-detail", {}, "POST");
      if (queued.already_available) {
        control.hidden = true;
        previewNode("note").textContent = "这张照片已有原图细节识别结果；结果仅用于人工检查与指定人物。";
        if (queued.job_id && stillCurrent()) await preview(assetId);
        return;
      }
      const deadline = Date.now() + 180000;
      while (stillCurrent() && Date.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, 500));
        if (!stillCurrent()) return;
        const job = await api("/jobs/" + encodeURIComponent(queued.job_id));
        if (job.state === "SUCCEEDED") {
          control.hidden = true;
          previewNode("note").textContent = "原图细节识别完成。新结果需你手动指定人物，不会自动归组。";
          await preview(assetId);
          return;
        }
        if (["FAILED", "BLOCKED", "CANCELLED"].includes(job.state)) {
          control.disabled = false; control.textContent = "重试原图细节识别";
          previewNode("note").textContent = "原图细节识别没有完成（" + job.state + "），可重试；已有结果和人物归属未改动。";
          return;
        }
        previewNode("note").textContent = job.state === "RUNNING"
          ? "正在用原图细节处理这张照片……请稍候。"
          : "这张照片已排队等待原图细节处理……";
      }
      if (stillCurrent()) {
        control.disabled = false; control.textContent = "查看原图细节任务";
        previewNode("note").textContent = "任务仍在队列中；关闭照片不会取消它，稍后可重新查看状态。";
      }
    } catch (error) {
      if (stillCurrent()) {
        control.disabled = false; control.textContent = "重试原图细节识别";
        previewNode("note").textContent = "原图细节识别没有排入队列：" + errorText(error);
      }
    } finally {
      state.detailBusyAssets.delete(Number(assetId));
      if (!control.hidden && state.previewAssetId === assetId) control.disabled = false;
    }
  }
  async function saveCover(faceId, control) {
    if (!state.person || state.writing) return;
    const person = state.person; const previewSeq = state.previewSeq;
    state.writing = true; control.disabled = true; updatePreviewNavigation();
    try {
      const result = await api("/persons/" + person.person_id + "/cover",
        { face_id: faceId, expected_revision: person.revision }, "PUT");
      loadPeople(false);
      if (state.person && state.person.person_id === person.person_id) {
        state.person = result.person; state.coverMode = false; albumHeading(); loadPhotos(false);
        if (previewSeq === state.previewSeq) {
          state.writing = false;
          control.disabled = false;
          updatePreviewNavigation();
          state.previewHandle.close();
        }
        message("已更换人物封面。", true);
      }
    } catch (error) { if (previewSeq === state.previewSeq) previewNode("note").textContent = errorText(error); }
    finally { state.writing = false; control.disabled = false; updatePreviewNavigation(); }
  }
  function editBase(edit, title, note) {
    if (state.pendingWrite) { message(errorText({code:"PENDING_WRITE"})); return false; }
    if (state.writing) return false;
    state.edit = edit; state.chosen = null; state.editSeq++; state.choiceSeq++;
    $("edit-title").textContent = title; $("edit-note").textContent = note;
    $("edit-error").textContent = ""; $("choices").replaceChildren(); $("choice-more").hidden = true;
    $("edit-query").value = edit.mode === "name" ? edit.person.name || "" : "";
    $("edit-query").disabled = false;
    $("edit-find").hidden = edit.mode === "name"; $("edit-label").textContent = edit.mode === "name" ? "名字" : "搜索并选择人物";
    $("edit-submit").textContent = edit.mode === "name" ? "保存名字" : (edit.mode === "merge" || edit.mode === "batchMerge") ? "确认并入所选人物" : "归到所选人物";
    $("edit-submit").disabled = edit.mode !== "name";
    $("create-person").hidden = !(edit.mode === "face" && edit.allowCreatePerson && !state.batchMode && !edit.face.assigned_person_id);
    $("create-person").disabled = false;
    $("create-person-note").hidden = $("create-person").hidden;
    $("edit").showModal(); $("edit-query").focus(); return true;
  }
  function editFace(face, options) {
    options = options || {};
    if (editBase({ mode: "face", face }, "这张脸是谁？", "只整理选中的这张脸。合照里的其他人保持原来的归属。")) {
      state.edit.allowCreatePerson = options.allowCreatePerson === true && !state.batchMode;
      $("create-person").hidden = !state.edit.allowCreatePerson || Boolean(face.assigned_person_id);
      $("create-person-note").hidden = $("create-person").hidden;
      const crop = picture(face.thumbnail_url, "正在更正的人脸", "pa-avatar"); $("choices").append(crop);
      if (face.assigned_person_id) $("choices").append(button("不是这个人，移出人物", () => writeFace("unassign")));
      loadChoices(false);
    }
  }
  function editMerge(person) {
    if (editBase({ mode: "merge", person }, "这是同一个人", "选择已有的人物，把这个分组的照片并进去。确认后可撤销。")) loadChoices(false);
  }
  function editBatchFaces(faces) {
    if (!faces.length || faces.length > 500) return;
    if (editBase({ mode: "batchFaces", faces: faces.map((face) => ({ ...face })) }, "更正所选人脸", "仅修改所选 " + faces.length + " 张脸，不影响合照里的其他人。")) {
      loadChoices(false);
    }
  }
  function editBatchMerge() {
    if (state.hiddenView || state.writing || !state.selectedPeople.size) return;
    const sources = Array.from(state.selectedPeople).map((id) => state.selectedPeopleSnapshots.get(id));
    if (sources.some((person) => !person)) { message("人物选择已失效，请取消后重新选择。"); return; }
    if (editBase({ mode: "batchMerge", sources }, "合并到已有的人物", "来源：" + sources.map(name).join("、") + "。选择目标后一次原子合并，可撤销。")) loadChoices(false);
  }
  async function loadChoices(more) {
    if (!state.edit || state.edit.mode === "name" || state.writing) return;
    const seq = ++state.choiceSeq; const editSeq = state.editSeq;
    const query = $("edit-query").value.trim(); const values = { kind: "all", q: query, sort: "photos_desc", limit: 20 };
    if (more && state.choiceCursor && query === state.choiceQuery) values.cursor = state.choiceCursor;
    else $("choices").querySelectorAll(".pa-choice").forEach((item) => item.remove());
    state.choiceQuery = query; state.chosen = null; $("edit-submit").disabled = true;
    $("choice-more").disabled = true;
    try {
      const page = await api("/persons/browse" + params(values)); if (seq !== state.choiceSeq || editSeq !== state.editSeq) return;
      page.persons.forEach((person) => {
        if (state.edit.person && person.person_id === state.edit.person.person_id) return;
        if (state.edit.sources && state.edit.sources.some((source) => source.person_id === person.person_id)) return;
        const card = button("", () => {
          state.chosen = person; $("choices").querySelectorAll(".pa-choice").forEach((item) => item.setAttribute("aria-pressed", "false"));
          card.setAttribute("aria-pressed", "true"); $("edit-submit").disabled = false;
          if (state.edit.mode === "batchMerge") $("edit-note").textContent = "将“" + state.edit.sources.map(name).join("、") + "”一次合并到“" + name(person) + "”。确认后可撤销。";
        }, "pa-choice"); card.setAttribute("aria-pressed", "false");
        card.append(picture(person.thumbnail_url, name(person), "pa-choice-avatar"), node("span", name(person) + " · " + person.asset_count + " 张")); $("choices").append(card);
      });
      state.choiceCursor = page.next_cursor; $("choice-more").hidden = !page.has_more;
      $("edit-error").textContent = page.persons.length ? "" : "没有找到人物，试试其他名字。";
    } catch (error) { if (seq === state.choiceSeq) $("edit-error").textContent = errorText(error); }
    finally { if (seq === state.choiceSeq) $("choice-more").disabled = false; }
  }
  function rememberOperation(operation, label) {
    state.operation = operation; $("undo-text").textContent = label;
    const undoable = operation.operation_type !== "UNDO" && operation.operation_type !== "CREATE_PERSON_FROM_FACE" && (operation.changed || /FACE_MAPPING/.test(operation.operation_type || ""));
    $("undo-bar").hidden = !undoable; $("undo").disabled = false;
    clearTimeout(state.undoTimer);
    if (undoable) state.undoTimer = setTimeout(() => {
      state.operation = null; $("undo-bar").hidden = true;
    }, 10000);
  }
  async function createPersonFromFace() {
    if (state.pendingWrite) { message(errorText({code:"PENDING_WRITE"})); return; }
    const face = state.edit && state.edit.mode === "face" ? state.edit.face : null;
    if (!face || state.batchMode || face.assigned_person_id || state.writing) return;
    let intent = state.createPersonIntent;
    if (!intent || intent.face_id !== face.face_id || intent.expected_control_revision !== Number(face.control_revision || 0)) {
      intent = { face_id: face.face_id, expected_control_revision: Number(face.control_revision || 0), request_id: requestId() };
      state.createPersonIntent = intent;
    }
    state.writing = true; $("create-person").disabled = true; $("edit-submit").disabled = true; $("edit-cancel").disabled = true;
    $("edit-error").textContent = "正在新建未命名人物……";
    try {
      await api("/faces/" + encodeURIComponent(intent.face_id) + "/create-person", {
        contract_version: "p4-person-create-from-face.v1",
        request_id: intent.request_id,
        expected_control_revision: intent.expected_control_revision,
      }, "POST");
      state.createPersonIntent = null;
      clearTimeout(state.undoTimer); state.undoTimer = null; state.operation = null; $("undo-bar").hidden = true;
      $("edit").close(); state.editSeq++; state.choiceSeq++;
      notifyPeopleChanged();
      message("已新建未命名人物（1 张脸）", true);
      loadPeople(false);
      if (state.organize) {
        const openGroups = Array.from($("inbox-cards").children)
          .filter((item) => item.open && item.groupKey).map((item) => item.groupKey);
        loadInbox(false, openGroups);
      }
    } catch (error) {
      // A transport failure leaves commit status unknown; retain this intent's request_id
      // so clicking retry cannot create a second person. HTTP responses are definitive.
      if (error.status && error.status < 500) state.createPersonIntent = null;
      $("edit-error").textContent = error.status === 409
        ? "整理状态有更新，请刷新后重新选择这张脸。"
        : errorText(error);
      $("create-person").hidden = false;
    } finally {
      state.writing = false; $("create-person").disabled = false; $("edit-cancel").disabled = false;
      if (state.edit && state.edit.mode === "face") $("edit-submit").disabled = !state.chosen;
    }
  }
  async function refreshAfterWrite(result, label) {
    if (result.operation_type === "FACE_REGROUP") {
      label = result.status === "UNDONE" ? "这次重新分组已被撤销" :
        "已归组 " + result.grouped_faces + " 张脸 · 新增 " + result.new_persons + " 个人物 · " + result.remaining_faces + " 张仍需整理";
    }
    rememberOperation(result, label); message(label, result.operation_type !== "FACE_REGROUP");
    if ($("edit").open) $("edit").close(); state.editSeq++; state.choiceSeq++;
    notifyPeopleChanged(); refreshPeopleViews();
    if (typeof window !== "undefined" && window.AIPersonTools) window.AIPersonTools.refreshVisible();
    if (state.person) {
      const id = state.person.person_id;
      try { const person = await api("/persons/" + id); if (!state.person || id !== state.person.person_id) return;
        state.person = person; albumHeading(); state.searching = false; $("photo-query").value = ""; loadPhotos(false); }
      catch (error) { message(label + "。" + errorText(error)); }
    }
  }
  async function mutate(path, body, method, label) {
    if (state.writing) throw { code: "WRITE_BUSY" };
    const payload = { ...body }; delete payload.request_id;
    const key = JSON.stringify([path, method || "POST", payload]);
    if (state.pendingWrite && state.pendingWrite.key !== key) throw { code: "PENDING_WRITE" };
    const intent = state.pendingWrite || { key, path, method, label, body: { ...payload, request_id: body.request_id || requestId() } };
    state.pendingWrite = intent; state.writing = true;
    renderPeopleManagement(); updateCollectionSelection();
    if (typeof window !== "undefined" && window.AIPersonTools) window.AIPersonTools.writeStateChanged();
    $("edit-submit").disabled = true; $("edit-cancel").disabled = true;
    try {
      const result = await api(intent.path, intent.body, intent.method);
      state.pendingWrite = null; $("write-retry").hidden = true;
      if (path === "/face-assignments/batch") { state.selectedInboxFaces.clear(); state.selectedHiddenFaces.clear(); }
      if (/\/persons\/merge$/.test(path)) { state.selectedPeople.clear(); state.selectedPeopleSnapshots.clear(); renderPeopleManagement(); }
      await refreshAfterWrite(result, label);
      return result;
    } catch (error) {
      if (error.status && error.status < 500) state.pendingWrite = null;
      $("write-retry").hidden = !state.pendingWrite;
      if (state.pendingWrite) message("保存结果尚未确认，请重试上次保存以读取实际结果。");
      throw error;
    } finally {
      state.writing = false; $("edit-submit").disabled = state.edit && state.edit.mode !== "name" && !state.chosen;
      $("edit-cancel").disabled = false;
      renderPeopleManagement();
      renderFaceManagement();
      if (typeof window !== "undefined" && window.AIPersonTools) window.AIPersonTools.writeStateChanged();
    }
  }
  async function write(path, body, method, label) {
    if (state.writing) return;
    $("edit-error").textContent = "正在保存……";
    try { await mutate(path, body, method, label); }
    catch (error) {
      if (error.details && error.details.reason === "NAME_IN_USE" && state.edit && state.edit.mode === "name") {
        const source = state.edit.person;
        try {
          const target = await api("/persons/" + error.details.target_person_id);
          state.edit = { mode: "merge", person: source }; state.chosen = target;
          $("edit-title").textContent = "已有这个名字的人";
          $("edit-note").textContent = "“" + name(target) + "”已经存在。确认后会把这两组照片并成一个人物。";
          $("edit-query").disabled = true; $("edit-submit").disabled = false;
          $("edit-submit").textContent = "确认并入“" + name(target) + "”"; $("edit-error").textContent = "";
        } catch (lookupError) { $("edit-error").textContent = errorText(lookupError); }
      } else $("edit-error").textContent = errorText(error) + (error.status === 409 ? " 关闭窗口，点刷新后重新选择。" : "");
    }
  }
  function writeFace(action) {
    const edit = state.edit; if (!edit || edit.mode !== "face" || (action === "assign" && !state.chosen)) return;
    const body = { contract_version: "p3-face-correction-contracts.v1", request_id: requestId(),
      action, expected_revision: edit.face.control_revision };
    if (action === "assign") body.target_person_id = state.chosen.person_id;
    write("/faces/" + edit.face.face_id + "/assignment", body, "PUT", action === "assign" ? "已归到“" + name(state.chosen) + "”" : "已移出人物，之后可在“整理一下”找到这张脸");
  }
  function submitEdit(event) {
    event.preventDefault(); const edit = state.edit; if (!edit || state.writing) return;
    if (edit.mode === "name") {
      const value = $("edit-query").value.trim(); if (!value) { $("edit-error").textContent = "请填写名字。"; return; }
      write("/persons/" + edit.person.person_id + "/label", { request_id: requestId(), name: value, expected_revision: edit.person.revision }, "PUT", "已保存名字“" + value + "”");
    } else if (!state.chosen) loadChoices(false);
    else if (edit.mode === "face") writeFace("assign");
    else if (edit.mode === "batchFaces") write("/face-assignments/batch", { contract_version: "p3-face-correction-contracts.v1",
      items: edit.faces.map((face) => ({ face_id: face.face_id, action: "assign", target_person_id: state.chosen.person_id, expected_revision: face.control_revision })) }, "POST", "已更正所选人脸归属");
    else if (edit.mode === "batchMerge") write("/persons/merge", { target_person_id: state.chosen.person_id,
      target_expected_revision: state.chosen.revision, sources: edit.sources.map((person) => ({ person_id: person.person_id, expected_revision: person.revision })) }, "POST", "已将所选人物合并到“" + name(state.chosen) + "”");
    else if (state.chosen) write("/persons/merge", { request_id: requestId(), target_person_id: state.chosen.person_id,
      target_expected_revision: state.chosen.revision, sources: [{ person_id: edit.person.person_id, expected_revision: edit.person.revision }] }, "POST", "已并入“" + name(state.chosen) + "”");
  }
  async function undo() {
    if (!state.operation || state.writing) return;
    const operation = state.operation; $("undo").disabled = true;
    try {
      await mutate("/person-operations/" + encodeURIComponent(operation.operation_id) + "/undo", { expected_operation_revision: operation.operation_revision }, "POST", "已撤销刚才的整理。");
      clearTimeout(state.undoTimer); state.operation = null; $("undo-bar").hidden = true;
    } catch (error) { message("无法撤销：" + errorText(error)); }
    finally { $("undo").disabled = false; }
  }
  function boot() {
    installDirectoryPagers();
    setSearchScopeAction = window.SearchScopeEntry ? window.SearchScopeEntry.configure({
      button: document.getElementById("global-search-scope"),
      input: document.getElementById("global-search-query"),
      getScope: function () {
        if ($("album").hidden) return null;
        if (state.collection) return { kind: "collection", collection_id: Number(state.collection.collection_id),
          instance_token: String(state.collection.instance_token), revision: Number(state.collection.revision),
          name: String(state.collection.name || "人物合集") };
        if (state.person) return { kind: "person", person_id: Number(state.person.person_id), name: String(name(state.person)) };
        return null;
      },
    }) : function () {};
    refreshSearchScopeAction();
    if (typeof window !== "undefined" && window.AIPersonTools) window.AIPersonTools.install({ api, node, button, picture, errorText, message, mutate, preview, editBatchFaces,
      isWriting: () => state.writing || Boolean(state.pendingWrite), isOrganizing: () => state.organize, currentPerson: () => state.person,
      organizeView: () => setView(true), peopleView: () => { backToLibrary(); setView(false); } });
    renderPeopleManagement();
    $("batch-merge").addEventListener("click", editBatchMerge);
    $("write-retry").addEventListener("click", async () => {
      if (!state.pendingWrite || state.writing) return;
      const intent = state.pendingWrite;
      try { await mutate(intent.path, intent.body, intent.method, intent.label); }
      catch (error) { message(errorText(error)); }
    });
    $("people").addEventListener("click", () => setView(false)); $("organize").addEventListener("click", () => setView(true));
    $("batch-toggle").addEventListener("click", () => {
      if (state.visibilityBusy || state.faceVisibilityBusy || state.writing || state.pendingWrite) return;
      state.batchMode = !state.batchMode;
      if (!state.batchMode) clearPeopleSelection();
      else renderPeopleManagement();
    });
    $("inbox-batch-toggle").addEventListener("click", () => {
      if (state.writing || state.pendingWrite || state.faceVisibilityBusy) return;
      if (state.inboxBatchMode) clearInboxSelection(false);
      state.inboxBatchMode = !state.inboxBatchMode; renderFaceManagement();
    });
    $("inbox-hidden-batch-toggle").addEventListener("click", () => {
      if (state.writing || state.pendingWrite || state.faceVisibilityBusy) return;
      if (state.hiddenBatchMode) clearInboxSelection(true);
      state.hiddenBatchMode = !state.hiddenBatchMode; renderFaceManagement();
    });
    $("people-clear").addEventListener("click", clearSelectedPeople);
    $("inbox-clear").addEventListener("click", () => clearInboxSelection(false));
    $("inbox-hidden-clear").addEventListener("click", () => clearInboxSelection(true));
    $("visibility-submit").addEventListener("click", submitVisibility);
    $("hidden-toggle").addEventListener("click", () => {
      state.hiddenView = !state.hiddenView; if (state.hiddenView) state.ignoreState = "TEMPORARY"; state.selectedPeople.clear(); renderPeopleManagement();
      $("query").value = ""; loadPeople(false);
    });
    $("ignore-temporary").addEventListener("click", () => setIgnoreState("TEMPORARY"));
    $("ignore-excluded").addEventListener("click", () => setIgnoreState("EXCLUDED"));
    $("find").addEventListener("submit", (event) => { event.preventDefault(); loadPeople(false); });
    $("query").addEventListener("search", () => loadPeople(false));
    $("people-retry").addEventListener("click", () => loadPeople(false));
    $("inbox-retry").addEventListener("click", () => loadInbox(false));
    $("inbox-summary-retry").addEventListener("click", loadInboxSummary);
    $("collections-retry").addEventListener("click", reloadCollections);
    $("collection-choice").addEventListener("focus", ensureAllCollectionsLoaded);
    $("collection-state").addEventListener("change", () => {
      const cleared = state.selectedPeople.size;
      state.selectedPeople.clear();
      renderPeopleManagement();
      if (cleared) message("已切换未加入合集筛选，并清除 " + cleared + " 项人物选择。", true);
      loadPeople(false);
    });
    $("refresh").addEventListener("click", refreshPeopleViews);
    try { state.peopleChangeToken = window.localStorage.getItem("aisearch.persons.changed"); }
    catch (error) { /* Browser storage is optional. */ }
    window.addEventListener("focus", refreshChangedPeople);
    window.addEventListener("pageshow", (event) => { if (event.persisted) refreshChangedPeople(); });
    window.addEventListener("storage", (event) => {
      if (event.key === "aisearch.persons.changed") refreshChangedPeople();
    });
    if (!state.peoplePager) $("more").addEventListener("click", () => loadPeople(true)); $("inbox-more").addEventListener("click", () => loadInbox(true));
    $("inbox-kind").addEventListener("change", () => {
      const cleared = state.selectedInboxFaces.size;
      state.selectedInboxFaces.clear(); state.inboxCursor = null;
      state.selectAnchors.delete("faces:inbox:list");
      state.inboxSeq++; state.inboxFilterSeq++; renderFaceManagement();
      if (cleared) message("切换待整理原因，已清除 " + cleared + " 项人脸选择。", true);
      loadInbox(false);
    });
    $("quality-toggle").addEventListener("change", () => {
      setQualityView($("quality-toggle").checked ? "all" : "clean");
    });
    $("inbox-hide-submit").addEventListener("click", () => submitInboxVisibility(true));
    $("inbox-assign").addEventListener("click", () => editSelectedInbox("assign"));
    $("inbox-unassign").addEventListener("click", () => editSelectedInbox("unassign"));
    $("inbox-auto").addEventListener("click", () => editSelectedInbox("restore_auto"));
    $("inbox-restore-submit").addEventListener("click", () => submitInboxVisibility(false));
    $("inbox-hidden-toggle").addEventListener("click", () => {
      state.inboxHiddenVisible = !state.inboxHiddenVisible;
      renderFaceManagement();
      if (state.inboxHiddenVisible) loadHiddenFaces(false);
    });
    $("inbox-hidden-more").addEventListener("click", () => loadHiddenFaces(true));
    $("rematch").addEventListener("click", () => rematchFaces(false));
    $("rematch-restart").addEventListener("click", () => rematchFaces(true));
    $("detail-batch").addEventListener("click", startDetailBatch);
    $("recognition-tools").addEventListener("toggle", () => { if ($("recognition-tools").open) loadDetailControls(); });
    $("detail-source").addEventListener("change", loadDetailBatch);
    $("detail-pause").addEventListener("click", () => changeDetailBatch("pause"));
    $("detail-resume").addEventListener("click", () => changeDetailBatch("resume"));
    $("detail-retry").addEventListener("click", () => changeDetailBatch("retry"));
    const photoReturn = photoReturnRequested();
    if (photoReturn) $("back").textContent = "← 返回查看照片";
    $("back").addEventListener("click", () => {
      if (photoReturn && window.history && window.history.length > 1) { window.history.back(); return; }
      const deepLinked = state.deepLinked;
      if (deepLinked) { state.deepLinked = false; state.linkSeq++; clearPersonRoute(); }
      backToLibrary();
    });
    $("collections-toggle").addEventListener("click", () => { $("collections-panel").hidden = !$("collections-panel").hidden; syncDirectoryPagers(); if (!$("collections-panel").hidden) loadCollections(false); });
    $("collection-create").addEventListener("click", async () => { const collection = await createCollection(); if (collection) { $("collections-panel").hidden = false; syncDirectoryPagers(); message("已新建人物合集。", true); } });
    if (!state.collectionPager) $("collections-more").addEventListener("click", () => loadCollections(true));
    $("collection-add").addEventListener("click", addSelectionToCollection);
    $("collection-photos-tab").addEventListener("click", () => { if (!state.collection || state.collectionPeopleBusy) return; if (state.collectionPeopleManage) finishCollectionPeopleManagement(); state.collectionMode = "photos"; $("collection-photos-tab").setAttribute("aria-pressed", "true"); $("collection-people-tab").setAttribute("aria-pressed", "false"); $("collection-people").hidden = true; $("collection-people-actions").hidden = true; $("collection-people-more").hidden = true; $("collection-people-retry").hidden = true; $("photos").hidden = false; $("photos-more").hidden = false; syncDirectoryPagers(); loadPhotos(false); });
    $("collection-people-tab").addEventListener("click", () => { if (!state.collection || state.collectionPeopleBusy) return; if (state.collectionMode !== "people") { state.collectionPeopleManage = false; state.collectionSelectedPeople.clear(); } state.collectionMode = "people"; refreshSearchScopeAction(); $("collection-photos-tab").setAttribute("aria-pressed", "false"); $("collection-people-tab").setAttribute("aria-pressed", "true"); $("photos").hidden = true; $("photos-more").hidden = true; $("timeline").hidden = true; $("collection-people").hidden = false; updateCollectionSelection(); syncDirectoryPagers(); loadCollectionPeople(false); });
    $("collection-people-retry").addEventListener("click", () => loadCollectionPeople(false));
    if (!state.collectionPeoplePager) $("collection-people-more").addEventListener("click", () => loadCollectionPeople(true));
    $("collection-manage-toggle").addEventListener("click", () => {
      if (state.collectionPeopleBusy || state.collectionRemoveUnknown || state.writing || state.pendingWrite) return;
      if (state.collectionPeopleManage) finishCollectionPeopleManagement();
      else { state.collectionPeopleManage = true; updateCollectionSelection(); }
    });
    $("collection-people-reload").addEventListener("click", reloadCollectionPeopleAfterUnknown);
    $("collection-remove").addEventListener("click", removeCollectionSelection);
    $("collection-selection-clear").addEventListener("click", clearCollectionSelection);
    $("collection-people-select-loaded").addEventListener("click", () => selectLoadedCollectionPeople(false));
    $("collection-people-invert-loaded").addEventListener("click", () => selectLoadedCollectionPeople(true));
    $("collection-rename").addEventListener("click", () => renameCollection(state.collection));
    $("collection-delete").addEventListener("click", () => deleteCollection(state.collection));
    $("name").addEventListener("click", () => editBase({ mode: "name", person: { ...state.person } }, "给这个人取名字", "可用家人的称呼或你习惯的名字。"));
    $("merge").addEventListener("click", () => editMerge({ ...state.person }));
    $("cover").addEventListener("click", () => {
      if (state.writing || !state.person) return;
      state.coverMode = !state.coverMode; state.searching = false; $("photo-query").value = "";
      albumHeading(); loadPhotos(false);
    });
    if (!state.photoPager) $("photos-more").addEventListener("click", () => loadPhotos(true));
    $("timeline-refresh").addEventListener("click", async () => {
      $("timeline-refresh").hidden = true;
      $("photo-note").textContent = "正在读取合集最新版本；年份、月份和日期分组筛选将重置……";
      const preferences = state.timeline && state.timeline.snapshot();
      const collectionId = state.collection && Number(state.collection.collection_id);
      if (!(await reloadCollections()) || !(await ensureAllCollectionsLoaded())) {
        $("photo-note").textContent = "合集列表刷新失败，请返回后重试。";
        $("timeline-refresh").hidden = false;
        return;
      }
      const current = state.collectionById.get(String(collectionId));
      if (!current) {
        $("timeline").dataset.collectionGone = "true";
        $("photo-note").textContent = "这个合集已不存在。请返回合集列表。";
        return;
      }
      state.collection = current; collectionHeading();
      delete $("timeline").dataset.collectionExpired;
      delete $("timeline").dataset.collectionGone;
      if (state.timeline) state.timeline.dispose();
      state.timeline = null; state.timelineScopeKey = "";
      showTimeline(false, preferences);
    });
    $("search-photos").addEventListener("submit", (event) => {
      event.preventDefault(); const text = $("photo-query").value.trim(); if (!text) { state.searching = false; loadPhotos(false); return; }
      state.searching = true; state.searchBody = { contract_version: "p2-contracts.v1", request_id: requestId(), text, person_ids: [state.person.person_id], person_match: "any", page_size: 60, top_k: 5000 }; loadPhotos(false);
    });
    $("all-photos").addEventListener("click", () => { state.searching = false; $("photo-query").value = ""; loadPhotos(false); });
    closeOnBackdrop($("preview"));
    window.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || event.repeat || event.defaultPrevented || event.isComposing) return;
      const target = event.target;
      if (document.querySelector && document.querySelector("dialog[open]")) return;
      const checkbox = target && String(target.tagName || "").toUpperCase() === "INPUT" && String(target.type || "").toLowerCase() === "checkbox";
      if (target && (target.isContentEditable || (!checkbox && /^(INPUT|TEXTAREA|SELECT)$/.test(String(target.tagName || "").toUpperCase())))) return;
      if (window.AIPersonTools && window.AIPersonTools.hideFaces()) { event.preventDefault(); return; }
      if (state.collectionPeopleManage && !$("album").hidden) {
        if (finishCollectionPeopleManagement()) event.preventDefault();
        return;
      }
      if (!$("library").hidden && state.organize && state.inboxHiddenVisible && state.hiddenBatchMode) {
        if (state.writing || state.pendingWrite || state.faceVisibilityBusy) return;
        clearInboxSelection(true); state.hiddenBatchMode = false; renderFaceManagement(); event.preventDefault(); return;
      }
      if (!$("library").hidden && state.organize && state.inboxBatchMode) {
        if (state.writing || state.pendingWrite || state.faceVisibilityBusy) return;
        clearInboxSelection(false); state.inboxBatchMode = false; renderFaceManagement(); event.preventDefault(); return;
      }
      if (!$("library").hidden && state.batchMode && !state.visibilityBusy && !state.writing && !state.pendingWrite) {
        clearPeopleSelection(); event.preventDefault();
      }
    });
    $("edit-form").addEventListener("submit", submitEdit);
    $("edit-find").addEventListener("click", () => loadChoices(false)); $("choice-more").addEventListener("click", () => loadChoices(true));
    $("edit-query").addEventListener("input", () => { if (state.edit && state.edit.mode !== "name") { state.chosen = null; state.choiceSeq++; $("edit-submit").disabled = true; } });
    $("edit-cancel").addEventListener("click", () => $("edit").close());
    $("edit").addEventListener("cancel", (event) => { if (state.writing) event.preventDefault(); });
    $("edit").addEventListener("close", () => { state.editSeq++; state.choiceSeq++; });
    $("undo").addEventListener("click", undo);
    $("create-person").addEventListener("click", createPersonFromFace);
    $("undo-dismiss").addEventListener("click", () => { clearTimeout(state.undoTimer); state.undoTimer = null; state.operation = null; $("undo-bar").hidden = true; });
    if (typeof window !== "undefined") window.addEventListener("pagehide", () => {
      state.selectedPeople.clear(); state.selectedInboxFaces.clear(); state.selectedHiddenFaces.clear();
      [state.peoplePager, state.collectionPager, state.collectionPeoplePager, state.photoPager].forEach((pager) => { if (pager) pager.setEnabled(false); });
    });
    window.addEventListener("pageshow", syncDirectoryPagers);
    const linkedPersonId = requestedPersonId();
    if (linkedPersonId) openPersonById(linkedPersonId);
    else { loadPeople(false); }
    const assetId = Number(new URLSearchParams(typeof window !== "undefined" ? window.location.search : "").get("asset_id"));
    if (Number.isSafeInteger(assetId) && assetId > 0 && window.location.hash !== "#panel-mappings") preview(assetId);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
