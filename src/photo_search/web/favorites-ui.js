/* Bounded, page-local favorite state and photo batch selection controls. */
(function () {
  "use strict";
  var API = "/api/v1";
  var roots = new WeakMap();
  var latestRevision = 0;
  var selectionRoots = new WeakMap();

  function csrfToken() {
    var match = document.cookie.match(/(?:^|;\s*)ps_csrf=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }
  function jsonRequest(url, options) {
    options = options || {};
    options.credentials = "same-origin";
    options.headers = Object.assign({ "Content-Type": "application/json" }, options.headers || {});
    return fetch(API + url, options).then(function (response) {
      return response.json().then(function (body) {
        if (!response.ok) { var error = new Error(body.message || body.detail || "请求失败"); error.status = response.status; throw error; }
        return body;
      });
    });
  }
  function paint(button, value, busy) {
    button.favoriteState = value;
    button.disabled = Boolean(busy) || value === null;
    button.textContent = value === true ? "★" : "☆";
    button.setAttribute("aria-pressed", value === true ? "true" : "false");
    button.setAttribute("aria-label", value === null ? "正在读取收藏状态" : value ? "取消收藏" : "收藏这张照片");
    button.title = value === true ? "取消收藏" : "收藏这张照片";
  }
  function favoriteButton(assetId) {
    var button = document.createElement("button");
    button.type = "button"; button.className = "favorite-star"; button.dataset.assetId = String(assetId);
    button.dataset.favoriteId = String(assetId); button.setAttribute("aria-pressed", "false");
    paint(button, null, false);
    button.addEventListener("click", function (event) {
      event.stopPropagation();
      if (button.favoriteState === null) { var state = roots.get(button.favoriteRoot); if (state) readStates(state, [button]); return; }
      if (button.favoriteBusy) return;
      var desired = !button.favoriteState, id = button.dataset.favoriteId;
      var state = roots.get(button.favoriteRoot);
      button.favoriteBusy = true; paint(button, button.favoriteState, true);
      jsonRequest("/assets/" + encodeURIComponent(id) + "/favorite", {
        method: desired ? "PUT" : "DELETE", headers: { "X-CSRF-Token": csrfToken() }, body: "{}"
      }).then(function (body) {
        var revision = Number(body.favorites_revision) || 0;
        latestRevision = Math.max(latestRevision, revision);
        if (state) {
          state.revisions.set(id, revision);
          state.values.set(id, { isFavorite: Boolean(body.is_favorite), revision: revision });
          state.buttons.forEach(function (other) { if (other.dataset.favoriteId === id && other.isConnected) paint(other, body.is_favorite, false); });
          if (typeof state.onChange === "function") state.onChange(body);
        } else paint(button, body.is_favorite, false);
      }).catch(function () {
        if (!state) { button.favoriteBusy = false; paint(button, null, false); return; }
        state.revisions.delete(id);
        state.values.delete(id);
        state.buttons.forEach(function (other) { if (other.dataset.favoriteId === id && other.isConnected) paint(other, null, false); });
        readStates(state, [button]).then(function () { if (typeof state.onError === "function") state.onError(id); });
      }).finally(function () { button.favoriteBusy = false; });
    });
    return button;
  }
  function readStates(state, buttons) {
    var unique = Array.from(new Set(buttons.filter(function (button) { return button.isConnected; }).map(function (button) { return button.dataset.favoriteId; })));
    if (!unique.length) return Promise.resolve();
    var pending = unique.filter(function (id) { return state.revisions.get(id) === undefined && !state.inFlight.has(id); });
    var chunks = [];
    while (pending.length) chunks.push(pending.splice(0, 100));
    return Promise.all(chunks.map(function (ids) {
      var requestEpoch = state.epoch;
      var stale = false;
      ids.forEach(function (id) { state.inFlight.add(id); });
      return jsonRequest("/favorites/states?" + ids.map(function (id) { return "asset_ids=" + encodeURIComponent(id); }).join("&"))
        .then(function (body) {
          var revision = Number(body.favorites_revision) || 0;
          if (requestEpoch !== state.epoch) return;
          if (revision < latestRevision) { stale = true; return; }
          latestRevision = Math.max(latestRevision, revision);
          var favorites = new Set((body.favorite_asset_ids || []).map(String));
          var visibleIds = new Set(Array.from(state.buttons).filter(function (button) {
            return button.isConnected && state.root.contains(button);
          }).map(function (button) { return button.dataset.favoriteId; }));
          ids.forEach(function (id) {
            if (!visibleIds.has(id)) return;
            var isFavorite = favorites.has(id);
            state.revisions.set(id, revision);
            state.values.set(id, { isFavorite: isFavorite, revision: revision });
            state.buttons.forEach(function (button) {
              if (button.isConnected && button.dataset.favoriteId === id) paint(button, isFavorite, false);
            });
          });
        }).catch(function () {
          ids.forEach(function (id) { state.buttons.forEach(function (button) {
            if (button.isConnected && button.dataset.favoriteId === id) {
              button.disabled = false; button.textContent = "☆"; button.setAttribute("aria-label", "收藏状态读取失败，点击重试");
            }
          }); });
        }).finally(function () {
          ids.forEach(function (id) { state.inFlight.delete(id); });
          if (stale || requestEpoch !== state.epoch) schedule(state);
        });
    }));
  }
  function mount(root, onChange, onError, known) {
    if (!root) return;
    var state = roots.get(root);
    if (!state) {
      state = { root: root, buttons: new Set(), revisions: new Map(), values: new Map(), inFlight: new Set(), epoch: 0, onChange: onChange, onError: onError, queued: false };
      roots.set(root, state);
      var observer = new MutationObserver(function () { schedule(state); });
      observer.observe(root, { childList: true, subtree: true }); state.observer = observer;
    }
    state.onChange = onChange || state.onChange; state.onError = onError || state.onError;
    Array.from(state.buttons).forEach(function (button) {
      if (!button.isConnected || !root.contains(button)) state.buttons.delete(button);
    });
    var fresh = Array.from(root.querySelectorAll(".favorite-star"));
    fresh.forEach(function (button) {
      button.favoriteRoot = root;
      if (!state.buttons.has(button)) {
        state.buttons.add(button);
        var cached = state.values.get(button.dataset.favoriteId);
        if (cached) paint(button, cached.isFavorite, false); else paint(button, null, false);
      }
    });
    if (known && Array.isArray(known.ids)) {
      known.ids.forEach(function (rawId) {
        var id = String(rawId), revision = Number(known.revision) || 0;
        state.revisions.set(id, revision);
        state.values.set(id, { isFavorite: Boolean(known.isFavorite), revision: revision });
        state.buttons.forEach(function (button) {
          if (button.isConnected && button.dataset.favoriteId === id) paint(button, Boolean(known.isFavorite), false);
        });
      });
    }
    state.revisions.forEach(function (_revision, id) {
      if (!Array.from(state.buttons).some(function (button) { return button.dataset.favoriteId === id && button.isConnected; })) {
        state.revisions.delete(id);
        state.values.delete(id);
      }
    });
    readStates(state, fresh);
  }
  function schedule(state) {
    if (state.queued) return;
    state.queued = true;
    Promise.resolve().then(function () { state.queued = false; mount(state.root, state.onChange, state.onError); });
  }
  function invalidate(root) {
    var state = root && roots.get(root);
    if (!state) return;
    state.epoch += 1; state.revisions.clear(); state.values.clear();
    Array.from(state.buttons).forEach(function (button) { if (button.isConnected) paint(button, null, false); });
    mount(root, state.onChange, state.onError);
  }
  function createBatchSelection(root, controls, onSuccess) {
    var state = { root: root, controls: controls, ids: new Set(), anchor: null, active: false, busy: false, observer: null, message: "" };
    selectionRoots.set(root, state);
    function currentItems() { return Array.from(root.children || []).filter(function (item) { return item.dataset && item.dataset.assetId; }); }
    function sync() {
      currentItems().forEach(function (item) {
        var id = String(item.dataset.assetId), box = item.querySelector(".photo-select-checkbox");
        if (!box) {
          box = document.createElement("input"); box.type = "checkbox"; box.className = "photo-select-checkbox";
          box.setAttribute("aria-label", "选择照片 " + id); item.appendChild(box);
          box.addEventListener("click", function (event) {
            if (event.shiftKey) { event.preventDefault(); toggle(id, true); }
          });
          box.addEventListener("change", function () { if (!state.busy) toggle(id, false, box.checked); });
        }
        box.hidden = !state.active; box.checked = state.ids.has(id); box.disabled = !state.active || state.busy;
        item.dataset.selected = String(state.ids.has(id));
      });
      root.classList.toggle("photo-selecting", state.active);
      controls.toggle.setAttribute("aria-pressed", String(state.active));
      controls.toggle.textContent = state.active ? "完成选择" : "选择照片";
      controls.toolbar.hidden = !state.active;
      controls.count.textContent = state.message || ("已选 " + state.ids.size + " / 500 张");
      [controls.all, controls.invert, controls.clear, controls.add, controls.remove].forEach(function (button) {
        button.disabled = state.busy || ((button === controls.add || button === controls.remove) && state.ids.size === 0);
      });
      controls.toggle.disabled = state.busy;
    }
    function toggle(id, shift, explicit) {
      if (!state.active || state.busy) return;
      state.message = "";
      id = String(id);
      var items = currentItems(), index = items.findIndex(function (item) { return String(item.dataset.assetId) === id; });
      if (shift && state.anchor !== null && index >= 0) {
        var anchorIndex = items.findIndex(function (item) { return String(item.dataset.assetId) === state.anchor; });
        if (anchorIndex >= 0) {
          var range = items.slice(Math.min(index, anchorIndex), Math.max(index, anchorIndex) + 1).map(function (item) { return String(item.dataset.assetId); });
          var additions = range.filter(function (assetId) { return !state.ids.has(assetId); });
          if (state.ids.size + additions.length > 500) { state.message = "这次选择会超过 500 张，已有选择未更改。"; sync(); return; }
          additions.forEach(function (assetId) { state.ids.add(assetId); });
        } else if (!state.ids.has(id)) {
          if (state.ids.size >= 500) { state.message = "一次最多选择 500 张，已有选择未更改。"; sync(); return; }
          state.ids.add(id);
        }
      } else {
        var want = typeof explicit === "boolean" ? explicit : !state.ids.has(id);
        if (want && !state.ids.has(id) && state.ids.size >= 500) { state.message = "一次最多选择 500 张，已有选择未更改。"; sync(); return; }
        if (want) state.ids.add(id); else state.ids.delete(id);
      }
      state.anchor = id; sync();
    }
    root.addEventListener("click", function (event) {
      if (!state.active) return;
      if (event.target.closest && event.target.closest(".favorite-star, .photo-select-checkbox")) return;
      var item = event.target.closest ? event.target.closest("[data-asset-id]") : null;
      if (!item || !root.contains(item)) return;
      while (item.parentNode && item.parentNode !== root) item = item.parentNode;
      if (event.target.closest && event.target.closest("button:not(.thumb-frame)")) return;
      event.preventDefault(); event.stopPropagation(); event.stopImmediatePropagation();
      toggle(item.dataset.assetId, event.shiftKey);
    }, true);
    controls.toggle.addEventListener("click", function () {
      if (state.busy) return;
      state.active = !state.active;
      state.message = "";
      if (!state.active) { state.ids.clear(); state.anchor = null; }
      sync();
    });
    controls.all.addEventListener("click", function () {
      state.message = "";
      var ids = currentItems().map(function (item) { return String(item.dataset.assetId); });
      var additions = ids.filter(function (id) { return !state.ids.has(id); });
      if (state.ids.size + additions.length > 500) { state.message = "全选当前项会超过 500 张，已有选择未更改。"; sync(); return; }
      additions.forEach(function (id) { state.ids.add(id); }); sync();
    });
    controls.invert.addEventListener("click", function () {
      state.message = "";
      var ids = currentItems().map(function (item) { return String(item.dataset.assetId); });
      var next = new Set(state.ids); ids.forEach(function (id) { if (next.has(id)) next.delete(id); else next.add(id); });
      if (next.size > 500) { state.message = "反选当前项会超过 500 张，已有选择未更改。"; sync(); return; }
      state.ids = next; sync();
    });
    controls.clear.addEventListener("click", function () { state.ids.clear(); state.anchor = null; state.message = ""; sync(); });
    function submit(target) {
      if (state.busy || !state.ids.size) return;
      var ids = Array.from(state.ids, Number);
      state.busy = true; sync();
      jsonRequest("/favorites/batch", { method: "POST", headers: { "X-CSRF-Token": csrfToken() }, body: JSON.stringify({ asset_ids: ids, is_favorite: target }) })
        .then(function (body) {
          latestRevision = Math.max(latestRevision, Number(body.favorites_revision) || 0);
          state.ids.clear(); state.anchor = null;
          state.message = (body.is_favorite ? "已加入收藏 " : "已取消收藏 ") + body.changed + " 张，" + body.unchanged + " 张已是目标状态。";
          if (typeof onSuccess === "function") onSuccess(body);
          invalidate(root);
        }).catch(function (error) {
          state.message = "批量操作失败，选择已保留，可重试：" + error.message;
          invalidate(root);
        }).finally(function () { state.busy = false; sync(); });
    }
    controls.add.addEventListener("click", function () { submit(true); });
    controls.remove.addEventListener("click", function () { submit(false); });
    root.ownerDocument.addEventListener("keydown", function (event) {
      if (!state.active || state.busy || event.key !== "Escape" || root.ownerDocument.querySelector("dialog[open]")) return;
      state.active = false; state.ids.clear(); state.anchor = null; sync();
    });
    var observer = new MutationObserver(function () { sync(); });
    observer.observe(root, { childList: true }); state.observer = observer;
    sync();
    return {
      reset: function () { state.ids.clear(); state.anchor = null; state.active = false; state.message = ""; sync(); },
      submit: submit,
      get selected() { return new Set(state.ids); }
    };
  }
  window.PhotoFavorites = { button: favoriteButton, mount: mount, invalidate: invalidate };
  window.PhotoBatchSelect = { create: createBatchSelection };
})();
