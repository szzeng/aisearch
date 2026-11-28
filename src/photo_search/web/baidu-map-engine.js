/* Baidu map adapter. GPS input stays WGS84; only visible grid centers are converted. */
(function () {
  "use strict";
  function wrap(value) { return ((value + 180) % 360 + 360) % 360 - 180; }
  function candidateBounds(map) {
    var bounds = map.getBounds(), sw = bounds.getSouthWest(), ne = bounds.getNorthEast();
    if (![sw.lng, sw.lat, ne.lng, ne.lat].every(Number.isFinite)) throw new Error("地图范围不可用");
    var span = ne.lng - sw.lng;
    if (span < 0) span += 360;
    var south = Math.max(-90, sw.lat - 0.1), north = Math.min(90, ne.lat + 0.1);
    if (span + 0.2 >= 360) return { west: -180, south: south, east: 180, north: north };
    return { west: wrap(sw.lng - 0.1), south: south, east: wrap(ne.lng + 0.1), north: north };
  }
  function create(container, onSelect) {
    var map = new window.BMap.Map(container), layer, generation = 0, icons = new Map();
    function icon(properties) {
      var count = Math.max(1, Number(properties.asset_count) || 1), key = String(count);
      if (icons.has(key)) return icons.get(key);
      var canvas = document.createElement("canvas"), ctx;
      canvas.width = canvas.height = 48; ctx = canvas.getContext("2d");
      ctx.fillStyle = count === 1 ? "#dc3545" : "#2463eb";
      ctx.beginPath(); ctx.arc(24, 24, count === 1 ? 9 : 20, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = "white"; ctx.lineWidth = 2; ctx.stroke();
      if (count > 1) { ctx.fillStyle = "white"; ctx.font = "bold 13px sans-serif";
        ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(key, 24, 24); }
      var image = { canvas: canvas, id: "map-count-" + key };
      icons.set(key, image); return image;
    }
    layer = new window.BMap.PointLayer({ enablePicked: true, icon: icon, iconSize: [48, 48] });
    map.addLayer(layer);
    layer.addEventListener("click", function (event) {
      var properties = event && event.value && event.value.properties;
      if (properties) onSelect(properties);
    });
    function setData(data) {
      var token = ++generation, features = data.features || [];
      layer.clearData(); icons.clear();
      if (features.length > 1000) return Promise.reject(new Error("地图网格超出上限"));
      return window.AISEARCH_BAIDU.convertPoints(features.map(function (f) { return f.geometry.coordinates; }),
        function () { return token === generation; })
        .then(function (points) {
          if (token !== generation) return false;
          layer.setData({ type: "FeatureCollection", features: features.map(function (f, index) {
            return { type: "Feature", geometry: { type: "Point", coordinates: points[index] }, properties: f.properties };
          }) });
          return true;
        });
    }
    function clear() { generation++; layer.clearData(); icons.clear(); }
    return { map: map, setData: setData, clear: clear, bounds: function () { return candidateBounds(map); } };
  }
  window.AISEARCH_BAIDU_ENGINE = Object.freeze({ create: create, candidateBounds: candidateBounds });
})();
