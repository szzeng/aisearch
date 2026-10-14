/* Public map templates only. The user's key is supplied at runtime in page memory. */
(function () {
  "use strict";
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
