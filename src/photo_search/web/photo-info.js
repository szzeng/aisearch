/* Shared, read-only photo details for every photo viewer. */
(function () {
  "use strict";
  var API = "/api/v1";
  var sourceRoots = new Map();
  var serial = 0;
  var mounts = new WeakMap();

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
      try {
        var config = window.AISEARCH_MAP_CONFIG || {};
        state.map = new window.maplibregl.Map({ container: canvas, center: [asset.longitude, asset.latitude], zoom: 12,
          interactive: false, attributionControl: true,
          style: { version: 8, sources: { osm: { type: "raster", tiles: [config.tile_url || "https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
            tileSize: 256, attribution: config.attribution || "© OpenStreetMap contributors" } }, layers: [{ id: "osm", type: "raster", source: "osm" }] } });
        state.map.on("load", function () {
          if (!state.active || !state.map) return;
          state.map.addSource("photo-location", { type: "geojson", data: { type: "Feature", geometry: { type: "Point", coordinates: [asset.longitude, asset.latitude] }, properties: {} } });
          state.map.addLayer({ id: "photo-location", type: "circle", source: "photo-location", paint: { "circle-radius": 7, "circle-color": "#dc3545", "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });
        });
        state.map.on("error", unavailable);
      } catch (_) { unavailable(); }
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
        entries.forEach(function (entry) {
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
          button.addEventListener("click", function () { if (typeof state.onPerson === "function") state.onPerson(id); else window.location.href = "/persons?person_id=" + encodeURIComponent(id); });
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
    host.replaceChildren(node("p", "正在读取照片信息…", "hint"));
    function current() { return state.active && seq === state.seq && host.isConnected; }
    request("/assets/" + encodeURIComponent(assetId)).then(function (detail) {
      if (!current()) return;
      host.replaceChildren();
      var title = node("h3", fileName((detail.locations || []).filter(function (x) { return x.availability === "PRESENT"; })[0] || (detail.locations || [])[0], assetId)); host.append(title);
      var list = node("dl", undefined, "photo-info-facts");
      addField(list, "分辨率", detail.width && detail.height ? detail.width + " × " + detail.height + " 像素" : null);
      addField(list, "格式", detail.format ? String(detail.format).toUpperCase() : null);
      addField(list, "大小", bytes(detail.byte_size));
      var taken = detail.taken_time || null;
      addField(list, "时间", taken && taken.taken_at ? (taken.label === "capture" ? "拍摄时间 " : "文件时间 ") + taken.taken_at.replace("T", " ") : null);
      addField(list, "相机", taken && taken.camera);
      host.append(list);
      var pathSection = node("section", undefined, "photo-info-paths"); pathSection.append(node("h3", "文件位置"));
      var locations = (detail.locations || []).slice().sort(function (a, b) { return (a.availability === "PRESENT" ? 0 : 1) - (b.availability === "PRESENT" ? 0 : 1); });
      if (!locations.length) pathSection.append(node("p", "没有登记文件位置。", "hint"));
      var status = node("p", "", "hint");
      locations.forEach(function (location) {
        var row = node("div", undefined, "photo-info-location");
        row.append(node("strong", (location.source_display_name || ("来源 #" + location.source_id)) + " · " + (location.availability === "PRESENT" ? "在库" : "不可用")));
        var relative = node("p", location.relative_path || "（无相对路径）", "photo-info-relative"); row.append(relative);
        sourceRoot(location.source_id).then(function (root) {
          if (!current()) return;
          var complete = safePath(root, location.relative_path);
          if (!complete) { row.append(node("p", "来源路径暂不可用；以上为相对路径。", "hint")); return; }
          var path = node("p", complete, "photo-info-full-path"); path.tabIndex = 0; row.append(path);
          var copy = node("button", "复制完整路径"); copy.type = "button"; copy.addEventListener("click", function () { copyPath(complete, copy, status); }); row.append(copy);
        }).catch(function () { if (current()) row.append(node("p", "完整路径暂不可用；以上为相对路径。", "hint")); });
        pathSection.append(row);
      });
      pathSection.append(status); host.append(pathSection);
      if (!(options && options.people === false)) {
        var people = node("section", undefined, "photo-info-people"); people.append(node("h3", "照片中的人物"));
        var peopleRows = node("div", undefined, "photo-info-people-list"); people.append(peopleRows); host.append(people);
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
          showMap(host, { latitude: lat, longitude: lon }, assetId, state);
        });
      }).catch(function () { if (current()) host.append(node("p", "GPS 信息暂时无法读取。", "hint")); });
    }).catch(function (error) { if (current()) host.replaceChildren(node("p", "照片信息暂时无法读取。" + (error.status === 401 ? "请重新打开本机应用。" : ""), "error")); });
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
    heading.append(node("h2", options.title || "查看照片"));
    var close = node("button", "关闭"); close.type = "button"; heading.append(close);
    var layout = node("div", undefined, "photo-viewer-layout");
    var image = node("img"); image.alt = options.alt || "照片大图"; image.src = options.imageUrl || API + "/assets/" + encodeURIComponent(assetId) + "/preview"; layout.append(image);
    var panel = node("aside", undefined, "photo-info-panel"); layout.append(panel);
    dialog.append(heading, layout); document.body.append(dialog);
    var mounted = render(panel, assetId, options);
    function dismiss() { if (dialog.open) dialog.close(); }
    close.addEventListener("click", dismiss);
    dialog.addEventListener("close", function () { mounted.dispose(); dialog.remove(); if (options.returnFocus && options.returnFocus.isConnected) options.returnFocus.focus(); });
    dialog.addEventListener("click", function (event) { if (event.target === dialog) dismiss(); });
    dialog.addEventListener("keydown", function (event) { if (event.key === "Escape" && dialog.open) dismiss(); });
    dialog.showModal();
    return { close: dismiss };
  }
  window.AIPhotoInfo = { render: render, open: open };
})();
