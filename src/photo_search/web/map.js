(function () {
  "use strict";
  var API = "/api/v1/map", PAGE = 48, version = null, cursor = null;
  var bbox = null, features = [], map = null, sequence = 0, timer = null;
  var orderMode = "asset_id_desc";
  var assetMode = true, sourceMode = null, summaryTotal = 0, gridAssets = [];
  var restoreState = null, clusterPaging = null, cellPaging = null, focusIntent = null;
  var runtimeConfig = null, basemapLayer = "vector", settingsDialog = null, clearArmed = false;
  function $(id) { return document.getElementById(id); }
  function req(url) {
    return fetch(url, { credentials: "same-origin" }).then(function (r) {
      return r.json().then(function (x) {
        if (!r.ok) throw new Error(x.message || x.detail || "请求失败");
        return x;
      });
    });
  }
  function csrfToken() {
    var match = document.cookie.match(/(?:^|;\s*)ps_csrf=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }
  function mutateSettings(method, body) {
    return fetch(API + "/provider-settings" + (method === "DELETE" ? "/key" : ""), {
      method: method, credentials: "same-origin", cache: "no-store",
      headers: Object.assign({ "X-CSRF-Token": csrfToken() }, body ? { "Content-Type": "application/json" } : {}),
      body: body ? JSON.stringify(body) : undefined
    }).then(function (r) { return r.json().then(function (x) { if (!r.ok) throw new Error(x.detail || x.message || "保存失败"); return x; }); });
  }
  function runtime() {
    return fetch(API + "/provider-runtime", { credentials: "same-origin", cache: "no-store" }).then(function (r) {
      return r.json().then(function (x) { if (!r.ok) throw new Error("地图运行配置不可用"); return x; });
    });
  }
  function setBaseLayer(layer) {
    basemapLayer = layer === "imagery" ? "imagery" : "vector";
    var control = $("map-layer-controls");
    if (control && control.querySelectorAll) control.querySelectorAll("button").forEach(function (button) {
      button.setAttribute("aria-pressed", String(button.dataset.layer === basemapLayer));
    });
    if (!map || !runtimeConfig || !runtimeConfig.key) return;
    var cfg = window.AISEARCH_MAP_CONFIG, wanted = cfg.layerIds(basemapLayer);
    ["td-vec-layer", "td-cva-layer", "td-img-layer", "td-cia-layer"].forEach(function (id) {
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", wanted.indexOf(id) >= 0 ? "visible" : "none");
    });
  }
  function installBasemap(config) {
    runtimeConfig = config;
    if (!map || !map.isStyleLoaded() || !config || config.status !== "CONFIGURED" || !config.key) {
      return;
    }
    var cfg = window.AISEARCH_MAP_CONFIG;
    var ids = ["td-vec-layer", "td-cva-layer", "td-img-layer", "td-cia-layer"];
    ids.forEach(function (id) { if (map.getLayer(id)) map.removeLayer(id); });
    ["td-vec", "td-cva", "td-img", "td-cia"].forEach(function (id) { if (map.getSource(id)) map.removeSource(id); });
    var sources = cfg.sourceTemplates(config.key), vector = basemapLayer === "vector";
    var names = vector ? ["td-vec", "td-cva"] : ["td-img", "td-cia"];
    var layers = vector ? ["td-vec-layer", "td-cva-layer"] : ["td-img-layer", "td-cia-layer"];
    names.forEach(function (name) { map.addSource(name, sources[name]); });
    layers.forEach(function (id, index) {
      map.addLayer({ id: id, type: "raster", source: names[index], minzoom: 0, maxzoom: 18 }, map.getLayer("clusters") ? "clusters" : undefined);
    });
    $("map-connection-status").textContent = "已配置；底图按当前视野加载。";
    setBaseLayer(basemapLayer);
  }
  function setSettingsStatus(message) { $("map-settings-status").textContent = message; }
  function loadSettingsUi() {
    return req(API + "/provider-settings").then(function (state) {
      $("map-key-clear").disabled = !state.configured;
      setSettingsStatus(state.status === "CONFIGURED" ? "已配置（Key 不会回显）" : state.status === "CONFIG_UNREADABLE" ? "配置无法读取，请检查本机设置文件。" : "未配置天地图，照片列表仍可用。");
      $("map-default-layer").value = state.default_layer || "vector";
      if (state.configured && (!runtimeConfig || runtimeConfig.status !== "CONFIGURED")) return runtime();
      return null;
    }).then(function (runtimeState) {
      if (runtimeState) { runtimeConfig = runtimeState; if (runtimeState.status === "CONFIGURED") installBasemap(runtimeState); }
    }).catch(function () { setSettingsStatus("设置读取失败；照片列表仍可用。"); });
  }
  function connectSettingsUi() {
    settingsDialog = $("map-settings");
    $("map-settings-open").addEventListener("click", function () {
      loadSettingsUi().then(function () { settingsDialog.showModal(); $("map-key").focus(); });
    });
    settingsDialog.addEventListener("close", function () { $("map-settings-open").focus(); });
    $("map-default-layer").addEventListener("change", function () {
      var layer = $("map-default-layer").value;
      mutateSettings("PUT", { default_layer: layer }).then(function (state) {
        runtimeConfig = runtimeConfig || {}; runtimeConfig.default_layer = state.default_layer;
        setBaseLayer(layer);
      }).catch(function () { setSettingsStatus("默认底图保存失败，请重试。"); });
    });
    $("map-key-save").addEventListener("click", function () {
      var input = $("map-key"), key = input.value;
      if (!key) { setSettingsStatus("请输入 Key；空值不会覆盖已保存内容。"); return; }
      mutateSettings("PUT", { key: key }).then(function () {
        input.value = ""; $("map-key-clear").disabled = false;
        setSettingsStatus("Key 已保存。瓦片连通状态请使用“测试连接”确认。");
        return runtime().then(function (state) {
          runtimeConfig = state; installBasemap(state); $("notice").textContent = "";
        }).catch(function () { setSettingsStatus("Key 已保存；地图运行配置暂不可用，请重新打开地图。"); });
      }).catch(function () { setSettingsStatus("Key 保存失败，请检查输入格式和本机存储。"); });
    });
    $("map-key-clear").addEventListener("click", function () {
      if (!clearArmed) {
        clearArmed = true; $("map-key-clear").textContent = "再次点击确认清除";
        setTimeout(function () { clearArmed = false; $("map-key-clear").textContent = "清除 Key"; }, 5000);
        return;
      }
      clearArmed = false; $("map-key-clear").textContent = "清除 Key";
      mutateSettings("DELETE").then(function () {
        runtimeConfig = { status: "NOT_CONFIGURED", default_layer: basemapLayer };
        if (map) { ["td-vec-layer", "td-cva-layer", "td-img-layer", "td-cia-layer"].forEach(function (id) { if (map.getLayer(id)) map.removeLayer(id); }); ["td-vec", "td-cva", "td-img", "td-cia"].forEach(function (id) { if (map.getSource(id)) map.removeSource(id); }); }
        $("map-key-clear").disabled = true; $("notice").textContent = "未配置天地图，照片列表仍可用。";
        setSettingsStatus("Key 已清除。照片列表仍可用。");
      }).catch(function () { setSettingsStatus("清除失败；原 Key 仍保留。"); });
    });
    $("map-key-test").addEventListener("click", function () {
      var key = $("map-key").value || (runtimeConfig && runtimeConfig.key);
      if (!key) { $("map-connection-status").textContent = "请先输入并保存 Key。"; return; }
      var image = new Image(), done = false, timeout = setTimeout(function () { finish(false); }, 8000);
      function finish(ok) { if (done) return; done = true; clearTimeout(timeout); image.onload = image.onerror = null;
        $("map-connection-status").textContent = ok ? "测试瓦片可解码；实际视野加载仍可能受来源、网络或配额影响。" : "测试瓦片不可用；可能是 Key、来源限制、网络或配额，请检查。"; }
      image.onload = function () { finish(image.naturalWidth > 0 && image.naturalHeight > 0); };
      image.onerror = function () { finish(false); };
      image.src = window.AISEARCH_MAP_CONFIG.testTileUrl(key);
    });
    if (new URLSearchParams(location.search).get("settings") === "1") {
      var cleanParams = new URLSearchParams(location.search); cleanParams.delete("settings");
      history.replaceState(null, "", location.pathname + (cleanParams.toString() ? "?" + cleanParams.toString() : "") + location.hash);
      loadSettingsUi().then(function () { settingsDialog.showModal(); });
    }
  }
  function bounds() {
    if (!map) return null;
    var b = map.getBounds(), w = b.getWest(), e = b.getEast();
    if (e - w >= 360) return { west: -180, south: Math.max(-90, b.getSouth()), east: 180, north: Math.min(90, b.getNorth()) };
    function wrap(x) { return ((x + 180) % 360 + 360) % 360 - 180; }
    return { west: wrap(w), south: Math.max(-90, b.getSouth()), east: wrap(e), north: Math.min(90, b.getNorth()) };
  }
  function sameBounds(left, right) {
    return Boolean(left && right && ["west", "south", "east", "north"].every(function (key) {
      return Number.isFinite(Number(left[key])) && Number(left[key]) === Number(right[key]);
    }));
  }
  function sameCaptureAssets(left, right) {
    function signature(rows) {
      return JSON.stringify((rows || []).map(function (asset) {
        return { asset_id: asset.asset_id, capture_time: asset.capture_time || null };
      }));
    }
    return signature(left) === signature(right);
  }
  function params(b) {
    var q = new URLSearchParams();
    if (b) Object.keys(b).forEach(function (k) { q.set(k, String(b[k])); });
    return q;
  }
  function endpoint(path, b) {
    var q = params(b);
    if (path === "/points" && map) q.set("zoom", String(Math.round(map.getZoom())));
    var query = q.toString(); return API + path + (query ? "?" + query : "");
  }
  function extent() { bbox = bounds(); return bbox; }
  function title(total) { $("grid-title").textContent = "当前范围 · " + total + " 张照片"; }
  function setOrderMode(value) {
    orderMode = value === "capture_time_desc" ? value : "asset_id_desc";
    $("map-order-default").setAttribute("aria-pressed", String(orderMode === "asset_id_desc"));
    $("map-order-capture").setAttribute("aria-pressed", String(orderMode === "capture_time_desc"));
    refresh();
  }
  function drawGrid(data, reset) {
    var box = $("grid");
    if (reset) { box.textContent = ""; gridAssets = []; }
    (data.assets || []).forEach(function (a) {
      gridAssets.push(a);
      var tile = document.createElement("article"), img = document.createElement("img"), button = document.createElement("button"), imageButton = document.createElement("button");
      tile.className = "tile"; img.loading = "lazy"; img.src = a.thumbnail || ("/api/v1/assets/" + encodeURIComponent(a.asset_id) + "/thumbnail"); img.alt = "照片 " + a.asset_id;
      button.textContent = "查看照片"; button.onclick = function () { openAsset(a.asset_id); };
      imageButton.type = "button"; imageButton.className = "map-photo-open"; imageButton.setAttribute("aria-label", "查看照片 " + a.asset_id); imageButton.onclick = button.onclick; imageButton.appendChild(img);
      tile.appendChild(imageButton); tile.appendChild(button); box.appendChild(tile);
      if (a.capture_time && a.capture_time.taken_at) {
        var date = document.createElement("small");
        date.textContent = String(a.capture_time.taken_at).replace("T", " ");
        tile.appendChild(date);
      }
    });
    cursor = data.next_cursor;
    $("more").hidden = !cursor && !clusterPaging;
    $("more").disabled = false;
  }
  function current(id, expectedVersion) {
    return id === sequence && (expectedVersion === null || expectedVersion === undefined || version === expectedVersion);
  }
  function fallbackGrid(id, b) {
    var target = b || null;
    var url = endpoint("/assets", target) + (target ? "&" : "?") + "limit=" + PAGE;
    if (orderMode === "capture_time_desc") url += "&order=" + encodeURIComponent(orderMode);
    req(url).then(function (x) {
      if (id !== sequence) return;
      if (version !== null && x.member_version !== version) { refresh(); return; }
      version = x.member_version;
      summaryTotal = x.total;
      drawGrid(x, true); title(x.total);
    }).catch(function () {
      if (id === sequence) $("notice").textContent = "照片列表暂时无法读取，请重试。";
    });
  }
  function labels(data) {
    document.querySelectorAll(".map-count").forEach(function (n) { n.remove(); });
    var rows = data.mode === "grid" ? (data.features || []) :
      (map.getLayer("clusters") ? map.queryRenderedFeatures({ layers: ["clusters"] }) : []);
    rows.forEach(function (f) {
      var p = f.properties || {}, count = p.asset_count;
      if (count === undefined) return;
      var node = document.createElement("button"); node.type = "button"; node.className = "map-count";
      node.textContent = String(count); node.setAttribute("aria-label", count + "张");
      node.addEventListener("click", function (event) {
        event.stopPropagation();
        if (data.mode === "grid") { openCell(p.cell_id, p.grid_zoom); return; }
        expandCluster(p.cluster_id, f.geometry.coordinates);
      });
      new maplibregl.Marker({ element: node }).setLngLat(f.geometry.coordinates).addTo(map);
    });
  }
  function expandCluster(clusterId, coordinates) {
    if (!map) return;
    var source = map.getSource("photos");
    source.getClusterExpansionZoom(clusterId).then(function (z) {
      if (z > 16 || z <= map.getZoom() + 0.01) {
        clusterPaging = { id: clusterId, offset: 0, seq: sequence, version: version };
        loadClusterLeaves();
      } else map.easeTo({ center: coordinates, zoom: Math.min(map.getMaxZoom(), z) });
    }).catch(function () {
      clusterPaging = { id: clusterId, offset: 0, seq: sequence, version: version };
      loadClusterLeaves();
    });
  }
  function loadClusterLeaves() {
    var state = clusterPaging;
    if (!state || !map || !current(state.seq, state.version)) return;
    map.getSource("photos").getClusterLeaves(state.id, PAGE, state.offset).then(function (leaves) {
      if (clusterPaging !== state || !current(state.seq, state.version)) return;
      var box = $("grid");
      if (state.offset === 0) { box.textContent = ""; gridAssets = []; }
      (leaves || []).forEach(function (f) {
        var p = f.properties || {}, button = document.createElement("button");
        button.textContent = "查看照片 " + p.asset_id;
        button.onclick = function () { openAsset(p.asset_id); };
        box.appendChild(button); gridAssets.push({ asset_id: p.asset_id });
      });
      state.offset += (leaves || []).length;
      $("grid-title").textContent = "同位置照片成员 · 已显示 " + state.offset + " 张";
      $("more").hidden = (leaves || []).length < PAGE;
      $("more").disabled = false;
    }).catch(function () { if (clusterPaging === state) $("more").disabled = false; });
  }
  function fetchSnapshot(id, b, attempt) {
    var assetUrl = endpoint("/assets", b) + (b ? "&" : "?") + "limit=" + PAGE;
    if (orderMode === "capture_time_desc") assetUrl += "&order=" + encodeURIComponent(orderMode);
    var canCheckSavedCursor = restoreState && restoreState.order === "capture_time_desc" &&
      restoreState.cursor && sameBounds(restoreState.bbox, b);
    var cursorCheck = Promise.resolve(true);
    if (canCheckSavedCursor) {
      var checkUrl = endpoint("/assets", b) + (b ? "&" : "?") + "limit=" + PAGE +
        "&order=capture_time_desc&cursor=" + encodeURIComponent(restoreState.cursor);
      cursorCheck = req(checkUrl).then(function () { return true; }, function () { return false; });
    }
    return Promise.all([req(endpoint("/points", b)), req(assetUrl), cursorCheck]).then(function (result) {
      if (id !== sequence) return;
      var points = result[0], assets = result[1], cursorIsCurrent = result[2];
      if (points.member_version !== assets.member_version) {
        if (attempt < 3) return fetchSnapshot(id, b, attempt + 1);
        $("notice").textContent = "照片列表正在更新，请稍后重试。";
        $("grid-title").textContent = "照片列表正在更新";
        return;
      }
      version = points.member_version; assetMode = points.mode === "photos";
      features = points.features || []; cursor = assets.next_cursor;
      var canRestore = restoreState && restoreState.version === version && sameBounds(restoreState.bbox, b);
      if (canRestore && restoreState.order === "capture_time_desc") {
        canRestore = cursorIsCurrent && (restoreState.cursor || sameCaptureAssets(restoreState.assets, assets.assets));
      }
      if (canRestore) {
        drawGrid({ assets: restoreState.assets || [], next_cursor: restoreState.cursor }, true);
        window.scrollTo(0, restoreState.scroll || 0); restoreState = null;
      } else {
        restoreState = null;
        drawGrid(assets, true);
      }
      if (map) {
        var source = map.getSource("photos");
        if (source && sourceMode !== assetMode) {
          if (map.getLayer("clusters")) map.removeLayer("clusters");
          if (map.getLayer("singles")) map.removeLayer("singles");
          map.removeSource("photos"); source = null;
        }
        sourceMode = assetMode;
        if (source) source.setData(points);
        else map.addSource("photos", { type: "geojson", data: points, cluster: assetMode, clusterRadius: 48,
          clusterMaxZoom: 16, clusterProperties: { asset_count: ["+", ["case", ["has", "asset_count"], ["get", "asset_count"], 1]] } });
        if (!map.getLayer("clusters")) {
          map.addLayer({ id: "clusters", type: "circle", source: "photos", filter: ["has", "point_count"],
            paint: { "circle-color": "#2463eb", "circle-radius": ["step", ["get", "point_count"], 18, 20, 24, 100, 30], "circle-stroke-width": 2, "circle-stroke-color": "#fff" } });
          map.addLayer({ id: "singles", type: "circle", source: "photos", filter: ["!", ["has", "point_count"]],
            paint: { "circle-color": "#dc3545", "circle-radius": 7, "circle-stroke-width": 2, "circle-stroke-color": "#fff" } });
        }
        map.once("idle", function () { if (current(id, version)) labels(points); });
      }
      title(points.total);
      if (orderMode === "capture_time_desc") {
        $("grid-title").textContent = "当前地图视口 · 按可信拍摄时间排序 · " + points.total + " 张照片";
      }
      $("notice").textContent = points.total ? "" : (summaryTotal > 0 ? "当前视野没有带位置的照片。" : "当前图库没有带位置的照片。");
      applyFocusIntent();
    });
  }
  function refresh() {
    cellPaging = null; clusterPaging = null;
    var id = ++sequence, b = extent();
    $("grid-title").textContent = "正在同步照片列表…";
    $("more").disabled = false;
    fetchSnapshot(id, b, 0).catch(function () {
      if (id !== sequence) return;
      $("notice").textContent = "地图暂不可用；当前范围照片仍可分页浏览。";
      fallbackGrid(id, b);
    });
  }
  function openAsset(id) {
    if (window.AIPhotoInfo) {
      window.AIPhotoInfo.open(id, { title: "查看地图照片", alt: "地图照片 #" + id, returnFocus: document.activeElement });
      return;
    }
    location.href = "/#/asset/" + encodeURIComponent(id);
  }
  function openCell(cell, z) {
    var id = sequence, b = bbox, expected = version;
    if (!b || !current(id, expected)) return;
    if (orderMode === "capture_time_desc") {
      orderMode = "asset_id_desc";
      $("map-order-default").setAttribute("aria-pressed", "true");
      $("map-order-capture").setAttribute("aria-pressed", "false");
    }
    clusterPaging = null; var selection = cellPaging = { id: cell, zoom: z, bbox: b, seq: id, version: expected };
    var q = params(b); q.set("limit", PAGE); q.set("cell_id", cell); q.set("grid_zoom", z);
    req(API + "/assets?" + q.toString()).then(function (x) {
      if (cellPaging !== selection || selection.seq !== sequence || !current(id, expected)) return;
      if (x.member_version !== expected) { refresh(); return; }
      drawGrid(x, true); $("grid-title").textContent = "聚合区域 · " + x.total + " 张照片";
    }).catch(function () { if (cellPaging === selection && selection.seq === sequence) refresh(); });
  }
  function schedule() {
    sequence++; clearTimeout(timer); clusterPaging = null; cellPaging = null; cursor = null;
    $("more").hidden = true; $("more").disabled = false;
    timer = setTimeout(refresh, 250);
  }
  function applyFocusIntent() {
    var intent = focusIntent;
    if (!intent || !intent.asset || version === null || !map) return;
    if (intent.version !== version) {
      intent.asset = null;
      if (intent.attempts < 3) requestFocusAsset(intent);
      else focusIntent = null;
      return;
    }
    focusIntent = null;
    map.jumpTo({ center: [intent.asset.longitude, intent.asset.latitude], zoom: 14 });
  }
  function requestFocusAsset(intent) {
    intent.attempts++;
    req(API + "/assets?asset_id=" + encodeURIComponent(intent.assetId) + "&limit=1").then(function (r) {
      if (focusIntent !== intent || !map) return;
      var a = r.assets && r.assets[0];
      if (!a) { focusIntent = null; return; }
      intent.version = r.member_version; intent.asset = a;
      applyFocusIntent();
    }).catch(function () { if (focusIntent === intent) focusIntent = null; });
  }
  function loadMore() {
    if (clusterPaging) { loadClusterLeaves(); return; }
    if (!cursor) return;
    var id = sequence, expected = version, b = bbox, next = cursor;
    $("more").disabled = true;
    var q = params(b); q.set("limit", PAGE); q.set("cursor", next);
    if (cellPaging) { q.set("cell_id", cellPaging.id); q.set("grid_zoom", cellPaging.zoom); }
    else if (orderMode === "capture_time_desc") q.set("order", orderMode);
    req(API + "/assets?" + q.toString()).then(function (x) {
      if (!current(id, expected) || bbox !== b || cursor !== next) return;
      if (x.member_version !== expected) { refresh(); return; }
      drawGrid(x, false);
    }).catch(function () {
      if (!current(id, expected)) return;
      $("more").disabled = false; refresh();
    });
  }
  function fallback(id) {
    map = null; $("notice").textContent = "地图暂不可用；照片仍可通过列表浏览。";
    bbox = null; fallbackGrid(id, null);
  }
  function revealPersonsNav(body) {
    var link = $("nav-persons");
    if (!link) return;
    var version = Number(body && body.schema_version);
    link.hidden = !(isFinite(version) && version >= 13 && String(body && body.schema_status) === "ok");
  }
  function probePageCapabilities() {
    req("/api/v1/capabilities/page").then(revealPersonsNav).catch(function () { revealPersonsNav(null); });
  }
  function init() {
    var id = ++sequence;
    req(API + "/summary").then(function (s) {
      if (id !== sequence) return;
      summaryTotal = s.total; $("summary").textContent = s.total + " 张带位置照片";
      $("notice").textContent = s.total ? "" : "当前图库没有带位置的照片。";
      var center = [0, 20], zoom = 1.5;
      if (s.bounds) {
        var w = s.bounds.west, e = s.bounds.east, span = e < w ? e + 360 - w : e - w;
        center = [((w + span / 2 + 180) % 360 + 360) % 360 - 180, (s.bounds.south + s.bounds.north) / 2];
        var latitudeSpan = Math.max(0.01, s.bounds.north - s.bounds.south);
        var fitSpan = Math.max(span, latitudeSpan * 2.2);
        zoom = s.total === 1 ? 12 : Math.min(8, Math.max(2, Math.log2(360 / Math.max(1, fitSpan)) - 0.7));
      }
      var saved = null;
      try { saved = JSON.parse(sessionStorage.getItem("aisearch.map-return.v1") || "null"); } catch (_) {}
      if (saved && saved.version === s.member_version && saved.center) { center = saved.center; zoom = saved.zoom; restoreState = saved; }
      if (saved && saved.version === s.member_version && saved.order) {
        orderMode = saved.order === "capture_time_desc" ? saved.order : "asset_id_desc";
        $("map-order-default").setAttribute("aria-pressed", String(orderMode === "asset_id_desc"));
        $("map-order-capture").setAttribute("aria-pressed", String(orderMode === "capture_time_desc"));
      }
      if (!window.maplibregl || !window.maplibregl.Map) { fallback(id); return; }
      runtime().catch(function () { return { status: "RUNTIME_UNAVAILABLE", default_layer: "vector" }; }).then(function (runtimeState) {
        if (id !== sequence) return;
        runtimeConfig = runtimeState; basemapLayer = runtimeState.default_layer === "imagery" ? "imagery" : "vector";
        setBaseLayer(basemapLayer);
        try {
          map = new maplibregl.Map({ container: "map", center: center, zoom: zoom, maxZoom: 19,
            style: { version: 8, sources: {}, layers: [] } });
        } catch (_) { fallback(id); return; }
        try { map.addControl(new maplibregl.NavigationControl()); } catch (_) { fallback(id); return; }
        map.on("click", function (e) {
          var f = map.queryRenderedFeatures(e.point, { layers: ["clusters", "singles"] })[0]; if (!f) return;
          var p = f.properties || {};
          if (f.layer.id === "clusters") {
            if (!assetMode) { openCell(p.cell_id, p.grid_zoom); return; }
            expandCluster(p.cluster_id, f.geometry.coordinates); return;
          }
          if (p.asset_id) openAsset(p.asset_id); else openCell(p.cell_id, p.grid_zoom);
        });
        map.on("load", function () {
          if (runtimeConfig.status === "CONFIGURED") installBasemap(runtimeConfig);
          else $("notice").textContent = runtimeConfig.status === "CONFIG_UNREADABLE" ? "地图配置无法读取；照片列表仍可用。" : runtimeConfig.status === "RUNTIME_UNAVAILABLE" ? "地图服务暂不可用；照片列表仍可用。" : "未配置天地图；照片列表仍可用。";
          refresh();
        });
        map.on("moveend", schedule);
        map.on("error", function (event) {
          var sourceId = event && event.sourceId;
          if (sourceId && sourceId.indexOf("td-") === 0) {
            $("map-connection-status").textContent = "底图瓦片不可用；可能是 Key、来源限制、网络或配额，请检查。";
            $("notice").textContent = "底图暂不可用；照片仍可浏览。";
          } else if (map && !map.isStyleLoaded()) {
            var failedId = sequence; $("notice").textContent = "地图暂不可用；照片列表仍可分页浏览。"; fallbackGrid(failedId, bbox);
          }
        });
        var focused = new URLSearchParams(location.search).get("asset_id");
        if (focused) { focusIntent = { assetId: focused, asset: null, version: null, attempts: 0 }; requestFocusAsset(focusIntent); }
        if (saved && saved.version === s.member_version) window.scrollTo(0, saved.scroll || 0);
      });
    }).catch(function (e) { $("notice").textContent = "读取地图摘要失败：" + e.message; });
  }
  function startMap() {
    probePageCapabilities();
    connectSettingsUi();
    $("more").onclick = loadMore;
    $("map-order-default").onclick = function () { setOrderMode("asset_id_desc"); };
    $("map-order-capture").onclick = function () { setOrderMode("capture_time_desc"); };
    var layerControls = $("map-layer-controls");
    if (layerControls && layerControls.querySelectorAll) layerControls.querySelectorAll("button").forEach(function (button) {
      button.addEventListener("click", function () {
        var layer = button.dataset.layer;
        if (runtimeConfig && runtimeConfig.status === "CONFIGURED" && layer !== basemapLayer) {
          basemapLayer = layer;
          mutateSettings("PUT", { default_layer: layer }).then(function (state) {
            runtimeConfig.default_layer = state.default_layer; installBasemap(runtimeConfig); setBaseLayer(layer);
          }).catch(function () { $("notice").textContent = "底图偏好保存失败，请重试。"; });
        } else if (!runtimeConfig || runtimeConfig.status !== "CONFIGURED") {
          $("notice").textContent = "请先在地图设置中配置天地图 Key。";
          $("map-settings-open").click();
        }
      });
    });
    if (window.maplibregl) init();
    else { sequence++; fallback(sequence); }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", startMap); else startMap();
})();
