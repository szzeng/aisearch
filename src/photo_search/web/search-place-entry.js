(function () {
  "use strict";
  var input = document.getElementById("query-text");
  var host = document.getElementById("place-entry");
  if (!input || !host) return;
  var timer = null;
  function element(tag, text) { var node = document.createElement(tag); if (text) node.textContent = text; return node; }
  function search(value) {
    if (value.length < 2) { host.hidden = true; host.replaceChildren(); return; }
    host.hidden = false; host.replaceChildren(element("p", "查找地点入口…"));
    Promise.all(["city", "province", "district", "country"].map(function (kind) {
      return fetch("/api/v1/places/albums?" + new URLSearchParams({ kind: kind, name: value }), { credentials: "same-origin", headers: { Accept: "application/json" } }).then(function (response) { if (!response.ok) throw new Error("地点查找暂不可用"); return response.json(); });
    })).then(function (responses) {
      host.replaceChildren();
      var found = [];
      responses.forEach(function (body) { (body.albums || []).forEach(function (album) { found.push(album); }); });
      if (!found.length) { host.appendChild(element("p", "没有地点相册匹配；普通文字/语义搜索仍按原规则执行。")); return; }
      host.appendChild(element("strong", "地点相册入口（不改变普通搜索）"));
      found.slice(0, 8).forEach(function (album) {
        var link = element("a", album.name + (album.parent ? " · " + album.parent : "") + " · " + album.total + " 张");
        link.href = "/places?album_kind=" + encodeURIComponent(album.kind) + "&album_key=" + encodeURIComponent(album.key);
        var preview = element("img"); preview.loading = "lazy"; preview.alt = ""; preview.src = "/api/v1/assets/" + encodeURIComponent(album.cover_asset_id) + "/thumbnail";
        link.prepend(preview); host.appendChild(link);
      });
    }).catch(function () { host.replaceChildren(element("p", "地点入口暂不可用；普通搜索保持原样。")); });
  }
  input.addEventListener("input", function () { window.clearTimeout(timer); var value = input.value.trim(); timer = window.setTimeout(function () { search(value); }, 240); });
})();
