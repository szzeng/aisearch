/* One-shot handoff from a page's current photo scope to the shared search page. */
(function () {
  "use strict";

  function validScope(scope) {
    if (!scope || typeof scope !== "object") return false;
    if (scope.kind === "favorites") return true;
    if (scope.kind === "person") return Number.isSafeInteger(Number(scope.person_id)) && Number(scope.person_id) > 0;
    if (scope.kind === "collection") return Number.isSafeInteger(Number(scope.collection_id)) && Number(scope.collection_id) > 0 &&
      Boolean(String(scope.instance_token || "")) && Number.isSafeInteger(Number(scope.revision)) && Number(scope.revision) >= 0;
    if (scope.kind === "photo_collection") return Number.isSafeInteger(Number(scope.photo_collection_id)) && Number(scope.photo_collection_id) > 0 &&
      Boolean(String(scope.instance_token || "")) && Number.isSafeInteger(Number(scope.revision)) && Number(scope.revision) >= 1;
    if (scope.kind === "place") return Boolean(String(scope.place_kind || "")) && String(scope.place_key || "") !== "";
    if (scope.kind === "source") return Number.isSafeInteger(Number(scope.source_id)) && Number(scope.source_id) > 0;
    return false;
  }

  function configure(options) {
    var button = options.button;
    var input = options.input;
    var getScope = options.getScope;
    if (!button || !input || typeof getScope !== "function") return function () {};
    var notice = document.getElementById("notice");
    var active = false;
    button.type = "button";
    button.hidden = true;
    button.addEventListener("click", function () {
      var scope = getScope();
      if (!active || !validScope(scope)) {
        button.hidden = true;
        active = false;
        return;
      }
      var query = String(input.value || "").trim();
      if (query.length > 256) query = query.slice(0, 256);
      var token = window.crypto && window.crypto.randomUUID
        ? window.crypto.randomUUID()
        : Date.now().toString(36) + "-" + Math.random().toString(36).slice(2) + "-" + Math.random().toString(36).slice(2);
      var payload = { version: 2, scope: scope, query: query, execute: true };
      try {
        window.sessionStorage.setItem("aisearch.search-scope.v1:" + token, JSON.stringify(payload));
      } catch (_error) {
        if (notice) {
          notice.textContent = "无法暂存搜索范围，请检查浏览器会话存储；范围搜索没有执行。";
          notice.hidden = false;
        }
        return;
      }
      window.location.href = "/search?scope_entry=" + encodeURIComponent(token);
    });
    return function (scope, label) {
      active = validScope(scope);
      button.hidden = !active;
      button.textContent = active
        ? (scope && scope.kind === "favorites" ? "搜索收藏" : "搜索此" + String(label || "相册"))
        : "";
    };
  }

  window.SearchScopeEntry = { configure: configure, validScope: validScope };
})();
