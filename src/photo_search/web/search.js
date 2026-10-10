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
  //: One page of the person picker. The page never materialises the whole person list: the
  //: server's own keyset cursor drives "加载更多人物", so a large catalog stays a paged read.
  var PERSON_LIMIT = 25;

  var HIT_REASON_TEXT = {
    TEXT: "文本命中（文件名 / 有效地点文本）",
    SEMANTIC: "图文向量命中",
    "TEXT+SEMANTIC": "文本 + 图文向量命中",
    FILTER_ONLY: "仅筛选（本次没有查询文本，两条通道都没有参与排序）",
  };
  var CHANNEL_TEXT = {
    fts: "文本通道名次",
    vector: "向量通道名次",
    filter: "权威列表次序",
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
      hits: [],
      lastBody: null,
      preview: { assetId: null, listVersion: 0, seq: 0, peopleSeq: 0, moving: false },
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

  function clear(node) {
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
    return node;
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
    if (failure.code === "RUNTIME_CAPABILITY_UNAVAILABLE") {
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
            "语义通道未运行：本进程没有查询编码器，所以这次文本查询没有语义召回" +
            "（RUNTIME_CAPABILITY_UNAVAILABLE / QUERY_ENCODER_UNAVAILABLE）。" +
            "这不是「没有结果」。",
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
      source: filters.sourceId,
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
    state.search.lastBody = null;
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
    renderNotices(els.resultNotices, []);
    text(els.captureEcho, "");
    setStatus(els.captureState, "");
    els.loadMore.hidden = true;
    els.queryActions.hidden = true;
    sendSearch(false);
  }

  function continueSearch() {
    sendSearch(true);
  }

  function sendSearch(continuation) {
    if (state.search.loading) {
      return;
    }
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
        if (body && body.intent_plan) {
          state.search.intentPlan = body.intent_plan;
        }
        if (body && body.place_scope) {
          state.search.placeAware = {
            original_query: body.original_query || els.queryText.value.trim(),
            content_query: body.content_query || "",
            place_kind: body.place_scope.kind,
            place_key: body.place_scope.key,
          };
        }
        renderPage(body, continuation);
        return true;
      },
      function (failure) {
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
    var placeAware = state.search.placeAware;
    var body = {
      contract_version: CONTRACT_VERSION,
      request_id: newRequestId("text"),
      availability: filters.availability,
      place_state: filters.placeState,
      include_descendants: filters.includeDescendants,
      page_size: filters.pageSize,
      original_query: placeAware ? placeAware.original_query : queryText,
    };
    if (placeAware) {
      body.content_query = placeAware.content_query;
      if (placeAware.place_cancelled) {
        body.place_cancelled = true;
      } else if (placeAware.place_kind && placeAware.place_key) {
        body.place_kind = placeAware.place_kind;
        body.place_key = placeAware.place_key;
      }
    }
    if (state.search.intentPlan) {
      body.intent_plan = state.search.intentPlan;
      body.content_query = state.search.intentPlan.content_query || "";
    }
    if (queryText) {
      body.text = queryText;
    }
    if (filters.sourceId !== null) {
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
      var button = element("button", "", String(collection.name || "人物合集"));
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
    els.resultIntentScope.appendChild(element("span", "", "该名称同时对应不同范围，请选择："));
    (body.scope_choices || []).forEach(function (choice) {
      var button = element("button", "", String(choice.label || "选择范围"));
      button.type = "button";
      button.addEventListener("click", function () {
        state.search.intentPlan = choice.intent_plan || null;
        state.search.placeAware = choice.type === "place" && choice.place ? {
          original_query: body.original_query,
          content_query: choice.place.content_query || "",
          place_kind: choice.place.kind, place_key: choice.place.key,
        } : null;
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
    if (!plan || (!plan.collection_scope && !plan.capture_from && !(plan.person_ids || []).length)) {
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
    if (plan.collection_scope) {
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
    if ((plan.person_ids || []).length) {
      var names = plan.person_names || plan.person_ids.map(function (id) { return "#" + id; });
      add("人物：" + names.join("、") + (plan.person_match === "all" ? "（合照）" : ""), function () {
        plan.person_ids = [];
        plan.person_names = [];
        plan.person_match = "any";
        state.search.intentPlan = plan;
        els.queryText.value = plan.content_query || "";
        sendSearch(false);
      });
    }
  }

  function sendImageSearch(continuation, filters) {
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
    if (failure.code === "SEARCH_SESSION_EXPIRED" || failure.code === "INVALID_CURSOR") {
      state.search.sessionId = null;
      state.search.cursor = null;
      els.loadMore.hidden = true;
    }
    if (failure.code === "CATALOG_FILTER_REVISION_STALE") {
      state.search.sessionId = null;
      state.search.cursor = null;
      clear(els.resultGrid);
      state.search.hits = [];
      state.search.count = 0;
      els.loadMore.hidden = true;
    }
    setStatus(els.queryStatus, "本次查询没有成功：" + describe(failure), "error");
    renderNotices(els.resultNotices, failureLines(failure));
    // A failed request is not a statement about the capture-time state; the previous echo
    // and block belong to a different query, so they are cleared instead of left standing.
    text(els.captureEcho, "");
    setStatus(els.captureState, "");
    // The query is preserved and the page offers a re-run, instead of silently showing an
    // empty grid.
    els.queryActions.hidden = false;
    saveSearchSnapshot();
  }

  function cancelPlaceScope(body) {
    state.search.placeAware = {
      original_query: body.original_query || els.queryText.value.trim(),
      content_query: body.content_query || "",
      place_cancelled: true,
    };
    if (state.search.intentPlan) {
      state.search.intentPlan.place_kind = null;
      state.search.intentPlan.place_key = null;
      state.search.intentPlan.content_query = state.search.placeAware.content_query;
    }
    els.queryText.value = state.search.placeAware.content_query;
    state.search.sessionId = null;
    state.search.cursor = null;
    state.search.binding = null;
    clear(els.resultGrid);
  }

  function renderPage(body, continuation) {
    clear(els.resultPlaceScope);
    if (body.place_scope) {
      var scopeSource = body.place_scope.source === "OSM" ? "（OpenStreetMap）"
        : body.place_scope.source === "GEONAMES" ? "（GeoNames）"
        : body.place_scope.origin === "GPS_NEARBY_CITY"
        ? "（GPS附近城市）"
        : String(body.place_scope.key || "").indexOf("cn:") === 0
          ? "（中国行政区）"
          : body.place_scope.kind === "province" ? "（省级地名资料）" : "";
      var chip = element("span", "", "地点：" + body.place_scope.name + scopeSource);
      var cancel = element("button", "", "取消地点条件");
      cancel.type = "button";
      cancel.addEventListener("click", function () {
        cancelPlaceScope(body);
        sendSearch(false);
      });
      els.resultPlaceScope.appendChild(chip);
      els.resultPlaceScope.appendChild(cancel);
      els.resultPlaceScope.hidden = false;
    } else {
      els.resultPlaceScope.hidden = true;
    }
    var hits = body.hits || [];
    var filterOnly = body.search_session_id === "filter-only" || String(body.search_session_id || "").indexOf("place-album:") === 0;
    var visibleStart = Number(body.page_offset) || 0;
    if (!continuation) {
      releaseObjectUrls();
      clear(els.resultGrid);
      state.search.count = 0;
    } else if (filterOnly) {
      // Pure filter browsing keeps one page in the DOM/session snapshot, regardless of total.
      releaseObjectUrls();
      clear(els.resultGrid);
      state.search.count = visibleStart;
    }
    hits.forEach(function (hit) {
      els.resultGrid.appendChild(buildHit(hit));
    });
    state.search.count += hits.length;
    state.search.hits = continuation && !filterOnly ? state.search.hits.concat(hits) : hits.slice();
    state.search.lastBody = body;
    state.search.sessionId = body.search_session_id || null;
    state.search.cursor = body.next_cursor || null;
    updateSearchPreviewNavigation();
    state.search.total = body.total_candidates;
    els.loadMore.hidden = !body.has_more;
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
         "当前显示 " + (visibleStart + 1) + "–" + (visibleStart + hits.length) + " 张",
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

  function searchFormSnapshot() {
    return {
      queryText: els.queryText.value,
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
    };
  }

  function saveSearchSnapshot() {
    if (!state.search.hits.length || !state.search.lastBody || state.search.mode !== "text") {
      return;
    }
    try {
      window.sessionStorage.setItem(
        SEARCH_SNAPSHOT_KEY,
        JSON.stringify({
          form: searchFormSnapshot(),
          hits: state.search.hits,
          search_session_id: state.search.sessionId,
          next_cursor: state.search.cursor,
          count: state.search.count,
          total_candidates: state.search.total,
          placeAware: state.search.placeAware,
          intentPlan: state.search.intentPlan,
          binding: state.search.binding,
          lastBody: state.search.lastBody,
          previewAssetId: state.search.preview.assetId,
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
    var form = saved.form;
    els.queryText.value = form.queryText || "";
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
    state.search.hits = saved.hits;
    state.search.sessionId = saved.search_session_id || null;
    state.search.cursor = saved.next_cursor || null;
    state.search.count = 0;
    state.search.total = saved.total_candidates;
    state.search.placeAware = saved.placeAware || null;
    state.search.intentPlan = saved.intentPlan || null;
    state.search.binding = saved.binding || null;
    state.search.lastBody = saved.lastBody;
    var body = Object.assign({}, saved.lastBody, {
      hits: saved.hits,
      search_session_id: saved.search_session_id,
      next_cursor: saved.next_cursor,
      total_candidates: saved.total_candidates,
      has_more: Boolean(saved.search_session_id && saved.next_cursor),
    });
    renderPage(body, false);
    window.requestAnimationFrame(function () {
      window.scrollTo(0, Number(saved.scrollY) || 0);
      if (saved.previewAssetId) openSearchPreview(saved.previewAssetId);
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
    var frame = element("button", "thumb-frame");
    frame.type = "button";
    frame.setAttribute("aria-label", "查看照片，搜索结果第 " + number(hit.rank) + " 名");
    frame.addEventListener("click", function () { openSearchPreview(hit.asset_id); });
    frame.dataset.assetId = String(hit.asset_id);
    observeThumbnail(frame);
    tile.appendChild(frame);

    var caption = element("div", "tile-caption");
    var head = element("p", "hit-head");
    head.appendChild(element("span", "rank", "第 " + number(hit.rank) + " 名"));
    head.appendChild(element("span", "chip", "资产 #" + hit.asset_id));
    caption.appendChild(head);
    caption.appendChild(
      element("span", "hit-reason", HIT_REASON_TEXT[hit.hit_reason] || String(hit.hit_reason))
    );
    caption.appendChild(
      element(
        "span",
        "hit-evidence",
        "语义就绪：" +
          (hit.semantic_ready ? "是（该世代有已确认的向量条目）" : "否（本次没有已确认的向量条目）")
      )
    );
    var channels = hit.matched_by || {};
    var names = Object.keys(channels);
    if (names.length) {
      var parts = names.map(function (name) {
        return (CHANNEL_TEXT[name] || name) + " " + channels[name];
      });
      caption.appendChild(element("span", "hit-evidence", "通道名次：" + parts.join(" · ")));
    }
    if (hit.matched_text && hit.matched_text.length) {
      caption.appendChild(
        element("span", "hit-evidence", "命中文本列：" + hit.matched_text.join("、"))
      );
    }
    var open = element("a", "tile-link", "查看详情");
    open.href = "/#/asset/" + encodeURIComponent(hit.asset_id);
    open.addEventListener("click", prepareSearchDetailReturn);
    caption.appendChild(open);
    tile.appendChild(caption);
    return tile;
  }

  function invalidateSearchPreview() {
    state.search.preview.seq += 1;
    state.search.preview.assetId = null;
    state.search.preview.listVersion += 1;
    state.search.preview.moving = false;
    if (els.searchPreview && els.searchPreview.open) els.searchPreview.close();
    if (els.searchPreviewImage) els.searchPreviewImage.removeAttribute("src");
    updateSearchPreviewNavigation();
  }

  function searchPreviewPosition() {
    if (state.search.preview.listSeq !== state.search.preview.listVersion) return -1;
    var target = String(state.search.preview.assetId);
    return state.search.hits.findIndex(function (hit) { return String(hit.asset_id) === target; });
  }

  function updateSearchPreviewNavigation() {
    if (!els.searchPreviewNav) return;
    var index = searchPreviewPosition();
    var active = index >= 0 && els.searchPreview.open;
    els.searchPreviewNav.hidden = !active;
    text(els.searchPreviewPosition, active ? "第 " + (index + 1) + " 张" : "");
    els.searchPreviewPrev.disabled = !active || index === 0 || state.search.preview.moving;
    els.searchPreviewNext.disabled = !active || state.search.preview.moving ||
      (index === state.search.hits.length - 1 && !state.search.cursor);
  }

  function openSearchPreview(assetId) {
    var preview = state.search.preview;
    var seq = ++preview.seq;
    if (preview.infoMount) preview.infoMount.dispose();
    preview.assetId = assetId;
    preview.listSeq = preview.listVersion;
    preview.moving = false;
    text(els.searchPreviewNote, "");
    var hit = state.search.hits.find(function (item) { return String(item.asset_id) === String(assetId); });
    text(
      els.searchPreviewInfo,
      hit
        ? "结果第 " + number(hit.rank) + " 名 · 资产 #" + number(hit.asset_id) + " · 正在读取照片信息…"
        : "正在读取照片信息…"
    );
    var image = els.searchPreviewImage;
    image.hidden = false;
    image.onerror = function () {
      if (seq === preview.seq) {
        image.hidden = true;
        text(els.searchPreviewNote, "大图暂时无法显示；照片来源可能已离线。");
      }
    };
    image.src =
      "/api/v1/assets/" + encodeURIComponent(assetId) + "/preview?v=p4-search-person-link-a-1";
    if (!els.searchPreview.open) els.searchPreview.showModal();
    if (window.AIPhotoInfo) preview.infoMount = window.AIPhotoInfo.render(
      document.getElementById("search-preview-photo-info"), assetId, { people: false }
    );
    updateSearchPreviewNavigation();
    loadPreviewPeople(assetId, seq);
    request("/assets/" + encodeURIComponent(assetId)).then(function (result) {
      if (seq !== preview.seq || !els.searchPreview.open) return;
      text(els.searchPreviewInfo, photoInfoText(hit, result.body || {}, assetId));
    }).catch(function () {
      if (seq === preview.seq && els.searchPreview.open) {
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

  function loadPreviewPeople(assetId, previewSeq) {
    var preview = state.search.preview;
    var peopleSeq = ++preview.peopleSeq;
    els.searchPreviewPeople.hidden = false;
    clear(els.searchPreviewPeopleList);
    text(els.searchPreviewPeopleNote, "正在读取已归属人物…");
    request("/assets/" + encodeURIComponent(assetId) + "/faces").then(function (result) {
      if (previewSeq !== preview.seq || peopleSeq !== preview.peopleSeq || !els.searchPreview.open) return null;
      var faces = (result.body && result.body.faces) || [];
      var unique = {};
      faces.forEach(function (face) {
        if (face.assigned_person_id !== null && face.assigned_person_id !== undefined) {
          var key = String(face.assigned_person_id);
          if (!unique[key]) unique[key] = face;
        }
      });
      var entries = Object.keys(unique).map(function (id) {
        var face = unique[id];
        return request("/persons/" + encodeURIComponent(id)).then(function (personResult) {
          return { face: face, person: personResult.body || {} };
        }).catch(function () { return { face: face, person: null }; });
      });
      return Promise.all(entries).then(function (people) {
        return { people: people, hasMoreFaces: Boolean(result.body && result.body.has_more) };
      });
    }).then(function (loaded) {
      if (!loaded || previewSeq !== preview.seq || peopleSeq !== preview.peopleSeq || !els.searchPreview.open) return;
      if (!loaded.people.length) {
        text(
          els.searchPreviewPeopleNote,
          loaded.hasMoreFaces
            ? "前 200 张人脸中还没有已归属人物；还有更多人脸待检查。"
            : "这张照片里还没有已归属的人物。"
        );
        return;
      }
      var canonicalIds = {};
      loaded.people.sort(function (a, b) {
        return Number((b.person || {}).asset_count || 0) - Number((a.person || {}).asset_count || 0)
          || Number((a.person || {}).canonical_person_id || (a.person || {}).person_id || a.face.assigned_person_id)
           - Number((b.person || {}).canonical_person_id || (b.person || {}).person_id || b.face.assigned_person_id);
      }).forEach(function (entry) {
        var face = entry.face;
        var person = entry.person || {};
        var personId = Number(person.canonical_person_id || person.person_id || face.assigned_person_id);
        if (!isFinite(personId) || personId < 1) return;
        var canonicalKey = String(personId);
        if (canonicalIds[canonicalKey]) return;
        canonicalIds[canonicalKey] = true;
        var avatarId = /^[a-f0-9]{64}$/.test(String(person.avatar_face_id || ""))
          ? person.avatar_face_id
          : null;
        var avatarUrl = avatarId
          ? "/api/v1/faces/" + avatarId + "/thumbnail"
          : face.thumbnail_url;
        var card = element("button", "search-preview-person");
        card.type = "button";
        card.setAttribute("aria-label", "查看" + (person.name || face.assigned_person_name || "未命名的人") + "的照片");
        var image = document.createElement("img");
        image.alt = "";
        image.loading = "lazy";
        image.src = avatarUrl ? avatarUrl + "?v=p4-search-person-link-a-1" : "";
        var label = person.name || face.assigned_person_name || "未命名的人";
        card.appendChild(image);
        card.appendChild(element("span", "", avatarId ? label : label + "（暂用这张照片的人脸）"));
        card.addEventListener("click", function () {
          prepareSearchDetailReturn();
          window.location.href = "/persons?person_id=" + encodeURIComponent(personId);
        });
        els.searchPreviewPeopleList.appendChild(card);
      });
      text(
        els.searchPreviewPeopleNote,
        loaded.hasMoreFaces ? "已按人物去重；当前只检查前 200 张人脸。" : "点人物头像查看这个人的照片。"
      );
    }).catch(function () {
      if (previewSeq === preview.seq && peopleSeq === preview.peopleSeq && els.searchPreview.open) {
        text(els.searchPreviewPeopleNote, "人物信息暂时无法读取。");
      }
    });
  }

  function moveSearchPreview(step) {
    var preview = state.search.preview;
    if (preview.moving || !els.searchPreview.open) return;
    var index = searchPreviewPosition();
    if (index < 0 || index + step < 0) return;
    var listVersion = preview.listVersion;
    var previewSeq = preview.seq;
    preview.moving = true;
    updateSearchPreviewNavigation();
    var load = index + step >= state.search.hits.length ? sendSearch(true) : Promise.resolve(true);
    Promise.resolve(load).then(function (succeeded) {
      if (previewSeq !== preview.seq || listVersion !== preview.listVersion || !els.searchPreview.open) return;
      if (!succeeded) {
        text(els.searchPreviewNote, "下一页结果没有加载成功，请再点“下一张”重试。");
        return;
      }
      var next = state.search.hits[index + step];
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
        "当前没有激活的搜索配置（active_profile_id 为空）：带查询文本的请求会被服务端拒绝" +
          "（需要激活的 profile 或显式绑定），纯筛选查询不受影响。"
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
    if (!search || search.query_encoder_available !== true) {
      lines.push(
        "本进程没有查询编码器（query_encoder_available=false）：文本查询的语义通道不会运行，" +
          "服务端会以 RUNTIME_CAPABILITY_UNAVAILABLE / QUERY_ENCODER_UNAVAILABLE 明确拒绝，" +
          "页面不会把这种情况显示成「没有结果」。"
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
    clearSearchSnapshot();
    invalidateSearchPreview();
    state.search.sessionId = null;
    state.search.cursor = null;
    state.search.count = 0;
    state.search.total = null;
    state.search.hits = [];
    state.search.lastBody = null;
    state.search.binding = null;
    state.search.placeAware = null;
    state.search.intentPlan = null;
    releaseObjectUrls();
    clear(els.resultGrid);
    clear(els.resultPlaceScope);
    els.resultPlaceScope.hidden = true;
    clear(els.resultIntentScope);
    els.resultIntentScope.hidden = true;
    renderNotices(els.resultNotices, []);
    els.loadMore.hidden = true;
    els.queryActions.hidden = true;
    els.queryText.value = "";
    els.querySource.value = "";
    els.queryAvailability.value = "any";
    els.queryPlaceState.value = "any";
    els.queryPlaceIds.value = "";
    els.queryDescendants.value = "true";
    els.queryCaptureFrom.value = "";
    els.queryCaptureTo.value = "";
    setStatus(els.captureState, "");
    text(els.captureEcho, "");
    els.queryPageSize.value = String(DEFAULT_PAGE_SIZE);
    clearPersonSelection();
    setStatus(els.queryStatus, "");
    renderNotices(els.resultNotices, []);
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
      queryText: document.getElementById("query-text"),
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
      loadMore: document.getElementById("load-more"),
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
      searchPreviewPeople: document.getElementById("search-preview-people"),
      searchPreviewPeopleNote: document.getElementById("search-preview-people-note"),
      searchPreviewPeopleList: document.getElementById("search-preview-people-list"),
      searchPreviewNote: document.getElementById("search-preview-note"),
    };

    els.searchForm.addEventListener("submit", function (event) {
      event.preventDefault();
      submitSearch();
    });
    document.getElementById("query-reset").addEventListener("click", resetFilters);
    document.getElementById("capture-clear").addEventListener("click", clearCaptureRange);
    document.getElementById("query-retry").addEventListener("click", function () {
      sendSearch(false);
    });
    els.loadMore.addEventListener("click", continueSearch);
    document.getElementById("search-preview-close").addEventListener("click", function () {
      els.searchPreview.close();
    });
    els.searchPreviewPrev.addEventListener("click", function () { moveSearchPreview(-1); });
    els.searchPreviewNext.addEventListener("click", function () { moveSearchPreview(1); });
    els.searchPreview.addEventListener("close", function () {
      if (state.search.preview.infoMount) state.search.preview.infoMount.dispose();
      state.search.preview.infoMount = null;
      state.search.preview.seq += 1;
      state.search.preview.assetId = null;
      els.searchPreviewImage.removeAttribute("src");
      updateSearchPreviewNavigation();
    });
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
    Promise.all([capabilitiesPromise, loadSources()]).then(restoreSearchSnapshot);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
