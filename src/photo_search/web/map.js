(function () {
  "use strict";
  var API = "/api/v1/map", PAGE = 48, version = null, cursor = null;
  var bbox = null, features = [], map = null, sequence = 0, timer = null;
  var assetMode = true, sourceMode = null, summaryTotal = 0, gridAssets = [];
  var restoreState = null, clusterPaging = null, cellPaging = null, focusIntent = null;
  function $(id) { return document.getElementById(id); }
  function req(url) {
    return fetch(url, { credentials: "same-origin" }).then(function (r) {
      return r.json().then(function (x) {
        if (!r.ok) throw new Error(x.message || x.detail || "请求失败");
        return x;
      });
    });
  }
  function bounds() {
    if (!map) return null;
    var b = map.getBounds(), w = b.getWest(), e = b.getEast();
    if (e - w >= 360) return { west: -180, south: Math.max(-90, b.getSouth()), east: 180, north: Math.min(90, b.getNorth()) };
    function wrap(x) { return ((x + 180) % 360 + 360) % 360 - 180; }
    return { west: wrap(w), south: Math.max(-90, b.getSouth()), east: wrap(e), north: Math.min(90, b.getNorth()) };
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
    req(endpoint("/assets", target) + (target ? "&" : "?") + "limit=" + PAGE).then(function (x) {
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
    return Promise.all([req(endpoint("/points", b)), req(endpoint("/assets", b) + (b ? "&" : "?") + "limit=" + PAGE)]).then(function (result) {
      if (id !== sequence) return;
      var points = result[0], assets = result[1];
      if (points.member_version !== assets.member_version) {
        if (attempt < 3) return fetchSnapshot(id, b, attempt + 1);
        $("notice").textContent = "照片列表正在更新，请稍后重试。";
        $("grid-title").textContent = "照片列表正在更新";
        return;
      }
      version = points.member_version; assetMode = points.mode === "photos";
      features = points.features || []; cursor = assets.next_cursor;
      if (restoreState && restoreState.version === version) {
        drawGrid({ assets: restoreState.assets || [], next_cursor: restoreState.cursor }, true);
        window.scrollTo(0, restoreState.scroll || 0); restoreState = null;
      } else drawGrid(assets, true);
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
      $("notice").textContent = points.total ? "" : "当前图库没有带位置的照片。";
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
    var center = map ? map.getCenter().toArray() : null, zoom = map ? map.getZoom() : null;
    sessionStorage.setItem("aisearch.map-return.v1", JSON.stringify({ center: center, zoom: zoom, bbox: bbox,
      version: version, scroll: window.scrollY, assets: gridAssets, cursor: cursor }));
    location.href = "/#/asset/" + encodeURIComponent(id);
  }
  function openCell(cell, z) {
    var id = sequence, b = bbox, expected = version;
    if (!b || !current(id, expected)) return;
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
        zoom = s.total === 1 ? 12 : Math.min(8, Math.max(2, Math.log2(360 / Math.max(1, span))));
      }
      var saved = null;
      try { saved = JSON.parse(sessionStorage.getItem("aisearch.map-return.v1") || "null"); } catch (_) {}
      if (saved && saved.version === s.member_version && saved.center) { center = saved.center; zoom = saved.zoom; restoreState = saved; }
      if (!window.maplibregl || !window.maplibregl.Map) { fallback(id); return; }
      try {
        map = new maplibregl.Map({ container: "map", center: center, zoom: zoom, maxZoom: 19,
          style: { version: 8, sources: { osm: { type: "raster", tiles: [window.AISEARCH_MAP_CONFIG.tile_url], tileSize: 256, attribution: window.AISEARCH_MAP_CONFIG.attribution } },
            layers: [{ id: "osm", type: "raster", source: "osm", minzoom: 0, maxzoom: 19 }] } });
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
      map.on("load", refresh); map.on("moveend", schedule);
      map.on("error", function () {
        if (map && !map.isStyleLoaded()) { var failedId = sequence; $("notice").textContent = "地图暂不可用；当前范围照片仍可分页浏览。"; fallbackGrid(failedId, bbox); }
      });
      var focused = new URLSearchParams(location.search).get("asset_id");
      if (focused) {
        focusIntent = { assetId: focused, asset: null, version: null, attempts: 0 };
        requestFocusAsset(focusIntent);
      }
      if (saved && saved.version === s.member_version) window.scrollTo(0, saved.scroll || 0);
      $("more").onclick = loadMore;
    }).catch(function (e) { $("notice").textContent = "读取地图摘要失败：" + e.message; });
  }
  function startMap() {
    $("more").onclick = loadMore;
    if (window.maplibregl) init();
    else { sequence++; fallback(sequence); }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", startMap); else startMap();
})();
