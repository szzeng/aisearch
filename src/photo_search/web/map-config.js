/* Public map templates only. The user's key is supplied at runtime in page memory. */
(function () {
  "use strict";
  var baiduPromise = null, baiduKey = null;
  var conversionTail = Promise.resolve();
  // In-memory only; exact inputs avoid approximation and repeated service calls.
  var conversionCache = new Map(), conversionCacheLimit = 2000;
  function rememberConversion(key, value) {
    conversionCache.delete(key); conversionCache.set(key, value);
    if (conversionCache.size > conversionCacheLimit) conversionCache.delete(conversionCache.keys().next().value);
  }
  function loadBaidu(ak) {
    if (!ak) return Promise.reject(new Error("地图 AK 未配置"));
    if (baiduPromise) {
      if (baiduKey !== ak) return Promise.reject(new Error("地图 AK 已更改，请重新打开页面"));
      return baiduPromise;
    }
    baiduKey = ak;
    baiduPromise = new Promise(function (resolve, reject) {
      var script = document.createElement("script"), settled = false;
      var timeout = setTimeout(function () { finish(false); }, 15000);
      function finish(ok) {
        if (settled) return;
        settled = true; clearTimeout(timeout); delete window.__aisearchBaiduReady;
        script.onerror = null;
        if (ok && window.BMap && window.BMAP_COORD_WGS84) {
          window.BMap.coordType = window.BMAP_COORD_WGS84;
          resolve(window.BMap);
        } else {
          script.remove(); baiduPromise = null; baiduKey = null;
          reject(new Error("百度地图 SDK 不可用"));
        }
      }
      window.__aisearchBaiduReady = function () { finish(true); };
      window.BMap_authError = function () {
        if (!settled) finish(false);
        if (typeof window.dispatchEvent === "function" && typeof Event === "function") {
          window.dispatchEvent(new Event("aisearch-baidu-auth-error"));
        }
      };
      script.onerror = function () { finish(false); };
      script.src = "https://api.map.baidu.com/api?v=4.0&ak=" + encodeURIComponent(ak) + "&callback=__aisearchBaiduReady";
      document.head.appendChild(script);
    });
    return baiduPromise;
  }
  function convertBaiduPoints(points, isCurrent) {
    if (!window.BMap || !Array.isArray(points) || points.length > 1000) {
      return Promise.reject(new Error("地图坐标转换不可用"));
    }
    if (points.some(function (p) {
      return !Array.isArray(p) || p.length !== 2 || !Number.isFinite(p[0]) ||
        !Number.isFinite(p[1]) || Math.abs(p[0]) > 180 || Math.abs(p[1]) > 90;
    })) return Promise.reject(new Error("地图坐标无效"));
    var converter = new window.BMap.Convertor(), output = new Array(points.length), missing = [];
    points.forEach(function (p, index) {
      var key = p[0] + "," + p[1], cached = conversionCache.get(key);
      if (cached) { output[index] = cached.slice(); rememberConversion(key, cached); }
      else missing.push({ key: key, index: index, point: p });
    });
    function next(offset) {
      if (isCurrent && !isCurrent()) return Promise.reject(new Error("地图视野已更新"));
      if (offset >= missing.length) return Promise.resolve(output);
      var entries = missing.slice(offset, offset + 10);
      var batch = entries.map(function (item) { return new window.BMap.Point(item.point[0], item.point[1]); });
      function attempt() {
        if (isCurrent && !isCurrent()) return Promise.reject(new Error("地图视野已更新"));
        return new Promise(function (resolve, reject) {
          var settled = false, timeout = setTimeout(function () { finish(null, "timeout"); }, 10000);
          function finish(result, failure) {
            if (settled) return;
            settled = true; clearTimeout(timeout);
            var code = failure;
            if (!code && !result) code = "invalid_result_empty";
            else if (!code && result.status !== 0) {
              code = Number.isInteger(result.status) ? "status_" + result.status : "invalid_result_status";
            } else if (!code && !Array.isArray(result.points)) code = "invalid_result_points";
            else if (!code && result.points.length !== batch.length) code = "invalid_result_count";
            else if (!code && result.points.some(function (p) {
              return !p || !Number.isFinite(p.lng) || !Number.isFinite(p.lat) ||
                Math.abs(p.lng) > 180 || Math.abs(p.lat) > 90;
            })) code = "invalid_result_coordinate";
            if (code) {
              var error = new Error("地图坐标转换失败"); error.code = code;
              reject(error); return;
            }
            resolve(result.points.map(function (p) { return [p.lng, p.lat]; }));
          }
          try { converter.translate(batch, 1, 5, finish); } catch (_) { finish(null, "exception"); }
        });
      }
      var operation = conversionTail.then(function () {
        return attempt().catch(function (error) {
          if (!error.code || error.code.indexOf("invalid_result_") !== 0) throw error;
          return new Promise(function (resolve) { setTimeout(resolve, 250); }).then(attempt);
        }).then(function (converted) {
          converted.forEach(function (point, index) {
            output[entries[index].index] = point;
            rememberConversion(entries[index].key, point.slice());
          });
        });
      });
      conversionTail = operation.catch(function () {});
      return operation.then(function () { return next(offset + entries.length); });
    }
    return next(0);
  }
  window.AISEARCH_BAIDU = Object.freeze({ load: loadBaidu, convertPoints: convertBaiduPoints });
  var HOST = "https://t0.tianditu.gov.cn/DataServer";
  var ATTRIBUTION = '© <a href="https://www.tianditu.gov.cn/">天地图</a> 国家地理信息公共服务平台 · 审图号：GS（2025）1508号';
  function tileUrl(layer, key) {
    return HOST + "?T=" + layer + "&x={x}&y={y}&l={z}&tk=" + encodeURIComponent(key);
  }
  function sources(key) {
    return {
      "td-vec": { type: "raster", tiles: [tileUrl("vec_w", key)], tileSize: 256, maxzoom: 18, attribution: ATTRIBUTION },
      "td-cva": { type: "raster", tiles: [tileUrl("cva_w", key)], tileSize: 256, maxzoom: 18, attribution: ATTRIBUTION },
      "td-img": { type: "raster", tiles: [tileUrl("img_w", key)], tileSize: 256, maxzoom: 18, attribution: ATTRIBUTION },
      "td-cia": { type: "raster", tiles: [tileUrl("cia_w", key)], tileSize: 256, maxzoom: 18, attribution: ATTRIBUTION }
    };
  }
  function layerIds(layer) { return layer === "imagery" ? ["td-img-layer", "td-cia-layer"] : ["td-vec-layer", "td-cva-layer"]; }
  function testTileUrl(key) { return HOST + "?T=vec_w&x=0&y=0&l=1&tk=" + encodeURIComponent(key); }
  function buildStyle(key, layer) {
    if (!key) return { version: 8, sources: {}, layers: [] };
    var src = sources(key), visible = layerIds(layer);
    return { version: 8, sources: src, layers: [
      { id: "td-vec-layer", type: "raster", source: "td-vec", minzoom: 0, maxzoom: 18, layout: { visibility: visible.indexOf("td-vec-layer") >= 0 ? "visible" : "none" } },
      { id: "td-cva-layer", type: "raster", source: "td-cva", minzoom: 0, maxzoom: 18, layout: { visibility: visible.indexOf("td-cva-layer") >= 0 ? "visible" : "none" } },
      { id: "td-img-layer", type: "raster", source: "td-img", minzoom: 0, maxzoom: 18, layout: { visibility: visible.indexOf("td-img-layer") >= 0 ? "visible" : "none" } },
      { id: "td-cia-layer", type: "raster", source: "td-cia", minzoom: 0, maxzoom: 18, layout: { visibility: visible.indexOf("td-cia-layer") >= 0 ? "visible" : "none" } }
    ] };
  }
  window.AISEARCH_MAP_CONFIG = Object.freeze({ provider: "tianditu", attribution: ATTRIBUTION,
    sourceTemplates: sources, layerIds: layerIds, buildStyle: buildStyle, testTileUrl: testTileUrl });
})();
