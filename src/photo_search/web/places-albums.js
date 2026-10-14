(function () {
  "use strict";
  var api = "/api/v1";
  var kind = document.getElementById("album-kind");
  var name = document.getElementById("album-name");
  var cards = document.getElementById("album-cards");
  var listView = document.getElementById("album-list-view");
  var detailView = document.getElementById("album-detail-view");
  var photos = document.getElementById("album-photos");
  var currentTitle = document.getElementById("album-current-title");
  var detailStatus = document.getElementById("album-detail-status");
  var management = document.getElementById("place-management");
  var advancedTools = document.getElementById("place-advanced-tools");
  var backfill = document.getElementById("album-backfill");
  var backfillStatus = document.getElementById("album-backfill-status");
  var current = null;
  var timeline = null;
  var albumRequest = 0;
  var detailGeneration = 0;
  var correctionGeneration = 0;
  var detailCanBack = false;
  var listLoaded = false;
  var listScrollY = 0;
  var albumViews = Object.create(null);
  var albumViewOrder = [];
  var ALBUM_KINDS = ["city", "province", "district", "country"];
  var MANAGEMENT_IDS = ["panel-datasets", "panel-directory", "panel-create", "panel-edit",
    "panel-suggestions", "panel-operations"];
  var MANAGEMENT_HASHES = MANAGEMENT_IDS;
  var setSearchScopeAction = window.SearchScopeEntry ? window.SearchScopeEntry.configure({
    button: document.getElementById("global-search-scope"),
    input: document.getElementById("global-search-query"),
    getScope: function () {
      return current ? { kind: "place", place_kind: current.kind, place_key: String(current.key),
        name: String(current.name || "地点"), parent: String(current.parent || "") } : null;
    },
  }) : function () {};

  function el(tag, cls, value) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (value !== undefined) node.textContent = String(value);
    return node;
  }
  function isPlaceKind(value) { return ALBUM_KINDS.indexOf(String(value || "")) >= 0; }
  function routeParams() { return new URLSearchParams(window.location.search || ""); }
  function albumRoute() {
    var params = routeParams();
    return { kind: params.get("album_kind"), key: params.get("album_key"), hasAlbumParam: params.has("album_kind") || params.has("album_key") };
  }
  function pageUrl(params) {
    var query = params.toString();
    return window.location.pathname + (query ? "?" + query : "") + (window.location.hash || "");
  }
  function stateWith(extra) {
    var state = window.history.state;
    var copy = state && typeof state === "object" ? Object.assign({}, state) : {};
    return Object.assign(copy, extra);
  }
  function setAlbumUrl(album, mode, returnEntry) {
    var params = routeParams();
    params.set("album_kind", String(album.kind));
    params.set("album_key", String(album.key));
    var url = pageUrl(params);
    var state = stateWith({ __placeAlbumDetail: true, __placeAlbumReturn: Boolean(returnEntry) });
    if (mode === "push") window.history.pushState(state, "", url);
    else window.history.replaceState(state, "", url);
  }
  function clearAlbumUrl(replace) {
    var params = routeParams();
    params.delete("album_kind"); params.delete("album_key");
    var url = pageUrl(params);
    var state = stateWith({ __placeAlbumDetail: false, __placeAlbumReturn: false });
    if (replace) window.history.replaceState(state, "", url);
    return url;
  }
  function refreshSearchScopeAction() {
    setSearchScopeAction(current ? { kind: "place", place_kind: String(current.kind), place_key: String(current.key),
      name: String(current.name || "地点"), parent: String(current.parent || "") } : null, "地点");
  }
  function get(url) {
    return fetch(api + url, { credentials: "same-origin", headers: { Accept: "application/json" } }).then(function (r) {
      return r.json().then(function (body) { if (!r.ok) { var error = new Error(body.message || body.code || "读取失败"); error.code = body.code; throw error; } return body; });
    });
  }
  function switchView(view, panelId) {
    var isDetail = view === "detail";
    var isManage = view === "manage";
    listView.hidden = !(!isDetail && !isManage);
    detailView.hidden = !isDetail;
    var albumPanel = document.getElementById("panel-albums");
    if (albumPanel) albumPanel.hidden = isManage;
    if (management) management.hidden = !isManage;
    if (advancedTools) advancedTools.hidden = !(isManage && panelId === "panel-datasets");
    MANAGEMENT_IDS.forEach(function (id) {
      var panel = document.getElementById(id);
      if (panel) panel.hidden = !isManage || id !== panelId;
    });
    document.body.dataset.placeAlbumView = view;
    if (albumPanel) albumPanel.setAttribute("aria-labelledby", isDetail ? "album-title-current" : "albums-title");
    if (!isDetail) refreshSearchScopeAction();
    if (view === "list" && typeof window.dispatchEvent === "function" && typeof Event === "function") {
      window.dispatchEvent(new Event("place-album-list-visible"));
    }
  }
  function managementPanelFromHash(hash) { return isManagementHash(hash) ? hash : "panel-suggestions"; }
  function showManagement(panelId, historyMode) {
    panelId = MANAGEMENT_HASHES.indexOf(panelId) >= 0 ? panelId : "panel-suggestions";
    if (!detailView.hidden) { disposeTimeline(true); current = null; detailCanBack = false; refreshSearchScopeAction(); }
    var params = routeParams(); params.delete("album_kind"); params.delete("album_key"); params.set("place_view", "manage");
    var url = window.location.pathname + "?" + params.toString() + "#" + panelId;
    var canReturn = historyMode === "push" || Boolean(window.history.state && window.history.state.__placeManageReturn);
    if (historyMode === "push") window.history.pushState(stateWith({ __placeManageReturn: true, __placeAlbumReturn: false }), "", url);
    else window.history.replaceState(stateWith({ __placeManageReturn: canReturn, __placeAlbumReturn: false }), "", url);
    switchView("manage", panelId);
    if (typeof window.CustomEvent === "function") window.dispatchEvent(new window.CustomEvent("place-management-panel-visible", { detail: { id: panelId } }));
  }
  function returnToBrowse() {
    if (window.history.state && window.history.state.__placeManageReturn) { window.history.back(); return; }
    var params = routeParams(); params.delete("place_view");
    var hash = isManagementHash((window.location.hash || "").slice(1)) ? "" : (window.location.hash || "");
    window.history.replaceState(stateWith({ __placeManageReturn: false }), "", window.location.pathname + (params.toString() ? "?" + params.toString() : "") + hash);
    switchView("list"); if (!listLoaded) loadAlbums();
  }
  function rememberAlbumView(album) {
    if (!album || !timeline) return;
    var snapshot = timeline.snapshot();
    var key = String(album.kind) + "\u0000" + String(album.key);
    albumViews[key] = {
      revision: snapshot.catalogFilterRevision === undefined ? null : snapshot.catalogFilterRevision,
      timeBasis: snapshot.timeBasis, groupBy: snapshot.groupBy, bucket: snapshot.bucket,
      year: snapshot.year, month: snapshot.month, anchor: snapshot.anchor || null, assetId: snapshot.assetId || null,
    };
    albumViewOrder = albumViewOrder.filter(function (item) { return item !== key; });
    albumViewOrder.push(key);
    while (albumViewOrder.length > 4) delete albumViews[albumViewOrder.shift()];
  }
  function disposeTimeline(save) {
    if (save) rememberAlbumView(current);
    if (timeline) { timeline.dispose(); timeline = null; }
  }
  function listSnapshot() {
    var active = document.activeElement;
    var focus = active && active.classList && active.classList.contains("album-card")
      ? { kind: active.dataset.albumKind || "", key: active.dataset.albumKey || "" } : null;
    return { kind: kind.value, name: name.value, scrollY: Number(window.scrollY || 0), focus: focus };
  }
  function restoreList(snapshot, loadIfEmpty) {
    detailGeneration++; correctionGeneration++; albumRequest++;
    disposeTimeline(true);
    current = null; detailCanBack = false;
    if (snapshot) { if (isPlaceKind(snapshot.kind)) kind.value = snapshot.kind; name.value = snapshot.name || ""; listScrollY = Number(snapshot.scrollY || 0); }
    photos.replaceChildren(); currentTitle.replaceChildren(); detailStatus.replaceChildren();
    switchView("list");
    if (!listLoaded && loadIfEmpty !== false) loadAlbums();
    window.setTimeout(function () {
      if (window.location.hash && isManagementHash(window.location.hash.slice(1))) {
        var target = document.getElementById(window.location.hash.slice(1));
        if (target) target.scrollIntoView({ block: "start" });
      } else if (window.scrollTo) window.scrollTo(0, listScrollY);
      if (snapshot && snapshot.focus) {
        var match = Array.prototype.slice.call(cards.querySelectorAll(".album-card")).find(function (node) {
          return node.dataset.albumKind === snapshot.focus.kind && node.dataset.albumKey === snapshot.focus.key;
        });
        if (match && match.focus) match.focus({ preventScroll: true });
      }
    }, 0);
  }
  function isManagementHash(hash) { return MANAGEMENT_HASHES.indexOf(hash) >= 0; }
  function loadAlbums() {
    if (detailView.hidden === false) return;
    var request = ++albumRequest;
    listLoaded = false;
    cards.replaceChildren(el("p", "hint", "正在整理地点相册…"));
    var params = new URLSearchParams({ kind: kind.value });
    if (name.value.trim()) params.set("name", name.value.trim());
    get("/places/albums?" + params.toString()).then(function (body) {
      if (request !== albumRequest || !detailView.hidden) return;
      cards.replaceChildren();
      listLoaded = true;
      loadCnCapability();
      if (!body.albums || !body.albums.length) { cards.appendChild(el("p", "hint", kind.value === "province" ? "暂无省份索引或暂无照片。旧地名包仍可浏览城市和国家。" : "当前没有可浏览的地点照片。")); return; }
      body.albums.forEach(function (album, index) {
        var button = el("button", "album-card"); button.type = "button";
        button.dataset.albumKind = String(album.kind); button.dataset.albumKey = String(album.key);
        var image = el("img", "album-cover"); image.loading = index < 4 ? "eager" : "lazy"; image.alt = "";
        if ("fetchPriority" in image) image.fetchPriority = index < 4 ? "high" : "auto";
        image.src = api + "/assets/" + encodeURIComponent(album.cover_asset_id) + "/thumbnail";
        button.appendChild(image);
        button.appendChild(el("strong", "album-title", album.name));
        button.appendChild(el("span", "album-parent", album.parent || ""));
        if (album.origin === "GPS_NEARBY_CITY" || album.origin === "MIXED" || album.origin === "GPS_ADMIN") button.appendChild(el("span", "album-origin", album.origin === "MIXED" ? "含机器地点与人工归类" : album.origin === "GPS_ADMIN" ? "GPS行政区 · 机器解析" : "GPS附近城市"));
        button.appendChild(el("span", "album-total", album.total + " 张照片"));
        button.addEventListener("click", function () { openAlbum(album, "push", true); });
        cards.appendChild(button);
      });
    }).catch(function (error) { if (request === albumRequest && detailView.hidden) { listLoaded = true; cards.replaceChildren(el("p", "error", error.message)); } });
  }
  function focusDetail() {
    var back = currentTitle.querySelector(".album-back");
    if (back && back.focus) back.focus({ preventScroll: true });
    var heading = document.getElementById("album-title-current");
    if (heading && heading.scrollIntoView) heading.scrollIntoView({ block: "start" });
  }
  function showDetailLoading(route, message) {
    detailGeneration++; correctionGeneration++; albumRequest++;
    disposeTimeline(true); current = null; refreshSearchScopeAction();
    switchView("detail");
    currentTitle.replaceChildren(); detailStatus.replaceChildren(); photos.replaceChildren();
    var back = el("button", "album-back", "返回地点相册列表"); back.type = "button";
    back.addEventListener("click", returnToList);
    var heading = el("h2", null, "正在打开地点相册…"); heading.id = "album-title-current";
    currentTitle.append(back, heading);
    detailStatus.textContent = message || "正在读取地点目录并检查照片范围…";
    return detailGeneration;
  }
  function showDetailError(route, message, retry) {
    detailGeneration++; correctionGeneration++;
    disposeTimeline(true); current = null; refreshSearchScopeAction();
    currentTitle.replaceChildren(); detailStatus.replaceChildren(); photos.replaceChildren();
    var back = el("button", "album-back", "返回地点相册列表"); back.type = "button";
    back.addEventListener("click", returnToList);
    var heading = el("h2", null, "地点相册暂不可用"); heading.id = "album-title-current";
    currentTitle.append(back, heading);
    detailStatus.appendChild(el("p", "error", message));
    if (retry) {
      var again = el("button", null, "重试"); again.type = "button";
      again.addEventListener("click", retry); detailStatus.appendChild(again);
    }
    focusDetail();
  }
  function openAlbum(album, historyMode, returnEntry) {
    if (!album || !isPlaceKind(album.kind) || album.key === undefined || album.key === null || String(album.key) === "") {
      showDetailError(album || {}, "地点身份无效。", null); return;
    }
    if (historyMode === "push") {
      var snap = listSnapshot(); listScrollY = snap.scrollY;
      window.history.replaceState(stateWith({ __placeAlbumList: snap }), "", pageUrl(routeParams()));
      setAlbumUrl(album, "push", returnEntry);
    } else if (historyMode === "replace") setAlbumUrl(album, "replace", returnEntry);
    detailCanBack = Boolean(returnEntry);
    var generation = showDetailLoading({ kind: album.kind, key: String(album.key) }, "");
    current = album;
    refreshSearchScopeAction();
    var heading = el("div", "album-current-title__heading");
    var title = el("h2", null, album.name + " · 地点相册"); title.id = "album-title-current";
    title.tabIndex = -1;
    heading.appendChild(title);
    if (album.parent) heading.appendChild(el("span", "album-parent", album.parent));
    if (album.origin) heading.appendChild(el("span", "album-origin", "来源 · " + album.origin));
    heading.appendChild(el("span", "album-total", album.total + " 张有效照片"));
    var back = el("button", "album-back", "返回地点相册列表"); back.type = "button"; back.addEventListener("click", returnToList);
    currentTitle.replaceChildren(back, heading);
    detailStatus.textContent = "正在读取照片时间轴…";
    var key = String(album.kind) + "\u0000" + String(album.key);
    var restore = albumViews[key] || null;
    timeline = window.PhotoTimeline.mount({
      host: photos,
      scope: { kind: "place", place_kind: album.kind, place_key: String(album.key) },
      restoreView: restore,
      adapter: {
        onPage: function (body) {
          if (generation !== detailGeneration || !current || current.kind !== album.kind || String(current.key) !== String(album.key)) return;
          var counts = body.counts || {};
          var effective = counts.effective_total;
          if (effective !== undefined && String(effective) !== String(album.total)) {
            album.total = effective; heading.lastChild.textContent = effective + " 张有效照片";
          }
          detailStatus.textContent = effective === 0 ? "这个地点暂时没有有效照片。" : "";
        },
        renderAsset: function (item) {
          var card = el("article", "album-photo");
          var view = el("button", "album-photo-open"); view.type = "button";
          view.setAttribute("aria-label", "查看照片 " + item.asset_id);
          var image = el("img"); image.loading = "lazy"; image.decoding = "async"; image.alt = "";
          image.src = api + "/assets/" + encodeURIComponent(item.asset_id) + "/thumbnail";
          view.appendChild(image); view.addEventListener("click", function () { showPreview(item.asset_id, view); });
          card.appendChild(view);
          var dateLabel = item.display_time || (item.date_state === "unknown"
            ? "日期未知 · 按加入图库日期" : item.date_state === "pending"
              ? "日期待就绪 · 按加入图库日期" : item.time_label || "查看照片");
          card.appendChild(el("small", "album-photo__date", dateLabel));
          var tools = document.createElement("details"); tools.className = "album-correction";
          tools.appendChild(el("summary", null, "更多操作"));
          var body = el("div", "album-correction__body");
          var assign = el("div", "album-correction__assign");
          var input = el("input"); input.type = "search"; input.maxLength = 200; input.placeholder = "搜索地点名称";
          input.setAttribute("aria-label", "搜索要更正到的地点"); assign.appendChild(input);
          var find = el("button", null, "改到其他地点"); find.type = "button";
          find.addEventListener("click", function () { correct(item.asset_id, "assign", input, body); }); assign.appendChild(find);
          assign.appendChild(el("small", "hint", "选择真实地点后保存，不能直接写入任意文本。")); body.appendChild(assign);
          var exclude = el("button", null, "排除此照片的地点归属"); exclude.type = "button";
          exclude.addEventListener("click", function () { correct(item.asset_id, "unassign", input, body); }); body.appendChild(exclude);
          body.appendChild(el("small", "hint", "排除后会阻止自动重新归入此地点。"));
          var restore = el("button", null, "恢复自动识别"); restore.type = "button";
          restore.addEventListener("click", function () { correct(item.asset_id, "restore_auto", input, body); }); body.appendChild(restore);
          tools.appendChild(body); card.appendChild(tools);
          return card;
        }
      }
    });
    detailStatus.textContent = "";
    focusDetail();
  }
  function openRoute(route, historyMode, returnEntry) {
    if (isPlaceKind(route.kind)) kind.value = route.kind;
    var generation = showDetailLoading(route, "正在读取地点目录并检查照片范围…");
    detailCanBack = Boolean(returnEntry);
    if (!isPlaceKind(route.kind) || !route.key) {
      showDetailError(route, "地点链接缺少有效的类型或地点身份。", null); return;
    }
    var params = new URLSearchParams({ kind: route.kind });
    get("/places/albums?" + params.toString()).then(function (body) {
      if (generation !== detailGeneration) return;
      var album = (body.albums || []).find(function (item) {
        return String(item.kind) === String(route.kind) && String(item.key) === String(route.key);
      });
      if (!album) { showDetailError(route, "此地点已不存在或当前没有有效照片。请返回地点相册列表重新选择。", null); return; }
      openAlbum(album, historyMode, returnEntry);
    }).catch(function (error) {
      if (generation !== detailGeneration) return;
      showDetailError(route, "无法读取此地点相册：" + error.message, function () { openRoute(route, "replace", returnEntry); });
    });
  }
  function returnToList() {
    disposeTimeline(true); current = null; refreshSearchScopeAction();
    if (detailCanBack && window.history.state && window.history.state.__placeAlbumReturn) {
      window.history.back(); return;
    }
    clearAlbumUrl(true);
    var snapshot = window.history.state && window.history.state.__placeAlbumList;
    restoreList(snapshot || { kind: kind.value, name: name.value, scrollY: listScrollY }, true);
  }
  function handlePopState(event) {
    var route = albumRoute();
    if (!route.hasAlbumParam && routeParams().get("place_view") === "manage") {
      showManagement(managementPanelFromHash((window.location.hash || "").slice(1)), "replace"); return;
    }
    if (route.hasAlbumParam) {
      openRoute(route, "pop", Boolean(event && event.state && event.state.__placeAlbumReturn));
      return;
    }
    var snapshot = event && event.state && event.state.__placeAlbumList;
    restoreList(snapshot || { kind: kind.value, name: name.value, scrollY: listScrollY }, true);
  }
  function handleHashChange() {
    var route = albumRoute();
    var hash = (window.location.hash || "").slice(1);
    if (route.hasAlbumParam && isManagementHash(hash)) {
      disposeTimeline(true); current = null; detailCanBack = false;
      showManagement(hash, "replace");
      return;
    }
    if (!route.hasAlbumParam && isManagementHash(hash)) { showManagement(hash, "replace"); return; }
  }
  function showPreview(assetId, returnFocus) {
    if (window.AIPhotoInfo) { window.AIPhotoInfo.open(assetId, { returnFocus: returnFocus }); return; }
    var dialog = document.createElement("dialog"); dialog.className = "album-preview";
    var close = el("button", null, "关闭"); close.type = "button"; close.addEventListener("click", function () { dialog.close(); dialog.remove(); });
    var image = el("img"); image.alt = "照片预览"; image.src = api + "/assets/" + encodeURIComponent(assetId) + "/preview";
    dialog.append(close, image); document.body.appendChild(dialog);
    dialog.addEventListener("click", function (event) { if (event.target === dialog) dialog.close(); });
    dialog.addEventListener("close", function () { dialog.remove(); }); dialog.showModal();
  }
  function csrf() {
    var match = document.cookie.match(/(?:^|;\s*)ps_csrf=([^;]+)/); return match ? decodeURIComponent(match[1]) : "";
  }
  function write(assetId, action, placeRef) {
    return get("/assets/" + assetId + "/place").then(function (state) {
      var assignment = state.assignment || {};
      var payload = { action: action, expected_revision: Number(assignment.revision || 0), request_id: "album-" + Date.now() + "-" + Math.random().toString(16).slice(2) };
      if (action === "assign") payload.target_place_ref = placeRef;
      return fetch(api + "/assets/" + assetId + "/place-assignment", { method: "PUT", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf() }, body: JSON.stringify(payload) }).then(function (r) { return r.json().then(function (b) { if (!r.ok) throw new Error(b.message || b.code || "保存失败"); return b; }); });
    });
  }
  function refreshCurrentAlbum() {
    if (!current || detailView.hidden) return;
    var album = current;
    var request = ++correctionGeneration;
    var key = String(album.kind) + "\u0000" + String(album.key);
    get("/places/albums?" + new URLSearchParams({ kind: album.kind }).toString()).then(function (body) {
      if (request !== correctionGeneration || current !== album || detailView.hidden) return;
      var updated = (body.albums || []).find(function (item) { return String(item.kind) === String(album.kind) && String(item.key) === String(album.key); });
      if (!updated) {
        showDetailError({ kind: album.kind, key: album.key }, "此地点已没有有效照片，或地点已失效。", function () { openRoute({ kind: album.kind, key: String(album.key) }, "replace", detailCanBack); });
        return;
      }
      current = updated;
      var heading = currentTitle.querySelector(".album-current-title__heading");
      if (heading) {
        var total = heading.querySelector(".album-total"); if (total) total.textContent = updated.total + " 张有效照片";
        var parent = heading.querySelector(".album-parent"); if (parent) parent.textContent = updated.parent || "";
      }
      refreshSearchScopeAction();
      detailStatus.textContent = "正在更新此地点的照片…";
      var saved = timeline && timeline.snapshot();
      if (timeline) timeline.reset(saved && saved.anchor || null);
      window.setTimeout(function () { if (current === updated) detailStatus.textContent = ""; }, 0);
    }).catch(function (error) {
      if (request !== correctionGeneration || current !== album) return;
      detailStatus.replaceChildren(el("p", "error", "地点更正已保存，但相册刷新失败：" + error.message));
      var retry = el("button", null, "重试刷新相册"); retry.type = "button"; retry.addEventListener("click", refreshCurrentAlbum); detailStatus.appendChild(retry);
    });
  }
  function correct(assetId, action, input, holder) {
    var generation = detailGeneration;
    var scopeKey = current ? String(current.kind) + "\u0000" + String(current.key) : "";
    function refreshIfCurrent() {
      if (generation === detailGeneration && current && scopeKey === String(current.kind) + "\u0000" + String(current.key)) refreshCurrentAlbum();
    }
    function showMessage(text, failed) {
      var status = holder.querySelector(".album-correction__status");
      if (!status) { status = el("p", "album-correction__status"); holder.appendChild(status); }
      status.setAttribute("role", failed ? "alert" : "status");
      status.className = "album-correction__status" + (failed ? " error" : ""); status.textContent = text;
    }
    if (action === "unassign" || action === "restore_auto") {
      write(assetId, action, null).then(function () { showMessage("已保存，正在更新当前地点相册…", false); refreshIfCurrent(); }).catch(function (e) { showMessage(e.message, true); });
      return;
    }
    var choice = input && input.value.trim();
    if (!choice) { showMessage("请输入地点名称，再从真实地点目录中选择。", true); if (input) input.focus(); return; }
    get("/places/choices?" + new URLSearchParams({ name: choice }).toString()).then(function (body) {
      if (generation !== detailGeneration) return;
      holder.querySelectorAll(".place-choice-list").forEach(function (item) { item.remove(); });
      var choices = body.choices || [];
      if (!choices.length) throw new Error("没有找到可选地点。请调整搜索词后重试。");
      var list = el("div", "place-choice-list");
      list.setAttribute("aria-label", "可选地点");
      list.appendChild(el("small", "hint", "请选择真实地点；同名地点显示上级和来源："));
      choices.forEach(function (place) {
        var button = el("button", null, place.name + (place.parent ? " · 上级 " + place.parent : "") + "（来源：" + place.source + "）");
        button.type = "button";
        button.addEventListener("click", function () {
          button.disabled = true;
          write(assetId, "assign", place.selection_ref).then(function () { showMessage("已保存，正在更新当前地点相册…", false); refreshIfCurrent(); }).catch(function (e) { button.disabled = false; showMessage(e.message, true); });
        });
        list.appendChild(button);
      });
      holder.appendChild(list);
    }).catch(function (e) { if (generation === detailGeneration) showMessage(e.message, true); });
  }

  kind.addEventListener("change", loadAlbums);
  var manageButton = document.getElementById("open-place-management");
  var manageBack = document.getElementById("back-to-place-albums");
  if (manageButton) manageButton.addEventListener("click", function () {
    var snapshot = listSnapshot(); listScrollY = snapshot.scrollY;
    window.history.replaceState(stateWith({ __placeAlbumList: snapshot }), "", pageUrl(routeParams()));
    showManagement("panel-suggestions", "push");
  });
  if (manageBack) manageBack.addEventListener("click", returnToBrowse);
  window.addEventListener("place-management-panel-requested", function (event) {
    showManagement(event && event.detail && event.detail.id, "replace");
  });
  Array.prototype.forEach.call(document.querySelectorAll("[data-place-panel]"), function (button) {
    button.addEventListener("click", function () { showManagement(button.dataset.placePanel, "replace"); });
  });
  var cnUpdate = document.getElementById("cn-update");
  var cnPause = document.getElementById("cn-pause");
  var cnStatus = document.getElementById("cn-update-status");
  var cnCapability = null;
  var cnCapabilityStarted = false;
  var cnStop = false;
  function loadCnCapability() {
    if (cnCapabilityStarted) return;
    cnCapabilityStarted = true;
    get("/places/admin/capabilities").then(function (body) {
      cnCapability = body; cnUpdate.hidden = !body.available;
      if (body.available && body.update && !body.update.complete) cnStatus.textContent = "中国地点更新尚未完成，点击继续；已完成的结果会复用。";
    }).catch(function (error) {
      cnStatus.textContent = "无法读取中国地点更新能力：" + error.message;
      cnCapabilityStarted = false;
    });
  }
  if (cnUpdate && cnPause && cnStatus) {
    cnPause.addEventListener("click", function () { cnStop = true; cnPause.disabled = true; cnStatus.textContent = "正在完成当前批次，然后暂停…"; });
    cnUpdate.addEventListener("click", function () {
      if (!cnCapability && !cnCapabilityStarted) loadCnCapability();
      if (!cnCapability || !cnCapability.available || cnUpdate.disabled) return;
      cnStop = false; cnUpdate.disabled = true; cnPause.hidden = false; cnPause.disabled = false;
      cnStatus.textContent = "正在更新中国地点，仅使用已有GPS…";
      var restart = Boolean(cnCapability.update && cnCapability.update.complete);
      function batch() {
        var query = new URLSearchParams({ dataset_id: cnCapability.dataset_id, restart: String(restart) }); restart = false;
        return fetch(api + "/places/admin/update?" + query, { method: "POST", credentials: "same-origin", headers: { "X-CSRF-Token": csrf(), Accept: "application/json" } }).then(function (r) {
          return r.json().then(function (b) { if (!r.ok) throw new Error(b.message || b.code || "更新失败"); return b; });
        }).then(function (body) {
          cnCapability.update = body;
          cnStatus.textContent = "已查看 " + body.total_scanned + " 张有效GPS照片，新解析 " + body.total_processed + " 张" + (body.complete ? "；更新完成。" : cnStop ? "；已暂停，可继续。" : "；更新中…");
          if (!body.complete && !cnStop) return new Promise(function (resolve) { window.setTimeout(resolve, 25); }).then(batch);
        });
      }
      batch().catch(function (error) { cnStatus.textContent = error.message + "；进度已保存，可继续。"; }).finally(function () {
        cnUpdate.disabled = false; cnPause.hidden = true; loadAlbums();
      });
    });
  }
  backfill.addEventListener("click", function () {
    backfill.disabled = true; backfillStatus.textContent = "正在处理本批缺失的 GPS 与附近地点…";
    fetch(api + "/places/albums/backfill", { method: "POST", credentials: "same-origin", headers: { "X-CSRF-Token": csrf(), Accept: "application/json" } }).then(function (response) {
      return response.json().then(function (body) { if (!response.ok) throw new Error(body.message || body.code || "处理失败"); return body; });
    }).then(function (body) {
      var resolverText = body.resolver_available ? "附近地点解析完成 " + (body.place_resolution.steps || 0) + " 步" : "未安装可用地名包（" + (body.resolver_reason || "不可用") + "），仍可使用人工地点";
      backfillStatus.textContent = "GPS处理 " + (body.gps.steps || 0) + " 步；" + resolverText + "。剩余 GPS " + body.remaining_gps + "，剩余解析 " + body.remaining_place_resolution + (body.complete ? "；已补齐。" : "；可再次点击继续。");
      loadAlbums();
    }).catch(function (error) { backfillStatus.textContent = error.message; }).finally(function () { backfill.disabled = false; });
  });
  var timer = null;
  name.addEventListener("input", function () { window.clearTimeout(timer); timer = window.setTimeout(loadAlbums, 220); });
  window.addEventListener("popstate", handlePopState);
  window.addEventListener("hashchange", handleHashChange);

  var route = albumRoute();
  var hash = (window.location.hash || "").slice(1);
  if (route.hasAlbumParam && isManagementHash(hash)) {
    showManagement(hash, "replace");
  } else if (!route.hasAlbumParam && (routeParams().get("place_view") === "manage" || isManagementHash(hash))) {
    showManagement(managementPanelFromHash(hash), "replace");
  } else if (route.hasAlbumParam) {
    if (routeParams().get("place_view") === "manage") {
      var detailParams = routeParams(); detailParams.delete("place_view");
      window.history.replaceState(stateWith({ __placeManageReturn: false }), "", pageUrl(detailParams));
    }
    if (isPlaceKind(route.kind)) kind.value = route.kind;
    var initGen = showDetailLoading(route, "正在恢复地点相册…");
    detailCanBack = Boolean(window.history.state && window.history.state.__placeAlbumReturn);
    if (!isPlaceKind(route.kind) || !route.key) showDetailError(route, "地点链接缺少有效的类型或地点身份。", null);
    else {
      var initParams = new URLSearchParams({ kind: route.kind });
      get("/places/albums?" + initParams.toString()).then(function (body) {
        if (initGen !== detailGeneration) return;
        var album = (body.albums || []).find(function (item) { return String(item.kind) === String(route.kind) && String(item.key) === String(route.key); });
        if (!album) showDetailError(route, "此地点已不存在或当前没有有效照片。请返回地点相册列表重新选择。", null);
        else openAlbum(album, "none", detailCanBack);
      }).catch(function (error) {
        if (initGen === detailGeneration) showDetailError(route, "无法读取此地点相册：" + error.message, function () { openRoute(route, "replace", detailCanBack); });
      });
    }
  } else {
    switchView("list"); loadAlbums();
  }
})();
