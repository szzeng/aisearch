/* Small, page-owned infinite-scroll trigger. Query state and cursor validation stay in callers. */
(function () {
  "use strict";

  function create(options) {
    var opts = options || {};
    var sentinel = opts.sentinel;
    var button = opts.button || null;
    var observer = null;
    var inFlight = false;
    var blocked = false;
    var enabled = true;
    var nearEnd = false;
    var scrollDemand = false;
    var initialBudget = 2;
    var disposed = false;
    var listening = false;
    var buttonListening = false;

    function canLoad() {
      return !disposed && enabled && !inFlight && !blocked &&
        typeof opts.canLoad === "function" && opts.canLoad();
    }

    function updateButton() {
      if (!button) return;
      var hasPage = typeof opts.canLoad === "function" && opts.canLoad();
      button.hidden = !enabled || !hasPage || (Boolean(observer) && !blocked);
      button.textContent = blocked ? (opts.retryLabel || "重试加载") : (opts.loadLabel || button.textContent);
    }

    function run(manual) {
      if (manual) blocked = false;
      if (!canLoad()) return Promise.resolve(false);
      if (!manual && !scrollDemand && initialBudget <= 0) return Promise.resolve(false);
      if (scrollDemand) scrollDemand = false;
      else if (initialBudget > 0) initialBudget -= 1;
      inFlight = true;
      updateButton();
      var result;
      try { result = opts.load(); }
      catch (_error) { result = false; }
      return Promise.resolve(result).then(function (ok) {
        if (ok === false) blocked = true;
        return ok !== false;
      }, function () {
        blocked = true;
        return false;
      }).then(function (ok) {
        inFlight = false;
        updateButton();
        if (typeof opts.onSettled === "function") opts.onSettled(ok);
        maybeRun();
        return ok;
      });
    }

    function maybeRun() {
      if (nearEnd && canLoad() && (scrollDemand || initialBudget > 0)) run(false);
    }

    function onScroll() {
      if (disposed || !enabled) return;
      initialBudget = 0;
      scrollDemand = true;
      maybeRun();
    }

    function onButtonClick() { run(true); }

    function connect() {
      if (!sentinel || observer || typeof IntersectionObserver !== "function") return;
      observer = new IntersectionObserver(function (entries) {
        nearEnd = entries.some(function (entry) { return entry.target === sentinel && entry.isIntersecting; });
        maybeRun();
      }, { rootMargin: "800px 0px" });
      observer.observe(sentinel);
    }

    function disconnect() {
      if (observer) observer.disconnect();
      observer = null;
      nearEnd = false;
    }

    function listen() {
      if (listening) return;
      window.addEventListener("scroll", onScroll, { passive: true });
      listening = true;
      if (button && !buttonListening) {
        button.addEventListener("click", onButtonClick);
        buttonListening = true;
      }
    }

    function unlisten() {
      if (listening) window.removeEventListener("scroll", onScroll);
      listening = false;
      if (button && buttonListening) button.removeEventListener("click", onButtonClick);
      buttonListening = false;
    }

    function reset() {
      blocked = false;
      initialBudget = 2;
      scrollDemand = false;
      nearEnd = false;
      updateButton();
    }

    connect();
    listen();
    updateButton();

    return {
      update: function () { updateButton(); maybeRun(); },
      reset: function () {
        reset();
        disconnect();
        if (enabled) connect();
      },
      setEnabled: function (value) {
        enabled = Boolean(value);
        if (enabled) { connect(); listen(); }
        else { disconnect(); unlisten(); scrollDemand = false; }
        updateButton();
      },
      retry: function () { return run(true); },
      dispose: function () {
        disposed = true;
        disconnect();
        unlisten();
      },
      getState: function () { return { inFlight: inFlight, blocked: blocked, initialBudget: initialBudget }; },
    };
  }

  function trimGrid(list, maximum) {
    if (!list || !list.children) return 0;
    var tiles = Array.prototype.filter.call(list.children, function (child) {
      return child && child.hasAttribute && child.hasAttribute("data-asset-id");
    });
    var excess = tiles.length - Math.max(1, Number(maximum) || 500);
    if (excess <= 0) return 0;
    var cutoff = tiles[excess - 1].getBoundingClientRect ? tiles[excess - 1].getBoundingClientRect().top : null;
    if (cutoff !== null) {
      while (excess < tiles.length && tiles[excess].getBoundingClientRect &&
          Math.abs(tiles[excess].getBoundingClientRect().top - cutoff) < 1) excess += 1;
    }
    var firstRect = tiles[0].getBoundingClientRect ? tiles[0].getBoundingClientRect() : null;
    var lastRect = tiles[excess - 1].getBoundingClientRect ? tiles[excess - 1].getBoundingClientRect() : null;
    var removedHeight = firstRect && lastRect ? Math.max(0, lastRect.bottom - firstRect.top) : 0;
    var spacer = list.querySelector && list.querySelector(":scope > .scroll-pager-spacer");
    if (!spacer) {
      spacer = document.createElement("li");
      spacer.className = "scroll-pager-spacer";
      spacer.setAttribute("aria-hidden", "true");
      spacer.style.gridColumn = "1 / -1";
      spacer.style.pointerEvents = "none";
      spacer.style.padding = "0";
      spacer.style.margin = "0";
      spacer.style.border = "0";
      spacer.style.height = "0px";
      list.insertBefore(spacer, tiles[0]);
    }
    spacer.style.height = (parseFloat(spacer.style.height) || 0) + removedHeight + "px";
    for (var index = 0; index < excess; index += 1) {
      var tile = tiles[index];
      var frames = tile.querySelectorAll ? tile.querySelectorAll("img") : [];
      Array.prototype.forEach.call(frames, function (img) {
        if (img.getAttribute("src")) img.removeAttribute("src");
      });
      tile.remove();
    }
    return excess;
  }

  function getSpacerHeight(list) {
    var spacer = list && list.querySelector && list.querySelector(":scope > .scroll-pager-spacer");
    return spacer ? (parseFloat(spacer.style.height) || 0) : 0;
  }

  function setSpacerHeight(list, height) {
    if (!list || !list.children) return;
    var pixels = Math.max(0, Number(height) || 0);
    var spacer = list.querySelector && list.querySelector(":scope > .scroll-pager-spacer");
    if (!pixels) {
      if (spacer) spacer.remove();
      return;
    }
    if (!spacer) {
      spacer = document.createElement("li");
      spacer.className = "scroll-pager-spacer";
      spacer.setAttribute("aria-hidden", "true");
      spacer.style.gridColumn = "1 / -1";
      spacer.style.pointerEvents = "none";
      spacer.style.padding = "0";
      spacer.style.margin = "0";
      spacer.style.border = "0";
      var firstTile = Array.prototype.find.call(list.children, function (child) {
        return child && child.hasAttribute && child.hasAttribute("data-asset-id");
      });
      list.insertBefore(spacer, firstTile || null);
    }
    spacer.style.height = pixels + "px";
  }

  window.PhotoScrollPager = { create: create, trimGrid: trimGrid, getSpacerHeight: getSpacerHeight, setSpacerHeight: setSpacerHeight };
})();
