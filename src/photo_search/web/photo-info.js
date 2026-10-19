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
  function writeRequest(path, method, body) {
    var csrf = document.cookie.split(";").map(function (part) { return part.trim(); })
      .find(function (part) { return part.indexOf("ps_csrf=") === 0; });
    var headers = { Accept: "application/json", "Content-Type": "application/json" };
    if (csrf) headers["X-CSRF-Token"] = decodeURIComponent(csrf.slice(8));
    return fetch(API + path, { method: method, credentials: "same-origin", cache: "no-store", headers: headers, body: JSON.stringify(body) })
      .then(function (response) { return response.json().catch(function () { return {}; }).then(function (value) {
        if (!response.ok) { var error = new Error(value.message || value.code || "操作未完成"); error.status = response.status; error.code = value.code; throw error; }
        return value;
      }); });
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
  function collapseMapAttribution(canvas) {
    var control = canvas.querySelector && canvas.querySelector(".maplibregl-ctrl-attrib");
    if (!control) return;
    control.classList.remove("maplibregl-compact-show");
    control.open = false;
    control.removeAttribute("open");
    var summary = control.querySelector && control.querySelector(".maplibregl-ctrl-attrib-button");
    if (summary) summary.setAttribute("aria-expanded", "false");
  }
  function showMap(host, asset, assetId, state) {
    var block = node("section", undefined, "photo-info-map");
    block.addEventListener("click", function (event) {
      var target = event.target;
      var summary = target && typeof target.closest === "function" && target.closest(".maplibregl-ctrl-attrib-button");
      if (!summary) return;
      block.classList.toggle("photo-info-map-attribution-open");
      var control = canvas.querySelector && canvas.querySelector(".maplibregl-ctrl-attrib");
      var expanded = block.classList.contains("photo-info-map-attribution-open");
      if (control) {
        control.open = expanded;
        if (expanded) control.setAttribute("open", "");
        else control.removeAttribute("open");
      }
      summary.setAttribute("aria-expanded", String(expanded));
      if (event.preventDefault) event.preventDefault();
    });
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
          // Compact attribution starts open; this detail map cannot be dragged to dismiss it.
          collapseMapAttribution(canvas);
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
  function faceRequestId() { return "photo-face-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2); }
  function renderFaceManager(section, assetId, state, body, reload) {
    var toggle = node("button", "管理人脸", "photo-face-manager-toggle"); toggle.type = "button";
    toggle.setAttribute("aria-expanded", "false");
    var panel = node("div", undefined, "photo-face-manager"); panel.hidden = true;
    var status = node("p", "", "hint"); status.setAttribute("role", "status");
    var refresh = node("button", "刷新当前照片人脸"); refresh.type = "button";
    var stale = false;
    function writeFace(path, method, value) {
      return writeRequest(path, method, value).then(function (result) {
        // The write is confirmed even if the viewer was closed while it ran.
        // Keep refresh failures separate from an unknown write result.
        if (typeof state.onFacesChanged === "function") {
          Promise.resolve().then(function () { return state.onFacesChanged(Number(assetId), result); })
            .catch(function () {
              if (!state.active) return;
              if (!state.faceRefreshWarning) {
                state.faceRefreshWarning = node("p", "", "hint"); state.faceRefreshWarning.setAttribute("role", "status");
                section.append(state.faceRefreshWarning);
              }
              state.faceRefreshWarning.textContent = "人脸已保存；人物列表刷新失败，请刷新列表查看，不要重复提交。";
            });
        } else {
          try { window.localStorage.setItem("aisearch.persons.changed", faceRequestId()); }
          catch (error) { /* Other pages still refresh when explicitly reopened. */ }
        }
        return result;
      });
    }
    function setStale(message) {
      stale = true; panel.dataset.stale = "true"; status.textContent = message;
      panel.querySelectorAll("button, input").forEach(function (control) { control.disabled = true; });
      refresh.disabled = false;
    }
    refresh.addEventListener("click", function () { refresh.disabled = true; reload().catch(function () { refresh.disabled = false; status.textContent = "刷新失败，请稍后重试。"; }); });
    toggle.addEventListener("click", function () {
      panel.hidden = !panel.hidden; toggle.setAttribute("aria-expanded", String(!panel.hidden));
      if (panel.hidden || panel.dataset.rendered === "true") return;
      panel.dataset.rendered = "true";
      var faces = (body.faces || []).slice();
      var assigned = faces.filter(function (face) { return face.assigned_person_id !== null && face.assigned_person_id !== undefined; });
      var pending = faces.filter(function (face) { return face.assigned_person_id === null || face.assigned_person_id === undefined; }).filter(function (face) { return !face.ignore_state; });
      var ignored = faces.filter(function (face) {
        return (face.assigned_person_id === null || face.assigned_person_id === undefined)
          && (face.ignore_state === "TEMPORARY" || face.ignore_state === "EXCLUDED");
      });
      var counts = node("p", "已检测 " + faces.length + " 张脸 · 已归属 " + assigned.length + " · 待整理 " + pending.length + " · 已忽略 " + ignored.length, "photo-face-counts");
      if (body.has_more) counts.append(node("span", "（当前已返回前 200 张脸）", "hint"));
      panel.append(counts, refresh);
      var selected = new Set();
      var batch = node("div", undefined, "photo-face-batch");
      var batchToggle = node("button", "批量整理", "photo-face-batch-toggle"); batchToggle.type = "button";
      batch.append(batchToggle);
      var batchActions = node("div", undefined, "photo-face-batch-actions"); batchActions.hidden = true;
      var temporary = node("button", "暂时忽略所选"); temporary.type = "button";
      var excluded = node("button", "明确排除所选"); excluded.type = "button";
      var clear = node("button", "清空"); clear.type = "button";
      batchActions.append(temporary, excluded, clear); batch.append(batchActions); panel.append(batch);
      var batchMode = false;
      batchToggle.setAttribute("aria-expanded", "false");
      batchToggle.addEventListener("click", function () {
        batchMode = !batchMode;
        batchActions.hidden = !batchMode;
        batchToggle.setAttribute("aria-expanded", String(batchMode));
        batchToggle.textContent = batchMode ? "取消批量整理" : "批量整理";
        if (!batchMode) selected.clear();
        panel.querySelectorAll(".photo-face-select").forEach(function (box) { box.hidden = !batchMode; box.checked = batchMode && selected.has(box.dataset.faceId); });
      });
      function applyVisibility(ids, hidden, ignoreState) {
        if (!ids.length || !state.active || stale) return;
        temporary.disabled = excluded.disabled = true; status.textContent = "正在保存人脸状态……";
        writeFace("/faces/inbox/visibility", "POST", { face_ids: ids, hidden: hidden, ignore_state: ignoreState })
          .then(function () { if (!state.active) return; status.textContent = "状态已保存。"; return reload(); })
          .catch(function (error) { if (state.active) {
            if (error.status >= 500 || !error.status) setStale("保存结果暂时无法确认。请先刷新当前照片状态，再决定是否重试。");
            else status.textContent = error.status === 409 ? "人脸状态已变化，请刷新后重试。" : error.status === 422 ? "此人脸当前不符合该操作条件。" : error.message || "操作未完成。";
          } })
          .then(function () { temporary.disabled = excluded.disabled = false; });
      }
      temporary.addEventListener("click", function () { applyVisibility(Array.from(selected), true, "TEMPORARY"); });
      excluded.addEventListener("click", function () { applyVisibility(Array.from(selected), true, "EXCLUDED"); });
      clear.addEventListener("click", function () { selected.clear(); panel.querySelectorAll(".photo-face-select").forEach(function (box) { box.checked = false; }); });
      function group(title, items, canSelect, open) {
        var details = node("details", undefined, "photo-face-group"); details.open = Boolean(open);
        var summary = node("summary", title + " · " + items.length); details.append(summary);
        details.addEventListener("toggle", function () {
          if (details.dataset.loaded === "true" || !details.open) return;
          details.dataset.loaded = "true";
        items.forEach(function (face) {
            var card = node("article", undefined, "photo-face-card");
            card.dataset.faceId = face.face_id;
            var canWrite = face.source_detail !== false && face.write_eligible !== false;
            var image = node("img"); image.alt = "人脸裁剪"; image.loading = "lazy"; image.src = API + "/faces/" + encodeURIComponent(face.face_id) + "/thumbnail";
            card.append(image);
            var personName = face.assigned_person_name || (face.assigned_person_id ? "未命名的人" : "待整理");
            card.append(node("strong", personName));
            if (face.display_quality_reason) card.append(node("span", face.display_quality_reason, "hint"));
            if (canSelect && canWrite) {
              var box = node("input"); box.type = "checkbox"; box.className = "photo-face-select"; box.setAttribute("aria-label", "选择此待整理人脸");
              box.dataset.faceId = face.face_id; box.hidden = !batchMode;
              box.addEventListener("change", function () { if (box.checked) selected.add(face.face_id); else selected.delete(face.face_id); });
              card.append(box);
            }
            if (!canWrite) card.append(node("span", face.source_detail === false ? "识别更新待确认 · 只读" : "映射待确认 · 只读", "hint"));
            else if (face.assigned_person_id !== null && face.assigned_person_id !== undefined) {
              var remove = node("button", "移出人物"); remove.type = "button";
              remove.addEventListener("click", function () {
                remove.disabled = true;
                writeFace("/faces/" + encodeURIComponent(face.face_id) + "/assignment", "PUT", {
                  contract_version: "p3-face-correction-contracts.v1", request_id: remove.dataset.requestId || (remove.dataset.requestId = faceRequestId()), action: "unassign",
                  expected_revision: Number(face.control_revision || 0)
                }).then(reload).catch(function (error) { status.textContent = error.status === 409 ? "归属已变化，请刷新后重试。" : error.message || "移出失败。"; }).then(function () { remove.disabled = false; });
              }); card.append(remove);
              var personAlbum = node("a", "查看该人物相册"); personAlbum.href = "/persons?person_id=" + encodeURIComponent(face.assigned_person_id) + "&from_photo=photo"; card.append(personAlbum);
              var change = node("button", "更换人物"); change.type = "button";
              change.addEventListener("click", function () {
                var query = node("input"); query.type = "search"; query.placeholder = "搜索人物"; query.setAttribute("aria-label", "搜索更换目标人物");
                var find = node("button", "查找人物"); find.type = "button"; var choices = node("div", undefined, "photo-face-choices");
                find.addEventListener("click", function () { find.disabled = true; choices.replaceChildren();
                  request("/persons/browse?kind=all&sort=photos_desc&limit=20&q=" + encodeURIComponent(query.value.trim())).then(function (page) {
                    (page.persons || []).filter(function (person) { return Number(person.person_id) !== Number(face.assigned_person_id); }).forEach(function (person) {
                      var option = node("button", person.name || "未命名的人"); option.type = "button"; option.addEventListener("click", function () {
                        option.disabled = true; writeFace("/faces/" + encodeURIComponent(face.face_id) + "/assignment", "PUT", { contract_version: "p3-face-correction-contracts.v1", request_id: option.dataset.requestId || (option.dataset.requestId = faceRequestId()), action: "assign", target_person_id: person.person_id, expected_revision: Number(face.control_revision || 0) }).then(reload).catch(function (error) { if (error.status >= 500 || !error.status) setStale("更换结果暂时无法确认。请刷新当前照片状态。"); else status.textContent = error.status === 409 ? "归属已变化，请刷新后重试。" : error.message || "更换人物失败。"; });
                      }); choices.append(option);
                    });
                  }).catch(function (error) { status.textContent = error.message || "人物暂时无法读取。"; }).then(function () { find.disabled = false; });
                });
                card.append(query, find, choices);
              }); card.append(change);
            } else if (face.ignore_state) {
              var restore = node("button", "恢复"); restore.type = "button";
              restore.addEventListener("click", function () { restore.disabled = true; applyVisibility([face.face_id], false, face.ignore_state); }); card.append(restore);
            } else if (face.source_detail) {
              var query = node("input"); query.type = "search"; query.placeholder = "搜索人物"; query.setAttribute("aria-label", "搜索人物");
              var choices = node("div", undefined, "photo-face-choices"); var choose = node("button", "查找人物"); choose.type = "button";
              choose.addEventListener("click", function () {
                choose.disabled = true; choices.replaceChildren();
                request("/persons/browse?kind=all&sort=photos_desc&limit=20&q=" + encodeURIComponent(query.value.trim()))
                  .then(function (page) { (page.persons || []).forEach(function (person) { var option = node("button", person.name || "未命名的人"); option.type = "button"; option.addEventListener("click", function () {
                    option.disabled = true;
                    writeFace("/faces/" + encodeURIComponent(face.face_id) + "/assignment", "PUT", { contract_version: "p3-face-correction-contracts.v1", request_id: option.dataset.requestId || (option.dataset.requestId = faceRequestId()), action: "assign", target_person_id: person.person_id, expected_revision: Number(face.control_revision || 0) }).then(reload).catch(function (error) { if (error.status >= 500 || !error.status) setStale("指定结果暂时无法确认。请刷新当前照片状态。"); else status.textContent = error.status === 409 ? "归属已变化，请刷新后重试。" : error.message || "指定人物失败。"; });
                  }); choices.append(option); }); if (!(page.persons || []).length) choices.append(node("span", "没有找到人物。", "hint")); })
                  .catch(function (error) { status.textContent = error.message || "人物暂时无法读取。"; }).then(function () { choose.disabled = false; });
              });
              card.append(query, choose, choices);
              var create = node("button", "新建未命名人物"); create.type = "button";
              create.addEventListener("click", function () { create.disabled = true; writeFace("/faces/" + encodeURIComponent(face.face_id) + "/create-person", "POST", { contract_version: "p4-person-create-from-face.v1", request_id: create.dataset.requestId || (create.dataset.requestId = faceRequestId()), expected_control_revision: Number(face.control_revision || 0) }).then(reload).catch(function (error) { if (error.status >= 500 || !error.status) setStale("新建结果暂时无法确认。请刷新照片状态，不要使用新请求重复创建。"); else status.textContent = error.status === 409 ? "人脸状态已变化，请刷新后重试。" : error.message || "创建人物失败。"; }).then(function () { create.disabled = false; }); }); card.append(create);
              ["TEMPORARY", "EXCLUDED"].forEach(function (ignoreState) { var ignore = node("button", ignoreState === "TEMPORARY" ? "暂时忽略" : "明确排除"); ignore.type = "button"; ignore.addEventListener("click", function () { applyVisibility([face.face_id], true, ignoreState); }); card.append(ignore); });
            }
            details.append(card);
          });
        });
        return details;
      }
      panel.append(group("已归属", assigned, false, false), group("待整理", pending, true, false));
      var ignoredDetails = node("details", undefined, "photo-face-group");
      ignoredDetails.append(node("summary", "已忽略 · " + ignored.length));
      ignoredDetails.addEventListener("toggle", function () {
        if (!ignoredDetails.open || ignoredDetails.dataset.loaded === "true") return;
        ignoredDetails.dataset.loaded = "true";
        ignoredDetails.append(group("暂时忽略", ignored.filter(function (face) { return face.ignore_state === "TEMPORARY"; }), false, false));
        ignoredDetails.append(group("明确排除", ignored.filter(function (face) { return face.ignore_state === "EXCLUDED"; }), false, false));
      });
      panel.append(ignoredDetails, status);
    });
    section.append(toggle, panel);
  }
  function renderPeople(host, assetId, state) {
    var section = host.parentElement || host.parentNode;
    function paint(body) {
      if (!state.active) return;
      host.replaceChildren();
      Array.prototype.slice.call(section.children).filter(function (item) { return item.className === "photo-face-manager-toggle" || item.className === "photo-face-manager"; }).forEach(function (item) { item.remove(); });
      function reload() { return request("/assets/" + encodeURIComponent(assetId) + "/faces").then(paint); }
      renderFaceManager(section, assetId, state, body, reload);
      if (typeof state.onFaces === "function") state.onFaces(Number(assetId), body);
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
      var entries = Array.from(byPerson.entries()).map(function (entry) {
        return { face: entry[1], person: { person_id: entry[0], canonical_person_id: entry[0], name: entry[1].assigned_person_name,
          avatar_face_id: entry[1].assigned_person_avatar_face_id, asset_count: entry[1].assigned_person_asset_count } };
      });
      return Promise.resolve().then(function () {
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
    }
    return request("/assets/" + encodeURIComponent(assetId) + "/faces").then(paint);
  }
  function render(host, assetId, options) {
    var prior = mounts.get(host);
    if (prior) prior.dispose();
    var state = { active: true, map: null, seq: ++serial, onPerson: options && options.onPerson,
      onFaces: options && options.onFaces, onFacesError: options && options.onFacesError,
      onFacesChanged: options && options.onFacesChanged };
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
      {
        var people = node("section", undefined, "photo-info-people"); people.append(node("h3", "照片中的人物"));
        var peopleRows = node("div", undefined, "photo-info-people-list"); people.append(peopleRows); content.append(people);
        if (!(options && options.people === false)) {
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
        }
        renderPeople(peopleRows, assetId, state).catch(function (error) {
          if (!current()) return;
          peopleRows.replaceChildren(node("p", "人物信息暂时无法读取。", "hint"));
          if (typeof state.onFacesError === "function") state.onFacesError(Number(assetId), error);
        });
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
    var favoriteButton = node("button", "☆ 收藏", "photo-viewer-favorite"); favoriteButton.type = "button";
    favoriteButton.disabled = true;
    favoriteButton.setAttribute("aria-pressed", "false");
    favoriteButton.setAttribute("aria-label", "正在读取收藏状态");
    var photoCollectionButton = node("button", "加入照片合集", "photo-viewer-collection"); photoCollectionButton.type = "button";
    headingActions.append(favoriteButton);
    headingActions.append(photoCollectionButton);
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
      image.style.transform = "translate(-50%,-50%) translate(" + Math.round(preview.x) + "px," + Math.round(preview.y) + "px)";
      zoomLabel.textContent = Math.round(scale * 100) + "%";
      zoomOut.disabled = scale <= 0.1; zoomIn.disabled = scale >= 8;
      clampPan();
    }
    function clampPan() {
      if (!preview.naturalWidth || !preview.naturalHeight || !stage.clientWidth || !stage.clientHeight) return;
      var width = preview.naturalWidth * preview.scale, height = preview.naturalHeight * preview.scale;
      var maxX = Math.max(0, (width - stage.clientWidth) / 2), maxY = Math.max(0, (height - stage.clientHeight) / 2);
      preview.x = Math.max(-maxX, Math.min(maxX, preview.x)); preview.y = Math.max(-maxY, Math.min(maxY, preview.y));
      image.style.transform = "translate(-50%,-50%) translate(" + Math.round(preview.x) + "px," + Math.round(preview.y) + "px)";
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
    var draggedClickPending = false, previousClickDragged = false, suppressNextDoubleClick = false;
    stage.addEventListener("click", function (event) {
      var wasDrag = draggedClickPending; draggedClickPending = false;
      var clickCount = Number(event.detail) || 0;
      if (clickCount < 2) {
        suppressNextDoubleClick = false;
        previousClickDragged = wasDrag;
        return;
      }
      if (clickCount === 2) suppressNextDoubleClick = wasDrag || previousClickDragged;
      previousClickDragged = false;
    });
    stage.addEventListener("dblclick", function (event) {
      var suppress = suppressNextDoubleClick; suppressNextDoubleClick = false;
      if (suppress || event.target !== image || !preview.naturalWidth || !preview.naturalHeight) return;
      if (preview.mode === "fit") { preview.mode = "zoom"; applyScale(1); } else fitPreview();
    });
    stage.addEventListener("wheel", function (event) {
      if (!event.ctrlKey || !preview.naturalWidth || event.target !== image) return;
      event.preventDefault(); var rect = stage.getBoundingClientRect();
      preview.mode = "zoom"; applyScale(preview.scale * (event.deltaY < 0 ? 1.25 : 1 / 1.25), event.clientX - rect.left - rect.width / 2, event.clientY - rect.top - rect.height / 2);
    }, { passive: false });
    var dragging = null;
    function clearDrag(releaseCapture, completed) {
      var active = dragging; dragging = null;
      if (active && active.active && completed) draggedClickPending = true;
      if (releaseCapture && active && active.captured && stage.releasePointerCapture) {
        try { if (!stage.hasPointerCapture || stage.hasPointerCapture(active.id)) stage.releasePointerCapture(active.id); } catch (error) { /* capture may already be lost */ }
      }
    }
    stage.addEventListener("pointerdown", function (event) {
      if (event.target !== image || !preview.naturalWidth || event.isPrimary === false
          || (event.pointerType === "mouse" && event.button !== undefined && event.button !== 0)
          || (preview.naturalWidth * preview.scale <= stage.clientWidth && preview.naturalHeight * preview.scale <= stage.clientHeight)) return;
      if (dragging) return;
      draggedClickPending = false;
      dragging = { id: event.pointerId, x: event.clientX, y: event.clientY, baseX: preview.x, baseY: preview.y, active: false, captured: false };
    });
    stage.addEventListener("pointermove", function (event) {
      if (!dragging || dragging.id !== event.pointerId) return;
      var deltaX = event.clientX - dragging.x, deltaY = event.clientY - dragging.y;
      if (!dragging.active) {
        if (deltaX * deltaX + deltaY * deltaY < 16) return;
        dragging.active = true;
        if (stage.setPointerCapture) { stage.setPointerCapture(event.pointerId); dragging.captured = true; }
      }
      preview.x = dragging.baseX + deltaX; preview.y = dragging.baseY + deltaY; clampPan();
      if (event.preventDefault) event.preventDefault();
    });
    ["pointerup", "pointercancel", "lostpointercapture"].forEach(function (name) {
      stage.addEventListener(name, function () { clearDrag(name !== "lostpointercapture", name === "pointerup"); });
    });
    var onWindowPointerEnd = function (event) {
      if (dragging && dragging.id === event.pointerId) clearDrag(true, event.type === "pointerup");
    };
    var onWindowBlur = function () { clearDrag(true, false); draggedClickPending = false; previousClickDragged = false; suppressNextDoubleClick = false; };
    var onWindowResize = function () { if (!dialog.open) return; if (preview.mode === "fit") fitPreview(); else clampPan(); };
    if (window.addEventListener) {
      window.addEventListener("resize", onWindowResize);
      window.addEventListener("pointerup", onWindowPointerEnd);
      window.addEventListener("pointercancel", onWindowPointerEnd);
      window.addEventListener("blur", onWindowBlur);
    }
    function setAsset(nextId, nextOptions) {
      clearDrag(true);
      generation++; var thisGeneration = generation;
      currentAssetId = String(nextId); preview.naturalWidth = 0; preview.naturalHeight = 0; preview.scale = 1; preview.mode = "fit"; preview.x = 0; preview.y = 0;
      var isFavorite = false;
      favoriteButton.disabled = true; favoriteButton.textContent = "读取收藏…";
      photoCollectionButton.disabled = false;
      photoCollectionButton.textContent = "加入照片合集";
      favoriteButton.setAttribute("aria-pressed", "false");
      favoriteButton.setAttribute("aria-label", "正在读取收藏状态");
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
      function applyFavoriteState(value) {
        if (thisGeneration !== generation || currentAssetId !== String(nextId)) return;
        isFavorite = value;
        favoriteButton.disabled = false;
        favoriteButton.textContent = isFavorite ? "★ 已收藏" : "☆ 收藏";
        favoriteButton.setAttribute("aria-pressed", isFavorite ? "true" : "false");
        favoriteButton.setAttribute("aria-label", isFavorite ? "取消收藏" : "收藏这张照片");
      }
      function readFavoriteState() {
        return request("/favorites/states?asset_ids=" + encodeURIComponent(String(nextId)))
          .then(function (body) {
            if (thisGeneration !== generation || currentAssetId !== String(nextId)) return;
            applyFavoriteState((body.favorite_asset_ids || []).map(String).indexOf(String(nextId)) >= 0);
          });
      }
      favoriteButton.onclick = function () {
        if (favoriteButton.disabled || thisGeneration !== generation || currentAssetId !== String(nextId)) return;
        var target = !isFavorite;
        favoriteButton.disabled = true; favoriteButton.textContent = "保存中…";
        favoriteButton.setAttribute("aria-label", "正在保存收藏状态");
        writeRequest("/assets/" + encodeURIComponent(String(nextId)) + "/favorite", target ? "PUT" : "DELETE", {})
          .then(function (body) {
            if (thisGeneration !== generation || currentAssetId !== String(nextId)) return;
            applyFavoriteState(Boolean(body.is_favorite));
            if (typeof options.onFavoriteChanged === "function") options.onFavoriteChanged(String(nextId), body);
          })
          .catch(function () {
            if (thisGeneration !== generation || currentAssetId !== String(nextId)) return;
            favoriteButton.textContent = "重新读取收藏…";
            favoriteButton.setAttribute("aria-label", "收藏写入未确认，正在重新读取状态");
            readFavoriteState().catch(function () {
              if (thisGeneration !== generation || currentAssetId !== String(nextId)) return;
              favoriteButton.disabled = true; favoriteButton.textContent = "收藏状态不可用";
              favoriteButton.setAttribute("aria-label", "收藏状态读取失败");
            });
          });
      };
      photoCollectionButton.onclick = function () {
        if (thisGeneration !== generation || currentAssetId !== String(nextId)) return;
        photoCollectionButton.disabled = true;
        photoCollectionButton.textContent = "读取合集…";
        request("/photo-collections?limit=50").then(function (body) {
          if (thisGeneration !== generation || currentAssetId !== String(nextId)) return;
          var collections = body.collections || [];
          if (!collections.length) {
            var name = window.prompt("还没有照片合集。输入名称创建并收录这张照片：");
            if (name === null || !name.trim()) return;
            var requestId = window.crypto && window.crypto.randomUUID ? window.crypto.randomUUID()
              : "pc-" + Date.now() + "-" + Math.random().toString(36).slice(2);
            return writeRequest("/photo-collections", "POST", {name:name, create_request_id:requestId,
              asset_ids:[Number(nextId)]}).then(function () { photoCollectionButton.textContent = "已加入新合集"; });
          }
          var choices = collections.map(function (item, index) { return (index + 1) + ". " + item.name + "（" + item.member_count + " 张）"; });
          var selected = window.prompt("输入合集序号收录此照片：\n" + choices.join("\n"));
          var index = Number(selected) - 1;
          if (!Number.isInteger(index) || index < 0 || index >= collections.length) return;
          var collection = collections[index];
          return writeRequest("/photo-collections/" + encodeURIComponent(collection.collection_id) + "/members/batch", "POST", {
            action:"add", asset_ids:[Number(nextId)], instance_token:collection.instance_token,
            expected_revision:Number(collection.revision)
          }).then(function () { photoCollectionButton.textContent = "已加入「" + collection.name + "」"; });
        }).catch(function (error) {
          if (thisGeneration === generation) photoCollectionButton.textContent = "加入失败：" + (error.message || "请重试");
        }).finally(function () {
          if (thisGeneration === generation && currentAssetId === String(nextId)) photoCollectionButton.disabled = false;
        });
      };
      readFavoriteState().catch(function () {
        if (thisGeneration !== generation || currentAssetId !== String(nextId)) return;
        favoriteButton.disabled = true; favoriteButton.textContent = "收藏状态不可用";
        favoriteButton.setAttribute("aria-label", "收藏状态读取失败");
      });
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
    if (typeof ResizeObserver !== "undefined") { var observer = new ResizeObserver(function () { if (!dialog.open) return; if (preview.mode === "fit") fitPreview(); else clampPan(); }); observer.observe(stage); observer.observe(panel); dialog.addEventListener("close", function () { observer.disconnect(); }); }
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
      clearDrag(true);
      generation++;
      if (window.removeEventListener) {
        window.removeEventListener("resize", onWindowResize);
        window.removeEventListener("pointerup", onWindowPointerEnd);
        window.removeEventListener("pointercancel", onWindowPointerEnd);
        window.removeEventListener("blur", onWindowBlur);
        if (historyOwned) window.removeEventListener("popstate", onPopState);
      }
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
