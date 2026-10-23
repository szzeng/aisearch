/* Local front end for the read-only photo-search API.
 *
 * Ground rules this file keeps (P1-05):
 *  - every piece of data comes from the session-protected /api/v1 endpoints;
 *  - the CSRF token is read from the ps_csrf cookie and sent in X-CSRF-Token only;
 *    it is never stored, never logged and never put in a URL;
 *  - only source display names and source-relative paths are rendered; an absolute
 *    local path appears only in the input box the user typed it into;
 *  - nothing is loaded from the network except this same origin;
 *  - a failed thumbnail shows its structured code instead of a broken image.
 */
(function () {
  "use strict";

  var API = "/api/v1";
  var SEARCH_RETURN_SOURCE_KEY = "aisearch.search-return-source.v1";
  var BROWSE_RETURN_KEY = "aisearch.home-browse-return.v1";
  var RANDOM_BROWSE_KEY = "aisearch.home-random-browse.v1";
  var PAGE_SIZE = 50;
  var POLL_INTERVAL_MS = 700;
  var JOB_SCAN_LIMIT = 200;
  var TERMINAL_JOB_STATES = {
    SUCCEEDED: true,
    FAILED: true,
    BLOCKED: true,
    CANCELLED: true,
  };
  var MEDIA_STATE_TEXT = {
    ready: "已生成",
    pending: "待生成",
    source_missing: "原文件不可用",
    failed: "生成失败",
    unsupported: "格式暂不支持",
  };
  var SOURCE_STATUS_TEXT = {
    ONLINE: "在线",
    OFFLINE: "离线",
  };
  //: P4-00B: the honest reading of the capture-time block (ADR-0011 §8.1). "PENDING" and
  //: "PARTIAL" mean the empty result is not evidence that nothing matches.
  var CAPTURE_STATE_TEXT = {
    PENDING: "拍摄时间尚未抽取：现在的时间条件命中为空，并不代表没有这些照片。",
    PARTIAL: "拍摄时间还在抽取中：时间条件的命中可能不完整。",
    READY: "拍摄时间已抽取完成。",
    NO_CAPTURE_TIME: "图库里没有任何可用的拍摄时间（只有文件时间），时间条件不会命中任何照片。",
  };
  var TIME_SOURCE_TEXT = {
    EXIF_ORIGINAL: "EXIF 原始时间",
    EXIF_DIGITIZED: "EXIF 数字化时间",
    EXIF_ROOT: "EXIF 根时间（文件时间）",
    FILE_MTIME: "文件修改时间",
  };
  var SESSION_HINT = "请使用启动器打印的一次性链接建立会话。";
  //: A drive-absolute or UNC spelling inside server-side text is never rendered.
  var PATH_LIKE = /(?:[A-Za-z]:[\\/])|(?:\\\\[^\s'"])/;
  var PATH_NOTE = "错误详情包含本机路径，已省略。";

  var state = {
    sessionMissing: false,
    sources: [],
    sourcesLoaded: false,
    sourcesLoadFailed: false,
    sourceDetails: {},
    sourcePathsById: {},
    sourcePathsLoaded: false,
    sourcePathsError: false,
    sourcePathsPromise: null,
    sourceDetailOpen: {},
    sourcesRequestVersion: 0,
    detailPreviewFit: null,
    browse: {
      scope: "all",
      favoritesRevision: null,
      favoritesOrder: "recent",
      sourceId: "",
      order: "time_desc",
      captureFrom: "",
      captureTo: "",
      //: The condition fingerprint this page-load is bound to; a changed control must not
      //: continue an old cursor (the server would refuse it, and the page would look broken).
      binding: null,
      cursor: null,
      hasMore: false,
      loading: false,
      loaded: false,
      count: 0,
      recycledCount: 0,
      pager: null,
      //: A control can change while a page is still in flight; the reload it owes is served
      //: as soon as that request settles, so the grid never keeps an older condition.
      owed: false,
      //: The honesty state of the last capture-conditioned page (``null`` when the page
      //: carried no time condition). Used to word an empty result honestly.
      captureState: null,
      //: Per-asset taken-time projections, fetched lazily for the visible tiles.
      timeCache: {},
      requestVersion: 0,
      returnScrollY: 0,
      returnAssetId: "",
      returnAssetTop: null,
      returnContext: null,
      returnPending: false,
      returnTargetCount: 0,
      returnTruncated: false,
      // The shared date timeline is primary; keep list mode as a recovery path.
      mode: "timeline",
      timelineMount: null,
      timelineScope: "",
      timelineReturnAnchor: "",
      random: { sourceId: "", batchIds: [], recentIds: [], batchSize: 300, loadedPages: 0,
        hasMore: false, loading: false, requestVersion: 0, nextInFlight: false,
        pendingBatch: null, assets: [], skippedCount: 0, scrollY: 0, statusError: "" },
    },
    photoCollections: { cursor: null, hasMore: false, revision: null, requestVersion: 0,
      collectionId: null, collection: null, memberCursor: null, memberHasMore: false,
      timelineMount: null, timelineScope: null, detailMode: "list" },
    photoCollectionKind: "ordinary",
    photoSmart: { cursor: null, hasMore: false, requestVersion: 0, viewEpoch: 0, entryEpoch: 0, smartId: null,
      collection: null, rule: null, assetCursor: null, assetHasMore: false,
      exclusionCursor: null, exclusionHasMore: false, view: "results", order: "rank",
      pageInFlight: false, pageRequestVersion: 0, pendingReload: false, pageNeedsReload: false, mutationInFlight: false, autoRefreshKeys: Object.create(null),
      pager: null, refreshTimer: null, refreshRequest: null, refreshObserverKey: null },
    detailRouteActive: false,
    detailRouteTransition: false,
    detailAssetId: null,
    detailReturnIntent: null,
    detailReturnWarning: false,
    pollTimer: null,
    //: P4-02Y: the polling timer of the one-click library refresh (one at a time).
    refreshTimer: null,
    // One active run; other source builds can wait in its queue.
    refreshRunning: false,
    refreshStatus: {},
    objectUrls: [],
    observer: null,
    storageBackupLoaded: false,
      libraryRetention: { cursor: 0, overview: null, items: [] },
      libraryRetentionRunId: null,
  };

  var els = {};

  // ------------------------------------------------------------------ utilities
  function cookieValue(name) {
    var parts = String(document.cookie || "").split(";");
    for (var index = 0; index < parts.length; index += 1) {
      var piece = parts[index].trim();
      if (piece.indexOf(name + "=") === 0) {
        return decodeURIComponent(piece.slice(name.length + 1));
      }
    }
    return "";
  }

  function text(node, value) {
    node.textContent = value === null || value === undefined ? "" : String(value);
    return node;
  }

  function element(tag, className, value) {
    var node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (value !== undefined) {
      text(node, value);
    }
    return node;
  }

  function formatMoment(milliseconds) {
    var value = Number(milliseconds);
    if (!isFinite(value) || value <= 0) {
      return "未知";
    }
    var moment = new Date(value);
    if (isNaN(moment.getTime())) {
      return "未知";
    }
    return moment.toLocaleString("zh-CN", { hour12: false });
  }

  function formatBytes(value) {
    var size = Number(value);
    if (!isFinite(size) || size < 0) {
      return "未知";
    }
    var units = ["B", "KiB", "MiB", "GiB"];
    var index = 0;
    while (size >= 1024 && index < units.length - 1) {
      size = size / 1024;
      index += 1;
    }
    return (index === 0 ? size : size.toFixed(1)) + " " + units[index];
  }

  function describe(failure) {
    var base = "[" + failure.code + "] " + failure.message;
    return failure.request_id ? base + "（请求编号 " + failure.request_id + "）" : base;
  }

  // The failure records of the media pipeline can embed a path in a repr-escaped form
  // that the server-side redactor does not recognise (see the P1-05 report, §8). The page
  // never renders a local absolute path, so such a message is replaced by a short note
  // instead of being trusted; the structured ``code`` is shown either way.
  function safeText(value) {
    if (value === null || value === undefined) {
      return "";
    }
    return PATH_LIKE.test(String(value)) ? PATH_NOTE : String(value);
  }

  function showNotice(message) {
    text(els.notice, message);
    els.notice.hidden = false;
  }

  function clearNotice() {
    els.notice.hidden = true;
    text(els.notice, "");
  }

  function showCollectionFailure(failure, target) {
    var message = describe(failure);
    if (target) setStatus(target, message, "error");
    else showNotice(message);
  }

  function noteSessionMissing() {
    if (state.sessionMissing) {
      return;
    }
    state.sessionMissing = true;
    showNotice(SESSION_HINT + "本页面不会自动重试；请重新打开启动器给出的链接。");
  }

  function setStatus(node, message, kind) {
    node.className = "state-block" + (kind ? " " + kind : "");
    text(node, message);
    node.hidden = !message;
  }

  function failureFrom(response, payload) {
    var body = payload && typeof payload === "object" ? payload : {};
    return {
      status: response.status,
      code: body.code || "HTTP_" + response.status,
      message: body.message || "请求未成功",
      request_id: body.request_id || "",
      details: body.details,
    };
  }

  function request(path, options) {
    var settings = options || {};
    var method = String(settings.method || "GET").toUpperCase();
    var headers = {};
    if (settings.body !== undefined) {
      headers["Content-Type"] = "application/json";
    }
    if (method !== "GET" && method !== "HEAD") {
      var csrf = cookieValue("ps_csrf");
      if (csrf) {
        headers["X-CSRF-Token"] = csrf;
      }
    }
    var init = {
      method: method,
      headers: headers,
      credentials: "same-origin",
      cache: "no-store",
    };
    if (settings.signal) init.signal = settings.signal;
    if (settings.body !== undefined) {
      init.body = JSON.stringify(settings.body);
    }
    return fetch(API + path, init).then(function (response) {
      var status = response.status;
      var contentType = response.headers.get("Content-Type") || "";
      if (status === 204 || contentType.indexOf("application/json") < 0) {
        if (response.ok) {
          return { status: status, body: null };
        }
        var plain = {
          status: status,
          code: "HTTP_" + status,
          message: "请求未成功",
          request_id: "",
        };
        if (status === 401) {
          noteSessionMissing();
        }
        throw plain;
      }
      return response
        .json()
        .catch(function () {
          return null;
        })
        .then(function (payload) {
          if (response.ok) {
            return { status: status, body: payload };
          }
          var failure = failureFrom(response, payload);
          if (status === 401) {
            noteSessionMissing();
          }
          throw failure;
        });
    });
  }

  function releaseObjectUrls() {
    while (state.objectUrls.length) {
      URL.revokeObjectURL(state.objectUrls.pop());
    }
  }

  // --------------------------------------------------------------------- images
  function fetchArtifact(path) {
    // ``no-cache`` revalidates on every use: the artifact bytes are immutable, but the
    // ``X-Original-Available`` header is a live fact and must never be served stale.
    return fetch(API + path, { credentials: "same-origin", cache: "no-cache" }).then(function (
      response
    ) {
      var available = response.headers.get("X-Original-Available");
      if (response.ok) {
        return response.blob().then(function (blob) {
          var url = URL.createObjectURL(blob);
          state.objectUrls.push(url);
          return { ok: true, url: url, originalAvailable: available };
        });
      }
      return response
        .json()
        .catch(function () {
          return null;
        })
        .then(function (payload) {
          return {
            ok: false,
            status: response.status,
            originalAvailable: available,
            code: (payload && payload.code) || "HTTP_" + response.status,
            message: (payload && payload.message) || "产物不可用",
            request_id: (payload && payload.request_id) || "",
          };
        });
    });
  }

  function clearFrame(frame) {
    while (frame.firstChild) {
      frame.removeChild(frame.firstChild);
    }
  }

  function noteInFrame(frame, className, message) {
    var note = element("p", className || "tile-note", message);
    frame.appendChild(note);
    return note;
  }

  function relativeLabel(asset) {
    if (asset && asset.current_location && asset.current_location.relative_path) {
      return asset.current_location.relative_path;
    }
    return "资产 #" + ((asset && asset.asset_id) || "?");
  }

  function availabilityBadge(frame, originalAvailable) {
    var missing = originalAvailable === "false" || frame.dataset.originalMissing === "true";
    if (!missing) {
      return;
    }
    addBadge(frame, "badge badge-original-missing", "原文件不可用");
  }

  function paintThumbnail(frame) {
    if (frame.dataset.state) {
      return;
    }
    frame.dataset.state = "loading";
    var assetId = frame.dataset.assetId;
    var label = frame.dataset.relativeLabel || "资产 #" + assetId;
    noteInFrame(frame, "tile-note", "缩略图加载中…");
    fetchArtifact("/assets/" + assetId + "/thumbnail")
      .then(function (result) {
        clearFrame(frame);
        frame.dataset.state = result.ok ? "ready" : "failed";
        if (result.ok) {
          var image = document.createElement("img");
          image.alt = "缩略图：" + label;
          image.loading = "lazy";
          image.decoding = "async";
          image.addEventListener("error", function () {
            clearFrame(frame);
            frame.dataset.state = "failed";
            noteInFrame(frame, "tile-note", "缩略图无法显示（IMAGE_UNREADABLE）");
          });
          image.src = result.url;
          frame.appendChild(image);
          availabilityBadge(frame, result.originalAvailable);
          return false;
        }
        addBadge(frame, "badge badge-code", result.code);
        noteInFrame(
          frame,
          "tile-note",
          "缩略图不可用：" + result.code + "（请求编号 " + (result.request_id || "无") + "）"
        );
      })
      .catch(function () {
        clearFrame(frame);
        frame.dataset.state = "failed";
        noteInFrame(frame, "tile-note", "缩略图请求失败（NETWORK_ERROR）");
      });
  }

  function addBadge(frame, className, label) {
    var badges = frame.querySelector(".badges");
    if (!badges) {
      badges = element("div", "badges");
      frame.appendChild(badges);
    }
    badges.appendChild(element("span", className, label));
  }

  function observeThumbnail(frame) {
    if (!state.observer) {
      paintThumbnail(frame);
      return;
    }
    state.observer.observe(frame);
  }

  function buildObserver() {
    if (typeof IntersectionObserver !== "function") {
      return null;
    }
    return new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) {
            return;
          }
          var frame = entry.target;
          state.observer.unobserve(frame);
          paintThumbnail(frame);
        });
      },
      { rootMargin: "200px" }
    );
  }

  // -------------------------------------------------------------------- sources
  function loadSources() {
    state.sourcesRequestVersion += 1;
    var requestVersion = state.sourcesRequestVersion;
    state.sourcePathsLoaded = false;
    setStatus(els.sourcesStatus, "正在读取图库入口…");
    state.sourceDetails = {};
    return request("/sources/summary")
      .then(function (result) {
        if (requestVersion !== state.sourcesRequestVersion) {
          return;
        }
        state.sources = (result.body && result.body.sources) || [];
        state.sourcesLoaded = true;
        state.sourcesLoadFailed = false;
        updateFirstUseGuide();
        updateGlobalRefreshActions();
        if (
          state.browse.sourceId &&
          !state.sources.some(function (source) {
            return String(source.source_id) === String(state.browse.sourceId);
          })
        ) {
          state.browse.sourceId = "";
          state.browse.loaded = false;
          if (!els.views.browse.hidden) {
            loadBrowse(true);
          }
        }
        renderSources();
        renderSourceOptions();
        updateSearchScopeAction();
        if (!els.views.sources.hidden && !sourcePathsCoverSources()) loadSourcePaths(true);
        if (state.browse.timelineEmptyParams) showTimelineEmptyMessage(state.browse.timelineEmptyParams);
        els.browseSource.value = state.browse.sourceId;
        els.browseOrder.value = state.browse.order;
        els.browseCaptureFrom.value = state.browse.captureFrom;
        els.browseCaptureTo.value = state.browse.captureTo;
        setStatus(els.sourcesStatus, "已登记 " + state.sources.length + " 个图库来源。");
      })
      .catch(function (failure) {
        if (requestVersion !== state.sourcesRequestVersion) {
          return;
        }
        setStatus(els.sourcesStatus, "读取来源失败：" + describe(failure), "error");
        state.sourcesLoaded = true;
        state.sourcesLoadFailed = true;
        if (state.browse.timelineEmptyParams) showTimelineEmptyMessage(state.browse.timelineEmptyParams);
        updateGlobalRefreshActions();
      });
  }

  function sourcePathsCoverSources() {
    return state.sourcePathsLoaded && state.sources.every(function (source) {
      return Object.prototype.hasOwnProperty.call(state.sourcePathsById, String(source.source_id));
    });
  }

  function loadSourcePaths(force) {
    if (!force && sourcePathsCoverSources()) return Promise.resolve();
    if (state.sourcePathsPromise) return state.sourcePathsPromise;
    var requestVersion = state.sourcesRequestVersion;
    state.sourcePathsLoaded = false;
    state.sourcePathsError = false;
    state.sourcePathsPromise = request("/sources/paths")
      .then(function (result) {
        if (requestVersion !== state.sourcesRequestVersion) return;
        var paths = (result.body && result.body.sources) || [];
        state.sourcePathsById = paths.reduce(function (byId, source) {
          byId[String(source.source_id)] = String(source.root_path || "");
          return byId;
        }, {});
        state.sourcePathsLoaded = true;
        if (state.sourcesLoaded) renderSources();
      })
      .catch(function (failure) {
        if (requestVersion !== state.sourcesRequestVersion) return;
        state.sourcePathsError = true;
        if (state.sourcesLoaded) renderSources();
        if (!els.views.sources.hidden) {
          setStatus(els.sourcesStatus, "读取图库路径失败：" + describe(failure), "error");
        }
      })
      .then(function () {
        state.sourcePathsPromise = null;
        // A source may have been added while this request was in flight. Its
        // summary refresh coalesced with us, so fetch the current paths now.
        if (requestVersion !== state.sourcesRequestVersion && !els.views.sources.hidden) {
          return loadSourcePaths(false);
        }
      });
    return state.sourcePathsPromise;
  }

  function loadStorageSummary() {
    setStatus(els.storageSummary, "正在读取数据库页统计…");
    return request("/storage/summary")
      .then(function (result) {
        var payload = result.body || {};
        var catalog = payload.catalog || {};
        var rows = [
          "目录数据库：" + (catalog.exists ? formatBytes(catalog.physical_bytes || 0) : "尚未创建"),
        ];
        if (catalog.page_count !== undefined) {
          rows.push("数据库页：" + catalog.page_count + " × " + formatBytes(catalog.page_size || 0));
          rows.push("可复用空闲页：" + catalog.freelist_count + "（逻辑 " + formatBytes(catalog.freelist_reusable_bytes || 0) + "，不会立即缩小数据库文件）");
        }
        if (catalog.counts) {
          rows.push("记录概览：" + Object.keys(catalog.counts).map(function (key) {
            return key + " " + catalog.counts[key];
          }).join("；"));
        }
        if (catalog.logical_blob_breakdown && Object.keys(catalog.logical_blob_breakdown).length) {
          rows.push("人脸向量 BLOB 为数据库内部逻辑分解，不重复计入目录空间。");
        }
        if (payload.last_deep_scan && payload.last_deep_scan.completed_at_ms) {
          rows.push("最近深度扫描：" + new Date(payload.last_deep_scan.completed_at_ms).toLocaleString() + (payload.last_deep_scan.partial ? "（部分结果）" : ""));
          Object.keys(payload.last_deep_scan.directories || {}).forEach(function (key) {
            var item = payload.last_deep_scan.directories[key] || {};
            rows.push(key + "：" + formatBytes(item.bytes || 0) + "，" + (item.files || 0) + " 个文件");
          });
        } else {
          rows.push("尚无深度扫描记录。");
        }
        setStatus(els.storageSummary, rows.join("\n") + (catalog.partial ? "\n数据库细项读取不完整。" : ""));
      })
      .catch(function (failure) {
        setStatus(els.storageSummary, "读取空间概览失败：" + describe(failure), "error");
      });
  }

  function storageStatusText(value) {
    if (!value) return "未知";
    if (value.status === "ok") return "正常";
    if (value.status === "preview") {
      if (value.eligible_files !== undefined) {
        var estimate = value.eligible_files ? "可优化：约 " + formatBytes(value.eligible_bytes || 0) + "，最多本次检查 " + (value.preview_batch_files || 0) + " 个文件" : "正常：没有符合条件的文件";
        return estimate + (value.partial ? "（扫描部分结果）" : "");
      }
      if (value.recommended === "vacuum") return "可优化：SQLite 完整性检查通过";
      if (value.recommended === "skip") return "正常：无需 VACUUM（" + (value.reason || "") + "）";
      var actions = (value.actions || []).filter(function (item) { return item.state === "would_run"; });
      return actions.length ? "可优化：" + actions.map(function (item) { return item.action; }).join("、") : "正常：无需向量维护";
    }
    if (value.status === "busy") return "无法维护：扫描正在运行";
    if (value.status === "skipped") return "无法维护：" + (value.reason || "条件不满足");
    return "无法维护：" + (value.code || value.reason || value.status || "状态未知");
  }

  function selectedStorageMaintenanceOperations() {
    return Array.prototype.slice.call(document.querySelectorAll('input[name="storage-maintenance-operation"]:checked')).map(function (input) { return input.value; });
  }

  function storageMaintenanceOperationLabel(operation) {
    var labels = {
      "clean:artifacts": "孤立产物",
      "clean:media-cache": "旧媒体缓存",
      "clean:previews": "预览缓存",
      "clean:face-crops": "人脸裁剪缓存",
      "optimize-vectors": "向量索引",
      vacuum: "SQLite VACUUM"
    };
    return labels[operation] || operation;
  }

  function storageMaintenanceItemDetails(item) {
    var details = [];
    if (item.deleted_files !== undefined) details.push("删除 " + item.deleted_files + " 个文件");
    var reclaimedBytes;
    if (item.freed_bytes !== undefined) reclaimedBytes = item.freed_bytes;
    else if (item.actual_bytes_freed !== undefined) reclaimedBytes = item.actual_bytes_freed;
    else if (item.actual_reclaimed_bytes !== undefined) reclaimedBytes = item.actual_reclaimed_bytes;
    if (reclaimedBytes !== undefined) {
      details.push("实际释放 " + formatBytes(reclaimedBytes));
    } else if (Array.isArray(item.stages)) {
      var reclaimedStages = item.stages.filter(function (stage) {
        return stage && typeof stage.actual_reclaimed_bytes === "number";
      });
      if (reclaimedStages.length) {
        var stageBytes = reclaimedStages.reduce(function (total, stage) { return total + stage.actual_reclaimed_bytes; }, 0);
        details.push("阶段实际释放 " + formatBytes(stageBytes));
      }
    }
    return details;
  }

  function updateStorageMaintenanceConfirm() {
    var button = document.getElementById("storage-maintenance-confirm");
    if (!button) return;
    var count = selectedStorageMaintenanceOperations().length;
    var countElement = document.getElementById("storage-maintenance-selected-count");
    var label = document.getElementById("storage-maintenance-selected-label");
    if (countElement) countElement.textContent = String(count);
    if (label) label.textContent = count ? "已选择 " + count + " 项维护" : "尚未选择项目";
    button.disabled = button.dataset.executionEnabled !== "true" || button.dataset.requestPending === "true" || count === 0;
  }

  function activateStorageTab(name) {
    Array.prototype.slice.call(document.querySelectorAll("[data-storage-tab]")).forEach(function (button) {
      var active = button.dataset.storageTab === name;
      button.setAttribute("aria-selected", active ? "true" : "false");
      button.tabIndex = active ? 0 : -1;
    });
    Array.prototype.slice.call(document.querySelectorAll(".storage-tab-panel")).forEach(function (panel) {
      panel.hidden = panel.id !== "storage-panel-" + name;
    });
    if (name === "retention" && !state.libraryRetention.overview) loadLibraryRetention(true);
    if (name === "backup" && !state.storageBackupLoaded) {
      state.storageBackupLoaded = true;
      loadStorageBackup();
    }
  }

  function renderMaintenance(payload) {
    var labels = { artifacts: "孤立产物", "media-cache": "旧媒体缓存", previews: "预览缓存", "face-crops": "人脸裁剪缓存", vectors: "向量索引", vacuum: "SQLite VACUUM" };
    var rows = Object.keys(labels).map(function (key) { return labels[key] + "：" + storageStatusText((payload.items || {})[key]); });
    if (payload.pending) rows.push("待执行：" + (payload.pending.operations || [payload.pending.operation]).filter(Boolean).map(storageMaintenanceOperationLabel).join("、") + "；请正常关闭相册后重新启动。有效期至 " + new Date(payload.pending.expires_at_ms).toLocaleString());
    else rows.push("待执行维护：无");
    var confirmButton = document.getElementById("storage-maintenance-confirm");
    var gate = document.getElementById("storage-maintenance-review-gate");
    confirmButton.dataset.executionEnabled = payload.execution_enabled === true ? "true" : "false";
    confirmButton.dataset.requestPending = payload.pending ? "true" : "false";
    updateStorageMaintenanceConfirm();
    text(gate, payload.review_required || "自动维护入口已通过独立窄审。");
    if (payload.last_result) {
      var last = payload.last_result;
      var details = [];
      if (last.deleted_files !== undefined) details.push("删除 " + last.deleted_files + " 个文件");
      if (last.freed_bytes !== undefined) details.push("释放 " + formatBytes(last.freed_bytes));
      if (last.actual_bytes_freed !== undefined) details.push("释放 " + formatBytes(last.actual_bytes_freed));
      if (last.status === "running") details.push("上次启动时操作中断，已完成部分不自动重试");
      var itemResults = (last.items || []).map(function (item) {
        var itemDetails = storageMaintenanceItemDetails(item);
        return storageMaintenanceOperationLabel(item.operation) + "：" + item.status + (item.partial ? "（部分结果）" : "") + (item.reason ? "（" + item.reason + "）" : "") + (itemDetails.length ? "；" + itemDetails.join("，") : "");
      });
      rows.push("上次启动结果：" + last.status + "（" + (last.operations || [last.operation]).filter(Boolean).map(storageMaintenanceOperationLabel).join("、") + (last.reason ? "；" + last.reason : "") + "）" + (itemResults.length ? "；" + itemResults.join("；") : "") + (details.length ? "；" + details.join("；") : ""));
    }
    setStatus(els.storageMaintenancePreview, rows.join("\n"));
  }

  function loadStorageMaintenance() {
    setStatus(els.storageMaintenancePreview, "正在读取维护预览…");
    return request("/storage/maintenance/preview").then(function (result) { renderMaintenance(result.body || {}); })
      .catch(function (failure) { setStatus(els.storageMaintenancePreview, "读取维护预览失败：" + describe(failure), "error"); });
  }

  function loadStorageBackup() {
    return request("/storage/backup").then(function (result) {
      var value = result.body || {};
      els.storageBackupDirectory.value = value.directory || "";
      var last = value.last_backup;
      setStatus(els.storageBackupStatus, last ? "最近手动备份：" + last.created_at + "\n文件：" + last.path + "\n状态：" + last.status + "；大小：" + formatBytes(last.bytes || 0) : "尚无手动备份记录。");
    }).catch(function (failure) { setStatus(els.storageBackupStatus, "读取备份设置失败：" + describe(failure), "error"); });
  }

  function renderDeepStorage(result) {
    var rows = ["深度扫描状态：" + (result.status || "unknown")];
    if (result.completed_at_ms) rows.push("检查时间：" + new Date(result.completed_at_ms).toLocaleString());
    if (result.status === "busy") rows.push("已有扫描占用检查锁，请稍后再试。");
    if (result.partial !== undefined) rows.push("扫描结果：" + (result.partial ? "部分结果" : "完整结果"));
    if (result.directories) Object.keys(result.directories).forEach(function (key) {
      var item = result.directories[key] || {};
      rows.push(key + "：" + formatBytes(item.bytes || item.total_bytes || 0) + (item.files !== undefined ? "，" + item.files + " 个文件" : ""));
    });
    var catalog = result.catalog || {};
    if (catalog.physical_bytes !== undefined) rows.push("数据库及 WAL：" + formatBytes(catalog.physical_bytes));
    setStatus(els.storageSummary, rows.join("\n"));
  }

  function runStorageDeepCheck() {
    setStatus(els.storageSummary, "正在后台进行有界深度扫描…HTTP 页面仍可响应。");
    return request("/storage/inspect", { method: "POST", body: {} }).then(function (result) { renderDeepStorage(result.body || {}); })
      .catch(function (failure) { setStatus(els.storageSummary, "深度扫描失败：" + describe(failure), "error"); });
  }

  function renderLibraryRetention() {
    var model = state.libraryRetention;
    var lines = [];
    if (model.overview) {
      var counts = model.overview.counts || {};
      lines.push("按数据库计数：MISSING 位置 " + (counts.missing_locations || 0) + "；无 PRESENT 位置的唯一资产 " + (counts.assets_without_present || 0) + "；零位置资产 " + (counts.zero_location_assets || 0) + "；有 PRESENT 副本的资产 " + (counts.assets_with_present_copy || 0) + "。这些数字有交集，不可相加。 ");
      lines.push(model.overview.retention_time_evidence || "首次失效时间未知，不具备清理资格。");
      var states = model.overview.retention_counts || {};
      lines.push("资格状态：观察中 " + (states.OBSERVING || 0) + "；到期且复核 " + (states.ELIGIBLE || 0) + "；受阻 " + (states.BLOCKED || 0) + "；来源批量异常待核实 " + (model.overview.unresolved_source_anomalies || 0) + "。一键操作会重新复核资格；人工记录不会删除。");
      var protectedLoaded = model.items.filter(function (item) { return item.has_human_or_protected_references; }).length;
      var scanLoaded = model.items.filter(function (item) { return item.unresolved_source_anomaly || item.retention_state === "UNKNOWN"; }).length;
      lines.push("已读取列表 " + model.items.length + " 张：含人工/受保护引用 " + protectedLoaded + "；观察起点未知、来源需复核或待扫描 " + scanLoaded + "。这只是已加载列表统计，不是全库数量。");
      var automatic = model.overview.automatic || {};
      if (els.libraryRetentionAutomatic && els.libraryRetentionAutomatic.checked !== Boolean(automatic.automatic_enabled)) {
        els.libraryRetentionAutomatic.checked = Boolean(automatic.automatic_enabled);
      }
      var runProgress = model.overview.latest_run;
      if (runProgress && runProgress.run) lines.push(renderRetentionRunLine(runProgress));
      var storage = model.overview.storage || {};
      lines.push("SQLite：catalog 文件 " + (storage.sqlite_catalog_file_bytes == null ? "无法准确估计" : formatBytes(storage.sqlite_catalog_file_bytes)) + "；逻辑页 " + (storage.sqlite_logical_page_bytes == null ? "无法准确估计" : formatBytes(storage.sqlite_logical_page_bytes)) + "；可回收量无法准确估计。LanceDB 逻辑行数与物理可回收量无法准确估计。");
    }
    model.items.forEach(function (item) {
      lines.push("asset " + item.asset_id + "：" + item.classification + "；" + item.availability_summary + "；" + item.reason + "；人工/受保护引用：" + (item.has_human_or_protected_references ? "有" : "未发现已知引用") + "；" + item.retention_eligibility);
    });
    if (!model.items.length && model.overview) lines.push("当前页没有失效位置或零位置资产。");
    text(els.libraryRetentionStatus, lines.join("\n"));
    els.libraryRetentionAnomalies.replaceChildren();
    ((model.overview && model.overview.source_anomalies) || []).forEach(function (anomaly) {
      var row = document.createElement("div");
      var detail = document.createElement("span");
      detail.textContent = "来源 " + anomaly.source_id + " 的扫描 " + anomaly.scan_id + "：新增失效 " + anomaly.newly_missing_assets + "/扫描前有效资产 " + anomaly.baseline_present_assets + "，需要人工核查。";
      var button = document.createElement("button");
      button.type = "button";
      button.dataset.retentionAnomaly = "resolve";
      button.dataset.sourceId = String(anomaly.source_id);
      button.dataset.scanId = String(anomaly.scan_id);
      button.textContent = "已核查，解除本批保护";
      row.append(detail, button);
      els.libraryRetentionAnomalies.appendChild(row);
    });
    els.libraryRetentionMore.hidden = !model.overview || model.overview.next_cursor == null;
    els.libraryRetentionMore.disabled = state.libraryRetentionLoading === true;
  }

  function renderRetentionRunLine(progress) {
    var run = progress.run || {};
    var runLabel = "清理任务 " + (run.run_id || "") + " / " + run.mode + " / " + run.state;
    var stateText = run.state === "PARTIAL" && progress.has_unsettled_work === false
      ? runLabel + "：本轮已结束，部分照片未清理；故障解除后可再次点击重新复核"
      : (run.state === "PARTIAL" && progress.has_unsettled_work
        ? "仍有未结算任务，可点击继续恢复（" + runLabel + "）"
        : runLabel);
    return stateText + "：已检查 " + (run.checked_assets || 0) +
      "，已清理 " + (run.completed_assets || 0) + "，人工保护跳过 " + (run.skipped_human_assets || 0) +
      "，来源/扫描跳过 " + (run.skipped_source_assets || 0) + "，其他跳过 " + (run.skipped_other_assets || 0) +
      "，失败 " + (run.failed_assets || 0) + "；" + (run.has_more_candidates ? "仍有下一批" : "本轮候选已检查") +
      "；阶段：" + (run.stage || "未知") +
      "。逻辑清理完成后，受管缓存仍需停止应用并运行 storage clean-retained。";
  }

  function pollLibraryRetentionRun(runId) {
    return request("/storage/library-retention/runs/" + encodeURIComponent(runId)).then(function (result) {
      var progress = result.body || {};
      var run = progress.run || {};
      setStatus(els.libraryRetentionStatus, renderRetentionRunLine(progress));
      if (run.state === "QUEUED" || run.state === "RUNNING") {
        window.setTimeout(function () { pollLibraryRetentionRun(runId); }, 1200);
      } else {
        state.libraryRetentionRunId = null;
        els.libraryRetentionCleanup.disabled = false;
        loadLibraryRetention(true);
      }
    }).catch(function (failure) {
      state.libraryRetentionRunId = null;
      els.libraryRetentionCleanup.disabled = false;
      setStatus(els.libraryRetentionStatus, "读取清理进度失败：" + describe(failure), "error");
    });
  }

  function startLibraryRetentionCleanup() {
    var overview = state.libraryRetention.overview || {};
    var counts = overview.retention_counts || {};
    var preview = "当前统计：到期且复核 " + (counts.ELIGIBLE || 0) + "，观察中 " + (counts.OBSERVING || 0) +
      "，来源异常 " + (overview.unresolved_source_anomalies || 0) + "。一次操作会分批检查本次候选范围内的照片；每批最多 20 张，逐张重新核验，实际可清理数可能更少。";
    if (!window.confirm(preview + "\n\n将清除 PhotoSearch 中通过复核的失效照片派生数据。不会删除磁盘原图或人工记录；手动确认不等待 30 天。缓存和 Artifact 文件需应用停止后另行回收。预计释放空间无法准确估计。继续吗？")) return;
    els.libraryRetentionCleanup.disabled = true;
    setStatus(els.libraryRetentionStatus, "正在创建清理任务…");
    request("/storage/library-retention/cleanup", { method: "POST", body: { confirmed: true } }).then(function (result) {
      var body = result.body || {};
      state.libraryRetentionRunId = body.run_id || null;
      if (body.run_id) {
        pollLibraryRetentionRun(body.run_id);
      } else {
        els.libraryRetentionCleanup.disabled = false;
        loadLibraryRetention(true);
      }
    }).catch(function (failure) {
      els.libraryRetentionCleanup.disabled = false;
      setStatus(els.libraryRetentionStatus, "启动清理失败：" + describe(failure), "error");
    });
  }

  function setLibraryRetentionAutomatic() {
    var enabled = els.libraryRetentionAutomatic.checked;
    els.libraryRetentionAutomatic.disabled = true;
    request("/storage/library-retention/automatic", { method: "POST", body: { enabled: enabled } }).then(function (result) {
      var conflict = result.body && result.body.run_conflict;
      if (conflict) {
        setStatus(els.libraryRetentionStatus, "另一授权模式的清理任务仍在执行（" + conflict.mode + " / " + conflict.state + "），本次未启动新的自动任务。", "error");
        return loadLibraryRetention(true);
      }
      var run = result.body && result.body.run && result.body.run.run;
      if (run && (run.state === "QUEUED" || run.state === "RUNNING")) {
        state.libraryRetentionRunId = run.run_id;
        pollLibraryRetentionRun(run.run_id);
      } else {
        return loadLibraryRetention(true);
      }
    }).catch(function (failure) {
      els.libraryRetentionAutomatic.checked = !enabled;
      setStatus(els.libraryRetentionStatus, "更新自动清理设置失败：" + describe(failure), "error");
    }).then(function () { els.libraryRetentionAutomatic.disabled = false; });
  }

  function loadLibraryRetention(reset) {
    if (state.libraryRetentionLoading) return Promise.resolve();
    if (reset) state.libraryRetention = { cursor: 0, overview: null, items: [] };
    var cursor = state.libraryRetention.cursor || 0;
    state.libraryRetentionLoading = true;
    els.libraryRetentionMore.disabled = true;
    setStatus(els.libraryRetentionStatus, "正在读取只读统计…");
    var suffix = cursor ? "?after_asset_id=" + encodeURIComponent(cursor) : "";
    return request("/storage/library-retention" + suffix).then(function (result) {
      var page = result.body || {};
      if (!state.libraryRetention.overview) state.libraryRetention.overview = page;
      state.libraryRetention.items = state.libraryRetention.items.concat(page.items || []);
      state.libraryRetention.cursor = page.next_cursor || 0;
      renderLibraryRetention();
    }).catch(function (failure) {
      setStatus(els.libraryRetentionStatus, "读取失效照片预览失败：" + describe(failure), "error");
    }).then(function () {
      state.libraryRetentionLoading = false;
      els.libraryRetentionMore.disabled = false;
    });
  }

  function saveStorageBackupDirectory() {
    return request("/storage/backup", { method: "PUT", body: { directory: els.storageBackupDirectory.value } }).then(function (result) {
      var body = result.body || {};
      if (body.status !== "ok") throw { code: "BACKUP_DIRECTORY_INVALID", message: body.message || "目录不可用" };
      setStatus(els.storageBackupStatus, "备份目录已保存并检查可写，剩余 " + formatBytes(body.free_bytes || 0) + "。\n" + body.directory);
    }).catch(function (failure) { setStatus(els.storageBackupStatus, "保存备份目录失败：" + (failure.message || describe(failure)), "error"); });
  }

  function createManualStorageBackup() {
    setStatus(els.storageBackupStatus, "正在后台创建并校验 SQLite catalog 快照…");
    return request("/storage/backup", { method: "POST", body: {} }).then(function (result) {
      var body = result.body || {};
      if (body.status !== "ok") throw { code: "BACKUP_FAILED", message: body.message || "快照创建失败" };
      setStatus(els.storageBackupStatus, "手动快照已完成：" + body.created_at + "\n文件：" + body.path + "\n大小：" + formatBytes(body.bytes || 0) + "。不含原图、向量外部产物或模型。");
    }).catch(function (failure) { setStatus(els.storageBackupStatus, "手动备份失败：" + (failure.message || describe(failure)), "error"); });
  }

  function confirmStorageMaintenance() {
    var operations = selectedStorageMaintenanceOperations();
    if (!operations.length) return;
    var labels = operations.map(storageMaintenanceOperationLabel);
    if (!window.confirm("确认安排“" + labels.join("、") + "”在相册下次启动时依次执行？请先按需手动备份人工整理数据。确认后只需关闭并重新启动一次；系统不会自动备份。")) return;
    request("/storage/maintenance/request", { method: "POST", body: { operations: operations, confirmed: true } }).then(function (result) {
      var body = result.body || {};
      if (body.status === "error") throw { code: "MAINTENANCE_REQUEST_FAILED", message: body.message || "无法安排维护" };
      setStatus(els.storageMaintenancePreview, "已确认 " + operations.length + " 项一次性维护。请关闭相册后重新启动一次；有效期一小时。没有执行自动备份。", "success");
      loadStorageMaintenance();
    }).catch(function (failure) { setStatus(els.storageMaintenancePreview, "安排维护失败：" + (failure.message || describe(failure)), "error"); });
  }

  function cancelStorageMaintenance() {
    request("/storage/maintenance/request", { method: "DELETE" }).then(function (result) {
      setStatus(els.storageMaintenancePreview, result.body && result.body.status === "cancelled" ? "待执行维护已取消。" : "没有待执行维护。");
      loadStorageMaintenance();
    }).catch(function (failure) { setStatus(els.storageMaintenancePreview, "取消维护失败：" + describe(failure), "error"); });
  }

  function renderSources() {
    els.sourcesList.textContent = "";
    if (!state.sources.length) {
      updateGlobalRefreshActions();
      els.sourcesList.appendChild(
        element("li", "hint", "还没有添加图库。请打开“图库管理”，展开“添加图库来源”，填写照片文件夹路径后开始建库。")
      );
      return;
    }
    state.sources.forEach(function (source) {
      var card = element("li", "source-card");
      var heading = element("div", "source-card-heading");
      var identityBlock = element("div", "source-identity");
      var title = element("h3", null, source.display_name);
      title.setAttribute("data-source-title", String(source.source_id));
      identityBlock.appendChild(title);
      identityBlock.appendChild(element("span", "source-id", "图库 #" + source.source_id));
      heading.appendChild(identityBlock);
      heading.appendChild(element("span", "source-record-status", SOURCE_STATUS_TEXT[source.status] || source.status || "已登记"));
      card.appendChild(heading);
      var rootPath = state.sourcePathsById[String(source.source_id)];
      card.appendChild(element("code", "source-path", rootPath || (state.sourcePathsError || state.sourcePathsLoaded ? "图库路径暂不可用" : "正在读取图库路径…")));
      var sourceMeta = "磁盘状态未实时检查";
      if (source.volume_hint) sourceMeta += " · " + safeText(source.volume_hint);
      card.appendChild(element("p", "source-meta", sourceMeta));
      var actions = element("div", "source-card-actions");
      var open = element("button", "view-source source-primary", "看照片");
      open.type = "button";
      open.addEventListener("click", function () {
        state.browse.sourceId = String(source.source_id);
        els.browseSource.value = state.browse.sourceId;
        state.browse.loaded = false;
        showView("browse");
      });
      var summaryActions = element("p", "source-actions source-primary-actions");
      var sourceProgress = state.sourceDetails[String(source.source_id)];
      var actionMode = sourceProgress ? sourceActionMode(sourceProgress.progress) : "unknown";
      var buildAction = element("button", "source-primary", actionMode === "build"
        ? "开始建库" : (actionMode === "continue" ? "继续建库" : "建库/补齐"));
      buildAction.type = "button";
      buildAction.setAttribute("data-build", String(source.source_id));
      buildAction.setAttribute("data-primary-build", String(source.source_id));
      buildAction.setAttribute("data-build-label", buildAction.textContent);
      buildAction.addEventListener("click", function () {
        refreshLibrary([Number(source.source_id)], "build");
      });
      buildAction.hidden = actionMode === "refresh";
      summaryActions.appendChild(buildAction);
      var refreshAction = element("button", null, "同步变化");
      refreshAction.type = "button";
      refreshAction.setAttribute("data-refresh", String(source.source_id));
      refreshAction.setAttribute("data-primary-refresh", String(source.source_id));
      refreshAction.addEventListener("click", function () {
        refreshLibrary([Number(source.source_id)], "refresh");
      });
      summaryActions.appendChild(refreshAction);
      actions.appendChild(open);
      actions.appendChild(summaryActions);
      card.appendChild(actions);
      addSourceRename(card, source, title, actions);

      var detail = element("details", "source-detail");
      detail.open = Boolean(state.sourceDetailOpen[source.source_id]);
      var summary = element("summary", null, "任务与统计明细（按需读取）");
      detail.appendChild(summary);
      var detailContent = element("div", "source-detail-content", "展开后读取统计与任务状态。");
      detail.appendChild(detailContent);
      detail.addEventListener("toggle", function () {
        state.sourceDetailOpen[source.source_id] = detail.open;
        if (detail.open) {
          loadSourceDetail(source, detailContent);
        }
      });
      card.appendChild(detail);
      els.sourcesList.appendChild(card);
      if (detail.open) {
        loadSourceDetail(source, detailContent);
      }
    });
  }

  function addSourceRename(card, source, title, actionContainer) {
    var edit = element("button", null, "修改名称");
    edit.type = "button";
    var form = element("form", "source-rename");
    form.hidden = true;
    var label = element("label", null, "图库名称 ");
    var input = element("input");
    input.type = "text";
    input.maxLength = 200;
    input.required = true;
    input.value = source.display_name;
    label.appendChild(input);
    var save = element("button", null, "保存");
    save.type = "submit";
    var cancel = element("button", null, "取消");
    cancel.type = "button";
    var feedback = element("p", "hint", "");
    feedback.setAttribute("role", "status");
    feedback.setAttribute("aria-live", "polite");
    form.appendChild(label);
    form.appendChild(save);
    form.appendChild(cancel);
    form.appendChild(feedback);
    edit.addEventListener("click", function () {
      input.value = source.display_name;
      text(feedback, "");
      edit.hidden = true;
      form.hidden = false;
      input.focus();
      input.select();
    });
    cancel.addEventListener("click", function () {
      form.hidden = true;
      edit.hidden = false;
      edit.focus();
    });
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      if (save.disabled) {
        return;
      }
      var name = input.value.trim();
      if (!name || name.length > 200) {
        text(feedback, "图库名称不能为空，最多200个字。");
        input.focus();
        return;
      }
      save.disabled = true;
      cancel.disabled = true;
      input.disabled = true;
      text(feedback, "正在保存…");
      request("/sources/" + encodeURIComponent(source.source_id), {
        method: "PATCH",
        body: { display_name: name },
      })
        .then(function (result) {
          source.display_name = result.body.display_name;
          state.sources.forEach(function (item) {
            if (String(item.source_id) === String(source.source_id)) {
              item.display_name = source.display_name;
            }
          });
          var cached = state.sourceDetails[String(source.source_id)];
          if (cached) {
            cached.display_name = source.display_name;
          }
          text(title, source.display_name);
          Array.prototype.forEach.call(
            els.sourcesList.querySelectorAll('[data-source-title="' + source.source_id + '"]'),
            function (node) { text(node, source.display_name); }
          );
          renderSourceOptions();
          state.browse.loaded = false;
          form.hidden = true;
          edit.hidden = false;
          edit.focus();
          setStatus(els.sourcesStatus, "图库名称已修改。");
        })
        .catch(function (failure) {
          text(feedback, "保存失败：" + describe(failure));
        })
        .finally(function () {
          save.disabled = false;
          cancel.disabled = false;
          input.disabled = false;
        });
    });
    (actionContainer || card).appendChild(edit);
    card.appendChild(form);
  }

  function loadSourceDetail(source, container) {
    var cached = state.sourceDetails[String(source.source_id)];
    if (cached) {
      renderSourceDetail(source, cached, container);
      return;
    }
    container.textContent = "正在读取这个图库的统计与任务状态…";
    request("/sources/" + encodeURIComponent(source.source_id))
      .then(function (result) {
        var full = result.body || {};
        state.sourceDetails[String(source.source_id)] = full;
        renderSourceDetail(source, full, container);
      })
      .catch(function (failure) {
        container.textContent = "明细读取失败：" + describe(failure) + " ";
        var retry = element("button", null, "重试读取明细");
        retry.type = "button";
        retry.addEventListener("click", function () {
          loadSourceDetail(source, container);
        });
        container.appendChild(retry);
      });
  }

  function renderSourceDetail(source, detail, container) {
    var counts = detail.counts || {};
    var progress = detail.progress || null;
    source = Object.assign({}, source, detail);
    container.textContent = "";
      var list = element("dl");
      var rows = [];
      if (progress && progress.scan) {
        rows.push(["扫描", String(progress.scan.files) + " 个文件（可显示 " + String(progress.scan.present) + "）"]);
      }
      rows.push(
        ["资产", String(counts.assets === undefined ? "未知" : counts.assets)],
        [
          "在库位置",
          String(counts.locations_present === undefined ? "未知" : counts.locations_present),
        ],
        [
          "缺失位置",
          String(counts.locations_missing === undefined ? "未知" : counts.locations_missing),
        ],
        ["语义索引", semanticProgressText(progress)],
        ["语义状态", semanticStateText(progress)],
      );
      ENRICHMENT_LABELS.forEach(function (pair) {
        var stage = progress && progress.stages ? progress.stages[pair[0]] : null;
        if (stage) {
          rows.push([pair[1], pair[0] === "ocr" ? ocrProgressText(stage) :
            (STAGE_STATE_TEXT[stage.state] || String(stage.state))]);
        }
      });
      rows.push(
        [
          "最近成功扫描",
          detail.last_successful_scan === null || detail.last_successful_scan === undefined
            ? "尚无成功扫描"
            : "扫描 #" + detail.last_successful_scan,
        ],
      );
      rows.forEach(function (pair) {
        list.appendChild(element("dt", null, pair[0]));
        list.appendChild(element("dd", null, pair[1]));
      });
      container.appendChild(list);
      updatePrimarySourceActions(source.source_id, progress);
      var mode = sourceActionMode(progress);
      var advanced = element("details", "source-advanced");
      var advancedSummary = element("summary", null, "更多操作（完整校验扫描、补齐建库、删除来源）");
      advanced.appendChild(advancedSummary);
      var actions = element("p", "source-actions");
      if (mode === "build" || mode === "continue") {
        // §1/§2: a source that was never built, or one whose durable backlog still holds work
        // (semantic index incomplete, or an enrichment stage with unfinished jobs), gets the
        // one entry that keeps building it.
        var build = element("button", null, mode === "continue" ? "继续建库" : "开始建库");
        build.type = "button";
        build.setAttribute("data-build", String(source.source_id));
        build.setAttribute("data-build-label", build.textContent);
        build.addEventListener("click", function () {
          refreshLibrary([Number(source.source_id)], "build");
        });
        actions.appendChild(build);
      } else {
        // Keep a manual build repair entry after the source is searchable.
        var buildCheck = element("button", null, "检查并补齐建库");
        buildCheck.type = "button";
        buildCheck.setAttribute("data-build", String(source.source_id));
        buildCheck.setAttribute("data-build-label", buildCheck.textContent);
        buildCheck.addEventListener("click", function () {
          refreshLibrary([Number(source.source_id)], "build");
        });
        actions.appendChild(buildCheck);
      }
      var scan = element("button", null, "完整校验扫描");
      scan.type = "button";
      scan.setAttribute("data-scan", String(source.source_id));
      scan.addEventListener("click", function () { startScan(source.source_id); });
      actions.appendChild(scan);
      var remove = element("button", "danger", "删除来源");
      remove.type = "button";
      remove.setAttribute("data-delete", String(source.source_id));
      remove.addEventListener("click", function () {
        deleteSource(source);
      });
      actions.appendChild(remove);
      updateSourceActions(actions);
      advanced.appendChild(actions);
      container.appendChild(advanced);
      var progressLine = element("p", "hint", "");
      progressLine.setAttribute("data-progress", String(source.source_id));
      progressLine.setAttribute("role", "status");
      progressLine.setAttribute("aria-live", "polite");
      container.appendChild(progressLine);
  }

  function updatePrimarySourceActions(sourceId, progress) {
    var mode = sourceActionMode(progress);
    Array.prototype.forEach.call(els.sourcesList.querySelectorAll("[data-primary-build]"), function (button) {
      if (Number(button.getAttribute("data-primary-build")) !== Number(sourceId)) return;
      button.hidden = mode === "refresh";
      var label = mode === "build" ? "开始建库" : (mode === "continue" ? "继续建库" : "建库/补齐");
      button.setAttribute("data-build-label", label);
      button.textContent = label;
    });
  }

  function updateGlobalRefreshActions() {
    if (!els.buildAllLibrary || !els.refreshLibrary) return;
    var noSources = !state.sources.length;
    els.buildAllLibrary.disabled = noSources;
    els.refreshLibrary.disabled = noSources || state.refreshRunning;
  }

  function updateSourceActions(container) {
    var status = state.refreshStatus;
    Array.prototype.forEach.call(
      container.querySelectorAll("[data-build], [data-refresh], [data-scan], [data-delete]"),
      function (button) {
        var sourceId = Number(button.getAttribute("data-build"));
        if (!button.hasAttribute("data-build")) {
          button.disabled = state.refreshRunning;
          return;
        }
        var queued = (status.pending_builds || []).some(function (ids) {
          return ids.indexOf(sourceId) >= 0;
        });
        var active = state.refreshRunning && status.mode === "build" &&
          (!status.source_ids || status.source_ids.indexOf(sourceId) >= 0);
        button.disabled = active || queued;
        button.textContent = queued ? "已排队" : (active ? "正在建库" :
          button.getAttribute("data-build-label"));
      }
    );
  }

  //: P4-02Y-I §3: the enrichment rows of a source card and their three honest words.
  var ENRICHMENT_LABELS = [
    ["taken_time", "拍摄时间"],
    ["gps", "定位信息"],
    ["place", "地点"],
    ["face", "人物"],
    ["ocr", "文字识别"],
  ];
  var STAGE_STATE_TEXT = {
    NOT_STARTED: "未开始",
    PENDING: "待处理",
    IN_PROGRESS: "处理中",
    COMPLETE: "完成",
    BLOCKED: "受阻",
    UNAVAILABLE: "不可用",
  };
  var SEMANTIC_STATE_TEXT = {
    UNBUILT: "未建库",
    NO_IMAGES: "已扫描，无可索引图片",
    UNINDEXABLE: "无法建立语义索引",
    BUILDING: "正在建立语义索引",
    SEARCHABLE: "✓ 可以语义搜索",
    UNAVAILABLE: "语义通道不可用",
  };

  function ocrProgressText(stage) {
    var notes = [];
    if (Number(stage.succeeded) > 0) {
      notes.push("已识别 " + String(stage.succeeded) + " 张");
    }
    if (Number(stage.failed) > 0) {
      notes.push(String(stage.failed) + " 张失败");
    }
    if (Number(stage.blocked) > 0) {
      notes.push(String(stage.blocked) + " 张受阻");
    }
    if (Number(stage.pending) > 0) {
      notes.push(String(stage.pending) + " 张处理中");
    }
    if (Number(stage.unattempted) > 0) {
      notes.push(String(stage.unattempted) + " 张待处理");
    }
    var label = stage.state === "IN_PROGRESS" ? "处理中" :
      (stage.state === "PENDING" ? "待处理" :
        (stage.state === "BLOCKED" ? "受阻" :
          (stage.state === "COMPLETE" ? "已处理" :
            (STAGE_STATE_TEXT[stage.state] || String(stage.state)))));
    return notes.length ? label + "（" + notes.join("，") + "）" : label;
  }

  function semanticProgressText(progress) {
    var semantic = progress && progress.stages ? progress.stages.semantic : null;
    if (!semantic) {
      return "未知";
    }
    if (semantic.total === 0 && semantic.confirmed === 0) {
      return semantic.state === "NO_IMAGES" ? "0 / 0（无可索引图片）" : "尚未开始";
    }
    var text = String(semantic.confirmed) + " / " + String(semantic.total);
    var notes = [];
    if (Number(semantic.failed) > 0) {
      notes.push(String(semantic.failed) + " 张无法编码");
    }
    if (Number(semantic.queued) > 0) {
      notes.push(String(semantic.queued) + " 项排队中");
    }
    return notes.length ? text + "（" + notes.join("，") + "）" : text;
  }

  function semanticStateText(progress) {
    var semantic = progress && progress.stages ? progress.stages.semantic : null;
    if (!semantic) {
      return "未知";
    }
    if (semantic.reason) {
      return (SEMANTIC_STATE_TEXT[semantic.state] || String(semantic.state)) +
        "（" + String(semantic.reason) + "）";
    }
    // A refused image never becomes searchable and never blocks the rest (P4-02Y's own rule),
    // but the card must not promise a bare "可以搜索" while hiding it (review F1).
    if (semantic.state === "SEARCHABLE" && Number(semantic.failed) > 0) {
      return "✓ 可以搜索，" + String(semantic.failed) + " 张无法编码（其余不受影响）";
    }
    if (semantic.state === "UNINDEXABLE") {
      return (
        "无法建立语义索引：" + String(semantic.total) +
        " 张图片均无法编码（修好文件后再刷新）"
      );
    }
    return SEMANTIC_STATE_TEXT[semantic.state] || String(semantic.state);
  }

  //: Which action a source card offers. ``build`` never-built, ``continue`` durable backlog
  //: left (semantic incomplete or an enrichment stage with unfinished jobs), ``refresh`` the
  //: daily incremental pass of an already built source.
  function sourceActionMode(progress) {
    if (!progress || !progress.stages) {
      return "build";
    }
    var semantic = progress.stages.semantic || {};
    if (!progress.built || semantic.state === "UNBUILT" || semantic.state === "UNAVAILABLE") {
      return "build";
    }
    if (semantic.state === "BUILDING") {
      return "continue";
    }
    var names = Object.keys(progress.stages);
    for (var index = 0; index < names.length; index += 1) {
      var stage = progress.stages[names[index]];
      if (stage && stage.state === "IN_PROGRESS") {
        return "continue";
      }
      if (names[index] === "ocr" && stage && stage.state === "PENDING" &&
          stage.runtime_available !== false && Number(stage.unattempted) > 0) {
        return "continue";
      }
    }
    return "refresh";
  }

  function renderSourceOptions() {
    var current = els.browseSource.value;
    els.browseSource.textContent = "";
    var all = element("option", null, "全部图库");
    all.value = "";
    els.browseSource.appendChild(all);
    state.sources.forEach(function (source) {
      var label = source.display_name + " · #" + source.source_id;
      if (source.volume_hint) {
        label += " · " + safeText(source.volume_hint);
      }
      var option = element("option", null, label);
      option.value = String(source.source_id);
      els.browseSource.appendChild(option);
    });
    els.browseSource.value = current;
    updateBrowseScope();
  }

  function updateSearchScopeAction() {
    if (!els.searchScopeAction || !els.views || state.detailRouteActive) {
      if (els.setSearchScopeAction) els.setSearchScopeAction(null, "");
      return;
    }
    if (els.views.photoCollection && !els.views.photoCollection.hidden && state.photoCollections.collection) {
      var collection = state.photoCollections.collection;
      els.setSearchScopeAction({kind:"photo_collection", photo_collection_id:Number(collection.collection_id),
        instance_token:String(collection.instance_token), revision:Number(collection.revision), name:String(collection.name)}, "照片合集");
      return;
    }
    if (!state.sourcesLoaded || els.views.browse.hidden) {
      if (els.setSearchScopeAction) els.setSearchScopeAction(null, "");
      return;
    }
    if (state.browse.scope === "favorites") {
      els.setSearchScopeAction({ kind: "favorites" }, "收藏");
      return;
    }
    var source = state.sources.find(function (item) {
      return String(item.source_id) === String(state.browse.sourceId);
    });
    els.setSearchScopeAction(source ? { kind: "source", source_id: Number(source.source_id), name: String(source.display_name || "图库") } : null, "图库");
  }

  function updateBrowseScope() {
    if (!els.browseScope) {
      return;
    }
    var selected = state.sources.find(function (source) {
      return String(source.source_id) === String(state.browse.sourceId);
    });
    els.browseScope.hidden = !selected;
    text(
      els.browseScopeName,
      selected ? "当前仅查看「" + selected.display_name + " · #" + selected.source_id + "」" : ""
    );
  }

  function setBrowseScope(scope) {
    var next = scope === "favorites" ? "favorites" : "all";
    if (state.browse.scope === next) return;
    if (state.browse.photoSelection) state.browse.photoSelection.reset();
    state.browse.scope = next;
    state.browse.sourceId = "";
    state.browse.order = "time_desc";
    state.browse.captureFrom = "";
    state.browse.captureTo = "";
    state.browse.favoritesRevision = null;
    state.browse.loaded = false;
    state.browse.cursor = null;
    state.browse.hasMore = false;
    state.browse.returnContext = null;
    state.browse.returnPending = false;
    if (els.browseSource) els.browseSource.value = "";
    if (els.browseOrder) els.browseOrder.value = "time_desc";
    if (els.browseCaptureFrom) els.browseCaptureFrom.value = "";
    if (els.browseCaptureTo) els.browseCaptureTo.value = "";
    var favorites = next === "favorites";
    if (els.browseTitle) text(els.browseTitle, favorites ? "我的收藏" : "全部照片");
    if (els.browseIntro) {
      text(els.browseIntro, favorites ? "按最近收藏排列。收藏绑定照片内容；原图暂时离线时仍保留收藏记录。" : "拍摄时间只读取照片文件声明。");
      els.browseIntro.hidden = !favorites && state.browse.mode === "random";
    }
    if (els.browseTimePolicy) els.browseTimePolicy.hidden = favorites || state.browse.mode === "random";
    if (els.browseModeControls) els.browseModeControls.hidden = favorites;
    if (els.favoritesOrderLabel) els.favoritesOrderLabel.hidden = !favorites;
    if (els.favoritesOrder) { els.favoritesOrder.hidden = !favorites; els.favoritesOrder.value = state.browse.favoritesOrder; }
    if (els.photoSelectionWrap) els.photoSelectionWrap.hidden = !favorites && state.browse.mode !== "list";
    if (els.favoritesReload) els.favoritesReload.hidden = !favorites;
    if (els.browseControls) els.browseControls.hidden = favorites;
    if (els.browseScope) els.browseScope.hidden = favorites;
    if (els.browseListTimeHint) els.browseListTimeHint.hidden = favorites || state.browse.mode === "timeline" || state.browse.mode === "random";
    if (els.browseTimelineNote) els.browseTimelineNote.hidden = favorites || state.browse.mode !== "timeline";
    if (els.browseCaptureState) els.browseCaptureState.hidden = favorites || state.browse.mode === "timeline" || state.browse.mode === "random";
    if (els.browseTimeline) els.browseTimeline.hidden = favorites || state.browse.mode !== "timeline";
    if (els.browseListView) els.browseListView.hidden = false;
    if (state.browse.pager) state.browse.pager.setEnabled(!favorites && state.browse.mode === "list");
    updateSearchScopeAction();
  }

  function startScan(sourceId) {
    setStatus(els.sourcesStatus, "正在请求扫描…");
    request("/sources/" + encodeURIComponent(sourceId) + "/scan", { method: "POST" })
      .then(function (result) {
        var body = result.body || {};
        setStatus(
          els.sourcesStatus,
          "完整校验扫描已排队（scan #" + body.scan_id + "，job #" + body.job_id + "）"
        );
        pollJob(sourceId, body.job_id);
      })
      .catch(function (failure) {
        setStatus(els.sourcesStatus, "扫描请求失败：" + describe(failure), "error");
      });
  }

  function updateFirstUseGuide() {
    var guide = document.getElementById("first-use-guide");
    if (!guide) return;
    if (!state.sourcesLoaded || !state.embeddingManagementStatus) {
      guide.hidden = true;
      return;
    }
    var qwen = (state.embeddingManagementStatus.providers || []).find(function (provider) {
      return provider.provider_id === "qwen3-vl-embedding-2b-exif-v2";
    });
    // Hide only after the default provider is installed and has an active published profile.
    // Manifest/file presence alone is deliberately never presented as hash/GPU proof.
    guide.hidden = Boolean(state.sources.length && qwen && qwen.installed && qwen.is_active);
  }

  // -------------------------------------------------------------------- refresh
  // P4-02Y: the one-click fast refresh of the whole library (or of the named sources).
  // It is an additive entry next to "扫描": the status text distinguishes the stage the
  // server reports, so "可搜索" is shown as soon as the new content is really searchable.
  var REFRESH_STATE_TEXT = {
    IDLE: "尚未刷新",
    SCANNING: "正在快速扫描来源",
    INDEXING: "正在更新搜索索引",
    SEARCH_READY: "可搜索：新增内容已进入搜索",
    ENRICHING: "可搜索；正在生成缩略图与增强信息",
    COMPLETE: "刷新完成",
    PARTIAL_FAILED: "刷新部分完成（部分增强未完成）",
    FAILED: "刷新失败",
  };
  //: P4-02Y-I §1/§2: the first build of one source. The card calls it "开始建库"; the server
  //: runs the same additive refresh entry in its ``build`` mode.
  var REFRESH_MODE_TEXT = {
    build: "开始建库",
    refresh: "刷新图库",
  };
  //: Why a refresh could not call itself search ready (``search_ready_basis``). A terminal
  //: "刷新失败" without a reason is exactly the kind of state the source card was fixed to
  //: avoid, so the run line names the gate it stopped at (review F3 of the P4-02Y-I
  //: re-review).
  var SEARCH_GATE_TEXT = {
    "semantic-backlog": "还有语义索引项待处理",
    "semantic-confirmed-nothing": "没有图片能建立语义索引",
    "semantic-nothing-to-index": "无需建立语义索引（没有可索引图片）",
    "semantic-pending": "语义索引尚未确认完成",
    GENERATION_NOT_PUBLISHED: "该向量空间还没有任何已发布的图像索引",
    MANIFEST_UNCONFIGURED: "未配置语义模型",
    MODEL_UNAVAILABLE: "无法加载图像编码器",
    MODEL_LOAD_FAILED: "图像编码器加载失败",
    ACTIVE_PROFILE_SPACE_MISMATCH: "活动搜索档案与本模型的向量空间不一致",
    "projection-lagging": "搜索投影尚未追赶完成",
  };

  function refreshLibrary(sourceIds, mode) {
    if (!sourceIds && !state.sources.length) {
      setStatus(els.refreshStatus, "还没有添加图库来源，暂时无法开始全部建库或同步。请先填写照片文件夹路径。", "error");
      updateGlobalRefreshActions();
      return;
    }
    var timer = state.refreshTimer;
    if (timer !== null) {
      clearTimeout(timer);
      state.refreshTimer = null;
    }
    var requested = mode === "build" ? "build" : "refresh";
    setStatus(els.refreshStatus, "正在请求" + REFRESH_MODE_TEXT[requested] + "…");
    var body = {
      mode: requested,
    };
    if (els.faceProfile && els.faceProfile.value) body.face_profile = els.faceProfile.value;
    if (sourceIds) {
      body.source_ids = sourceIds;
    }
    request("/library/refresh", { method: "POST", body: body })
      .then(function (result) {
        var status = result.body || {};
        var sameActiveBuild = status.running && status.mode === "build" &&
          (sourceIds
            ? Array.isArray(status.source_ids) && sourceIds.every(function (id) {
              return status.source_ids.indexOf(id) >= 0;
            })
            : status.source_ids === null);
        if (!status.accepted && !status.queued && !sameActiveBuild) {
          setStatus(els.refreshStatus, "有任务执行中，请结束后重试。", "error");
          if (status.running) pollRefresh();
          return;
        }
        renderRefresh(status);
        pollRefresh();
      })
      .catch(function (failure) {
        setStatus(els.refreshStatus, "请求失败：" + describe(failure), "error");
        if (state.refreshRunning) {
          state.refreshTimer = setTimeout(pollRefresh, POLL_INTERVAL_MS);
        }
      });
  }

  function pollRefresh() {
    request("/library/refresh")
      .then(function (result) {
        var body = result.body || {};
        renderRefresh(body);
        var finished = body.state === "COMPLETE" ||
          body.state === "PARTIAL_FAILED" ||
          body.state === "FAILED";
        if (body.running) {
          if (state.refreshTimer !== null) {
            clearTimeout(state.refreshTimer);
          }
          state.refreshTimer = setTimeout(pollRefresh, POLL_INTERVAL_MS);
          return;
        }
        state.refreshTimer = null;
        if (finished) {
          state.browse.loaded = false;
          loadSources();
        }
      })
      .catch(function (failure) {
        state.refreshTimer = null;
        setStatus(els.refreshStatus, "读取刷新进度失败：" + describe(failure), "error");
      });
  }

  function renderRefresh(status) {
    var counters = status.counters || {};
    var sourceRows = Array.isArray(status.sources) ? status.sources : [];
    var sourcesFailed = Number(counters.sources_failed || 0);
    var failedStages = status.failed_stages || [];
    var unfinished = failedStages.indexOf("semantic-backlog") >= 0;
    var refusedAll = failedStages.indexOf("semantic-confirmed-nothing") >= 0;
    var stateText = sourcesFailed > 0
      ? (status.running
        ? "部分来源扫描失败，本轮仍在进行"
        : "部分来源扫描失败（可重试）")
      : (unfinished
        ? "尚未完成建立语义索引（可再次点击继续）"
        : (refusedAll
          ? "没有图片建立语义索引：这些图片都无法编码（可修好文件后再刷新）"
          : (REFRESH_STATE_TEXT[status.state] || String(status.state || ""))));
    var parts = [];
    if (status.mode === "build") {
      parts.push("开始建库");
    }
    parts.push(stateText);
    state.refreshRunning = Boolean(status.running);
    state.refreshStatus = status;
    updateSourceActions(els.sourcesList);
    updateGlobalRefreshActions();
    if (status.queued) {
      parts.push("已加入建库队列，当前任务结束后自动开始");
    }
    if ((status.pending_builds || []).length) {
      parts.push("等待建库 " + status.pending_builds.length + " 项");
    }
    if (status.pending_all_build) {
      parts.push("全部建库已排队（执行时包含当时登记的所有图库）");
    }
    var semantic = status.stages && status.stages.semantic ? status.stages.semantic : null;
    if (semantic && semantic.backlog_total !== undefined) {
      parts.push(
        "语义索引 " + String(semantic.backlog_confirmed) + " / " + String(semantic.backlog_total) +
        "（待处理 " + String(semantic.backlog_pending) + "）"
      );
    }
    if (!status.search_ready && status.search_ready_basis) {
      var gate = String(status.search_ready_basis);
      parts.push("原因：" + (SEARCH_GATE_TEXT[gate] || gate));
    }
    var terminal = !status.running &&
      ["COMPLETE", "PARTIAL_FAILED", "FAILED"].indexOf(status.state) >= 0 &&
      Number(counters.sources_requested || 0) > 0;
    if (terminal) {
      parts.push(refreshOutcomeSummary(counters, sourcesFailed, status.mode === "build"));
    }
    var kind = status.state === "FAILED" || sourcesFailed > 0
      ? "error"
      : (status.state === "PARTIAL_FAILED" ? "error" : "");
    setStatus(els.refreshStatus, "", kind);
    if (parts.length) {
      els.refreshStatus.hidden = false;
      var summary = element("p", null, parts.join("；"));
      els.refreshStatus.appendChild(summary);
    }
    if (terminal && sourceRows.length) {
      var details = element("details", "refresh-details");
      details.appendChild(element("summary", null, "按图库明细"));
      if (counters.scan_ms !== undefined) {
        details.appendChild(element("p", "hint", "本轮扫描累计耗时 " + String(counters.scan_ms) + " ms。"));
      }
      var list = element("ul", "refresh-source-results");
      sourceRows.forEach(function (source) {
        list.appendChild(renderRefreshSource(source, counters));
      });
      details.appendChild(list);
      els.refreshStatus.appendChild(details);
    }
  }

  function refreshOutcomeSummary(counters, sourcesFailed, isBuild) {
    var requested = Number(counters.sources_requested || 0);
    var scanned = Number(counters.sources_scanned || 0);
    var added = Number(counters.locations_added || 0);
    var missing = Number(counters.locations_missing || 0);
    var hashed = Number(counters.hash_targets || 0);
    var newAssets = Number(counters.new_assets || 0);
    var rebound = Number(counters.rebound_assets || 0);
    var unchanged = Number(counters.unchanged_reused || 0);
    var changed = added + missing + hashed + newAssets + rebound > 0;
    var action = isBuild ? "扫描" : "同步";
    var line = sourcesFailed
      ? (isBuild ? "已扫描 " : "已同步成功 ") + scanned + "/" + requested +
        " 个图库，另有 " + sourcesFailed + " 个图库" + action + "失败"
      : "已" + action + " " + scanned + "/" + requested + " 个图库";
    if (!changed) {
      line += sourcesFailed
        ? "；已成功" + action + "的图库未发现文件变化"
        : "；未发现文件变化";
      if (counters.enumerated !== undefined) {
        line += "，检查了 " + String(counters.enumerated) + " 个文件位置";
      }
      return line + "。";
    }
    var changes = [];
    if (added) changes.push("新增文件位置 " + added + " 个");
    if (missing) changes.push("变为缺失的位置 " + missing + " 个");
    if (hashed) changes.push("重新校验文件内容 " + hashed + " 个位置");
    if (newAssets) changes.push("真正新内容 " + newAssets + " 个");
    if (rebound) changes.push("重新关联已有内容 " + rebound + " 个");
    line += "；" + changes.slice(0, 5).join("、");
    return line + "。";
  }

  function renderRefreshSource(source, aggregate) {
    var sourceId = Number(source.source_id);
    var identity = state.sources.find(function (item) {
      return Number(item.source_id) === sourceId;
    });
    var name = identity ? safeText(identity.display_name) : "图库 #" + sourceId;
    var failed = String(source.state || "").toUpperCase() === "FAILED";
    var row = element("li", failed ? "error" : "");
    row.appendChild(element("strong", null, String(name)));
    row.appendChild(element("span", null, failed
      ? "：扫描失败（" + refreshFailureLabel(source.code) + "，可重试）"
      : "：扫描完成"));
    var keys = [
      ["enumerated", "检查文件位置"],
      ["unchanged_reused", "未变化并跳过内容校验的位置"],
      ["hash_targets", "重新校验文件内容的位置"],
      ["new_assets", "真正新内容"],
      ["rebound_assets", "重新关联已有内容"],
      ["locations_added", "新增文件位置"],
      ["locations_missing", "本轮新确认缺失的位置"],
      ["content_delta_count", "涉及的内容记录数"],
      ["location_delta_count", "位置状态变化记录数"],
    ];
    if (source.elapsed_ms !== undefined) keys.push(["elapsed_ms", "扫描耗时 ms"]);
    var metrics = [];
    keys.forEach(function (entry) {
      var value = source[entry[0]];
      if (value !== undefined && value !== null) {
        metrics.push(entry[1] + " " + String(value));
      }
    });
    if (!failed && metrics.length === 0) {
      keys.forEach(function (entry) {
        var value = aggregate[entry[0]];
        if (value !== undefined && value !== null) metrics.push(entry[1] + " " + String(value));
      });
    }
    if (metrics.length) row.appendChild(element("span", "hint", "（" + metrics.join("；") + "）"));
    if (source.delta_overflow) {
      row.appendChild(element("span", "hint", "变化明细达到记录上限，实际数量可能更多。"));
    }
    return row;
  }

  function refreshFailureLabel(code) {
    var labels = {
      SOURCE_OFFLINE: "来源暂时不可用",
      ACCESS_DENIED: "访问被拒绝",
      FILE_READ_ERROR: "读取文件失败",
      SOURCE_NOT_FOUND: "来源不存在",
      SCAN_INTERRUPTED: "扫描中断",
    };
    var safeCode = /^[A-Z0-9_]+$/.test(String(code || "")) ? String(code) : "未知错误";
    return labels[safeCode] || "扫描未完成（" + safeCode + "）";
  }

  function deleteSource(source) {
    // P1-05 keeps every absolute local path out of the page: the confirmation names the
    // display name only, exactly like the source list does. The registered root stays in the
    // catalog (and in the input box the user typed it into) and is never read back here.
    var message =
      "删除来源只会移除 PhotoSearch 中的记录，不会删除文件夹中的照片。\n\n" +
      "来源：" + String(source.display_name) + "\n" +
      "确认删除这个来源吗？";
    if (typeof window.confirm !== "function" || !window.confirm(message)) {
      return;
    }
    setStatus(els.sourcesStatus, "正在删除来源…");
    request("/sources/" + encodeURIComponent(source.source_id), { method: "DELETE" })
      .then(function (result) {
        var body = result.body || {};
        state.browse.loaded = false;
        setStatus(
          els.sourcesStatus,
          "已删除来源「" + String(body.display_name || source.display_name) + "」：移除 " +
            String(body.locations_removed === undefined ? 0 : body.locations_removed) +
            " 个位置记录、共享之外 " +
            String(body.assets_orphaned === undefined ? 0 : body.assets_orphaned) +
            " 个仅存于该来源的资产；原始照片 0 修改 / 0 删除。"
        );
        return loadSources();
      })
      .catch(function (failure) {
        setStatus(els.sourcesStatus, "删除来源失败：" + describe(failure), "error");
      });
  }

  function progressNode(sourceId) {
    return els.sourcesList.querySelector('[data-progress="' + sourceId + '"]');
  }

  function stopPolling() {
    if (state.pollTimer !== null) {
      clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
  }

  function pollJob(sourceId, jobId) {
    stopPolling();
    var url = "/jobs?source_id=" + encodeURIComponent(sourceId) + "&limit=" + JOB_SCAN_LIMIT;

    function describeJob(job) {
      var line =
        "扫描状态 " +
        job.state +
        " · 尝试 " +
        job.attempts +
        "/" +
        job.max_attempts +
        " · 进度 " +
        Math.round(Number(job.progress) * 100) +
        "%";
      if (job.last_error_code) {
        line += " · 最近错误 " + job.last_error_code;
      }
      return line;
    }

    function tick() {
      request(url)
        .then(function (result) {
          var jobs = (result.body && result.body.jobs) || [];
          var job = null;
          for (var index = 0; index < jobs.length; index += 1) {
            if (Number(jobs[index].job_id) === Number(jobId)) {
              job = jobs[index];
              break;
            }
          }
          if (!job && jobs.length) {
            job = jobs[jobs.length - 1];
          }
          var node = progressNode(sourceId);
          if (node && job) {
            text(node, describeJob(job));
          }
          if (job && TERMINAL_JOB_STATES[job.state]) {
            state.pollTimer = null;
            state.browse.loaded = false;
            var summary = "扫描已结束。" + describeJob(job);
            // The refreshed list rebuilds the cards, so the final summary is written
            // back afterwards instead of disappearing with the old progress line.
            loadSources().then(function () {
              var refreshed = progressNode(sourceId);
              if (refreshed) {
                text(refreshed, summary);
              }
            });
            return;
          }
          state.pollTimer = setTimeout(tick, POLL_INTERVAL_MS);
        })
        .catch(function (failure) {
          state.pollTimer = null;
          setStatus(els.sourcesStatus, "读取扫描进度失败：" + describe(failure), "error");
        });
    }
    tick();
  }

  function submitSource(event) {
    event.preventDefault();
    var name = els.sourceName.value.trim();
    var root = els.sourceRoot.value.trim();
    els.sourceFormError.hidden = true;
    if (!name || !root) {
      text(els.sourceFormError, "显示名和来源根目录都不能为空。");
      els.sourceFormError.hidden = false;
      return;
    }
    setStatus(els.sourcesStatus, "正在注册来源…");
    request("/sources", {
      method: "POST",
      body: { display_name: name, root_path: root },
    })
      .then(function () {
        els.sourceForm.reset();
        setStatus(
          els.sourcesStatus,
          "来源已注册。点「开始建库」即可扫描、建立语义索引并进入可搜索状态。"
        );
        return loadSources();
      })
      .catch(function (failure) {
        var hint = "";
        if (failure.code === "SOURCE_OVERLAP") {
          hint = "该目录与已登记来源重叠；请合并使用，或改登记来源根目录。";
        } else if (failure.code === "DATA_ROOT_INSIDE_SOURCE") {
          hint = "程序数据目录位于这个来源内部，请选择别的目录。";
        }
        text(els.sourceFormError, "注册失败：" + describe(failure) + (hint ? "\n" + hint : ""));
        els.sourceFormError.hidden = false;
        setStatus(els.sourcesStatus, "");
      });
  }

  // --------------------------------------------------------------------- browse
  function captureConditionActive() {
    return (
      state.browse.order === "capture_desc" ||
      state.browse.captureFrom !== "" ||
      state.browse.captureTo !== ""
    );
  }

  function browseFingerprint() {
    return JSON.stringify({
      source: state.browse.sourceId,
      order: state.browse.order,
      captureFrom: state.browse.captureFrom,
      captureTo: state.browse.captureTo,
    });
  }

  function persistRandomBrowse() {
    var random = state.browse.random;
    if (!random.batchIds.length && !random.recentIds.length) {
      try { window.sessionStorage.removeItem(RANDOM_BROWSE_KEY); } catch (_error) {}
      return;
    }
    var saved = {
      sourceId: random.sourceId,
      batchSize: random.batchSize,
      batchIds: random.batchIds.slice(0, 1000),
      recentIds: random.recentIds.slice(0, 1200),
      loadedPages: Math.min(20, random.loadedPages),
      scrollY: random.scrollY,
      pendingBatch: random.pendingBatch ? {
        sourceId: random.pendingBatch.sourceId,
        requestedCount: random.pendingBatch.requestedCount,
        underfilled: Boolean(random.pendingBatch.underfilled),
        assetIds: random.pendingBatch.assetIds.slice(0, 1000),
      } : null,
      savedAt: Date.now(),
    };
    try { window.sessionStorage.setItem(RANDOM_BROWSE_KEY, JSON.stringify(saved)); } catch (_error) {}
  }

  function readRandomBrowse() {
    try {
      var saved = JSON.parse(window.sessionStorage.getItem(RANDOM_BROWSE_KEY) || "null");
      if (!saved || Date.now() - Number(saved.savedAt || 0) > 30 * 60 * 1000 ||
          !Array.isArray(saved.batchIds) || saved.batchIds.length > 1000 ||
          !Array.isArray(saved.recentIds) || saved.recentIds.length > 1200) return null;
      return saved;
    } catch (_error) { return null; }
  }

  function restoreRandomSnapshot(saved) {
    if (!saved || !Array.isArray(saved.batchIds) || !Array.isArray(saved.recentIds)) return false;
    var random = state.browse.random;
    random.sourceId = String(saved.sourceId || "");
    random.batchSize = Math.max(1, Math.min(1000, Number(saved.batchSize) || 300));
    random.batchIds = saved.batchIds.map(Number).filter(function (id) { return Number.isSafeInteger(id) && id > 0; }).slice(0, 1000);
    random.recentIds = saved.recentIds.map(Number).filter(function (id) { return Number.isSafeInteger(id) && id > 0; }).slice(0, 1200);
    random.loadedPages = Math.min(20, Math.max(0, Number(saved.loadedPages) || 0));
    random.scrollY = Math.max(0, Number(saved.scrollY) || 0);
    random.pendingBatch = saved.pendingBatch && Array.isArray(saved.pendingBatch.assetIds) &&
      saved.pendingBatch.assetIds.length <= 1000 ? {
        sourceId: String(saved.pendingBatch.sourceId || ""),
        requestedCount: Math.max(1, Math.min(1000, Number(saved.pendingBatch.requestedCount) || random.batchSize)),
        underfilled: Boolean(saved.pendingBatch.underfilled),
        assetIds: saved.pendingBatch.assetIds.map(Number).filter(function (id) { return Number.isSafeInteger(id) && id > 0; }),
      } : null;
    random.assets = [];
    random.hasMore = random.loadedPages * PAGE_SIZE < random.batchIds.length;
    state.browse.sourceId = random.sourceId;
    if (els && els.browseSource) els.browseSource.value = random.sourceId;
    return true;
  }

  function updateRandomStatus(message, isError) {
    var random = state.browse.random;
    if (message) random.statusError = isError ? message : "";
    var status = random.statusError || message || (random.batchIds.length
      ? "随机展示 " + Math.max(0, random.batchIds.length - random.skippedCount) + " 张照片"
      : "当前图库范围没有抽取到可浏览的照片。");
    text(els.browseRandomState, status);
    els.browseRandomState.classList.toggle("is-error", Boolean(random.statusError));
    els.browseRandomNext.disabled = random.loading || random.nextInFlight;
    els.browseRandomNext.textContent = random.pendingBatch ? "重试下一批" : "下一批";
    els.browseRandomSize.disabled = random.loading || random.nextInFlight || Boolean(random.pendingBatch);
  }

  function activateRandomBrowse() {
    var random = state.browse.random;
    els.browseRandomSize.value = String(random.batchSize || 300);
    if (String(random.sourceId || "") !== String(state.browse.sourceId || "")) {
      resetRandomForSource();
      startRandomBatch(false);
      return;
    }
    if (!random.batchIds.length) {
      els.assetGrid.textContent = "";
      if (window.PhotoScrollPager) window.PhotoScrollPager.setSpacerHeight(els.assetGrid, 0);
      startRandomBatch(false);
      return;
    }
    if (random.assets.length) {
      renderRandomAssets();
      updateRandomStatus();
      if (random.scrollY) window.requestAnimationFrame(function () { window.scrollTo(0, random.scrollY); });
      if (state.browse.pager) state.browse.pager.update();
      return;
    }
    random.restorePagesTarget = Math.max(1, random.loadedPages || 1);
    random.loadedPages = 0;
    random.hasMore = true;
    loadRandomPage(true);
  }

  function resetRandomForSource() {
    var random = state.browse.random;
    random.requestVersion += 1;
    random.sourceId = String(state.browse.sourceId || "");
    random.batchIds = [];
    random.recentIds = [];
    random.loadedPages = 0;
    random.hasMore = false;
    random.assets = [];
    random.skippedCount = 0;
    random.pendingBatch = null;
    random.statusError = "";
    random.loading = false;
    random.nextInFlight = false;
    random.scrollY = 0;
    els.assetGrid.textContent = "";
    if (window.PhotoScrollPager) window.PhotoScrollPager.setSpacerHeight(els.assetGrid, 0);
    persistRandomBrowse();
  }

  function startRandomBatch(_explicitRestart) {
    var random = state.browse.random;
    if (random.loading || random.nextInFlight) return Promise.resolve(false);
    var version = ++random.requestVersion;
    state.browse.requestVersion += 1;
    state.browse.owed = false;
    random.sourceId = String(state.browse.sourceId || "");
    random.batchSize = Math.max(1, Math.min(1000, Math.floor(Number(els.browseRandomSize.value) || 300)));
    var requestedCount = random.batchSize;
    random.loading = true;
    random.nextInFlight = true;
    updateRandomStatus("正在抽取照片…");
    return request("/photo-random-sample", { method: "POST", body: {
      source_id: random.sourceId ? Number(random.sourceId) : null,
      count: requestedCount,
      recent_ids: random.recentIds.slice(0, 1200),
    } }).then(function (result) {
      if (version !== random.requestVersion || state.browse.mode !== "random") return false;
      var body = result.body || {};
      var ids = Array.isArray(body.asset_ids) ? body.asset_ids.map(Number).filter(function (id) {
        return Number.isSafeInteger(id) && id > 0;
      }) : [];
      ids = Array.from(new Set(ids)).slice(0, 1000);
      random.pendingBatch = { sourceId: random.sourceId, requestedCount: requestedCount,
        underfilled: Boolean(body.underfilled), assetIds: ids };
      if (!ids.length) {
        random.batchIds = [];
        random.assets = [];
        random.loadedPages = 0;
        random.hasMore = false;
        random.skippedCount = 0;
        random.pendingBatch = null;
        els.assetGrid.textContent = "";
        random.loading = false;
        random.nextInFlight = false;
        updateRandomStatus("当前图库范围没有抽取到可浏览的照片。");
        persistRandomBrowse();
        return true;
      }
      return readRandomPage(0, true, version);
    }).catch(function (failure) {
      if (version !== random.requestVersion || state.browse.mode !== "random") return false;
      random.loading = false;
      random.nextInFlight = false;
      updateRandomStatus("抽取失败：" + describe(failure) + "。当前批次已保留，可重试。", true);
      return false;
    }).finally(function () {
      if (version === random.requestVersion) {
        random.loading = false;
        random.nextInFlight = false;
        updateRandomStatus();
      }
    });
  }

  function renderRandomAssets() {
    var random = state.browse.random;
    els.assetGrid.textContent = "";
    random.assets.forEach(function (asset) { els.assetGrid.appendChild(buildTile(asset)); });
    if (window.PhotoFavorites) window.PhotoFavorites.mount(els.assetGrid);
    if (window.PhotoScrollPager) state.browse.recycledCount += window.PhotoScrollPager.trimGrid(els.assetGrid, 500);
    random.hasMore = random.loadedPages * PAGE_SIZE < random.batchIds.length;
    els.loadMore.hidden = !random.hasMore;
    els.gridSentinel.hidden = !random.hasMore;
  }

  function readRandomPage(pageIndex, replaceBatch, requestVersion) {
    var random = state.browse.random;
    var version = requestVersion == null ? random.requestVersion : requestVersion;
    if (version !== random.requestVersion || random.loading && !replaceBatch) return Promise.resolve(false);
    var ids = replaceBatch && random.pendingBatch ? random.pendingBatch.assetIds : random.batchIds;
    var scope = replaceBatch && random.pendingBatch ? random.pendingBatch.sourceId : random.sourceId;
    var start = Math.max(0, pageIndex) * PAGE_SIZE;
    var pageIds = ids.slice(start, start + PAGE_SIZE);
    if (!pageIds.length) return Promise.resolve(false);
    random.loading = true;
    state.browse.loading = true;
    updateRandomStatus("正在读取照片…");
    return request("/photo-random-summaries", { method: "POST", body: {
      source_id: scope ? Number(scope) : null, asset_ids: pageIds,
    } }).then(function (result) {
      if (version !== random.requestVersion || state.browse.mode !== "random") return false;
      var body = result.body || {};
      var assets = Array.isArray(body.assets) ? body.assets : [];
      if (replaceBatch) {
        // A failed or stale first page never clears the old visible batch.
        random.batchIds = ids.slice(0, 1000);
        random.sourceId = scope;
        random.assets = [];
        random.loadedPages = 0;
        random.skippedCount = 0;
        random.pendingBatch = null;
        random.scrollY = 0;
        state.browse.returnScrollY = 0;
        els.assetGrid.textContent = "";
        if (window.PhotoScrollPager) window.PhotoScrollPager.setSpacerHeight(els.assetGrid, 0);
        random.recentIds = Array.from(new Set(random.batchIds.concat(random.recentIds))).slice(0, 1200);
        window.scrollTo(0, 0);
      }
      var existing = new Set(random.assets.map(function (asset) { return Number(asset.asset_id); }));
      assets.forEach(function (asset) {
        var id = Number(asset.asset_id);
        if (!existing.has(id)) { existing.add(id); random.assets.push(asset); }
      });
      random.skippedCount += Math.max(0, pageIds.length - assets.length);
      random.loadedPages = pageIndex + 1;
      random.loading = false;
      state.browse.loading = false;
      renderRandomAssets();
      updateRandomStatus();
      persistRandomBrowse();
      random.restorePagesTarget = Number(random.restorePagesTarget) || 0;
      if (random.restorePagesTarget > random.loadedPages && random.hasMore) return loadRandomPage(true);
      if (random.restorePagesTarget && random.loadedPages >= random.restorePagesTarget) {
        random.restorePagesTarget = 0;
        if (random.scrollY) window.requestAnimationFrame(function () { window.scrollTo(0, random.scrollY); });
      }
      if (state.browse.pager) state.browse.pager.update();
      return true;
    }).catch(function (failure) {
      if (version !== random.requestVersion) return false;
      random.loading = false;
      state.browse.loading = false;
      updateRandomStatus("读取失败：" + describe(failure) + "。当前批次已保留，可重试。", true);
      if (state.browse.pager) state.browse.pager.update();
      return false;
    }).finally(function () {
      if (version === random.requestVersion) {
        random.loading = false;
        state.browse.loading = false;
        if (random.nextInFlight) random.nextInFlight = false;
        if (state.browse.mode === "random") updateRandomStatus();
      }
    });
  }

  function loadRandomPage(_fromRestore) {
    var random = state.browse.random;
    if (!random.hasMore || random.loading) return Promise.resolve(false);
    return readRandomPage(random.loadedPages, false, random.requestVersion);
  }

  function requestNextRandomBatch() {
    var random = state.browse.random;
    if (random.loading || random.nextInFlight) return Promise.resolve(false);
    random.nextInFlight = true;
    random.batchSize = Math.max(1, Math.min(1000, Math.floor(Number(els.browseRandomSize.value) || 300)));
    var requestedCount = random.batchSize;
    var version = ++random.requestVersion;
    updateRandomStatus("正在抽取下一批…");
    var sampled = random.pendingBatch ? Promise.resolve(null) : request("/photo-random-sample", {
      method: "POST", body: {
        source_id: random.sourceId ? Number(random.sourceId) : null,
        count: requestedCount,
        recent_ids: random.recentIds.slice(0, 1200),
      },
    });
    return sampled.then(function (result) {
      if (version !== random.requestVersion || state.browse.mode !== "random") return false;
      if (result) {
        var body = result.body || {};
        var ids = Array.isArray(body.asset_ids) ? body.asset_ids.map(Number).filter(function (id) {
          return Number.isSafeInteger(id) && id > 0;
        }) : [];
        random.pendingBatch = { sourceId: random.sourceId, requestedCount: requestedCount,
          underfilled: Boolean(body.underfilled), assetIds: Array.from(new Set(ids)).slice(0, 1000) };
      }
      if (!random.pendingBatch || !random.pendingBatch.assetIds.length) {
        random.nextInFlight = false;
        random.loading = false;
        updateRandomStatus("当前图库范围没有抽取到可浏览的照片。", true);
        return false;
      }
      persistRandomBrowse();
      return readRandomPage(0, true, version);
    }).catch(function (failure) {
      if (version !== random.requestVersion || state.browse.mode !== "random") return false;
      random.loading = false;
      random.nextInFlight = false;
      updateRandomStatus("换批失败：" + describe(failure) + "。当前批次已保留，可重试。", true);
      return false;
    }).finally(function () {
      if (version === random.requestVersion) {
        random.loading = false;
        random.nextInFlight = false;
        updateRandomStatus();
      }
    });
  }

  function timelineRenderAsset(item) {
    var card = element("button", "photo-timeline__card");
    card.type = "button";
    card.setAttribute("aria-label", "打开照片 " + item.asset_id + "，" + (item.display_time || item.time_label));
    var image = element("img", null, "");
    image.alt = "照片 " + item.asset_id;
    image.loading = "lazy";
    image.decoding = "async";
    image.src = item.thumbnail_url;
    var filename = item.filename || "文件信息不可用";
    var directory = item.directory_path || "";
    var name = element("span", "photo-timeline__filename", filename);
    card.append(image, name);
    card.dataset.assetId = String(item.asset_id);
    if (directory) card.append(element("small", "photo-timeline__directory", directory));
    if (directory && item.filename) card.title = directory + filename;
    return card;
  }

  function showTimelineEmptyMessage(params) {
    if (!params || !state.browse.timelineMount || !state.sourcesLoaded) return;
    var status = els.browseTimeline.querySelector(".photo-timeline__status");
    if (!status || status.classList.contains("is-error")) return;
    var sourceId = params.source_id;
    var source = sourceId ? state.sources.find(function (item) {
      return String(item.source_id) === String(sourceId);
    }) : null;
    var message;
    if (state.sourcesLoadFailed) {
      message = "图库状态读取失败，请在图库管理中重新读取；当前无法判断照片是否已建库。";
    } else if (!state.sources.length) {
      message = "还没有添加图库。请打开“图库管理”，展开“添加图库来源”，填写照片文件夹路径后开始建库。";
    } else if (
      state.refreshRunning &&
      (!sourceId || !Array.isArray(state.refreshStatus.source_ids) ||
        state.refreshStatus.source_ids.indexOf(Number(sourceId)) >= 0)
    ) {
      message = state.refreshStatus.mode === "build"
        ? "正在为图库建库，完成后即可查看扫描结果。"
        : "图库正在刷新，完成后即可查看最新结果。";
    } else if (source && source.last_successful_scan == null) {
      message = "这个图库目前没有可浏览的扫描结果。若正在扫描或建库，请等待完成；否则可在图库管理中点击“开始建库”。";
    } else if (params.year || params.month || params.seek_year || params.seek_month) {
      message = "这个年月在当前图库范围内没有匹配照片。可以清除年月筛选或直接定位其他年月。";
    } else if (params.bucket === "unknown") {
      message = "当前图库范围没有“日期未知”的照片。";
    } else if (params.bucket === "pending") {
      message = "当前图库范围没有“日期待就绪”的照片。";
    } else if (params.time_basis === "capture" && params.bucket !== "all") {
      message = "当前没有可信拍摄日期的照片。可打开“日期未知”“日期待就绪”或切换到加入图库时间浏览。";
    } else {
      message = "当前图库范围还没有可浏览的照片。若来源尚未完成扫描，可在图库管理中建库或同步变化。";
    }
    status.textContent = message;
  }

  function showBrowseMode(mode) {
    if (state.browse.scope === "favorites") mode = "list";
    if (mode !== "timeline" && mode !== "list" && mode !== "random") mode = "list";
    var previousMode = state.browse.mode;
    var useTimeline = mode === "timeline";
    var useRandom = mode === "random";
    if (useTimeline && state.browse.photoSelection) state.browse.photoSelection.reset();
    if (state.browse.mode === "random" && !useRandom) {
      state.browse.random.requestVersion += 1;
      state.browse.random.loading = false;
      state.browse.random.nextInFlight = false;
      state.browse.loading = false;
      state.browse.random.scrollY = Math.max(0, Number(window.scrollY || window.pageYOffset) || 0);
      persistRandomBrowse();
    }
    state.browse.mode = mode;
    if (els.browseStatus) els.browseStatus.hidden = useRandom;
    if (els.browseIntro) els.browseIntro.hidden = useRandom;
    if (els.browseTimePolicy) els.browseTimePolicy.hidden = useRandom || state.browse.scope === "favorites";
    if (els.assetGrid) els.assetGrid.setAttribute("aria-label", useRandom ? "随机照片" : "按加入时间排列的图片");
    els.browseTimeline.hidden = !useTimeline;
    els.browseListView.hidden = useTimeline;
    els.browseShowTimeline.hidden = state.browse.scope === "favorites";
    els.browseShowList.hidden = state.browse.scope === "favorites";
    els.browseShowRandom.hidden = state.browse.scope === "favorites";
    els.browseShowTimeline.setAttribute("aria-pressed", useTimeline ? "true" : "false");
    els.browseShowList.setAttribute("aria-pressed", mode === "list" ? "true" : "false");
    els.browseShowRandom.setAttribute("aria-pressed", useRandom ? "true" : "false");
    els.browseCaptureState.hidden = useTimeline || useRandom;
    els.browseListFilters.hidden = useTimeline || useRandom;
    els.browseRandomControls.hidden = !useRandom;
    els.browseRandomState.hidden = !useRandom;
    if (els.photoSelectionWrap) els.photoSelectionWrap.hidden = state.browse.scope !== "favorites" && (useTimeline || useRandom);
    els.browseListTimeHint.hidden = state.browse.scope === "favorites" || useTimeline || useRandom;
    els.browseTimelineNote.hidden = !useTimeline;
    if (!useTimeline) {
      if (state.browse.pager) state.browse.pager.setEnabled(true);
      if (useRandom) {
        activateRandomBrowse();
        return;
      }
      if (previousMode === "random" || !state.browse.loaded || state.browse.binding !== browseFingerprint()) loadBrowse(true);
      return;
    }
    if (state.browse.pager) state.browse.pager.setEnabled(false);
    if (!window.PhotoTimeline) {
      setStatus(els.browseStatus, "时间轴组件暂不可用，请刷新页面后重试。", "error");
      showBrowseMode("list");
      return;
    }
    var scope = state.browse.sourceId
      ? { kind: "source", source_id: Number(state.browse.sourceId) }
      : { kind: "all" };
    if (!state.browse.timelineMount) {
      state.browse.timelineMount = window.PhotoTimeline.mount({
        host: els.browseTimeline,
        scope: scope,
        returnAnchor: state.browse.returnPending ? state.browse.timelineReturnAnchor : null,
        returnAssetId: state.browse.returnPending ? state.browse.returnAssetId : null,
        adapter: {
          renderAsset: timelineRenderAsset,
          onRequest: function () {
            state.browse.timelineEmptyParams = null;
          },
          onPage: function (body, _page, params) {
            if (body.assets && body.assets.length) {
              state.browse.timelineEmptyParams = null;
              return;
            }
            state.browse.timelineEmptyParams = params;
            showTimelineEmptyMessage(params);
          },
          onOpen: function (item) {
            state.browse.timelineReturnAnchor = item.anchor || "";
            showDetail(item.asset_id);
          },
          onRestoreAsset: function (tile) {
            window.requestAnimationFrame(function () {
              var scrollY = state.browse.returnScrollY;
              if (tile && state.browse.returnAssetTop !== null) {
                scrollY = (window.scrollY || window.pageYOffset || 0) +
                  tile.getBoundingClientRect().top - state.browse.returnAssetTop;
              }
              window.scrollTo(0, Math.max(0, scrollY));
            });
          },
        },
      });
      state.browse.timelineScope = JSON.stringify(scope);
    } else if (state.browse.timelineScope !== JSON.stringify(scope)) {
      state.browse.timelineScope = JSON.stringify(scope);
      var restoreAnchor = state.browse.returnPending ? state.browse.timelineReturnAnchor : null;
      var restoreAssetId = state.browse.returnPending ? state.browse.returnAssetId : null;
      state.browse.timelineMount.setScope(scope, restoreAnchor, restoreAssetId);
    } else if (state.browse.returnPending && state.browse.timelineReturnAnchor) {
      state.browse.timelineMount.setReturnAnchor(state.browse.returnAssetId, state.browse.timelineReturnAnchor);
    }
  }

  function loadBrowse(reset) {
    if (state.browse.scope !== "favorites" && state.browse.mode === "timeline") return;
    if (reset) {
      if (state.browse.photoSelection) state.browse.photoSelection.reset();
      state.browse.requestVersion += 1;
      releaseObjectUrls();
      state.browse.cursor = null;
      state.browse.hasMore = false;
      state.browse.count = 0;
      state.browse.recycledCount = 0;
      state.browse.timeCache = {};
      if (state.browse.pager) state.browse.pager.reset();
      setStatus(els.browseCaptureState, "");
      els.assetGrid.textContent = "";
      els.loadMore.hidden = true;
      els.gridSentinel.hidden = true;
    }
    if (state.browse.loading) {
      if (reset) state.browse.owed = true;
      return Promise.resolve(false);
    }
    if (state.browse.scope === "favorites") return loadFavorites(reset);
    if (continuationRefused(reset)) {
      return Promise.resolve(false);
    }
    updateBrowseScope();
    state.browse.loading = true;
    var requestVersion = state.browse.requestVersion;
    setStatus(els.browseStatus, reset ? "正在加载图片…" : "正在加载下一页…");
    var params = new URLSearchParams();
    params.set("order", state.browse.order);
    params.set("limit", String(PAGE_SIZE));
    if (state.browse.sourceId) {
      params.set("source_id", state.browse.sourceId);
    }
    if (state.browse.captureFrom) {
      params.set("capture_from", state.browse.captureFrom);
    }
    if (state.browse.captureTo) {
      params.set("capture_to_exclusive", state.browse.captureTo);
    }
    if (state.browse.cursor) {
      params.set("cursor", state.browse.cursor);
    }
    return request("/assets?" + params.toString())
      .then(function (result) {
        if (requestVersion !== state.browse.requestVersion) {
          state.browse.loading = false;
          if (state.browse.owed) {
            state.browse.owed = false;
            loadBrowse(true);
          }
          return;
        }
        var body = result.body || {};
        var assets = body.assets || [];
        assets.forEach(function (asset) {
          els.assetGrid.appendChild(buildTile(asset));
        });
        if (window.PhotoFavorites) window.PhotoFavorites.mount(els.assetGrid);
        if (window.PhotoScrollPager) {
          state.browse.recycledCount += window.PhotoScrollPager.trimGrid(els.assetGrid, 500);
        }
        state.browse.count += assets.length;
        state.browse.cursor = body.next_cursor || null;
        state.browse.hasMore = Boolean(body.has_more);
        state.browse.loaded = true;
        state.browse.loading = false;
        restoreBrowsePosition();
        renderCaptureState(body);
        setStatus(els.browseStatus, browseSummary());
        els.loadMore.hidden = !state.browse.hasMore;
        els.gridSentinel.hidden = !state.browse.hasMore;
        if (state.browse.pager) state.browse.pager.update();
        if (state.browse.owed) {
          state.browse.owed = false;
          loadBrowse(true);
        }
        return true;
      })
      .catch(function (failure) {
        state.browse.loading = false;
        if (requestVersion !== state.browse.requestVersion) {
          if (state.browse.owed) {
            state.browse.owed = false;
            loadBrowse(true);
          }
          return true;
        }
        els.loadMore.hidden = false;
        els.gridSentinel.hidden = !state.browse.hasMore;
        var hint = "";
        if (failure.code === "INVALID_TIME_RANGE") {
          hint =
            "拍摄时间范围只支持 YYYY-MM-DD 或 YYYY-MM-DDTHH:MM:SS[.毫秒] 的墙上时间写法，" +
            "不接受时区后缀，且起始必须早于结束（左闭右开）。";
        } else if (failure.code === "CATALOG_FILTER_REVISION_STALE") {
          hint =
            "拍摄时间列表绑定的权威 revision 在这次翻页期间变化了：服务端拒绝续页以避免重页或漏页，" +
            "请点“刷新”从第一页重新开始。";
        }
        setStatus(
          els.browseStatus,
          "加载图片失败：" + describe(failure) + (hint ? "\n" + hint : ""),
          "error"
        );
        if (state.browse.pager) state.browse.pager.update();
        if (state.browse.owed) {
          state.browse.owed = false;
          loadBrowse(true);
        }
        return false;
      });
  }

  function loadFavorites(reset) {
    if (state.browse.loading) {
      if (reset) state.browse.owed = true;
      return Promise.resolve(false);
    }
    state.browse.loading = true;
    var requestVersion = state.browse.requestVersion;
    var params = new URLSearchParams();
    params.set("limit", String(PAGE_SIZE));
    params.set("order", state.browse.favoritesOrder);
    if (state.browse.cursor) params.set("cursor", state.browse.cursor);
    setStatus(els.browseStatus, reset ? "正在读取收藏…" : "正在读取下一页收藏…");
    return request("/favorites?" + params.toString()).then(function (result) {
      if (requestVersion !== state.browse.requestVersion || state.browse.scope !== "favorites") {
        state.browse.loading = false;
        if (state.browse.owed) { state.browse.owed = false; loadBrowse(true); }
        return;
      }
      var body = result.body || {};
      var assets = body.assets || [];
      assets.forEach(function (asset) { els.assetGrid.appendChild(buildTile(asset)); });
      if (window.PhotoFavorites) window.PhotoFavorites.mount(els.assetGrid, function (favoriteResult) {
        if (state.browse.scope === "favorites" && !favoriteResult.is_favorite) loadBrowse(true);
      }, null, { ids: assets.map(function (asset) { return asset.asset_id; }), isFavorite: true, revision: body.favorites_revision });
      if (window.PhotoScrollPager) {
        state.browse.recycledCount += window.PhotoScrollPager.trimGrid(els.assetGrid, 500);
      }
      state.browse.count += assets.length;
      state.browse.cursor = body.next_cursor || null;
      state.browse.hasMore = Boolean(body.has_more);
      state.browse.favoritesRevision = Number(body.favorites_revision);
      state.browse.loaded = true;
      state.browse.loading = false;
      restoreBrowsePosition();
      setStatus(els.browseStatus, state.browse.count ? "已显示 " + state.browse.count + " 张收藏照片 · " +
        (state.browse.favoritesOrder === "recent" ? "按最近收藏排列" : "按可信拍摄时间排列，未知时间置尾") : "还没有收藏照片。打开照片后点击“☆ 收藏”即可加入。");
      if (els.favoritesReload) els.favoritesReload.hidden = true;
      els.loadMore.hidden = !state.browse.hasMore;
      els.gridSentinel.hidden = !state.browse.hasMore;
      if (state.browse.pager) state.browse.pager.update();
      if (state.browse.owed) { state.browse.owed = false; loadBrowse(true); }
    }).catch(function (failure) {
      state.browse.loading = false;
      if (requestVersion !== state.browse.requestVersion) return;
      var hint = failure.code === "FAVORITES_REVISION_STALE" ? " 收藏内容已变化，请刷新后从第一页重新读取。" : "";
      setStatus(els.browseStatus, "读取收藏失败：" + describe(failure) + hint, "error");
      if (els.favoritesReload) els.favoritesReload.hidden = false;
      els.loadMore.hidden = false;
      if (state.browse.pager) state.browse.pager.update();
    });
  }

  function continuationRefused(reset) {
    if (reset) {
      state.browse.binding = browseFingerprint();
      return false;
    }
    if (state.browse.binding && state.browse.binding !== browseFingerprint()) {
      setStatus(
        els.browseStatus,
        "筛选条件已经改变：旧游标只能沿用首次加载时的条件，不能续页。请点“刷新”按当前条件重新开始。",
        "error"
      );
      return true;
    }
    return false;
  }

  function renderCaptureState(body) {
    var payload = body || {};
    var block = payload.taken_time || null;
    state.browse.captureState = block === null ? null : String(block.state);
    var lines = [];
    // The server's normalized echo: the fixed-width wall-clock spelling of what it *used*,
    // which is the honest answer when the user typed the short ``2022-01-01`` form.
    var from = payload.capture_from === undefined ? null : payload.capture_from;
    var to = payload.capture_to_exclusive === undefined ? null : payload.capture_to_exclusive;
    if (from !== null || to !== null) {
      lines.push(
        "实际生效的拍摄时间范围：" +
          (from === null ? "开始不限" : safeText(from)) +
          " 至 " +
          (to === null ? "结束不限" : safeText(to)) +
          "（含开始，不含结束）。"
      );
    }
    if (block) {
      lines.push(
        "拍摄时间状态：" +
          (CAPTURE_STATE_TEXT[block.state] || String(block.state)) +
          "（已抽取 " +
          Number(block.extracted_assets) +
          "/" +
          Number(block.assets_total) +
          "，其中有拍摄时间 " +
          Number(block.capture_time_assets) +
          "）"
      );
    }
    setStatus(els.browseCaptureState, lines.join(" "));
  }

  function browseSummary() {
    var orderText =
      state.browse.order === "capture_desc"
        ? "按拍摄时间从新到旧，无拍摄时间的排在最后"
        : state.browse.order === "asset_id"
          ? "按资产编号"
          : "按加入时间从新到旧";
    if (!state.browse.count) {
      var captureActive = state.browse.captureFrom !== "" || state.browse.captureTo !== "";
      var captureState = state.browse.captureState;
      if (captureActive && (captureState === "PENDING" || captureState === "PARTIAL")) {
        // "Not extracted yet" must never be reported as "no match".
        return (
          "这个时间范围内暂时没有结果，但" +
          (CAPTURE_STATE_TEXT[captureState] || String(captureState)) +
          "：先完成拍摄时间抽取，再判断是否真的没有命中。" +
          orderText +
          "排序仍然有效。"
        );
      }
      if (captureActive && (captureState === "READY" || captureState === "NO_CAPTURE_TIME")) {
        return (
          "这个时间范围内确实没有命中的照片（" +
          (CAPTURE_STATE_TEXT[captureState] || String(captureState)) +
          "）。"
        );
      }
      return (
        "当前条件下没有找到照片，可以调整或清除筛选（" + orderText + "）。"
      );
    }
    return (
      "已加载 " +
      state.browse.count +
      " 张（" +
      orderText +
      "）" +
      (state.browse.hasMore ? "，还有更多。" : "，已经到底。") +
      (state.browse.recycledCount ? " 为保持浏览流畅，较早的 " + state.browse.recycledCount + " 张照片已从当前页面暂时移除，原照片没有删除。" : "")
    );
  }

  //: One normalized offset. A row without a recorded offset is never shown as UTC.
  function offsetText(minutes) {
    if (minutes === null || minutes === undefined) {
      return "";
    }
    var value = Number(minutes);
    if (!isFinite(value)) {
      return "";
    }
    var sign = value < 0 ? "-" : "+";
    var absolute = Math.abs(value);
    var hours = Math.floor(absolute / 60);
    var rest = Math.round(absolute % 60);
    var pad = function (part) {
      return (part < 10 ? "0" : "") + part;
    };
    return "（" + sign + pad(hours) + ":" + pad(rest) + "）";
  }

  //: The one sentence a tile or the detail view shows for a taken-time projection.
  function captureLine(view) {
    if (!view) {
      return "拍摄时间未抽取";
    }
    var source = TIME_SOURCE_TEXT[view.time_source] || String(view.time_source || "未知来源");
    if (view.label === "capture") {
      return (
        "拍摄时间 " +
        String(view.taken_at) +
        offsetText(view.timezone_offset_minutes) +
        "（" +
        String(view.time_confidence) +
        " · " +
        source +
        "）"
      );
    }
    return "文件时间" + (view.taken_at ? " " + String(view.taken_at) : "") + "（" + source + "）";
  }

  function paintCaptureTime(assetId, node) {
    var key = String(assetId);
    if (Object.prototype.hasOwnProperty.call(state.browse.timeCache, key)) {
      text(node, captureLine(state.browse.timeCache[key]));
      return;
    }
    text(node, "拍摄时间读取中…");
    request("/assets/" + encodeURIComponent(key))
      .then(function (result) {
        var view = (result.body || {}).taken_time || null;
        state.browse.timeCache[key] = view;
        text(node, captureLine(view));
      })
      .catch(function (failure) {
        text(node, "拍摄时间读取失败：" + describe(failure));
      });
  }

  function buildTile(asset) {
    var tile = element("li", "card-tile");
    tile.dataset.assetId = String(asset.asset_id);
    var frame = element("button", "thumb-frame");
    frame.type = "button";
    frame.setAttribute("aria-label", state.browse.mode === "random" ? "查看照片" : "查看照片 " + asset.asset_id);
    frame.addEventListener("click", function () { showDetail(asset.asset_id); });
    frame.dataset.assetId = String(asset.asset_id);
    frame.dataset.relativeLabel = relativeLabel(asset);
    if (asset.availability === "missing") {
      frame.dataset.originalMissing = "true";
    }
    observeThumbnail(frame);
    tile.appendChild(frame);
    if (window.PhotoFavorites) tile.appendChild(window.PhotoFavorites.button(asset.asset_id));

    var caption = element("div", "tile-caption");
    var currentSource = asset.current_location
      ? state.sources.find(function (source) {
          return String(source.source_id) === String(asset.current_location.source_id);
        })
      : null;
    var locationLine = currentSource
      ? currentSource.display_name + " · " + relativeLabel(asset)
      : relativeLabel(asset);
    if (asset.current_location && Number(asset.location_count) > 1) {
      locationLine += " · 另有 " + String(Number(asset.location_count) - 1) + " 个位置";
    }
    caption.appendChild(element("span", null, locationLine));
    var meta = element("span", "meta", state.browse.mode === "random"
      ? "加入时间 " + formatMoment(asset.created_at) + " · " + asset.location_count + " 个位置"
      : "资产 #" + asset.asset_id + " · 加入时间 " + formatMoment(asset.created_at) + " · " + asset.location_count + " 个位置");
    caption.appendChild(meta);
    if (state.browse.mode !== "random" && captureConditionActive()) {
      // ADR-0011 §8.1: the list carries the honesty block, not a per-row time projection, so
      // the per-row sentence comes from the closed detail object of that same asset. It is
      // fetched lazily for the tiles the user actually looks at.
      var timeLine = element("span", "tile-time", "");
      timeLine.setAttribute("data-capture-time", String(asset.asset_id));
      caption.appendChild(timeLine);
      paintCaptureTime(asset.asset_id, timeLine);
    }
    if (state.browse.mode !== "random") {
      var open = element("button", "tile-link", asset.availability === "missing" ? "查看离线详情" : "查看详情");
      open.type = "button";
      open.addEventListener("click", function () {
        showDetail(asset.asset_id);
      });
      caption.appendChild(open);
    }
    tile.appendChild(caption);
    return tile;
  }

  // --------------------------------------------------------------------- detail
  function saveBrowseReturn(assetId) {
    if (state.browse.mode === "random") {
      state.browse.random.scrollY = Math.max(0, Number(window.scrollY || window.pageYOffset) || 0);
      persistRandomBrowse();
    }
    var snapshot = {
      assetId: String(assetId),
      scope: state.browse.scope,
      sourceId: state.browse.sourceId,
      order: state.browse.order,
      captureFrom: state.browse.captureFrom,
      captureTo: state.browse.captureTo,
      scrollY: state.browse.returnScrollY,
      returnAssetId: state.browse.returnAssetId,
      returnAssetTop: state.browse.returnAssetTop,
      count: state.browse.count,
      savedAt: Date.now(),
      mode: state.browse.mode,
      timelineReturnAnchor: state.browse.timelineReturnAnchor,
      random: state.browse.mode === "random" ? readRandomBrowse() : null,
    };
    state.browse.returnContext = snapshot;
    try { window.sessionStorage.setItem(BROWSE_RETURN_KEY, JSON.stringify(snapshot)); } catch (_error) {}
  }

  function restoreBrowseReturn(assetId) {
    var snapshot = null;
    try { snapshot = JSON.parse(window.sessionStorage.getItem(BROWSE_RETURN_KEY) || "null"); } catch (_error) {}
    if (!snapshot || String(snapshot.assetId) !== String(assetId)) return false;
    setBrowseScope(snapshot.scope === "favorites" ? "favorites" : "all");
    state.browse.sourceId = String(snapshot.sourceId || "");
    state.browse.order = String(snapshot.order || "time_desc");
    state.browse.captureFrom = String(snapshot.captureFrom || "");
    state.browse.captureTo = String(snapshot.captureTo || "");
    if (snapshot.random) restoreRandomSnapshot(snapshot.random);
    state.browse.mode = snapshot.mode === "timeline" ? "timeline" : (snapshot.mode === "random" ? "random" : "list");
    state.browse.timelineReturnAnchor = String(snapshot.timelineReturnAnchor || "");
    state.browse.returnScrollY = Math.max(0, Number(snapshot.scrollY) || 0);
    state.browse.returnAssetId = String(snapshot.returnAssetId || assetId);
    state.browse.returnAssetTop = Number.isFinite(Number(snapshot.returnAssetTop)) ? Number(snapshot.returnAssetTop) : null;
    var targetCount = Math.max(0, Number(snapshot.count) || 0);
    state.browse.returnTruncated = targetCount > PAGE_SIZE * 5;
    state.browse.returnTargetCount = Math.min(targetCount, PAGE_SIZE * 5);
    state.browse.returnContext = snapshot;
    state.browse.returnPending = true;
    state.browse.loaded = false;
    // Sources may have finished loading while the full details page was open.
    // Restore visible filters now; loadSources repeats this if options arrive later.
    els.browseSource.value = state.browse.sourceId;
    els.browseOrder.value = state.browse.order;
    els.browseCaptureFrom.value = state.browse.captureFrom;
    els.browseCaptureTo.value = state.browse.captureTo;
    try { window.sessionStorage.removeItem(BROWSE_RETURN_KEY); } catch (_error) {}
    return true;
  }

  function clearBrowseReturn() {
    state.browse.returnContext = null;
    try { window.sessionStorage.removeItem(BROWSE_RETURN_KEY); } catch (_error) {}
  }

  function restoreBrowsePosition() {
    if (!state.browse.returnPending) return;
    if (state.browse.mode === "random") {
      var random = state.browse.random;
      if (!random.batchIds.length) {
        state.browse.returnPending = false;
        return;
      }
      random.restorePagesTarget = Math.max(1, random.loadedPages || 1);
      random.loadedPages = 0;
      random.assets = [];
      random.hasMore = true;
      state.browse.returnPending = false;
      loadRandomPage(true);
      return;
    }
    if (state.browse.mode === "timeline") {
      state.browse.returnTargetCount = 0;
      state.browse.returnPending = false;
      return;
    }
    if (!state.browse.loaded) return;
    if (state.browse.returnTruncated) {
      state.browse.returnTargetCount = 0;
      state.browse.returnPending = false;
      state.browse.returnTruncated = false;
      setStatus(els.browseStatus, browseSummary() + " 先返回首屏：原位置过深，已按预算限制恢复页数。", "error");
      window.requestAnimationFrame(function () { window.scrollTo(0, 0); });
      return;
    }
    if (state.browse.count < state.browse.returnTargetCount && state.browse.hasMore) {
      loadBrowse(false);
      return;
    }
    state.browse.returnTargetCount = 0;
    state.browse.returnPending = false;
    window.requestAnimationFrame(function () {
      var scrollY = state.browse.returnScrollY;
      var returnTile = els.assetGrid.querySelector('[data-asset-id="' + state.browse.returnAssetId + '"]');
      if (returnTile && state.browse.returnAssetTop !== null) {
        scrollY += returnTile.getBoundingClientRect().top - state.browse.returnAssetTop;
      }
      window.scrollTo(0, Math.max(0, scrollY));
    });
  }

  function showDetail(assetId) {
    if (state.browse.mode === "random") {
      state.browse.random.scrollY = Math.max(0, Number(window.scrollY || window.pageYOffset) || 0);
      persistRandomBrowse();
    }
    var returnTile = (state.browse.mode === "timeline" ? els.browseTimeline : els.assetGrid)
      .querySelector('[data-asset-id="' + String(assetId) + '"]');
    if (!window.AIPhotoInfo) { location.hash = "#/asset/" + encodeURIComponent(assetId); return; }
    if (state.detailViewer && state.detailViewer.dialog.open) state.detailViewer.close();
    state.detailViewer = window.AIPhotoInfo.open(assetId, {
      title: "查看照片", alt: state.browse.mode === "random" ? "照片" : "照片 #" + assetId,
      returnFocus: returnTile && returnTile.querySelector("button"),
      onOpenFullDetails: function (id) {
        state.browse.returnScrollY = Math.max(0, Number(window.scrollY || window.pageYOffset) || 0);
        state.browse.returnAssetId = String(id);
        state.browse.returnAssetTop = returnTile ? returnTile.getBoundingClientRect().top : null;
        saveBrowseReturn(id);
        return window.AIPhotoInfo.navigateToDetails(id, "home");
      },
      onFavoriteChanged: function (id, result) {
        if (state.browse.scope !== "favorites" && window.PhotoFavorites) {
          window.PhotoFavorites.invalidate(els.assetGrid);
          window.PhotoFavorites.invalidate(els.browseTimeline);
        }
        if (state.browse.scope !== "favorites" || result.is_favorite) return;
        var scrollY = Math.max(0, Number(window.scrollY || window.pageYOffset) || 0);
        var tile = els.assetGrid.querySelector('[data-asset-id="' + String(id) + '"]');
        if (tile) tile.remove();
        state.browse.count = Math.max(0, state.browse.count - 1);
        state.browse.cursor = null;
        state.browse.hasMore = false;
        state.browse.favoritesRevision = Number(result.favorites_revision);
        state.browse.loaded = false;
        state.browse.requestVersion += 1;
        state.browse.loading = false;
        loadBrowse(true).then(function () {
          window.requestAnimationFrame(function () { window.scrollTo(0, scrollY); });
        });
      },
      onClose: function () {
        state.detailViewer = null;
        if (state.detailRouteTransition) { state.detailRouteTransition = false; return; }
        if (state.browse.scope === "favorites" && returnTile && !returnTile.isConnected) {
          var nextTile = els.assetGrid.querySelector(".thumb-frame");
          if (nextTile) nextTile.focus();
          else els.browseStatus.focus();
        }
      }
    });
  }

  function renderDetail(assetId) {
    showView("detail");
    window.scrollTo(0, 0);
    state.detailPreviewFit = null;
    if (state.detailInfoMount) state.detailInfoMount.dispose();
    var detailSeq = state.detailSeq = (state.detailSeq || 0) + 1;
    els.detailBody.textContent = "";
    els.detailBody.appendChild(element("p", "state-block", "正在读取图片详情…"));
    request("/assets/" + encodeURIComponent(assetId))
      .then(function (result) {
        if (state.detailSeq === detailSeq) drawDetail(result.body || {}, assetId);
      })
      .catch(function (failure) {
        if (state.detailSeq !== detailSeq) return;
        els.detailBody.textContent = "";
        els.detailBody.appendChild(
          element("p", "error", "读取图片详情失败：" + describe(failure))
        );
      });
  }

  function drawDetail(body, assetId) {
    els.detailBody.textContent = "";
    var mapReturn = null;
    try { mapReturn = JSON.parse(sessionStorage.getItem("aisearch.map-return.v1") || "null"); } catch (_) {}
    var mapReturnCenter = mapReturn && Array.isArray(mapReturn.center) ? mapReturn.center.map(Number) : [];
    var hasValidMapReturn = Boolean(mapReturn && mapReturn.viewOnly === true && mapReturn.version !== undefined &&
      mapReturn.version !== null && isFinite(Number(mapReturn.version)) &&
      mapReturnCenter.length >= 2 && mapReturnCenter.every(isFinite) &&
      mapReturnCenter[0] >= -180 && mapReturnCenter[0] <= 180 && mapReturnCenter[1] >= -90 && mapReturnCenter[1] <= 90 &&
      isFinite(Number(mapReturn.zoom)) && Number(mapReturn.zoom) >= 0 && Number(mapReturn.zoom) <= 19);
    if (hasValidMapReturn) {
      var mapBack = element("a", "nav-link", "返回地图相册");
      mapBack.href = "/map";
      els.detailBody.appendChild(mapBack);
    }
    var label = detailLabel(body, assetId);
    var figure = element("figure", "detail-figure");
    var controls = element("div", "detail-preview-controls");
    var zoomOut = element("button", null, "−");
    zoomOut.type = "button";
    zoomOut.disabled = true;
    zoomOut.setAttribute("aria-label", "缩小预览");
    var fit = element("button", null, "适合窗口");
    fit.type = "button";
    fit.disabled = true;
    var actual = element("button", null, "实际大小");
    actual.type = "button";
    actual.disabled = true;
    var zoomIn = element("button", null, "+");
    zoomIn.type = "button";
    zoomIn.disabled = true;
    zoomIn.setAttribute("aria-label", "放大预览");
    var zoomLabel = element("span", "hint", "读取尺寸中…");
    [zoomOut, fit, actual, zoomIn, zoomLabel].forEach(function (control) {
      controls.appendChild(control);
    });
    figure.appendChild(controls);
    var holder = element("div", "thumb-frame detail-preview-frame");
    figure.appendChild(holder);
    var caption = element("figcaption", "hint", "正在加载预览…");
    figure.appendChild(caption);
    var viewer = element("div", "detail-viewer-layout");
    viewer.appendChild(figure);
    var infoHost = element("aside", "photo-info-panel", "");
    viewer.appendChild(infoHost);
    els.detailBody.appendChild(viewer);
    if (window.AIPhotoInfo) {
      state.detailInfoMount = window.AIPhotoInfo.render(infoHost, assetId);
    }
    var advanced = element("details", "detail-advanced");
    var advancedTitle = element("summary", null, "更多信息与位置记录");
    advanced.appendChild(advancedTitle);
    els.detailBody.appendChild(advanced);

    var preview = { image: null, naturalWidth: 0, naturalHeight: 0, scale: 1, mode: "fit" };
    function applyScale(scale) {
      if (!preview.image || !preview.naturalWidth || !preview.naturalHeight) {
        return;
      }
      preview.scale = Math.max(0.1, Math.min(8, scale));
      preview.image.style.width = Math.round(preview.naturalWidth * preview.scale) + "px";
      preview.image.style.height = Math.round(preview.naturalHeight * preview.scale) + "px";
      zoomLabel.textContent = Math.round(preview.scale * 100) + "%";
      zoomOut.disabled = preview.scale <= 0.1;
      zoomIn.disabled = preview.scale >= 8;
    }
    function fitPreview() {
      if (!preview.image || !preview.naturalWidth || !preview.naturalHeight) {
        return;
      }
      preview.mode = "fit";
      var availableWidth = Math.max(1, holder.clientWidth - 16);
      var availableHeight = Math.max(1, holder.clientHeight - 16);
      applyScale(Math.min(availableWidth / preview.naturalWidth, availableHeight / preview.naturalHeight));
    }
    zoomOut.addEventListener("click", function () {
      preview.mode = "zoom";
      applyScale(preview.scale / 1.25);
    });
    zoomIn.addEventListener("click", function () {
      preview.mode = "zoom";
      applyScale(preview.scale * 1.25);
    });
    fit.addEventListener("click", fitPreview);
    actual.addEventListener("click", function () {
      preview.mode = "actual";
      applyScale(1);
    });
    state.detailPreviewFit = function () {
      if (preview.mode === "fit") {
        fitPreview();
      }
    };
    [zoomOut, fit, actual, zoomIn].forEach(function (control) {
      control.disabled = true;
    });
    function showPreviewArtifact(artifact, description) {
      var image = placeImage(holder, artifact.url, description, function (loaded) {
        preview.image = loaded;
        preview.naturalWidth = loaded.naturalWidth;
        preview.naturalHeight = loaded.naturalHeight;
        [zoomOut, fit, actual, zoomIn].forEach(function (control) {
          control.disabled = false;
        });
        fitPreview();
      });
      image.className = "detail-preview-image";
    }

    fetchArtifact("/assets/" + assetId + "/preview").then(function (preview) {
      if (preview.ok) {
        showPreviewArtifact(preview, "预览：" + label);
        text(caption, preview.originalAvailable === "false" ? "预览可用；原文件当前不可用。" : "预览");
        if (preview.originalAvailable === "false") {
          addBadge(holder, "badge badge-original-missing", "原文件不可用");
        }
        return;
      }
      caption.textContent = "预览不可用（" + preview.code + "），正在回退缩略图…";
      return fetchArtifact("/assets/" + assetId + "/thumbnail").then(function (thumb) {
        if (thumb.ok) {
          showPreviewArtifact(thumb, "缩略图：" + label);
          text(
            caption,
            "预览不可用（" + preview.code + "），已回退显示缩略图" +
              (thumb.originalAvailable === "false" ? "；原文件当前不可用。" : "。")
          );
          if (thumb.originalAvailable === "false") {
            addBadge(holder, "badge badge-original-missing", "原文件不可用");
          }
          return;
        }
        clearFrame(holder);
        zoomLabel.textContent = "预览不可用";
        noteInFrame(holder, "error", "没有任何可用产物：" + thumb.code);
        text(
          caption,
          "原文件不可用（" +
            thumb.code +
            "，请求编号 " +
            (thumb.request_id || "无") +
            "），也没有已生成的缩略图。"
        );
      });
    });

    var facts = element("section", "detail-section");
    facts.appendChild(element("h3", null, "身份与时间"));
    var factsList = element("dl", "media-list");
    [
      ["资产编号", "#" + body.asset_id],
      ["内容指纹", body.sha256],
      ["字节数", formatBytes(body.byte_size)],
      ["加入时间", formatMoment(body.created_at) + "（入库时间，不是拍摄时间）"],
      [
        "图库记录（最近一次扫描）",
        body.availability === "missing" ? "原文件不可用" : "在库",
      ],
      ["位置数量", String(body.location_count)],
    ].forEach(function (pair) {
      factsList.appendChild(element("dt", null, pair[0]));
      factsList.appendChild(element("dd", null, pair[1]));
    });
    facts.appendChild(factsList);
    advanced.appendChild(facts);

    var capture = element("section", "detail-section");
    capture.appendChild(element("h3", null, "拍摄时间（EXIF）"));
    var captureList = element("dl", "media-list");
    var time = body.taken_time || null;
    [
      [
        "结论",
        time === null
          ? "未抽取（或本程序不信任当前规则版本的既有结果）"
          : time.label === "capture"
            ? "拍摄时间"
            : "文件时间（不是拍摄时间）",
      ],
      ["规范化墙上时间", time === null || !time.taken_at ? "（无）" : String(time.taken_at)],
      [
        "记录时区",
        time === null || time.timezone_offset_minutes === null
          ? "文件未记录（不会显示或推断为 UTC）"
          : offsetText(time.timezone_offset_minutes),
      ],
      [
        "来源",
        time === null
          ? "（无）"
          : TIME_SOURCE_TEXT[time.time_source] || String(time.time_source || "未知"),
      ],
      ["置信度", time === null ? "（无）" : String(time.time_confidence || "未知")],
      ["相机", time === null || !time.camera ? "（无记录）" : String(time.camera)],
      [
        "排序键（civil key）",
        time === null || time.taken_at_sort === null || time.taken_at_sort === undefined
          ? "（无，排在拍摄时间列表末尾）"
          : String(time.taken_at_sort),
      ],
    ].forEach(function (pair) {
      captureList.appendChild(element("dt", null, pair[0]));
      captureList.appendChild(element("dd", null, pair[1]));
    });
    capture.appendChild(captureList);
    capture.appendChild(
      element(
        "p",
        "hint",
        "原始 EXIF 时间文本不会通过接口返回；这里显示的是规范化后的墙上时间，排序键也不是 UTC 时刻。"
      )
    );
    advanced.appendChild(capture);

    var locations = element("section", "detail-section");
    locations.appendChild(element("h3", null, "全部文件位置"));
    locations.appendChild(buildLocationTable(body.locations || []));
    var openOriginal = element("p");
    var link = element("a", null, "打开原图");
    link.href = API + "/assets/" + encodeURIComponent(assetId) + "/original";
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    openOriginal.appendChild(link);
    openOriginal.appendChild(
      element(
        "span",
        "hint",
        "（同源导航，浏览器会带上会话 cookie；原文件不可用时显示结构化错误）"
      )
    );
    locations.appendChild(openOriginal);
    advanced.appendChild(locations);

    var media = element("section", "detail-section");
    media.appendChild(element("h3", null, "产物状态"));
    var mediaList = element("dl", "media-list");
    var kinds = Object.keys(body.media || {});
    if (!kinds.length) {
      mediaList.appendChild(element("dt", null, "（无）"));
      mediaList.appendChild(element("dd", null, "没有登记任何产物类型。"));
    }
    kinds.forEach(function (kind) {
      var item = body.media[kind] || {};
      var label = kind === "thumbnail" ? "缩略图" : kind === "preview" ? "预览" : kind;
      mediaList.appendChild(element("dt", null, label + "（" + item.processor_id + "）"));
      var detail =
        (MEDIA_STATE_TEXT[item.state] || item.state) +
        (item.code ? "：代码 " + item.code : "") +
        (item.message ? "（" + safeText(item.message) + "）" : "");
      mediaList.appendChild(element("dd", null, detail));
    });
    media.appendChild(mediaList);
    advanced.appendChild(media);
  }

  function placeImage(holder, url, altText, onLoaded) {
    clearFrame(holder);
    var image = document.createElement("img");
    image.alt = altText;
    image.addEventListener("error", function () {
      clearFrame(holder);
      noteInFrame(holder, "error", "图片无法显示（IMAGE_UNREADABLE）");
    });
    if (typeof onLoaded === "function") {
      image.addEventListener("load", function () {
        onLoaded(image);
      });
    }
    image.src = url;
    holder.appendChild(image);
    return image;
  }

  //: The detail payload carries ``locations`` (not ``current_location``), so the first
  //: registered position names the image instead of the bare asset id.
  function detailLabel(body, assetId) {
    var locations = body.locations || [];
    for (var index = 0; index < locations.length; index += 1) {
      var location = locations[index];
      if (location && location.relative_path) {
        return location.relative_path;
      }
    }
    return "资产 #" + assetId;
  }

  function buildLocationTable(locations) {
    var table = element("table");
    var head = element("thead");
    var headRow = element("tr");
    ["来源", "相对路径", "可用性（最近扫描）", "大小", "修改时间"].forEach(function (label) {
      var cell = element("th", null, label);
      cell.scope = "col";
      headRow.appendChild(cell);
    });
    head.appendChild(headRow);
    table.appendChild(head);
    var body = element("tbody");
    if (!locations.length) {
      var empty = element("tr");
      var cell = element("td", null, "没有登记任何位置。");
      cell.colSpan = 5;
      empty.appendChild(cell);
      body.appendChild(empty);
    }
    locations.forEach(function (location) {
      var row = element("tr");
      row.appendChild(
        element("td", null, location.source_display_name || "#" + location.source_id)
      );
      row.appendChild(element("td", null, location.relative_path));
      row.appendChild(
        element(
          "td",
          null,
          location.availability === "PRESENT"
            ? "在库"
            : location.availability === "MISSING"
              ? "缺失"
              : location.availability
        )
      );
      row.appendChild(element("td", null, formatBytes(location.size)));
      row.appendChild(element("td", null, formatMoment(Number(location.mtime_ns) / 1000000)));
      body.appendChild(row);
    });
    table.appendChild(body);
    return table;
  }

  // ------------------------------------------------------------------ navigation
  function clearHash() {
    if (location.hash) {
      // ``replaceState`` does not fire ``hashchange``, so the view switch stays in charge.
      var path = location.pathname + location.search;
      if (path === "/" && state.browse.scope === "favorites") path = "/?view=favorites";
      history.replaceState(null, "", path);
    }
  }

  function showView(name) {
    var previousView = state.currentView;
    if (previousView === "photoSmartCollection" && name !== "photoSmartCollection") {
      state.photoSmart.viewEpoch += 1;
      state.photoSmart.requestVersion += 1;
      state.photoSmart.entryEpoch += 1;
      stopPhotoSmartRefreshObserver();
      if (state.photoSmart.pager) state.photoSmart.pager.setEnabled(false);
    }
    if (name === "sources") enterEmbeddingManagementView(previousView);
    state.currentView = name;
    if (name !== "sources") stopEmbeddingTaskPoll();
    if (name !== "detail" && state.detailInfoMount) {
      state.detailInfoMount.dispose();
      state.detailInfoMount = null;
      state.detailSeq = (state.detailSeq || 0) + 1;
    }
    ["sources", "browse", "detail", "photoCollections", "photoCollection", "photoSmartCollection"].forEach(function (view) {
      var section = els.views[view];
      if (!section) return;
      var active = view === name;
      section.hidden = !active;
    });
    Array.prototype.forEach.call(els.navButtons, function (button) {
      button.setAttribute("aria-current", button.dataset.view === name ? "true" : "false");
    });
    var shellLinks = document.querySelectorAll ? document.querySelectorAll("[data-shell-page]") : [];
    Array.prototype.forEach.call(shellLinks, function (link) {
      var activePage = name === "sources" ? "management" :
        (name === "photoCollections" || name === "photoCollection" || name === "photoSmartCollection" ? "photo-collections" :
          (name === "browse" && state.browse.scope === "favorites" ? "favorites" : "photos"));
      if (link.dataset.shellPage === activePage) {
        link.setAttribute("aria-current", "page");
      } else {
        link.removeAttribute("aria-current");
      }
    });
    els.main.focus({ preventScroll: true });
    if (name === "browse") showBrowseMode(state.browse.mode);
    if (name === "sources") {
      loadSourcePaths(false);
      loadEmbeddingManagementStatus();
    }
    if (name === "photoCollections") {
      if (state.photoCollectionKind === "smart") loadPhotoSmartCollections(false);
      else loadPhotoCollections(false);
    }
    if (name === "photoCollection") loadPhotoCollectionAssets(false);
    if (name === "photoSmartCollection") loadPhotoSmartAssets(false);
    updateSearchScopeAction();
  }

  var embeddingStatusRequest = null;
  var embeddingTaskPollTimer = null;
  var embeddingTaskPollRequest = null;
  var embeddingChecks = Object.create(null);
  var embeddingCheckSequence = 0;
  var embeddingTaskSignature = null;
  function embeddingProviderSnapshot(provider) {
    var generation = provider.generation || {};
    return {
      provider_id: String(provider.provider_id || ""),
      processor_id: String(provider.processor_id || ""),
      space_id: String(provider.space_id || ""),
      generation_id: String((generation.generation_id || provider.generation_id) || ""),
      profile_id: String((generation.profile_id || provider.profile_id) || ""),
      is_active: Boolean(provider.is_active),
    };
  }
  function sameEmbeddingSnapshot(left, right) {
    return Boolean(left && right && left.provider_id === right.provider_id &&
      left.processor_id === right.processor_id && left.space_id === right.space_id &&
      left.generation_id === right.generation_id && left.profile_id === right.profile_id &&
      left.is_active === right.is_active);
  }
  function clearEmbeddingChecks(providerId) {
    var keys = providerId ? [providerId] : Object.keys(embeddingChecks);
    keys.forEach(function (key) {
      var check = embeddingChecks[key];
      if (check && check.state === "checking") check.obsolete = true;
      else delete embeddingChecks[key];
    });
  }
  function enterEmbeddingManagementView(previousView) {
    if (previousView !== "sources") clearEmbeddingChecks();
  }
  function observeEmbeddingTask(task) {
    var taskSignature = task
      ? [task.task_id || "", task.provider_id || "", task.status || ""].join("|")
      : "none";
    if (embeddingTaskSignature !== null && embeddingTaskSignature !== taskSignature) clearEmbeddingChecks();
    embeddingTaskSignature = taskSignature;
  }
  function safeEmbeddingCode(value) {
    var code = String(value || "");
    return /^[A-Za-z0-9_.:-]{1,80}$/.test(code) ? code : "检查详情";
  }
  function embeddingCoverageText(coverage) {
    if (!coverage || typeof coverage !== "object") return "";
    if (coverage.covered === null || coverage.covered === undefined || coverage.required === null || coverage.required === undefined) return "";
    var covered = Number(coverage.covered);
    var required = Number(coverage.required);
    if (!isFinite(covered) || !isFinite(required)) return "";
    var summary = "；在线可索引图片覆盖：" + covered + "/" + required;
    if (required > 0) {
      var percent = coverage.percent === null || coverage.percent === undefined || coverage.percent === ""
        ? NaN : Number(coverage.percent);
      if (!isFinite(percent)) percent = covered * 100 / required;
      summary += "（" + percent.toFixed(1) + "%）";
    }
    var labels = {missing: "缺失", pending_outbox: "待提交", pending_repair: "待修复",
      stale_entries: "过期条目", unreadable_artifacts: "不可读产物"};
    Object.keys(labels).forEach(function (key) {
      var count = Number(coverage[key]);
      if (isFinite(count) && count > 0) summary += "；" + labels[key] + " " + count;
    });
    return summary;
  }
  function embeddingOfflineWarning(coverage) {
    var count = Number(coverage && coverage.offline_assets);
    if (!isFinite(count) || count <= 0) return "";
    return "；另有 " + count + " 条资产当前没有可用位置，不计入上述在线覆盖率；切换后它们可能缺少新模型语义结果，恢复原图后需增量建库";
  }
  function embeddingCheckMessage(check) {
    if (!check || check.state === "idle") return "点击检查以核验索引覆盖和切换资格。";
    if (check.state === "checking" && check.obsolete) return "模型或建库状态已变化；本次结果将作废，正在等待检查请求结束…";
    if (check.state === "checking") return "正在检查索引…（可能需要一些时间）";
    if (check.state === "error") return "检查失败：" + String(check.message || "请求失败") + "（可重新检查）";
    var body = check.body || {};
    if (body.status === "not_ready" || body.reason === "NO_GENERATION") return "尚无可检查的索引，请先建库。";
    var coverage = embeddingCoverageText(body.coverage);
    if (check.state === "passed") return "最近一次检查通过，可以安排切换" + coverage +
      embeddingOfflineWarning(body.coverage) + "。";
    var selection = body.selection_error;
    var code = selection && selection.code;
    var reason = selection && selection.details && selection.details.reason;
    code = code || reason || body.reason;
    var message = "检查完成，暂不能切换" + coverage + embeddingOfflineWarning(body.coverage);
    if (code) message += "；原因：" + safeEmbeddingCode(code);
    if (body.coverage && Number(body.coverage.required) === 0) message += "；当前没有可核验资产，不能据此确认切换资格";
    return message + "。可重新检查。";
  }
  function embeddingCheckIsCurrent(check, provider) {
    if (!check || !sameEmbeddingSnapshot(check.snapshot, embeddingProviderSnapshot(provider))) return false;
    if (check.state !== "passed") return true;
    var body = check.body || {};
    var snapshot = embeddingProviderSnapshot(provider);
    return body.status === "ok" && body.switch_eligible === true && Boolean(body.profile_id) &&
      body.provider_id === provider.provider_id && body.generation_id === snapshot.generation_id &&
      body.profile_id === snapshot.profile_id;
  }
  function stopEmbeddingTaskPoll() {
    if (embeddingTaskPollTimer !== null) window.clearTimeout(embeddingTaskPollTimer);
    embeddingTaskPollTimer = null;
  }
  function scheduleEmbeddingTaskPoll() {
    stopEmbeddingTaskPoll();
    if (state.currentView !== "sources" || document.hidden) return;
    embeddingTaskPollTimer = window.setTimeout(pollEmbeddingTaskProgress, 1500);
  }
  function pollEmbeddingTaskProgress() {
    embeddingTaskPollTimer = null;
    if (state.currentView !== "sources" || document.hidden || embeddingTaskPollRequest) return;
    embeddingTaskPollRequest = request("/embedding-management/task-progress")
      .then(function (result) {
        var task = (result.body || {}).candidate_task;
        observeEmbeddingTask(task);
        if (task && ["RUNNING", "PAUSE_REQUESTED"].indexOf(task.status) >= 0) {
          var activeEl = document.getElementById("active-search-model");
          if (activeEl) activeEl.textContent = activeEl.textContent.replace(/；候选建库：[^；]*/, "") +
            "；候选建库：" + task.status + "，已确认向量 " + Number(task.confirmed_entries || 0);
          scheduleEmbeddingTaskPoll();
        } else {
          loadEmbeddingManagementStatus();
        }
      })
      .catch(function () { scheduleEmbeddingTaskPoll(); })
      .finally(function () { embeddingTaskPollRequest = null; });
  }
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) stopEmbeddingTaskPoll();
    else if (state.currentView === "sources") loadEmbeddingManagementStatus();
  });
  function loadEmbeddingManagementStatus() {
    if (embeddingStatusRequest) return embeddingStatusRequest;
    var activeEl = document.getElementById("active-search-model");
    var listEl = document.getElementById("embedding-provider-list");
    var otherListEl = document.getElementById("embedding-provider-other-list");
    if (!activeEl || !listEl) return Promise.resolve();
    embeddingStatusRequest = request("/embedding-management/status")
      .then(function (result) {
        var body = result.body || {};
        state.embeddingManagementStatus = body;
        updateFirstUseGuide();
        observeEmbeddingTask(body.candidate_task || null);
        var active = body.active_profile;
        var activeProvider = (body.providers || []).find(function (provider) {
          return active && provider.processor_id === active.processor_id &&
            provider.space_id === active.space_id && provider.generation &&
            provider.generation.generation_id === active.generation_id;
        });
        activeEl.textContent = active
          ? "当前搜索模型：" + String(activeProvider ? activeProvider.display_name : active.provider_id)
          : "当前没有活动搜索模型";
        function renderProviders(target, providers) {
          target.replaceChildren();
          providers.forEach(function (provider) {
          var card = document.createElement("article");
          card.className = "embedding-provider-card";
          var title = document.createElement("p");
          var strong = document.createElement("strong");
          strong.textContent = String(provider.display_name || provider.provider_id);
          title.appendChild(strong);
          if (provider.provider_id === "qwen3-vl-embedding-2b-1024d-exif-v2") {
            var trial = document.createElement("span");
            trial.className = "hint embedding-trial-label";
            trial.textContent = "（试用配置）";
            title.appendChild(trial);
          }
          var state = document.createElement("p");
          state.textContent = provider.is_active ? "正在使用" :
            (!provider.installed ? "未安装" :
              (provider.index_state === "ORPHAN_TABLE" ? "发现旧索引残留，需要处理" :
                (provider.index_state === "NO_GENERATION" ? "已安装，未建索引" : "索引待检查")));
          var detail = document.createElement("p");
          detail.className = "hint";
          var generation = provider.generation;
          detail.textContent = "有效向量：" + (generation ? generation.confirmed : 0) +
            "；图库覆盖：待检查；切换资格：未核验";
          var checked = embeddingChecks[provider.provider_id];
          if (checked && !embeddingCheckIsCurrent(checked, provider)) {
            clearEmbeddingChecks(provider.provider_id);
            checked = checked.state === "checking" ? embeddingChecks[provider.provider_id] : null;
          }
          if (checked && checked.state !== "idle" && checked.state !== "checking") {
            detail.textContent = "有效向量：" + (generation ? generation.confirmed : 0) +
              (checked.body ? embeddingCoverageText(checked.body.coverage) : "；图库覆盖：待检查");
          }
          var checkStatus = document.createElement("p");
          checkStatus.className = "hint embedding-check-status";
          checkStatus.setAttribute("role", "status");
          checkStatus.setAttribute("aria-live", "polite");
          checkStatus.textContent = checked && checked.state === "passed" && provider.is_active
            ? "最近一次检查通过；当前模型正在使用" + embeddingCoverageText(checked.body && checked.body.coverage) + "。"
            : embeddingCheckMessage(checked);
          card.append(title, state, detail, checkStatus);
          var task = body.candidate_task;
          var activeTask = task && ["RUNNING", "PAUSE_REQUESTED", "PAUSED", "INTERRUPTED", "FAILED"].indexOf(task.status) >= 0;
          if (provider.installed && provider.index_state !== "ORPHAN_TABLE") {
            var check = document.createElement("button");
            check.type = "button";
            check.textContent = checked && checked.state === "checking" ? "检查中…" :
              (checked && checked.state !== "idle" ? "重新检查" : "检查");
            check.disabled = Boolean(checked && checked.state === "checking");
            check.addEventListener("click", function () {
              var current = embeddingChecks[provider.provider_id];
              if (current && current.state === "checking") return;
              var requestId = ++embeddingCheckSequence;
              var snapshot = embeddingProviderSnapshot(provider);
              embeddingChecks[provider.provider_id] = {id: requestId, state: "checking", snapshot: snapshot};
              check.disabled = true;
              check.textContent = "检查中…";
              checkStatus.textContent = embeddingCheckMessage(embeddingChecks[provider.provider_id]);
              request("/embedding-management/providers/" + encodeURIComponent(provider.provider_id) + "/check", {method:"POST"})
                .then(function (result) {
                  var currentCheck = embeddingChecks[provider.provider_id];
                  if (!currentCheck || currentCheck.id !== requestId ||
                      !sameEmbeddingSnapshot(currentCheck.snapshot, snapshot)) return;
                  if (currentCheck.obsolete) {
                    delete embeddingChecks[provider.provider_id];
                    return loadEmbeddingManagementStatus();
                  }
                  var response = result.body || {};
                  var required = response.coverage && Number(response.coverage.required);
                  var eligible = response.status === "ok" && response.switch_eligible === true &&
                    response.provider_id === snapshot.provider_id &&
                    response.generation_id === snapshot.generation_id &&
                    Boolean(snapshot.profile_id) && response.profile_id === snapshot.profile_id &&
                    isFinite(required) && required > 0;
                  embeddingChecks[provider.provider_id] = {
                    id: requestId, state: eligible ? "passed" : "blocked", snapshot: snapshot, body: response,
                  };
                  return loadEmbeddingManagementStatus();
                })
                .catch(function (failure) {
                  var currentCheck = embeddingChecks[provider.provider_id];
                  if (!currentCheck || currentCheck.id !== requestId) return;
                  if (currentCheck.obsolete) {
                    delete embeddingChecks[provider.provider_id];
                    return loadEmbeddingManagementStatus();
                  }
                  embeddingChecks[provider.provider_id] = {
                    id: requestId, state: "error", snapshot: snapshot,
                    message: "[" + safeEmbeddingCode(failure && failure.code || "NETWORK_ERROR") + "] 请求失败，请重试",
                  };
                  checkStatus.textContent = embeddingCheckMessage(embeddingChecks[provider.provider_id]);
                  check.disabled = false;
                  check.textContent = "重新检查";
                  return loadEmbeddingManagementStatus();
                });
            });
            card.appendChild(check);
            if (checked && checked.state === "passed" && !provider.is_active) {
              var arrange = document.createElement("button");
              arrange.type = "button";
              arrange.textContent = "安排切换";
              arrange.addEventListener("click", function () {
                var offlineWarning = embeddingOfflineWarning(checked.body && checked.body.coverage);
                var message = "切换将在正常退出并重新打开应用时执行。" +
                  (offlineWarning ? "\n\n注意：" + offlineWarning.slice(2) + "。" : "") + "\n\n确认安排？";
                if (!window.confirm(message)) return;
                var requestId = window.crypto && window.crypto.randomUUID ? window.crypto.randomUUID() : String(Date.now()) + "-" + Math.random().toString(16).slice(2);
                request("/embedding-management/providers/" + encodeURIComponent(provider.provider_id) + "/switch", {
                  method:"POST", body:{profile_id:provider.profile_id,request_id:requestId,confirmed:true},
                }).then(loadEmbeddingManagementStatus).catch(function (failure) { activeEl.textContent = describe(failure); });
              });
              card.appendChild(arrange);
            }
          }
          if (!provider.installed || provider.index_state === "ORPHAN_TABLE") {
            var disabled = document.createElement("button");
            disabled.type = "button";
            disabled.textContent = provider.index_state === "ORPHAN_TABLE" ? "安排隔离旧索引" : "模型未安装";
            if (provider.index_state === "ORPHAN_TABLE") {
              disabled.addEventListener("click", function () {
                if (!window.confirm("将先在 vectors 同一文件系统内复制并校验旧索引，再隔离原表。空间不足或校验失败时原表会保留。确认安排？")) return;
                var requestId = window.crypto && window.crypto.randomUUID ? window.crypto.randomUUID() : String(Date.now()) + "-" + Math.random().toString(16).slice(2);
                request("/embedding-management/providers/" + encodeURIComponent(provider.provider_id) + "/isolate-orphan", {
                  method:"POST", body:{request_id:requestId,confirmed:true},
                }).then(loadEmbeddingManagementStatus).catch(function (failure) { activeEl.textContent = describe(failure); });
              });
            } else {
              disabled.disabled = true;
            }
            card.appendChild(disabled);
          } else if (activeTask && task.provider_id === provider.provider_id) {
            if (task.status === "RUNNING") {
              var pause = document.createElement("button");
              pause.type = "button";
              pause.textContent = task.status === "PAUSE_REQUESTED" ? "正在暂停…" : "暂停";
              pause.disabled = task.status === "PAUSE_REQUESTED";
              pause.addEventListener("click", function () {
                request("/embedding-management/tasks/" + encodeURIComponent(task.task_id) + "/pause", {method:"POST"})
                  .then(loadEmbeddingManagementStatus).catch(function (failure) { activeEl.textContent = describe(failure); });
              });
              card.appendChild(pause);
            } else if (["PAUSED", "INTERRUPTED", "FAILED"].indexOf(task.status) >= 0) {
              var resume = document.createElement("button");
              resume.type = "button";
              resume.textContent = "继续建库";
              resume.addEventListener("click", function () {
                request("/embedding-management/tasks/" + encodeURIComponent(task.task_id) + "/resume", {method:"POST"})
                  .then(loadEmbeddingManagementStatus).catch(function (failure) { activeEl.textContent = describe(failure); });
              });
              card.appendChild(resume);
            }
          } else if (!activeTask) {
            var start = document.createElement("button");
            start.type = "button";
            start.textContent = "开始建库";
            start.addEventListener("click", function () {
              if (!window.confirm("候选建库将临时独占模型资源；语义搜索可能等待或返回繁忙。确认开始？")) return;
              var requestId = window.crypto && window.crypto.randomUUID ? window.crypto.randomUUID() : String(Date.now()) + "-" + Math.random().toString(16).slice(2);
              request("/embedding-management/providers/" + encodeURIComponent(provider.provider_id) + "/candidate-build", {
                method:"POST", body:{request_id:requestId,exclusive_model_use_confirmed:true},
              }).then(loadEmbeddingManagementStatus).catch(function (failure) { activeEl.textContent = describe(failure); });
            });
            card.appendChild(start);
          }
          target.appendChild(card);
          });
        }
        var providers = body.providers || [];
        var easyToFindQwen = ["qwen3-vl-embedding-2b-exif-v2", "qwen3-vl-embedding-2b-1024d-exif-v2"];
        renderProviders(listEl, providers.filter(function (item) {
          return !/EXIF v2/i.test(String(item.display_name || "")) || easyToFindQwen.indexOf(item.provider_id) >= 0;
        }));
        if (otherListEl) renderProviders(otherListEl, providers.filter(function (item) {
          return /EXIF v2/i.test(String(item.display_name || "")) && easyToFindQwen.indexOf(item.provider_id) < 0;
        }));
        var transition = body.offline_transition || {};
        var pendingTransition = transition.pending;
        if (pendingTransition) {
          activeEl.textContent += "；已安排停机操作：" + String(pendingTransition.kind || "未知") + "（退出并重新打开应用后执行）";
          var cancel = document.createElement("button");
          cancel.type = "button";
          cancel.textContent = "取消待执行操作";
          cancel.addEventListener("click", function () {
            request("/embedding-management/offline-transition", {method:"DELETE"})
              .then(loadEmbeddingManagementStatus).catch(function (failure) { activeEl.textContent = describe(failure); });
          });
          listEl.appendChild(cancel);
        } else if (transition.last_result && transition.last_result.status) {
          activeEl.textContent += "；最近停机操作：" + String(transition.last_result.status);
        }
        var task = body.candidate_task;
        if (task && ["RUNNING", "PAUSE_REQUESTED"].indexOf(task.status) >= 0) {
          activeEl.textContent += "；候选建库：" + task.status + "，已确认向量 " + Number(task.confirmed_entries || 0);
          scheduleEmbeddingTaskPoll();
        } else if (task && task.last_error) {
          stopEmbeddingTaskPoll();
          activeEl.textContent += "；候选建库结果：" + task.status + "（" + String(task.last_error.code || "未知错误") + "）";
        } else stopEmbeddingTaskPoll();
      })
      .catch(function (failure) {
        activeEl.textContent = "读取模型状态失败：" + describe(failure);
      })
      .finally(function () { embeddingStatusRequest = null; });
    return embeddingStatusRequest;
  }

  function loadPhotoCollections(append) {
    state.photoCollectionKind = "ordinary";
    if (els.views && els.views.photoCollections) els.views.photoCollections.classList.remove("smart-collections-active");
    if (els.photoSmartCollectionsPanel) els.photoSmartCollectionsPanel.hidden = true;
    if (els.photoCollectionCreate) els.photoCollectionCreate.hidden = false;
    if (els.photoCollectionsList) els.photoCollectionsList.hidden = false;
    if (els.photoCollectionsMore) els.photoCollectionsMore.hidden = false;
    if (els.photoCollectionsIntro) els.photoCollectionsIntro.textContent = "普通照片合集保存你确认的照片；新照片不会自动加入。";
    if (els.photoCollectionsTabOrdinary) els.photoCollectionsTabOrdinary.setAttribute("aria-pressed", "true");
    if (els.photoCollectionsTabSmart) els.photoCollectionsTabSmart.setAttribute("aria-pressed", "false");
    var version = ++state.photoCollections.requestVersion;
    var path = "/photo-collections?limit=20" + (append && state.photoCollections.cursor
      ? "&cursor=" + encodeURIComponent(state.photoCollections.cursor) : "");
    request(path).then(function (result) {
      if (version !== state.photoCollections.requestVersion) return;
      var body = result.body || {};
      state.photoCollections.cursor = body.next_cursor || null;
      state.photoCollections.hasMore = Boolean(body.has_more);
      state.photoCollections.revision = Number(body.photo_collections_revision);
      if (!append) els.photoCollectionsList.replaceChildren();
      (body.collections || []).forEach(function (item) {
        var row = element("li", "source-card");
        if (item.cover_asset_id) {
          var cover = element("img", "collection-cover");
          cover.src = API + "/assets/" + encodeURIComponent(item.cover_asset_id) + "/thumbnail";
          cover.alt = "";
          row.appendChild(cover);
        }
        var open = element("a", null, item.name + " · " + item.member_count + " 张");
        open.href = "/?view=photo-collection&id=" + encodeURIComponent(item.collection_id);
        row.appendChild(open);
        var rename = element("button", null, "改名");
        rename.type = "button";
        rename.addEventListener("click", function () {
          window.AIPhotoInfo.actionDialog({kind:"text", title:"重命名照片合集", message:"请输入新名称。", label:"合集名称", value:item.name})
            .then(function (next) {
              if (next === null) return;
              return request("/photo-collections/" + encodeURIComponent(item.collection_id), {method:"PATCH", body:{
                instance_token:item.instance_token, expected_revision:item.revision, name:next
              }}).then(function () { loadPhotoCollections(false); });
            }).catch(function (failure) { showCollectionFailure(failure, els.photoCollectionsStatus); });
        });
        row.appendChild(rename);
        var remove = element("button", null, "删除");
        remove.type = "button";
        remove.addEventListener("click", function () {
          window.AIPhotoInfo.actionDialog({kind:"confirm", title:"删除照片合集", message:"删除“" + item.name + "”及其中的收录关系？照片文件不会删除。", confirmLabel:"删除"})
            .then(function (confirmed) {
              if (!confirmed) return;
              return request("/photo-collections/" + encodeURIComponent(item.collection_id), {method:"DELETE", body:{
                instance_token:item.instance_token, expected_revision:item.revision
              }}).then(function () { loadPhotoCollections(false); });
            }).catch(function (failure) { showCollectionFailure(failure, els.photoCollectionsStatus); });
        });
        row.appendChild(remove);
        els.photoCollectionsList.appendChild(row);
      });
      els.photoCollectionsMore.hidden = !state.photoCollections.hasMore;
      els.photoCollectionsStatus.textContent = body.collections && body.collections.length
        ? "共显示 " + els.photoCollectionsList.children.length + " 个照片合集。" : "还没有照片合集；可以创建空合集，或从搜索结果保存照片。";
    }).catch(function (failure) { showCollectionFailure(failure, els.photoCollectionsStatus); });
  }

  function loadPhotoSmartCollections(append) {
    state.photoCollectionKind = "smart";
    if (els.views && els.views.photoCollections) els.views.photoCollections.classList.add("smart-collections-active");
    if (els.photoSmartCollectionsPanel) els.photoSmartCollectionsPanel.hidden = false;
    if (els.photoCollectionCreate) els.photoCollectionCreate.hidden = true;
    if (els.photoCollectionsList) els.photoCollectionsList.hidden = true;
    if (els.photoCollectionsMore) els.photoCollectionsMore.hidden = true;
    if (els.photoCollectionsIntro) els.photoCollectionsIntro.textContent = "按已确认的规则动态筛选或更新排名。";
    if (els.photoCollectionsTabOrdinary) els.photoCollectionsTabOrdinary.setAttribute("aria-pressed", "false");
    if (els.photoCollectionsTabSmart) els.photoCollectionsTabSmart.setAttribute("aria-pressed", "true");
    var version = ++state.photoSmart.requestVersion;
    var path = "/photo-smart-collections?limit=20" + (append && state.photoSmart.cursor
      ? "&cursor=" + encodeURIComponent(state.photoSmart.cursor) : "");
    request(path).then(function (result) {
      if (version !== state.photoSmart.requestVersion || state.currentView !== "photoCollections" ||
          state.photoCollectionKind !== "smart") return;
      var body = result.body || {};
      state.photoSmart.cursor = body.next_cursor || null;
      state.photoSmart.hasMore = Boolean(body.has_more);
      if (!append) els.photoSmartCollectionsList.replaceChildren();
      (body.collections || []).forEach(function (item) {
        var row = element("li", "smart-collection-card");
        var open = element("a", null, item.name);
        open.href = "/?view=photo-smart-collection&id=" + encodeURIComponent(item.smart_id);
        var heading = element("h3"); heading.appendChild(open); row.appendChild(heading);
        var kind = ({FILTER:"条件筛选", TEXT:"文字检索", COMBINED:"综合搜索"})[item.execution_kind] || "智能合集";
        var freshness = ({CHECK_ON_OPEN:"打开后检查", CURRENT:"已缓存", STALE:"待更新",
          REFRESHING:"更新中", NO_CACHE:"尚无缓存", MODEL_REBIND_REQUIRED:"模型需重新绑定",
          STRONG_MATCH_REBUILD_REQUIRED:"需要重新生成高度匹配结果"})[item.freshness] || "状态待检查";
        if (item.execution_kind === "COMBINED") kind += " · 仅高度匹配";
        if (item.freshness === "STRONG_MATCH_REBUILD_REQUIRED") item.last_error_code = null;
        if (item.last_error_code) freshness = "更新失败 · " + item.last_error_code;
        row.appendChild(element("p", "smart-collection-meta", kind + " · " + freshness));
        var candidateCount = Number(item.candidate_count);
        if (item.candidate_count !== null && item.candidate_count !== undefined && Number.isFinite(candidateCount) &&
            (candidateCount > 0 || ["CURRENT", "STALE", "REFRESHING", "CHECK_ON_OPEN"].indexOf(item.freshness) >= 0)) {
          row.appendChild(element("p", "smart-collection-meta", "规则候选照片：" + candidateCount + " 张"));
        }
        var actions = element("div", "smart-collection-actions");
        var listIsCurrent = function () { return version === state.photoSmart.requestVersion &&
          state.currentView === "photoCollections" && state.photoCollectionKind === "smart"; };
        var rename = element("button", null, "改名");
        rename.type = "button";
        rename.addEventListener("click", function () {
          window.AIPhotoInfo.actionDialog({kind:"text", title:"重命名智能合集", message:"请输入新名称。", label:"合集名称", value:item.name})
            .then(function (next) {
              if (next === null || !listIsCurrent()) return;
              return request("/photo-smart-collections/" + encodeURIComponent(item.smart_id), {method:"PATCH", body:{
                instance_token:item.instance_token, expected_revision:item.revision, name:next
              }}).then(function () { if (listIsCurrent()) loadPhotoSmartCollections(false); });
            }).catch(function (failure) { if (listIsCurrent()) showCollectionFailure(failure, els.photoSmartCollectionsStatus); });
        });
        actions.appendChild(rename);
        var remove = element("button", null, "删除");
        remove.type = "button";
        remove.addEventListener("click", function () {
          window.AIPhotoInfo.actionDialog({kind:"confirm", title:"删除智能合集", message:"删除“" + item.name + "”的规则、排除与候选缓存？照片文件不会删除。", confirmLabel:"删除"})
            .then(function (confirmed) {
              if (!confirmed || !listIsCurrent()) return;
              return request("/photo-smart-collections/" + encodeURIComponent(item.smart_id), {method:"DELETE", body:{
                instance_token:item.instance_token, expected_revision:item.revision
              }}).then(function () { if (listIsCurrent()) loadPhotoSmartCollections(false); });
            }).catch(function (failure) { if (listIsCurrent()) showCollectionFailure(failure, els.photoSmartCollectionsStatus); });
        });
        actions.appendChild(remove);
        row.appendChild(actions);
        els.photoSmartCollectionsList.appendChild(row);
      });
      els.photoSmartCollectionsMore.hidden = !state.photoSmart.hasMore;
      els.photoSmartCollectionsStatus.textContent = body.collections && body.collections.length
        ? "共显示 " + els.photoSmartCollectionsList.children.length + " 个智能合集。规则候选照片经排除项复核后，实际可浏览数量可能不同。"
        : "还没有智能合集；请从已完成的搜索结果保存规则。";
    }).catch(function (failure) {
      if (version === state.photoSmart.requestVersion && state.currentView === "photoCollections" && state.photoCollectionKind === "smart")
        showCollectionFailure(failure, els.photoSmartCollectionsStatus);
    });
  }

  function photoSmartKindLabel(kind) {
    return ({FILTER:"条件筛选", TEXT:"文字检索", COMBINED:"综合搜索"})[kind] || "智能合集";
  }

  function photoSmartCollectionStateLabel(collection) {
    var value = collection || {};
    if (value.freshness === "STRONG_MATCH_REBUILD_REQUIRED") return "需要重新生成高度匹配结果";
    if (value.last_error_code) return "更新失败（" + value.last_error_code + "），可重试";
    return ({CHECK_ON_OPEN:"打开后检查", CURRENT:"已缓存", STALE:"待更新", REFRESHING:"更新中",
      NO_CACHE:"尚无缓存", MODEL_REBIND_REQUIRED:"模型需重新绑定",
      STRONG_MATCH_REBUILD_REQUIRED:"需要重新生成高度匹配结果"})[value.freshness] || "状态待检查";
  }

  function photoSmartThumbUrl(path, assetId) {
    if (typeof path === "string" && path) {
      if (/^https?:\/\//i.test(path)) return path;
      return path.indexOf("/api/v1/") === 0 ? path : (API + (path.charAt(0) === "/" ? path : "/" + path));
    }
    return API + "/assets/" + encodeURIComponent(assetId) + "/thumbnail";
  }

  function photoSmartContextKey() {
    return [state.photoSmart.viewEpoch, state.photoSmart.smartId, state.photoSmart.view].join("|");
  }

  function photoSmartAuthorityKey(collection) {
    var value = collection || state.photoSmart.collection || {};
    return [state.photoSmart.entryEpoch, state.photoSmart.smartId, value.instance_token || "",
      value.rule_revision || ""].join("|");
  }

  function enterPhotoSmartCollection(smartId) {
    var smart = state.photoSmart;
    smart.viewEpoch += 1;
    smart.entryEpoch += 1;
    smart.requestVersion += 1;
    stopPhotoSmartRefreshObserver();
    smart.autoRefreshKeys = Object.create(null);
    smart.smartId = smartId;
    smart.collection = null;
    smart.rule = null;
    smart.pageInFlight = false;
    smart.pageRequestVersion = 0;
    smart.pendingReload = false;
    smart.assetCursor = null;
    smart.exclusionCursor = null;
    smart.assetHasMore = smart.exclusionHasMore = false;
    smart.pageNeedsReload = false;
    smart.view = "results";
    smart.order = "rank";
    els.photoSmartAssets.replaceChildren();
    els.photoSmartMore.hidden = true;
    els.photoSmartReloadResults.hidden = true;
  }

  function photoSmartPageKey(collection, epoch, view, order) {
    var value = collection || {};
    return [epoch, state.photoSmart.smartId, value.instance_token || "", view, order,
      value.revision || "", value.rule_revision || "", value.exclusions_revision || "", value.build_id || ""].join("|");
  }

  function stopPhotoSmartRefreshObserver() {
    if (state.photoSmart.refreshTimer !== null) window.clearTimeout(state.photoSmart.refreshTimer);
    state.photoSmart.refreshTimer = null;
    var refreshRequest = state.photoSmart.refreshRequest;
    state.photoSmart.refreshRequest = null;
    if (refreshRequest) {
      if (refreshRequest.timer !== null) window.clearTimeout(refreshRequest.timer);
      if (refreshRequest.cancel) refreshRequest.cancel({status:0, code:"PHOTO_SMART_REFRESH_STATUS_CANCELLED"});
      if (refreshRequest.controller) refreshRequest.controller.abort();
    }
    state.photoSmart.refreshObserverKey = null;
    if (els.photoSmartRefresh) els.photoSmartRefresh.disabled = false;
  }

  function photoSmartPageAvailable() {
    var smart = state.photoSmart;
    var hasMore = smart.view === "excluded" ? smart.exclusionHasMore : smart.assetHasMore;
    var cursor = smart.view === "excluded" ? smart.exclusionCursor : smart.assetCursor;
    var viewerOpen = document.querySelector && document.querySelector("dialog.photo-viewer-dialog[open]");
    return state.currentView === "photoSmartCollection" && !state.detailRouteActive && !smart.pageNeedsReload &&
      !viewerOpen && !smart.mutationInFlight && !smart.pageInFlight && Boolean(smart.collection) && hasMore && Boolean(cursor);
  }

  function updatePhotoSmartPager() {
    if (!state.photoSmart.pager) return;
    state.photoSmart.pager.setEnabled(state.currentView === "photoSmartCollection" && !state.detailRouteActive);
    state.photoSmart.pager.update();
  }

  function resetPhotoSmartView(view, order) {
    var smart = state.photoSmart;
    smart.viewEpoch += 1;
    smart.requestVersion += 1;
    smart.pageInFlight = false;
    smart.pageRequestVersion = 0;
    smart.pendingReload = false;
    smart.view = view || smart.view;
    if (order) smart.order = order;
    smart.assetCursor = null;
    smart.assetHasMore = false;
    smart.exclusionCursor = null;
    smart.exclusionHasMore = false;
    smart.pageNeedsReload = false;
    if (smart.pager) smart.pager.reset();
    els.photoSmartAssets.replaceChildren();
    els.photoSmartMore.hidden = true;
    els.photoSmartReloadResults.hidden = true;
    els.photoSmartDetailStatus.textContent = smart.view === "excluded" ? "正在加载已排除照片…" : "正在加载匹配结果…";
    updatePhotoSmartPager();
  }

  function renderPhotoSmartTile(asset, excluded, pageKey) {
    var tile = element("div", "card-tile smart-collection-tile");
    tile.dataset.assetId = String(asset.asset_id);
    var open = element("button", "collection-asset-open");
    open.type = "button";
    open.setAttribute("aria-label", (excluded ? "查看已排除照片 #" : "查看照片 #") + asset.asset_id);
    var frame = element("span", "smart-thumbnail-frame");
    var placeholder = element("span", "smart-thumbnail-placeholder", excluded && !asset.thumbnail_url
      ? "原图当前不可用" : "缩略图加载中…");
    placeholder.setAttribute("role", "img");
    placeholder.setAttribute("aria-label", placeholder.textContent);
    frame.appendChild(placeholder);
    if (!excluded || asset.thumbnail_url) {
      var thumb = element("img", "thumbnail");
      thumb.src = photoSmartThumbUrl(excluded ? asset.thumbnail_url : null, asset.asset_id);
      thumb.alt = "";
      thumb.loading = "lazy";
      thumb.decoding = "async";
      thumb.addEventListener("load", function () { placeholder.hidden = true; });
      thumb.addEventListener("error", function () {
        thumb.hidden = true;
        placeholder.hidden = false;
        placeholder.textContent = "缩略图不可用或原图离线";
        placeholder.setAttribute("aria-label", placeholder.textContent);
      });
      frame.appendChild(thumb);
      placeholder.textContent = "缩略图加载中…";
    }
    open.appendChild(frame);
    open.appendChild(element("span", "tile-label", !excluded && state.photoSmart.order === "rank" && asset.smart_rank
      ? "相关度第 " + asset.smart_rank + " 位" : "照片 #" + asset.asset_id));
    open.addEventListener("click", function () {
      if (photoSmartPageKey(state.photoSmart.collection, state.photoSmart.viewEpoch, state.photoSmart.view, state.photoSmart.order) !== pageKey) return;
      if (window.AIPhotoInfo) window.AIPhotoInfo.open(String(asset.asset_id),
        {title:excluded ? "已排除照片" : "智能合集照片", alt:"照片 #" + asset.asset_id});
    });
    tile.appendChild(open);
    var action = element("button", "collection-asset-remove", excluded ? "恢复按规则匹配" : "从智能合集排除");
    action.type = "button";
    action.setAttribute("aria-label", action.textContent + "（照片 #" + asset.asset_id + "）");
    action.disabled = false;
    action.addEventListener("click", function () {
      var smart = state.photoSmart;
      if (smart.mutationInFlight || photoSmartPageKey(smart.collection, smart.viewEpoch, smart.view, smart.order) !== pageKey) return;
      var collection = smart.collection;
      var contextKey = photoSmartContextKey();
      smart.mutationInFlight = true;
      updatePhotoSmartPager();
      request("/photo-smart-collections/" + encodeURIComponent(smart.smartId) + "/exclusions/" + encodeURIComponent(asset.asset_id), {
        method:excluded ? "DELETE" : "PUT", body:{instance_token:collection.instance_token, expected_revision:Number(collection.revision)}
      }).then(function (change) {
        if (contextKey !== photoSmartContextKey()) return;
        collection.revision = change.body.revision;
        collection.exclusions_revision = change.body.exclusions_revision;
        resetPhotoSmartView(smart.view, smart.order);
        loadPhotoSmartAssets(false);
      }).catch(function (failure) {
        if (contextKey !== photoSmartContextKey()) return;
        if (failure.status === 409) els.photoSmartDetailStatus.textContent = "合集已变化，请重新读取后再操作。";
        else showCollectionFailure(failure, els.photoSmartDetailStatus);
      }).then(function () {
        smart.mutationInFlight = false;
        updatePhotoSmartPager();
        if (contextKey === photoSmartContextKey() && smart.pendingReload) {
          smart.pendingReload = false;
          window.setTimeout(function () {
            if (state.currentView === "photoSmartCollection") loadPhotoSmartAssets(false);
          }, 0);
        }
      });
    });
    tile.appendChild(action);
    return tile;
  }

  function observePhotoSmartRefresh(collection, contextKey, attempts) {
    if (state.photoSmart.refreshObserverKey === contextKey) return;
    stopPhotoSmartRefreshObserver();
    state.photoSmart.refreshObserverKey = contextKey;
    if (els.photoSmartRefresh) els.photoSmartRefresh.disabled = true;
    var count = attempts || 0;
    var initialBuildId = collection.build_id || null;
    var maxAttempts = 60;
    var statusTimeoutMs = 8000;
    function currentAuthorityKey() {
      return photoSmartAuthorityKey(state.photoSmart.collection || collection);
    }
    function requestStatus(path) {
      var controller = typeof window.AbortController === "function" ? new window.AbortController() : null;
      var rejectTimeout;
      var timeoutPromise = new Promise(function (_, reject) { rejectTimeout = reject; });
      var active = {controller:controller, timer:null, cancel:function (reason) { rejectTimeout(reason); }};
      function clearActive() {
        if (active.timer !== null) window.clearTimeout(active.timer);
        active.timer = null;
        if (state.photoSmart.refreshRequest === active) state.photoSmart.refreshRequest = null;
      }
      state.photoSmart.refreshRequest = active;
      active.timer = window.setTimeout(function () {
        if (state.photoSmart.refreshRequest !== active) return;
        active.timer = null;
        state.photoSmart.refreshRequest = null;
        rejectTimeout({status:0, code:"PHOTO_SMART_REFRESH_STATUS_TIMEOUT"});
        if (controller) controller.abort();
      }, statusTimeoutMs);
      var operation;
      try {
        operation = request(path, controller ? {signal:controller.signal} : undefined);
      } catch (failure) {
        operation = Promise.reject(failure);
      }
      return Promise.race([operation, timeoutPromise]).then(function (result) {
        clearActive();
        return result;
      }, function (failure) {
        clearActive();
        throw failure;
      });
    }
    function finish(message) {
      if (state.photoSmart.refreshObserverKey !== contextKey) return;
      stopPhotoSmartRefreshObserver();
      if (contextKey === currentAuthorityKey() && state.currentView === "photoSmartCollection" && message) {
        els.photoSmartDetailStatus.textContent = message;
      }
    }
    function schedule(delay) {
      if (count >= maxAttempts) {
        finish("刷新状态暂时无法确认，已保留当前照片；可手动重试或重新读取。");
        return;
      }
      state.photoSmart.refreshTimer = window.setTimeout(check, delay);
    }
    function check() {
      if (state.photoSmart.refreshObserverKey !== contextKey) return;
      state.photoSmart.refreshTimer = null;
      if (contextKey !== currentAuthorityKey() || state.currentView !== "photoSmartCollection") {
        stopPhotoSmartRefreshObserver();
        return;
      }
      if (count >= maxAttempts) {
        finish("刷新状态暂时无法确认，已保留当前照片；可手动重试或重新读取。");
        return;
      }
      count += 1;
      requestStatus("/photo-smart-collections/" + encodeURIComponent(collection.smart_id) +
        "?instance_token=" + encodeURIComponent(collection.instance_token)).then(function (detail) {
        if (state.photoSmart.refreshObserverKey !== contextKey) return;
        if (contextKey !== currentAuthorityKey() || state.currentView !== "photoSmartCollection") {
          stopPhotoSmartRefreshObserver();
          return;
        }
        var current = detail && detail.body && detail.body.collection;
        var status = current && current.refresh_status;
        if (!current || typeof status !== "string") {
          finish("刷新状态无法确认，已保留当前照片；可手动重试或重新读取。");
          return;
        }
        if (status === "RUNNING" || status === "QUEUED" || current.freshness === "REFRESHING") {
          schedule(1000);
          return;
        }
        if (status === "FAILED" || current.last_error_code) {
          finish("上次刷新失败（" + String(current.last_error_code || "PHOTO_SMART_REFRESH_FAILED") +
            "），已保留当前照片；可手动重试。");
          return;
        }
        var currentBuildId = detail.body.build_id || current.build_id || null;
        var hasPublishedCache = status === "NO_CACHE" && Boolean(currentBuildId) && currentBuildId !== initialBuildId;
        if (hasPublishedCache) {
          stopPhotoSmartRefreshObserver();
          var nearTop = (document.documentElement && document.documentElement.scrollTop || window.scrollY || 0) < 400;
          if (nearTop) {
            loadPhotoSmartAssets(false, true);
          } else {
            state.photoSmart.pageNeedsReload = true;
            els.photoSmartReloadResults.hidden = false;
            els.photoSmartDetailStatus.textContent = "新缓存已发布，点击更新；当前浏览位置已保留。";
            updatePhotoSmartPager();
          }
          return;
        }
        finish("刷新状态无法确认（" + status + "），已保留当前照片；可手动重试或重新读取。");
      }).catch(function () {
        if (state.photoSmart.refreshObserverKey !== contextKey) return;
        if (contextKey !== currentAuthorityKey() || state.currentView !== "photoSmartCollection") {
          stopPhotoSmartRefreshObserver();
          return;
        }
        schedule(1200);
      });
    }
    schedule(800);
  }

  function requestPhotoSmartRefresh(collection, manual, contextKey) {
    var smart = state.photoSmart;
    var authorityKey = photoSmartAuthorityKey(collection);
    var key = [collection.smart_id, collection.instance_token, collection.rule_revision || ""].join("|");
    if (!manual && smart.autoRefreshKeys[key]) return Promise.resolve(false);
    if (!manual) smart.autoRefreshKeys[key] = true;
    function isAuthorityLive() {
      return authorityKey === photoSmartAuthorityKey(smart.collection || collection);
    }
    return request("/photo-smart-collections/" + encodeURIComponent(collection.smart_id) + "/refresh", {
      method:"POST", body:{instance_token:collection.instance_token, manual:Boolean(manual)}
    }).then(function (result) {
      if (!isAuthorityLive() || state.currentView !== "photoSmartCollection") return false;
      var body = result.body || {};
      if (body.status === "QUEUED" || body.status === "RUNNING") {
        els.photoSmartDetailStatus.textContent = "正在更新排名候选；完成前保留已发布结果。";
        observePhotoSmartRefresh(collection, authorityKey, 0);
      } else {
        els.photoSmartDetailStatus.textContent = body.reason === "MODEL_REBIND_REQUIRED"
          ? "搜索模型需重新绑定，请用新搜索替换规则。"
          : (body.status === "ALREADY_CURRENT" ? "排名缓存已是最新。"
            : (body.status === "BUSY" ? "刷新队列已满，请稍后重试。" : "排名缓存暂不可更新：" + (body.reason || body.status || "未知状态")));
      }
      if (body.status !== "QUEUED" && body.status !== "RUNNING" && els.photoSmartRefresh) {
        els.photoSmartRefresh.disabled = false;
      }
      return true;
    }).catch(function (failure) {
      if (isAuthorityLive() && state.currentView === "photoSmartCollection") {
        showCollectionFailure(failure, els.photoSmartDetailStatus);
        if (els.photoSmartRefresh) els.photoSmartRefresh.disabled = false;
      }
      return false;
    });
  }

  function photoSmartRefreshStatus(collection, body) {
    var refreshFailure = collection.last_error_code
      ? "上次刷新失败（" + collection.last_error_code + "），可重试。 " : "";
    var resultStatus;
    if (collection.freshness === "STRONG_MATCH_REBUILD_REQUIRED") resultStatus = "需要重新生成高度匹配结果；旧缓存不可浏览或另存。";
    else if (body.stale_label) resultStatus = body.stale_label +
      (body.assets && body.assets.length ? "；照片已按当前范围和排除条件复核。" : "；当前没有可验证的旧候选。");
    else if (body.coverage === "INDEXING") resultStatus = "索引处理中；已发布候选仍可浏览并标记为待更新。";
    else if (body.coverage === "TRUNCATED") resultStatus = "候选已截断，当前仅显示已发布的最多 5000 个候选。";
    else if (collection.freshness === "MODEL_REBIND_REQUIRED") resultStatus = "搜索模型需重新绑定，请用新搜索替换规则。";
    else if (collection.freshness === "NO_CACHE") resultStatus = "尚无排名缓存，正在检查是否可以生成。";
    else if (collection.freshness === "REFRESHING") resultStatus = "正在更新高度匹配照片。";
    else resultStatus = body.assets && body.assets.length ? "显示当前动态筛选或已发布排名候选。"
      : (collection.execution_kind === "COMBINED" ? "暂无高度匹配照片。 可手动刷新高度匹配结果。"
        : "当前没有匹配结果。" + (collection.execution_kind === "FILTER" ? "" : " 可手动刷新排名缓存。"));
    return refreshFailure + resultStatus;
  }

  function loadPhotoSmartAssets(append, forceDetail) {
    var smartId = Number(state.photoSmart.smartId);
    var smart = state.photoSmart;
    if (!Number.isSafeInteger(smartId) || smartId < 1 || (append && !photoSmartPageAvailable())) return Promise.resolve(false);
    if (smart.pageInFlight) {
      if (!append) smart.pendingReload = true;
      return Promise.resolve(false);
    }
    var version = ++smart.requestVersion, epoch = smart.viewEpoch, view = smart.view, order = smart.order;
    var contextKey = photoSmartContextKey(), pageKey = null;
    var isContextLive = function () { return version === smart.requestVersion && contextKey === photoSmartContextKey() &&
      state.currentView === "photoSmartCollection" && !state.detailRouteActive && order === smart.order; };
    smart.pageInFlight = true;
    smart.pageRequestVersion = version;
    var ready = append || (smart.collection && !forceDetail)
      ? Promise.resolve({body:{collection:smart.collection, rule:smart.rule || {}}})
      : request("/photo-smart-collections/" + smartId + (forceDetail && smart.collection
        ? "?instance_token=" + encodeURIComponent(smart.collection.instance_token) : ""));
    return ready.then(function (detail) {
      if (!isContextLive()) return false;
      var collection = detail.body.collection;
      if (detail.body.build_id) collection.build_id = detail.body.build_id;
      if (!append && smart.collection &&
          photoSmartPageKey(smart.collection, epoch, view, order) !== photoSmartPageKey(collection, epoch, view, order)) {
        els.photoSmartAssets.replaceChildren();
        smart.assetCursor = smart.exclusionCursor = null;
        smart.assetHasMore = smart.exclusionHasMore = false;
        smart.pageNeedsReload = false;
        smart.collection = collection;
        smart.rule = detail.body.rule || smart.rule;
      }
      if (append && photoSmartPageKey(collection, epoch, view, order) !== photoSmartPageKey(smart.collection, epoch, view, order)) {
        smart.pageNeedsReload = true;
        smart.assetCursor = smart.exclusionCursor = null;
        els.photoSmartReloadResults.hidden = false;
        els.photoSmartDetailStatus.textContent = "合集结果已变化，点击重新加载。";
        updatePhotoSmartPager();
        return false;
      }
      var countLabel = collection.execution_kind === "COMBINED" && collection.candidate_count !== null &&
        collection.candidate_count !== undefined ? " · 高度匹配 · " + Number(collection.candidate_count) + " 张" : "";
      els.photoSmartDetailTitle.textContent = collection.name + " · " + photoSmartKindLabel(collection.execution_kind) + countLabel;
      var freshnessLabel = photoSmartCollectionStateLabel(collection);
      els.photoSmartRuleSummary.textContent = ((detail.body.rule && detail.body.rule.display_query) || "纯条件筛选") +
        " · " + freshnessLabel + (detail.body.indexing || collection.indexing ? " · 索引处理中" : "") +
        (collection.truncated ? " · 候选已截断（最多 5000 个候选）" : "");
      var isFilter = collection.execution_kind === "FILTER";
      Array.prototype.forEach.call(els.photoSmartOrder.options, function (option) { if (option.value === "rank") option.hidden = isFilter; });
      if (isFilter && smart.order !== "capture_desc") { smart.order = "capture_desc"; order = "capture_desc"; }
      if (!isFilter && smart.order !== "rank" && smart.order !== "capture_desc") { smart.order = "rank"; order = "rank"; }
      els.photoSmartOrder.value = smart.order;
      pageKey = photoSmartPageKey(collection, epoch, view, order);
      if (!append && view === "results" && collection.execution_kind !== "FILTER") {
        if (collection.refresh_status === "QUEUED" || collection.refresh_status === "RUNNING" || collection.freshness === "REFRESHING") {
          observePhotoSmartRefresh(collection, photoSmartAuthorityKey(collection), 0);
        } else if (["CURRENT", "MODEL_REBIND_REQUIRED"].indexOf(collection.freshness) < 0) {
          requestPhotoSmartRefresh(collection, false, contextKey);
        }
      }
      var cursor = view === "excluded" ? smart.exclusionCursor : smart.assetCursor;
      var endpoint = view === "excluded" ? "/photo-smart-collections/" + smartId + "/exclusions?limit=50&instance_token="
        : "/photo-smart-collections/" + smartId + "/assets?limit=50&instance_token=";
      var path = endpoint + encodeURIComponent(collection.instance_token);
      if (view !== "excluded") path += "&order=" + encodeURIComponent(order);
      if (append && cursor) path += "&cursor=" + encodeURIComponent(cursor);
      return request(path).then(function (result) {
        if (!isContextLive() || (append && photoSmartPageKey(smart.collection, epoch, view, order) !== pageKey)) return false;
        var body = result.body || {}, assets = body.assets || [];
        var nextCursor = body.next_cursor || null, hasMore = Boolean(body.has_more);
        if (hasMore && (!nextCursor || (append && nextCursor === cursor))) {
          smart.pageNeedsReload = true;
          smart.assetCursor = smart.exclusionCursor = null;
          els.photoSmartDetailStatus.textContent = "分页游标无效或未前进，请重新读取合集。";
          els.photoSmartReloadResults.textContent = "合集结果已变化，点击重新加载";
          els.photoSmartReloadResults.hidden = false;
          return false;
        }
        if (body.freshness && body.freshness !== collection.freshness && body.freshness === "STALE" && append) {
          smart.pageNeedsReload = true;
          els.photoSmartDetailStatus.textContent = "合集结果已变化，点击重新加载。";
          els.photoSmartReloadResults.hidden = false;
          return false;
        }
        var responseCollection = body.collection || collection;
        if (collection.build_id && !responseCollection.build_id) {
          responseCollection = Object.assign({}, responseCollection, {build_id:collection.build_id});
        }
        if (photoSmartPageKey(responseCollection, epoch, view, order) !== pageKey) {
          if (!append) {
            els.photoSmartAssets.replaceChildren();
            smart.assetCursor = smart.exclusionCursor = null;
            smart.assetHasMore = smart.exclusionHasMore = false;
            smart.collection = responseCollection;
          }
          smart.pageNeedsReload = true;
          smart.assetCursor = smart.exclusionCursor = null;
          els.photoSmartReloadResults.hidden = false;
          els.photoSmartReloadResults.textContent = "合集结果已变化，点击重新加载";
          els.photoSmartDetailStatus.textContent = "合集结果已变化，点击重新加载。已显示的照片仍保留。";
          return false;
        }
        if (append) {
          var seen = Object.create(null);
          Array.prototype.forEach.call(els.photoSmartAssets.querySelectorAll("[data-asset-id]"), function (tile) {
            seen[tile.dataset.assetId] = true;
          });
          var duplicate = assets.some(function (asset) {
            var id = String(asset.asset_id);
            if (seen[id]) return true;
            seen[id] = true;
            return false;
          });
          if (duplicate) {
            smart.pageNeedsReload = true;
            smart.assetCursor = smart.exclusionCursor = null;
            els.photoSmartDetailStatus.textContent = "续页与已显示照片重复，已停止自动拼接；请重新读取合集。";
            els.photoSmartReloadResults.hidden = false;
            els.photoSmartReloadResults.textContent = "合集结果已变化，点击重新加载";
            return false;
          }
        }
        if (!append) els.photoSmartAssets.replaceChildren();
        smart.collection = responseCollection;
        smart.rule = detail.body.rule || smart.rule;
        if (!append) {
          smart.pageNeedsReload = false;
          els.photoSmartReloadResults.hidden = true;
          els.photoSmartReloadResults.textContent = "合集结果已变化，点击重新加载";
        }
        if (view === "excluded") { smart.exclusionCursor = nextCursor; smart.exclusionHasMore = hasMore; }
        else { smart.assetCursor = nextCursor; smart.assetHasMore = hasMore; }
        assets.forEach(function (asset) { els.photoSmartAssets.appendChild(renderPhotoSmartTile(asset, view === "excluded", pageKey)); });
        if (view === "excluded") {
          els.photoSmartDetailStatus.textContent = assets.length ? "这些排除记录会保留，直到你逐项恢复。" : "没有已排除照片。";
        } else els.photoSmartDetailStatus.textContent = photoSmartRefreshStatus(collection, body);
        els.photoSmartMore.hidden = !(view === "excluded" ? smart.exclusionHasMore : smart.assetHasMore);
        updatePhotoSmartPager();
        return true;
      });
    }).catch(function (failure) {
      if (!isContextLive()) return false;
      if (append && failure.status === 409) {
        smart.pageNeedsReload = true;
        smart.assetCursor = smart.exclusionCursor = null;
        els.photoSmartReloadResults.hidden = false;
        els.photoSmartReloadResults.textContent = "合集结果已变化，点击重新加载";
        els.photoSmartDetailStatus.textContent = "合集结果已变化，点击重新加载。已显示的照片仍保留。";
        updatePhotoSmartPager();
      } else {
        if (!append) {
          els.photoSmartReloadResults.hidden = false;
          els.photoSmartReloadResults.textContent = "当前列表加载失败，点击重试";
        }
        showCollectionFailure(failure, els.photoSmartDetailStatus);
      }
      return false;
    }).then(function (ok) {
      if (version === smart.pageRequestVersion) {
        smart.pageInFlight = false;
        updatePhotoSmartPager();
        if (smart.pendingReload) {
          smart.pendingReload = false;
          window.setTimeout(function () {
            if (state.currentView === "photoSmartCollection") loadPhotoSmartAssets(false);
          }, 0);
        }
      }
      return ok === true;
    });
  }

  function loadPhotoSmartExclusions(version, append) {
    return version === state.photoSmart.requestVersion ? loadPhotoSmartAssets(append) : Promise.resolve(false);
  }

  function loadPhotoCollectionAssets(append) {
    var cid = Number(state.photoCollections.collectionId);
    if (!Number.isSafeInteger(cid) || cid < 1) return;
    var version = ++state.photoCollections.requestVersion;
    var detail = state.photoCollections.collection;
    var ready = detail ? Promise.resolve({body:{collection:detail}}) : request("/photo-collections/" + cid);
    ready.then(function (detailResult) {
      var collection = detailResult.body.collection;
      state.photoCollections.collection = collection;
      updateSearchScopeAction();
      var path = "/photo-collections/" + cid + "/assets?limit=50&instance_token=" + encodeURIComponent(collection.instance_token);
      if (append && state.photoCollections.memberCursor) path += "&cursor=" + encodeURIComponent(state.photoCollections.memberCursor);
      return request(path);
    }).then(function (result) {
      if (version !== state.photoCollections.requestVersion) return;
      var body = result.body || {};
      state.photoCollections.collection = body.collection;
      state.photoCollections.memberCursor = body.next_cursor || null;
      state.photoCollections.memberHasMore = Boolean(body.has_more);
      els.photoCollectionTitle.textContent = body.collection.name + " · " + body.collection.member_count + " 张";
      updateSearchScopeAction();
      if (!append) els.photoCollectionAssets.replaceChildren();
      (body.assets || []).forEach(function (asset) {
        var tile = element("div", "card-tile");
        tile.dataset.assetId = String(asset.asset_id);
        var open = element("button", "collection-asset-open");
        open.type = "button";
        var thumb = element("img", "thumbnail");
        thumb.src = API + "/assets/" + encodeURIComponent(asset.asset_id) + "/thumbnail";
        thumb.alt = "照片 #" + asset.asset_id;
        thumb.loading = "lazy";
        open.appendChild(thumb);
        open.appendChild(element("span", "tile-label", "第 " + asset.collection_position + " 张"));
        open.addEventListener("click", function () {
          if (window.AIPhotoInfo) window.AIPhotoInfo.open(String(asset.asset_id), {title:"查看照片", alt:"照片 #" + asset.asset_id});
        });
        tile.appendChild(open);
        var remove = element("button", "collection-asset-remove", "移出合集");
        remove.type = "button";
        remove.addEventListener("click", function () {
          var collection = state.photoCollections.collection;
          request("/photo-collections/" + encodeURIComponent(cid) + "/members/batch", {method:"POST", body:{
            action:"remove", asset_ids:[Number(asset.asset_id)], instance_token:collection.instance_token,
            expected_revision:Number(collection.revision)
          }}).then(function (change) {
            state.photoCollections.collection = change.body.collection;
            state.photoCollections.memberCursor = null;
            loadPhotoCollectionAssets(false);
          }).catch(function (failure) { showCollectionFailure(failure, els.photoCollectionDetailStatus); });
        });
        tile.appendChild(remove);
        els.photoCollectionAssets.appendChild(tile);
      });
      els.photoCollectionMore.hidden = !state.photoCollections.memberHasMore;
      els.photoCollectionDetailStatus.textContent = body.assets && body.assets.length
        ? "按合集收录顺序显示。" : "这个合集还没有照片。";
      if (state.photoCollections.detailMode === "timeline") showPhotoCollectionMode("timeline");
    }).catch(function (failure) { showCollectionFailure(failure, els.photoCollectionDetailStatus); });
  }

  function showPhotoCollectionMode(mode) {
    var timeline = mode === "timeline";
    state.photoCollections.detailMode = timeline ? "timeline" : "list";
    els.photoCollectionAssets.hidden = timeline;
    els.photoCollectionMore.hidden = timeline || !state.photoCollections.memberHasMore;
    els.photoCollectionTimeline.hidden = !timeline;
    els.photoCollectionShowList.setAttribute("aria-pressed", String(!timeline));
    els.photoCollectionShowTimeline.setAttribute("aria-pressed", String(timeline));
    if (!timeline || !state.photoCollections.collection) return;
    var collection = state.photoCollections.collection;
    var scope = {kind:"photo_collection", photo_collection_id:Number(collection.collection_id),
      photo_collection_instance:collection.instance_token, photo_collection_revision:Number(collection.revision)};
    if (!window.PhotoTimeline) {
      els.photoCollectionDetailStatus.textContent = "时间轴暂不可用，请刷新页面后重试。";
      return;
    }
    if (!state.photoCollections.timelineMount) {
      state.photoCollections.timelineMount = window.PhotoTimeline.mount({
        host:els.photoCollectionTimeline, scope:scope,
        adapter:{renderAsset:timelineRenderAsset, onOpen:function (item) {
          if (window.AIPhotoInfo) window.AIPhotoInfo.open(String(item.asset_id),
            {title:"查看合集照片", alt:"照片 #" + item.asset_id});
        }},
      });
      state.photoCollections.timelineScope = JSON.stringify(scope);
    } else if (state.photoCollections.timelineScope !== JSON.stringify(scope)) {
      state.photoCollections.timelineScope = JSON.stringify(scope);
      state.photoCollections.timelineMount.setScope(scope);
    }
  }

  function handleHash() {
    var detailsMatch = /^#\/asset\/(\d+)\/details$/.exec(String(location.hash || ""));
    if (detailsMatch) {
      if (state.detailViewer && state.detailViewer.dialog && state.detailViewer.dialog.open) {
        state.detailRouteTransition = true;
        state.detailViewer.close();
      }
      var sameDetailsRoute = state.detailRouteActive && state.detailAssetId === detailsMatch[1];
      state.detailRouteActive = true;
      state.detailAssetId = detailsMatch[1];
      if (!sameDetailsRoute) {
        var detailIntent = window.AIPhotoInfo && window.AIPhotoInfo.consumeDetailReturn
          ? window.AIPhotoInfo.consumeDetailReturn(detailsMatch[1]) : null;
        state.detailReturnIntent = detailIntent && !detailIntent.invalid ? detailIntent : null;
        state.detailReturnWarning = Boolean(detailIntent && detailIntent.invalid);
        if (state.detailReturnWarning) showNotice("原来源状态已失效或不可用；仍可查看当前照片，关闭后会安全返回照片页。");
      }
      if (!state.detailReturnIntent
          && window.history && window.history.replaceState && window.history.pushState
          && !(window.history.state && window.history.state.photoDetailsRoute)) {
        window.history.replaceState({ photoDetailsSafeHome: true }, "", "/");
        window.history.pushState({ photoDetailsRoute: true }, "", "/#/asset/" + encodeURIComponent(detailsMatch[1]) + "/details");
      }
      renderDetail(detailsMatch[1]);
      return;
    }
    var match = /^#\/asset\/(\d+)$/.exec(String(location.hash || ""));
    if (match) {
      state.detailRouteActive = true;
      if (!state.browse.returnContext) restoreBrowseReturn(match[1]);
      if (!window.AIPhotoInfo) { renderDetail(match[1]); return; }
      showView("browse");
      if (!state.detailViewer || !state.detailViewer.dialog.open) {
        state.detailViewer = window.AIPhotoInfo.open(match[1], {
          title: "查看照片", alt: "照片 #" + match[1],
          history: false,
          onOpenFullDetails: function (id) {
            state.detailRouteActive = false;
            return window.AIPhotoInfo.navigateToDetails(id, "home", "/", true);
          },
          onClose: function () {
            state.detailViewer = null;
            if (state.detailRouteTransition) { state.detailRouteTransition = false; return; }
            if (!state.detailRouteActive) return;
            state.detailRouteActive = false;
            var showedDetailReturnWarning = state.detailReturnWarning;
            state.detailReturnWarning = false;
            var detailReturn = window.AIPhotoInfo && window.AIPhotoInfo.consumeDetailReturn(match[1]);
            if (detailReturn && detailReturn.invalid) {
              showNotice("原来源状态已失效或不可用；已安全返回照片页。");
            }
            else if (showedDetailReturnWarning) showNotice("原来源状态已失效或不可用；已安全返回照片页。");
            if (detailReturn && !detailReturn.invalid && detailReturn.source !== "home") { window.location.replace(detailReturn.path); return; }
            var fromSearch = false;
            try { fromSearch = window.sessionStorage.getItem(SEARCH_RETURN_SOURCE_KEY) === "search"; if (fromSearch) window.sessionStorage.removeItem(SEARCH_RETURN_SOURCE_KEY); } catch (_error) {}
            if (fromSearch) { window.location.href = "/search"; return; }
            if (!state.browse.returnContext && window.location.replace) { window.location.replace("/"); return; }
            clearHash();
            if (state.browse.returnContext) { state.browse.returnPending = true; restoreBrowsePosition(); clearBrowseReturn(); }
            showView("browse");
          }
        });
      }
      return;
    }
    if (state.detailRouteActive) {
      state.detailRouteActive = false;
      state.detailReturnIntent = null;
      state.detailReturnWarning = false;
      if (window.history && window.history.state && window.history.state.photoViewerKey) {
        showView("browse");
        return;
      }
      if (state.detailViewer && state.detailViewer.dialog.open) { state.detailViewer.close(); return; }
      if (state.browse.returnContext) {
        state.browse.returnPending = true;
        showView("browse");
        restoreBrowsePosition();
        clearBrowseReturn();
        return;
      }
    }
    var params = new URLSearchParams(String(location.search || ""));
    if (!location.hash) {
      try {
        var savedBrowse = JSON.parse(window.sessionStorage.getItem(BROWSE_RETURN_KEY) || "null");
        var expectedScope = params.get("view") === "favorites" ? "favorites" : "all";
        if (savedBrowse && savedBrowse.scope === expectedScope && Number.isFinite(Number(savedBrowse.savedAt)) && Date.now() - Number(savedBrowse.savedAt) < 30 * 60 * 1000) {
          restoreBrowseReturn(savedBrowse.assetId);
        }
      } catch (_error) {}
    }
    setBrowseScope(params.get("view") === "favorites" ? "favorites" : "all");
    var view = params.get("view");
    if (!location.hash && !view && state.browse.scope === "all") {
      var savedRandom = readRandomBrowse();
      if (savedRandom && restoreRandomSnapshot(savedRandom)) state.browse.mode = "random";
    }
    if (view === "photo-collection") {
      state.photoCollections.collectionId = params.get("id");
      state.photoCollections.collection = null;
      state.photoCollections.memberCursor = null;
      showView("photoCollection");
    } else if (view === "photo-collections") {
      state.photoCollections.cursor = null;
      state.photoCollectionKind = params.get("kind") === "smart" ? "smart" : "ordinary";
      showView("photoCollections");
    } else if (view === "photo-smart-collection") {
      enterPhotoSmartCollection(params.get("id"));
      showView("photoSmartCollection");
    } else {
      showView(view === "sources" ? "sources" : "browse");
    }
  }

  //: The person page is only offered when the read layer it needs really exists in this
  //: catalog: the P3-05B schema (person controls, the widened operation log, the split
  //: vocabulary). Missing face models never hide it - browsing and curating the person data
  //: that already exists works without a model.
  function revealPersonsNav(body) {
    var link = document.getElementById("nav-persons");
    if (!link) {
      return;
    }
    var version = Number(body && body.schema_version);
    var usable =
      isFinite(version) && version >= 13 && String(body && body.schema_status) === "ok";
    link.hidden = !usable;
  }

  function probeCapabilities() {
    // Hidden-by-default navigation entry: it is revealed only when this catalog really
    // carries the person read layer (see revealPersonsNav).
    request("/capabilities/page")
      .then(function (result) {
        var body = result.body || {};
        if (state.detailReturnWarning) showNotice("原来源状态已失效或不可用；仍可查看当前照片，关闭后会安全返回照片页。");
        else clearNotice();
        revealPersonsNav(body);
        var schema =
          body.schema_version === null || body.schema_version === undefined
            ? "尚未初始化"
            : "v" + body.schema_version;
        text(els.appMeta, "版本 " + body.app_version + " · 图库格式 " + schema);
      })
      .catch(function (failure) {
        if (failure.code === "SCHEMA_TOO_NEW") {
          showNotice(
            "图库的 schema 版本高于本程序支持的版本（" +
              failure.code +
              "）。请升级程序或改用匹配的数据目录；本页面不会显示空图库来误导你。" +
              "（请求编号 " +
              (failure.request_id || "无") +
              "）"
          );
          return;
        }
        if (failure.status !== 401) {
          text(els.appMeta, "读取程序能力失败：" + describe(failure));
        }
      });
  }

  function boot() {
    els = {
      appMeta: document.getElementById("app-meta"),
      searchScopeAction: document.getElementById("global-search-scope"),
      notice: document.getElementById("notice"),
      main: document.getElementById("main"),
      sourcesList: document.getElementById("sources-list"),
      sourcesStatus: document.getElementById("sources-status"),
      refreshLibrary: document.getElementById("refresh-library"),
      buildAllLibrary: document.getElementById("build-all-library"),
      storageSummary: document.getElementById("storage-summary"),
      storageMaintenancePreview: document.getElementById("storage-maintenance-preview"),
      storageBackupDirectory: document.getElementById("storage-backup-directory"),
      storageBackupStatus: document.getElementById("storage-backup-status"),
      libraryRetentionStatus: document.getElementById("library-retention-status"),
      libraryRetentionAnomalies: document.getElementById("library-retention-anomalies"),
      libraryRetentionMore: document.getElementById("library-retention-more"),
      libraryRetentionAutomatic: document.getElementById("library-retention-automatic"),
      libraryRetentionCleanup: document.getElementById("library-retention-cleanup"),
      faceProfile: document.getElementById("face-profile"),
      refreshStatus: document.getElementById("refresh-status"),
      sourceForm: document.getElementById("source-form"),
      sourceFormError: document.getElementById("source-form-error"),
      sourceName: document.getElementById("source-name"),
      sourceRoot: document.getElementById("source-root"),
      browseStatus: document.getElementById("browse-status"),
      browseTimeline: document.getElementById("browse-timeline"),
      browseListView: document.getElementById("browse-list-view"),
      browseShowTimeline: document.getElementById("browse-show-timeline"),
      browseShowList: document.getElementById("browse-show-list"),
      browseShowRandom: document.getElementById("browse-show-random"),
      browseRandomControls: document.getElementById("browse-random-controls"),
      browseRandomSize: document.getElementById("browse-random-size"),
      browseRandomNext: document.getElementById("browse-random-next"),
      browseRandomState: document.getElementById("browse-random-state"),
      browseTitle: document.getElementById("view-browse-title"),
      browseIntro: document.getElementById("browse-intro"),
      browseTimePolicy: document.getElementById("browse-time-policy"),
      browseModeControls: document.getElementById("browse-mode-controls"),
      browseControls: document.getElementById("browse-controls"),
      browseListFilters: document.getElementById("browse-list-filters"),
      browseListTimeHint: document.getElementById("browse-list-time-hint"),
      browseTimelineNote: document.getElementById("browse-timeline-note"),
      browseScope: document.getElementById("browse-scope"),
      browseScopeName: document.getElementById("browse-scope-name"),
      browseSource: document.getElementById("browse-source"),
      browseOrder: document.getElementById("browse-order"),
      browseCaptureFrom: document.getElementById("browse-capture-from"),
      browseCaptureTo: document.getElementById("browse-capture-to"),
      browseCaptureState: document.getElementById("browse-capture-state"),
      assetGrid: document.getElementById("asset-grid"),
      favoritesOrderLabel: document.getElementById("favorites-order-label"),
      favoritesOrder: document.getElementById("favorites-order"),
      photoSelectionWrap: document.getElementById("photo-selection"),
      photoSelectionToggle: document.getElementById("photo-select-toggle"),
      photoSelectionToolbar: document.getElementById("photo-selection-toolbar"),
      photoSelectionCount: document.getElementById("photo-selection-count"),
      photoSelectAll: document.getElementById("photo-select-all"),
      photoSelectInvert: document.getElementById("photo-select-invert"),
      photoSelectClear: document.getElementById("photo-select-clear"),
      photoSelectAdd: document.getElementById("photo-select-add"),
      photoSelectRemove: document.getElementById("photo-select-remove"),
      loadMore: document.getElementById("load-more"),
      favoritesReload: document.getElementById("favorites-reload"),
      gridSentinel: document.getElementById("grid-sentinel"),
      detailBody: document.getElementById("detail-body"),
      photoCollectionsList: document.getElementById("photo-collections-list"),
      photoCollectionsStatus: document.getElementById("photo-collections-status"),
      photoCollectionsMore: document.getElementById("photo-collections-more"),
      photoCollectionCreate: document.getElementById("photo-collection-create"),
      photoCollectionsIntro: document.getElementById("photo-collections-intro"),
      photoCollectionsTabOrdinary: document.getElementById("photo-collections-tab-ordinary"),
      photoCollectionsTabSmart: document.getElementById("photo-collections-tab-smart"),
      photoSmartCollectionsPanel: document.getElementById("photo-smart-collections-panel"),
      photoSmartCollectionsList: document.getElementById("photo-smart-collections-list"),
      photoSmartCollectionsStatus: document.getElementById("photo-smart-collections-status"),
      photoSmartCollectionsMore: document.getElementById("photo-smart-collections-more"),
      photoSmartDetailTitle: document.getElementById("photo-smart-detail-title"),
      photoSmartRuleSummary: document.getElementById("photo-smart-rule-summary"),
      photoSmartDetailStatus: document.getElementById("photo-smart-detail-status"),
      photoSmartAssets: document.getElementById("photo-smart-assets"),
      photoSmartSentinel: document.getElementById("photo-smart-sentinel"),
      photoSmartReloadResults: document.getElementById("photo-smart-reload-results"),
      photoSmartMore: document.getElementById("photo-smart-more"),
      photoSmartRefresh: document.getElementById("photo-smart-refresh"),
      photoSmartSaveStatic: document.getElementById("photo-smart-save-static"),
      photoSmartShowResults: document.getElementById("photo-smart-show-results"),
      photoSmartShowExclusions: document.getElementById("photo-smart-show-exclusions"),
      photoSmartOrder: document.getElementById("photo-smart-order"),
      photoSmartReplaceRule: document.getElementById("photo-smart-replace-rule"),
      photoSmartDelete: document.getElementById("photo-smart-delete"),
      photoCollectionTitle: document.getElementById("photo-collection-detail-title"),
      photoCollectionDetailStatus: document.getElementById("photo-collection-detail-status"),
      photoCollectionAssets: document.getElementById("photo-collection-assets"),
      photoCollectionMore: document.getElementById("photo-collection-more"),
      photoCollectionTimeline: document.getElementById("photo-collection-timeline"),
      photoCollectionShowList: document.getElementById("photo-collection-show-list"),
      photoCollectionShowTimeline: document.getElementById("photo-collection-show-timeline"),
      photoCollectionSearch: document.getElementById("photo-collection-search"),
      navButtons: document.querySelectorAll(".nav-button"),
      views: {
        sources: document.getElementById("view-sources"),
        browse: document.getElementById("view-browse"),
        detail: document.getElementById("view-detail"),
        photoCollections: document.getElementById("view-photo-collections"),
        photoCollection: document.getElementById("view-photo-collection"),
        photoSmartCollection: document.getElementById("view-photo-smart-collection"),
      },
    };

    if (window.PhotoScrollPager) state.photoSmart.pager = window.PhotoScrollPager.create({
      sentinel:els.photoSmartSentinel, button:els.photoSmartMore,
      canLoad:photoSmartPageAvailable, load:function () { return loadPhotoSmartAssets(true); },
      loadLabel:"加载更多", retryLabel:"重试加载"
    });

    if (window.PhotoBatchSelect) state.browse.photoSelection = window.PhotoBatchSelect.create(els.assetGrid, {
      toggle: els.photoSelectionToggle, toolbar: els.photoSelectionToolbar, count: els.photoSelectionCount,
      all: els.photoSelectAll, invert: els.photoSelectInvert, clear: els.photoSelectClear,
      add: els.photoSelectAdd, remove: els.photoSelectRemove,
    }, function (body) {
      els.photoSelectionCount.textContent = (body.is_favorite ? "已加入收藏 " : "已取消收藏 ") + body.changed + " 张，" + body.unchanged + " 张已是目标状态。";
      if (state.browse.scope === "favorites" && !body.is_favorite) loadBrowse(true);
    });

    els.setSearchScopeAction = window.SearchScopeEntry && window.SearchScopeEntry.configure({
      button: els.searchScopeAction,
      input: document.getElementById("global-search-query"),
      getScope: function () {
        if (!els.views.photoCollection.hidden && state.photoCollections.collection) {
          var collection = state.photoCollections.collection;
          return {kind:"photo_collection", photo_collection_id:Number(collection.collection_id),
            instance_token:String(collection.instance_token), revision:Number(collection.revision), name:String(collection.name)};
        }
        if (els.views.browse.hidden || state.detailRouteActive || !state.sourcesLoaded) return null;
        if (state.browse.scope === "favorites") return { kind: "favorites" };
        var source = state.sources.find(function (item) {
          return String(item.source_id) === String(state.browse.sourceId);
        });
        return source ? { kind: "source", source_id: Number(source.source_id), name: String(source.display_name || "图库") } : null;
      },
    });

    els.navButtons.forEach(function (button) {
      button.addEventListener("click", function () {
        clearHash();
        state.detailRouteActive = false;
        clearBrowseReturn();
        showView(button.dataset.view);
      });
    });
    function returnFromDetail() {
      if (!state.detailRouteActive || !state.detailAssetId) return;
      if (state.detailReturnIntent && window.AIPhotoInfo && window.AIPhotoInfo.restoreDetailReturn) {
        window.AIPhotoInfo.restoreDetailReturn(state.detailAssetId, state.detailReturnIntent);
      }
      window.location.replace("/#/asset/" + encodeURIComponent(state.detailAssetId));
    }
    document.getElementById("detail-back").addEventListener("click", returnFromDetail);
    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape" || !state.detailRouteActive || event.defaultPrevented) return;
      if (event.target && event.target.closest && event.target.closest("dialog[open]")) return;
      event.preventDefault();
      returnFromDetail();
    });
    document.getElementById("view-all-galleries").addEventListener("click", function () {
      state.browse.sourceId = "";
      els.browseSource.value = "";
      state.browse.loaded = false;
      showView("browse");
    });
    document.getElementById("photo-collection-create").addEventListener("submit", function (event) {
      event.preventDefault();
      var input = document.getElementById("photo-collection-name");
      request("/photo-collections", {method:"POST", body:{name:input.value, create_request_id:window.crypto.randomUUID()}})
        .then(function () { input.value = ""; loadPhotoCollections(false); })
        .catch(function (failure) { showCollectionFailure(failure, els.photoCollectionsStatus); });
    });
    els.photoCollectionShowList.addEventListener("click", function () { showPhotoCollectionMode("list"); });
    els.photoCollectionShowTimeline.addEventListener("click", function () { showPhotoCollectionMode("timeline"); });
    els.photoCollectionSearch.addEventListener("click", function () {
      updateSearchScopeAction();
      if (!els.searchScopeAction.hidden) els.searchScopeAction.click();
    });
    els.photoCollectionsMore.addEventListener("click", function () { loadPhotoCollections(true); });
    els.photoCollectionMore.addEventListener("click", function () { loadPhotoCollectionAssets(true); });
    els.photoCollectionsTabOrdinary.addEventListener("click", function () {
      state.photoCollections.cursor = null; loadPhotoCollections(false);
    });
    els.photoCollectionsTabSmart.addEventListener("click", function () {
      state.photoSmart.cursor = null; loadPhotoSmartCollections(false);
    });
    els.photoSmartCollectionsMore.addEventListener("click", function () { loadPhotoSmartCollections(true); });
    els.photoSmartReloadResults.addEventListener("click", function () {
      loadPhotoSmartAssets(false, true);
    });
    els.photoSmartShowResults.addEventListener("click", function () {
      resetPhotoSmartView("results", state.photoSmart.order);
      els.photoSmartShowResults.setAttribute("aria-pressed", "true");
      els.photoSmartShowExclusions.setAttribute("aria-pressed", "false");
      loadPhotoSmartAssets(false);
    });
    els.photoSmartShowExclusions.addEventListener("click", function () {
      resetPhotoSmartView("excluded", state.photoSmart.order);
      els.photoSmartShowResults.setAttribute("aria-pressed", "false");
      els.photoSmartShowExclusions.setAttribute("aria-pressed", "true");
      loadPhotoSmartAssets(false);
    });
    els.photoSmartOrder.addEventListener("change", function () {
      if (state.photoSmart.collection && state.photoSmart.collection.execution_kind === "FILTER") {
        state.photoSmart.order = "capture_desc"; els.photoSmartOrder.value = "capture_desc"; return;
      }
      resetPhotoSmartView(state.photoSmart.view, els.photoSmartOrder.value === "capture_desc" ? "capture_desc" : "rank");
      loadPhotoSmartAssets(false);
    });
    els.photoSmartSaveStatic.addEventListener("click", function () {
      var collection = state.photoSmart.collection;
      if (!collection) return;
      var confirmStale = false, confirmTruncated = false, confirmIndexing = false;
      var mustConfirmStale = ["STALE", "REFRESHING"].indexOf(collection.freshness) >= 0;
      var ask = function (needed, title, message) {
        return needed ? window.AIPhotoInfo.actionDialog({kind:"confirm", title:title, message:message,
          confirmLabel:"继续另存"}) : Promise.resolve(true);
      };
      ask(mustConfirmStale, "保存上次结果", "排名候选尚未更新。保存前会按当前筛选范围和排除记录复核。")
        .then(function (ok) {
          if (!ok) return false;
          confirmStale = mustConfirmStale;
          return ask(Boolean(collection.truncated), "保存可能截断的候选", "候选最多 5000 张且可能还有其他照片。")
            .then(function (confirmed) { confirmTruncated = Boolean(collection.truncated && confirmed); return confirmed; });
        }).then(function (ok) {
          if (!ok) return false;
          return ask(Boolean(collection.indexing), "索引仍在更新", "将保存当前已发布并经当前范围复核的候选。")
            .then(function (confirmed) { confirmIndexing = Boolean(collection.indexing && confirmed); return confirmed; });
        }).then(function (ok) {
          if (!ok) return;
          els.photoSmartSaveStatic.disabled = true;
          return request("/photo-smart-collections/" + encodeURIComponent(collection.smart_id) + "/save-static", {
            method:"POST", body:{instance_token:collection.instance_token,
              expected_revision:Number(collection.revision), name:collection.name + "（静态副本）",
              create_request_id:window.crypto.randomUUID(), order:state.photoSmart.order, confirm_stale:confirmStale,
              confirm_truncated:confirmTruncated, confirm_indexing:confirmIndexing}
          }).then(function (result) {
            els.photoSmartDetailStatus.textContent = "已另存为普通合集；智能规则与排除记录保持不变。";
            window.location.href = "/?view=photo-collection&id=" + encodeURIComponent(result.body.collection.collection_id);
          });
        }).catch(function (failure) {
          showCollectionFailure(failure, els.photoSmartDetailStatus);
          els.photoSmartSaveStatic.disabled = false;
        });
    });
    els.photoSmartRefresh.addEventListener("click", function () {
      var collection = state.photoSmart.collection;
      if (!collection || collection.execution_kind === "FILTER") return;
      var contextKey = photoSmartContextKey();
      els.photoSmartRefresh.disabled = true;
      requestPhotoSmartRefresh(collection, true, contextKey).then(function () {
        if (contextKey === photoSmartContextKey() && !state.photoSmart.refreshObserverKey) els.photoSmartRefresh.disabled = false;
      });
    });
    els.photoSmartDelete.addEventListener("click", function () {
      var collection = state.photoSmart.collection;
      if (!collection) return;
      window.AIPhotoInfo.actionDialog({kind:"confirm", title:"删除智能合集", message:"删除规则、人工排除与候选缓存？照片文件不会删除。", confirmLabel:"删除"})
        .then(function (ok) {
          if (!ok) return;
          return request("/photo-smart-collections/" + encodeURIComponent(collection.smart_id), {method:"DELETE", body:{
            instance_token:collection.instance_token, expected_revision:Number(collection.revision)
          }}).then(function () { window.location.href = "/?view=photo-collections&kind=smart"; });
        }).catch(function (failure) { showCollectionFailure(failure, els.photoSmartDetailStatus); });
    });
    els.photoSmartReplaceRule.addEventListener("click", function () {
      var collection = state.photoSmart.collection;
      if (!collection) return;
      window.sessionStorage.setItem("photo-smart-replace-target", JSON.stringify({
        smart_id:collection.smart_id, instance_token:collection.instance_token,
        expected_revision:collection.revision, name:collection.name
      }));
      window.location.href = "/search";
    });
    document.getElementById("browse-reload").addEventListener("click", function () {
      loadBrowse(true);
    });
    els.favoritesReload.addEventListener("click", function () { loadBrowse(true); });
    els.favoritesOrder.addEventListener("change", function () {
      state.browse.favoritesOrder = els.favoritesOrder.value === "capture_desc" ? "capture_desc" : "recent";
      loadBrowse(true);
    });
    els.browseShowTimeline.addEventListener("click", function () { showBrowseMode("timeline"); });
    els.browseShowList.addEventListener("click", function () { showBrowseMode("list"); });
    els.browseShowRandom.addEventListener("click", function () { showBrowseMode("random"); });
    els.browseRandomSize.addEventListener("change", function () {
      var size = Math.floor(Number(els.browseRandomSize.value));
      if (!Number.isFinite(size)) size = 300;
      size = Math.max(1, Math.min(1000, size));
      els.browseRandomSize.value = String(size);
      state.browse.random.batchSize = size;
      persistRandomBrowse();
    });
    els.browseRandomNext.addEventListener("click", function () { requestNextRandomBatch(); });
    els.browseSource.addEventListener("change", function () {
      state.browse.sourceId = els.browseSource.value;
      updateSearchScopeAction();
      if (state.browse.mode === "timeline") showBrowseMode("timeline");
      else if (state.browse.mode === "random") {
        resetRandomForSource();
        startRandomBatch(false);
      }
      else loadBrowse(true);
    });
    document.getElementById("clear-browse-source").addEventListener("click", function () {
      state.browse.sourceId = "";
      els.browseSource.value = "";
      updateSearchScopeAction();
      if (state.browse.mode === "timeline") showBrowseMode("timeline");
      else if (state.browse.mode === "random") {
        resetRandomForSource();
        startRandomBatch(false);
      }
      else loadBrowse(true);
    });
    els.browseOrder.addEventListener("change", function () {
      state.browse.order = els.browseOrder.value;
      loadBrowse(true);
    });
    els.browseCaptureFrom.addEventListener("change", function () {
      state.browse.captureFrom = els.browseCaptureFrom.value.trim();
      loadBrowse(true);
    });
    els.browseCaptureTo.addEventListener("change", function () {
      state.browse.captureTo = els.browseCaptureTo.value.trim();
      loadBrowse(true);
    });
    state.browse.pager = window.PhotoScrollPager ? window.PhotoScrollPager.create({
      sentinel: els.gridSentinel,
      button: els.loadMore,
      loadLabel: "加载更多",
      retryLabel: "重试加载",
      canLoad: function () {
        return !els.views.browse.hidden && !els.browseListView.hidden &&
          (state.browse.mode === "list" || state.browse.mode === "random") && !state.detailRouteActive &&
          !(state.detailViewer && state.detailViewer.dialog && state.detailViewer.dialog.open) &&
          document.visibilityState !== "hidden" && !state.browse.loading && !state.browse.random.loading &&
          (state.browse.mode === "random" ? state.browse.random.hasMore :
            (state.browse.hasMore && Boolean(state.browse.cursor)));
      },
      load: function () { return state.browse.mode === "random" ? loadRandomPage(false) : loadBrowse(false); },
    }) : null;
    if (state.browse.pager) state.browse.pager.setEnabled(false);
    els.sourceForm.addEventListener("submit", submitSource);
    els.refreshLibrary.addEventListener("click", function () {
      refreshLibrary(null, "refresh");
    });
    els.buildAllLibrary.addEventListener("click", function () {
      refreshLibrary(null, "build");
    });
    document.getElementById("storage-summary-refresh").addEventListener("click", loadStorageSummary);
    document.getElementById("storage-deep-check").addEventListener("click", runStorageDeepCheck);
    document.getElementById("storage-maintenance-refresh").addEventListener("click", loadStorageMaintenance);
    document.getElementById("storage-maintenance-confirm").addEventListener("click", confirmStorageMaintenance);
    document.querySelectorAll('input[name="storage-maintenance-operation"]').forEach(function (input) { input.addEventListener("change", updateStorageMaintenanceConfirm); });
    document.getElementById("storage-maintenance-cancel").addEventListener("click", cancelStorageMaintenance);
    document.getElementById("storage-backup-save").addEventListener("click", saveStorageBackupDirectory);
    document.getElementById("storage-backup-create").addEventListener("click", createManualStorageBackup);
    document.getElementById("library-retention-refresh").addEventListener("click", function () { loadLibraryRetention(true); });
    els.libraryRetentionCleanup.addEventListener("click", startLibraryRetentionCleanup);
    els.libraryRetentionAutomatic.addEventListener("change", setLibraryRetentionAutomatic);
    els.libraryRetentionAnomalies.addEventListener("click", function (event) {
      var button = event.target.closest('[data-retention-anomaly="resolve"]');
      if (!button || !window.confirm("确认已经检查该来源扫描结果？解除保护不会清理任何数据；之后仍需该来源和其他相关来源重新成功扫描，观察期从重新确认时开始。")) return;
      request("/storage/library-retention/anomalies/" + encodeURIComponent(button.dataset.sourceId) + "/" + encodeURIComponent(button.dataset.scanId) + "/resolve", { method: "POST", body: {} })
        .then(function () { return loadLibraryRetention(true); })
        .catch(function (failure) { setStatus(els.libraryRetentionStatus, "确认来源异常失败：" + describe(failure), "error"); });
    });
    els.libraryRetentionMore.addEventListener("click", function () { loadLibraryRetention(false); });
    document.querySelectorAll("[data-storage-tab]").forEach(function (button) {
      button.addEventListener("click", function () { activateStorageTab(button.dataset.storageTab); });
      button.addEventListener("keydown", function (event) {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        var tabs = Array.prototype.slice.call(document.querySelectorAll("[data-storage-tab]"));
        var index = tabs.indexOf(button);
        var nextIndex = (index + (event.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length;
        tabs[nextIndex].focus();
        activateStorageTab(tabs[nextIndex].dataset.storageTab);
        event.preventDefault();
      });
    });
    window.addEventListener("hashchange", handleHash);

    state.observer = buildObserver();
    handleHash();
    loadSources().then(probeCapabilities);
    pollRefresh();
    window.addEventListener("resize", function () {
      if (state.detailPreviewFit) {
        state.detailPreviewFit();
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
