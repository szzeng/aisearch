/* Search page for the read-only photo-search API.
 *
 * Ground rules this file keeps (P2-07):
 *  - every piece of data comes from the session-protected /api/v1 endpoints;
 *  - the CSRF token is read from the ps_csrf cookie and sent in X-CSRF-Token only, and the
 *    uploaded file is only ever sent to the same origin;
 *  - the page never renders a local absolute path, a coordinate, a session token or a query
 *    vector; server-supplied text is scrubbed before it is shown;
 *  - a non-success state is stated explicitly instead of being downgraded into "no result":
 *    a missing query/image encoder, an expired session or cursor, a lagging semantic index
 *    and an incomplete vector scan gets an explicit warning;
 *  - hit reasons and coverage numbers stay separate facts: no distance, BM25 score or
 *    reranker score is ever turned into an "accuracy" or a percentage here;
 *  - nothing is loaded from the network except this same origin.
 */
(function () {
  "use strict";

  var API = "/api/v1";
  var CONTRACT_VERSION = "p2-contracts.v1";
  var MIN_PAGE_SIZE = 1;
  var MAX_PAGE_SIZE = 100;
  var DEFAULT_PAGE_SIZE = 20;
  // Keep aligned with search.retrieval.DEFAULT_TOP_K; legacy similar-search
  // requests omitted this field and rely on the API's same default.
  var DEFAULT_SIMILAR_TOP_K = 5000;
  var SEARCH_SNAPSHOT_KEY = "aisearch.search-return.v1";
  var SEARCH_RETURN_SOURCE_KEY = "aisearch.search-return-source.v1";
  var backdropBoundDialogs = new WeakSet();
  //: One page of the person picker. The page never materialises the whole person list: the
  //: server's own keyset cursor drives "加载更多人物", so a large catalog stays a paged read.
  var PERSON_LIMIT = 25;

  var HIT_REASON_TEXT = {
    TEXT: "文字匹配",
    SEMANTIC: "图文向量命中",
    "TEXT+SEMANTIC": "文本 + 图文向量命中",
    FILTER_ONLY: "仅筛选（本次没有查询文本，两条通道都没有参与排序）",
  };
  var TEXT_FIELD_LABELS = {
    filename_text: "文件名", place_text: "地点名称", ocr_text: "图片文字",
    user_text: "备注", tag_text: "标签", caption_text: "图片描述",
  };
  var PROJECTION_STATE_TEXT = { SYNCED: "已同步", LAGGING: "仍在追赶" };
  //: P4-00B: the honest reading of the capture-time block (ADR-0011 §8.1). A capture search
  //: that returns nothing must say *why*: "the extraction has not run yet" is a different
  //: fact from "nothing matches".
  var CAPTURE_STATE_TEXT = {
    PENDING: "拍摄时间尚未抽取：现在的时间条件命中为空，并不代表没有这些照片。",
    PARTIAL: "拍摄时间还在抽取中：时间条件的命中可能不完整。",
    READY: "拍摄时间已抽取完成。",
    NO_CAPTURE_TIME: "图库里没有任何可用的拍摄时间（只有文件时间），时间条件不会命中任何照片。",
  };
  var FAILURE_HINT = {
    SEARCH_SESSION_EXPIRED:
      "这个搜索会话已经过期（进程重启或超过有效期）；查询条件已保留，请重新搜索。",
    INVALID_CURSOR: "分页游标与这次查询不匹配（可能被改动过，或查询条件已改）；请重新搜索。",
    PROFILE_GENERATION_MISMATCH:
      "搜索配置或索引世代在翻页期间发生了变化；旧会话不再有效，请重新搜索。",
    CATALOG_FILTER_REVISION_STALE:
      "人物、地点或可见性在翻页期间发生了变化（它们共用同一个权威筛选 revision）；" +
      "旧会话不再有效，请重新搜索。",
    PLACE_SUGGESTION_REVISION_STALE:
      "可见地点建议在翻页期间发生了变化，旧会话不再有效；请重新搜索。",
    UPLOAD_TOO_LARGE: "上传的图片超过了服务端允许的字节上限。",
    UPLOAD_TOO_MANY_PIXELS: "上传的图片超过了服务端允许的像素上限。",
    FORMAT_UNSUPPORTED: "这个图片格式本构建还不解码（与 P1-03 的格式白名单一致）。",
    DECODE_FAILED: "上传的图片无法解码（可能已损坏，或解码超过了时间预算）。",
    VALIDATION_ERROR: "请求参数不被接受，请检查上面的筛选条件。",
    SESSION_REQUIRED: "本地会话已失效。",
    INVALID_PERSON_FILTER:
      "人物条件不被接受（例如选了「全部」却没有选中任何人物，或人物数量超过 100）；请检查上面的选择后重搜。",
    INVALID_TIME_RANGE:
      "拍摄时间范围不被接受：只支持 YYYY-MM-DD 或 YYYY-MM-DDTHH:MM:SS[.毫秒] 的墙上时间写法，" +
      "不接受时区后缀（Z 或 ±HH:MM），且起始必须早于结束（左闭右开，空区间会被拒绝）。请修正上面的输入后重搜。",
    PERSON_NOT_FOUND: "所选人物已经不存在；请按服务器的回显重新载入人物并重选。",
    PERSON_REDIRECT_CYCLE: "人物重定向链异常（成环或悬挂）；本次查询已被整请求拒绝，请重新载入人物。",
    SCHEMA_TOO_NEW: "图库的 schema 版本高于本程序支持的版本；请升级程序或改用匹配的数据目录。",
    SCHEMA_INCONSISTENT: "图库 schema 状态不一致，服务端拒绝发布分页；请检查数据目录。",
  };
  var SESSION_HINT = "请使用启动器打印的一次性链接建立会话。";
  //: A drive-absolute or UNC spelling inside server-side text is never rendered.
  var PATH_LIKE = /(?:[A-Za-z]:[\\/])|(?:\\\\[^\s'"])/;
  var PATH_NOTE = "错误详情包含本机路径，已省略。";

  var state = {
    sessionMissing: false,
    sources: [],
    capabilities: null,
    search: {
      sessionId: null,
      cursor: null,
      count: 0,
      total: null,
      loading: false,
      mode: "text",
      assetQueryId: null,
      assetQuery: null,
      imageFile: null,
      pendingImageFile: null,
      //: The condition digest this session is bound to (null while no session is open).
      binding: null,
      placeAware: null,
      intentPlan: null,
      selectedScope: null,
      favoritesOnly: false,
      requestGeneration: 0,
      hits: [],
      windowOffset: 0,
      recycledCount: 0,
      pager: null,
      lastBody: null,
      confirmedSearch: null,
      saveReconfirmRequired: false,
      lastRequestBody: null,
      timeBrowse: { active: false, cursor: null, retryCursor: null, previous: null, rulesVersion: null, requestSeq: 0, total:null, truncated:false },
      preview: { assetId: null, listVersion: 0, seq: 0, moving: false, handle: null },
    },
    requestCounter: 0,
    coverageLoaded: false,
    coverageLoading: false,
    objectUrls: [],
    observer: null,
    person: {
      //: person_id -> the row the server last sent for it (the source of an option label).
      rows: {},
      //: person_id -> the current selection. Keyed by the *id*, so two people who share a
      //: name stay two entries and paging or redrawing can never drop a chosen id.
      selected: {},
      cursor: null,
      hasMore: false,
      loading: false,
      enabled: false,
      reason: "",
    },
  };
  var perfDiag = { enabled: false, runId: null, samples: [], active: null, model: null, count: 0,
    autoRunning: false, autoStop: false, nextSampleType: null, identitySeq: 0,
    identityChecking: false, autoConditions: null, autoSemantic: null, runSamplesStart: 0 };
  var smartReplaceTarget = null;

  var els = {};
  var photoSelection = null;

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

  function clear(node) {
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
    return node;
  }

  function closeOnBackdrop(dialog) {
    if (!dialog || backdropBoundDialogs.has(dialog)) return;
    backdropBoundDialogs.add(dialog);
    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) dialog.close();
    });
  }

  function number(value) {
    var parsed = Number(value);
    return isFinite(parsed) ? parsed : 0;
  }

  function describe(failure) {
    var base = "[" + failure.code + "] " + failure.message;
    return failure.request_id ? base + "（请求编号 " + failure.request_id + "）" : base;
  }

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
    if (settings.perfDiag && perfDiag.enabled) headers["X-Search-Perf"] = "1";
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
    if (settings.form !== undefined) {
      // The browser owns the multipart boundary; the page never sets Content-Type by hand.
      init.body = settings.form;
    } else if (settings.body !== undefined) {
      init.body = JSON.stringify(settings.body);
    }
    var requestStarted = settings.perfDiag ? performance.now() : 0;
    if (settings.perfDiag && perfDiag.active) {
      perfDiag.active.request_started = requestStarted;
      perfDiag.active.request_ms = requestStarted - perfDiag.active.click_started;
    }
    return fetch(API + path, init).then(function (response) {
      var status = response.status;
      var contentType = response.headers.get("Content-Type") || "";
      if (status === 204 || contentType.indexOf("application/json") < 0) {
        if (response.ok) {
            return { status: status, body: null, perf: settings.perfDiag ? {
              requestToResponseMs: performance.now() - requestStarted,
              serverTiming: response.headers.get("Server-Timing") || "",
              perfMeta: response.headers.get("X-Search-Perf-Meta") || "",
            } : null };
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
            return { status: status, body: payload, perf: settings.perfDiag ? {
              requestToResponseMs: performance.now() - requestStarted,
              serverTiming: response.headers.get("Server-Timing") || "",
              perfMeta: response.headers.get("X-Search-Perf-Meta") || "",
            } : null };
          }
          var failure = failureFrom(response, payload);
          if (status === 401) {
            noteSessionMissing();
          }
          throw failure;
        });
    });
  }

  function perfIdentityFromResponse(body) {
    if (!body || body.identity_confirmed !== true || body.binding_consistent !== true ||
        body.generation_status !== "READY" || !body.active_profile_id ||
        !body.active_image_generation_id || !body.provider_id || perfProviderName(body.provider_id) === "模型未确认" || !body.processor_id ||
        !body.space_id || typeof body.dimension !== "number" || !Number.isFinite(body.dimension)) return null;
    return { active_profile_id: body.active_profile_id,
      active_image_generation_id: body.active_image_generation_id,
      provider_id: body.provider_id, processor_id: body.processor_id,
      space_id: body.space_id, dimension: body.dimension,
      encoder_identity: body.provider_id };
  }

  function perfIdentityReason(body) {
    var messages = { NO_ACTIVE_PROFILE: "没有活动搜索 Profile；请先在模型管理中激活模型。",
      ACTIVE_PROFILE_POINTER_MISSING: "图库缺少活动 Profile 指针。",
      ACTIVE_PROFILE_NOT_FOUND: "活动 Profile 指向不存在的配置。",
      ACTIVE_GENERATION_NOT_FOUND: "活动 Profile 绑定的 Generation 不存在。",
      PROFILE_GENERATION_MISMATCH: "活动 Profile 与 Generation 的空间或处理器绑定不一致。",
      GENERATION_NOT_READY: "活动 Profile 对应的 Generation 尚未就绪。",
      PROFILE_SCHEMA_UNAVAILABLE: "当前图库没有可读取的搜索 Profile / Generation 结构。",
      PROVIDER_ID_UNAVAILABLE: "无法从活动 Generation 确认模型提供方。",
      CATALOG_UNAVAILABLE: "当前图库数据库不可用。" };
    var reason = body && body.reason || "IDENTITY_UNCONFIRMED";
    if (reason === "GENERATION_NOT_READY" && body && body.generation_status) {
      return messages[reason] + " 当前状态：" + body.generation_status + "。";
    }
    return messages[reason] || "模型身份未确认（" + reason + "）。";
  }

  function perfRefreshIdentity() {
    if (!perfDiag.enabled || perfDiag.identityChecking) return Promise.resolve(null);
    var sequence = ++perfDiag.identitySeq;
    var target = document.getElementById("search-perf-model");
    var retry = document.getElementById("search-perf-identity-retry");
    var start = document.getElementById("search-perf-start");
    perfDiag.identityChecking = true;
    perfDiag.model = null;
    start.disabled = true;
    retry.hidden = true;
    target.textContent = "正在确认当前模型…";
    return request("/search-perf/identity").then(function (result) {
      if (!perfDiag.enabled || sequence !== perfDiag.identitySeq) return null;
      var body = result.body || {};
      var identity = perfIdentityFromResponse(body);
      if (!identity) {
        target.textContent = perfIdentityReason(body);
        retry.hidden = false;
        return null;
      }
      perfDiag.model = identity;
      target.textContent = "当前模型：" + perfProviderName(identity.provider_id) +
        " · Generation " + identity.generation_status;
      return identity;
    }).catch(function (failure) {
      if (!perfDiag.enabled || sequence !== perfDiag.identitySeq) return null;
      perfDiag.model = null;
      target.textContent = "读取活动模型身份失败（" + String(failure && failure.code || "IDENTITY_READ_FAILED") + "）。";
      retry.hidden = false;
      return null;
    }).then(function (identity) {
      if (sequence === perfDiag.identitySeq) {
        perfDiag.identityChecking = false;
        start.disabled = !perfDiag.enabled || !identity || perfDiag.autoRunning;
      }
      return identity;
    });
  }

  function perfProviderName(provider) {
    return ({ "chinese-clip-vit-b-16": "Chinese-CLIP",
      "chinese-clip-vit-b-16-exif-v2": "Chinese-CLIP EXIF v2",
      "qwen3-vl-embedding-2b": "Qwen3-VL-Embedding-2B",
      "qwen3-vl-embedding-2b-exif-v2": "Qwen3-VL-Embedding-2B EXIF v2" })[provider] || "模型未确认";
  }

  function perfUpdateSummary() {
    var summary = document.getElementById("search-perf-summary");
    if (!summary) return;
    var latest = perfDiag.samples.length ? perfDiag.samples[perfDiag.samples.length - 1] : null;
    var api = latest && latest.server && latest.server.search_execution_ms;
    var first = latest && latest.click_to_first_image_decode_ready_ms;
    var ready = latest && latest.click_to_viewport_ready_ms;
    summary.textContent = "已记录 " + perfDiag.samples.length + " 次；最近后端搜索执行 " +
      (api == null ? "未测" : api.toFixed(1) + " ms") + "；首张图片解码就绪 " +
      (first == null ? "未测" : first.toFixed(1) + " ms") + "；视口就绪 " +
      (ready == null ? "未测" : ready.toFixed(1) + " ms") +
      (latest && latest.valid === false ? "；最近样本无效：" + (latest.invalid_reason || "绑定未确认") : "");
  }

  function perfParseServerTiming(header) {
    var metrics = {};
    String(header || "").split(",").forEach(function (part) {
      var match = part.trim().match(/^([a-z0-9_]+);dur=([0-9.]+)/i);
      if (match) metrics[match[1]] = Number(match[2]);
    });
    return metrics;
  }

  function perfBegin(requestId, pageSize) {
    if (!perfDiag.enabled) return null;
    if (perfDiag.active) {
      var previous = perfDiag.active;
      if (previous.viewport_deadline) window.clearTimeout(previous.viewport_deadline);
      previous.valid = false; previous.sample_type = "interrupted"; previous.invalid_reason = "SUPERSEDED_BY_NEW_SEARCH";
      perfDiag.samples.push(previous);
    }
    var sample = { run_id: perfDiag.runId, request_id: requestId,
      started_at: new Date().toISOString(), page_size: pageSize,
      retrieval_mode: textRetrievalMode(),
      search_scope: state.search.selectedScope && state.search.selectedScope.kind || "all",
      sample_type: perfDiag.nextSampleType || (perfDiag.count === 0 ? "first_observed" : "manual"),
      test_mode: perfDiag.autoRunning ? "standard_auto" : "manual",
      valid: true, continuation: false, click_started: performance.now(),
      thumbnail_success: 0, thumbnail_failed: 0, thumbnail_timeout: 0,
      viewport_asset_ids: [], settled_asset_ids: [], server: {},
      model_before: perfDiag.model ? Object.assign({}, perfDiag.model) : null };
    perfDiag.nextSampleType = null;
    perfDiag.active = sample;
    perfDiag.count += 1;
    return sample;
  }

  function perfRecordFailure(requestId, failure) {
    var sample = perfDiag.active;
    if (!sample || sample.request_id !== requestId) return;
    sample.sample_type = "failure";
    sample.valid = false;
    sample.invalid_reason = String((failure || {}).code || "SEARCH_FAILED");
    sample.click_to_request_ms = sample.request_ms || 0;
    sample.request_to_response_ms = sample.request_started ? performance.now() - sample.request_started : null;
    perfDiag.samples.push(sample); perfDiag.active = null; perfUpdateSummary();
  }

  function perfResponse(requestId, result) {
    var sample = perfDiag.active;
    if (!sample || sample.request_id !== requestId) return;
    var perf = result.perf || {};
    sample.request_to_response_ms = perf.requestToResponseMs;
    sample.click_to_request_ms = sample.request_ms || 0;
    sample.server = perfParseServerTiming(perf.serverTiming);
    try { sample.server_meta = JSON.parse(perf.perfMeta || "{}"); } catch (_error) { sample.server_meta = {}; }
    sample.model_identity = sample.server_meta.model_identity || null;
    sample.search_execution_ms = sample.server.search_execution_ms;
    if (!result.body || !result.body.search_session_id) {
      sample.valid = false; sample.invalid_reason = "SEARCH_SESSION_NOT_CREATED";
      perfDiag.samples.push(sample); perfDiag.active = null; perfUpdateSummary(); return;
    }
    var before = sample.model_before, actual = sample.model_identity;
    if (!actual || !before || before.provider_id !== actual.provider_id ||
        before.processor_id !== actual.processor_id ||
        before.active_profile_id !== actual.active_profile_id || before.space_id !== actual.space_id || before.dimension !== actual.dimension ||
        before.active_image_generation_id !== actual.active_image_generation_id ||
        (before.processor_id && before.processor_id !== (sample.server_meta.encoder_identity || {}).processor_id) ||
        sample.server_meta.model_binding_mismatch === true) {
      sample.valid = false; sample.invalid_reason = "MODEL_BINDING_MISMATCH";
    }
    sample.top_k = sample.server_meta.top_k || null;
    sample.scanned_assets = sample.server_meta.scanned_assets;
    sample.vector_blocks = sample.server_meta.vector_blocks;
    sample.returned_candidates = sample.server_meta.returned_candidates;
    sample.first_page_hits = sample.server_meta.first_page_hits;
    sample.worker_pid = sample.server_meta.worker_pid == null ? null : sample.server_meta.worker_pid;
    sample.worker_started_for_request = sample.server_meta.worker_started_for_request == null
      ? null : sample.server_meta.worker_started_for_request;
    sample.worker_cold = sample.server_meta.worker_cold_observed === true ? true : "not_measured";
    sample.model_binding_mismatch = sample.server_meta.model_binding_mismatch;
    sample.encoder_identity = sample.server_meta.encoder_identity || null;
    sample.search_publish_attempts = sample.server_meta.search_publish_attempts || null;
    sample.background_task_wait = "not_measured";
  }

  function perfAfterRender(requestId, body) {
    var sample = perfDiag.active;
    if (!sample || sample.request_id !== requestId) return;
    if (perfDiag.autoRunning) {
      var semantic = perfSemanticSnapshot();
      if (perfDiag.autoSemantic === null) perfDiag.autoSemantic = semantic;
      else if (perfDiag.autoSemantic !== semantic) {
        stopPerfTest("服务端解析出的搜索条件发生变化，标准测试已停止");
        return;
      }
    }
    sample.click_to_results_dom_ms = performance.now() - sample.click_started;
    var viewportHeight = window.visualViewport ? window.visualViewport.height : window.innerHeight;
    var frames = Array.prototype.slice.call(document.querySelectorAll("#result-grid .thumb-frame")).filter(function (frame) {
      var rect = frame.getBoundingClientRect(); return rect.bottom > 0 && rect.top < viewportHeight && rect.right > 0 && rect.left < window.innerWidth;
    });
    sample.viewport_asset_ids = frames.map(function (frame) { return frame.dataset.assetId; });
    if (!frames.length) {
      if ((body.hits || []).length) {
        sample.valid = false; sample.invalid_reason = "NO_VISIBLE_THUMBNAILS";
      }
      perfFinish(sample); return;
    }
    sample.viewport_deadline = window.setTimeout(function () {
      if (perfDiag.active !== sample) return;
      frames.forEach(function (frame) {
        var id = frame.dataset.assetId;
        if (sample.settled_asset_ids.indexOf(id) >= 0) return;
        sample.thumbnail_timeout += 1; sample.settled_asset_ids.push(id);
      });
      perfMaybeFinish(sample);
    }, 10000);
    window.requestAnimationFrame(function () { window.requestAnimationFrame(function () {
      if (perfDiag.active !== sample) return;
      sample.click_to_results_dom_ms = performance.now() - sample.click_started;
      frames.forEach(function (frame) {
        if (frame.dataset.perfRequestId !== sample.request_id) return;
        if (frame.dataset.perfImageState === "ready") perfThumb(sample.request_id, frame.dataset.assetId, true);
        else if (frame.dataset.perfImageState === "failed" || frame.dataset.state === "failed") perfThumb(sample.request_id, frame.dataset.assetId, false);
      });
      perfUpdateSummary();
    }); });
  }

  function perfThumb(requestId, assetId, ok) {
    var sample = perfDiag.active;
    if (!sample || sample.request_id !== requestId || sample.viewport_asset_ids.indexOf(String(assetId)) < 0 || sample.settled_asset_ids.indexOf(String(assetId)) >= 0) return;
    sample.settled_asset_ids.push(String(assetId));
    if (ok) {
      sample.thumbnail_success += 1;
      if (sample.click_to_first_image_decode_ready_ms == null) {
        sample.click_to_first_image_decode_ready_ms = performance.now() - sample.click_started;
      }
    } else sample.thumbnail_failed += 1;
    perfMaybeFinish(sample);
  }

  function perfMaybeFinish(sample) {
    if (sample.settled_asset_ids.length < sample.viewport_asset_ids.length) return;
    window.clearTimeout(sample.viewport_deadline);
    if (sample.thumbnail_timeout || sample.thumbnail_failed) {
      sample.valid = false; sample.invalid_reason = sample.thumbnail_timeout ? "THUMBNAIL_TIMEOUT" : "THUMBNAIL_FAILED";
    }
    sample.click_to_viewport_ready_ms = (sample.thumbnail_timeout || sample.thumbnail_failed)
      ? null : performance.now() - sample.click_started;
    perfFinish(sample);
  }

  function perfFinish(sample) {
    if (perfDiag.active !== sample) return;
    sample.thumbnail_timeout = sample.thumbnail_timeout || 0;
    sample.environment = { app_version: state.capabilities && state.capabilities.app_version || null,
      catalog_assets: null, valid_vector_assets: null, online_photos: null, offline_photos: null,
      gpu: null, runtime_device_config: sample.server_meta.encoder_identity || null,
      actual_runtime_device: "not_measured", background_tasks: "not_measured",
      thumbnail_cache: "unknown" };
    perfDiag.samples.push(sample); perfDiag.active = null; perfUpdateSummary();
    if (perfDiag.autoRunning) updatePerfTestSummary();
  }

  function perfSampleMatchesCurrentIdentity(sample) {
    var actual = sample && sample.model_identity, expected = perfDiag.model;
    return Boolean(actual && expected && actual.active_profile_id === expected.active_profile_id &&
      actual.active_image_generation_id === expected.active_image_generation_id &&
      actual.provider_id === expected.provider_id && actual.processor_id === expected.processor_id &&
      actual.space_id === expected.space_id && actual.dimension === expected.dimension);
  }

  function perfCurrentRunSamples() {
    return perfDiag.samples.slice(perfDiag.runSamplesStart || 0);
  }

  function perfPercentile(samples, key, p) {
    var values = samples.map(function (sample) {
      var value = sample && sample[key];
      if (typeof value !== "number" && sample && sample.server) value = sample.server[key];
      return typeof value === "number" && Number.isFinite(value) ? value : null;
    })
      .filter(function (value) { return typeof value === "number" && Number.isFinite(value); })
      .sort(function (a, b) { return a - b; });
    if (!values.length) return { value: null, n: 0 };
    return { value: values[Math.max(0, Math.ceil(p * values.length) - 1)], n: values.length };
  }

  function perfExport() {
    var validHot = perfCurrentRunSamples().filter(function (sample) {
      return sample.valid && sample.test_mode === "standard_auto" && sample.sample_type === "hot" &&
        perfSampleMatchesCurrentIdentity(sample);
    });
    var report = { format: "photo-search-perf", format_version: 1, run_id: perfDiag.runId,
      generated_at: new Date().toISOString(), model_identity: perfDiag.model,
      environment: { app_version: state.capabilities && state.capabilities.app_version || null,
        catalog_assets: null, valid_vector_assets: null, online_photos: null, offline_photos: null,
        gpu: null, actual_runtime_device: "not_measured", thumbnail_cache: "unknown",
        background_tasks: "not_measured" },
      samples: perfDiag.samples.map(function (sample) {
        var copy = Object.assign({}, sample); delete copy.click_started; delete copy.request_started;
        delete copy.viewport_deadline; delete copy.settled_asset_ids; delete copy.viewport_asset_ids;
        delete copy.model_before; return copy;
      }),
      hot_search_execution_ms: { p50: perfPercentile(validHot, "search_execution_ms", .5), p95: perfPercentile(validHot, "search_execution_ms", .95) },
      request_to_response_ms: { p50: perfPercentile(validHot, "request_to_response_ms", .5), p95: perfPercentile(validHot, "request_to_response_ms", .95) },
      query_encode_ms: { p50: perfPercentile(validHot, "query_encode_ms", .5), n: perfPercentile(validHot, "query_encode_ms", .5).n },
      vector_ms: { p50: perfPercentile(validHot, "vector_ms", .5), n: perfPercentile(validHot, "vector_ms", .5).n },
      first_image_decode_ready_ms: { p50: perfPercentile(validHot, "click_to_first_image_decode_ready_ms", .5), p95: perfPercentile(validHot, "click_to_first_image_decode_ready_ms", .95) },
      viewport_ready_ms: { p50: perfPercentile(validHot, "click_to_viewport_ready_ms", .5), p95: perfPercentile(validHot, "click_to_viewport_ready_ms", .95) },
      p95_reliability: validHot.length < 20 ? "LOW_SAMPLE_COUNT" : "SAMPLE_COUNT_RECORDED",
      valid_hot_samples: validHot.length,
      metric_definitions: { search_execution_ms: "后端搜索执行时间，不含完整 HTTP 响应序列化",
        request_to_response_ms: "浏览器请求至响应及必要数据读取完成",
        click_to_first_image_decode_ready_ms: "从搜索动作至首张缩略图解码就绪",
        click_to_viewport_ready_ms: "从搜索动作至当前视口缩略图全部成功就绪" },
      not_measured_stages: ["page_ms", "worker_infer_ms", "worker_reload_count", "background_wait_ms"],
      note: "P95 是样本内 nearest-rank 值；样本数不足 20 时仅作本轮描述，不代表长期性能。" };
    report.samples.forEach(function (sample) { sample.search_execution_ms = sample.server && sample.server.search_execution_ms; });
    var blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
    var link = document.createElement("a"), url = URL.createObjectURL(blob);
    var provider = perfDiag.model && perfDiag.model.provider_id || "unconfirmed";
    var date = new Date().toISOString().slice(0, 10).replace(/-/g, "");
    link.href = url; link.download = "photo-search-perf-" + provider + "-" + date + ".json";
    link.click(); window.setTimeout(function () { URL.revokeObjectURL(url); }, 0);
  }

  function initPerfDiagnostics() {
    var toggle = document.getElementById("search-perf-toggle");
    var panel = document.getElementById("search-perf-panel");
    if (!toggle || !panel) return;
    toggle.checked = false;
    toggle.addEventListener("change", function () {
      perfDiag.enabled = toggle.checked; panel.hidden = !perfDiag.enabled;
      if (perfDiag.enabled) {
        perfDiag.runId = (window.crypto && window.crypto.randomUUID) ? window.crypto.randomUUID() : String(Date.now()) + "-" + Math.random().toString(16).slice(2);
        perfRefreshIdentity();
      } else {
        perfDiag.identitySeq += 1;
        perfDiag.identityChecking = false;
        perfDiag.model = null;
        document.getElementById("search-perf-start").disabled = true;
        document.getElementById("search-perf-identity-retry").hidden = true;
        if (perfDiag.autoRunning) stopPerfTest("用户关闭了诊断");
        else if (perfDiag.active) {
          if (perfDiag.active.viewport_deadline) window.clearTimeout(perfDiag.active.viewport_deadline);
          perfDiag.active.valid = false; perfDiag.active.sample_type = "interrupted";
          perfDiag.active.invalid_reason = "DIAGNOSTICS_DISABLED";
          perfDiag.samples.push(perfDiag.active); perfDiag.active = null; perfUpdateSummary();
        }
      }
    });
    document.getElementById("search-perf-identity-retry").addEventListener("click", perfRefreshIdentity);
    document.getElementById("search-perf-start").addEventListener("click", startPerfTest);
    document.getElementById("search-perf-stop").addEventListener("click", function () { stopPerfTest("用户停止"); });
    document.getElementById("search-perf-clear").addEventListener("click", function () {
      stopPerfTest("记录已清空");
      if (perfDiag.active && perfDiag.active.viewport_deadline) window.clearTimeout(perfDiag.active.viewport_deadline);
      perfDiag.active = null; perfDiag.samples = []; perfDiag.count = 0; perfDiag.runSamplesStart = 0; perfUpdateSummary(); updatePerfTestSummary();
    });
    document.getElementById("search-perf-export").addEventListener("click", perfExport);
    if (typeof window.addEventListener === "function") {
      window.addEventListener("pagehide", function () { if (perfDiag.autoRunning) stopPerfTest("页面已离开"); });
    }
  }

  function updatePerfTestSummary(message) {
    var target = document.getElementById("search-perf-test-summary");
    if (!target) return;
    var hot = perfCurrentRunSamples().filter(function (sample) {
      return sample.valid && sample.test_mode === "standard_auto" && sample.sample_type === "hot" &&
        perfSampleMatchesCurrentIdentity(sample);
    });
    var exec = perfPercentile(hot, "search_execution_ms", .5), exec95 = perfPercentile(hot, "search_execution_ms", .95);
    var enc = perfPercentile(hot, "query_encode_ms", .5), vector = perfPercentile(hot, "vector_ms", .5);
    var first = perfPercentile(hot, "click_to_first_image_decode_ready_ms", .5);
    var viewport = perfPercentile(hot, "click_to_viewport_ready_ms", .5), viewport95 = perfPercentile(hot, "click_to_viewport_ready_ms", .95);
    function fmt(item) { return item.value == null ? "未测" : item.value.toFixed(1) + " ms"; }
    target.textContent = (message ? message + " · " : "") + "有效热样本 " + hot.length +
      "；搜索执行 P50/P95 " + fmt(exec) + "/" + fmt(exec95) + "；编码 P50 " + fmt(enc) + " (n=" + enc.n + ")" +
      "；LanceDB P50 " + fmt(vector) + " (n=" + vector.n + ")" +
      "；首图 P50 " + fmt(first) + "；视口 P50/P95 " + fmt(viewport) + "/" + fmt(viewport95);
  }

  function perfConditionsSnapshot() {
    return JSON.stringify({ query: els.queryText.value.trim(), filters: collectFilters(), mode: textRetrievalMode(),
      favorites: Boolean(state.search.favoritesOnly), pageSize: els.queryPageSize.value });
  }

  function perfSemanticSnapshot() {
    return JSON.stringify({ intentPlan: state.search.intentPlan,
      placeAware: state.search.placeAware && { original_query: state.search.placeAware.original_query,
        content_query: state.search.placeAware.content_query, place_kind: state.search.placeAware.place_kind,
        place_key: state.search.placeAware.place_key, place_cancelled: Boolean(state.search.placeAware.place_cancelled) },
      selectedScope: state.search.selectedScope });
  }

  function freezePerfControls(frozen) {
    var controls = document.querySelectorAll("#search-form input, #search-form select, #search-form button, #search-filter-controls input, #search-filter-controls select, #search-filter-controls button");
    Array.prototype.forEach.call(controls, function (control) {
      if (frozen) { control.dataset.perfWasDisabled = control.disabled ? "1" : "0"; control.disabled = true; }
      else if (control.dataset.perfWasDisabled !== undefined) { control.disabled = control.dataset.perfWasDisabled === "1"; delete control.dataset.perfWasDisabled; }
    });
  }

  function stopPerfTest(reason) {
    if (!perfDiag.autoRunning) return;
    perfDiag.autoStop = true;
    if (perfDiag.active) {
      var sample = perfDiag.active;
      if (sample.viewport_deadline) window.clearTimeout(sample.viewport_deadline);
      sample.valid = false; sample.sample_type = "interrupted"; sample.invalid_reason = reason;
      perfDiag.samples.push(sample); perfDiag.active = null; perfUpdateSummary();
    }
    perfDiag.autoRunning = false; document.getElementById("search-perf-stop").hidden = true;
    document.getElementById("search-perf-start").disabled = !perfDiag.enabled || !perfDiag.model;
    freezePerfControls(false); updatePerfTestSummary(reason);
  }

  function waitForPerfSample(sample, timeoutMs) {
    var start = Date.now();
    return new Promise(function (resolve) {
      function check() {
        if (perfDiag.active !== sample) return resolve(true);
        if (Date.now() - start >= timeoutMs) {
          if (sample.viewport_deadline) window.clearTimeout(sample.viewport_deadline);
          sample.valid = false; sample.sample_type = "timeout"; sample.invalid_reason = "SAMPLE_TIMEOUT";
          perfDiag.samples.push(sample); perfDiag.active = null; perfUpdateSummary(); return resolve(false);
        }
        window.setTimeout(check, 50);
      }
      check();
    });
  }

  function startPerfTest() {
    if (!perfDiag.enabled || perfDiag.autoRunning || perfDiag.identityChecking) return;
    if (!String(els.queryText.value || "").trim()) { updatePerfTestSummary("请先输入搜索词"); return; }
    if (state.search.mode !== "text" || textRetrievalMode() !== "combined" || state.search.loading || perfDiag.active) {
      updatePerfTestSummary("请等待当前搜索及视口图片观察结束，并使用综合搜索进行标准测试"); return;
    }
    var conditions = perfConditionsSnapshot();
    var selectedScopeBeforeIdentityCheck = JSON.stringify(state.search.selectedScope);
    updatePerfTestSummary("开始前重新确认当前模型身份…");
    perfRefreshIdentity().then(function (model) {
      if (!model || !perfDiag.enabled || perfDiag.autoRunning) return;
      if (conditions !== perfConditionsSnapshot() ||
          selectedScopeBeforeIdentityCheck !== JSON.stringify(state.search.selectedScope)) {
        updatePerfTestSummary("身份确认期间搜索条件发生变化；测试未启动"); return;
      }
      startPerfTestWithIdentity(model, conditions);
    });
  }

  function startPerfTestWithIdentity(model, conditions) {
    if (!perfDiag.enabled || perfDiag.autoRunning || !model) return;
    if (state.search.mode !== "text" || textRetrievalMode() !== "combined" || state.search.loading || perfDiag.active) {
      updatePerfTestSummary("请等待当前搜索及视口图片观察结束，并使用综合搜索进行标准测试"); return;
    }
    var currentQuery = els.queryText.value.trim();
    if (state.search.intentPlan && state.search.intentPlan.original_query !== currentQuery) state.search.intentPlan = null;
    if (state.search.placeAware && state.search.placeAware.original_query !== currentQuery) state.search.placeAware = null;
    perfDiag.model = model; perfDiag.autoRunning = true; perfDiag.autoStop = false;
    perfDiag.autoConditions = conditions; perfDiag.autoSemantic = null;
    perfDiag.runSamplesStart = perfDiag.samples.length;
    var frozen = conditions;
    document.getElementById("search-perf-start").disabled = true; document.getElementById("search-perf-stop").hidden = false;
    freezePerfControls(true); els.panelResults.scrollIntoView({ block: "start", behavior: "instant" });
    var types = ["first_observed", "warmup", "warmup"];
    for (var i = 0; i < 20; i += 1) types.push("hot");
    function next(index) {
      if (!perfDiag.autoRunning || perfDiag.autoStop) return Promise.resolve();
      if (perfConditionsSnapshot() !== frozen) { stopPerfTest("搜索条件发生变化，测试已停止"); return Promise.resolve(); }
      if (perfDiag.autoSemantic !== null && perfSemanticSnapshot() !== perfDiag.autoSemantic) {
        stopPerfTest("服务端解析出的有效搜索条件发生变化，标准测试已停止"); return Promise.resolve();
      }
      if (index >= types.length) {
        perfDiag.autoRunning = false; document.getElementById("search-perf-stop").hidden = true;
        document.getElementById("search-perf-start").disabled = false; freezePerfControls(false);
        var validHotCount = perfCurrentRunSamples().filter(function (sample) {
          return sample.valid && sample.test_mode === "standard_auto" && sample.sample_type === "hot" &&
            perfSampleMatchesCurrentIdentity(sample);
        }).length;
        updatePerfTestSummary(validHotCount === 20
          ? "标准测试完成：1 次首次观察、2 次预热、20 个有效热样本"
          : "测试轮次结束，但有效热样本仅 " + validHotCount + "/20；失败样本已保留且未重试");
        return Promise.resolve();
      }
      perfDiag.nextSampleType = types[index];
      var samplesBefore = perfDiag.samples.length;
      return Promise.resolve(sendSearch(false)).then(function () {
        var sample = perfDiag.active || (perfDiag.samples.length > samplesBefore
          ? perfDiag.samples[perfDiag.samples.length - 1] : null);
        if (!sample || sample.test_mode !== "standard_auto") { stopPerfTest("本轮搜索未生成可观察样本"); return; }
        updatePerfTestSummary("正在执行第 " + (index + 1) + "/23 轮");
        var observation = perfDiag.active === sample ? waitForPerfSample(sample, 15000) : Promise.resolve(true);
        return observation.then(function (done) {
          if (!done || sample.invalid_reason === "MODEL_BINDING_MISMATCH" || sample.invalid_reason === "MANUAL_SELECTION_REQUIRED") {
            stopPerfTest(sample.invalid_reason || "本轮超时"); return;
          }
          return next(index + 1);
        });
      }).catch(function (failure) {
        var code = failure && failure.code || "SEARCH_FAILED";
        stopPerfTest("搜索请求失败（" + String(code) + "），自动测试已停止");
      });
    }
    next(0);
  }

  function abortPerfForChoice() {
    if (perfDiag.active) {
      var sample = perfDiag.active; sample.valid = false; sample.sample_type = "interrupted";
      sample.invalid_reason = "MANUAL_SELECTION_REQUIRED"; perfDiag.samples.push(sample); perfDiag.active = null; perfUpdateSummary();
    }
    if (perfDiag.autoRunning) stopPerfTest("需要人工选择范围，测试已停止");
  }

  function releaseObjectUrls() {
    while (state.objectUrls.length) {
      URL.revokeObjectURL(state.objectUrls.pop());
    }
  }

  function fetchArtifact(path) {
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
            code: (payload && payload.code) || "HTTP_" + response.status,
            message: (payload && payload.message) || "产物不可用",
            request_id: (payload && payload.request_id) || "",
          };
        });
    });
  }

  // ------------------------------------------------------------------- notices
  function pushNotice(container, kind, message) {
    container.appendChild(element("p", "notice-line" + (kind ? " " + kind : ""), message));
  }

  function renderNotices(container, lines) {
    clear(container);
    lines.forEach(function (line) {
      pushNotice(container, line.kind, line.text);
    });
  }

  function successNotices(body) {
    var lines = [];
    if (body.retrieval_mode === "text") {
      lines.push({ kind: "informational", text: "本次按文字找：匹配文件名、已归属地点名称及已识别的图片文字。引号可限定完整短语；尚未识别的图片文字暂时无法命中。" });
    }
    if (body.candidates_truncated) {
      lines.push({ kind: "informational", text: "本次候选达到检索上限，可能还有更多匹配。可增加文字或筛选条件缩小范围。" });
    }
    var projection = body.search_projection || {};
    var capture = captureCondition(body);
    if (capture.present) {
      var notice = captureNotice(capture.block, (body.hits || []).length);
      if (notice) {
        lines.push({ kind: notice.kind, text: notice.text });
      }
    }
    if (!body.asset_query && projection.state === "LAGGING") {
      lines.push({
        kind: "",
        text:
          "文本投影仍在追赶：还有 " +
          number(projection.queued_assets) +
          " 个资产等待重新派生文本索引（本次结果已按图库权威记录逐行复核）。",
      });
    }
    var pending = body.pending_semantic;
    if (pending && number(pending.count) > 0) {
      lines.push({
        kind: "",
        text:
          "语义索引仍在追赶：绑定世代 " +
          safeText(pending.generation_id || "") +
          " 下有 " +
          number(pending.count) +
          " 个权威候选还没有已确认的向量条目（reason=" +
          safeText(pending.reason || "") +
          "）" +
          (pending.truncated ? "，计数已被上界截断" : "") +
          "。这些照片可能相关但语义通道现在还看不到。",
      });
    }
    var channels = body.channels || {};
    var vector = channels.vector || {};
    if (vector.scan_truncated) {
      lines.push({ kind: "", text: "向量扫描未完整完成，本次结果可能不完整。" });
    }
    if (body.state === "FILTER_REVISION_CHANGED") {
      lines.push({
        kind: "",
        text:
          "查询执行期间地点/可见性发生了变化（state=FILTER_REVISION_CHANGED）：" +
          "本次已按最新权威条件逐行复核，但可能有更新的结果；需要时可重新搜索。",
      });
    }
    var authoritative = channels.authoritative || {};
    if (authoritative.truncated) {
      lines.push({
        kind: "informational",
        text:
          "权威候选列表只发布了前 " +
          number(authoritative.listed) +
          " 项（共 " +
          number(authoritative.total) +
          " 项，order=" +
          safeText(authoritative.order || "") +
          "）：分页只在这份有界列表内进行。",
      });
    }
    if (!body.asset_query && !body.query && !body.image_query) {
      lines.push({
        kind: "informational",
        text:
          "本次没有查询文本：没有生成任何查询向量，结果由图库权威列表给出" +
          "（hit_reason=FILTER_ONLY）。",
      });
    }
    if (body.image_query) {
      var image = body.image_query;
      lines.push({
        kind: "informational",
        text:
          "上传图：" +
          number(image.width) +
          "×" +
          number(image.height) +
          " · " +
          safeText(image.format) +
          " · " +
          number(image.byte_size) +
          " 字节 · 解码 " +
          number(image.decode_ms) +
          " ms（服务端限制：" +
          number((image.limits || {}).max_bytes) +
          " 字节 / " +
          number((image.limits || {}).max_pixels) +
          " 像素 / " +
          number((image.limits || {}).decode_budget_ms) +
          " ms）。",
      });
    }
    if (body.rerank_state && body.rerank_state !== "NOT_REQUESTED") {
      lines.push({
        kind: "informational",
        text:
          "精排状态 " +
          safeText(body.rerank_state) +
          "：精排分数与 RRF 分数是两套含义，本页不把它们相加，也不把它当成准确率。",
      });
    }
    return lines;
  }

  //: P4-00B: whether this response carried a capture condition, and its honesty block.
  function captureCondition(body) {
    var filters = (body && body.filters) || {};
    var from = filters.capture_from;
    var to = filters.capture_to_exclusive;
    var present =
      (from !== null && from !== undefined) || (to !== null && to !== undefined);
    return { present: present, block: (body && body.taken_time) || null };
  }

  //: ADR-0011 §8.1/§8.4: the honest sentence for a capture-conditioned page. It never turns
  //: "the extraction has not run" into "there is no match", and it never prints a UTC
  //: instant - the echoed bounds are wall-clock spellings.
  function captureNotice(block, hits) {
    if (!block) {
      return null;
    }
    if (!hits && block.state !== "READY" && block.state !== "NO_CAPTURE_TIME") {
      return {
        kind: "",
        text:
          (CAPTURE_STATE_TEXT[block.state] || String(block.state)) +
          "（已抽取 " +
          number(block.extracted_assets) +
          "/" +
          number(block.assets_total) +
          "，其中有拍摄时间 " +
          number(block.capture_time_assets) +
          "）：这一页为空说明拍摄时间还没跟上，不代表这些照片不存在。",
      };
    }
    if (!hits && block.state === "NO_CAPTURE_TIME") {
      return {
        kind: "",
        text:
          CAPTURE_STATE_TEXT.NO_CAPTURE_TIME +
          "（资产 " +
          number(block.assets_total) +
          " 个，已抽取 " +
          number(block.extracted_assets) +
          " 个，可用的拍摄时间 0 个）。",
      };
    }
    return {
      kind: "informational",
      text:
        (CAPTURE_STATE_TEXT[block.state] || String(block.state)) +
        "（已抽取 " +
        number(block.extracted_assets) +
        "/" +
        number(block.assets_total) +
        "，其中有拍摄时间 " +
        number(block.capture_time_assets) +
        "）。",
    };
  }

  function failureLines(failure) {
    var lines = [];
    var reason = (failure.details || {}).reason;
    if (failure.code === "VALIDATION_ERROR" && reason === "SEARCH_PROFILE_REQUIRED") {
      lines.push({ kind: "", text: "尚未启用画面搜索模型。请切换“按文字找”查询文件名或图片文字，也可以只用筛选条件浏览照片。" });
    } else if (failure.code === "RUNTIME_CAPABILITY_UNAVAILABLE") {
      if (reason === "IMAGE_ENCODER_UNAVAILABLE") {
        lines.push({
          kind: "",
          text:
            "本进程没有图像编码器，无法用上传图做语义检索" +
            "（RUNTIME_CAPABILITY_UNAVAILABLE / IMAGE_ENCODER_UNAVAILABLE）；" +
            "服务端没有用文本通道或「零命中」冒充结果。",
        });
      } else if (reason === "QUERY_ENCODER_UNAVAILABLE") {
        lines.push({
          kind: "",
          text:
            "当前画面搜索模型不可用。可切换“按文字找”查询文件名或图片文字，" +
            "也可以只用筛选条件浏览照片；本次尚未完成画面搜索。",
        });
      } else {
        lines.push({
          kind: "",
          text:
            "本次查询需要的运行时能力在本进程不可用（reason=" +
            safeText(reason || "未提供") +
            "）。",
        });
      }
    } else if (FAILURE_HINT[failure.code]) {
      lines.push({ kind: "", text: FAILURE_HINT[failure.code] });
    }
    lines.push({ kind: "informational", text: "服务端返回：" + describe(failure) });
    return lines;
  }

  // --------------------------------------------------------------------- filters
  function parsePlaceIds(raw) {
    var source = String(raw || "").trim();
    if (!source) {
      return { values: [] };
    }
    var parts = source.split(/[,，\s]+/);
    var values = [];
    for (var index = 0; index < parts.length; index += 1) {
      var piece = parts[index].trim();
      if (!piece) {
        continue;
      }
      if (!/^[0-9]+$/.test(piece)) {
        return { values: [], problem: "地点 place_id 只能是正整数，多个用逗号分隔。" };
      }
      var value = Number(piece);
      if (value < 1) {
        return { values: [], problem: "地点 place_id 必须大于 0。" };
      }
      if (values.indexOf(value) < 0) {
        values.push(value);
      }
    }
    return { values: values };
  }

  function collectFilters() {
    var pageSize = Number(els.queryPageSize.value || DEFAULT_PAGE_SIZE);
    if (!isFinite(pageSize) || Math.floor(pageSize) !== pageSize) {
      return { problem: "每页数量必须是整数。" };
    }
    if (pageSize < MIN_PAGE_SIZE || pageSize > MAX_PAGE_SIZE) {
      return {
        problem: "每页数量必须在 " + MIN_PAGE_SIZE + " 到 " + MAX_PAGE_SIZE + " 之间。",
      };
    }
    var places = parsePlaceIds(els.queryPlaceIds.value);
    if (places.problem) {
      return { problem: places.problem };
    }
    var personIds = selectedPersonIds();
    var personMatch = els.queryPersonMatch.value === "all" ? "all" : "any";
    if (!personIds.length) {
      // No person selected is *not* "nobody's face": the page drops the condition entirely
      // instead of sending an empty list with "all".
      personMatch = "any";
    }
    return {
      sourceId: els.querySource.value ? Number(els.querySource.value) : null,
      availability: els.queryAvailability.value,
      placeIds: places.values,
      placeState: els.queryPlaceState.value,
      includeDescendants: els.queryDescendants.value === "true",
      personIds: personIds,
      personMatch: personMatch,
      captureFrom: els.queryCaptureFrom.value.trim(),
      captureTo: els.queryCaptureTo.value.trim(),
      pageSize: pageSize,
    };
  }

  function newRequestId(prefix) {
    state.requestCounter += 1;
    return prefix + "-" + Date.now().toString(36) + "-" + state.requestCounter;
  }

  // ----------------------------------------------------------------- person filter
  // The picker is a *read* of the authoritative person list: it never guesses an internal id,
  // never reads the catalog directly and never folds two people who share a name into one.
  // The selection is keyed by person_id and lives outside the DOM, so loading the next page or
  // redrawing a page cannot lose a choice that has already been made.

  function personKey(value) {
    return String(value);
  }

  function personDisplayName(person) {
    var name = person && person.name ? String(person.name) : "";
    return name || "未命名人物";
  }

  function personOptionText(person) {
    return (
      personDisplayName(person) +
      " #" +
      number(person.person_id) +
      "（照片 " +
      number(person.asset_count) +
      " · 脸 " +
      number(person.face_count) +
      "）"
    );
  }

  function selectedPersonIds() {
    var ids = Object.keys(state.person.selected).map(function (key) {
      return Number(key);
    });
    ids = ids.filter(function (value) {
      return isFinite(value) && value >= 1 && Math.floor(value) === value;
    });
    ids.sort(function (left, right) {
      return left - right;
    });
    return ids;
  }

  function sameIdList(left, right) {
    if (left.length !== right.length) {
      return false;
    }
    for (var index = 0; index < left.length; index += 1) {
      if (Number(left[index]) !== Number(right[index])) {
        return false;
      }
    }
    return true;
  }

  function updatePersonSelection() {
    var ids = selectedPersonIds();
    var parts = ids.map(function (id) {
      var row = state.person.selected[personKey(id)];
      return "#" + id + " " + personDisplayName(row);
    });
    text(
      els.personSelection,
      ids.length
        ? "已选择 " +
            ids.length +
            " 个人物（canonical）：" +
            parts.join("、") +
            "。同名人物各按 #person_id 计，请求按 person_id 升序去重；以服务端回显为准。"
        : "未选择人物：普通搜索不会发送 person_ids，也不会发送 person_match=all。"
    );
    syncPersonControls();
  }

  function syncPersonControls() {
    var usable = state.person.enabled;
    var ids = selectedPersonIds();
    els.queryPersonMatch.disabled = !usable;
    var allOption = els.queryPersonMatch.querySelector('option[value="all"]');
    if (allOption) {
      allOption.disabled = !usable || !ids.length;
    }
    if (!ids.length && els.queryPersonMatch.value === "all") {
      // "all" without any id is refused by the frozen shape, so the page never builds it.
      els.queryPersonMatch.value = "any";
    }
    els.personClear.disabled = !usable || !ids.length;
    els.personReload.disabled = !usable || state.person.loading;
    els.personMore.disabled = !usable || state.person.loading || !state.person.hasMore;
    els.personMore.hidden = !usable || !state.person.hasMore;
    if (!usable) {
      setStatus(
        els.personStatus,
        state.person.reason || "正在读取程序能力与人物列表…",
        state.person.reason ? "error" : ""
      );
    }
  }

  function disablePersonFilter(reason) {
    state.person.enabled = false;
    state.person.reason = reason;
    state.person.selected = {};
    state.person.rows = {};
    state.person.cursor = null;
    state.person.hasMore = false;
    clear(els.personOptions);
    text(els.personEcho, "");
    syncPersonControls();
  }

  function renderPersonOptions(rows, reset) {
    if (reset) {
      clear(els.personOptions);
    }
    rows.forEach(function (person) {
      var personId = Number(person.person_id);
      var key = personKey(personId);
      state.person.rows[key] = person;
      var item = element("li", "person-option");
      var box = document.createElement("input");
      box.type = "checkbox";
      box.id = "person-option-" + key;
      box.value = key;
      box.checked = Object.prototype.hasOwnProperty.call(state.person.selected, key);
      box.disabled = !state.person.enabled;
      box.addEventListener("change", function () {
        togglePerson(personId, box.checked);
      });
      var label = document.createElement("label");
      label.setAttribute("for", box.id);
      text(label, personOptionText(person));
      item.appendChild(box);
      item.appendChild(label);
      els.personOptions.appendChild(item);
    });
  }

  function togglePerson(personId, checked) {
    var key = personKey(personId);
    if (checked) {
      var row = state.person.rows[key] || {
        person_id: personId,
        name: null,
        face_count: 0,
        asset_count: 0,
      };
      state.person.selected[key] = {
        person_id: personId,
        name: row.name === undefined ? null : row.name,
        face_count: number(row.face_count),
        asset_count: number(row.asset_count),
      };
    } else {
      delete state.person.selected[key];
    }
    // A changed selection makes the previous echo a statement about a different query.
    text(els.personEcho, "");
    updatePersonSelection();
  }

  function clearPersonSelection() {
    state.person.selected = {};
    var boxes = els.personOptions.querySelectorAll("input[type=checkbox]");
    for (var index = 0; index < boxes.length; index += 1) {
      boxes[index].checked = false;
    }
    text(els.personEcho, "");
    updatePersonSelection();
  }

  function loadPersons(reset) {
    if (!state.person.enabled || state.person.loading) {
      return Promise.resolve(false);
    }
    if (reset) {
      state.person.cursor = null;
      state.person.hasMore = false;
    }
    state.person.loading = true;
    setStatus(
      els.personStatus,
      reset ? "正在读取人物（照片最多优先）…" : "正在读取更多人物…"
    );
    var query = "?kind=all&sort=photos_desc&limit=" + PERSON_LIMIT;
    if (!reset && state.person.cursor) {
      query += "&cursor=" + encodeURIComponent(state.person.cursor);
    }
    syncPersonControls();
    return request("/persons/browse" + query).then(
      function (result) {
        var body = result.body || {};
        var rows = body.persons || [];
        state.person.loading = false;
        renderPersonOptions(rows, reset);
        state.person.cursor = body.next_cursor || null;
        state.person.hasMore = Boolean(body.has_more);
        setStatus(
          els.personStatus,
          rows.length === 0
            ? reset
              ? "这个图库还没有 active canonical 人物（是空列表，不是读取失败）。"
              : "没有更多人物。"
            : "本页 " +
                rows.length +
                " 个人物（active canonical）" +
                (body.has_more ? " · 还有更多" : " · 已到末尾")
        );
        updatePersonSelection();
        return true;
      },
      function (failure) {
        state.person.loading = false;
        disablePersonFilter(
          "人物列表读取失败：" +
            describe(failure) +
            "；人物筛选已禁用，本次不会发送人物条件（这不代表图库里没有人物）。"
        );
        return false;
      }
    );
  }

  function renderPersonEcho(filters, mode) {
    var body = filters || {};
    var ids = Array.isArray(body.person_ids) ? body.person_ids.map(Number) : [];
    var match = body.person_match === "all" ? "all" : "any";
    var lines = [];
    if (mode === "image") {
      lines.push(
        "上传搜图不携带人物条件：服务端的回显是 person_ids=[]、person_match=any" +
          "（人物筛选只作用于普通搜索）。"
      );
    } else if (!ids.length) {
      lines.push("后端回显：person_ids=[]、person_match=any（本次查询没有人物条件）。");
    } else {
      lines.push(
        "后端回显（canonical）：person_ids=[" + ids.join(", ") + "]、person_match=" + match + "。"
      );
      if (!sameIdList(ids, selectedPersonIds())) {
        lines.push("服务端把提交的 id 解析并折叠成了上面的 canonical 人物；本页照回显显示。");
      }
    }
    text(els.personEcho, lines.join(" "));
  }

  //: The condition set one search session is bound to. Continuing an old cursor after any of
  //: these changed would ask the server to mix two queries, so the page refuses and offers a
  //: fresh search instead of sending the old cursor with new conditions.
  function conditionFingerprint(filters) {
    return JSON.stringify({
      text: els.queryText.value.trim(),
      retrievalMode: state.search.mode === "image" ? "image" : textRetrievalMode(),
      source: filters.sourceId,
      fixedScope: state.search.selectedScope ? JSON.stringify(state.search.selectedScope) : null,
      favoritesOnly: Boolean(state.search.favoritesOnly),
      availability: filters.availability,
      places: filters.placeIds,
      descendants: filters.includeDescendants,
      placeState: filters.placeState,
      persons: filters.personIds,
      personMatch: filters.personIds.length ? filters.personMatch : "any",
      captureFrom: filters.captureFrom,
      captureTo: filters.captureTo,
      pageSize: filters.pageSize,
    });
  }

  //: P4-00B: the condition is echoed exactly as the server normalized it (fixed-width
  //: wall-clock spelling), never as a local guess and never as a UTC instant.
  function renderCaptureEcho(filters, mode) {
    var body = filters || {};
    var from = body.capture_from === undefined ? null : body.capture_from;
    var to = body.capture_to_exclusive === undefined ? null : body.capture_to_exclusive;
    if (mode === "image") {
      text(
        els.captureEcho,
        "上传搜图不携带拍摄时间条件：服务端 multipart 闭集里没有 capture_from / capture_to_exclusive。"
      );
      return;
    }
    if (from === null && to === null) {
      text(els.captureEcho, "后端回显：本次查询没有拍摄时间条件（两个键都为 null）。");
      return;
    }
    text(
      els.captureEcho,
      "后端回显（规范化墙上时间，左闭右开）：capture_from=" +
        (from === null ? "null" : safeText(from)) +
        "，capture_to_exclusive=" +
        (to === null ? "null" : safeText(to)) +
        "。"
    );
  }

  // ---------------------------------------------------------------------- search
  function textRetrievalMode() {
    return els.queryRetrievalMode && els.queryRetrievalMode.value === "text" ? "text" : "combined";
  }

  function candidateTimeLabel() {
    return state.search.lastBody && state.search.lastBody.retrieval_mode === "text"
      ? "按时间查看文字结果" : "按时间查看当前语义候选";
  }

  function similarAssetFromUrl() {
    var params = new URLSearchParams(window.location.search || "");
    if (!params.has("similar_asset")) return { present: false };
    var raw = params.get("similar_asset") || "";
    if (!/^[1-9][0-9]{0,17}$/.test(raw) || !Number.isSafeInteger(Number(raw))) {
      return { present: true, valid: false };
    }
    return { present: true, valid: true, assetId: Number(raw), conflicts: params.has("scope_entry") || params.has("q") };
  }

  function clearSimilarAssetUrl() {
    var params = new URLSearchParams(window.location.search || "");
    if (!params.has("similar_asset")) return;
    params.delete("similar_asset");
    var query = params.toString();
    window.history.replaceState(window.history.state, "", (window.location.pathname || "/search") +
      (query ? "?" + query : "") + (window.location.hash || ""));
  }

  function setSimilarQueryControls(active) {
    if (!els.searchFilterControls) return;
    els.searchFilterControls.inert = Boolean(active);
    els.searchFilterControls.setAttribute("aria-disabled", active ? "true" : "false");
    if (els.searchSubmit) els.searchSubmit.textContent = active
      ? "结束相似搜索并按文字搜索"
      : state.search.selectedScope ? "搜索当前范围" : "全库搜索";
  }

  function clearAssetQuerySummary() {
    state.search.assetQueryId = null;
    state.search.assetQuery = null;
    if (!els.assetQuerySummary) return;
    els.assetQuerySummary.hidden = true;
    if (els.assetQueryImage) {
      els.assetQueryImage.removeAttribute("src");
      els.assetQueryImage.hidden = true;
    }
    text(els.assetQueryTitle, "");
    text(els.assetQueryFilename, "");
    text(els.assetQueryScope, "");
  }

  function startSimilarAssetSearch(assetId) {
    state.search.mode = "asset_image";
    state.search.assetQueryId = Number(assetId);
    state.search.assetQuery = null;
    state.search.selectedScope = null;
    state.search.favoritesOnly = false;
    state.search.placeAware = null;
    state.search.intentPlan = null;
    state.search.lastBody = null;
    state.search.confirmedSearch = null;
    state.search.lastRequestBody = null;
    state.search.sessionId = null;
    state.search.cursor = null;
    state.search.hits = [];
    state.search.count = 0;
    state.search.total = null;
    state.search.binding = null;
    els.queryText.value = "";
    els.querySource.value = "";
    els.queryAvailability.value = "any";
    els.queryPlaceState.value = "any";
    els.queryPlaceIds.value = "";
    els.queryDescendants.value = "true";
    els.queryCaptureFrom.value = "";
    els.queryCaptureTo.value = "";
    els.queryPageSize.value = String(DEFAULT_PAGE_SIZE);
    els.queryPersonMatch.value = "any";
    if (els.queryFavoritesOnly) els.queryFavoritesOnly.checked = false;
    clearPersonSelection();
    setSimilarQueryControls(true);
    if (els.assetQuerySummary) {
      text(els.assetQueryTitle, "相似照片搜索");
      text(els.assetQueryFilename, "正在读取参考照片…");
      text(els.assetQueryScope, "搜索范围：全部图库");
      els.assetQueryImage.removeAttribute("src");
      els.assetQueryImage.hidden = true;
      els.assetQuerySummary.hidden = false;
    }
    renderScopeControls();
    sendSearch(false);
  }

  function sendSimilarAssetSearch(continuation) {
    var body = {
      contract_version: CONTRACT_VERSION,
      request_id: newRequestId("similar"),
      asset_id: Number(state.search.assetQueryId),
      page_size: Number(els.queryPageSize.value) || DEFAULT_PAGE_SIZE,
    };
    if (continuation) {
      body.search_session_id = state.search.sessionId;
      body.cursor = state.search.cursor;
    } else {
      state.search.lastRequestBody = JSON.parse(JSON.stringify(body));
    }
    return request("/search/by-asset", { method: "POST", body: body }).then(function (result) {
      return result.body || {};
    });
  }

  function canRunTextSemantic() {
    var search = state.capabilities && state.capabilities.search;
    if (!search) {
      return false;
    }
    return Boolean(search.active_profile_id) && search.query_encoder_available === true;
  }

  function submitSearch() {
    clearSimilarAssetUrl();
    clearAssetQuerySummary();
    setSimilarQueryControls(false);
    state.search.imageFile = null;
    state.search.pendingImageFile = null;
    clearSearchSnapshot();
    state.search.hits = [];
    state.search.windowOffset = 0;
    state.search.recycledCount = 0;
    state.search.lastBody = null;
    state.search.confirmedSearch = null;
    state.search.lastRequestBody = null;
    state.search.placeAware = null;
    state.search.intentPlan = null;
    state.search.mode = "text";
    state.search.sessionId = null;
    state.search.cursor = null;
    state.search.count = 0;
    state.search.total = null;
    state.search.binding = null;
    releaseObjectUrls();
    clear(els.resultGrid);
    renderIntentChips(null);
    renderScopeControls();
    renderNotices(els.resultNotices, []);
    text(els.captureEcho, "");
    setStatus(els.captureState, "");
    els.loadMore.hidden = true;
    if (els.gridSentinel) els.gridSentinel.hidden = true;
    if (state.search.pager) state.search.pager.reset();
    els.queryActions.hidden = true;
    sendSearch(false);
  }

  function continueSearch() {
    if (state.search.timeBrowse.active) {
      return loadCandidateTimeBrowse(true);
    }
    return sendSearch(true);
  }

  function toggleCandidateTimeBrowse() {
    var browse = state.search.timeBrowse;
    if (browse.active) {
      browse.active = false;
      browse.cursor = null;
      browse.retryCursor = null;
      browse.requestSeq += 1;
      state.search.loading = false;
      els.loadMore.disabled = false;
      if (browse.previous) {
        state.search.hits = browse.previous.hits;
        state.search.cursor = browse.previous.cursor;
        state.search.count = browse.previous.count;
        state.search.total = browse.previous.total;
        state.search.windowOffset = browse.previous.windowOffset || 0;
        state.search.recycledCount = browse.previous.recycledCount || 0;
        clear(els.resultGrid);
        state.search.hits.forEach(function (hit) { els.resultGrid.appendChild(buildHit(hit)); });
        if (window.PhotoScrollPager) {
          window.PhotoScrollPager.setSpacerHeight(els.resultGrid, browse.previous.spacerHeight);
        }
        els.loadMore.hidden = !state.search.cursor;
        if (els.gridSentinel) els.gridSentinel.hidden = !state.search.cursor;
        updateSearchPreviewNavigation();
      }
      browse.previous = null;
      els.candidateTimeToggle.textContent = candidateTimeLabel();
      if (state.search.pager) {
        state.search.pager.reset();
        state.search.pager.update();
      }
      renderSearchStatus();
      return;
    }
    if (!state.search.sessionId || !state.search.confirmedSearch || !state.search.confirmedSearch.query) return;
    browse.previous = { hits: state.search.hits.slice(), cursor: state.search.cursor,
      count: state.search.count, total: state.search.total, windowOffset: state.search.windowOffset,
      recycledCount: state.search.recycledCount,
      spacerHeight: window.PhotoScrollPager ? window.PhotoScrollPager.getSpacerHeight(els.resultGrid) : 0 };
    browse.active = true;
    browse.cursor = null;
    browse.retryCursor = null;
    browse.total = state.search.confirmedSearch.total;
    browse.truncated = state.search.confirmedSearch.truncated;
    browse.rulesVersion = null;
    state.search.count = 0;
    state.search.hits = [];
    state.search.cursor = null;
    state.search.windowOffset = 0;
    state.search.recycledCount = 0;
    if (state.search.pager) state.search.pager.reset();
    clear(els.resultGrid);
    invalidateSearchPreview();
    els.candidateTimeToggle.textContent = "返回相关度排序";
    renderSearchStatus();
    loadCandidateTimeBrowse(false);
  }

  function loadCandidateTimeBrowse(continuation) {
    var browse = state.search.timeBrowse;
    if (state.search.loading || !state.search.sessionId || (continuation && !(browse.cursor || browse.retryCursor))) return Promise.resolve(false);
    state.search.loading = true;
    var sessionId = state.search.sessionId;
    var requestCursor = continuation ? (browse.cursor || browse.retryCursor) : null;
    var pageSize = Number(els.queryPageSize.value) || DEFAULT_PAGE_SIZE;
    var query = new URLSearchParams({ limit: String(Math.max(1, Math.min(100, pageSize))) });
    if (requestCursor) query.set("cursor", requestCursor);
    browse.retryCursor = null;
    var generation = state.search.requestGeneration;
    var requestSeq = ++browse.requestSeq;
    els.loadMore.disabled = true;
    return request("/search/sessions/" + encodeURIComponent(sessionId) +
      "/time?" + query.toString()).then(function (result) {
      if (!browse.active || requestSeq !== browse.requestSeq ||
          generation !== state.search.requestGeneration || sessionId !== state.search.sessionId) return true;
      var body = result.body || {}, assets = body.assets || [];
      if (!continuation) clear(els.resultGrid);
      var hits = assets.map(function (asset) { return {
        asset_id: asset.asset_id, rank: asset.semantic_rank, score: asset.score,
        matched_by: asset.matched_by, hit_reason: asset.hit_reason,
        matched_text: asset.matched_text, semantic_ready: asset.semantic_ready,
        capture_time: asset.capture_time,
      }; });
      hits.forEach(function (hit) { els.resultGrid.appendChild(buildHit(hit)); });
      if (!continuation) {
        state.search.windowOffset = 0;
        state.search.recycledCount = 0;
        state.search.count = 0;
      }
      state.search.hits = continuation ? state.search.hits.concat(hits) : hits;
      state.search.count += hits.length;
      var removed = window.PhotoScrollPager ? window.PhotoScrollPager.trimGrid(els.resultGrid, 500) : 0;
      if (removed) {
        state.search.hits.splice(0, removed);
        state.search.windowOffset += removed;
        state.search.recycledCount += removed;
      }
      state.search.total = body.total_candidates;
      browse.cursor = body.next_cursor || null;
      browse.retryCursor = null;
      browse.rulesVersion = body.taken_time_rules_version;
      state.search.cursor = browse.cursor;
      els.loadMore.hidden = !browse.cursor;
      if (els.gridSentinel) els.gridSentinel.hidden = !browse.cursor;
      els.loadMore.disabled = false;
      state.search.loading = false;
      if (state.search.pager) state.search.pager.update();
      updateSearchPreviewNavigation();
      browse.total = body.total_candidates;
      browse.truncated = Boolean(body.candidates_truncated);
      renderSearchStatus();
      return true;
    }).catch(function (failure) {
      if (!browse.active || requestSeq !== browse.requestSeq ||
          generation !== state.search.requestGeneration || sessionId !== state.search.sessionId) return true;
      state.search.loading = false;
      els.loadMore.disabled = false;
      if (continuation) {
        browse.retryCursor = requestCursor;
        browse.cursor = null;
        els.loadMore.hidden = !browse.retryCursor;
        if (els.gridSentinel) els.gridSentinel.hidden = true;
      }
      onSearchFailure(failure);
      return false;
    });
  }

  function sendSearch(continuation) {
    if (state.search.mode !== "asset_image") {
      state.search.assetQuery = null;
      if (els.assetQuerySummary) els.assetQuerySummary.hidden = true;
    }
    if (!continuation && photoSelection && state.search.mode !== "image") photoSelection.reset();
    if (state.search.loading && continuation) {
      return Promise.resolve(false);
    }
    if (!continuation) {
      state.search.requestGeneration += 1;
      state.search.confirmedSearch = null;
      state.search.saveReconfirmRequired = false;
      renderResultOperations();
      state.search.loading = false;
      els.loadMore.disabled = false;
      if (state.search.pager) state.search.pager.reset();
      state.search.timeBrowse.active = false;
      state.search.timeBrowse.cursor = null;
      state.search.timeBrowse.retryCursor = null;
      state.search.timeBrowse.previous = null;
      if (els.candidateTimeToggle) {
        els.candidateTimeToggle.hidden = true;
        els.candidateTimeToggle.disabled = true;
      }
    }
    var requestGeneration = state.search.requestGeneration;
    var filters = collectFilters();
    if (filters.problem) {
      setStatus(els.queryStatus, filters.problem, "error");
      return;
    }
    if (continuation && (!state.search.sessionId || !state.search.cursor)) {
      return;
    }
    if (!continuation) {
      invalidateSearchPreview();
    }
    if (continuation) {
      if (state.search.binding && conditionFingerprint(filters) !== state.search.binding) {
        setStatus(
          els.queryStatus,
          "人物、地点或其它条件已经改变：旧会话的游标只能沿用首次查询绑定的条件，不能续页。" +
            "请按当前条件重新搜索。",
          "error"
        );
        els.queryActions.hidden = false;
        return Promise.resolve(false);
      }
    } else {
      // The digest of the conditions this session is opened with; a later continuation is
      // only allowed while they are unchanged.
      state.search.binding = conditionFingerprint(filters);
    }
    state.search.loading = true;
    setStatus(els.queryStatus, continuation ? "正在加载下一页…" : "正在搜索…");
    var promise = state.search.mode === "image"
      ? sendImageSearch(continuation, filters)
      : state.search.mode === "asset_image"
        ? sendSimilarAssetSearch(continuation)
        : sendTextSearch(continuation, filters);
    return promise.then(
      function (body) {
        if (requestGeneration !== state.search.requestGeneration) {
          return true;
        }
        state.search.loading = false;
        if (body && body.needs_place_choice) {
          abortPerfForChoice();
          showPlaceChoices(body);
          return true;
        }
        if (body && body.needs_collection_choice) {
          abortPerfForChoice();
          showCollectionChoices(body);
          return true;
        }
        if (body && body.needs_scope_choice) {
          abortPerfForChoice();
          showScopeChoices(body);
          return true;
        }
        if (body && body.needs_person_choice) {
          abortPerfForChoice();
          showPersonChoices(body);
          return true;
        }
        if (body && body.unsupported_intent) {
          clear(els.resultIntentScope);
          els.resultIntentScope.hidden = false;
          state.search.hits = [];
          state.search.count = 0;
          state.search.total = null;
          state.search.sessionId = null;
          state.search.cursor = null;
          clear(els.resultGrid);
          els.loadMore.hidden = true;
          setStatus(els.queryStatus, String(body.notice || "这组条件暂不支持自动筛选，请改写查询。"), "error");
          return true;
        }
        if (body && body.intent_plan) {
          state.search.intentPlan = body.intent_plan;
        }
        if (body && body.search_scope) {
          state.search.selectedScope = body.search_scope;
        }
        if (body && body.place_scope) {
          state.search.placeAware = {
            original_query: body.original_query || els.queryText.value.trim(),
            content_query: body.content_query || "",
            place_kind: body.place_scope.kind,
            place_key: body.place_scope.key,
            place_name: body.place_scope.name,
            parent: body.place_scope.parent,
            source: body.place_scope.source,
          };
        }
        if (state.search.mode === "image" && !continuation) {
          state.search.imageFile = state.search.pendingImageFile;
          if (photoSelection) photoSelection.reset();
        }
        state.search.pendingImageFile = null;
        renderPage(body, continuation);
        return true;
      },
      function (failure) {
        if (requestGeneration !== state.search.requestGeneration) {
          return true;
        }
        state.search.loading = false;
        if (state.search.mode === "image" && !continuation) {
          var uploadFailureLines = failureLines(failure);
          setStatus(
            els.queryStatus,
            "本次图片搜索没有成功：" + uploadFailureLines.map(function (line) {
              return line.text;
            }).join(" "),
            "error"
          );
          renderNotices(els.resultNotices, uploadFailureLines);
          els.queryActions.hidden = false;
          return false;
        }
        onSearchFailure(failure);
        // The caller learns *whether* the query succeeded instead of seeing a resolved
        // promise and reporting a success line for a refused request.
        return false;
      }
    );
  }

  function sendTextSearch(continuation, filters) {
    var queryText = els.queryText.value.trim();
    var requestId = newRequestId("text");
    var perfSample = !continuation ? perfBegin(requestId, Number(els.queryPageSize.value) || DEFAULT_PAGE_SIZE) : null;
    var literalText = textRetrievalMode() === "text";
    var placeAware = literalText ? null : state.search.placeAware;
    var body = {
      contract_version: CONTRACT_VERSION,
      request_id: requestId,
      availability: filters.availability,
      place_state: filters.placeState,
      include_descendants: filters.includeDescendants,
      page_size: filters.pageSize,
      original_query: placeAware ? placeAware.original_query : queryText,
    };
    if (literalText) body.retrieval_mode = "text";
    if (placeAware) {
      body.content_query = placeAware.content_query;
      if (placeAware.place_cancelled) {
        body.place_cancelled = true;
      } else if (placeAware.place_kind && placeAware.place_key) {
        body.place_kind = placeAware.place_kind;
        body.place_key = placeAware.place_key;
      }
    }
    if (!literalText && state.search.intentPlan) {
      body.intent_plan = state.search.intentPlan;
      body.original_query = state.search.intentPlan.original_query || body.original_query;
      body.content_query = state.search.intentPlan.content_query || "";
    }
    if (state.search.selectedScope && state.search.selectedScope.kind !== "source") {
      body.search_scope = state.search.selectedScope;
    }
    body.favorites_only = Boolean(state.search.favoritesOnly);
    if (queryText) {
      body.text = queryText;
    }
    if (state.search.selectedScope && state.search.selectedScope.kind === "source") {
      body.source_id = Number(state.search.selectedScope.source_id);
    } else if (filters.sourceId !== null) {
      body.source_id = filters.sourceId;
    }
    if (filters.placeIds.length) {
      body.place_ids = filters.placeIds;
    }
    if (filters.personIds.length) {
      // Sent only for a real selection: ascending, de-duplicated positive ids plus the
      // chosen match mode. The server echoes the canonical set it actually used.
      body.person_ids = filters.personIds;
      body.person_match = filters.personMatch;
    }
    if (filters.captureFrom) {
      body.capture_from = filters.captureFrom;
    }
    if (filters.captureTo) {
      body.capture_to_exclusive = filters.captureTo;
    }
    if (continuation) {
      body.search_session_id = state.search.sessionId;
      body.cursor = state.search.cursor;
    } else {
      state.search.lastRequestBody = JSON.parse(JSON.stringify(body));
    }
    return request("/search/place-aware", { method: "POST", body: body, perfDiag: Boolean(perfSample) }).then(function (result) {
      var answer = result.body || {};
      if (perfSample) {
        perfResponse(requestId, result);
        answer.__searchPerfRequestId = requestId;
      }
      return answer;
    }).catch(function (failure) {
      if (perfSample) perfRecordFailure(requestId, failure);
      throw failure;
    });
  }

  function showPlaceChoices(body) {
    clear(els.resultPlaceScope);
    els.resultPlaceScope.hidden = false;
    var label = element("span", "", "检测到同名地点，请选择范围（内容词会保留）：");
    els.resultPlaceScope.appendChild(label);
    (body.place_choices || []).forEach(function (place) {
      var kindLabel = { city: "城市", country: "国家/地区", province: "省级地区", district: "区县" }[place.kind] || place.kind;
      var sourceLabel = { OSM: "OpenStreetMap", GEONAMES: "GeoNames", local: "本地地点", province_index: "地名资料", cn_admin: "中国行政区" }[place.source];
      if (sourceLabel) kindLabel += " · " + sourceLabel;
      var button = element("button", "", place.name + (place.parent ? " · " + place.parent : "") + "（" + kindLabel + "）");
      button.type = "button";
      button.addEventListener("click", function () {
        state.search.placeAware = {
          original_query: body.original_query,
          content_query: place.content_query === undefined ? (body.content_query || "") : place.content_query,
          place_kind: place.kind,
          place_key: place.key,
        };
        state.search.sessionId = null;
        state.search.cursor = null;
        sendSearch(false);
      });
      els.resultPlaceScope.appendChild(button);
    });
    setStatus(els.queryStatus, "地点范围尚未确定，请选择后再搜索。", "error");
  }

  function showPersonChoices(body) {
    clear(els.resultIntentScope);
    els.resultIntentScope.hidden = false;
    els.resultIntentScope.appendChild(element("span", "", "检测到同名人物，请选择："));
    (body.person_choices || []).forEach(function (person) {
      var button = element("button", "", String(person.name || "人物") + " #" + number(person.person_id));
      button.type = "button";
      button.addEventListener("click", function () {
        var plan = Object.assign({}, body.intent_plan || {});
        plan.person_ids = (plan.person_ids || []).concat([Number(person.person_id)]);
        plan.person_names = (plan.person_names || []).concat([String(person.name || "人物")]);
        state.search.intentPlan = plan;
        state.search.sessionId = null;
        state.search.cursor = null;
        sendSearch(false);
      });
      els.resultIntentScope.appendChild(button);
    });
    setStatus(els.queryStatus, "人物范围尚未确定，请选择后再搜索。", "error");
  }

  function showCollectionChoices(body) {
    clear(els.resultIntentScope);
    els.resultIntentScope.hidden = false;
    els.resultIntentScope.appendChild(element("span", "", "检测到多个同名合集，请选择："));
    (body.collection_choices || []).forEach(function (collection) {
      var button = element("button", "", String(collection.label || collection.name || "人物合集"));
      button.type = "button";
      button.addEventListener("click", function () {
        var plan = collection.intent_plan || { original_query: body.original_query,
          collection_scope: { collection_id: Number(collection.collection_id),
            instance_token: String(collection.instance_token), revision: Number(collection.revision),
            name: String(collection.name) },
          collection_query: "", content_query: "", capture_from: null,
          capture_to_exclusive: null, person_ids: [], person_match: "any", person_names: [],
          place_kind: null, place_key: null };
        state.search.intentPlan = plan;
        state.search.sessionId = null;
        state.search.cursor = null;
        sendSearch(false);
      });
      els.resultIntentScope.appendChild(button);
    });
    setStatus(els.queryStatus, "人物合集范围尚未确定，请选择后再搜索。", "error");
  }

  function showScopeChoices(body) {
    clear(els.resultIntentScope);
    els.resultIntentScope.hidden = false;
    var choices = body.scope_choices || [];
    var peopleOnly = choices.length > 0 && choices.every(function (choice) { return choice.type === "person"; });
    var placesOnly = choices.length > 0 && choices.every(function (choice) { return choice.type === "place"; });
    var sourcesOnly = choices.length > 0 && choices.every(function (choice) { return choice.type === "source"; });
    els.resultIntentScope.appendChild(element("span", "", peopleOnly
      ? "该名称对应多个人物，请选择：" : placesOnly
        ? "该名称对应多个地点，请选择：" : sourcesOnly
          ? "该名称对应多个图库来源，请选择：" : "该名称同时对应不同范围，请选择："));
    choices.forEach(function (choice) {
      var button = element("button", "", String(choice.label || "选择范围"));
      button.type = "button";
      button.addEventListener("click", function () {
        state.search.intentPlan = choice.intent_plan || null;
        if (choice.type === "place" && choice.place) {
          state.search.placeAware = {
            original_query: body.original_query,
            content_query: choice.place.content_query || "",
            place_kind: choice.place.kind, place_key: choice.place.key,
          };
        }
        els.queryText.value = (choice.intent_plan && choice.intent_plan.content_query) || "";
        state.search.sessionId = null;
        state.search.cursor = null;
        state.search.binding = null;
        sendSearch(false);
      });
      els.resultIntentScope.appendChild(button);
    });
    setStatus(els.queryStatus, "请先确定要搜索的人物、合集或地点。", "error");
  }

  function renderIntentChips(plan) {
    clear(els.resultIntentScope);
    renderScopeControls();
    var fixedPlace = state.search.selectedScope && state.search.selectedScope.kind === "place"
      ? state.search.selectedScope : null;
    var plannedPlace = plan && plan.place_kind && plan.place_key ? {
      kind: plan.place_kind, key: plan.place_key, name: plan.place_name,
      source: plan.place_selection && plan.place_selection.source,
      parent: plan.place_parent,
      original_query: plan.original_query,
      content_query: plan.content_query,
    } : null;
    var echoedPlace = state.search.placeAware && state.search.placeAware.place_kind &&
      state.search.placeAware.place_key ? state.search.placeAware : null;
    var samePlace = function (left, right) {
      var identityKind = function (place) {
        var kind = String(place.kind || place.place_kind || "");
        return kind === "place" ? String(place.place_kind || "") : kind;
      };
      return left && right && identityKind(left) === identityKind(right) &&
        String(left.key || left.place_key || "") === String(right.key || right.place_key || "");
    };
    var activePlace = fixedPlace && plannedPlace && !samePlace(fixedPlace, plannedPlace)
      ? plannedPlace : fixedPlace || plannedPlace || echoedPlace;
    var hasPlan = Boolean(plan && (plan.collection_scope || plan.capture_from ||
      plan.capture_status || plan.library_from || plan.library_to_exclusive ||
      (plan.person_ids || []).length || plan.source_id != null)) || Boolean(activePlace);
    if (!hasPlan && !state.search.selectedScope && !state.search.favoritesOnly) {
      els.resultIntentScope.hidden = true;
      return;
    }
    els.resultIntentScope.hidden = false;
    function add(label, remove) {
      els.resultIntentScope.appendChild(element("span", "", label + " "));
      var button = element("button", "", "取消");
      button.type = "button";
      button.addEventListener("click", remove);
      els.resultIntentScope.appendChild(button);
    }
    if (state.search.favoritesOnly) {
      add("仅搜索收藏", function () {
        state.search.favoritesOnly = false;
        if (els.queryFavoritesOnly) els.queryFavoritesOnly.checked = false;
        invalidateSearchForScopeChange("已取消收藏限制，点击搜索后按当前其它条件重新查询。");
      });
    }
    function addActivePlaceChip() {
      if (!activePlace || samePlace(activePlace, fixedPlace)) return;
      var placeSource = activePlace.source === "OSM" ? "（OpenStreetMap）"
        : activePlace.source === "GEONAMES" ? "（GeoNames）" : "";
      var placeParent = activePlace.parent ? " · " + String(activePlace.parent) : "";
      add("地点：" + String(activePlace.name || activePlace.place_name || activePlace.key) +
        placeParent + placeSource, function () {
        cancelAutomaticPlace(activePlace, plan);
      });
    }
    if (state.search.selectedScope) {
      var selected = state.search.selectedScope;
      var scopeLabel = selected.kind === "person" ? "固定相册：" + String(selected.name || "人物")
        : selected.kind === "photo_collection" ? "照片合集：" + String(selected.name || "合集")
        : selected.kind === "collection" ? "固定人物合集：" + String(selected.name || "合集")
          : selected.kind === "source" ? "固定图库：" + String(selected.name || ("#" + selected.source_id))
            : "固定地点：" + String(selected.name || selected.place_key || "地点") +
              (selected.parent ? " · " + String(selected.parent) : "");
      add(scopeLabel, function () {
        if (selected.kind === "place" && state.search.intentPlan &&
            state.search.intentPlan.place_kind && state.search.intentPlan.place_key &&
            !samePlace(selected, {
              kind: state.search.intentPlan.place_kind,
              key: state.search.intentPlan.place_key,
            })) {
          setStatus(els.queryStatus, "固定地点与另一地点条件不一致；请先取消其中一个地点条件。", "error");
          return;
        }
        state.search.selectedScope = null;
        if (selected.kind === "place") {
          var priorPlace = state.search.placeAware || {};
          var priorPlan = state.search.intentPlan || {};
          var place = {
            original_query: priorPlace.original_query || priorPlan.original_query || els.queryText.value.trim(),
            content_query: priorPlace.content_query || priorPlan.content_query || "",
            place_name: priorPlace.place_name || priorPlan.place_name || selected.name,
          };
          var content = ordinaryContentAfterPlaceCancel(place);
          state.search.placeAware = {
            original_query: place.original_query, content_query: content, place_cancelled: true,
          };
          els.queryText.value = content;
        }
        if (selected.kind === "place" && state.search.intentPlan &&
            String(state.search.intentPlan.place_kind || "") === String(selected.place_kind || "") &&
            String(state.search.intentPlan.place_key || "") === String(selected.place_key || "")) {
          clearPlacePlan(state.search.intentPlan, state.search.placeAware.content_query);
        }
        if (selected.kind === "person" && state.search.intentPlan &&
            Object.prototype.hasOwnProperty.call(state.search.intentPlan, "person_match_before_fixed_scope")) {
          // Legacy plans mixed the album person into the query's group. Re-parse them
          // on the next explicit search; current plans keep both conditions separate.
          state.search.intentPlan = null;
        }
        if (selected.kind === "collection" && state.search.intentPlan &&
            state.search.intentPlan.collection_scope &&
            Number(state.search.intentPlan.collection_scope.collection_id) === Number(selected.collection_id) &&
            String(state.search.intentPlan.collection_scope.instance_token || "") === String(selected.instance_token || "") &&
            Number(state.search.intentPlan.collection_scope.revision) === Number(selected.revision)) {
          state.search.intentPlan.collection_scope = null;
        }
        if (selected.kind === "source" && state.search.intentPlan &&
            Number(state.search.intentPlan.source_id) === Number(selected.source_id)) {
          state.search.intentPlan.source_id = null;
          state.search.intentPlan.source_name = null;
        }
        if (selected.kind === "source") els.querySource.value = "";
        state.search.sessionId = null;
        state.search.cursor = null;
        state.search.binding = null;
        state.search.requestGeneration += 1;
        state.search.loading = false;
        state.search.hits = [];
        state.search.lastBody = null;
        state.search.confirmedSearch = null;
        renderResultOperations();
        state.search.lastRequestBody = null;
        state.search.count = 0;
        state.search.total = null;
        state.search.timeBrowse.active = false;
        state.search.timeBrowse.cursor = null;
        state.search.timeBrowse.previous = null;
        clearSearchSnapshot();
        invalidateSearchPreview();
        releaseObjectUrls();
        clear(els.resultGrid);
        clear(els.resultPlaceScope);
        els.resultPlaceScope.hidden = true;
        els.loadMore.hidden = true;
        els.queryActions.hidden = true;
        els.candidateTimeToggle.hidden = true;
        els.candidateTimeToggle.disabled = true;
        renderNotices(els.resultNotices, []);
        renderIntentChips(state.search.intentPlan);
        renderScopeControls();
        setStatus(els.queryStatus, "已取消范围，点击全库搜索重新查询。", "success");
      });
    }
    if (!plan) { addActivePlaceChip(); return; }
    if (!hasPlan) return;
    if (plan.collection_scope && !(state.search.selectedScope && state.search.selectedScope.kind === "collection")) {
      add("人物合集：" + String(plan.collection_scope.name || "合集"), function () {
        plan.collection_scope = null;
        state.search.intentPlan = plan;
        els.queryText.value = plan.content_query || "";
        state.search.sessionId = null;
        state.search.cursor = null;
        state.search.binding = null;
        clear(els.resultGrid);
        sendSearch(false);
      });
    }
    if (plan.capture_from) {
      add("拍摄时间：" + plan.capture_from + " 至 " + plan.capture_to_exclusive + "（不含）", function () {
        plan.capture_from = null;
        plan.capture_to_exclusive = null;
        state.search.intentPlan = plan;
        els.queryText.value = plan.content_query || "";
        sendSearch(false);
      });
    }
    if (plan.time_basis === "library" && (plan.library_from || plan.library_to_exclusive)) {
      add("入库时间：" + String(plan.library_from || "起始") + " 至 " +
        String(plan.library_to_exclusive || "当前") + "（不含）", function () {
        plan.time_basis = "capture"; plan.library_from = null; plan.library_to_exclusive = null;
        state.search.intentPlan = plan; state.search.sessionId = null; state.search.cursor = null;
        state.search.binding = null; sendSearch(false);
      });
    }
    if (plan.capture_status) {
      add(plan.capture_status === "unknown" ? "拍摄日期：未知" : "拍摄日期：待处理", function () {
        plan.capture_status = null; state.search.intentPlan = plan;
        state.search.sessionId = null; state.search.cursor = null; state.search.binding = null;
        sendSearch(false);
      });
    }
    var visiblePersonIds = (plan.person_ids || []).filter(function (id) {
      return !(state.search.selectedScope && state.search.selectedScope.kind === "person" &&
        Number(state.search.selectedScope.person_id) === Number(id));
    });
    if (visiblePersonIds.length) {
      var suppliedNames = Array.isArray(plan.person_names) ? plan.person_names : [];
      var names = visiblePersonIds.map(function (id) {
        var index = plan.person_ids.indexOf(id);
        var supplied = suppliedNames[index];
        if (supplied != null && String(supplied).trim()) {
          return String(supplied);
        }
        var person = state.person.rows[personKey(id)] || state.person.selected[personKey(id)];
        return personDisplayName(person);
      });
      add("人物：" + names.join("、") + (plan.person_match === "all" ? "（合照）" : ""), function () {
        plan.person_ids = [];
        plan.person_names = [];
        plan.person_match = "any";
        state.search.intentPlan = plan;
        els.queryText.value = plan.content_query || "";
        sendSearch(false);
      });
    }
    addActivePlaceChip();
    if (plan.source_id != null && !(state.search.selectedScope && state.search.selectedScope.kind === "source")) {
      add("图库来源：" + String(plan.source_name || ("#" + plan.source_id)), function () {
        plan.source_id = null;
        state.search.intentPlan = plan;
        els.queryText.value = plan.content_query || "";
        state.search.sessionId = null;
        state.search.cursor = null;
        sendSearch(false);
      });
    }
  }

  function invalidateSearchForScopeChange(message) {
    state.search.requestGeneration += 1;
    state.search.loading = false;
    state.search.sessionId = null;
    state.search.cursor = null;
    state.search.binding = null;
    state.search.hits = [];
    state.search.count = 0;
    state.search.total = null;
    state.search.lastBody = null;
    state.search.confirmedSearch = null;
    renderResultOperations();
    state.search.lastRequestBody = null;
    state.search.timeBrowse.active = false;
    state.search.timeBrowse.cursor = null;
    state.search.timeBrowse.retryCursor = null;
    state.search.timeBrowse.previous = null;
    clearSearchSnapshot();
    invalidateSearchPreview();
    releaseObjectUrls();
    clear(els.resultGrid);
    clear(els.resultPlaceScope);
    els.resultPlaceScope.hidden = true;
    els.loadMore.hidden = true;
    els.queryActions.hidden = true;
    els.candidateTimeToggle.hidden = true;
    els.candidateTimeToggle.disabled = true;
    if (state.search.pager) state.search.pager.reset();
    renderIntentChips(state.search.intentPlan);
    renderScopeControls();
    setStatus(els.queryStatus, message, "success");
  }

  function ordinaryContentAfterPlaceCancel(place) {
    var content = String(place.content_query || "").trim();
    if (content) return content;
    var original = String(place.original_query || "").trim();
    if (!original || /(?:在.+?(?:拍摄的|拍的|拍摄))/.test(original)) return "";
    var name = String(place.place_name || place.name || "").trim();
    if (name && (original === name || original === name + "的照片" || original === name + "的图片")) {
      return name;
    }
    return "";
  }

  function clearPlacePlan(plan, content) {
    if (!plan) return;
    plan.place_kind = null;
    plan.place_key = null;
    plan.place_name = null;
    plan.place_selection = null;
    plan.content_query = content;
  }

  function cancelAutomaticPlace(place, plan) {
    var content = ordinaryContentAfterPlaceCancel(place);
    clearPlacePlan(plan, content);
    var hasOtherCondition = Boolean(plan && (plan.collection_scope || plan.capture_from ||
      plan.capture_status || plan.library_from || plan.library_to_exclusive ||
      (plan.person_ids || []).length || plan.source_id != null));
    state.search.intentPlan = hasOtherCondition ? plan : null;
    state.search.placeAware = {
      original_query: String(place.original_query || els.queryText.value.trim()),
      content_query: content,
      place_cancelled: true,
    };
    els.queryText.value = content;
    state.search.requestGeneration += 1;
    state.search.loading = false;
    state.search.sessionId = null;
    state.search.cursor = null;
    state.search.binding = null;
    state.search.hits = [];
    state.search.count = 0;
    state.search.total = null;
    state.search.lastBody = null;
    state.search.confirmedSearch = null;
    renderResultOperations();
    state.search.lastRequestBody = null;
    clearSearchSnapshot();
    invalidateSearchPreview();
    releaseObjectUrls();
    clear(els.resultGrid);
    clear(els.resultPlaceScope);
    els.resultPlaceScope.hidden = true;
    els.loadMore.hidden = true;
    els.queryActions.hidden = true;
    renderIntentChips(state.search.intentPlan);
    if (!content && !hasOtherCondition) {
      setStatus(els.queryStatus, "地点条件已取消，请输入内容后搜索。", "success");
      return;
    }
    sendSearch(false);
  }

  function sendImageSearch(continuation, filters) {
    if (state.search.selectedScope || state.search.favoritesOnly) {
      return Promise.reject({ code: "VALIDATION_ERROR", message: "当前范围仅支持文字/条件搜索，请取消范围后以图搜图。", request_id: "" });
    }
    var file = continuation ? state.search.imageFile : state.search.pendingImageFile;
    if (!file) file = els.uploadFile.files && els.uploadFile.files.length ? els.uploadFile.files[0] : null;
    if (!file) {
      return Promise.reject({
        code: "VALIDATION_ERROR",
        message: "请先选择一个本地图片文件",
        request_id: "",
      });
    }
    var form = new FormData();
    form.append("request_id", newRequestId("image"));
    form.append("availability", filters.availability);
    form.append("place_state", filters.placeState);
    form.append("include_descendants", filters.includeDescendants ? "true" : "false");
    form.append("page_size", String(filters.pageSize));
    if (filters.sourceId !== null) {
      form.append("source_id", String(filters.sourceId));
    }
    if (filters.placeIds.length) {
      form.append("place_ids", filters.placeIds.join(","));
    }
    if (continuation) {
      form.append("search_session_id", state.search.sessionId);
      form.append("cursor", state.search.cursor);
    }
    form.append("image", file, file.name);
    return request("/search/image", { method: "POST", form: form }).then(function (result) {
      return result.body || {};
    });
  }

  function onSearchFailure(failure) {
    if (failure.code === "FAVORITES_REVISION_STALE") {
      invalidateSearchForScopeChange("收藏已变化，请点击搜索重新搜索；旧收藏结果和分页已失效。");
      els.queryActions.hidden = false;
      return;
    }
    if (failure.code === "SEARCH_SESSION_EXPIRED" || failure.code === "INVALID_CURSOR" ||
        failure.code === "PROFILE_GENERATION_MISMATCH") {
      state.search.confirmedSearch = null;
      renderResultOperations();
      state.search.sessionId = null;
      state.search.cursor = null;
      state.search.timeBrowse.cursor = null;
      state.search.timeBrowse.retryCursor = null;
      els.loadMore.hidden = true;
    }
    if (failure.code === "CATALOG_FILTER_REVISION_STALE") {
      state.search.confirmedSearch = null;
      renderResultOperations();
      state.search.sessionId = null;
      state.search.cursor = null;
      state.search.timeBrowse.cursor = null;
      state.search.timeBrowse.retryCursor = null;
      clear(els.resultGrid);
      state.search.hits = [];
      state.search.count = 0;
      els.loadMore.hidden = true;
    }
    if (els.gridSentinel) {
      var nextCursor = state.search.timeBrowse.active ? state.search.timeBrowse.cursor : state.search.cursor;
      els.gridSentinel.hidden = !nextCursor;
    }
    var failureNotices = failureLines(failure);
    var failureReason = (failure.details || {}).reason;
    var modelUnavailable = failureReason === "SEARCH_PROFILE_REQUIRED" || failureReason === "QUERY_ENCODER_UNAVAILABLE";
    setStatus(els.queryStatus, modelUnavailable ? failureNotices[0].text : "本次查询没有成功：" + describe(failure), "error");
    renderNotices(els.resultNotices, failureNotices);
    // A failed request is not a statement about the capture-time state; the previous echo
    // and block belong to a different query, so they are cleared instead of left standing.
    text(els.captureEcho, "");
    setStatus(els.captureState, "");
    // The query is preserved and the page offers a re-run, instead of silently showing an
    // empty grid.
    els.queryActions.hidden = false;
    saveSearchSnapshot();
  }

  function renderPage(body, continuation, windowRestore) {
    clear(els.resultPlaceScope);
    // Confirmed place identity and result echo share one cancelable chip in the
    // intent row. This container remains available only for disambiguation choices.
    els.resultPlaceScope.hidden = true;
    var hits = body.hits || [];
    if (body.asset_query && state.search.mode === "asset_image") {
      state.search.assetQuery = body.asset_query;
      if (els.assetQuerySummary) {
        text(els.assetQueryTitle, "相似照片搜索");
        text(els.assetQueryFilename, body.asset_query.filename || ("资产 #" + body.asset_query.asset_id));
        text(els.assetQueryScope, "搜索范围：全部图库");
        if (body.asset_query.thumbnail_url) {
          els.assetQueryImage.src = body.asset_query.thumbnail_url;
          els.assetQueryImage.hidden = false;
        } else {
          els.assetQueryImage.removeAttribute("src");
          els.assetQueryImage.hidden = true;
        }
        els.assetQuerySummary.hidden = false;
      }
    } else {
      clearSimilarAssetUrl();
      clearAssetQuerySummary();
    }
    // Filter-only and place-album cursors keep every loaded page visible as users continue.
    var filterOnly = body.search_session_id === "filter-only" || String(body.search_session_id || "").indexOf("place-album:") === 0;
    if (!continuation) {
      releaseObjectUrls();
      clear(els.resultGrid);
      state.search.count = windowRestore ? Math.max(0, number(windowRestore.count) - hits.length) : 0;
      state.search.windowOffset = windowRestore ? Math.max(0, number(windowRestore.windowOffset)) : 0;
      state.search.recycledCount = windowRestore ? Math.max(0, number(windowRestore.recycledCount)) : 0;
    }
    hits.forEach(function (hit) {
      els.resultGrid.appendChild(buildHit(hit));
    });
    if (window.PhotoFavorites) window.PhotoFavorites.mount(els.resultGrid, function (change) {
      if (state.search.favoritesOnly && Number(change.changed || 0) > 0) {
        invalidateSearchForScopeChange("收藏已变化，请点击搜索重新搜索；旧收藏结果和分页已失效。");
      }
    });
    state.search.count += hits.length;
    state.search.hits = continuation ? state.search.hits.concat(hits) : hits.slice();
    var removedHits = window.PhotoScrollPager ? window.PhotoScrollPager.trimGrid(els.resultGrid, 500) : 0;
    if (removedHits) {
      state.search.hits.splice(0, removedHits);
      state.search.windowOffset += removedHits;
      state.search.recycledCount += removedHits;
    }
    if (windowRestore && window.PhotoScrollPager && windowRestore.spacerHeight !== undefined &&
        windowRestore.spacerHeight !== null && Number.isFinite(Number(windowRestore.spacerHeight))) {
      window.PhotoScrollPager.setSpacerHeight(els.resultGrid, windowRestore.spacerHeight);
    }
    state.search.lastBody = body;
    if (!continuation) state.search.sessionId = body.search_session_id || null;
    state.search.cursor = body.next_cursor || null;
    updateSearchPreviewNavigation();
    if (!continuation) state.search.total = body.total_candidates;
    if (!continuation && body.search_session_id && (body.query || body.photo_collection_source_token) && !windowRestore) {
      var vector = (body.channels || {}).vector || {};
      state.search.confirmedSearch = {
        sessionId: body.search_session_id,
        fingerprint: conditionFingerprint(collectFilters()),
        query: body.query || null,
        retrievalMode: body.retrieval_mode || textRetrievalMode(),
        profileId: body.active_profile_id || body.profile_id ||
          (state.capabilities && state.capabilities.search && state.capabilities.search.active_profile_id) || null,
        generationId: body.active_image_generation_id || body.image_generation_id || body.generation_id || null,
        sourceToken: body.photo_collection_source_token || null,
        sourceMode: body.photo_collection_source_mode || null,
        strongMatch: vector.engaged && vector.strong_match_available !== false
          ? { available:true, count:vector.strong_match_count, more:Boolean(vector.strong_match_more_possible) }
          : { available:false, unavailable:Boolean(vector.engaged && vector.strong_match_available === false) },
        total: body.total_candidates,
        truncated: Boolean(body.candidates_truncated),
        candidateLimit: body.candidate_limit || 5000,
      };
    }
    renderResultOperations();
    var hasMore = state.search.timeBrowse.active
      ? Boolean(state.search.timeBrowse.cursor || state.search.timeBrowse.retryCursor)
      : Boolean(body.has_more);
    els.loadMore.hidden = !hasMore;
    if (els.gridSentinel) els.gridSentinel.hidden = !hasMore;
    els.candidateTimeToggle.hidden = !(
      body.query && body.search_session_id && hits.length && state.search.mode === "text"
    );
    els.candidateTimeToggle.disabled = false;
    if (!state.search.timeBrowse.active) {
      state.search.timeBrowse.cursor = null;
      state.search.timeBrowse.previous = null;
      els.candidateTimeToggle.textContent = candidateTimeLabel();
    } else {
      els.candidateTimeToggle.textContent = "返回相关度排序";
    }
    els.queryActions.hidden = true;
    var notices = successNotices(body);
    if (body.place_parse_notice) {
      notices.unshift({ kind: "informational", text: body.place_parse_notice });
    }
    renderIntentChips(state.search.intentPlan);
    renderNotices(els.resultNotices, notices);
    renderPersonEcho(body.filters, state.search.mode);
    renderCaptureEcho(body.filters, state.search.mode);
    state.search.statusFilterOnly = filterOnly;
    state.search.statusHasMore = Boolean(body.has_more);
    state.search.statusEmpty = !hits.length;
    state.search.statusEmptyNote = "";
    if (!hits.length) {
      var capture = captureCondition(body);
      if (capture.present && capture.block && capture.block.state !== "READY") {
        state.search.statusEmptyNote = "（拍摄时间状态见上方说明，不代表没有这些照片）";
      } else if (capture.present) {
        state.search.statusEmptyNote = "（拍摄时间已抽取完成，这个范围内确实没有命中）";
      } else if (body.place_scope) {
        state.search.statusEmptyNote = "当前地点范围内没有匹配照片；可取消地点条件或修改查询。没有自动扩大范围。";
      }
    }
    renderSearchStatus();
    if (state.search.pager) state.search.pager.update();
    saveSearchSnapshot();
    if (body.__searchPerfRequestId) perfAfterRender(body.__searchPerfRequestId, body);
  }

  function confirmedSearchCanSave(search) {
    if (!search || !search.sourceToken || search.sessionId !== state.search.sessionId) return false;
    if (search.fingerprint !== conditionFingerprint(collectFilters())) return false;
    // Capabilities are a cached UI snapshot and may be absent or stale. The issued token
    // carries the authoritative session/profile/generation binding; the save endpoint
    // confirms those bindings and the server TTL on every operation.
    return true;
  }

  function confirmedSearchIsCurrent(search) {
    if (!state.search.saveReconfirmRequired && state.search.confirmedSearch === search &&
        confirmedSearchCanSave(search)) return true;
    renderResultOperations();
    setStatus(els.queryStatus, "当前保存凭据已过期或搜索条件/模型已变化；请重新搜索后保存。", "error");
    return false;
  }

  function renderResultOperations() {
    var confirmed = state.search.confirmedSearch;
    var valid = confirmedSearchCanSave(confirmed);
    var needsReconfirm = Boolean(state.search.saveReconfirmRequired);
    if (els.resultOperations) els.resultOperations.hidden = !needsReconfirm && (!confirmed || !confirmed.sourceToken);
    if (els.saveSmartCollection) {
      els.saveSmartCollection.hidden = !needsReconfirm && (!confirmed || !confirmed.sourceToken);
      els.saveSmartCollection.disabled = needsReconfirm || Boolean(confirmed && confirmed.sourceToken && !valid);
      els.saveSmartCollection.textContent = smartReplaceTarget ? "用本次搜索替换智能规则" : "保存为智能合集";
    }
    var ranked = valid && confirmed.sourceMode === "ranked_candidates";
    var filtered = valid && confirmed.sourceMode === "all_filter_matches";
    if (els.saveRankedCollection) els.saveRankedCollection.hidden = !ranked;
    if (els.saveAllFilterCollection) els.saveAllFilterCollection.hidden = !filtered;
    if (els.appendRankedCollection) els.appendRankedCollection.hidden = !ranked;
    if (els.appendAllFilterCollection) els.appendAllFilterCollection.hidden = !filtered;
    if (els.moreResultOperations) els.moreResultOperations.hidden = !(ranked || filtered);
    if (els.resultOperationHint) {
      els.resultOperationHint.hidden = !needsReconfirm && (!confirmed || !confirmed.sourceToken || valid);
      if (!els.resultOperationHint.hidden) text(els.resultOperationHint, "当前结果仍可查看，但保存凭据已失效；请重新搜索确认后保存。");
    }
    if (els.smartTimeBrowseHint) els.smartTimeBrowseHint.hidden = !state.search.timeBrowse.active;
  }

  function invalidateConfirmedSearchOnSaveFailure(failure) {
    var code = failure && failure.code;
    var message = String(failure && failure.message || "");
    var tokenExpired = /保存凭据.*过期|凭据.*不属于当前应用会话/.test(message);
    var applicationSessionInvalid = Boolean(failure && failure.status === 401) || code === "SESSION_INVALID";
    var sessionInvalid = applicationSessionInvalid || code === "SEARCH_SESSION_EXPIRED" || code === "INVALID_CURSOR" ||
        code === "CATALOG_FILTER_REVISION_STALE" || code === "PROFILE_GENERATION_MISMATCH" ||
        code === "PHOTO_COLLECTION_REVISION_STALE" || code === "PHOTO_SMART_DEPENDENCY_CHANGED";
    if (sessionInvalid || tokenExpired) {
      state.search.confirmedSearch = null;
      state.search.saveReconfirmRequired = Boolean(!applicationSessionInvalid && (tokenExpired || sessionInvalid));
      if (sessionInvalid) {
        state.search.sessionId = null;
        state.search.cursor = null;
        state.search.timeBrowse.cursor = null;
        state.search.timeBrowse.retryCursor = null;
        if (state.search.timeBrowse.previous) state.search.timeBrowse.previous.cursor = null;
        els.loadMore.hidden = true;
        if (els.gridSentinel) els.gridSentinel.hidden = true;
      }
      renderResultOperations();
    }
  }

  function renderSearchStatus() {
    var confirmed = state.search.confirmedSearch;
    var parts = [];
    if (state.search.timeBrowse.active) {
      var timeTotal = state.search.timeBrowse.total;
      parts.push("按拍摄时间查看当前候选 · 已显示 " + number(state.search.count) + "/" + number(timeTotal) + " 张");
      if (confirmed && confirmed.strongMatch && confirmed.strongMatch.available) {
        parts.push((confirmed.strongMatch.more ? "高度匹配统计来自原始语义搜索（5000+ 张）" :
          "高度匹配统计来自原始语义搜索（约 " + number(confirmed.strongMatch.count) + " 张）"));
      } else if (confirmed && confirmed.strongMatch && confirmed.strongMatch.unavailable) {
        parts.push("原始语义搜索的高度匹配统计不可用");
      }
      if (state.search.timeBrowse.truncated) parts.push("候选可能已截断（最多 " + number(confirmed && confirmed.candidateLimit || 5000) + " 个）");
    } else {
      if (confirmed && confirmed.strongMatch && confirmed.strongMatch.available) {
        parts.push(confirmed.strongMatch.more ? "5000+ 张高度匹配" : "约 " + number(confirmed.strongMatch.count) + " 张高度匹配");
      } else if (confirmed && confirmed.strongMatch && confirmed.strongMatch.unavailable) {
        parts.push("高度匹配统计不可用");
      }
      parts.push("已显示 " + number(state.search.count) + " 行");
      parts.push("候选共 " + number(confirmed ? confirmed.total : state.search.total) + " 个");
      if (confirmed && confirmed.truncated) parts.push("候选可能已截断（最多 " + number(confirmed.candidateLimit) + " 个）");
    }
    if (state.search.recycledCount) parts.push("当前保留最近 " + state.search.hits.length + " 张；前 " + state.search.recycledCount + " 张已回收");
    if (state.search.statusEmpty) parts.push("这一页没有命中的行");
    if (state.search.statusEmptyNote) parts.push(state.search.statusEmptyNote);
    setStatus(els.queryStatus, parts.join(" · "));
    renderResultOperations();
  }

  function clearSearchSnapshot() {
    try {
      window.sessionStorage.removeItem(snapshotStorageKey());
      window.sessionStorage.removeItem(SEARCH_SNAPSHOT_KEY);
      window.sessionStorage.removeItem(SEARCH_RETURN_SOURCE_KEY);
    } catch (_error) {
      // Storage may be disabled by the browser; the search page remains usable.
    }
  }

  function consumeEntryQuery() {
    var params = new URLSearchParams(window.location.search || "");
    if (!params.has("q")) {
      return null;
    }
    var query = String(params.get("q") || "").slice(0, 256);
    params.delete("q");
    var remaining = params.toString();
    var path = window.location.pathname || "/search";
    var hash = window.location.hash || "";
    if (window.history && typeof window.history.replaceState === "function") {
      window.history.replaceState(
        window.history.state,
        "",
        path + (remaining ? "?" + remaining : "") + hash
      );
    }
    return query;
  }

  function consumeEntryScope() {
    var params = new URLSearchParams(window.location.search || "");
    var hasEntry = params.has("scope_entry");
    var token = params.get("scope_entry");
    params.delete("scope_entry");
    var path = window.location.pathname || "/search";
    var hash = window.location.hash || "";
    if (hasEntry && window.history && typeof window.history.replaceState === "function") {
      var remaining = params.toString();
      window.history.replaceState(window.history.state, "",
        path + (remaining ? "?" + remaining : "") + hash);
    }
    if (!hasEntry) return { present: false };
    if (!token) return { present: true, valid: false };
    if (!/^[a-zA-Z0-9_-]{20,80}$/.test(token)) return { present: true, valid: false };
    try {
      var key = "aisearch.search-scope.v1:" + token;
      var value = JSON.parse(window.sessionStorage.getItem(key) || "null");
      window.sessionStorage.removeItem(key);
      if (!value || typeof value !== "object") return { present: true, valid: false };
      if (value.version === 2) {
        var valid = window.SearchScopeEntry && window.SearchScopeEntry.validScope(value.scope) && value.execute === true &&
          typeof value.query === "string" && value.query.length <= 256;
        return { present: true, valid: Boolean(valid), scope: value.scope, query: value.query, execute: value.execute === true };
      }
      return window.SearchScopeEntry && window.SearchScopeEntry.validScope(value)
        ? { present: true, valid: true, scope: value, query: null, execute: false }
        : { present: true, valid: false };
    } catch (_error) { return { present: true, valid: false }; }
  }

  function restoreOrStartSearch() {
    var similar = similarAssetFromUrl();
    if (similar.present) {
      if (!similar.valid || similar.conflicts) {
        setStatus(els.queryStatus, !similar.valid
          ? "相似搜索参考照片编号无效，未执行查询。"
          : "相似搜索入口与文字查询或固定范围入口冲突，未执行查询。", "error");
        return;
      }
      var historyState = window.history && window.history.state;
      if (historyState && historyState.searchSnapshotToken) {
        restoreSearchSnapshot(similar.assetId);
        return;
      }
      if (historyState && historyState.searchSnapshotSaveFailed) {
        clear(els.resultGrid);
        setStatus(els.queryStatus, "这条历史相似搜索没有可恢复的快照，请重新启动相似搜索。", "error");
        return;
      }
      startSimilarAssetSearch(similar.assetId);
      return;
    }
    var entry = consumeEntryScope();
    var query = consumeEntryQuery();
    if (entry.present) {
      if (!entry.valid) {
        state.search.selectedScope = null;
        renderIntentChips(null);
        renderScopeControls();
        setStatus(els.queryStatus, "搜索范围交接已失效或找不到；未执行全库搜索。请回到有效相册重新点击范围搜索。", "error");
        return;
      }
      clearSearchSnapshot();
      if (entry.scope.kind === "favorites") {
        state.search.selectedScope = null;
        state.search.favoritesOnly = true;
        if (els.queryFavoritesOnly) els.queryFavoritesOnly.checked = true;
      } else {
        state.search.selectedScope = entry.scope;
      }
      var entryQuery = entry.query === null ? (query || "") : entry.query;
      els.queryText.value = entryQuery;
      if (entry.scope.kind === "source") els.querySource.value = String(entry.scope.source_id);
      renderIntentChips(null);
      renderScopeControls();
      if (entry.execute) submitSearch();
      else if (entryQuery.trim()) submitSearch();
      else els.queryText.focus();
      return;
    }
    if (query === null) {
      restoreSearchSnapshot();
      return;
    }
    clearSearchSnapshot();
    els.queryText.value = query;
    if (query.trim()) {
      submitSearch();
    } else {
      els.queryText.focus();
    }
  }

  function renderScopeControls() {
    if (!els.searchSubmit) return;
    var selected = state.search.selectedScope;
    els.searchSubmit.textContent = state.search.mode === "asset_image"
      ? "结束相似搜索并按文字搜索"
      : selected ? "搜索当前范围" : "全库搜索";
    if (els.querySource) els.querySource.disabled = Boolean(selected && selected.kind === "source");
  }

  function bindQuerySubmission() {
    var composingQuery = false;
    els.queryText.addEventListener("compositionstart", function () {
      composingQuery = true;
    });
    els.queryText.addEventListener("compositionend", function () {
      composingQuery = false;
    });
    els.queryText.addEventListener("keydown", function (event) {
      if (event.key === "Enter" && (composingQuery || event.isComposing || event.keyCode === 229)) {
        event.preventDefault();
      }
    });
    els.searchForm.addEventListener("submit", function (event) {
      event.preventDefault();
      submitSearch();
    });
  }

  function searchFormSnapshot() {
    return {
      queryText: els.queryText.value,
      retrievalMode: textRetrievalMode(),
      source: els.querySource.value,
      availability: els.queryAvailability.value,
      placeState: els.queryPlaceState.value,
      placeIds: els.queryPlaceIds.value,
      descendants: els.queryDescendants.value,
      captureFrom: els.queryCaptureFrom.value,
      captureTo: els.queryCaptureTo.value,
      pageSize: els.queryPageSize.value,
      personIds: selectedPersonIds(),
      personMatch: els.queryPersonMatch.value,
      favoritesOnly: Boolean(state.search.favoritesOnly),
    };
  }

  function snapshotStorageKey(override) {
    if (override) return override;
    var token = window.history && window.history.state && window.history.state.searchSnapshotToken;
    return token ? SEARCH_SNAPSHOT_KEY + ":" + String(token) : SEARCH_SNAPSHOT_KEY;
  }

  function pruneSearchSnapshots(keepKey, keepCount) {
    var storage = window.sessionStorage;
    var prefix = SEARCH_SNAPSHOT_KEY + ":";
    var keys = [];
    for (var index = 0; index < storage.length; index += 1) {
      var key = storage.key(index);
      if (key && key.indexOf(prefix) === 0 && key !== keepKey) keys.push(key);
    }
    if (keys.length <= keepCount) return;
    keys = keys.map(function (key) {
      var timestamp = 0;
      try { timestamp = Number((JSON.parse(storage.getItem(key) || "{}") || {}).saved_at) || 0; }
      catch (_error) { timestamp = 0; }
      return { key: key, timestamp: timestamp };
    }).sort(function (a, b) { return a.timestamp - b.timestamp; });
    while (keys.length > keepCount) storage.removeItem(keys.shift().key);
  }

  function saveSearchSnapshot(keyOverride) {
    if (!state.search.lastBody || (state.search.mode !== "text" && state.search.mode !== "asset_image")) {
      return false;
    }
    var key = snapshotStorageKey(keyOverride);
    var snapshot = JSON.stringify({
      saved_at: Date.now(),
      mode: state.search.mode,
      assetQueryId: state.search.assetQueryId,
      assetQuery: state.search.assetQuery,
      form: searchFormSnapshot(),
      hits: state.search.hits,
      windowOffset: state.search.windowOffset,
      recycledCount: state.search.recycledCount,
      spacerHeight: window.PhotoScrollPager ? window.PhotoScrollPager.getSpacerHeight(els.resultGrid) : 0,
      search_session_id: state.search.sessionId,
      next_cursor: state.search.cursor,
      count: state.search.count,
      total_candidates: state.search.total,
      placeAware: state.search.placeAware,
      intentPlan: state.search.intentPlan,
      selectedScope: state.search.selectedScope,
      binding: state.search.binding,
      lastBody: state.search.lastBody,
      lastRequestBody: state.search.lastRequestBody,
      previewAssetId: state.search.preview.assetId,
      timeBrowseActive: state.search.timeBrowse.active,
      timeBrowseCursor: state.search.timeBrowse.cursor,
      timeBrowsePrevious: state.search.timeBrowse.previous,
      timeBrowseRulesVersion: state.search.timeBrowse.rulesVersion,
      confirmedSearch: state.search.confirmedSearch,
      saveReconfirmRequired: state.search.saveReconfirmRequired,
      timeBrowseTotal: state.search.timeBrowse.total,
      timeBrowseTruncated: state.search.timeBrowse.truncated,
      timeBrowseRetryCursor: state.search.timeBrowse.retryCursor,
      scrollY: window.scrollY || 0,
    });
    try {
      pruneSearchSnapshots(key, 5);
      window.sessionStorage.setItem(key, snapshot);
      return true;
    } catch (_error) {
      try {
        pruneSearchSnapshots(key, 0);
        window.sessionStorage.setItem(key, snapshot);
        return true;
      } catch (_retryError) {
        return false;
      }
    }
  }

  function restoreSearchSnapshot(expectedSimilarAssetId) {
    var saved;
    var restoreKey = snapshotStorageKey();
    try {
      saved = JSON.parse(window.sessionStorage.getItem(restoreKey) || "null");
      window.sessionStorage.removeItem(SEARCH_RETURN_SOURCE_KEY);
    } catch (_error) {
      clearSearchSnapshot();
      return;
    }
    if (!saved || !saved.form || !Array.isArray(saved.hits) || !saved.lastBody) {
      if (window.history && window.history.state && window.history.state.searchSnapshotSaveFailed) {
        setStatus(els.queryStatus, "上次搜索结果快照未能保存；可从当前条件重新搜索恢复结果。", "error");
      } else if (window.history && window.history.state && window.history.state.searchSnapshotToken) {
        setStatus(els.queryStatus, "搜索结果快照已不可用；可从当前条件重新搜索恢复结果。", "error");
      }
      return;
    }
    if (expectedSimilarAssetId !== undefined &&
        (saved.mode !== "asset_image" || Number(saved.assetQueryId) !== Number(expectedSimilarAssetId))) {
      clear(els.resultGrid);
      clearSearchSnapshot();
      setStatus(els.queryStatus, "历史相似搜索快照与当前参考照片不匹配，请重新搜索。", "error");
      return;
    }
    var routeSimilarAsset = similarAssetFromUrl();
    if (saved.mode === "asset_image" &&
        (!routeSimilarAsset.valid || Number(routeSimilarAsset.assetId) !== Number(saved.assetQueryId))) {
      clear(els.resultGrid);
      clearSearchSnapshot();
      setStatus(els.queryStatus, "历史相似搜索地址与快照不匹配，请重新搜索。", "error");
      return;
    }
    var restoreGeneration = state.search.requestGeneration;
    if (saved.mode === "asset_image" && Number(saved.assetQueryId) > 0) {
      state.search.mode = "asset_image";
      state.search.assetQueryId = Number(saved.assetQueryId);
      state.search.assetQuery = saved.assetQuery || null;
      els.queryText.value = "";
      els.queryPageSize.value = saved.form.pageSize || String(DEFAULT_PAGE_SIZE);
      state.search.sessionId = saved.search_session_id || null;
      state.search.cursor = saved.next_cursor || null;
      state.search.total = saved.total_candidates;
      state.search.lastRequestBody = saved.lastRequestBody || null;
      setSimilarQueryControls(true);
      var savedTopK = saved.lastRequestBody && saved.lastRequestBody.top_k !== undefined
        ? Number(saved.lastRequestBody.top_k) : DEFAULT_SIMILAR_TOP_K;
      var savedPageSize = Number(saved.lastRequestBody && saved.lastRequestBody.page_size);
      if (!saved.search_session_id || !saved.lastRequestBody ||
          !Number.isInteger(savedTopK) || savedTopK < 1 ||
          !Number.isInteger(savedPageSize) || savedPageSize < 1) {
        clearSearchSnapshot();
        setStatus(els.queryStatus, "相似搜索会话无法验证，请重新从照片启动搜索。", "error");
        return;
      }
      request("/search/by-asset/validate", { method: "POST", body: {
        contract_version: CONTRACT_VERSION,
        request_id: newRequestId("return-similar"),
        asset_id: Number(saved.assetQueryId),
        top_k: savedTopK,
        page_size: savedPageSize,
        search_session_id: saved.search_session_id,
      }}).then(function (result) {
        if (restoreGeneration !== state.search.requestGeneration) return;
        var body = result.body || {};
        if (body.valid !== true || Number(body.asset_id) !== Number(saved.assetQueryId) ||
            body.search_session_id !== saved.search_session_id) {
          throw { code: "SNAPSHOT_IDENTITY_MISMATCH" };
        }
        restoreValidatedSearchSnapshot(saved);
      }).catch(function (failure) {
        if (restoreGeneration !== state.search.requestGeneration) return;
        clearSearchSnapshot();
        clear(els.resultGrid);
        state.search.sessionId = null; state.search.cursor = null;
        setStatus(els.queryStatus, "返回时相似搜索会话已过期或参考向量已变化" +
          (failure && failure.code ? "（" + describe(failure) + "）" : "") + "，请重新搜索。", "error");
      });
      return;
    }
    var form = saved.form;
    els.queryText.value = form.queryText || "";
    if (els.queryRetrievalMode) els.queryRetrievalMode.value = form.retrievalMode === "text" ? "text" : "combined";
    els.querySource.value = form.source || "";
    els.queryAvailability.value = form.availability || "any";
    els.queryPlaceState.value = form.placeState || "any";
    els.queryPlaceIds.value = form.placeIds || "";
    els.queryDescendants.value = form.descendants || "true";
    els.queryCaptureFrom.value = form.captureFrom || "";
    els.queryCaptureTo.value = form.captureTo || "";
    els.queryPageSize.value = form.pageSize || String(DEFAULT_PAGE_SIZE);
    els.queryPersonMatch.value = form.personMatch === "all" ? "all" : "any";
    state.person.selected = {};
    (form.personIds || []).forEach(function (personId) {
      var id = Number(personId);
      if (isFinite(id) && id > 0 && Math.floor(id) === id) {
        state.person.selected[personKey(id)] = { person_id: id, name: null };
      }
    });
    updatePersonSelection();
    state.search.mode = "text";
    state.search.assetQueryId = null;
    state.search.assetQuery = null;
    setSimilarQueryControls(false);
    state.search.hits = [];
    state.search.sessionId = saved.search_session_id || null;
    state.search.cursor = saved.next_cursor || null;
    state.search.count = 0;
    state.search.total = saved.total_candidates;
    state.search.placeAware = saved.placeAware || null;
    state.search.intentPlan = saved.intentPlan || null;
    state.search.selectedScope = saved.selectedScope || null;
    state.search.favoritesOnly = Boolean(form.favoritesOnly ||
      (saved.lastRequestBody && saved.lastRequestBody.favorites_only));
    if (els.queryFavoritesOnly) els.queryFavoritesOnly.checked = state.search.favoritesOnly;
    state.search.binding = saved.binding || null;
    state.search.lastBody = null;
    state.search.confirmedSearch = null;
    state.search.lastRequestBody = saved.lastRequestBody || null;
    state.search.timeBrowse.active = Boolean(saved.timeBrowseActive);
    state.search.timeBrowse.cursor = saved.timeBrowseCursor || null;
    state.search.timeBrowse.previous = saved.timeBrowsePrevious || null;
    state.search.timeBrowse.rulesVersion = saved.timeBrowseRulesVersion;
    state.search.timeBrowse.total = saved.timeBrowseTotal;
    state.search.timeBrowse.truncated = Boolean(saved.timeBrowseTruncated);
    state.search.timeBrowse.retryCursor = saved.timeBrowseRetryCursor || null;
    var restoredConfirmed = saved.confirmedSearch || null;
    if (!restoredConfirmed && saved.lastBody && saved.lastBody.photo_collection_source_token) {
      var restoredVector = (saved.lastBody.channels || {}).vector || {};
      restoredConfirmed = {
        sessionId:saved.search_session_id, fingerprint:saved.binding || "",
        query:saved.lastBody.query || null, retrievalMode:saved.lastBody.retrieval_mode || "combined",
        profileId:saved.lastBody.profile_id || null, generationId:saved.lastBody.image_generation_id || null,
        sourceToken:saved.lastBody.photo_collection_source_token,
        sourceMode:saved.lastBody.photo_collection_source_mode,
        strongMatch:restoredVector.engaged && restoredVector.strong_match_available !== false
          ? {available:true,count:restoredVector.strong_match_count,more:Boolean(restoredVector.strong_match_more_possible)}
          : {available:false,unavailable:Boolean(restoredVector.engaged && restoredVector.strong_match_available === false)},
        total:saved.total_candidates, truncated:Boolean(saved.lastBody.candidates_truncated),
        candidateLimit:saved.lastBody.candidate_limit || 5000,
      };
    }
    state.search.confirmedSearch = restoredConfirmed;
    // Snapshot validation confirms the search session, not the cached source token.
    // Keep the results visible, but require a fresh search before enabling save actions.
    state.search.saveReconfirmRequired = Boolean(saved.saveReconfirmRequired ||
      (restoredConfirmed && restoredConfirmed.sourceToken));
    var filterOnlySnapshot = saved.search_session_id === "filter-only" ||
      String(saved.search_session_id || "").indexOf("place-album:") === 0;
    var canRefreshFilterOnly = filterOnlySnapshot && saved.lastRequestBody &&
      typeof saved.lastRequestBody === "object";
    var canValidate = !filterOnlySnapshot && saved.search_session_id && saved.lastBody.query;
    var validation;
    if (canRefreshFilterOnly) {
      var refreshBody = JSON.parse(JSON.stringify(saved.lastRequestBody));
      refreshBody.request_id = newRequestId("return");
      delete refreshBody.search_session_id;
      delete refreshBody.cursor;
      validation = request("/search/place-aware", { method: "POST", body: refreshBody }).then(function (result) {
        return { refreshed: true, body: result.body || {}, requestBody: refreshBody };
      });
    } else if (canValidate) {
      validation = request("/search/sessions/" + encodeURIComponent(saved.search_session_id) + "/time?limit=1")
        .then(function (result) { return { refreshed: false, body: result.body || {} }; });
    } else {
      validation = Promise.reject({ code: "SNAPSHOT_UNVERIFIABLE" });
    }
    validation.then(function (result) {
      if (restoreGeneration !== state.search.requestGeneration) return;
      var currentRulesVersion = result && result.body && result.body.taken_time_rules_version;
      if (!result.refreshed && saved.timeBrowseActive && currentRulesVersion !== saved.timeBrowseRulesVersion) {
        throw { code: "TIME_RULES_VERSION_STALE" };
      }
      if (result.refreshed) {
        state.search.lastRequestBody = result.requestBody;
        state.search.saveReconfirmRequired = false;
        state.search.timeBrowse.active = false;
        state.search.timeBrowse.cursor = null;
        state.search.timeBrowse.previous = null;
        state.search.timeBrowse.rulesVersion = null;
        renderPage(result.body, false);
        window.requestAnimationFrame(function () { window.scrollTo(0, 0); });
      } else {
        restoreValidatedSearchSnapshot(saved);
      }
    }).catch(function (failure) {
      if (restoreGeneration !== state.search.requestGeneration) return;
      clearSearchSnapshot();
      state.search.sessionId = null;
      state.search.cursor = null;
      state.search.lastBody = null;
      state.search.confirmedSearch = null;
      renderResultOperations();
      state.search.lastRequestBody = null;
      state.search.hits = [];
      state.search.timeBrowse.active = false;
      state.search.timeBrowse.cursor = null;
      state.search.timeBrowse.rulesVersion = null;
      clear(els.resultGrid);
      els.loadMore.hidden = true;
      els.queryActions.hidden = false;
      renderIntentChips(state.search.intentPlan);
      var reason = failure && failure.code ? "（" + describe(failure) + "）" : "";
      setStatus(els.queryStatus, "返回时未恢复旧结果" + reason + "。查询条件和范围已恢复，请重新搜索。", "error");
    });
  }

  function restoreValidatedSearchSnapshot(saved) {
    state.search.timeBrowse.total = saved.timeBrowseTotal;
    state.search.timeBrowse.truncated = Boolean(saved.timeBrowseTruncated);
    state.search.timeBrowse.retryCursor = saved.timeBrowseRetryCursor || null;
    var body = Object.assign({}, saved.lastBody, {
      hits: saved.hits,
      search_session_id: saved.search_session_id,
      next_cursor: saved.next_cursor,
      total_candidates: saved.total_candidates,
      has_more: Boolean(saved.search_session_id && saved.next_cursor),
    });
    renderPage(body, false, {
      count: saved.count,
      windowOffset: saved.windowOffset,
      recycledCount: saved.recycledCount,
      spacerHeight: saved.spacerHeight,
    });
    window.requestAnimationFrame(function () {
      window.scrollTo(0, Number(saved.scrollY) || 0);
      if (saved.previewAssetId) openSearchPreview(saved.previewAssetId, findSearchPreviewTrigger(saved.previewAssetId));
    });
  }

  function prepareSearchSimilarReturn() {
    var token = newRequestId("return-similar");
    var saved = saveSearchSnapshot(SEARCH_SNAPSHOT_KEY + ":" + token);
    if (!saved) {
      setStatus(els.queryStatus, "当前搜索结果无法保存返回快照；返回后可能需要重新搜索。", "error");
    }
    try {
      var current = window.history.state && typeof window.history.state === "object"
        ? Object.assign({}, window.history.state) : {};
      if (saved) {
        current.searchSnapshotToken = token;
        delete current.searchSnapshotSaveFailed;
      } else {
        delete current.searchSnapshotToken;
        current.searchSnapshotSaveFailed = true;
      }
      delete current.photoViewerKey;
      window.history.replaceState(current, "", window.location.href);
    } catch (_error) {
      saveSearchSnapshot();
    }
  }

  function prepareSearchDetailReturn() {
    saveSearchSnapshot();
    try {
      window.sessionStorage.setItem(SEARCH_RETURN_SOURCE_KEY, "search");
    } catch (_error) {
      // The snapshot may still restore on browser back when the browser blocks storage writes.
    }
  }

  // ----------------------------------------------------------------------- tiles
  function buildHit(hit) {
    var tile = element("li", "card-tile");
    tile.dataset.assetId = String(hit.asset_id);
    var frame = element("button", "thumb-frame");
    frame.type = "button";
    frame.setAttribute("aria-label", "查看照片，搜索结果第 " + number(hit.rank) + " 名");
    frame.addEventListener("click", function () { openSearchPreview(hit.asset_id, frame); });
    frame.dataset.assetId = String(hit.asset_id);
    if (perfDiag.active) frame.dataset.perfRequestId = perfDiag.active.request_id;
    observeThumbnail(frame);
    tile.appendChild(frame);
    if (window.PhotoFavorites) tile.appendChild(window.PhotoFavorites.button(hit.asset_id));

    var caption = element("div", "tile-caption");
    var head = element("p", "hit-head");
    head.appendChild(element("span", "rank", "第 " + number(hit.rank) + " 名"));
    head.appendChild(element("span", "chip", "资产 #" + hit.asset_id));
    caption.appendChild(head);
    caption.appendChild(
      element("span", "hit-reason", HIT_REASON_TEXT[hit.hit_reason] || String(hit.hit_reason))
    );
    if (hit.capture_time && hit.capture_time.taken_at) {
      caption.appendChild(element("span", "hit-evidence",
        "拍摄时间：" + String(hit.capture_time.taken_at).replace("T", " ")));
    }
    if (hit.matched_text && hit.matched_text.length) {
      caption.appendChild(
        element("span", "hit-evidence", "匹配位置：" + hit.matched_text.map(function (field) {
          return TEXT_FIELD_LABELS[field] || field;
        }).join("、"))
      );
    }
    tile.appendChild(caption);
    return tile;
  }

  function invalidateSearchPreview() {
    state.search.preview.seq += 1;
    state.search.preview.assetId = null;
    state.search.preview.listVersion += 1;
    state.search.preview.moving = false;
    var viewer = state.search.preview.handle;
    if (viewer && viewer.dialog.open) viewer.close();
    state.search.preview.handle = null;
    updateSearchPreviewNavigation();
  }

  function searchPreviewPosition() {
    if (state.search.preview.listSeq !== state.search.preview.listVersion) return -1;
    var target = String(state.search.preview.assetId);
    return state.search.hits.findIndex(function (hit) { return String(hit.asset_id) === target; });
  }

  function updateSearchPreviewNavigation() {
    var viewer = state.search.preview.handle;
    if (!viewer) return;
    var index = searchPreviewPosition();
    var active = index >= 0 && viewer.dialog.open;
    viewer.setPosition(active ? "第 " + (state.search.windowOffset + index + 1) + " 张" : "");
    viewer.setNavigationEnabled(active && index > 0 && !state.search.preview.moving,
      active && !state.search.preview.moving && (index < state.search.hits.length - 1 || Boolean(state.search.cursor)));
  }

  function createSearchPreview(assetId, returnFocus) {
    if (!window.AIPhotoInfo) {
      var fallback = state.search.preview;
      fallback.handle = { dialog: els.searchPreview, image: els.searchPreviewImage,
        close: function () { els.searchPreview.close(); },
        setAsset: function (id) { els.searchPreviewImage.hidden = false; els.searchPreviewImage.src = "/api/v1/assets/" + encodeURIComponent(id) + "/preview"; },
        setPosition: function (value) { text(els.searchPreviewPosition, value); els.searchPreviewNav.hidden = !value; },
        setNavigationEnabled: function (previous, next) { els.searchPreviewPrev.disabled = !previous; els.searchPreviewNext.disabled = !next; } };
      els.searchPreview.showModal(); closeOnBackdrop(els.searchPreview);
      document.getElementById("search-preview-close").addEventListener("click", fallback.handle.close);
      els.searchPreviewPrev.addEventListener("click", function () { moveSearchPreview(-1); });
      els.searchPreviewNext.addEventListener("click", function () { moveSearchPreview(1); });
      els.searchPreview.addEventListener("close", function () { fallback.seq++; fallback.assetId = null; fallback.handle = null; });
      fallback.handle.setAsset(assetId);
      return;
    }
    var extension = element("section", "search-preview-extension");
    var info = element("p", "search-preview-info", "正在读取照片信息…"); info.setAttribute("role", "status");
    var note = element("p", "hint", ""); note.setAttribute("role", "status"); extension.append(info, note);
    els.searchPreviewInfo = info; els.searchPreviewNote = note;
    var preview = state.search.preview;
    preview.handle = window.AIPhotoInfo.open(assetId, {
      title: "搜索结果照片", alt: "搜索结果照片", extension: extension, people: false, returnFocus: returnFocus,
      onFindSimilar: function (id) {
        prepareSearchSimilarReturn();
        window.location.assign("/search?similar_asset=" + encodeURIComponent(id));
      },
      onOpenFullDetails: function (id) {
        prepareSearchDetailReturn();
        window.AIPhotoInfo.navigateToDetails(id, "search");
      },
      onPerson: function (id) {
        prepareSearchDetailReturn();
        window.location.href = "/persons?person_id=" + encodeURIComponent(id) + "&from_photo=photo";
      },
      navigation: { previous: function () { moveSearchPreview(-1); }, next: function () { moveSearchPreview(1); } },
      onFavoriteChanged: function (_id, result) {
        if (window.PhotoFavorites) window.PhotoFavorites.invalidate(els.resultGrid);
        if (state.search.favoritesOnly && Number((result || {}).changed || 0) > 0) {
          invalidateSearchForScopeChange("收藏已变化，请点击搜索重新搜索；旧收藏结果和分页已失效。");
        }
      },
      onClose: function () {
        preview.seq += 1; preview.assetId = null; preview.handle = null;
        updateSearchPreviewNavigation();
      }
    });
  }

  function findSearchPreviewTrigger(assetId) {
    var hit = state.search.hits.find(function (item) { return String(item.asset_id) === String(assetId); });
    var result = hit && els.resultGrid.querySelector('[data-asset-id="' + String(hit.asset_id).replace(/[^0-9]/g, "") + '"]');
    if (!result) return null;
    return result.className === "thumb-frame" ? result : result.querySelector(".thumb-frame");
  }

  function openSearchPreview(assetId, returnFocus) {
    var preview = state.search.preview;
    var seq = ++preview.seq;
    preview.assetId = assetId;
    preview.listSeq = preview.listVersion;
    preview.moving = false;
    if (!preview.handle) createSearchPreview(assetId, returnFocus);
    else preview.handle.setAsset(assetId);
    text(els.searchPreviewNote, "");
    var hit = state.search.hits.find(function (item) { return String(item.asset_id) === String(assetId); });
    text(
      els.searchPreviewInfo,
      hit
        ? "结果第 " + number(hit.rank) + " 名 · 资产 #" + number(hit.asset_id) + " · 正在读取照片信息…"
        : "正在读取照片信息…"
    );
    updateSearchPreviewNavigation();
    request("/assets/" + encodeURIComponent(assetId)).then(function (result) {
      if (seq !== preview.seq || !preview.handle || !preview.handle.dialog.open) return;
      text(els.searchPreviewInfo, photoInfoText(hit, result.body || {}, assetId));
    }).catch(function () {
      if (seq === preview.seq && preview.handle && preview.handle.dialog.open) {
        var prefix = hit
          ? "结果第 " + number(hit.rank) + " 名 · 资产 #" + number(hit.asset_id) + " · "
          : "";
        text(els.searchPreviewInfo, prefix + "照片信息暂时无法读取。");
      }
    });
  }

  function photoInfoText(hit, detail, assetId) {
    var parts = [];
    if (hit) parts.push("结果第 " + number(hit.rank) + " 名");
    parts.push("资产 #" + number(detail.asset_id || assetId));
    if (detail.width && detail.height) parts.push(number(detail.width) + " × " + number(detail.height));
    if (detail.format) parts.push(String(detail.format).toUpperCase());
    if (detail.byte_size !== null && detail.byte_size !== undefined) parts.push(formatBytes(detail.byte_size));
    var taken = detail.taken_time;
    if (taken && taken.taken_at) {
      var timeLabel = taken.label === "file_time" ? "文件时间 " : "拍摄时间 ";
      parts.push(timeLabel + String(taken.taken_at).replace("T", " "));
      if (taken.camera) parts.push("相机 " + taken.camera);
    } else {
      parts.push("未记录拍摄时间");
    }
    if (detail.availability === "missing") parts.push("原文件不可用");
    return parts.join(" · ");
  }

  function formatBytes(value) {
    var bytes = Number(value);
    if (!isFinite(bytes) || bytes < 0) return "";
    if (bytes < 1024) return bytes + " B";
    var units = ["KB", "MB", "GB", "TB"];
    var amount = bytes;
    var unit = -1;
    while (amount >= 1024 && unit < units.length - 1) {
      amount /= 1024;
      unit += 1;
    }
    return amount.toFixed(amount >= 100 ? 0 : amount >= 10 ? 1 : 2) + " " + units[unit];
  }

  function moveSearchPreview(step) {
    var preview = state.search.preview;
    if (preview.moving || !preview.handle || !preview.handle.dialog.open) return;
    var index = searchPreviewPosition();
    if (index < 0 || index + step < 0) return;
    var listVersion = preview.listVersion;
    var previewSeq = preview.seq;
    preview.moving = true;
    updateSearchPreviewNavigation();
    var load = index + step >= state.search.hits.length ? continueSearch() : Promise.resolve(true);
    Promise.resolve(load).then(function (succeeded) {
      if (previewSeq !== preview.seq || listVersion !== preview.listVersion || !preview.handle || !preview.handle.dialog.open) return;
      if (!succeeded) {
        text(els.searchPreviewNote, "下一页结果没有加载成功，请再点“下一张”重试。");
        return;
      }
      var currentIndex = state.search.hits.findIndex(function (hit) {
        return String(hit.asset_id) === String(preview.assetId);
      });
      var next = currentIndex >= 0 ? state.search.hits[currentIndex + step] : null;
      if (next) openSearchPreview(next.asset_id);
    }).finally(function () {
      preview.moving = false;
      updateSearchPreviewNavigation();
    });
  }

  function clearFrame(frame) {
    clear(frame);
  }

  function noteInFrame(frame, className, message) {
    frame.appendChild(element("p", className || "tile-note", message));
  }

  function paintThumbnail(frame) {
    if (frame.dataset.state) {
      return;
    }
    frame.dataset.state = "loading";
    var assetId = frame.dataset.assetId;
    var perfRequestId = frame.dataset.perfRequestId || "";
    noteInFrame(frame, "tile-note", "缩略图加载中…");
    fetchArtifact("/assets/" + encodeURIComponent(assetId) + "/thumbnail")
      .then(function (result) {
        clearFrame(frame);
        frame.dataset.state = result.ok ? "ready" : "failed";
        if (result.ok) {
          var image = document.createElement("img");
          image.alt = "资产 #" + assetId + " 的缩略图";
          image.loading = "lazy";
          image.decoding = "async";
          image.addEventListener("error", function () {
            clearFrame(frame);
            frame.dataset.state = "failed";
            frame.dataset.perfImageState = "failed";
            perfThumb(perfRequestId, assetId, false);
            noteInFrame(frame, "tile-note", "缩略图无法显示（IMAGE_UNREADABLE）");
          });
          image.addEventListener("load", function () {
            Promise.resolve(typeof image.decode === "function" ? image.decode() : undefined).then(function () {
              frame.dataset.perfImageState = "ready";
              perfThumb(perfRequestId, assetId, true);
            }).catch(function () {
              frame.dataset.perfImageState = "failed";
              perfThumb(perfRequestId, assetId, false);
            });
          });
          image.src = result.url;
          frame.appendChild(image);
          if (result.originalAvailable === "false") {
            var badges = element("div", "badges");
            badges.appendChild(element("span", "badge badge-original-missing", "原文件不可用"));
            frame.appendChild(badges);
          }
          return;
        }
        noteInFrame(
          frame,
          "tile-note",
          "缩略图不可用：" + result.code + "（请求编号 " + (result.request_id || "无") + "）"
        );
        frame.dataset.perfImageState = "failed";
        perfThumb(perfRequestId, assetId, false);
      })
      .catch(function () {
        clearFrame(frame);
        frame.dataset.state = "failed";
        frame.dataset.perfImageState = "failed";
        perfThumb(perfRequestId, assetId, false);
        noteInFrame(frame, "tile-note", "缩略图请求失败（NETWORK_ERROR）");
      });
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
          state.observer.unobserve(entry.target);
          paintThumbnail(entry.target);
        });
      },
      { rootMargin: "200px" }
    );
  }

  // --------------------------------------------------------------------- coverage
  function coverageCard(title, rows, note) {
    var card = element("div", "coverage-card");
    card.appendChild(element("h3", null, title));
    var list = element("dl");
    rows.forEach(function (pair) {
      list.appendChild(element("dt", null, pair[0]));
      list.appendChild(element("dd", null, pair[1]));
    });
    card.appendChild(list);
    if (note) {
      card.appendChild(element("p", "note", note));
    }
    return card;
  }

  function loadCoverage() {
    if (state.coverageLoading) return Promise.resolve(false);
    state.coverageLoading = true;
    els.coverageReload.disabled = true;
    els.coverageRefresh.disabled = true;
    clear(els.coverageBody);
    els.coverageBody.appendChild(element("p", "hint", "正在读取覆盖率…"));
    return Promise.all([
      request("/capabilities"),
      request("/places/coverage"),
      request("/stats"),
      request("/sources/summary"),
    ])
      .then(function (results) {
        var capabilities = results[0].body || {};
        var places = results[1].body || {};
        var stats = results[2].body || {};
        var sources = (results[3].body || {}).sources || [];
        state.capabilities = capabilities;
        state.sources = sources;
        renderCoverage(capabilities, places, stats, sources);
        renderBindingHint(capabilities);
        state.coverageLoaded = true;
        els.coverageReload.hidden = true;
        els.coverageRefresh.hidden = false;
        return true;
      })
      .catch(function (failure) {
        clear(els.coverageBody);
        els.coverageBody.appendChild(
          element("p", "error", "读取覆盖率失败：" + describe(failure))
        );
        return false;
      })
      .then(function (loaded) {
        state.coverageLoading = false;
        els.coverageReload.disabled = false;
        els.coverageRefresh.disabled = false;
        if (!loaded && !state.coverageLoaded) els.coverageReload.hidden = false;
        return loaded;
      });
  }

  function renderCoverage(capabilities, places, stats, sources) {
    clear(els.coverageBody);
    var search = capabilities.search;
    var indexing = capabilities.indexing;
    var counts = stats.counts || {};

    els.coverageBody.appendChild(
      coverageCard(
        "1. 文本投影覆盖（P2-04 派生文本层）",
        search
          ? [
              ["已投影文档", number(search.documents)],
              ["待投影资产", number(search.queued_assets)],
              ["状态", PROJECTION_STATE_TEXT[search.state] || safeText(search.state || "未知")],
              ["tokenizer 世代", safeText(search.tokenizer_version || "未知")],
              ["FTS 世代", safeText(search.fts_generation_id || "未知")],
            ]
          : [["说明", "本图库还没有派生搜索层（缺少 0006 迁移）"]],
        "口径：这是从图库权威记录重新派生的文本索引（文件名 + 有效地点文本）条数与滞后队列。" +
          "权威筛选不依赖它，因此它低不代表查询结果错，只代表文本通道可能少一些命中。"
      )
    );

    var pending = search && search.pending_projection ? search.pending_projection : null;
    els.coverageBody.appendChild(
      coverageCard(
        "2. 语义索引覆盖（图文向量）",
        indexing
          ? [
              ["已确认向量条目", number(indexing.semantic_ready)],
              ["待修复条目", number(indexing.pending_repair)],
              ["未确认 outbox", number(indexing.pending)],
              ["已确认 outbox", number(indexing.confirmed)],
              [
                "活动世代",
                (indexing.active_generations || []).length
                  ? indexing.active_generations
                      .map(function (item) {
                        return item.generation_id + "（" + item.state + "）";
                      })
                      .join(" · ")
                  : "无",
              ],
              ["投影状态", pending ? safeText(pending.state || "未知") : "未知"],
              ["投影滞后事件", pending ? number(pending.pending_events) : 0],
            ]
          : [["说明", "本图库还没有产物/索引表（缺少 0003 迁移）"]],
        "口径：只有 index_entries.state=CONFIRMED 的条目才进入语义召回；outbox 未确认说明" +
          "「已算但还没提交」，不是「没有相似结果」。"
      )
    );

    els.coverageBody.appendChild(
      coverageCard(
        "3. 地点覆盖",
        [
          ["资产总数", number(places.assets_total)],
          ["已提取 GPS 事实", number(places.gps_extracted)],
          ["有效 GPS", number(places.gps_valid)],
          ["无 GPS", number(places.gps_absent)],
          ["GPS 非法", number(places.gps_invalid)],
          ["已归类地点", number(places.places_assigned)],
          ["待确认建议", number(places.places_suggested)],
          ["人工未归类", number(places.places_manual_unassigned)],
          ["未归类", number(places.places_unclassified)],
          [
            "离线地名包",
            places.resolver_available ? "可用（只给建议）" : "不可用（新解析被阻塞）",
          ],
        ],
        "口径：机器候选始终是「待确认建议」，不参与有效地点过滤；人工归属/取消带锁，" +
          "不受后续自动解析覆盖。"
      )
    );

    var online = 0;
    var offline = 0;
    sources.forEach(function (source) {
      if (String(source.status) === "ONLINE") {
        online += 1;
      } else {
        offline += 1;
      }
    });
    els.coverageBody.appendChild(
      coverageCard(
        "4. 来源可用性",
        [
          ["登记来源", number(counts.sources)],
          ["在线来源", online],
          ["离线来源", offline],
          ["在库位置", number(counts.locations_present)],
          ["缺失位置", number(counts.locations_missing)],
          ["资产总数", number(counts.assets)],
        ],
        "口径：位置为 MISSING 时原文件不可用，但已生成的缩略图仍可显示；" +
          "资产身份、人工地点与备注不受来源离线影响。"
      )
    );

    els.coverageBody.appendChild(
      element(
        "p",
        "hint",
        "以上四项各自独立，本页不把任何一个换算成「整体 AI 准确率」或加权总分。"
      )
    );
  }

  function renderBindingHint(capabilities) {
    var search = capabilities.search;
    var lines = [];
    if (!search || !search.active_profile_id) {
      lines.push(
        "尚未启用画面搜索模型。可使用“按文字找”查询已索引文字，或使用筛选条件浏览照片。"
      );
    } else {
      var model = search.active_embedding_model;
      lines.push(
        "当前搜索配置 " +
          safeText(search.active_profile_id) +
          " · 图像世代 " +
          safeText(search.active_image_generation_id || "未激活")
      );
      if (model) {
        lines.push(
          "活动语义模型 " +
            safeText(model.display_name || model.provider_id || "未知") +
            "（adapter=" +
            safeText(model.adapter || "未知") +
            " · " +
            safeText(model.dimension === null || model.dimension === undefined
              ? "维度未知"
              : model.dimension + "D") +
            " · 本机" +
            (model.installed ? "已安装" : "未安装：" + safeText(model.reason || "UNKNOWN")) +
            "）"
        );
        if (model.strong_match_available === false) {
          lines.push("该空间没有标定高度匹配统计，本页不显示“高度匹配”句。");
        }
      }
    }
    if (search && search.active_profile_id && search.query_encoder_available !== true) {
      var unavailableHint = search.query_encoder_state === "BUSY"
        ? "建库期间模型暂不可用；资源释放后可重试语义搜索。"
        : search.query_encoder_state === "MODEL_MISSING"
          ? "当前搜索模型文件缺失；请先恢复模型文件。"
          : search.query_encoder_state === "DAMAGED"
            ? "当前搜索模型加载失败（" + safeText(search.query_encoder_failure || "UNKNOWN") + "）；请检查模型状态。"
            : "当前画面搜索模型不可用。";
      lines.push(unavailableHint + "“按文字找”和条件筛选仍可使用。");
    }
    text(els.bindingHint, lines.join(" "));
  }

  var busyCapabilityRefresh = false;
  function refreshBusyEncoderCapability() {
    var search = state.capabilities && state.capabilities.search;
    if (document.hidden || busyCapabilityRefresh || !search || search.query_encoder_state !== "BUSY") return;
    busyCapabilityRefresh = true;
    request("/capabilities")
      .then(function (result) {
        var capabilities = result.body || {};
        if (capabilities.search) {
          state.capabilities = capabilities;
          renderBindingHint(capabilities);
        }
      })
      .catch(function () {})
      .finally(function () { busyCapabilityRefresh = false; });
  }
  document.addEventListener("visibilitychange", refreshBusyEncoderCapability);
  window.addEventListener("focus", refreshBusyEncoderCapability);

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

  //: The person filter needs exactly the read layer the person page needs: the P3-05B schema
  //: in a healthy catalog. A missing face model never disables it - listing and choosing the
  //: persons that already exist needs no inference at all. An unusable read layer disables the
  //: control *and* says so; it is never rendered as "there are no people".
  function personFilterUsable(body) {
    var version = Number(body && body.schema_version);
    return isFinite(version) && version >= 13 && String(body && body.schema_status) === "ok";
  }

  function loadCapabilities() {
    // The person navigation entry reads the same capabilities document; it stays hidden
    // until the catalog really carries the person read layer.
    return request("/capabilities/page").then(
      function (result) {
        var body = result.body || {};
        state.capabilities = body;
        clearNotice();
        revealPersonsNav(body);
        if (personFilterUsable(body)) {
          state.person.enabled = true;
          state.person.reason = "";
          syncPersonControls();
          loadPersons(true);
        } else {
          disablePersonFilter(
            "本图库还没有人物读层（schema " +
              (body.schema_version === null || body.schema_version === undefined
                ? "未知"
                : "v" + body.schema_version) +
              " / " +
              String(body.schema_status) +
              "）：人物筛选已禁用，本次不会发送人物条件。"
          );
        }
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
        text(els.bindingHint, "搜索配置将在搜索时确认；分项覆盖率可按需查看。");
      },
      function (failure) {
        disablePersonFilter(
          "读取程序能力失败：" +
            describe(failure) +
            "；人物筛选已禁用，本次不会发送人物条件。"
        );
        if (failure.code === "SCHEMA_TOO_NEW") {
          showNotice(
            "图库的 schema 版本高于本程序支持的版本（" +
              failure.code +
              "）。请升级程序或改用匹配的数据目录；本页面不会显示空结果来误导你。" +
              "（请求编号 " +
              (failure.request_id || "无") +
              "）"
          );
          return;
        }
        if (failure.status !== 401) {
          text(els.appMeta, "读取程序能力失败：" + describe(failure));
        }
      }
    );
  }

  function loadSources() {
    return request("/sources/summary").then(
      function (result) {
        state.sources = (result.body && result.body.sources) || [];
        renderSourceOptions();
      },
      function () {
        // The coverage panel reports the read failure; the query form stays usable.
      }
    );
  }

  function loadActiveSearchModelLabel() {
    var link = document.getElementById("current-search-model");
    if (!link) return;
    request("/embedding-management/status")
      .then(function (result) {
        var body = result.body || {};
        var active = body.active_profile;
        var provider = (body.providers || []).find(function (item) {
          return active && item.processor_id === active.processor_id &&
            item.space_id === active.space_id && item.generation &&
            item.generation.generation_id === active.generation_id;
        });
        link.textContent = active
          ? "当前：" + String(provider ? provider.display_name : active.provider_id)
          : "当前：未选择模型";
      })
      .catch(function () { link.textContent = "当前：状态不可用"; });
  }

  function renderSourceOptions() {
    var current = els.querySource.value;
    clear(els.querySource);
    var all = element("option", null, "全部来源");
    all.value = "";
    els.querySource.appendChild(all);
    state.sources.forEach(function (source) {
      var option = element("option", null, source.display_name);
      option.value = String(source.source_id);
      els.querySource.appendChild(option);
    });
    els.querySource.value = current;
  }

  function submitUpload(event) {
    if (event) {
      event.preventDefault();
    }
    if (state.search.selectedScope || state.search.favoritesOnly) {
      setStatus(els.uploadStatus, "当前范围仅支持文字/条件搜索，请取消范围后以图搜图。", "error");
      return;
    }
    var filters = collectFilters();
    if (filters.problem) {
      setStatus(els.uploadStatus, filters.problem, "error");
      return;
    }
    if (selectedPersonIds().length || filters.captureFrom || filters.captureTo ||
        els.queryText.value.trim() || state.search.intentPlan || state.search.placeAware) {
      setStatus(els.uploadStatus, "以图搜图暂不支持人物、拍摄时间或文字条件；请先清除这些条件。", "error");
      return;
    }
    var file = els.uploadFile.files && els.uploadFile.files.length ? els.uploadFile.files[0] : null;
    if (!file) {
      setStatus(els.uploadStatus, "请先选择一张图片。", "error");
      return;
    }
    if (state.search.loading) return;
    var previous = {
      mode: state.search.mode, sessionId: state.search.sessionId, cursor: state.search.cursor,
      count: state.search.count, total: state.search.total, binding: state.search.binding,
      assetQueryId: state.search.assetQueryId, assetQuery: state.search.assetQuery,
      hits: state.search.hits, lastBody: state.search.lastBody,
      lastRequestBody: state.search.lastRequestBody,
      timeBrowse: Object.assign({}, state.search.timeBrowse),
      imageFile: state.search.imageFile,
    };
    clearSimilarAssetUrl();
    state.search.assetQueryId = null;
    state.search.assetQuery = null;
    setSimilarQueryControls(false);
    if (els.assetQuerySummary) els.assetQuerySummary.hidden = true;
    state.search.mode = "image";
    state.search.pendingImageFile = file;
    setStatus(els.uploadStatus, "正在上传并校验这张图片…");
    els.uploadSubmit.disabled = true;
    var promise = sendSearch(false);
    var generation = state.search.requestGeneration;
    if (promise) {
      promise.then(function (succeeded) {
        els.uploadSubmit.disabled = false;
        if (generation !== state.search.requestGeneration) return;
        if (succeeded) {
          setStatus(els.uploadStatus, "图片搜索完成。");
          if (els.uploadDialog.open) els.uploadDialog.close();
          els.panelResults.scrollIntoView({ block: "start", behavior: "smooth" });
          return;
        }
        state.search.mode = previous.mode;
        state.search.assetQueryId = previous.assetQueryId;
        state.search.assetQuery = previous.assetQuery;
        setSimilarQueryControls(previous.mode === "asset_image");
        if (els.assetQuerySummary) els.assetQuerySummary.hidden = previous.mode !== "asset_image";
        state.search.sessionId = previous.sessionId;
        state.search.cursor = previous.cursor;
        state.search.count = previous.count;
        state.search.total = previous.total;
        state.search.binding = previous.binding;
        state.search.hits = previous.hits;
        state.search.lastBody = previous.lastBody;
        state.search.lastRequestBody = previous.lastRequestBody;
        state.search.timeBrowse = previous.timeBrowse;
        state.search.imageFile = previous.imageFile;
        state.search.pendingImageFile = null;
        els.loadMore.hidden = !previous.cursor;
        if (els.gridSentinel) els.gridSentinel.hidden = !previous.cursor;
        setStatus(els.uploadStatus, els.queryStatus.textContent || "图片搜索失败。", "error");
        saveSearchSnapshot();
      });
    }
  }

  function resetFilters() {
    var query = els.queryText.value;
    var plan = state.search.intentPlan;
    var placeAware = state.search.placeAware;
    if (!(state.search.selectedScope && state.search.selectedScope.kind === "source")) els.querySource.value = "";
    els.queryAvailability.value = "any";
    els.queryPlaceState.value = "any";
    els.queryPlaceIds.value = "";
    els.queryDescendants.value = "true";
    els.queryCaptureFrom.value = "";
    els.queryCaptureTo.value = "";
    els.queryPageSize.value = String(DEFAULT_PAGE_SIZE);
    clearPersonSelection();
    setStatus(els.captureState, "");
    text(els.captureEcho, "");

    if (state.search.mode === "image") {
      setStatus(els.queryStatus, "高级筛选已清除；以图搜图不使用这些条件。重新提交文字查询后生效。");
      return;
    }
    if (!query.trim() && !plan && !placeAware) {
      state.search.requestGeneration += 1;
      state.search.loading = false;
      clearSearchSnapshot();
      state.search.hits = [];
      state.search.lastBody = null;
      state.search.confirmedSearch = null;
      renderResultOperations();
      state.search.sessionId = null;
      state.search.cursor = null;
      state.search.binding = null;
      clear(els.resultGrid);
      clear(els.resultPlaceScope);
      els.resultPlaceScope.hidden = true;
      clear(els.resultIntentScope);
      els.resultIntentScope.hidden = true;
      els.loadMore.hidden = true;
      els.queryActions.hidden = true;
      renderNotices(els.resultNotices, []);
      setStatus(els.queryStatus, "高级筛选已清除；输入内容后搜索。");
      return;
    }

    var committedIntent = (plan && plan.original_query === query) ||
      (placeAware && (placeAware.original_query === query || placeAware.content_query === query));
    if (query.trim() && !committedIntent) {
      submitSearch();
      return;
    }
    clearSearchSnapshot();
    invalidateSearchPreview();
    state.search.requestGeneration += 1;
    state.search.loading = false;
    state.search.sessionId = null;
    state.search.cursor = null;
    state.search.count = 0;
    state.search.total = null;
    state.search.hits = [];
    state.search.lastBody = null;
    state.search.confirmedSearch = null;
    renderResultOperations();
    state.search.binding = null;
    releaseObjectUrls();
    clear(els.resultGrid);
    renderNotices(els.resultNotices, []);
    els.loadMore.hidden = true;
    els.queryActions.hidden = true;
    sendSearch(false);
  }

  function clearCaptureRange() {
    els.queryCaptureFrom.value = "";
    els.queryCaptureTo.value = "";
    text(els.captureEcho, "");
    setStatus(els.captureState, "");
  }

  function boot() {
    els = {
      appMeta: document.getElementById("app-meta"),
      notice: document.getElementById("notice"),
      main: document.getElementById("main"),
      bindingHint: document.getElementById("binding-hint"),
      queryStatus: document.getElementById("query-status"),
      queryActions: document.getElementById("query-actions"),
      searchForm: document.getElementById("search-form"),
      searchSubmit: document.getElementById("search-submit"),
      queryText: document.getElementById("query-text"),
      queryFavoritesOnly: document.getElementById("query-favorites-only"),
      queryRetrievalMode: document.getElementById("query-retrieval-mode"),
      resultPlaceScope: document.getElementById("result-place-scope"),
      resultIntentScope: document.getElementById("result-intent-scope"),
      querySource: document.getElementById("query-source"),
      queryAvailability: document.getElementById("query-availability"),
      queryPlaceState: document.getElementById("query-place-state"),
      queryPlaceIds: document.getElementById("query-place-ids"),
      queryDescendants: document.getElementById("query-descendants"),
      queryCaptureFrom: document.getElementById("query-capture-from"),
      queryCaptureTo: document.getElementById("query-capture-to"),
      captureEcho: document.getElementById("capture-echo"),
      captureState: document.getElementById("capture-state"),
      queryPageSize: document.getElementById("query-page-size"),
      queryPersonMatch: document.getElementById("query-person-match"),
      personOptions: document.getElementById("person-options"),
      personMore: document.getElementById("person-more"),
      personClear: document.getElementById("person-clear"),
      personReload: document.getElementById("person-reload"),
      personSelection: document.getElementById("person-selection"),
      personEcho: document.getElementById("person-echo"),
      personStatus: document.getElementById("person-status"),
      resultNotices: document.getElementById("result-notices"),
      resultGrid: document.getElementById("result-grid"),
      photoSelectionToggle: document.getElementById("search-photo-select-toggle"),
      photoSelectionToolbar: document.getElementById("search-photo-selection-toolbar"),
      photoSelectionCount: document.getElementById("search-photo-selection-count"),
      photoSelectAll: document.getElementById("search-photo-select-all"),
      photoSelectInvert: document.getElementById("search-photo-select-invert"),
      photoSelectClear: document.getElementById("search-photo-select-clear"),
      photoSelectAdd: document.getElementById("search-photo-select-add"),
      photoSelectRemove: document.getElementById("search-photo-select-remove"),
      photoSaveCollection: document.getElementById("search-photo-save-collection"),
      photoAppendCollection: document.getElementById("search-photo-append-collection"),
      saveRankedCollection: document.getElementById("search-save-ranked-collection"),
      saveAllFilterCollection: document.getElementById("search-save-all-filter-collection"),
      saveSmartCollection: document.getElementById("search-save-smart-collection"),
      resultOperations: document.getElementById("search-result-operations"),
      moreResultOperations: document.getElementById("search-more-result-operations"),
      resultOperationHint: document.getElementById("search-result-operation-hint"),
      smartTimeBrowseHint: document.getElementById("search-smart-time-browse-hint"),
      appendRankedCollection: document.getElementById("search-append-ranked-collection"),
      appendAllFilterCollection: document.getElementById("search-append-all-filter-collection"),
      gridSentinel: document.getElementById("search-grid-sentinel"),
      loadMore: document.getElementById("load-more"),
      candidateTimeToggle: document.getElementById("candidate-time-toggle"),
      uploadForm: document.getElementById("upload-form"),
      uploadFile: document.getElementById("upload-file"),
      uploadFileName: document.getElementById("upload-file-name"),
      uploadSubmit: document.getElementById("upload-submit"),
      uploadDialog: document.getElementById("upload-dialog"),
      uploadOpen: document.getElementById("open-upload"),
      uploadClose: document.getElementById("upload-close"),
      uploadStatus: document.getElementById("upload-status"),
      panelResults: document.getElementById("panel-results"),
      searchFilterControls: document.getElementById("search-filter-controls"),
      assetQuerySummary: document.getElementById("asset-query-summary"),
      assetQueryTitle: document.getElementById("asset-query-title"),
      assetQueryFilename: document.getElementById("asset-query-filename"),
      assetQueryScope: document.getElementById("asset-query-scope"),
      assetQueryImage: document.getElementById("asset-query-image"),
      coverageBody: document.getElementById("coverage-body"),
      coverageDialog: document.getElementById("coverage-dialog"),
      coverageOpen: document.getElementById("open-coverage"),
      coverageClose: document.getElementById("coverage-close"),
      coverageReload: document.getElementById("coverage-reload"),
      coverageRefresh: document.getElementById("coverage-refresh"),
      searchPreview: document.getElementById("search-preview"),
      searchPreviewImage: document.getElementById("search-preview-image"),
      searchPreviewNav: document.getElementById("search-preview-nav"),
      searchPreviewPrev: document.getElementById("search-preview-prev"),
      searchPreviewNext: document.getElementById("search-preview-next"),
      searchPreviewPosition: document.getElementById("search-preview-position"),
      searchPreviewInfo: document.getElementById("search-preview-info"),
      searchPreviewNote: document.getElementById("search-preview-note"),
    };

    if (window.PhotoBatchSelect) photoSelection = window.PhotoBatchSelect.create(els.resultGrid, {
      toggle: els.photoSelectionToggle, toolbar: els.photoSelectionToolbar, count: els.photoSelectionCount,
      all: els.photoSelectAll, invert: els.photoSelectInvert, clear: els.photoSelectClear,
      add: els.photoSelectAdd, remove: els.photoSelectRemove,
    }, function (body) {
      els.photoSelectionCount.textContent = (body.is_favorite ? "已加入收藏 " : "已取消收藏 ") + body.changed + " 张，" + body.unchanged + " 张已是目标状态。";
      if (state.search.favoritesOnly && Number(body.changed || 0) > 0) {
        invalidateSearchForScopeChange("收藏已变化，请点击搜索重新搜索；旧收藏结果和分页已失效。");
      }
    });
    if (els.photoSaveCollection) els.photoSaveCollection.addEventListener("click", function () {
      var selected = photoSelection ? Array.from(photoSelection.selected, Number) : [];
      if (!selected.length || selected.length > 500) return;
      els.photoSaveCollection.disabled = true;
      var confirmed = state.search.confirmedSearch;
      var query = confirmed && confirmed.query;
      var keyword = query && typeof query === "object" ? query.text : query;
      var suggestedName = Array.from(String(keyword || "").trim()).slice(0, 80).join("");
      window.AIPhotoInfo.actionDialog({kind:"text", title:"保存为照片合集", message:"为已选的 " + selected.length + " 张照片创建合集。", label:"合集名称", value:suggestedName})
        .then(function (name) {
          if (name === null || !name.trim()) return null;
          var requestId = window.crypto && window.crypto.randomUUID
            ? window.crypto.randomUUID() : "pc-" + Date.now() + "-" + Math.random().toString(36).slice(2);
          return request("/photo-collections", {method:"POST", body:{name:name, create_request_id:requestId, asset_ids:selected}});
        })
        .then(function (result) {
          if (!result) return;
          if (photoSelection) photoSelection.reset();
          window.location.href = "/?view=photo-collection&id=" + encodeURIComponent(result.body.collection.collection_id);
        }).catch(function (failure) {
          setStatus(els.queryStatus, "保存照片合集失败：" + describe(failure), "error");
        }).finally(function () { els.photoSaveCollection.disabled = false; });
    });
    function choosePhotoCollection() {
      return request("/photo-collections?limit=50").then(function (result) {
        var collections = (result.body || {}).collections || [];
        if (!collections.length) throw {status:404, message:"还没有照片合集，请先创建一个合集。"};
        return window.AIPhotoInfo.actionDialog({kind:"select", title:"选择照片合集", message:"显示最近 50 个照片合集。", label:"目标合集",
          options:collections.map(function (item) { return {value:item.collection_id, label:item.name + "（" + item.member_count + " 张）"}; })})
          .then(function (selected) {
            return selected === null ? null : collections.find(function (item) { return String(item.collection_id) === String(selected); }) || null;
          });
      });
    }
    if (els.photoAppendCollection) els.photoAppendCollection.addEventListener("click", function () {
      var selected = photoSelection ? Array.from(photoSelection.selected, Number) : [];
      if (!selected.length || selected.length > 500) return;
      els.photoAppendCollection.disabled = true;
      choosePhotoCollection().then(function (collection) {
        if (!collection) return;
        return request("/photo-collections/" + encodeURIComponent(collection.collection_id) + "/members/batch", {
          method:"POST", body:{action:"add", asset_ids:selected, instance_token:collection.instance_token,
            expected_revision:Number(collection.revision)}
        }).then(function (result) {
          if (photoSelection) photoSelection.reset();
          setStatus(els.queryStatus, "已加入「" + collection.name + "」：新增 " + result.body.changed + " 张，已有 " + result.body.unchanged + " 张。", "success");
        });
      }).catch(function (failure) {
        setStatus(els.queryStatus, "加入照片合集失败：" + describe(failure), "error");
      }).finally(function () { els.photoAppendCollection.disabled = false; });
    });
    function saveSearchSnapshot(mode, button) {
      var confirmed = state.search.confirmedSearch;
      if (!confirmedSearchIsCurrent(confirmed) || confirmed.sourceMode !== mode) return;
      button.disabled = true;
      window.AIPhotoInfo.actionDialog({kind:"text", title:"保存搜索结果为照片合集",
        message:mode === "ranked_candidates" ? "按本次服务端排名候选保存；若候选可能被截断，会再次确认。" : "保存全部符合筛选条件的照片，上限 5000 张。",
        label:"合集名称"}).then(function (name) {
        if (name === null || !name.trim()) return null;
        if (!confirmedSearchIsCurrent(confirmed)) return null;
        var requestId = window.crypto && window.crypto.randomUUID
          ? window.crypto.randomUUID() : "pc-" + Date.now() + "-" + Math.random().toString(36).slice(2);
        var sourceToken = confirmed.sourceToken;
        return request("/photo-collections/from-search", {method:"POST", body:{
          name:name, create_request_id:requestId, source_token:sourceToken,
          mode:mode, confirm_truncated:false,
        }}).catch(function (failure) {
          if (failure.status === 409 && failure.code === "PHOTO_COLLECTION_TRUNCATION_CONFIRMATION_REQUIRED") {
            return window.AIPhotoInfo.actionDialog({kind:"confirm", title:"候选结果可能不完整",
              message:"本次候选可能被截断。是否保存当前候选快照？", confirmLabel:"保存当前候选"}).then(function (shouldConfirm) {
              if (!shouldConfirm || !confirmedSearchIsCurrent(confirmed)) return null;
              return request("/photo-collections/from-search", {method:"POST", body:{
                name:name, create_request_id:requestId, source_token:sourceToken,
                mode:mode, confirm_truncated:true,
              }});
            });
          }
          throw failure;
        });
      }).then(function (result) {
        if (!result || !result.body || !result.body.collection) return;
        window.location.href = "/?view=photo-collection&id=" + encodeURIComponent(result.body.collection.collection_id);
      }).catch(function (failure) {
        invalidateConfirmedSearchOnSaveFailure(failure);
        setStatus(els.queryStatus, "保存搜索结果为照片合集失败：" + describe(failure), "error");
      }).finally(function () { renderResultOperations(); });
    }
    if (els.saveRankedCollection) els.saveRankedCollection.addEventListener("click", function () {
      saveSearchSnapshot("ranked_candidates", els.saveRankedCollection);
    });
    if (els.saveAllFilterCollection) els.saveAllFilterCollection.addEventListener("click", function () {
      saveSearchSnapshot("all_filter_matches", els.saveAllFilterCollection);
    });
    smartReplaceTarget = null;
    try {
      smartReplaceTarget = JSON.parse(window.sessionStorage.getItem("photo-smart-replace-target") || "null");
      if (!smartReplaceTarget || !Number.isSafeInteger(Number(smartReplaceTarget.smart_id)) ||
          !smartReplaceTarget.instance_token || !Number.isSafeInteger(Number(smartReplaceTarget.expected_revision))) {
        smartReplaceTarget = null;
      }
    } catch (_error) { smartReplaceTarget = null; }
    if (els.saveSmartCollection) els.saveSmartCollection.addEventListener("click", function () {
      var confirmed = state.search.confirmedSearch;
      if (!confirmedSearchIsCurrent(confirmed)) {
        renderResultOperations();
        return;
      }
      els.saveSmartCollection.disabled = true;
      var replace = smartReplaceTarget;
      var message = replace
        ? "将“" + String(replace.name || "智能合集") + "”切换为本次已确认的规则；原有人工排除会保留。"
        : (confirmed.sourceMode === "ranked_candidates"
          ? "保存本次综合或文字排名规则；候选缓存有上限，之后可手动刷新。"
          : "保存已确认的筛选条件；新照片会在下次读取时按条件出现。");
      if (state.search.timeBrowse.active) message += " 时间查看只是本次候选的临时排序；保存的仍是原始搜索规则。";
      var query = confirmed && confirmed.query;
      var keyword = query && typeof query === "object" ? query.text : query;
      var suggestedName = replace ? String(replace.name || "")
        : Array.from(String(keyword || "").trim()).slice(0, 80).join("");
      window.AIPhotoInfo.actionDialog({kind:"text", title:replace ? "替换智能合集规则" : "保存为智能合集",
        message:message, label:"智能合集名称", value:suggestedName})
        .then(function (name) {
          if (name === null || !name.trim()) return null;
          if (!confirmedSearchIsCurrent(confirmed)) return null;
          var requestId = window.crypto && window.crypto.randomUUID
            ? window.crypto.randomUUID() : "smart-" + Date.now() + "-" + Math.random().toString(36).slice(2);
          var path, requestBody;
          if (replace) {
            path = "/photo-smart-collections/" + encodeURIComponent(replace.smart_id) + "/replace-rule";
            requestBody = {instance_token:replace.instance_token, expected_revision:Number(replace.expected_revision),
              request_id:requestId, source_token:confirmed.sourceToken};
          } else {
            path = "/photo-smart-collections/from-search";
            requestBody = {name:name, create_request_id:requestId, source_token:confirmed.sourceToken};
          }
          return request(path, {method:"POST", body:requestBody});
        }).then(function (result) {
          if (!result || !result.body) return;
          if (replace) window.sessionStorage.removeItem("photo-smart-replace-target");
          var collection = result.body.collection;
          window.location.href = "/?view=photo-smart-collection&id=" + encodeURIComponent(collection.smart_id);
        }).catch(function (failure) {
          invalidateConfirmedSearchOnSaveFailure(failure);
          setStatus(els.queryStatus, "保存智能合集失败：" + describe(failure), "error");
        }).finally(function () { renderResultOperations(); });
    });
    var pendingAppendRequestIds = Object.create(null);
    function appendSearchSnapshot(mode, button) {
      var confirmed = state.search.confirmedSearch;
      if (!confirmedSearchIsCurrent(confirmed) || confirmed.sourceMode !== mode) return;
      button.disabled = true;
      choosePhotoCollection().then(function (collection) {
        if (!collection) return;
        if (!confirmedSearchIsCurrent(confirmed)) return null;
        var pendingKey = String(collection.collection_id) + ":" + String(confirmed.sessionId || "") + ":" + mode;
        var requestId = pendingAppendRequestIds[pendingKey];
        if (!requestId) {
          requestId = window.crypto && window.crypto.randomUUID ? window.crypto.randomUUID()
            : "pc-append-" + Date.now() + "-" + Math.random().toString(36).slice(2);
          pendingAppendRequestIds[pendingKey] = requestId;
        }
        var requestBody = {request_id:requestId, source_token:confirmed.sourceToken, mode:mode,
          confirm_truncated:false, instance_token:collection.instance_token,
          expected_revision:Number(collection.revision)};
        var path = "/photo-collections/" + encodeURIComponent(collection.collection_id) + "/members/from-search";
        return request(path, {method:"POST", body:requestBody}).catch(function (failure) {
          if (failure.status !== 409 || failure.code !== "PHOTO_COLLECTION_TRUNCATION_CONFIRMATION_REQUIRED") throw failure;
          return window.AIPhotoInfo.actionDialog({kind:"confirm", title:"候选结果可能不完整",
            message:"本次排名候选可能被截断。是否追加当前候选快照？", confirmLabel:"追加当前候选"}).then(function (shouldConfirm) {
            if (!shouldConfirm || !confirmedSearchIsCurrent(confirmed)) return null;
            requestBody.confirm_truncated = true;
            return request(path, {method:"POST", body:requestBody});
          });
        }).then(function (result) {
          if (!result) return;
          delete pendingAppendRequestIds[pendingKey];
          setStatus(els.queryStatus, "已追加到「" + collection.name + "」：新增 " + result.body.changed + " 张，已有 " + result.body.unchanged + " 张。", "success");
        });
      }).catch(function (failure) {
        invalidateConfirmedSearchOnSaveFailure(failure);
        setStatus(els.queryStatus, "追加搜索结果失败：" + describe(failure), "error");
      }).finally(function () { renderResultOperations(); });
    }
    if (els.appendRankedCollection) els.appendRankedCollection.addEventListener("click", function () {
      appendSearchSnapshot("ranked_candidates", els.appendRankedCollection);
    });
    if (els.appendAllFilterCollection) els.appendAllFilterCollection.addEventListener("click", function () {
      appendSearchSnapshot("all_filter_matches", els.appendAllFilterCollection);
    });

    bindQuerySubmission();
    els.searchForm.addEventListener("input", function () { renderResultOperations(); });
    els.searchForm.addEventListener("change", function () { renderResultOperations(); });
    if (els.queryFavoritesOnly) {
      els.queryFavoritesOnly.addEventListener("change", function () {
        state.search.favoritesOnly = Boolean(els.queryFavoritesOnly.checked);
        invalidateSearchForScopeChange(state.search.favoritesOnly
          ? "已启用收藏限制，点击搜索后生效；旧结果已清除。"
          : "已取消收藏限制，点击搜索后按当前其它条件重新查询。未自动扩大范围。");
      });
    }
    els.queryRetrievalMode.addEventListener("change", function () {
      if (state.search.mode === "image") {
        setStatus(els.queryStatus, "搜索方式已选择，输入文字后搜索。上传搜图结果仍在下方。");
      } else if (els.queryText.value.trim() || state.search.lastBody || state.search.loading) {
        submitSearch();
      }
    });
    document.getElementById("query-reset").addEventListener("click", resetFilters);
    document.getElementById("capture-clear").addEventListener("click", clearCaptureRange);
    document.getElementById("query-retry").addEventListener("click", function () {
      sendSearch(false);
    });
    state.search.pager = window.PhotoScrollPager ? window.PhotoScrollPager.create({
      sentinel: els.gridSentinel,
      button: els.loadMore,
      loadLabel: "加载下一页",
      retryLabel: "重试加载",
      canLoad: function () {
        var cursor = state.search.timeBrowse.active
          ? (state.search.timeBrowse.cursor || state.search.timeBrowse.retryCursor) : state.search.cursor;
        var previewOpen = state.search.preview.handle && state.search.preview.handle.dialog.open;
        return document.visibilityState !== "hidden" && !state.search.loading && !previewOpen &&
          Boolean(state.search.sessionId && cursor);
      },
      load: continueSearch,
    }) : null;
    els.candidateTimeToggle.addEventListener("click", toggleCandidateTimeBrowse);
    els.personMore.addEventListener("click", function () {
      loadPersons(false);
    });
    els.personClear.addEventListener("click", clearPersonSelection);
    els.personReload.addEventListener("click", function () {
      loadPersons(true);
    });
    els.queryPersonMatch.addEventListener("change", function () {
      text(els.personEcho, "");
      updatePersonSelection();
    });
    els.uploadForm.addEventListener("submit", submitUpload);
    els.uploadOpen.addEventListener("click", function () {
      if (state.search.mode !== "image" || !state.search.loading) {
        setStatus(els.uploadStatus, state.search.loading ? "当前搜索仍在进行中。" : "");
        els.uploadSubmit.disabled = Boolean(state.search.loading);
      }
      els.uploadDialog.showModal();
      els.uploadFile.focus();
    });
    els.uploadClose.addEventListener("click", function () { els.uploadDialog.close(); });
    els.uploadDialog.addEventListener("close", function () { els.uploadOpen.focus(); });
    els.uploadFile.addEventListener("change", function () {
      var file = els.uploadFile.files && els.uploadFile.files.length ? els.uploadFile.files[0] : null;
      text(els.uploadFileName, file ? file.name : "尚未选择文件");
    });
    els.coverageOpen.addEventListener("click", function () {
      closeOnBackdrop(els.coverageDialog);
      els.coverageDialog.showModal();
    });
    els.coverageClose.addEventListener("click", function () { els.coverageDialog.close(); });
    els.coverageDialog.addEventListener("close", function () { els.coverageOpen.focus(); });
    els.coverageReload.addEventListener("click", loadCoverage);
    els.coverageRefresh.addEventListener("click", loadCoverage);

    state.observer = buildObserver();
    updatePersonSelection();
    syncPersonControls();
    var capabilitiesPromise = loadCapabilities();
    loadActiveSearchModelLabel();
    Promise.all([capabilitiesPromise, loadSources()]).then(function () {
      initPerfDiagnostics();
      restoreOrStartSearch();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
