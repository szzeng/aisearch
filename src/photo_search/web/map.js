(function () {
  "use strict";
  var API = "/api/v1/map", PAGE = 48, version = null, cursor = null;
  var MAP_RETURN_KEY = "aisearch.map-return.v1";
  var bbox = null, features = [], map = null, engine = null, sequence = 0, timer = null;
  var orderMode = "asset_id_desc";
  var assetMode = true, sourceMode = null, summaryTotal = 0, gridAssets = [];
  var restoreState = null, clusterPaging = null, cellPaging = null, focusIntent = null;
  var runtimeConfig = null, basemapLayer = "normal", settingsDialog = null, clearArmed = false;
  var hoverMarker = null, hoverMarkerElement = null, mouseHoverOwner = null, focusHoverOwner = null;
  var keyboardFocusMode = false, hoverEventsBound = false;
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
    return fetch(API + "/baidu/provider-settings" + (method === "DELETE" ? "/ak" : ""), {
      method: method, credentials: "same-origin", cache: "no-store",
      headers: Object.assign({ "X-CSRF-Token": csrfToken() }, body ? { "Content-Type": "application/json" } : {}),
      body: body ? JSON.stringify(body) : undefined
    }).then(function (r) { return r.json().then(function (x) { if (!r.ok) throw new Error(x.detail || x.message || "保存失败"); return x; }); });
  }
  function runtime() {
    return fetch(API + "/baidu/provider-runtime", { credentials: "same-origin", cache: "no-store" }).then(function (r) {
      return r.json().then(function (x) { if (!r.ok) throw new Error("地图运行配置不可用"); return x; });
    });
  }
  function setBaseLayer(layer) {
    basemapLayer = ["normal", "satellite", "hybrid"].indexOf(layer) >= 0 ? layer : "normal";
    var control = $("map-layer-controls");
    if (control) control.querySelectorAll("button").forEach(function (button) {
      button.setAttribute("aria-pressed", String(button.dataset.layer === basemapLayer));
    });
    if (map) map.setMapType(basemapLayer === "satellite" ? BMAP_SATELLITE_MAP :
      basemapLayer === "hybrid" ? BMAP_HYBRID_MAP : BMAP_NORMAL_MAP);
  }
  function setSettingsStatus(message) { $("map-settings-status").textContent = message; }
  function loadSettingsUi() {
    return req(API + "/baidu/provider-settings").then(function (state) {
      $("map-key-clear").disabled = !state.configured;
      $("map-default-layer").value = state.default_layer || "normal";
      setSettingsStatus(state.status === "CONFIGURED" ? "已配置（AK 不回显）" :
        state.status === "CONFIG_UNREADABLE" ? "地图配置无法读取。" : "未配置百度地图，照片列表仍可用。");
    }).catch(function () { setSettingsStatus("地图设置读取失败。"); });
  }
  function connectSettingsUi() {
    settingsDialog = $("map-settings");
    $("map-settings-open").addEventListener("click", function () {
      loadSettingsUi().then(function () { settingsDialog.showModal(); $("map-key").focus(); });
    });
    settingsDialog.addEventListener("close", function () { $("map-settings-open").focus(); });
    $("map-default-layer").addEventListener("change", function () {
      var layer = this.value;
      mutateSettings("PUT", {default_layer: layer}).then(function () { setBaseLayer(layer); })
        .catch(function () { setSettingsStatus("默认地图保存失败。"); });
    });
    $("map-key-save").addEventListener("click", function () {
      var input = $("map-key"), key = input.value; input.value = "";
      if (!key) { setSettingsStatus("请输入 AK。" ); return; }
      mutateSettings("PUT", {ak: key}).then(function () {
        $("map-key-clear").disabled = false;
        setSettingsStatus("AK 已保存，正在打开地图。");
        settingsDialog.close();
        if (map) { location.reload(); return; }
        init();
      }).catch(function () { setSettingsStatus("AK 保存失败，请检查输入和本机存储。"); });
    });
    $("map-key-clear").addEventListener("click", function () {
      if (!clearArmed) { clearArmed = true; this.textContent = "再次点击确认清除";
        setTimeout(function () { clearArmed = false; $("map-key-clear").textContent = "清除 AK"; }, 5000); return; }
      clearArmed = false;
      mutateSettings("DELETE").then(function () { location.reload(); })
        .catch(function () { setSettingsStatus("清除失败，原配置保留。"); });
    });
    $("map-key-test").addEventListener("click", function () {
      var key = $("map-key").value || (runtimeConfig && runtimeConfig.ak);
      if (!key) { $("map-connection-status").textContent = "请先输入 AK。"; return; }
      $("map-connection-status").textContent = "正在加载 SDK；底图状态待检查。";
      window.AISEARCH_BAIDU.load(key).then(function () {
        $("map-connection-status").textContent = "SDK 已加载；请在地图显示后确认底图是否可用。";
      }).catch(function () { $("map-connection-status").textContent = "SDK 不可用，请检查 AK、来源限制和网络。"; });
    });
    if (new URLSearchParams(location.search).get("settings") === "1") {
      var clean = new URLSearchParams(location.search); clean.delete("settings");
      history.replaceState(null, "", location.pathname + (clean.toString() ? "?" + clean.toString() : "") + location.hash);
      loadSettingsUi().then(function () { settingsDialog.showModal(); });
    }
  }
  function bounds() { return engine ? engine.bounds() : null; }
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
    if (path === "/points" && map) { q.set("zoom", String(Math.round(map.getZoom()))); q.set("grid", "true"); }
    var query = q.toString(); return API + path + (query ? "?" + query : "");
  }
  function extent() { bbox = bounds(); return bbox; }
  function title(total) { $("grid-title").textContent = "附近候选范围 · " + total + " 张照片"; }
  function validPhotoCoordinates(coordinates) {
    if (!Array.isArray(coordinates) || coordinates.length < 2 || coordinates[0] === null || coordinates[0] === undefined || coordinates[1] === null || coordinates[1] === undefined || coordinates[0] === "" || coordinates[1] === "") return false;
    var longitude = Number(coordinates[0]), latitude = Number(coordinates[1]);
    return Number.isFinite(longitude) && Number.isFinite(latitude) && longitude >= -180 && longitude <= 180 && latitude >= -90 && latitude <= 90;
  }
  function hoverOwner(node) {
    var box = $("grid");
    while (node && node !== box) {
      if (node.__mapPhotoHover) return node;
      node = node.parentNode;
    }
    return null;
  }
  function setHoverMarker(owner) {
    var data = owner && owner.__mapPhotoHover, coordinates = data && data.coordinates;
    if (!map || !data || !validPhotoCoordinates(coordinates)) {
      if (hoverMarker) hoverMarker.hide();
      return;
    }
    var point = new BMap.Point(coordinates[0], coordinates[1]);
    if (!hoverMarker) { hoverMarker = new BMap.Marker(point); map.addOverlay(hoverMarker); }
    else hoverMarker.setPosition(point);
    hoverMarker.show();
  }
  function updateHoverMarker() {
    var owner = mouseHoverOwner || focusHoverOwner;
    setHoverMarker(owner);
    var box = $("grid");
    if (box && box.querySelectorAll) box.querySelectorAll(".tile, .map-cluster-member").forEach(function (node) {
      if (node.classList) node.classList.toggle("map-photo-hover-active", node === owner);
    });
  }
  function clearPhotoHover() {
    mouseHoverOwner = null; focusHoverOwner = null;
    updateHoverMarker();
  }
  function bindPhotoHover() {
    if (hoverEventsBound) return;
    var box = $("grid");
    if (!box) return;
    hoverEventsBound = true;
    box.addEventListener("pointerover", function (event) {
      if (event.pointerType !== "mouse" && event.pointerType !== "pen") return;
      var owner = hoverOwner(event.target), previous = hoverOwner(event.relatedTarget);
      if (owner && owner !== previous) { mouseHoverOwner = owner; updateHoverMarker(); }
    });
    box.addEventListener("pointerout", function (event) {
      if (event.pointerType !== "mouse" && event.pointerType !== "pen") return;
      var owner = hoverOwner(event.target), next = hoverOwner(event.relatedTarget);
      if (owner && owner !== next && mouseHoverOwner === owner) { mouseHoverOwner = null; updateHoverMarker(); }
    });
    box.addEventListener("focusin", function (event) {
      if (!keyboardFocusMode) return;
      var owner = hoverOwner(event.target);
      if (owner) { focusHoverOwner = owner; updateHoverMarker(); }
    });
    box.addEventListener("focusout", function (event) {
      var owner = hoverOwner(event.target), next = hoverOwner(event.relatedTarget);
      if (owner && owner !== next && focusHoverOwner === owner) { focusHoverOwner = null; updateHoverMarker(); }
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Tab") keyboardFocusMode = true;
    });
    document.addEventListener("pointerdown", function (event) {
      keyboardFocusMode = false;
      focusHoverOwner = null;
      if (event.pointerType === "touch") mouseHoverOwner = null;
      updateHoverMarker();
    });
  }
  function addPhotoHoverData(node, assetId, longitude, latitude) {
    node.__mapPhotoHover = { asset_id: assetId, coordinates: [longitude, latitude] };
  }
  function setOrderMode(value) {
    orderMode = value === "capture_time_desc" ? value : "asset_id_desc";
    $("map-order-default").setAttribute("aria-pressed", String(orderMode === "asset_id_desc"));
    $("map-order-capture").setAttribute("aria-pressed", String(orderMode === "capture_time_desc"));
    refresh();
  }
  function saveMapReturnSnapshot() {
    if (!map || version === null || !window.sessionStorage) return false;
    var center = map.getCenter(), zoom = Number(map.getZoom());
    var scroll = Number(window.scrollY || window.pageYOffset || 0);
    var snapshot = {
      version: version,
      center: [Number(center.lng), Number(center.lat)],
      zoom: zoom,
      order: orderMode,
      scroll: Math.max(0, isFinite(scroll) ? scroll : 0),
      viewOnly: true,
    };
    if (!snapshot.center.every(isFinite) || !isFinite(zoom) || zoom < 0 || zoom > 19) return false;
    try { window.sessionStorage.setItem(MAP_RETURN_KEY, JSON.stringify(snapshot)); return true; }
    catch (_) { return false; }
  }
  function drawGrid(data, reset) {
    var box = $("grid");
    if (reset) { clearPhotoHover(); box.textContent = ""; gridAssets = []; }
    (data.assets || []).forEach(function (a) {
      gridAssets.push(a);
      var tile = document.createElement("article"), img = document.createElement("img"), button = document.createElement("button"), imageButton = document.createElement("button");
      addPhotoHoverData(tile, a.asset_id, a.longitude, a.latitude);
      tile.className = "tile"; tile.dataset.assetId = String(a.asset_id); img.loading = "lazy"; img.src = a.thumbnail || ("/api/v1/assets/" + encodeURIComponent(a.asset_id) + "/thumbnail"); img.alt = "照片 " + a.asset_id;
      button.textContent = "查看照片"; button.className = "map-photo-action"; button.onclick = function () { openAsset(a.asset_id); };
      imageButton.type = "button"; imageButton.className = "map-photo-open"; imageButton.setAttribute("aria-label", "查看照片 " + a.asset_id); imageButton.onclick = button.onclick; imageButton.appendChild(img);
      tile.appendChild(imageButton); tile.appendChild(button);
      if (window.PhotoFavorites) tile.appendChild(window.PhotoFavorites.button(a.asset_id));
      box.appendChild(tile);
      if (a.capture_time && a.capture_time.taken_at) {
        var date = document.createElement("small");
        date.textContent = String(a.capture_time.taken_at).replace("T", " ");
        tile.appendChild(date);
      }
    });
    cursor = data.next_cursor;
    if (window.PhotoFavorites) window.PhotoFavorites.mount(box);
    $("more").hidden = !cursor && !clusterPaging;
    $("more").disabled = false;
    if (cellPaging) {
      $("grid-title").textContent = "聚合区域 · 共 " + cellPaging.total + " 张，已显示 " + gridAssets.length + " 张";
    }
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
      drawGrid(x, true);
      if (target) title(x.total); else $("grid-title").textContent = "全部带位置照片 · " + x.total + " 张";
    }).catch(function () {
      if (id === sequence) $("notice").textContent = "照片列表暂时无法读取，请重试。";
    });
  }
  function loadClusterLeaves() {}
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
      var restoreView = restoreState && restoreState.viewOnly && restoreState.version === version;
      var canRestore = restoreState && restoreState.version === version && sameBounds(restoreState.bbox, b);
      if (canRestore && restoreState.order === "capture_time_desc") {
        canRestore = cursorIsCurrent && (restoreState.cursor || sameCaptureAssets(restoreState.assets, assets.assets));
      }
      if (restoreView) {
        drawGrid(assets, true);
        window.scrollTo(0, Number(restoreState.scroll) || 0);
        restoreState = null;
      } else if (canRestore) {
        drawGrid({ assets: restoreState.assets || [], next_cursor: restoreState.cursor }, true);
        window.scrollTo(0, restoreState.scroll || 0); restoreState = null;
      } else {
        restoreState = null;
        drawGrid(assets, true);
      }
      if (engine) {
        engine.setData(points).catch(function (error) {
          if (id === sequence) {
            var code = error && typeof error.code === "string" && /^(timeout|exception|invalid_result(?:_(?:empty|status|points|count|coordinate))?|status_[0-9]{1,4})$/.test(error.code)
              ? error.code : "unknown";
            $("notice").textContent = "地图点暂时无法显示（" + code + "），照片列表仍可用。请稍后点击重试。";
            var retry = document.createElement("button");
            retry.type = "button"; retry.textContent = "重试地图点";
            retry.addEventListener("click", refresh, { once: true });
            $("notice").appendChild(retry);
          }
        });
      }
      title(points.total);
      if (orderMode === "capture_time_desc") {
        $("grid-title").textContent = "附近候选范围 · 按可信拍摄时间排序 · " + points.total + " 张照片";
      }
      $("notice").textContent = points.total ? "当前显示视野附近的候选照片，边缘范围可能存在偏差。" :
        (summaryTotal > 0 ? "当前候选范围没有带位置的照片。" : "当前图库没有带位置的照片。");
      applyFocusIntent();
    });
  }
  function refresh() {
    clearPhotoHover();
    cellPaging = null; clusterPaging = null;
    if (engine) engine.clear();
    var id = ++sequence, b = extent();
    $("grid-title").textContent = "正在同步照片列表…";
    $("more").disabled = false;
    fetchSnapshot(id, b, 0).catch(function () {
      if (id !== sequence) return;
      $("notice").textContent = "地图底图暂不可用，仍可通过照片列表浏览。";
      fallbackGrid(id, b);
    });
  }
  function openAsset(id) {
    clearPhotoHover();
    if (window.AIPhotoInfo) {
      window.AIPhotoInfo.open(id, { title: "查看地图照片", alt: "地图照片 #" + id, returnFocus: document.activeElement,
        onOpenFullDetails: function (assetId) {
          saveMapReturnSnapshot();
          window.AIPhotoInfo.navigateToDetails(assetId, "map");
        } });
      return;
    }
    location.href = "/#/asset/" + encodeURIComponent(id);
  }
  function openCell(cell, z, count) {
    clearPhotoHover();
    var id = sequence, b = bbox, expected = version;
    if (!b || !current(id, expected)) return;
    if (orderMode === "capture_time_desc") {
      orderMode = "asset_id_desc";
      $("map-order-default").setAttribute("aria-pressed", "true");
      $("map-order-capture").setAttribute("aria-pressed", "false");
    }
    clusterPaging = null; var selection = cellPaging = { id: cell, zoom: z, bbox: b, seq: id, version: expected, total: null };
    cursor = null; $("grid").textContent = ""; gridAssets = []; $("more").hidden = true;
    $("grid-title").textContent = "正在读取聚合照片…";
    var q = params(b); q.set("limit", PAGE); q.set("cell_id", cell); q.set("grid_zoom", z);
    req(API + "/assets?" + q.toString()).then(function (x) {
      if (cellPaging !== selection || selection.seq !== sequence || !current(id, expected)) return;
      if (x.member_version !== expected) { refresh(); return; }
      if (Number.isSafeInteger(Number(count)) && Number(count) !== x.total) {
        cellPaging = null; $("grid-title").textContent = "聚合数据已变化，正在刷新…"; refresh(); return;
      }
      selection.total = x.total; drawGrid(x, true);
    }).catch(function () {
      if (cellPaging !== selection || selection.seq !== sequence) return;
      cellPaging = null; $("grid-title").textContent = "聚合照片读取失败，请重新点击地图上的数量。";
    });
  }
  function schedule() {
    clearPhotoHover();
    sequence++; if (engine) engine.clear(); clearTimeout(timer); clusterPaging = null; cellPaging = null; cursor = null;
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
    map.centerAndZoom(new BMap.Point(intent.asset.longitude, intent.asset.latitude), 14);
    schedule();
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
    clearPhotoHover();
    clearTimeout(timer); version = null; cursor = null; clusterPaging = null; cellPaging = null;
    if (engine) { engine.clear(); try { engine.map.destroy(); } catch (_) {} engine = null; }
    map = null; $("notice").textContent = "地图底图暂不可用，仍可通过照片列表浏览。";
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
      return runtime().then(function (state) {
        if (id !== sequence) return;
        runtimeConfig = state; basemapLayer = state.default_layer || "normal";
        if (state.status !== "CONFIGURED" || !state.ak) { fallback(id); return; }
        return window.AISEARCH_BAIDU.load(state.ak).then(function () {
          if (id !== sequence) return;
          engine = window.AISEARCH_BAIDU_ENGINE.create("map", function (properties) {
            if (Number(properties.asset_count) === 1 && properties.asset_id) openAsset(properties.asset_id);
            else if (properties.cell_id) openCell(properties.cell_id, properties.grid_zoom, properties.asset_count);
          });
          map = engine.map;
          var liveMap = map, tileReady = false;
          map.addEventListener("firsttileload", function () {
            tileReady = true; $("map-connection-status").textContent = "SDK 已加载；底图已加载。";
          });
          setTimeout(function () {
            if (!tileReady && map === liveMap) {
              $("map-connection-status").textContent = "SDK 已加载；底图状态未确认，请查看地图。";
            }
          }, 12000);
          var center = [0, 20], zoom = 3, saved = null;
          if (s.bounds) {
            var west = s.bounds.west, east = s.bounds.east;
            var span = east < west ? east + 360 - west : east - west;
            center = [((west + span / 2 + 180) % 360 + 360) % 360 - 180,
              (s.bounds.south + s.bounds.north) / 2];
            zoom = s.total === 1 ? 12 : Math.min(8, Math.max(3, Math.log2(360 / Math.max(1, span)) - 0.7));
          }
          try { saved = JSON.parse(sessionStorage.getItem(MAP_RETURN_KEY) || "null"); } catch (_) {}
          try { sessionStorage.removeItem(MAP_RETURN_KEY); } catch (_) {}
          var savedCenter = saved && Array.isArray(saved.center) && saved.center.length >= 2
            ? [Number(saved.center[0]), Number(saved.center[1])] : null;
          var validSavedView = Boolean(saved && saved.version === s.member_version && savedCenter &&
            savedCenter.every(isFinite) && savedCenter[0] >= -180 && savedCenter[0] <= 180 &&
            savedCenter[1] >= -90 && savedCenter[1] <= 90 && isFinite(Number(saved.zoom)) &&
            Number(saved.zoom) >= 0 && Number(saved.zoom) <= 19);
          if (validSavedView) {
            center = savedCenter; zoom = Number(saved.zoom); restoreState = saved;
            orderMode = saved.order === "capture_time_desc" ? saved.order : "asset_id_desc";
            $("map-order-default").setAttribute("aria-pressed", String(orderMode === "asset_id_desc"));
            $("map-order-capture").setAttribute("aria-pressed", String(orderMode === "capture_time_desc"));
          }
          map.centerAndZoom(new BMap.Point(center[0], center[1]), Math.max(3, Math.round(zoom)));
          setBaseLayer(basemapLayer);
          map.addEventListener("moveend", schedule); map.addEventListener("zoomend", schedule);
          var focused = new URLSearchParams(location.search).get("asset_id");
          if (focused) { focusIntent = {assetId: focused, asset: null, version: null, attempts: 0}; requestFocusAsset(focusIntent); }
          refresh();
        }).catch(function () { if (id === sequence) fallback(id); });
      });
    }).catch(function () { if (id === sequence) fallback(id); });
  }
  function startMap() {
    window.addEventListener("aisearch-baidu-auth-error", function () {
      $("map-connection-status").textContent = "百度地图授权失败，请检查 AK 和来源限制。";
      fallback(++sequence);
    });
    bindPhotoHover();
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
            runtimeConfig.default_layer = state.default_layer; setBaseLayer(layer);
          }).catch(function () { $("notice").textContent = "底图偏好保存失败，请重试。"; });
        } else if (!runtimeConfig || runtimeConfig.status !== "CONFIGURED") {
          $("notice").textContent = "请先在地图设置中配置百度 AK。";
          $("map-settings-open").click();
        }
      });
    });
    init();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", startMap); else startMap();
})();
