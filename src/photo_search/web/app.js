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
    },
    pollTimer: null,
    //: P4-02Y: the polling timer of the one-click library refresh (one at a time).
    refreshTimer: null,
    // One active run; other source builds can wait in its queue.
    refreshRunning: false,
    refreshStatus: {},
    objectUrls: [],
    observer: null,
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
        setStatus(els.sourcesStatus, "");
      })
      .catch(function (failure) {
        if (requestVersion !== state.sourcesRequestVersion) {
          return;
        }
        setStatus(els.sourcesStatus, "读取来源失败：" + describe(failure), "error");
      });
  }

  function renderSources() {
    els.sourcesList.textContent = "";
    if (!state.sources.length) {
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
      var open = element("button", "view-source", "查看此图库照片");
      open.type = "button";
      open.addEventListener("click", function () {
        state.browse.sourceId = String(source.source_id);
        els.browseSource.value = state.browse.sourceId;
        state.browse.loaded = false;
        showView("browse");
      });
      card.appendChild(open);
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
          rows.push([pair[1], STAGE_STATE_TEXT[stage.state] || String(stage.state)]);
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
      var mode = sourceActionMode(progress);
      var actions = element("p");
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
        var buildScan = element("button", null, "完整校验扫描");
        buildScan.type = "button";
        buildScan.setAttribute("data-scan", String(source.source_id));
        buildScan.addEventListener("click", function () {
          startScan(source.source_id);
        });
        actions.appendChild(buildScan);
      } else {
        // §4: once a source is built the daily action is a fast source-scoped refresh; the
        // complete-verification scan stays as the secondary maintenance entry.
        var refreshOne = element("button", null, "快速刷新此来源");
        refreshOne.type = "button";
        refreshOne.setAttribute("data-refresh", String(source.source_id));
        refreshOne.addEventListener("click", function () {
          refreshLibrary([Number(source.source_id)], "refresh");
        });
        actions.appendChild(refreshOne);
        var scan = element("button", null, "完整校验扫描");
        scan.type = "button";
        scan.setAttribute("data-scan", String(source.source_id));
        scan.addEventListener("click", function () {
          startScan(source.source_id);
        });
        actions.appendChild(scan);
      }
      var remove = element("button", "danger", "删除来源");
      remove.type = "button";
      remove.setAttribute("data-delete", String(source.source_id));
      remove.addEventListener("click", function () {
        deleteSource(source);
      });
      actions.appendChild(remove);
      updateSourceActions(actions);
      container.appendChild(actions);
      var progressLine = element("p", "hint", "");
      progressLine.setAttribute("data-progress", String(source.source_id));
      progressLine.setAttribute("role", "status");
      progressLine.setAttribute("aria-live", "polite");
      container.appendChild(progressLine);
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
    var timer = state.refreshTimer;
    if (timer !== null) {
      clearTimeout(timer);
      state.refreshTimer = null;
    }
    var requested = mode === "build" ? "build" : "refresh";
    setStatus(els.refreshStatus, "正在请求" + REFRESH_MODE_TEXT[requested] + "…");
    var body = { mode: requested };
    if (sourceIds) {
      body.source_ids = sourceIds;
    }
    request("/library/refresh", { method: "POST", body: body })
      .then(function (result) {
        renderRefresh(result.body || {});
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
    var failedStages = status.failed_stages || [];
    var unfinished = failedStages.indexOf("semantic-backlog") >= 0;
    var refusedAll = failedStages.indexOf("semantic-confirmed-nothing") >= 0;
    var stateText = unfinished
      ? "尚未完成建立语义索引（可再次点击继续）"
      : (refusedAll
        ? "没有图片建立语义索引：这些图片都无法编码（可修好文件后再刷新）"
        : (REFRESH_STATE_TEXT[status.state] || String(status.state || "")));
    var parts = [];
    if (status.mode === "build") {
      parts.push("开始建库");
    }
    parts.push(stateText);
    state.refreshRunning = Boolean(status.running);
    state.refreshStatus = status;
    updateSourceActions(els.sourcesList);
    els.refreshLibrary.disabled = state.refreshRunning;
    if (status.queued) {
      parts.push("已加入建库队列，当前任务结束后自动开始");
    }
    if ((status.pending_builds || []).length) {
      parts.push("等待建库 " + status.pending_builds.length + " 项");
    }
    var semantic = status.stages && status.stages.semantic ? status.stages.semantic : null;
    if (semantic && semantic.backlog_total !== undefined) {
      parts.push(
        "语义索引 " + String(semantic.backlog_confirmed) + " / " + String(semantic.backlog_total) +
        "（待处理 " + String(semantic.backlog_pending) + "）"
      );
    }
    if (counters.hash_targets !== undefined) {
      parts.push(
        "已重新校验 " + String(counters.hash_targets) + " 个位置" +
        "（复用 " + String(counters.unchanged_reused || 0) + " 个未变位置）"
      );
    }
    if (counters.content_delta_count !== undefined) {
      parts.push("新增/变化内容 " + String(counters.content_delta_count) + " 项");
    }
    if (!status.search_ready && status.search_ready_basis) {
      var gate = String(status.search_ready_basis);
      parts.push("原因：" + (SEARCH_GATE_TEXT[gate] || gate));
    }
    var kind = status.state === "FAILED"
      ? "error"
      : (status.state === "PARTIAL_FAILED" ? "error" : "");
    setStatus(els.refreshStatus, parts.join("；"), kind);
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

  function loadBrowse(reset) {
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
    var frame = element("div", "thumb-frame");
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
  function showDetail(assetId) {
    state.browse.returnScrollY = window.scrollY || window.pageYOffset || 0;
    var returnTile = els.assetGrid.querySelector('[data-asset-id="' + String(assetId) + '"]');
    state.browse.returnAssetId = String(assetId);
    state.browse.returnAssetTop = returnTile ? returnTile.getBoundingClientRect().top : null;
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
    els.detailBody.textContent = "";
    els.detailBody.appendChild(element("p", "state-block", "正在读取图片详情…"));
    request("/assets/" + encodeURIComponent(assetId))
      .then(function (result) {
        drawDetail(result.body || {}, assetId);
      })
      .catch(function (failure) {
        els.detailBody.textContent = "";
        els.detailBody.appendChild(
          element("p", "error", "读取图片详情失败：" + describe(failure))
        );
      });
  }

  function drawDetail(body, assetId) {
    els.detailBody.textContent = "";
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
    els.detailBody.appendChild(figure);

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
    els.detailBody.appendChild(facts);

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
    els.detailBody.appendChild(capture);

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
    els.detailBody.appendChild(locations);

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
    els.detailBody.appendChild(media);
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
    ["sources", "browse", "detail"].forEach(function (view) {
      var section = els.views[view];
      var active = view === name;
      section.hidden = !active;
    });
    Array.prototype.forEach.call(els.navButtons, function (button) {
      button.setAttribute("aria-current", button.dataset.view === name ? "true" : "false");
    });
    els.main.focus();
    if (name === "browse" && !state.browse.loaded) {
      loadBrowse(true);
    }
  }

  function handleHash() {
    var match = /^#\/asset\/(\d+)$/.exec(String(location.hash || ""));
    if (match) {
      renderDetail(match[1]);
      return;
    }
    showView("sources");
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
      notice: document.getElementById("notice"),
      main: document.getElementById("main"),
      sourcesList: document.getElementById("sources-list"),
      sourcesStatus: document.getElementById("sources-status"),
      refreshLibrary: document.getElementById("refresh-library"),
      refreshStatus: document.getElementById("refresh-status"),
      sourceForm: document.getElementById("source-form"),
      sourceFormError: document.getElementById("source-form-error"),
      sourceName: document.getElementById("source-name"),
      sourceRoot: document.getElementById("source-root"),
      browseStatus: document.getElementById("browse-status"),
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

    els.navButtons.forEach(function (button) {
      button.addEventListener("click", function () {
        clearHash();
        showView(button.dataset.view);
      });
    });
    document.getElementById("detail-back").addEventListener("click", function () {
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
      showView("browse");
      window.requestAnimationFrame(function () {
        var scrollY = state.browse.returnScrollY;
        var returnTile = els.assetGrid.querySelector(
          '[data-asset-id="' + state.browse.returnAssetId + '"]'
        );
        if (returnTile && state.browse.returnAssetTop !== null) {
          scrollY += returnTile.getBoundingClientRect().top - state.browse.returnAssetTop;
        }
        window.scrollTo(0, Math.max(0, scrollY));
      });
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
    els.browseSource.addEventListener("change", function () {
      state.browse.sourceId = els.browseSource.value;
      loadBrowse(true);
    });
    document.getElementById("clear-browse-source").addEventListener("click", function () {
      state.browse.sourceId = "";
      els.browseSource.value = "";
      loadBrowse(true);
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
      refreshLibrary(null);
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
