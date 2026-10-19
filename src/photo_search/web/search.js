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
      lastRequestBody: null,
      timeBrowse: { active: false, cursor: null, previous: null, rulesVersion: null, requestSeq: 0 },
      preview: { assetId: null, listVersion: 0, seq: 0, moving: false, handle: null },
    },
    requestCounter: 0,
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
    if (projection.state === "LAGGING") {
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
    if (!body.query && !body.image_query) {
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

  function canRunTextSemantic() {
    var search = state.capabilities && state.capabilities.search;
    if (!search) {
      return false;
    }
    return Boolean(search.active_profile_id) && search.query_encoder_available === true;
  }

  function submitSearch() {
    clearSearchSnapshot();
    state.search.hits = [];
    state.search.windowOffset = 0;
    state.search.recycledCount = 0;
    state.search.lastBody = null;
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
      return;
    }
    if (!state.search.sessionId || !state.search.lastBody || !state.search.lastBody.query) return;
    browse.previous = { hits: state.search.hits.slice(), cursor: state.search.cursor,
      count: state.search.count, total: state.search.total, windowOffset: state.search.windowOffset,
      recycledCount: state.search.recycledCount,
      spacerHeight: window.PhotoScrollPager ? window.PhotoScrollPager.getSpacerHeight(els.resultGrid) : 0 };
    browse.active = true;
    browse.cursor = null;
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
    loadCandidateTimeBrowse(false);
  }

  function loadCandidateTimeBrowse(continuation) {
    var browse = state.search.timeBrowse;
    if (state.search.loading || !state.search.sessionId || (continuation && !browse.cursor)) return Promise.resolve(false);
    state.search.loading = true;
    var sessionId = state.search.sessionId;
    var pageSize = Number(els.queryPageSize.value) || DEFAULT_PAGE_SIZE;
    var query = new URLSearchParams({ limit: String(Math.max(1, Math.min(100, pageSize))) });
    if (continuation) query.set("cursor", browse.cursor);
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
      browse.rulesVersion = body.taken_time_rules_version;
      state.search.cursor = browse.cursor;
      els.loadMore.hidden = !browse.cursor;
      if (els.gridSentinel) els.gridSentinel.hidden = !browse.cursor;
      els.loadMore.disabled = false;
      state.search.loading = false;
      if (state.search.pager) state.search.pager.update();
      updateSearchPreviewNavigation();
      var note = body.candidates_truncated
        ? "候选达到检索上限，当前最多浏览 " + number(body.candidate_limit || 5000) + " 条，可能还有更多匹配"
        : "仅对本次搜索结果排序，不代表完整图库时间轴";
      if (state.search.recycledCount) note += "；已回收较早的 " + state.search.recycledCount + " 张候选";
      setStatus(els.queryStatus, "按拍摄时间整理当前搜索结果 · 已显示 " +
        number(state.search.count) + " / " + number(body.total_candidates) + " · " + note);
      return true;
    }).catch(function (failure) {
      if (!browse.active || requestSeq !== browse.requestSeq ||
          generation !== state.search.requestGeneration || sessionId !== state.search.sessionId) return true;
      state.search.loading = false;
      els.loadMore.disabled = false;
      onSearchFailure(failure);
      return false;
    });
  }

  function sendSearch(continuation) {
    if (!continuation && photoSelection) photoSelection.reset();
    if (state.search.loading && continuation) {
      return Promise.resolve(false);
    }
    if (!continuation) {
      state.search.requestGeneration += 1;
      state.search.loading = false;
      els.loadMore.disabled = false;
      if (state.search.pager) state.search.pager.reset();
      state.search.timeBrowse.active = false;
      state.search.timeBrowse.cursor = null;
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
    var promise =
      state.search.mode === "image"
        ? sendImageSearch(continuation, filters)
        : sendTextSearch(continuation, filters);
    return promise.then(
      function (body) {
        if (requestGeneration !== state.search.requestGeneration) {
          return true;
        }
        state.search.loading = false;
        if (body && body.needs_place_choice) {
          showPlaceChoices(body);
          return true;
        }
        if (body && body.needs_collection_choice) {
          showCollectionChoices(body);
          return true;
        }
        if (body && body.needs_scope_choice) {
          showScopeChoices(body);
          return true;
        }
        if (body && body.needs_person_choice) {
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
        renderPage(body, continuation);
        return true;
      },
      function (failure) {
        if (requestGeneration !== state.search.requestGeneration) {
          return true;
        }
        state.search.loading = false;
        onSearchFailure(failure);
        // The caller learns *whether* the query succeeded instead of seeing a resolved
        // promise and reporting a success line for a refused request.
        return false;
      }
    );
  }

  function sendTextSearch(continuation, filters) {
    var queryText = els.queryText.value.trim();
    var literalText = textRetrievalMode() === "text";
    var placeAware = literalText ? null : state.search.placeAware;
    var body = {
      contract_version: CONTRACT_VERSION,
      request_id: newRequestId("text"),
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
    return request("/search/place-aware", { method: "POST", body: body }).then(function (result) {
      return result.body || {};
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
    state.search.lastRequestBody = null;
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
    var file = els.uploadFile.files && els.uploadFile.files.length ? els.uploadFile.files[0] : null;
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
    if (failure.code === "SEARCH_SESSION_EXPIRED" || failure.code === "INVALID_CURSOR") {
      state.search.sessionId = null;
      state.search.cursor = null;
      state.search.timeBrowse.cursor = null;
      els.loadMore.hidden = true;
    }
    if (failure.code === "CATALOG_FILTER_REVISION_STALE") {
      state.search.sessionId = null;
      state.search.cursor = null;
      state.search.timeBrowse.cursor = null;
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
    state.search.sessionId = body.search_session_id || null;
    state.search.cursor = body.next_cursor || null;
    updateSearchPreviewNavigation();
    state.search.total = body.total_candidates;
    if (els.saveRankedCollection) {
      els.saveRankedCollection.hidden = !body.photo_collection_source_token ||
        body.photo_collection_source_mode !== "ranked_candidates";
    }
    if (els.saveAllFilterCollection) {
      els.saveAllFilterCollection.hidden = !body.photo_collection_source_token ||
        body.photo_collection_source_mode !== "all_filter_matches";
    }
    if (els.saveSmartCollection) {
      els.saveSmartCollection.hidden = !body.photo_collection_source_token;
      els.saveSmartCollection.textContent = smartReplaceTarget ? "用本次搜索替换智能规则" : "保存为智能合集";
    }
    if (els.appendRankedCollection && els.saveRankedCollection) els.appendRankedCollection.hidden = els.saveRankedCollection.hidden;
    if (els.appendAllFilterCollection && els.saveAllFilterCollection) els.appendAllFilterCollection.hidden = els.saveAllFilterCollection.hidden;
    els.loadMore.hidden = !body.has_more;
    if (els.gridSentinel) els.gridSentinel.hidden = !body.has_more;
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
    var parts = filterOnly
      ? ["符合条件共 " + number(body.total_candidates) + " 张",
         "已加载 " + number(state.search.count) + " 张",
         body.has_more ? "还有下一页" : "已到末尾"]
      : ["已显示 " + state.search.count + " 行",
         "候选共 " + number(body.total_candidates) + " 个",
         body.has_more ? "还有下一页" : "已到末尾"];
    var vector = (body.channels || {}).vector || {};
    // This is a read-only count computed from retrieved vector distances. Availability and
    // threshold are representation-space specific; neither changes the returned hits or order.
    if (vector.engaged && vector.strong_match_available !== false) {
      parts.unshift(
        vector.strong_match_more_possible
          ? "5000+ 张高度匹配"
          : "约 " + number(vector.strong_match_count) + " 张高度匹配"
      );
    }
    if (!hits.length) {
      var capture = captureCondition(body);
      if (capture.present && capture.block && capture.block.state !== "READY") {
        parts.push("这一页没有命中的行（拍摄时间状态见上方说明，不代表没有这些照片）");
      } else if (capture.present) {
        parts.push("这一页没有命中的行（拍摄时间已抽取完成，这个范围内确实没有命中）");
      } else if (body.place_scope) {
        parts.push("当前地点范围内没有匹配照片；可取消地点条件或修改查询。没有自动扩大范围。");
      } else {
        parts.push("这一页没有命中的行");
      }
    }
    setStatus(els.queryStatus, parts.join(" · "));
    if (state.search.recycledCount) {
      setStatus(els.queryStatus, parts.join(" · ") + " · 当前保留最近 " + state.search.hits.length +
        " 张；前 " + state.search.recycledCount + " 张已回收，回到列表顶部可按当前条件重新搜索。");
    }
    if (state.search.pager) state.search.pager.update();
    saveSearchSnapshot();
  }

  function clearSearchSnapshot() {
    try {
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
    els.searchSubmit.textContent = selected ? "搜索当前范围" : "全库搜索";
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

  function saveSearchSnapshot() {
    if (!state.search.lastBody || state.search.mode !== "text") {
      return;
    }
    try {
      window.sessionStorage.setItem(
        SEARCH_SNAPSHOT_KEY,
        JSON.stringify({
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
          placeAware: state.search.placeAware,
          previewAssetId: state.search.preview.assetId,
          timeBrowseActive: state.search.timeBrowse.active,
          timeBrowseCursor: state.search.timeBrowse.cursor,
          timeBrowsePrevious: state.search.timeBrowse.previous,
          timeBrowseRulesVersion: state.search.timeBrowse.rulesVersion,
          scrollY: window.scrollY || 0,
        })
      );
    } catch (_error) {
      // Quota or privacy settings can disable storage without affecting current results.
    }
  }

  function restoreSearchSnapshot() {
    var saved;
    try {
      saved = JSON.parse(window.sessionStorage.getItem(SEARCH_SNAPSHOT_KEY) || "null");
      window.sessionStorage.removeItem(SEARCH_RETURN_SOURCE_KEY);
    } catch (_error) {
      clearSearchSnapshot();
      return;
    }
    if (!saved || !saved.form || !Array.isArray(saved.hits) || !saved.lastBody) {
      return;
    }
    var restoreGeneration = state.search.requestGeneration;
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
    state.search.lastRequestBody = saved.lastRequestBody || null;
    state.search.timeBrowse.active = Boolean(saved.timeBrowseActive);
    state.search.timeBrowse.cursor = saved.timeBrowseCursor || null;
    state.search.timeBrowse.previous = saved.timeBrowsePrevious || null;
    state.search.timeBrowse.rulesVersion = saved.timeBrowseRulesVersion;
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
    var open = element("button", "tile-link", "查看详情");
    open.type = "button";
    open.addEventListener("click", function () { openSearchPreview(hit.asset_id, open); });
    caption.appendChild(open);
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
    var frame = hit && els.resultGrid.querySelector('[data-asset-id="' + String(hit.asset_id).replace(/[^0-9]/g, "") + '"]');
    var tile = frame && frame.parentNode;
    return tile && (tile.querySelector(".tile-link") || frame);
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
            noteInFrame(frame, "tile-note", "缩略图无法显示（IMAGE_UNREADABLE）");
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
      })
      .catch(function () {
        clearFrame(frame);
        frame.dataset.state = "failed";
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
      })
      .catch(function (failure) {
        clear(els.coverageBody);
        els.coverageBody.appendChild(
          element("p", "error", "读取覆盖率失败：" + describe(failure))
        );
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
      lines.push(
        "当前画面搜索模型不可用；“按文字找”和条件筛选仍可使用。"
      );
    }
    text(els.bindingHint, lines.join(" "));
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
    state.search.mode = "image";
    clearSearchSnapshot();
    state.search.hits = [];
    state.search.lastBody = null;
    state.search.sessionId = null;
    state.search.cursor = null;
    state.search.count = 0;
    state.search.binding = null;
    releaseObjectUrls();
    clear(els.resultGrid);
    renderNotices(els.resultNotices, []);
    els.loadMore.hidden = true;
    els.queryActions.hidden = true;
    setStatus(els.uploadStatus, "正在上传并校验这张图片…");
    var promise = sendSearch(false);
    if (promise) {
      promise.then(function (succeeded) {
        if (succeeded) {
          setStatus(els.uploadStatus, "上传图查询成功（结果见上面的网格）。");
          return;
        }
        setStatus(els.uploadStatus, "上传图查询没有成功，详见上面的说明。", "error");
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
      appendRankedCollection: document.getElementById("search-append-ranked-collection"),
      appendAllFilterCollection: document.getElementById("search-append-all-filter-collection"),
      gridSentinel: document.getElementById("search-grid-sentinel"),
      loadMore: document.getElementById("load-more"),
      candidateTimeToggle: document.getElementById("candidate-time-toggle"),
      uploadForm: document.getElementById("upload-form"),
      uploadFile: document.getElementById("upload-file"),
      uploadStatus: document.getElementById("upload-status"),
      coverageBody: document.getElementById("coverage-body"),
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
      window.AIPhotoInfo.actionDialog({kind:"text", title:"保存为照片合集", message:"为已选的 " + selected.length + " 张照片创建合集。", label:"合集名称"})
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
      var body = state.search.lastBody || {};
      if (!body.photo_collection_source_token || body.photo_collection_source_mode !== mode) return;
      button.disabled = true;
      window.AIPhotoInfo.actionDialog({kind:"text", title:"保存搜索结果为照片合集",
        message:mode === "ranked_candidates" ? "按本次服务端排名候选保存；若候选可能被截断，会再次确认。" : "保存全部符合筛选条件的照片，上限 5000 张。",
        label:"合集名称"}).then(function (name) {
        if (name === null || !name.trim()) return null;
        var requestId = window.crypto && window.crypto.randomUUID
          ? window.crypto.randomUUID() : "pc-" + Date.now() + "-" + Math.random().toString(36).slice(2);
        var sourceToken = body.photo_collection_source_token;
        return request("/photo-collections/from-search", {method:"POST", body:{
          name:name, create_request_id:requestId, source_token:sourceToken,
          mode:mode, confirm_truncated:false,
        }}).catch(function (failure) {
          if (failure.status === 409 && failure.code === "PHOTO_COLLECTION_TRUNCATION_CONFIRMATION_REQUIRED") {
            return window.AIPhotoInfo.actionDialog({kind:"confirm", title:"候选结果可能不完整",
              message:"本次候选可能被截断。是否保存当前候选快照？", confirmLabel:"保存当前候选"}).then(function (confirmed) {
              if (!confirmed) return null;
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
        setStatus(els.queryStatus, "保存搜索结果为照片合集失败：" + describe(failure), "error");
      }).finally(function () { button.disabled = false; });
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
      var body = state.search.lastBody || {};
      if (!body.photo_collection_source_token) return;
      els.saveSmartCollection.disabled = true;
      var replace = smartReplaceTarget;
      var message = replace
        ? "将“" + String(replace.name || "智能合集") + "”切换为本次已确认的规则；原有人工排除会保留。"
        : (body.photo_collection_source_mode === "ranked_candidates"
          ? "保存本次综合或文字排名规则；候选缓存有上限，之后可手动刷新。"
          : "保存已确认的筛选条件；新照片会在下次读取时按条件出现。");
      window.AIPhotoInfo.actionDialog({kind:"text", title:replace ? "替换智能合集规则" : "保存为智能合集",
        message:message, label:"智能合集名称", value:replace ? String(replace.name || "") : ""})
        .then(function (name) {
          if (name === null || !name.trim()) return null;
          var requestId = window.crypto && window.crypto.randomUUID
            ? window.crypto.randomUUID() : "smart-" + Date.now() + "-" + Math.random().toString(36).slice(2);
          var path, requestBody;
          if (replace) {
            path = "/photo-smart-collections/" + encodeURIComponent(replace.smart_id) + "/replace-rule";
            requestBody = {instance_token:replace.instance_token, expected_revision:Number(replace.expected_revision),
              request_id:requestId, source_token:body.photo_collection_source_token};
          } else {
            path = "/photo-smart-collections/from-search";
            requestBody = {name:name, create_request_id:requestId, source_token:body.photo_collection_source_token};
          }
          return request(path, {method:"POST", body:requestBody});
        }).then(function (result) {
          if (!result || !result.body) return;
          if (replace) window.sessionStorage.removeItem("photo-smart-replace-target");
          var collection = result.body.collection;
          window.location.href = "/?view=photo-smart-collection&id=" + encodeURIComponent(collection.smart_id);
        }).catch(function (failure) {
          setStatus(els.queryStatus, "保存智能合集失败：" + describe(failure), "error");
        }).finally(function () { els.saveSmartCollection.disabled = false; });
    });
    var pendingAppendRequestIds = Object.create(null);
    function appendSearchSnapshot(mode, button) {
      var body = state.search.lastBody || {};
      if (!body.photo_collection_source_token || body.photo_collection_source_mode !== mode) return;
      button.disabled = true;
      choosePhotoCollection().then(function (collection) {
        if (!collection) return;
        var pendingKey = String(collection.collection_id) + ":" + String(body.request_id || "") + ":" + mode;
        var requestId = pendingAppendRequestIds[pendingKey];
        if (!requestId) {
          requestId = window.crypto && window.crypto.randomUUID ? window.crypto.randomUUID()
            : "pc-append-" + Date.now() + "-" + Math.random().toString(36).slice(2);
          pendingAppendRequestIds[pendingKey] = requestId;
        }
        var requestBody = {request_id:requestId, source_token:body.photo_collection_source_token, mode:mode,
          confirm_truncated:false, instance_token:collection.instance_token,
          expected_revision:Number(collection.revision)};
        var path = "/photo-collections/" + encodeURIComponent(collection.collection_id) + "/members/from-search";
        return request(path, {method:"POST", body:requestBody}).catch(function (failure) {
          if (failure.status !== 409 || failure.code !== "PHOTO_COLLECTION_TRUNCATION_CONFIRMATION_REQUIRED") throw failure;
          return window.AIPhotoInfo.actionDialog({kind:"confirm", title:"候选结果可能不完整",
            message:"本次排名候选可能被截断。是否追加当前候选快照？", confirmLabel:"追加当前候选"}).then(function (confirmed) {
            if (!confirmed) return null;
            requestBody.confirm_truncated = true;
            return request(path, {method:"POST", body:requestBody});
          });
        }).then(function (result) {
          if (!result) return;
          delete pendingAppendRequestIds[pendingKey];
          setStatus(els.queryStatus, "已追加到「" + collection.name + "」：新增 " + result.body.changed + " 张，已有 " + result.body.unchanged + " 张。", "success");
        });
      }).catch(function (failure) {
        setStatus(els.queryStatus, "追加搜索结果失败：" + describe(failure), "error");
      }).finally(function () { button.disabled = false; });
    }
    if (els.appendRankedCollection) els.appendRankedCollection.addEventListener("click", function () {
      appendSearchSnapshot("ranked_candidates", els.appendRankedCollection);
    });
    if (els.appendAllFilterCollection) els.appendAllFilterCollection.addEventListener("click", function () {
      appendSearchSnapshot("all_filter_matches", els.appendAllFilterCollection);
    });

    bindQuerySubmission();
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
        var cursor = state.search.timeBrowse.active ? state.search.timeBrowse.cursor : state.search.cursor;
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
    document.getElementById("coverage-reload").addEventListener("click", loadCoverage);

    state.observer = buildObserver();
    updatePersonSelection();
    syncPersonControls();
    var capabilitiesPromise = loadCapabilities();
    Promise.all([capabilitiesPromise, loadSources()]).then(restoreOrStartSearch);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
