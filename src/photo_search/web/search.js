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
 *    and a truncated candidate list each get their own sentence;
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
  var FAILURE_HINT = {
    SEARCH_SESSION_EXPIRED:
      "这个搜索会话已经过期（进程重启或超过有效期）；查询条件已保留，请重新搜索。",
    INVALID_CURSOR: "分页游标与这次查询不匹配（可能被改动过，或查询条件已改）；请重新搜索。",
    PROFILE_GENERATION_MISMATCH:
      "搜索配置或索引世代在翻页期间发生了变化；旧会话不再有效，请重新搜索。",
    CATALOG_FILTER_REVISION_STALE:
      "地点或可见性在翻页期间发生了变化，旧会话不再有效；请重新搜索。",
    PLACE_SUGGESTION_REVISION_STALE:
      "可见地点建议在翻页期间发生了变化，旧会话不再有效；请重新搜索。",
    UPLOAD_TOO_LARGE: "上传的图片超过了服务端允许的字节上限。",
    UPLOAD_TOO_MANY_PIXELS: "上传的图片超过了服务端允许的像素上限。",
    FORMAT_UNSUPPORTED: "这个图片格式本构建还不解码（与 P1-03 的格式白名单一致）。",
    DECODE_FAILED: "上传的图片无法解码（可能已损坏，或解码超过了时间预算）。",
    VALIDATION_ERROR: "请求参数不被接受，请检查上面的筛选条件。",
    SESSION_REQUIRED: "本地会话已失效。",
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
    },
    requestCounter: 0,
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

  function truncationLine(label) {
    return (
      label +
      "的候选达到了本次上界并被截断（truncated=true）：这条通道的结果可能不完整，" +
      "调大 top_k 或在更窄的筛选下重搜会更完整。"
    );
  }

  function successNotices(body) {
    var lines = [];
    var projection = body.search_projection || {};
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
    if (body.candidates_truncated) {
      lines.push({
        kind: "",
        text: "候选列表达到了服务端上界（1000）并被截断：结果可能不完整。",
      });
    }
    var channels = body.channels || {};
    var fts = channels.fts || {};
    var vector = channels.vector || {};
    if (fts.truncated) {
      lines.push({ kind: "", text: truncationLine("文本通道") });
    }
    if (vector.truncated) {
      lines.push({ kind: "", text: truncationLine("向量通道") });
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
    return {
      sourceId: els.querySource.value ? Number(els.querySource.value) : null,
      availability: els.queryAvailability.value,
      placeIds: places.values,
      placeState: els.queryPlaceState.value,
      includeDescendants: els.queryDescendants.value === "true",
      pageSize: pageSize,
    };
  }

  function newRequestId(prefix) {
    state.requestCounter += 1;
    return prefix + "-" + Date.now().toString(36) + "-" + state.requestCounter;
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
    state.search.mode = "text";
    state.search.sessionId = null;
    state.search.cursor = null;
    state.search.count = 0;
    state.search.total = null;
    releaseObjectUrls();
    clear(els.resultGrid);
    renderNotices(els.resultNotices, []);
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
    state.search.loading = true;
    setStatus(els.queryStatus, continuation ? "正在加载下一页…" : "正在搜索…");
    var promise =
      state.search.mode === "image"
        ? sendImageSearch(continuation, filters)
        : sendTextSearch(continuation, filters);
    return promise.then(
      function (body) {
        state.search.loading = false;
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
    var body = {
      contract_version: CONTRACT_VERSION,
      request_id: newRequestId("text"),
      availability: filters.availability,
      place_state: filters.placeState,
      include_descendants: filters.includeDescendants,
      page_size: filters.pageSize,
    };
    if (queryText) {
      body.text = queryText;
    }
    if (filters.sourceId !== null) {
      body.source_id = filters.sourceId;
    }
    if (filters.placeIds.length) {
      body.place_ids = filters.placeIds;
    }
    if (continuation) {
      body.search_session_id = state.search.sessionId;
      body.cursor = state.search.cursor;
    }
    return request("/search", { method: "POST", body: body }).then(function (result) {
      return result.body || {};
    });
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
    }
    setStatus(els.queryStatus, "本次查询没有成功：" + describe(failure), "error");
    renderNotices(els.resultNotices, failureLines(failure));
    // The query is preserved and the page offers a re-run, instead of silently showing an
    // empty grid.
    els.queryActions.hidden = false;
  }

  function renderPage(body, continuation) {
    var hits = body.hits || [];
    if (!continuation) {
      releaseObjectUrls();
      clear(els.resultGrid);
      state.search.count = 0;
    }
    hits.forEach(function (hit) {
      els.resultGrid.appendChild(buildHit(hit));
    });
    state.search.count += hits.length;
    state.search.sessionId = body.search_session_id || null;
    state.search.cursor = body.next_cursor || null;
    state.search.total = body.total_candidates;
    els.loadMore.hidden = !body.has_more;
    els.queryActions.hidden = true;
    renderNotices(els.resultNotices, successNotices(body));
    var parts = [
      "已显示 " + state.search.count + " 行",
      "候选共 " + number(body.total_candidates) + " 个",
      body.has_more ? "还有下一页" : "已到末尾",
    ];
    if (!hits.length) {
      parts.push("这一页没有命中的行");
    }
    setStatus(els.queryStatus, parts.join(" · "));
  }

  // ----------------------------------------------------------------------- tiles
  function buildHit(hit) {
    var tile = element("li", "card-tile");
    var frame = element("div", "thumb-frame");
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
    caption.appendChild(open);
    tile.appendChild(caption);
    return tile;
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
      request("/sources"),
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
      lines.push(
        "当前搜索配置 " +
          safeText(search.active_profile_id) +
          " · 图像世代 " +
          safeText(search.active_image_generation_id || "未激活")
      );
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

  function loadCapabilities() {
    // The person navigation entry reads the same capabilities document; it stays hidden
    // until the catalog really carries the person read layer.
    return request("/capabilities").then(
      function (result) {
        var body = result.body || {};
        state.capabilities = body;
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
        renderBindingHint(body);
      },
      function (failure) {
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
    return request("/sources").then(
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
    state.search.sessionId = null;
    state.search.cursor = null;
    state.search.count = 0;
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
    els.queryText.value = "";
    els.querySource.value = "";
    els.queryAvailability.value = "any";
    els.queryPlaceState.value = "any";
    els.queryPlaceIds.value = "";
    els.queryDescendants.value = "true";
    els.queryPageSize.value = String(DEFAULT_PAGE_SIZE);
    setStatus(els.queryStatus, "");
    renderNotices(els.resultNotices, []);
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
      querySource: document.getElementById("query-source"),
      queryAvailability: document.getElementById("query-availability"),
      queryPlaceState: document.getElementById("query-place-state"),
      queryPlaceIds: document.getElementById("query-place-ids"),
      queryDescendants: document.getElementById("query-descendants"),
      queryPageSize: document.getElementById("query-page-size"),
      resultNotices: document.getElementById("result-notices"),
      resultGrid: document.getElementById("result-grid"),
      loadMore: document.getElementById("load-more"),
      uploadForm: document.getElementById("upload-form"),
      uploadFile: document.getElementById("upload-file"),
      uploadStatus: document.getElementById("upload-status"),
      coverageBody: document.getElementById("coverage-body"),
    };

    els.searchForm.addEventListener("submit", function (event) {
      event.preventDefault();
      submitSearch();
    });
    document.getElementById("query-reset").addEventListener("click", resetFilters);
    document.getElementById("query-retry").addEventListener("click", function () {
      sendSearch(false);
    });
    els.loadMore.addEventListener("click", continueSearch);
    els.uploadForm.addEventListener("submit", submitUpload);
    document.getElementById("coverage-reload").addEventListener("click", loadCoverage);

    state.observer = buildObserver();
    loadCapabilities().then(loadCoverage);
    loadSources();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
