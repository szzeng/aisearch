/* Shared, bounded photo timeline. Pages plug in fetchPage/renderAsset/onOpen adapters. */
(function (root) {
  "use strict";

  var PAGE_SIZE = 50;
  var ACTIVE_PAGES = 8;
  var DETAIL_PAGES = 10;
  var ANCHOR_PAGES = 64;
  var API = "/api/v1";
  var PREFERENCE_KEY = "photo-timeline.preferences.v1";

  function readPreferences() {
    try {
      var value = JSON.parse(root.localStorage.getItem(PREFERENCE_KEY) || "null");
      return {
        timeBasis: value && (value.timeBasis === "capture" || value.timeBasis === "library") ? value.timeBasis : "capture",
        groupBy: value && (value.groupBy === "day" || value.groupBy === "month" || value.groupBy === "year") ? value.groupBy : "day",
      };
    } catch (_) {
      return { timeBasis: "capture", groupBy: "day" };
    }
  }

  function writePreferences(state) {
    try {
      root.localStorage.setItem(PREFERENCE_KEY, JSON.stringify({ timeBasis: state.basis, groupBy: state.group }));
    } catch (_) { /* Storage may be unavailable; this page can continue without persistence. */ }
  }

  function element(tag, className, value) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (value !== undefined) node.textContent = String(value);
    return node;
  }

  function apiFetch(path, signal) {
    var token = "";
    String(document.cookie || "").split(";").forEach(function (part) {
      var piece = part.trim();
      if (piece.indexOf("ps_csrf=") === 0) token = decodeURIComponent(piece.slice(8));
    });
    return fetch(path, { credentials: "same-origin", signal: signal,
      headers: token ? { "X-CSRF-Token": token } : {} }).then(function (response) {
      return response.json().then(function (body) {
        if (!response.ok) {
          var error = new Error(body.message || "时间轴请求失败");
          error.code = body.code || "REQUEST_FAILED";
          error.status = response.status;
          throw error;
        }
        return body;
      });
    });
  }

  function create(options) {
    var host = options.host;
    var adapter = options.adapter || {};
    var preferences = readPreferences();
    var restore = options.restoreView || {};
    var restoreBucket = ["all", "capture", "unknown", "pending"].indexOf(restore.bucket) >= 0 ? restore.bucket : "all";
    var state = {
      generation: 0, periodsGeneration: 0, disposed: false, busy: false, failed: false,
      stalePaused: false, staleAnchor: null, lastOpenParams: null, assetsRevision: null, periodsRevision: null,
      revisionMismatch: false,
      expectedRestoreRevision: restore.catalogFilterRevision === null || restore.catalogFilterRevision === undefined
        ? null : String(restore.catalogFilterRevision),
      restoreMessage: "",
      cursor: null, hasMore: true, pageNumber: 0, pages: [],
      active: [], detailCache: [], anchorCache: [],
      basis: options.timeBasis || restore.timeBasis || preferences.timeBasis,
      group: options.groupBy || restore.groupBy || preferences.groupBy,
      bucket: restore.timeBasis && restore.timeBasis !== (options.timeBasis || restore.timeBasis || preferences.timeBasis) ? "all" : restoreBucket,
      year: restore.year === null || restore.year === undefined ? null : Number(restore.year),
      month: restore.month === null || restore.month === undefined ? null : Number(restore.month),
      scope: options.scope || { kind: "all" },
      scopeViews: {},
      seekYear: null, seekMonth: null,
      observer: null, request: null, restoredAssetId: options.returnAssetId || restore.assetId || null, lastGroupToken: null,
    };
    host.classList.add("photo-timeline");
    var controls = element("div", "photo-timeline__controls");
    var basis = element("select", "photo-timeline__basis");
    [["capture", "拍摄时间"], ["library", "加入图库时间"]].forEach(function (pair) {
      var opt = element("option", "", pair[1]); opt.value = pair[0]; basis.append(opt);
    });
    basis.value = state.basis;
    var grouping = element("select", "photo-timeline__group");
    [["day", "按日"], ["month", "按月"], ["year", "按年"]].forEach(function (pair) {
      var opt = element("option", "", pair[1]); opt.value = pair[0]; grouping.append(opt);
    });
    grouping.value = state.group;
    var bucketGroup = element("div", "photo-timeline__buckets");
    bucketGroup.setAttribute("role", "group");
    bucketGroup.setAttribute("aria-label", "日期范围快捷入口");
    var bucketButtons = {};
    [["all", "全部照片"], ["capture", "可信拍摄日期"], ["unknown", "日期未知"], ["pending", "日期待就绪"], ["library", "按加入图库时间浏览"]].forEach(function (pair) {
      var button = element("button", "photo-timeline__bucket", pair[1]);
      button.type = "button";
      button.dataset.bucket = pair[0];
      button.setAttribute("aria-pressed", "false");
      bucketButtons[pair[0]] = button;
      bucketGroup.append(button);
    });
    var year = element("select", "photo-timeline__year"); year.setAttribute("aria-label", "筛选年份");
    var month = element("select", "photo-timeline__month"); month.setAttribute("aria-label", "筛选月份");
    var filter = element("button", "", "筛选年月"); filter.type = "button";
    var jump = element("button", "", "定位到年月"); jump.type = "button";
    var summaryRetry = element("button", "photo-timeline__summary-retry", "重试日期摘要");
    summaryRetry.type = "button"; summaryRetry.hidden = true;
    var summaryStatus = element("p", "photo-timeline__summary-status");
    summaryStatus.setAttribute("role", "status"); summaryStatus.setAttribute("aria-live", "polite");
    var status = element("p", "photo-timeline__status"); status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
    var groups = element("div", "photo-timeline__groups"); groups.setAttribute("aria-live", "polite");
    var sentinel = element("div", "photo-timeline__sentinel"); sentinel.setAttribute("aria-hidden", "true");
    var retry = element("button", "photo-timeline__retry", "重试加载"); retry.type = "button"; retry.hidden = true;
    var next = element("button", "photo-timeline__next", "加载更多"); next.type = "button"; next.hidden = true;
    controls.append(basis, grouping, year, month, filter, jump, summaryRetry);
    host.replaceChildren(controls, bucketGroup, summaryStatus, status, groups, sentinel, retry, next);

    function scopeParams() {
      var params = new URLSearchParams({ kind: state.scope.kind || "all", time_basis: state.basis,
        group_by: state.group, bucket: state.bucket, limit: String(PAGE_SIZE) });
      ["source_id", "person_id", "collection_id", "place_kind", "place_key"].forEach(function (key) {
        if (state.scope[key] !== undefined && state.scope[key] !== null) params.set(key, String(state.scope[key]));
      });
      if (state.year !== null) params.set("year", String(state.year));
      if (state.month !== null) params.set("month", String(state.month));
      if (state.seekYear !== null) params.set("seek_year", String(state.seekYear));
      if (state.seekMonth !== null) params.set("seek_month", String(state.seekMonth));
      return params;
    }

    function setMessage(message, isError) {
      status.textContent = message || "";
      status.classList.toggle("is-error", Boolean(isError));
    }

    function scopeKey(scope) {
      var value = scope || {};
      return [value.kind || "all", value.source_id || "", value.person_id || "", value.collection_id || "",
        value.place_kind || "", value.place_key || ""].join(":");
    }

    function visibleAnchor() {
      var cards = Array.prototype.slice.call(host.querySelectorAll(".photo-timeline__card"));
      var visible = cards.map(function (card) {
        return { card: card, rect: card.getBoundingClientRect() };
      }).filter(function (item) { return item.rect.bottom > 0 && item.rect.top < window.innerHeight; })
        .sort(function (a, b) { return a.rect.top - b.rect.top; })[0];
      return visible ? { anchor: visible.card.dataset.anchor || "", assetId: visible.card.dataset.assetId || "" } : null;
    }

    function saveScopeView() {
      var visible = visibleAnchor();
      state.scopeViews[scopeKey(state.scope)] = {
        basis: state.basis, bucket: state.bucket, year: state.year, month: state.month,
        seekYear: state.seekYear, seekMonth: state.seekMonth,
        anchor: visible && visible.anchor, assetId: visible && visible.assetId,
      };
    }

    function updateBucketButtons(counts) {
      var values = counts || {};
      var total = values.effective_total;
      var labels = {
        all: "全部照片" + (total === undefined ? "" : " · " + total),
        capture: "可信拍摄日期" + (values.trusted === undefined ? "" : " · " + values.trusted),
        unknown: "日期未知 · 加入图库日期" + (values.unknown === undefined ? "" : " · " + values.unknown),
        pending: "日期待就绪 · 加入图库日期" + (values.pending === undefined ? "" : " · " + values.pending),
        library: "按加入图库时间浏览" + (total === undefined ? "" : " · " + total),
      };
      Object.keys(bucketButtons).forEach(function (key) {
        var button = bucketButtons[key];
        button.textContent = labels[key];
        var active = key === "library" ? state.basis === "library" : state.basis === "capture" && state.bucket === key;
        button.setAttribute("aria-pressed", active ? "true" : "false");
      });
    }

    function activateBucket(value) {
      state.year = null; state.month = null; state.seekYear = null; state.seekMonth = null;
      if (value === "library") { state.basis = "library"; state.bucket = "all"; }
      else { state.basis = "capture"; state.bucket = value; }
      basis.value = state.basis;
      year.value = "";
      month.replaceChildren();
      writePreferences(state);
      updateBucketButtons();
      reset();
      loadPeriods(null);
    }

    Object.keys(bucketButtons).forEach(function (key) {
      bucketButtons[key].addEventListener("click", function () { activateBucket(key); });
    });
    updateBucketButtons();

    function updateRevisionStatus() {
      if (state.assetsRevision !== null && state.periodsRevision !== null &&
          String(state.assetsRevision) !== String(state.periodsRevision)) {
        state.revisionMismatch = true;
        updateBucketButtons({});
        summaryStatus.textContent = "日期摘要与照片列表版本不一致，摘要暂不可用。";
        summaryRetry.hidden = false;
        return false;
      }
      state.revisionMismatch = false;
      return true;
    }

    function responseRevision(body) {
      var place = body && body.place_revisions;
      return place ? JSON.stringify([body.catalog_filter_revision, place.suggestion_revision, place.package_revision])
        : String(body && body.catalog_filter_revision);
    }

    function updateMonths(months) {
      var selected = state.month === null ? "" : String(state.month);
      month.replaceChildren();
      var any = element("option", "", "全部月份"); any.value = ""; month.append(any);
      (months || []).slice().sort(function (a, b) { return Number(b.month) - Number(a.month); }).forEach(function (item) {
        var option = element("option", "", String(item.month).padStart(2, "0") + "月 · " + item.count);
        option.value = String(item.month); month.append(option);
      });
      month.value = selected;
    }

    function loadPeriods(selectedYear) {
      var params = new URLSearchParams({ kind: state.scope.kind || "all", time_basis: state.basis });
      ["source_id", "person_id", "collection_id", "place_kind", "place_key"].forEach(function (key) {
        if (state.scope[key] !== undefined && state.scope[key] !== null) params.set(key, String(state.scope[key]));
      });
      if (selectedYear) params.set("year", String(selectedYear));
      var generation = ++state.periodsGeneration;
      state.periodsRevision = null;
      summaryRetry.hidden = true;
      summaryStatus.textContent = "正在读取日期摘要…";
      return apiFetch(API + "/timeline/periods?" + params.toString()).then(function (body) {
        if (state.disposed || generation !== state.periodsGeneration) return;
        state.periodsRevision = responseRevision(body);
        var sameRevision = updateRevisionStatus();
        if (!selectedYear) {
          var keep = state.year === null ? "" : String(state.year);
          year.replaceChildren();
          var allYears = element("option", "", "全部年份"); allYears.value = ""; year.append(allYears);
          (body.years || []).forEach(function (item) {
            var option = element("option", "", String(item.year) + "年 · " + item.count);
            option.value = String(item.year); year.append(option);
          });
          year.value = keep;
          updateMonths([]);
        } else updateMonths(body.months || []);
        updateBucketButtons(sameRevision ? body.counts || {} : {});
        if (sameRevision && body.counts) {
          if (state.basis === "capture") {
            summaryStatus.textContent = "当前范围 " + body.counts.effective_total + " 张 · 可信拍摄日期 " + body.counts.trusted +
              " · 日期未知 " + body.counts.unknown + " · 日期待就绪 " + body.counts.pending;
          } else {
            summaryStatus.textContent = "当前范围 " + body.counts.effective_total + " 张，按加入图库时间浏览。";
          }
        }
        if (typeof adapter.onPeriods === "function") adapter.onPeriods(body, state.scope);
      }).catch(function (error) {
        if (!state.disposed && generation === state.periodsGeneration) {
          summaryRetry.hidden = false;
          summaryStatus.textContent = "日期摘要暂不可用：" + error.message;
        }
      });
    }

    function renderAsset(item) {
      var card;
      if (typeof adapter.renderAsset === "function") {
        card = adapter.renderAsset(item);
      } else {
        card = element("button", "photo-timeline__card"); card.type = "button";
        var image = element("img", "", ""); image.alt = "照片 " + item.asset_id; image.loading = "lazy";
        image.decoding = "async"; image.src = item.thumbnail_url;
        var label = element("span", "photo-timeline__date", item.display_time || item.time_label);
        var source = element("small", "photo-timeline__source", item.time_label || "");
        card.append(image, label, source);
      }
      if (typeof adapter.onOpen === "function") {
        card.addEventListener("click", function (event) {
          var control = event.target !== card && event.target.closest
            ? event.target.closest("button, a, input, select, textarea, summary, details") : null;
          if (control && control !== card) return;
          adapter.onOpen(item, card);
        });
      }
      return card;
    }

    function groupLabel(item) {
      if (!item.group_key) return item.date_state === "unknown" ? "日期未知 · 按加入图库日期" : "日期待就绪 · 按加入图库日期";
      if (state.group === "year") return item.group_key + "年";
      if (state.group === "month") return item.group_key.replace("-", "年") + "月";
      return item.group_key.slice(0, 4) + "年" + item.group_key.slice(5, 7) + "月" + item.group_key.slice(8, 10) + "日";
    }

    function releasePage(page) {
      if (!page || page.released) return;
      page.height = Math.max(page.section.getBoundingClientRect().height, page.height || 1);
      var pageNodes = Array.prototype.slice.call(groups.querySelectorAll(".photo-timeline__page"));
      var pageIndex = pageNodes.indexOf(page.section);
      var followingPage = pageIndex >= 0 ? state.pages.find(function (candidate) {
        return candidate.section === pageNodes[pageIndex + 1];
      }) : null;
      if (followingPage) {
        Array.prototype.slice.call(page.section.querySelectorAll(".photo-timeline__heading")).forEach(function (ownedHeading) {
          var token = ownedHeading.dataset.group;
          if (token !== followingPage.firstGroupToken || followingPage.headingTokens.indexOf(token) >= 0) return;
          var heading = element("h3", "photo-timeline__heading", ownedHeading.textContent);
          heading.dataset.group = token;
          followingPage.section.insertBefore(heading, followingPage.section.firstChild);
          followingPage.headingTokens.push(token);
        });
      }
      page.section.className = "photo-timeline__spacer";
      page.section.setAttribute("aria-label", "照片节点已回收；选择可按日期锚点重新定位");
      var restore = element("button", "photo-timeline__restore", "重新定位此时间段");
      restore.type = "button";
      restore.addEventListener("click", function () { if (page.firstAnchor) reset(page.firstAnchor); });
      page.section.replaceChildren(restore);
      page.section.style.height = Math.ceil(page.height) + "px";
      page.released = true;
      page.assets = null;
      state.active = state.active.filter(function (value) { return value !== page; });
      state.pages = state.pages.filter(function (value) { return value !== page; });
      coalesceSpacers();
    }

    function coalesceSpacers() {
      var blocks = Array.prototype.slice.call(groups.querySelectorAll(".photo-timeline__spacer"));
      for (var index = 1; index < blocks.length; index += 1) {
        var previous = blocks[index - 1]; var current = blocks[index];
        if (previous.nextElementSibling !== current) continue;
        var total = Number(previous.style.height.replace("px", "")) + Number(current.style.height.replace("px", ""));
        previous.style.height = Math.ceil(total) + "px";
        var second = current.querySelector("button");
        if (second && !previous.querySelector("button")) previous.append(second);
        current.remove();
      }
    }

    function trimPages(focusPage) {
      var keep = state.active.slice();
      while (keep.length > ACTIVE_PAGES) {
        var focused = document.activeElement;
        var candidates = keep.filter(function (page) { return !page.section.contains(focused); });
        candidates.sort(function (a, b) { return Math.abs(b.number - focusPage.number) - Math.abs(a.number - focusPage.number); });
        var victim = candidates[0] || keep[0];
        keep = keep.filter(function (page) { return page !== victim; });
        releasePage(victim);
      }
      var activeNumbers = state.active.map(function (page) { return page.number; });
      state.pages.forEach(function (page) {
        if (page && !page.released && activeNumbers.indexOf(page.number) < 0) releasePage(page);
      });
      state.detailCache = state.detailCache.slice(-DETAIL_PAGES);
      state.detailCache.forEach(function (cached) {
        if (cached.number < focusPage.number - DETAIL_PAGES + 1) cached.cacheAssets = null;
      });
      state.anchorCache = state.anchorCache.slice(-ANCHOR_PAGES);
    }

    function appendPage(body, generation) {
      if (state.disposed || generation !== state.generation) return;
      var section = element("section", "photo-timeline__page");
      section.dataset.page = String(state.pageNumber + 1);
      var page = { number: state.pageNumber + 1, section: section, assets: body.assets || [], height: 0,
        released: false, firstAnchor: body.assets && body.assets[0] ? body.assets[0].anchor : null,
        lastAnchor: body.assets && body.assets.length ? body.assets[body.assets.length - 1].anchor : null,
        firstGroupToken: null, headingTokens: [],
        cacheAssets: body.assets || [] };
      var lastGroup = state.lastGroupToken;
      page.assets.forEach(function (item, index) {
        var label = groupLabel(item);
        var groupToken = item.group_key || item.date_state;
        if (index === 0) page.firstGroupToken = groupToken;
        if (groupToken !== lastGroup) {
          var heading = element("h3", "photo-timeline__heading", label);
          heading.dataset.group = groupToken;
          section.append(heading);
          page.headingTokens.push(groupToken);
        }
        lastGroup = groupToken;
        var card = renderAsset(item);
        card.dataset.assetId = String(item.asset_id);
        card.dataset.anchor = String(item.anchor || "");
        section.append(card);
      });
      if (state.request && state.request.replacingPage) {
        var existing = state.request.replacingPage;
        groups.replaceChild(section, existing.section);
        state.pages = state.pages.filter(function (value) { return value !== existing; });
        state.request.replacingPage = null;
      } else groups.append(section);
      state.pageNumber += 1;
      state.pages.push(page); state.active.push(page); state.detailCache.push(page);
      state.anchorCache.push({ number: page.number, first: page.firstAnchor, last: page.lastAnchor, height: section.getBoundingClientRect().height });
      state.cursor = body.next_cursor || null; state.hasMore = Boolean(body.next_cursor);
      state.assetsRevision = responseRevision(body);
      updateRevisionStatus();
      state.seekYear = null; state.seekMonth = null;
      state.lastGroupToken = lastGroup;
      state.failed = false; retry.hidden = true; next.hidden = !state.hasMore;
      trimPages(page);
      return page;
    }

    function fetchNext() {
      if (state.disposed || state.busy || !state.hasMore) return Promise.resolve(null);
      state.busy = true; next.disabled = true; retry.hidden = true;
      var generation = state.generation;
      var params = scopeParams();
      if (state.cursor) params.set("cursor", state.cursor);
      var controller = typeof AbortController === "function" ? new AbortController() : null;
      state.request = { controller: controller, replacingPage: null };
      if (typeof adapter.onRequest === "function") adapter.onRequest(Object.fromEntries(params.entries()));
      var request = adapter.fetchPage ? adapter.fetchPage(Object.fromEntries(params.entries()), controller && controller.signal) : apiFetch(API + "/timeline/assets?" + params.toString(), controller && controller.signal);
      return Promise.resolve(request).then(function (body) {
        var page = appendPage(body, generation);
        if (page && typeof adapter.onPage === "function") adapter.onPage(body, page, Object.fromEntries(params.entries()));
        return page;
      }).catch(function (error) {
        if (state.disposed || generation !== state.generation || (error && error.name === "AbortError")) return null;
        if (error && error.code === "CATALOG_FILTER_REVISION_STALE") {
          state.cursor = null; state.hasMore = false; state.stalePaused = true;
          var lastPage = state.active.slice().sort(function (a, b) { return a.number - b.number; }).pop();
          state.staleAnchor = lastPage ? lastPage.lastAnchor : null;
          state.failed = false; next.hidden = true; retry.hidden = false;
          retry.textContent = "重新定位时间轴";
          setMessage("照片日期或成员已更新。已暂停续页；点击“重新定位时间轴”重新打开当前范围。", true);
        } else {
          state.failed = true; retry.hidden = false; setMessage("加载失败：" + error.message, true);
        }
        return null;
      }).finally(function () {
        if (generation === state.generation) {
          state.busy = false; next.disabled = false; state.request = null;
        }
      });
    }

    function reset(keepAnchor, openingParams) {
      var oldAnchor = keepAnchor || null;
      state.generation += 1;
      if (state.request && state.request.controller) state.request.controller.abort();
      state.busy = false; state.failed = false; state.stalePaused = false; state.staleAnchor = null;
      state.revisionMismatch = false;
      state.cursor = null; state.hasMore = true;
      state.pageNumber = 0; state.pages = []; state.active = []; state.detailCache = []; state.anchorCache = [];
      state.lastGroupToken = null; state.assetsRevision = null;
      groups.replaceChildren(); next.hidden = true; retry.hidden = true;
      retry.textContent = "重试加载";
      setMessage("", false);
      state.request = { controller: null, replacingPage: null };
      var params = openingParams ? new URLSearchParams(openingParams) : scopeParams();
      if (oldAnchor) params.set("anchor", oldAnchor);
      else params.delete("anchor");
      state.lastOpenParams = params.toString();
      var generation = state.generation;
      var controller = typeof AbortController === "function" ? new AbortController() : null;
      state.request.controller = controller;
      state.busy = true;
      if (typeof adapter.onRequest === "function") adapter.onRequest(Object.fromEntries(params.entries()));
      var request = adapter.fetchPage ? adapter.fetchPage(Object.fromEntries(params.entries()), controller && controller.signal) : apiFetch(API + "/timeline/assets?" + params.toString(), controller && controller.signal);
      return Promise.resolve(request).then(function (body) {
        if (state.expectedRestoreRevision !== null &&
            String(responseRevision(body)) !== state.expectedRestoreRevision) {
          // A cross-mount snapshot is only a hint. If its catalog revision is
          // stale, discard its filters and anchor and reopen the authoritative
          // first page for this scope instead of silently replaying old state.
          state.expectedRestoreRevision = null;
          state.bucket = "all"; state.year = null; state.month = null;
          state.seekYear = null; state.seekMonth = null; state.restoredAssetId = null;
          year.value = ""; month.value = "";
          updateBucketButtons();
          state.restoreMessage = "当前地点相册已更新，已重新读取最新照片范围。";
          loadPeriods(null);
          return reset(null);
        }
        // The snapshot revision only guards the initial cross-mount restore.
        // Later resets belong to the user's current view and must not be
        // mistaken for another attempt to replay the old snapshot.
        state.expectedRestoreRevision = null;
        var page = appendPage(body, generation);
        if (state.restoredAssetId && page) {
          var target = page.section.querySelector('[data-asset-id="' + CSS.escape(String(state.restoredAssetId)) + '"]');
          if (typeof adapter.onRestoreAsset === "function") adapter.onRestoreAsset(target || null, state.restoredAssetId);
          else if (target) target.scrollIntoView({ block: "center" });
          state.restoredAssetId = null;
        }
        updateRevisionStatus();
        if (!status.classList.contains("is-error")) setMessage(state.restoreMessage, false);
        if (page && typeof adapter.onPage === "function") adapter.onPage(body, page, Object.fromEntries(params.entries()));
        return page;
      }).catch(function (error) {
        if (!state.disposed && generation === state.generation) { state.failed = true; retry.hidden = false; setMessage("无法打开时间轴：" + error.message, true); }
        return null;
      }).finally(function () {
        if (generation === state.generation) { state.busy = false; state.request = null; next.disabled = false; }
      });
    }

    function selectedYear() { return year.value ? Number(year.value) : null; }
    function selectedMonth() { return month.value ? Number(month.value) : null; }
    function reloadForFilters() {
      state.year = selectedYear(); state.month = selectedMonth();
      saveScopeView();
      reset();
      loadPeriods(state.year);
    }

    function preserveVisibleAssetOnResize() {
      var cards = Array.prototype.slice.call(host.querySelectorAll(".photo-timeline__card"));
      var reference = cards.map(function (card) { return { card: card, rect: card.getBoundingClientRect() }; })
        .filter(function (entry) { return entry.rect.bottom > 0 && entry.rect.top < window.innerHeight; })
        .sort(function (a, b) { return Math.abs(a.rect.top) - Math.abs(b.rect.top); })[0];
      if (!reference) return;
      var id = reference.card.dataset.assetId;
      var oldTop = reference.rect.top;
      window.requestAnimationFrame(function () {
        var current = host.querySelector('.photo-timeline__card[data-asset-id="' + CSS.escape(id) + '"]');
        if (!current || state.disposed) return;
        var delta = current.getBoundingClientRect().top - oldTop;
        if (Math.abs(delta) > 1) window.scrollBy(0, delta);
      });
    }

    basis.addEventListener("change", function () {
      state.basis = basis.value; state.bucket = "all";
      state.year = null; state.month = null; state.seekYear = null; state.seekMonth = null;
      writePreferences(state);
      updateBucketButtons();
      reset();
      loadPeriods(null);
    });
    grouping.addEventListener("change", function () { state.group = grouping.value; writePreferences(state); reset(); });
    year.addEventListener("change", function () { state.month = null; month.value = ""; saveScopeView(); loadPeriods(selectedYear()); });
    month.addEventListener("change", function () {});
    filter.addEventListener("click", reloadForFilters);
    jump.addEventListener("click", function () {
      var targetYear = selectedYear();
      var targetMonth = selectedMonth();
      if (targetYear === null) { setMessage("先选择要定位的年份。", true); return; }
      state.year = null; state.month = null;
      state.seekYear = targetYear; state.seekMonth = targetMonth;
      state.bucket = state.basis === "capture" ? "capture" : "all";
      updateBucketButtons();
      reset();
      loadPeriods(targetYear);
    });
    summaryRetry.addEventListener("click", function () {
      if (!state.revisionMismatch) return loadPeriods(state.year);
      var anchor = visibleAnchor();
      var page = reset(anchor && anchor.anchor);
      var periods = loadPeriods(null).then(function () {
        return state.year === null ? null : loadPeriods(state.year);
      });
      return Promise.all([periods, page]).then(function (results) { return results[1]; });
    });
    retry.addEventListener("click", function () {
      if (state.stalePaused) {
        var periods = loadPeriods(null).then(function () {
          return state.year === null ? null : loadPeriods(state.year);
        });
        var page = reset(state.staleAnchor, state.lastOpenParams);
        return Promise.all([periods, page]).then(function (results) { return results[1]; });
      }
      return fetchNext();
    });
    next.addEventListener("click", fetchNext);
    sentinel.addEventListener("click", function () { if (state.hasMore && !state.busy) fetchNext(); });
    if (typeof IntersectionObserver === "function") {
      state.observer = new IntersectionObserver(function (entries) {
        if (entries.some(function (entry) { return entry.isIntersecting; })) fetchNext();
      }, { root: options.scrollRoot || null, rootMargin: "900px 0px" });
      state.observer.observe(sentinel);
    }
    window.addEventListener("resize", preserveVisibleAssetOnResize, { passive: true });
    var onHostScroll = function () {
      if (state.disposed || state.busy || typeof adapter.onScroll !== "function") return;
      adapter.onScroll({ host: host, state: state, reset: reset });
    };
    host.addEventListener("scroll", onHostScroll, { passive: true });
    reset(options.returnAnchor || restore.anchor || null);
    loadPeriods(null).then(function () {
      if (state.year !== null) return loadPeriods(state.year);
      return null;
    });

    return {
      render: reset,
      append: fetchNext,
      reset: reset,
      setScope: function (scope, anchor, assetId) {
        saveScopeView();
        state.scope = Object.assign({}, scope || { kind: "all" });
        var saved = state.scopeViews[scopeKey(state.scope)];
        var sameBasis = Boolean(saved && saved.basis === state.basis);
        state.bucket = state.basis === "capture" && sameBasis && ["all", "capture", "unknown", "pending"].indexOf(saved.bucket) >= 0
          ? saved.bucket : "all";
        state.year = sameBasis ? saved.year : null;
        state.month = sameBasis ? saved.month : null;
        state.seekYear = sameBasis ? saved.seekYear : null;
        state.seekMonth = sameBasis ? saved.seekMonth : null;
        year.value = state.year === null ? "" : String(state.year);
        month.value = state.month === null ? "" : String(state.month);
        state.restoredAssetId = assetId || (sameBasis && saved.assetId ? saved.assetId : null);
        updateBucketButtons();
        var page = reset(anchor || (sameBasis ? saved.anchor : null));
        var periods = loadPeriods(null).then(function () {
          return state.year === null ? null : loadPeriods(state.year);
        });
        return Promise.all([periods, page]).then(function (results) { return results[1]; });
      },
      setReturnAnchor: function (assetId, anchor) { state.restoredAssetId = assetId || null; return reset(anchor); },
      dispose: function () {
        state.disposed = true; state.generation += 1;
        if (state.request && state.request.controller) state.request.controller.abort();
        if (state.observer) state.observer.disconnect();
        window.removeEventListener("resize", preserveVisibleAssetOnResize);
        host.removeEventListener("scroll", onHostScroll);
        state.pages.forEach(releasePage); state.pages = []; state.active = [];
        host.replaceChildren(); host.classList.remove("photo-timeline");
      },
      snapshot: function () { var visible = visibleAnchor(); return { generation: state.generation, inFlight: state.busy ? 1 : 0,
        timeBasis: state.basis, groupBy: state.group, bucket: state.bucket, year: state.year, month: state.month,
        catalogFilterRevision: state.assetsRevision,
        scope: Object.assign({}, state.scope),
        anchor: visible && visible.anchor || null, assetId: visible && visible.assetId || null,
        activePages: state.active.length, detailPages: state.detailCache.length,
        anchorPages: state.anchorCache.length, loadedPages: state.pageNumber }; },
    };
  }

  root.PhotoTimeline = { mount: create, PAGE_SIZE: PAGE_SIZE,
    limits: { activePages: ACTIVE_PAGES, detailPages: DETAIL_PAGES, anchorPages: ANCHOR_PAGES } };
})(window);
