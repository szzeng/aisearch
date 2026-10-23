/* Small renderer helpers for the memories view; state and authenticated requests belong to app.js. */
(function () {
  "use strict";
  function element(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }
  function card(item, onOpen) {
    var button = element("button", "memory-card card-tile");
    button.type = "button";
    button.dataset.memoryKey = item.memory_type + "-" + item.year;
    button.setAttribute("aria-label", item.title + "，精选 " + item.selected_count + " 张");
    var image = element("img", "thumbnail");
    image.src = "/api/v1/assets/" + encodeURIComponent(item.cover_asset_id) + "/thumbnail";
    image.alt = ""; image.loading = "lazy"; image.decoding = "async";
    button.appendChild(image);
    var copy = element("span", "memory-card__copy");
    copy.appendChild(element("strong", "", item.title));
    copy.appendChild(element("small", "", "精选 " + item.selected_count + " 张"));
    button.appendChild(copy);
    button.addEventListener("click", function () { onOpen(item); });
    return button;
  }
  function tile(asset, onOpen) {
    var button = element("button", "card-tile collection-asset-open");
    button.type = "button";
    button.setAttribute("aria-label", "查看照片 #" + asset.asset_id);
    var image = element("img", "thumbnail");
    image.src = "/api/v1/assets/" + encodeURIComponent(asset.asset_id) + "/thumbnail";
    image.alt = "照片 #" + asset.asset_id; image.loading = "lazy"; image.decoding = "async";
    image.addEventListener("error", function () { image.alt = "缩略图当前不可用，照片 #" + asset.asset_id; });
    button.appendChild(image);
    button.appendChild(element("span", "tile-label", asset.is_favorite ? "★ 收藏" : "照片 #" + asset.asset_id));
    button.addEventListener("click", function () { onOpen(asset); });
    return button;
  }
  window.PhotoMemoriesUI = { card: card, tile: tile, element: element };
})();
