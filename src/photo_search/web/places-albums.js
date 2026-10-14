(function () {
  "use strict";
  var api = "/api/v1";
  var kind = document.getElementById("album-kind");
  var name = document.getElementById("album-name");
  var cards = document.getElementById("album-cards");
  var photos = document.getElementById("album-photos");
  var currentTitle = document.getElementById("album-current-title");
  var backfill = document.getElementById("album-backfill");
  var backfillStatus = document.getElementById("album-backfill-status");
  var current = null;
  var timeline = null;
  var albumRequest = 0;
  var photoVersion = 0;
  var initialKind = null;
  var initialKey = null;
  var cnCapabilityStarted = false;
  function el(tag, cls, value) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (value !== undefined) node.textContent = String(value);
    return node;
  }
  function searchThisAlbum(scope) {
    var token = window.crypto && window.crypto.randomUUID
      ? window.crypto.randomUUID()
      : Date.now().toString(36) + "-" + Math.random().toString(36).slice(2) + "-" + Math.random().toString(36).slice(2);
    try { window.sessionStorage.setItem("aisearch.search-scope.v1:" + token, JSON.stringify(scope)); }
    catch (_error) { return; }
    window.location.href = "/search?scope_entry=" + encodeURIComponent(token);
  }
  function get(url) {
    return fetch(api + url, { credentials: "same-origin", headers: { Accept: "application/json" } }).then(function (r) {
      return r.json().then(function (body) { if (!r.ok) throw new Error(body.message || body.code || "读取失败"); return body; });
    });
  }
  function loadAlbums() {
    var request = ++albumRequest;
    if (current && current.kind !== kind.value) {
      current = null; photoVersion++;
      if (timeline) { timeline.dispose(); timeline = null; }
      photos.hidden = true; currentTitle.hidden = true; cards.hidden = false;
    }
    cards.replaceChildren(el("p", "hint", "正在整理地点相册…"));
    var params = new URLSearchParams({ kind: kind.value });
    if (name.value.trim()) params.set("name", name.value.trim());
    get("/places/albums?" + params.toString()).then(function (body) {
      if (request !== albumRequest) return;
      cards.replaceChildren();
      loadCnCapability();
      if (!body.albums.length) { cards.appendChild(el("p", "hint", kind.value === "province" ? "暂无省份索引或暂无照片。旧地名包仍可浏览城市和国家。" : "当前没有可浏览的地点照片。")); return; }
      body.albums.forEach(function (album, index) {
        var button = el("button", "album-card"); button.type = "button";
        var image = el("img", "album-cover"); image.loading = index < 4 ? "eager" : "lazy"; image.alt = "";
        if ("fetchPriority" in image) image.fetchPriority = index < 4 ? "high" : "auto";
        image.src = api + "/assets/" + encodeURIComponent(album.cover_asset_id) + "/thumbnail";
        button.appendChild(image);
        var title = el("strong", "album-title", album.name); button.appendChild(title);
        button.appendChild(el("span", "album-parent", album.parent || ""));
        if (album.origin === "GPS_NEARBY_CITY" || album.origin === "MIXED" || album.origin === "GPS_ADMIN") button.appendChild(el("span", "album-origin", album.origin === "MIXED" ? "含机器地点与人工归类" : album.origin === "GPS_ADMIN" ? "GPS行政区 · 机器解析" : "GPS附近城市"));
        button.appendChild(el("span", "album-total", album.total + " 张照片"));
        button.addEventListener("click", function () { openAlbum(album); }); cards.appendChild(button);
        if (initialKind === kind.value && initialKey && String(album.key) === initialKey) {
          initialKey = null;
          openAlbum(album);
        }
      });
    }).catch(function (error) { if (request === albumRequest) cards.replaceChildren(el("p", "error", error.message)); });
  }
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
  function openAlbum(album) {
    photoVersion++; current = album;
    if (timeline) timeline.dispose();
    currentTitle.replaceChildren(); currentTitle.hidden = false; cards.hidden = true; photos.hidden = false;
    var back = el("button", "album-back", "返回地点目录"); back.type = "button";
    back.addEventListener("click", function () { if (timeline) { timeline.dispose(); timeline = null; } current = null; photos.hidden = true; currentTitle.hidden = true; cards.hidden = false; });
    var heading = el("div", "album-current-title__heading");
    heading.appendChild(el("h3", null, album.name));
    heading.appendChild(el("span", "album-parent", album.parent || ""));
    heading.appendChild(el("span", "album-total", album.total + " 张有效照片"));
    var search = el("button", "album-search", "搜索此地点"); search.type = "button";
    search.addEventListener("click", function () {
      searchThisAlbum({ kind: "place", place_kind: album.kind, place_key: String(album.key),
        name: String(album.name || "地点"), parent: String(album.parent || "") });
    });
    currentTitle.append(back, heading, search);
    timeline = window.PhotoTimeline.mount({
      host: photos,
      scope: { kind: "place", place_kind: album.kind, place_key: String(album.key) },
      adapter: {
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
          tools.appendChild(el("summary", null, "更正地点"));
          var input = el("input"); input.type = "text"; input.maxLength = 200;
          input.placeholder = "地点名；留空取消归类；* 恢复自动";
          input.setAttribute("aria-label", "更正地点名称"); tools.appendChild(input);
          var fix = el("button", null, "查找可选地点"); fix.type = "button";
          fix.addEventListener("click", function () { correct(item.asset_id, input.value.trim(), tools); });
          tools.appendChild(fix); card.appendChild(tools);
          return card;
        }
      }
    });
    currentTitle.scrollIntoView({ behavior: "smooth", block: "start" });
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
  function write(assetId, action, placeRef) {
    return get("/assets/" + assetId + "/place").then(function (state) {
      var assignment = state.assignment || {};
      var payload = { action: action, expected_revision: Number(assignment.revision || 0), request_id: "album-" + Date.now() + "-" + Math.random().toString(16).slice(2) };
      if (action === "assign") payload.target_place_ref = placeRef;
      return fetch(api + "/assets/" + assetId + "/place-assignment", { method: "PUT", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf() }, body: JSON.stringify(payload) }).then(function (r) { return r.json().then(function (b) { if (!r.ok) throw new Error(b.message || b.code || "保存失败"); return b; }); });
    });
  }
  function reloadCurrentAlbum() {
    loadAlbums();
    if (!current) return;
    get("/places/albums?" + new URLSearchParams({ kind: current.kind })).then(function (body) {
      var updated = (body.albums || []).find(function (item) { return String(item.key) === String(current.key); });
      if (updated) openAlbum(updated); else { if (timeline) { timeline.dispose(); timeline = null; } photos.hidden = true; currentTitle.hidden = true; cards.hidden = false; }
    }).catch(function () {});
  }
  function correct(assetId, choice, holder) {
    if (choice.trim() === "*") { write(assetId, "restore_auto", null).then(reloadCurrentAlbum).catch(function (e) { window.alert(e.message); }); return; }
    if (!choice.trim()) { write(assetId, "unassign", null).then(reloadCurrentAlbum).catch(function (e) { window.alert(e.message); }); return; }
    get("/places/choices?" + new URLSearchParams({ name: choice.trim() }).toString()).then(function (body) {
      var exact = (body.choices || []).filter(function (place) {
        var name = choice.trim().normalize("NFKC").toLocaleLowerCase();
        return place.name.normalize("NFKC").toLocaleLowerCase() === name || (place.aliases || []).some(function (a) { return a.normalize("NFKC").toLocaleLowerCase() === name; });
      });
      holder.querySelectorAll(".place-choice-list").forEach(function (item) { item.remove(); });
      if (!exact.length) throw new Error("当前有效地点目录中没有这个名称，无法新建或指定任意文本。");
      var list = el("div", "place-choice-list");
      list.appendChild(el("small", "hint", "请选择地点范围与来源："));
      exact.forEach(function (place) {
        var button = el("button", null, place.name + (place.parent ? " · " + place.parent : "") + "（" + place.kind + " · " + place.source + "）");
        button.type = "button";
        button.addEventListener("click", function () {
          button.disabled = true;
          write(assetId, "assign", place.selection_ref).then(reloadCurrentAlbum).catch(function (e) { button.disabled = false; window.alert(e.message); });
        });
        list.appendChild(button);
      });
      holder.appendChild(list);
    }).catch(function (e) { window.alert(e.message); });
  }
  kind.addEventListener("change", loadAlbums);
  var cnUpdate = document.getElementById("cn-update");
  var cnPause = document.getElementById("cn-pause");
  var cnStatus = document.getElementById("cn-update-status");
  var cnCapability = null;
  var cnStop = false;
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
  var timer = null; name.addEventListener("input", function () { window.clearTimeout(timer); timer = window.setTimeout(loadAlbums, 220); });
  var params = new URLSearchParams(window.location.search);
  initialKind = params.get("album_kind");
  initialKey = params.get("album_key");
  if (initialKind && initialKey) {
    kind.value = initialKind;
  }
  loadAlbums();
})();
