/* Shared, read-only photo details for every photo viewer. */
(function () {
  "use strict";
  var API = "/api/v1";
  var sourceRoots = new Map();
  var serial = 0;
  var mounts = new WeakMap();
  var DETAIL_RETURN_KEY = "aisearch.photo-detail-return.v1";
  var DETAIL_RETURN_PATHS = { home: "/", search: "/search", persons: "/persons", places: "/places", map: "/map" };

  function detailReturnPath(value, source) {
    try {
      var url = new URL(String(value || ""), window.location.origin);
      if (url.origin !== window.location.origin || !DETAIL_RETURN_PATHS[source] || url.pathname !== DETAIL_RETURN_PATHS[source]) return null;
      return url.pathname + url.search + url.hash;
    } catch (_) { return null; }
  }
  function saveDetailReturn(assetId, source, returnPath, requiresBackstop) {
    var path = detailReturnPath(returnPath || window.location.href, source);
    if (path) {
      var handoffKey = String(Date.now()) + "-" + Math.random().toString(36).slice(2);
      try { window.sessionStorage.setItem(DETAIL_RETURN_KEY, JSON.stringify({ assetId: String(assetId), source: source, path: path, requiresBackstop: Boolean(requiresBackstop), handoffKey: handoffKey, savedAt: Date.now() })); }
      catch (_) { /* The detail route remains usable without a return snapshot. */ }
      window.__aiPhotoDetailHandoff = { assetId: String(assetId), handoffKey: handoffKey };
    }
    try { window.location.replace("/#/asset/" + encodeURIComponent(assetId) + "/details"); }
    catch (error) { try { window.sessionStorage.removeItem(DETAIL_RETURN_KEY); } catch (_) {} throw error; }
  }
  function consumeDetailReturn(assetId) {
    try {
      var handoff = window.__aiPhotoDetailHandoff;
      window.__aiPhotoDetailHandoff = null;
      var raw = window.sessionStorage.getItem(DETAIL_RETURN_KEY);
      window.sessionStorage.removeItem(DETAIL_RETURN_KEY);
      if (!raw) return null;
      var value = raw && JSON.parse(raw), path = value && detailReturnPath(value.path, value.source), savedAt = Number(value && value.savedAt);
      if (!value || String(value.assetId) !== String(assetId) || !path || !isFinite(savedAt) || savedAt > Date.now() + 60000 || Date.now() - savedAt > 30 * 60 * 1000) return { invalid: true };
      var sameDocumentHandoff = Boolean(handoff && handoff.assetId === String(assetId) && handoff.handoffKey === value.handoffKey);
      var referrerMatches = false;
      try {
        var referrer = new URL(document.referrer);
        var intendedSource = new URL(path, window.location.origin);
        referrerMatches = referrer.origin === window.location.origin && referrer.pathname === intendedSource.pathname && referrer.search === intendedSource.search;
      } catch (_) { /* Direct navigation has no trusted originating document. */ }
      if (!sameDocumentHandoff && !referrerMatches) return { invalid: true };
      return { source: value.source, path: path, requiresBackstop: Boolean(value.requiresBackstop) };
    } catch (_) { return { invalid: true }; }
  }
  function restoreDetailReturn(assetId, intent) {
    if (!intent || !DETAIL_RETURN_PATHS[intent.source]) return false;
    var path = detailReturnPath(intent.path, intent.source);
    if (!path) return false;
    try {
      var handoffKey = String(Date.now()) + "-" + Math.random().toString(36).slice(2);
      window.sessionStorage.setItem(DETAIL_RETURN_KEY, JSON.stringify({ assetId: String(assetId), source: intent.source, path: path, requiresBackstop: Boolean(intent.requiresBackstop), handoffKey: handoffKey, savedAt: Date.now() }));
      window.__aiPhotoDetailHandoff = { assetId: String(assetId), handoffKey: handoffKey };
      return true;
    } catch (_) { return false; }
  }

  function node(tag, text, className) {
    var item = document.createElement(tag);
    if (text !== undefined && text !== null) item.textContent = String(text);
    if (className) item.className = className;
    return item;
  }
  function request(path) {
    return fetch(API + path, { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } })
      .then(function (response) {
        return response.json().then(function (body) {
          if (!response.ok) { var error = new Error(body.message || body.code || "读取失败"); error.status = response.status; throw error; }
          return body;
        });
      });
  }
  function localAction(assetId, locationId, action, button, status, current) {
    if (button.disabled) return;
    var csrf = document.cookie.split(";").map(function (part) { return part.trim(); })
      .find(function (part) { return part.indexOf("ps_csrf=") === 0; });
    var headers = { Accept: "application/json", "Content-Type": "application/json" };
    if (csrf) headers["X-CSRF-Token"] = decodeURIComponent(csrf.slice(8));
    button.disabled = true;
    fetch(API + "/assets/" + encodeURIComponent(assetId) + "/local-action", {
      method: "POST", credentials: "same-origin", cache: "no-store", headers: headers,
      body: JSON.stringify({ action: action, location_id: Number(locationId) })
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (body) {
        if (!response.ok) throw new Error(body.message || body.code || "本机操作失败");
        return body;
      });
    }).then(function () {
      if (current()) status.textContent = action === "open" ? "已请求系统打开原图。" : "已请求资源管理器定位文件。";
    }).catch(function (error) {
      if (current()) status.textContent = error.message || "本机操作失败。";
    }).then(function () {
      if (current()) button.disabled = false;
    });
  }
  function safePath(root, relative) {
    if (!root || !relative) return null;
    var normalizedRoot = String(root).replace(/\//g, "\\");
    if (!/^[A-Za-z]:\\$/.test(normalizedRoot)) normalizedRoot = normalizedRoot.replace(/\\+$/, "");
    if (!/^[A-Za-z]:\\(?:.*)?$/.test(normalizedRoot) && !/^\\\\[^\\]+\\[^\\]+(?:\\.*)?$/.test(normalizedRoot)) return null;
    var rawRelative = String(relative);
    if (/^[\\/]/.test(rawRelative)) return null;
    var parts = rawRelative.replace(/\\/g, "/").split("/");
    if (parts.some(function (part) { return part === ".." || part === "." || /^[A-Za-z]:/.test(part); })) return null;
    return normalizedRoot + (normalizedRoot.endsWith("\\") ? "" : "\\") + parts.filter(Boolean).join("\\");
  }
  function fileName(location, assetId) {
    var value = location && location.relative_path;
    if (!value) return "照片 #" + assetId;
    var parts = String(value).split(/[\\/]/).filter(Boolean);
    return parts[parts.length - 1] || "照片 #" + assetId;
  }
  function bytes(value) {
    var amount = Number(value);
    if (!isFinite(amount) || amount < 0) return "";
    if (amount < 1024) return amount + " B";
    var units = ["KB", "MB", "GB", "TB"], index = -1;
    while (amount >= 1024 && index < units.length - 1) { amount /= 1024; index++; }
    return amount.toFixed(amount >= 100 ? 0 : amount >= 10 ? 1 : 2) + " " + units[index];
  }
  function addField(list, label, value) {
    if (value === null || value === undefined || value === "") return;
    list.append(node("dt", label)); list.append(node("dd", value));
  }
  function copyPath(path, button, status) {
    function failed() {
      var input = node("textarea", undefined, "photo-info-copy-fallback");
      input.value = path; input.setAttribute("aria-label", "选择并复制完整路径");
      button.after(input); input.focus(); input.select(); status.textContent = "自动复制失败，请手动复制选中的完整路径。";
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(path).then(function () { status.textContent = "完整路径已复制。"; }).catch(failed);
    } else failed();
  }
  function sourceRoot(sourceId) {
    var key = String(sourceId);
    if (!sourceRoots.has(key)) sourceRoots.set(key, request("/sources/" + encodeURIComponent(key)).then(function (body) { return body.root_path; }).catch(function (error) { sourceRoots.delete(key); throw error; }));
    return sourceRoots.get(key);
  }
  function disposeMap(state) {
    if (state.map) { try { state.map.remove(); } catch (_) {} state.map = null; }
  }
  function showMap(host, asset, assetId, state) {
    var block = node("section", undefined, "photo-info-map");
    block.append(node("h3", "拍摄位置"));
    var coords = node("p", "坐标：" + asset.latitude + ", " + asset.longitude, "hint"); block.append(coords);
    var canvas = node("div", undefined, "photo-info-map-canvas");
    canvas.setAttribute("aria-label", "照片 GPS 位置地图"); block.append(canvas); host.append(block);
    var url = document.createElement("a"); url.className = "nav-link"; url.textContent = "在地图相册中查看";
    url.href = "/map?asset_id=" + encodeURIComponent(assetId); block.append(url);
    function unavailable() {
      if (!state.active) return;
      canvas.textContent = "地图暂不可用；已知坐标：" + asset.latitude + ", " + asset.longitude;
      disposeMap(state);
    }
    function draw() {
      if (!state.active || !canvas.isConnected || !window.maplibregl) return;
      fetch("/api/v1/map/provider-runtime", { credentials: "same-origin", cache: "no-store" }).then(function (response) {
        return response.json().then(function (runtime) { if (!response.ok) throw new Error("runtime unavailable"); return runtime; });
      }).then(function (runtime) {
        if (!state.active || !canvas.isConnected) return;
        if (runtime.status !== "CONFIGURED" || !runtime.key) {
          var unavailableMessage = runtime.status === "NOT_CONFIGURED"
            ? "未配置天地图；已知坐标："
            : runtime.status === "CONFIG_UNREADABLE"
              ? "地图配置无法读取；已知坐标："
              : "地图服务暂不可用；已知坐标：";
          canvas.textContent = unavailableMessage + asset.latitude + ", " + asset.longitude;
          return;
        }
        var config = window.AISEARCH_MAP_CONFIG, layerType = runtime.default_layer === "imagery" ? "imagery" : "vector";
        var allSources = config.sourceTemplates(runtime.key), ids = config.layerIds(layerType);
        var sourceNames = layerType === "imagery" ? ["td-img", "td-cia"] : ["td-vec", "td-cva"];
        state.map = new window.maplibregl.Map({ container: canvas, center: [asset.longitude, asset.latitude], zoom: 12,
          interactive: false, attributionControl: { compact: true }, style: { version: 8, sources: {}, layers: [] } });
        state.map.on("load", function () {
          if (!state.active || !state.map) return;
          sourceNames.forEach(function (name) { state.map.addSource(name, allSources[name]); });
          ids.forEach(function (id, index) { state.map.addLayer({ id: id, type: "raster", source: sourceNames[index], minzoom: 0, maxzoom: 18 }); });
          state.map.addSource("photo-location", { type: "geojson", data: { type: "Feature", geometry: { type: "Point", coordinates: [asset.longitude, asset.latitude] }, properties: {} } });
          state.map.addLayer({ id: "photo-location", type: "circle", source: "photo-location", paint: { "circle-radius": 7, "circle-color": "#dc3545", "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });
          // Compact attribution starts expanded and normally collapses after a drag.
          // The detail map is intentionally non-interactive, so fire that public event once.
          state.map.fire("drag");
        });
        state.map.on("error", unavailable);
      }).catch(unavailable);
    }
    if (window.maplibregl) draw();
    else {
      var css = "/web/vendor/maplibre-4.7.1/dist/maplibre-gl.css";
      if (!document.querySelector('link[href="' + css + '"]')) { var linkTag = node("link"); linkTag.rel = "stylesheet"; linkTag.href = css; document.head.append(linkTag); }
      var script = document.querySelector('script[data-photo-map="true"]');
      if (!script) { script = node("script"); script.src = "/web/vendor/maplibre-4.7.1/dist/maplibre-gl.js"; script.dataset.photoMap = "true"; document.head.append(script); }
      script.addEventListener("load", draw, { once: true }); script.addEventListener("error", unavailable, { once: true });
    }
  }
  function renderPeople(host, assetId, state) {
    return request("/assets/" + encodeURIComponent(assetId) + "/faces").then(function (body) {
      if (!state.active) return;
      var byPerson = new Map();
      (body.faces || []).forEach(function (face) {
        if (face.assigned_person_id !== null && face.assigned_person_id !== undefined && !byPerson.has(String(face.assigned_person_id))) byPerson.set(String(face.assigned_person_id), face);
      });
      if (!byPerson.size) {
        host.textContent = body.has_more
          ? "前 200 张人脸中还没有已归属人物；还有更多人脸待检查。"
          : "这张照片里还没有已归属的人物。";
        return;
      }
      return Promise.all(Array.from(byPerson.entries()).map(function (entry) {
        return request("/persons/" + encodeURIComponent(entry[0])).then(function (person) {
          return { face: entry[1], person: person };
        }).catch(function () { return { face: entry[1], person: null }; });
      })).then(function (entries) {
        if (!state.active) return;
        var canonicalIds = new Set();
        entries.sort(function (a, b) {
          return Number((b.person || {}).asset_count || 0) - Number((a.person || {}).asset_count || 0)
            || Number((a.person || {}).canonical_person_id || (a.person || {}).person_id || a.face.assigned_person_id)
             - Number((b.person || {}).canonical_person_id || (b.person || {}).person_id || b.face.assigned_person_id);
        }).forEach(function (entry) {
          var face = entry.face, person = entry.person || {};
          var id = Number(person.canonical_person_id || person.person_id || face.assigned_person_id);
          if (!Number.isSafeInteger(id) || id < 1) return;
          if (canonicalIds.has(id)) return;
          canonicalIds.add(id);
          var label = person.name || face.assigned_person_name || "未命名的人";
          var avatar = /^[a-f0-9]{64}$/.test(String(person.avatar_face_id || ""))
            ? API + "/faces/" + person.avatar_face_id + "/thumbnail" : face.thumbnail_url;
          var button = node("button", undefined, "photo-info-person"); button.type = "button";
          button.setAttribute("aria-label", "查看" + label + "的照片");
          var image = node("img"); image.alt = ""; image.loading = "lazy"; image.src = avatar || "";
          image.addEventListener("error", function () { if (avatar !== face.thumbnail_url && face.thumbnail_url) { image.src = face.thumbnail_url; avatar = face.thumbnail_url; } else image.hidden = true; });
          button.append(image, node("span", label));
          button.addEventListener("click", function () {
            if (typeof state.onPerson === "function") { state.onPerson(id); return; }
            window.location.href = "/persons?person_id=" + encodeURIComponent(id) + "&from_photo=photo";
          });
          host.append(button);
        });
        if (body.has_more) host.append(node("p", "只检查了接口返回的人脸范围，人物按有效归属去重。", "hint"));
      });
    });
  }
  function render(host, assetId, options) {
    var prior = mounts.get(host);
    if (prior) prior.dispose();
    var state = { active: true, map: null, seq: ++serial, onPerson: options && options.onPerson };
    var seq = state.seq;
    var disclosure = node("details", undefined, "photo-info-details");
    disclosure.open = true;
    var summary = node("summary", "照片信息");
    summary.className = "photo-info-summary";
    var content = node("div", undefined, "photo-info-content");
    disclosure.append(summary, content);
    host.replaceChildren(disclosure);
    content.append(node("p", "正在读取照片信息…", "hint"));
    function current() { return state.active && seq === state.seq && host.isConnected; }
    request("/assets/" + encodeURIComponent(assetId)).then(function (detail) {
      if (!current()) return;
      if (options && typeof options.onDetail === "function") options.onDetail(detail);
      content.replaceChildren();
      var title = node("h3", fileName((detail.locations || []).filter(function (x) { return x.availability === "PRESENT"; })[0] || (detail.locations || [])[0], assetId)); content.append(title);
      var list = node("dl", undefined, "photo-info-facts");
      addField(list, "分辨率", detail.width && detail.height ? detail.width + " × " + detail.height + " 像素" : null);
      addField(list, "格式", detail.format ? String(detail.format).toUpperCase() : null);
      addField(list, "大小", bytes(detail.byte_size));
      var taken = detail.taken_time || null;
      addField(list, "时间", taken && taken.taken_at ? (taken.label === "capture" ? "拍摄时间 " : "文件时间 ") + taken.taken_at.replace("T", " ") : null);
      addField(list, "相机", taken && taken.camera);
      content.append(list);
      var pathSection = node("section", undefined, "photo-info-paths"); pathSection.append(node("h3", "文件位置"));
      var locations = (detail.locations || []).slice().sort(function (a, b) { return (a.availability === "PRESENT" ? 0 : 1) - (b.availability === "PRESENT" ? 0 : 1); });
      if (!locations.length) pathSection.append(node("p", "没有登记文件位置。", "hint"));
      var status = node("p", "", "hint");
      locations.forEach(function (location) {
        var row = node("div", undefined, "photo-info-location");
        row.append(node("strong", (location.source_display_name || ("来源 #" + location.source_id)) + " · " + (location.availability === "PRESENT" ? "在库" : "不可用")));
        var relative = node("p", location.relative_path || "（无相对路径）", "photo-info-relative"); row.append(relative);
        if (location.availability === "PRESENT") {
          var openOriginal = node("button", "系统打开原图", "photo-info-local-action"); openOriginal.type = "button";
          openOriginal.addEventListener("click", function () {
            localAction(assetId, location.location_id, "open", openOriginal, status, current);
          });
          var revealFile = node("button", "定位文件位置", "photo-info-local-action"); revealFile.type = "button";
          revealFile.addEventListener("click", function () {
            localAction(assetId, location.location_id, "reveal", revealFile, status, current);
          });
          row.append(openOriginal, revealFile);
        }
        sourceRoot(location.source_id).then(function (root) {
          if (!current()) return;
          var complete = safePath(root, location.relative_path);
          if (!complete) { row.append(node("p", "来源路径暂不可用；以上为相对路径。", "hint")); return; }
          var path = node("p", complete, "photo-info-full-path"); path.tabIndex = 0; row.append(path);
          var copy = node("button", "复制完整路径"); copy.type = "button"; copy.addEventListener("click", function () { copyPath(complete, copy, status); }); row.append(copy);
        }).catch(function () { if (current()) row.append(node("p", "完整路径暂不可用；以上为相对路径。", "hint")); });
        pathSection.append(row);
      });
      pathSection.append(status); content.append(pathSection);
      if (!(options && options.people === false)) {
        var people = node("section", undefined, "photo-info-people"); people.append(node("h3", "照片中的人物"));
        var peopleRows = node("div", undefined, "photo-info-people-list"); people.append(peopleRows); content.append(people);
        var correctPeople = node("a", "更正这张照片中的人脸");
        correctPeople.href = "/persons?asset_id=" + encodeURIComponent(assetId); people.append(correctPeople);
        var reviewNote = node("div"); people.append(reviewNote);
        var reviewRequestSeq = 0;
        function loadReviewNote() {
          var reviewSeq = ++reviewRequestSeq;
          reviewNote.textContent = "";
          request("/assets/" + encodeURIComponent(assetId) + "/face-review?limit=1").then(function (page) {
            if (!current() || reviewSeq !== reviewRequestSeq) return;
            if ((page.groups || []).length) {
              var review = node("a", "这张照片有识别更新待确认");
              review.href = "/persons?asset_id=" + encodeURIComponent(assetId) + "#panel-mappings"; reviewNote.append(review);
            }
          }).catch(function () {
            if (!current() || reviewSeq !== reviewRequestSeq) return;
            var retry = node("button", "重试读取识别更新提示"); retry.type = "button";
            retry.addEventListener("click", loadReviewNote); reviewNote.append(retry);
          });
        }
        loadReviewNote();
        renderPeople(peopleRows, assetId, state).catch(function () { if (current()) peopleRows.replaceChildren(node("p", "人物信息暂时无法读取。", "hint")); });
      }
      request("/map/assets?asset_id=" + encodeURIComponent(assetId) + "&limit=1").then(function (members) {
        if (!current() || !members || members.total !== 1) return;
        return request("/assets/" + encodeURIComponent(assetId) + "/geo").then(function (geo) {
          if (!current()) return;
          var point = geo.normalized_wgs84;
          if (geo.status !== "VALID" || geo.input_revision !== detail.sha256 || !["WGS84", "ASSUMED_WGS84"].includes(geo.datum_status) || !point) return;
          var lat = Number(point.latitude), lon = Number(point.longitude);
          if (!isFinite(lat) || !isFinite(lon) || lat < -90 || lat > 90 || lon < -180 || lon > 180) return;
          showMap(content, { latitude: lat, longitude: lon }, assetId, state);
        });
      }).catch(function () { if (current()) content.append(node("p", "GPS 信息暂时无法读取。", "hint")); });
    }).catch(function (error) { if (current()) content.replaceChildren(node("p", "照片信息暂时无法读取。" + (error.status === 401 ? "请重新打开本机应用。" : ""), "error")); });
    var mount = { dispose: function () {
      state.active = false; state.seq++; disposeMap(state);
      if (mounts.get(host) === mount) mounts.delete(host);
    } };
    mounts.set(host, mount);
    return mount;
  }
  function open(assetId, options) {
    options = options || {};
    var dialog = node("dialog", undefined, "photo-viewer-dialog");
    var heading = node("header", undefined, "photo-viewer-heading");
    var title = node("h2", options.title || "查看照片"); title.id = "photo-viewer-title-" + (++serial);
    heading.append(title);
    var headingActions = node("div", undefined, "photo-viewer-heading-actions");
    var fullDetails = node("button", "完整详情", "photo-viewer-details"); fullDetails.type = "button";
    fullDetails.setAttribute("aria-label", "打开当前照片的完整详情");
    headingActions.append(fullDetails);
    var close = node("button", "关闭", "photo-viewer-close"); close.type = "button";
    close.setAttribute("aria-label", "关闭照片预览"); headingActions.append(close); heading.append(headingActions);
    var layout = node("div", undefined, "photo-viewer-layout");
    var figure = node("figure", undefined, "photo-viewer-figure");
    var stage = node("div", undefined, "photo-viewer-stage");
    var image = node("img"); image.alt = options.alt || "照片大图";
    var imageNote = node("figcaption", "", "photo-viewer-image-note"); imageNote.setAttribute("role", "status");
    stage.append(image); figure.append(stage, imageNote);
    var controls = node("div", undefined, "photo-viewer-controls");
    var zoomOut = node("button", "−"); zoomOut.type = "button"; zoomOut.setAttribute("aria-label", "缩小预览"); zoomOut.disabled = true;
    var fit = node("button", "适合窗口"); fit.type = "button"; fit.disabled = true;
    var actual = node("button", "预览 100%"); actual.type = "button"; actual.disabled = true;
    var zoomIn = node("button", "+"); zoomIn.type = "button"; zoomIn.setAttribute("aria-label", "放大预览"); zoomIn.disabled = true;
    var zoomLabel = node("span", "读取尺寸中…", "photo-viewer-zoom-label"); zoomLabel.setAttribute("role", "status");
    controls.append(zoomOut, fit, actual, zoomIn, zoomLabel);
    if (options.navigation) {
      var previous = node("button", "← 上一张"); previous.type = "button";
      var position = node("span", "", "photo-viewer-position"); position.setAttribute("role", "status");
      var next = node("button", "下一张 →"); next.type = "button";
      controls.append(previous, position, next);
    }
    figure.append(controls); layout.append(figure);
    var panel = node("aside", undefined, "photo-info-panel"); layout.append(panel);
    var extension = node("section", undefined, "photo-viewer-extension");
    if (options.extension) extension.append(options.extension);
    dialog.setAttribute("aria-labelledby", title.id);
    dialog.append(heading, layout, extension); document.body.append(dialog);
    var mounted = null, generation = 0, currentAssetId = null, historyOwned = false, closingFromHistory = false, navigatingToDetails = false;
    var historyKey = "photo-viewer-" + serial;
    var preview = { image: image, naturalWidth: 0, naturalHeight: 0, scale: 1, mode: "fit", x: 0, y: 0 };
    function applyScale(value, anchorX, anchorY) {
      if (!preview.naturalWidth || !preview.naturalHeight) return;
      var old = preview.scale || 1, scale = Math.max(0.1, Math.min(8, Number(value)));
      if (!isFinite(scale)) scale = old;
      if (anchorX !== undefined && anchorY !== undefined) {
        preview.x = anchorX - (anchorX - preview.x) * scale / old;
        preview.y = anchorY - (anchorY - preview.y) * scale / old;
      }
      preview.scale = scale;
      image.style.width = Math.round(preview.naturalWidth * scale) + "px";
      image.style.height = Math.round(preview.naturalHeight * scale) + "px";
      image.style.transform = "translate(" + Math.round(preview.x) + "px," + Math.round(preview.y) + "px)";
      zoomLabel.textContent = Math.round(scale * 100) + "%";
      zoomOut.disabled = scale <= 0.1; zoomIn.disabled = scale >= 8;
      clampPan();
    }
    function clampPan() {
      if (!preview.naturalWidth || !preview.naturalHeight || !stage.clientWidth || !stage.clientHeight) return;
      var width = preview.naturalWidth * preview.scale, height = preview.naturalHeight * preview.scale;
      var maxX = Math.max(0, (width - stage.clientWidth) / 2), maxY = Math.max(0, (height - stage.clientHeight) / 2);
      preview.x = Math.max(-maxX, Math.min(maxX, preview.x)); preview.y = Math.max(-maxY, Math.min(maxY, preview.y));
      image.style.transform = "translate(" + Math.round(preview.x) + "px," + Math.round(preview.y) + "px)";
    }
    function fitPreview() {
      if (!preview.naturalWidth || !preview.naturalHeight) return;
      preview.mode = "fit"; preview.x = 0; preview.y = 0;
      var width = stage.clientWidth || preview.naturalWidth, height = stage.clientHeight || preview.naturalHeight;
      applyScale(Math.min(1, width / preview.naturalWidth, height / preview.naturalHeight));
    }
    zoomOut.addEventListener("click", function () { preview.mode = "zoom"; applyScale(preview.scale / 1.25); });
    zoomIn.addEventListener("click", function () { preview.mode = "zoom"; applyScale(preview.scale * 1.25); });
    fit.addEventListener("click", fitPreview);
    actual.addEventListener("click", function () { preview.mode = "zoom"; preview.x = 0; preview.y = 0; applyScale(1); });
    stage.addEventListener("dblclick", function (event) { if (event.target !== image) return; if (preview.mode === "fit") { preview.mode = "zoom"; applyScale(1); } else fitPreview(); });
    stage.addEventListener("wheel", function (event) {
      if (!event.ctrlKey || !preview.naturalWidth || event.target !== image) return;
      event.preventDefault(); var rect = stage.getBoundingClientRect();
      preview.mode = "zoom"; applyScale(preview.scale * (event.deltaY < 0 ? 1.25 : 1 / 1.25), event.clientX - rect.left - rect.width / 2, event.clientY - rect.top - rect.height / 2);
    }, { passive: false });
    var dragging = null;
    stage.addEventListener("pointerdown", function (event) {
      if (event.target !== image || !preview.naturalWidth || (preview.naturalWidth * preview.scale <= stage.clientWidth && preview.naturalHeight * preview.scale <= stage.clientHeight)) return;
      dragging = { id: event.pointerId, x: event.clientX, y: event.clientY, baseX: preview.x, baseY: preview.y };
      if (stage.setPointerCapture) stage.setPointerCapture(event.pointerId);
    });
    stage.addEventListener("pointermove", function (event) { if (dragging && dragging.id === event.pointerId) { preview.x = dragging.baseX + event.clientX - dragging.x; preview.y = dragging.baseY + event.clientY - dragging.y; clampPan(); } });
    ["pointerup", "pointercancel", "lostpointercapture"].forEach(function (name) { stage.addEventListener(name, function () { dragging = null; }); });
    var onWindowResize = function () { if (dialog.open && preview.mode === "fit") fitPreview(); };
    if (window.addEventListener) window.addEventListener("resize", onWindowResize);
    function setAsset(nextId, nextOptions) {
      generation++; var thisGeneration = generation;
      currentAssetId = String(nextId); preview.naturalWidth = 0; preview.naturalHeight = 0; preview.scale = 1; preview.mode = "fit"; preview.x = 0; preview.y = 0;
      [zoomOut, fit, actual, zoomIn].forEach(function (button) { button.disabled = true; }); zoomLabel.textContent = "读取尺寸中…";
      imageNote.textContent = "正在加载照片预览…"; image.alt = options.alt || "照片大图"; image.removeAttribute("src");
      if (mounted) mounted.dispose();
      panel.replaceChildren();
      var renderOptions = Object.assign({}, options, nextOptions || {}), callerOnDetail = renderOptions.onDetail;
      renderOptions.onDetail = function (detail) {
        var locations = (detail.locations || []).slice().sort(function (a, b) { return (a.availability === "PRESENT" ? 0 : 1) - (b.availability === "PRESENT" ? 0 : 1); });
        title.textContent = fileName(locations[0], currentAssetId);
        if (callerOnDetail) callerOnDetail(detail);
      };
      mounted = render(panel, currentAssetId, renderOptions);
      var previewUrl = (nextOptions && nextOptions.imageUrl) || options.imageUrl || API + "/assets/" + encodeURIComponent(currentAssetId) + "/preview";
      var thumbnailUrl = API + "/assets/" + encodeURIComponent(currentAssetId) + "/thumbnail", thumbnailTried = false;
      image.onerror = function () {
        if (thisGeneration !== generation) return;
        if (!thumbnailTried) { thumbnailTried = true; imageNote.textContent = "大图暂不可用，正在尝试缩略图……"; image.src = thumbnailUrl; return; }
        imageNote.textContent = "照片预览和缩略图暂不可用。"; image.alt = "照片暂不可用"; zoomLabel.textContent = "预览不可用";
      };
      image.onload = function () {
        if (thisGeneration !== generation) return;
        imageNote.textContent = thumbnailTried ? "原图暂不可用，当前显示缩略图。" : "";
        preview.naturalWidth = image.naturalWidth || 0; preview.naturalHeight = image.naturalHeight || 0;
        if (!preview.naturalWidth || !preview.naturalHeight) return;
        [zoomOut, fit, actual, zoomIn].forEach(function (button) { button.disabled = false; }); fitPreview();
      };
      image.src = previewUrl;
      if (typeof options.onAssetChange === "function") options.onAssetChange(currentAssetId, handle);
    }
    function canClose() { return typeof options.beforeClose !== "function" || options.beforeClose(currentAssetId, handle) !== false; }
    function dismiss() { if (dialog.open && canClose()) dialog.close(); }
    fullDetails.disabled = typeof options.onOpenFullDetails !== "function";
    fullDetails.addEventListener("click", function () {
      if (navigatingToDetails || !dialog.open || !canClose() || typeof options.onOpenFullDetails !== "function") return;
      navigatingToDetails = true;
      fullDetails.disabled = true;
      if (historyOwned && window.removeEventListener) { window.removeEventListener("popstate", onPopState); historyOwned = false; }
      try {
        options.onOpenFullDetails(currentAssetId, handle);
        if (dialog.open) dialog.close();
      }
      catch (error) {
        navigatingToDetails = false; fullDetails.disabled = false;
        if (options.history !== false && window.history && window.history.pushState && window.addEventListener) {
          historyOwned = true; window.addEventListener("popstate", onPopState);
        }
        if (typeof options.onDetailNavigationError === "function") options.onDetailNavigationError(error);
      }
    });
    var onPopState = function (event) {
      if (!historyOwned || !dialog.open) return;
      if (!canClose()) { window.history.pushState({ photoViewerKey: historyKey }, "", window.location.href); return; }
      closingFromHistory = true; dismiss();
    };
    var handle = { dialog: dialog, image: image, panel: panel, extension: extension, controls: controls, title: title,
      close: dismiss, setAsset: setAsset, setPosition: function (value) { if (position) position.textContent = value || ""; },
      setNavigationEnabled: function (previousEnabled, nextEnabled) { if (!options.navigation) return; controls.children[5].disabled = !previousEnabled; controls.children[7].disabled = !nextEnabled; },
      setNote: function (value) { imageNote.textContent = value || ""; } };
    if (options.navigation) { controls.children[5].addEventListener("click", function () { if (options.navigation.previous) options.navigation.previous(currentAssetId, handle); }); controls.children[7].addEventListener("click", function () { if (options.navigation.next) options.navigation.next(currentAssetId, handle); }); }
    if (typeof ResizeObserver !== "undefined") { var observer = new ResizeObserver(function () { if (preview.mode === "fit" && dialog.open) fitPreview(); }); observer.observe(stage); observer.observe(panel); dialog.addEventListener("close", function () { observer.disconnect(); }); }
    close.addEventListener("click", dismiss);
    dialog.addEventListener("cancel", function (event) { if (!canClose()) event.preventDefault(); });
    dialog.addEventListener("keydown", function (event) {
      if ((event.key !== "ArrowLeft" && event.key !== "ArrowRight") || !options.navigation) return;
      var target = event.target;
      if (target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName || ""))) return;
      if (document.querySelector && document.querySelector("dialog[open]") !== dialog) return;
      event.preventDefault();
      var action = event.key === "ArrowLeft" ? options.navigation.previous : options.navigation.next;
      if (action) action(currentAssetId, handle);
    });
    dialog.addEventListener("close", function () {
      generation++;
      if (window.removeEventListener) { window.removeEventListener("resize", onWindowResize); if (historyOwned) window.removeEventListener("popstate", onPopState); }
      if (historyOwned && !closingFromHistory && window.history.back) window.history.back();
      if (mounted) mounted.dispose(); image.removeAttribute("src"); dialog.remove();
      if (options.onClose) options.onClose(currentAssetId, handle);
      if (options.returnFocus && options.returnFocus.isConnected) options.returnFocus.focus();
    });
    dialog.addEventListener("click", function (event) { if (event.target === dialog) dismiss(); });
    dialog.addEventListener("keydown", function (event) { if (event.key === "Escape" && dialog.open) { event.preventDefault(); dismiss(); } });
    dialog.showModal();
    if (options.history !== false && window.history && window.history.pushState && window.addEventListener) {
      window.history.pushState({ photoViewerKey: historyKey }, "", window.location.href);
      historyOwned = true; window.addEventListener("popstate", onPopState);
    }
    close.focus();
    setAsset(assetId);
    return handle;
  }
  window.AIPhotoInfo = { render: render, open: open, navigateToDetails: saveDetailReturn, consumeDetailReturn: consumeDetailReturn, restoreDetailReturn: restoreDetailReturn };
})();
