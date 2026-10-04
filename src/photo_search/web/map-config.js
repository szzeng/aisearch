/* Central V1 display provider. Storage and member queries stay WGS84. */
window.AISEARCH_MAP_CONFIG = Object.freeze({
  provider: "osm",
  tile_url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
  attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
  display_crs: "WGS84",
});
