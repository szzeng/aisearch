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
    sourceDetailOpen: {},
    sourcesRequestVersion: 0,
    detailPreviewFit: null,
    browse: {
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
      // The shared date timeline is primary; keep list mode as a recovery path.
      mode: "timeline",
      timelineMount: null,
      timelineScope: "",
      timelineReturnAnchor: "",
    },
    detailRouteActive: false,
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
          return;
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
        if (state.browse.timelineEmptyParams) showTimelineEmptyMessage(state.browse.timelineEmptyParams);
        els.browseSource.value = state.browse.sourceId;
        els.browseOrder.value = state.browse.order;
        els.browseCaptureFrom.value = state.browse.captureFrom;
        els.browseCaptureTo.value = state.browse.captureTo;
        setStatus(els.sourcesStatus, "");
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
    button.disabled = button.dataset.executionEnabled !== "true" || button.dataset.requestPending === "true" || selectedStorageMaintenanceOperations().length === 0;
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
      var storage = model.overview.storage || {};
      lines.push("SQLite：catalog 文件 " + (storage.sqlite_catalog_file_bytes == null ? "无法准确估计" : formatBytes(storage.sqlite_catalog_file_bytes)) + "；逻辑页 " + (storage.sqlite_logical_page_bytes == null ? "无法准确估计" : formatBytes(storage.sqlite_logical_page_bytes)) + "；可回收量无法准确估计。LanceDB 逻辑行数与物理可回收量无法准确估计。");
    }
    model.items.forEach(function (item) {
      lines.push("asset " + item.asset_id + "：" + item.classification + "；" + item.availability_summary + "；" + item.reason + "；人工/受保护引用：" + (item.has_human_or_protected_references ? "有" : "未发现已知引用") + "；" + item.retention_eligibility);
    });
    if (!model.items.length && model.overview) lines.push("当前页没有失效位置或零位置资产。");
    text(els.libraryRetentionStatus, lines.join("\n"));
    els.libraryRetentionMore.hidden = !model.overview || model.overview.next_cursor == null;
    els.libraryRetentionMore.disabled = state.libraryRetentionLoading === true;
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
        element("li", "hint", "还没有登记图库。展开“图库管理与分析明细”添加一个图库。")
      );
      return;
    }
    state.sources.forEach(function (source) {
      var card = element("li", "source-card");
      var title = element("h3", null, source.display_name);
      title.setAttribute("data-source-title", String(source.source_id));
      card.appendChild(title);
      var identity = "图库 #" + source.source_id;
      if (source.volume_hint) {
        identity += " · " + safeText(source.volume_hint);
      }
      card.appendChild(element("p", "hint", identity));
      card.appendChild(
        element(
          "p",
          "hint",
          "记录状态 " + (SOURCE_STATUS_TEXT[source.status] || source.status || "已登记") +
            " · 未实时检查磁盘"
        )
      );
      var open = element("button", "view-source source-primary", "看照片");
      open.type = "button";
      open.addEventListener("click", function () {
        state.browse.sourceId = String(source.source_id);
        els.browseSource.value = state.browse.sourceId;
        state.browse.loaded = false;
        showView("browse");
      });
      card.appendChild(open);
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
      card.appendChild(summaryActions);
      addSourceRename(card, source, title);

      var detail = element("details", "source-detail");
      detail.open = Boolean(state.sourceDetailOpen[source.source_id]);
      var summary = element("summary", null, "完整路径、状态与管理明细（按需读取）");
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

  function addSourceRename(card, source, title) {
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
    card.appendChild(edit);
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
      var rows = [
        ["来源编号", "#" + source.source_id],
        ["图库记录状态", SOURCE_STATUS_TEXT[source.status] || source.status],
      ];
      if (detail.root_path) {
        rows.push(["完整根路径", detail.root_path]);
      }
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
    if (!els.searchScopeAction || !state.sourcesLoaded || !els.views || els.views.browse.hidden || state.detailRouteActive) {
      if (els.setSearchScopeAction) els.setSearchScopeAction(null, "");
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
      setStatus(els.refreshStatus, "还没有登记图库，不能启动全部建库或同步。", "error");
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
      message = "图库状态读取失败，请在图库管理中重试；当前无法判断照片是否已建库。";
    } else if (!state.sources.length) {
      message = "还没有登记图库。请到“图库管理”添加来源并开始建库。";
    } else if (source && source.last_successful_scan == null) {
      message = "这个图库尚未建库。请到“图库管理”点击“开始建库”；现在还没有可浏览的扫描结果。";
    } else if (params.year || params.month || params.seek_year || params.seek_month) {
      message = "这个年月在当前图库范围内没有匹配照片。可以清除年月筛选或直接定位其他年月。";
    } else if (params.bucket === "unknown") {
      message = "当前图库范围没有“日期未知”的照片。";
    } else if (params.bucket === "pending") {
      message = "当前图库范围没有“日期待就绪”的照片。";
    } else if (params.time_basis === "capture" && params.bucket !== "all") {
      message = "当前没有可信拍摄日期的照片。可打开“日期未知”“日期待就绪”或切换到加入图库时间浏览。";
    } else {
      message = "当前图库范围还没有可浏览的照片。若来源尚未完成扫描，请先在图库管理中建库或同步变化。";
    }
    status.textContent = message;
  }

  function showBrowseMode(mode) {
    var useTimeline = mode === "timeline";
    state.browse.mode = useTimeline ? "timeline" : "list";
    els.browseTimeline.hidden = !useTimeline;
    els.browseListView.hidden = useTimeline;
    els.browseShowTimeline.hidden = useTimeline;
    els.browseShowList.hidden = !useTimeline;
    els.browseShowTimeline.setAttribute("aria-pressed", useTimeline ? "true" : "false");
    els.browseShowList.setAttribute("aria-pressed", useTimeline ? "true" : "false");
    els.browseCaptureState.hidden = useTimeline;
    els.browseListFilters.hidden = useTimeline;
    els.browseListTimeHint.hidden = useTimeline;
    els.browseTimelineNote.hidden = !useTimeline;
    if (!useTimeline) {
      if (!state.browse.loaded || state.browse.binding !== browseFingerprint()) loadBrowse(true);
      return;
    }
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
    if (state.browse.mode === "timeline") return;
    if (reset) {
      state.browse.requestVersion += 1;
      releaseObjectUrls();
      state.browse.cursor = null;
      state.browse.hasMore = false;
      state.browse.count = 0;
      state.browse.timeCache = {};
      setStatus(els.browseCaptureState, "");
      els.assetGrid.textContent = "";
      els.loadMore.hidden = true;
      els.gridSentinel.hidden = true;
    }
    if (state.browse.loading) {
      state.browse.owed = true;
      return;
    }
    if (continuationRefused(reset)) {
      return;
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
    request("/assets?" + params.toString())
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
        if (state.browse.owed) {
          state.browse.owed = false;
          loadBrowse(true);
        }
      })
      .catch(function (failure) {
        state.browse.loading = false;
        if (requestVersion !== state.browse.requestVersion) {
          if (state.browse.owed) {
            state.browse.owed = false;
            loadBrowse(true);
          }
          return;
        }
        els.loadMore.hidden = true;
        els.gridSentinel.hidden = true;
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
        if (state.browse.owed) {
          state.browse.owed = false;
          loadBrowse(true);
        }
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
        "服务端回显（规范化墙上时间，左闭右开）：capture_from=" +
          (from === null ? "null" : safeText(from)) +
          "，capture_to_exclusive=" +
          (to === null ? "null" : safeText(to)) +
          "。"
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
        "图库在这个筛选下还没有图片（" +
        orderText +
        "）。先在“来源与扫描”里注册来源并扫描。"
      );
    }
    return (
      "已加载 " +
      state.browse.count +
      " 张（" +
      orderText +
      "）" +
      (state.browse.hasMore ? "，还有更多。" : "，已经到底。")
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
    var frame = element("button", "thumb-frame");
    frame.type = "button";
    frame.setAttribute("aria-label", "查看照片 " + asset.asset_id);
    frame.addEventListener("click", function () { showDetail(asset.asset_id); });
    frame.dataset.assetId = String(asset.asset_id);
    frame.dataset.relativeLabel = relativeLabel(asset);
    if (asset.availability === "missing") {
      frame.dataset.originalMissing = "true";
    }
    observeThumbnail(frame);
    tile.appendChild(frame);

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
    var meta = element(
      "span",
      "meta",
      "资产 #" +
        asset.asset_id +
        " · 加入时间 " +
        formatMoment(asset.created_at) +
        " · " +
        asset.location_count +
        " 个位置"
    );
    caption.appendChild(meta);
    if (captureConditionActive()) {
      // ADR-0011 §8.1: the list carries the honesty block, not a per-row time projection, so
      // the per-row sentence comes from the closed detail object of that same asset. It is
      // fetched lazily for the tiles the user actually looks at.
      var timeLine = element("span", "tile-time", "");
      timeLine.setAttribute("data-capture-time", String(asset.asset_id));
      caption.appendChild(timeLine);
      paintCaptureTime(asset.asset_id, timeLine);
    }
    var open = element("button", "tile-link", asset.availability === "missing" ? "查看离线详情" : "查看详情");
    open.type = "button";
    open.addEventListener("click", function () {
      showDetail(asset.asset_id);
    });
    caption.appendChild(open);
    tile.appendChild(caption);
    return tile;
  }

  // --------------------------------------------------------------------- detail
  function saveBrowseReturn(assetId) {
    var snapshot = {
      assetId: String(assetId),
      sourceId: state.browse.sourceId,
      order: state.browse.order,
      captureFrom: state.browse.captureFrom,
      captureTo: state.browse.captureTo,
      scrollY: state.browse.returnScrollY,
      returnAssetId: state.browse.returnAssetId,
      returnAssetTop: state.browse.returnAssetTop,
      count: state.browse.count,
      mode: state.browse.mode,
      timelineReturnAnchor: state.browse.timelineReturnAnchor,
    };
    state.browse.returnContext = snapshot;
    try { window.sessionStorage.setItem(BROWSE_RETURN_KEY, JSON.stringify(snapshot)); } catch (_error) {}
  }

  function restoreBrowseReturn(assetId) {
    var snapshot = null;
    try { snapshot = JSON.parse(window.sessionStorage.getItem(BROWSE_RETURN_KEY) || "null"); } catch (_error) {}
    if (!snapshot || String(snapshot.assetId) !== String(assetId)) return false;
    state.browse.sourceId = String(snapshot.sourceId || "");
    state.browse.order = String(snapshot.order || "time_desc");
    state.browse.captureFrom = String(snapshot.captureFrom || "");
    state.browse.captureTo = String(snapshot.captureTo || "");
    state.browse.mode = snapshot.mode === "timeline" ? "timeline" : "list";
    state.browse.timelineReturnAnchor = String(snapshot.timelineReturnAnchor || "");
    state.browse.returnScrollY = Math.max(0, Number(snapshot.scrollY) || 0);
    state.browse.returnAssetId = String(snapshot.returnAssetId || assetId);
    state.browse.returnAssetTop = Number.isFinite(Number(snapshot.returnAssetTop)) ? Number(snapshot.returnAssetTop) : null;
    state.browse.returnTargetCount = Math.max(0, Number(snapshot.count) || 0);
    state.browse.returnContext = snapshot;
    state.browse.returnPending = true;
    state.browse.loaded = false;
    return true;
  }

  function clearBrowseReturn() {
    state.browse.returnContext = null;
    try { window.sessionStorage.removeItem(BROWSE_RETURN_KEY); } catch (_error) {}
  }

  function restoreBrowsePosition() {
    if (!state.browse.returnPending) return;
    if (state.browse.mode === "timeline") {
      state.browse.returnTargetCount = 0;
      state.browse.returnPending = false;
      return;
    }
    if (!state.browse.loaded) return;
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
    state.browse.returnScrollY = window.scrollY || window.pageYOffset || 0;
    var returnTile = (state.browse.mode === "timeline" ? els.browseTimeline : els.assetGrid)
      .querySelector('[data-asset-id="' + String(assetId) + '"]');
    state.browse.returnAssetId = String(assetId);
    state.browse.returnAssetTop = returnTile ? returnTile.getBoundingClientRect().top : null;
    state.detailRouteActive = true;
    saveBrowseReturn(assetId);
    var target = "#/asset/" + assetId;
    if (location.hash === target) {
      renderDetail(assetId);
      return;
    }
    // Changing the hash fires ``hashchange``, which renders the detail view once.
    location.hash = target;
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
    if (mapReturn) {
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
      history.replaceState(null, "", location.pathname + location.search);
    }
  }

  function showView(name) {
    if (name !== "detail" && state.detailInfoMount) {
      state.detailInfoMount.dispose();
      state.detailInfoMount = null;
      state.detailSeq = (state.detailSeq || 0) + 1;
    }
    ["sources", "browse", "detail"].forEach(function (view) {
      var section = els.views[view];
      var active = view === name;
      section.hidden = !active;
    });
    Array.prototype.forEach.call(els.navButtons, function (button) {
      button.setAttribute("aria-current", button.dataset.view === name ? "true" : "false");
    });
    var shellLinks = document.querySelectorAll ? document.querySelectorAll("[data-shell-page]") : [];
    Array.prototype.forEach.call(shellLinks, function (link) {
      var activePage = name === "sources" ? "management" : "photos";
      if (link.dataset.shellPage === activePage) {
        link.setAttribute("aria-current", "page");
      } else {
        link.removeAttribute("aria-current");
      }
    });
    els.main.focus({ preventScroll: true });
    if (name === "browse") showBrowseMode(state.browse.mode);
    updateSearchScopeAction();
  }

  function handleHash() {
    var match = /^#\/asset\/(\d+)$/.exec(String(location.hash || ""));
    if (match) {
      state.detailRouteActive = true;
      if (!state.browse.returnContext) restoreBrowseReturn(match[1]);
      renderDetail(match[1]);
      return;
    }
    if (state.detailRouteActive) {
      state.detailRouteActive = false;
      if (state.browse.returnContext) {
        state.browse.returnPending = true;
        showView("browse");
        restoreBrowsePosition();
        clearBrowseReturn();
        return;
      }
    }
    var params = new URLSearchParams(String(location.search || ""));
    showView(params.get("view") === "sources" ? "sources" : "browse");
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
        clearNotice();
        revealPersonsNav(body);
        var schema =
          body.schema_version === null || body.schema_version === undefined
            ? "尚未初始化"
            : "v" + body.schema_version;
        text(
          els.appMeta,
          "版本 " +
            body.app_version +
            " · 图库 schema " +
            schema +
            "（" +
            body.schema_status +
            "） · 只读受控访问"
        );
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
      libraryRetentionMore: document.getElementById("library-retention-more"),
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
      loadMore: document.getElementById("load-more"),
      gridSentinel: document.getElementById("grid-sentinel"),
      detailBody: document.getElementById("detail-body"),
      navButtons: document.querySelectorAll(".nav-button"),
      views: {
        sources: document.getElementById("view-sources"),
        browse: document.getElementById("view-browse"),
        detail: document.getElementById("view-detail"),
      },
    };

    els.setSearchScopeAction = window.SearchScopeEntry && window.SearchScopeEntry.configure({
      button: els.searchScopeAction,
      input: document.getElementById("global-search-query"),
      getScope: function () {
        if (els.views.browse.hidden || state.detailRouteActive || !state.sourcesLoaded) return null;
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
      var fromSearch = false;
      try {
        fromSearch = window.sessionStorage.getItem(SEARCH_RETURN_SOURCE_KEY) === "search";
        if (fromSearch) {
          window.sessionStorage.removeItem(SEARCH_RETURN_SOURCE_KEY);
        }
      } catch (_error) {
        // If storage is blocked, retain the existing in-page return to gallery behavior.
      }
      if (fromSearch) {
        window.location.href = "/search";
        return;
      }
      clearHash();
      state.detailRouteActive = false;
      showView("browse");
      state.browse.returnPending = true;
      restoreBrowsePosition();
      clearBrowseReturn();
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
    document.getElementById("browse-reload").addEventListener("click", function () {
      loadBrowse(true);
    });
    els.browseShowTimeline.addEventListener("click", function () { showBrowseMode("timeline"); });
    els.browseShowList.addEventListener("click", function () { showBrowseMode("list"); });
    els.browseSource.addEventListener("change", function () {
      state.browse.sourceId = els.browseSource.value;
      updateSearchScopeAction();
      if (state.browse.mode === "timeline") showBrowseMode("timeline");
      else loadBrowse(true);
    });
    document.getElementById("clear-browse-source").addEventListener("click", function () {
      state.browse.sourceId = "";
      els.browseSource.value = "";
      updateSearchScopeAction();
      if (state.browse.mode === "timeline") showBrowseMode("timeline");
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
    els.loadMore.addEventListener("click", function () {
      loadBrowse(false);
    });
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
    els.libraryRetentionMore.addEventListener("click", function () { loadLibraryRetention(false); });
    document.getElementById("library-retention-details").addEventListener("toggle", function (event) {
      if (event.currentTarget.open && !state.libraryRetention.overview) loadLibraryRetention(true);
    });
    document.getElementById("storage-management-details").addEventListener("toggle", function (event) {
      if (event.currentTarget.open && !state.storageBackupLoaded) {
        state.storageBackupLoaded = true;
        loadStorageBackup();
      }
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
