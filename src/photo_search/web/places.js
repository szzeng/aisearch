/* Local place-curation page for the read-only API plus the frozen ADR-0005 write surface.
 *
 * Ground rules this file keeps (P2-03C):
 *  - every piece of data comes from the session-protected /api/v1 endpoints;
 *  - the CSRF token is read from the ps_csrf cookie and sent in X-CSRF-Token only;
 *    it is never stored, never logged and never put in a URL;
 *  - the page never asks for exact coordinates and never renders a local absolute path:
 *    server text is scrubbed before it is shown;
 *  - a machine candidate is always displayed as a suggestion-only proposal; only an
 *    explicit human action writes an effective assignment;
 *  - nothing is loaded from the network except this same origin.
 */
(function () {
  "use strict";

  var API = "/api/v1";
  var DEFAULT_LIMIT = 25;
  var MAX_BATCH = 500;
  var SNAPSHOT_PAGE_LIMIT = 200;
  var SNAPSHOT_CAP = 2000;

  var KIND_TEXT = {
    country: "国家",
    city: "城市",
    attraction: "景点",
    custom: "自定义",
  };
  var LIFECYCLE_TEXT = { ACTIVE: "启用", RETIRED: "已停用" };
  var ORIGIN_TEXT = {
    NONE: "无",
    MANUAL: "人工",
    CONFIRMED_SUGGESTION: "确认机器建议",
  };
  var MODE_TEXT = {
    AUTO_ALLOWED: "允许机器建议（无有效归属）",
    MANUAL_ASSIGNED: "人工归属（带锁）",
    MANUAL_UNASSIGNED: "人工取消归类（带锁）",
  };
  var DATASET_STATE_TEXT = {
    unavailable: "未安装地名包",
    installed: "已导入，未激活",
    active: "已激活",
    missing: "包文件缺失",
    invalid: "包校验失败",
  };
  var GPS_STATE_TEXT = {
    NOT_EXTRACTED: "尚未提取 GPS",
    VALID: "有有效 GPS 事实",
    ASSUMED_WGS84: "GPS 事实（假设 WGS84，仅建议）",
    ABSENT: "无 GPS",
    INVALID: "GPS 非法",
    UNSUPPORTED_CRS: "坐标系不支持（不转换）",
    SOURCE_UNAVAILABLE: "读取期间来源不可用",
  };
  var SESSION_HINT = "请使用启动器打印的一次性链接建立会话。";
  //: A drive-absolute or UNC spelling inside server-side text is never rendered.
  var PATH_LIKE = /(?:[A-Za-z]:[\\/])|(?:\\\\[^\s'"])/;
  var PATH_NOTE = "错误详情包含本机路径，已省略。";

  var state = {
    sessionMissing: false,
    placeIndex: {},
    directory: { limit: DEFAULT_LIMIT, cursor: null, hasMore: false, loading: false, count: 0 },
    edit: { place: null },
    suggested: {
      limit: DEFAULT_LIMIT,
      cursor: null,
      hasMore: false,
      loading: false,
      count: 0,
      revision: null,
      frozen: null,
    },
    selection: {},
    operations: { cursor: null, hasMore: false, loading: false },
    requestCounter: 0,
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

  function errorText(code) {
    if (code === "CATALOG_FILTER_REVISION_STALE") {
      return "目录已变化，需重新加载";
    }
    if (code === "PLACE_SUGGESTION_REVISION_STALE") {
      return "建议集合已变化，需重新加载";
    }
    if (code === "PLACE_ASSIGNMENT_CONFLICT") {
      return "revision 已过期";
    }
    if (code === "PLACE_OPERATION_NOT_UNDOABLE") {
      return "该操作已被后续编辑改变，不能整批撤销";
    }
    if (code === "PLACE_IDEMPOTENCY_CONFLICT") {
      return "request_id 已绑定不同的请求内容";
    }
    if (code === "PLACE_BATCH_TOO_LARGE") {
      return "单批超过 500 项，请拆分";
    }
    if (code === "PLACE_DATASET_MISSING") {
      return "缺少可用的地名包，新解析被阻塞";
    }
    if (code === "PLACE_DATASET_INVALID") {
      return "地名包校验失败，新解析被阻塞";
    }
    if (code === "PLACE_PARENT_CYCLE") {
      return "父级非法（类型不允许或会形成环）";
    }
    if (code === "PLACE_REFERENCED") {
      return "该对象仍被引用";
    }
    if (code === "PLACE_NOT_FOUND") {
      return "地点或资产不存在";
    }
    if (code === "INVALID_CURSOR") {
      return "分页游标无效，需重新加载";
    }
    return "";
  }

  function describe(failure) {
    var base = "[" + failure.code + "] " + failure.message;
    var hint = errorText(failure.code);
    if (hint) {
      base += "（" + hint + "）";
    }
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

  function setError(node, message) {
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

  //: One printable-ASCII idempotency key per submitted operation or batch.
  function newRequestId(prefix) {
    state.requestCounter += 1;
    var random = Math.floor(Math.random() * 0x100000000).toString(36);
    var parts = [prefix, Date.now().toString(36), state.requestCounter.toString(36), random];
    return parts.join("-").slice(0, 64);
  }

  function lines(value) {
    return String(value || "")
      .split("\n")
      .map(function (line) {
        return line.trim();
      })
      .filter(function (line) {
        return line.length > 0;
      });
  }

  //: ``name`` or ``name | language`` per line, the only alias shape the API accepts.
  function aliasPayload(value) {
    return lines(value).map(function (line) {
      var parts = line.split("|");
      var alias = { text: parts[0].trim() };
      var language = parts.length > 1 ? parts.slice(1).join("|").trim() : "";
      alias.language = language ? language : null;
      return alias;
    });
  }

  function aliasLines(place) {
    return (place.aliases || [])
      .map(function (alias) {
        var line = String(alias.text);
        if (alias.language) {
          line += " | " + alias.language;
        }
        if (alias.source === "DATASET") {
          line += "  （来自数据包）";
        }
        return line;
      })
      .join("\n");
  }

  function placeLabel(place) {
    return String(place.canonical_name) + " #" + place.place_id;
  }

  function placeKindText(place) {
    return KIND_TEXT[place.kind] || place.kind;
  }

  function integerOrNull(value) {
    var trimmed = String(value === undefined || value === null ? "" : value).trim();
    if (!trimmed) {
      return null;
    }
    if (!/^[0-9]+$/.test(trimmed)) {
      return null;
    }
    return Number(trimmed);
  }
  // ------------------------------------------------------------------ capabilities
  function probeCapabilities() {
    request("/capabilities")
      .then(function (result) {
        var body = result.body || {};
        clearNotice();
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
            "） · 只读受控访问 + 本机人工整理"
        );
      })
      .catch(function (failure) {
        if (failure.code === "SCHEMA_TOO_NEW") {
          showNotice(
            "图库的 schema 版本高于本程序支持的版本（" +
              failure.code +
              "）。请升级程序或改用匹配的数据目录。（请求编号 " +
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

  // ---------------------------------------------------------------- dataset panel
  function loadDatasets() {
    setStatus(els.datasetBody, "正在读取地名包状态…");
    request("/places/datasets")
      .then(function (result) {
        drawDatasets(result.body || {});
      })
      .catch(function (failure) {
        setStatus(els.datasetBody, "读取地名包状态失败：" + describe(failure), "error");
      });
  }

  function drawDatasets(body) {
    var stateName = String(body.state || "unknown");
    els.datasetBody.className = "state-block";
    els.datasetBody.hidden = false;
    clear(els.datasetBody);

    els.datasetBody.appendChild(
      element(
        "p",
        "dataset-headline",
        "地名包状态：" + (DATASET_STATE_TEXT[stateName] || stateName) + "（" + stateName + "）"
      )
    );

    var facts = element("dl", "media-list");
    [
      ["解析器可用", body.resolver_available ? "可用" : "不可用（新解析会被阻塞）"],
      ["suggestion_revision", "#" + body.suggestion_revision],
      ["state reason", body.reason ? String(body.reason) : "无（正常）"],
    ].forEach(function (pair) {
      facts.appendChild(element("dt", null, pair[0]));
      facts.appendChild(element("dd", null, pair[1]));
    });
    els.datasetBody.appendChild(facts);

    var active = body.active_dataset;
    if (active) {
      var activeList = element("dl", "media-list");
      [
        ["活动包", String(active.package_id) + " @" + active.package_version],
        ["dataset_id", String(active.dataset_id)],
        ["包状态", String(active.state)],
        ["记录数", String(active.record_count) + " 条 / " + String(active.file_count) + " 个文件"],
        ["数据来源", String(active.provider) + "（许可 " + String(active.license_id) + "）"],
        [
          "兼容 schema",
          String(active.compatible_catalog_schema.minimum) +
            " – " +
            String(active.compatible_catalog_schema.maximum),
        ],
        ["上次激活", formatMoment(active.activated_at)],
      ].forEach(function (pair) {
        activeList.appendChild(element("dt", null, pair[0]));
        activeList.appendChild(element("dd", null, pair[1]));
      });
      els.datasetBody.appendChild(activeList);
      if (active.problems && active.problems.length) {
        var problems = element("ul", "log");
        active.problems.forEach(function (problem) {
          problems.appendChild(
            element("li", "error", "[" + problem.code + "] " + safeText(problem.message))
          );
        });
        els.datasetBody.appendChild(problems);
      }
    } else {
      els.datasetBody.appendChild(
        element("p", "hint", "当前没有任何活动包；页面不会显示伪造的 ACTIVE 状态。")
      );
    }

    var installed = body.datasets || [];
    if (installed.length) {
      var list = element("ul", "log");
      installed.forEach(function (dataset) {
        var line =
          String(dataset.dataset_id) +
          " · " +
          String(dataset.package_version) +
          " · " +
          String(dataset.state) +
          " · " +
          String(dataset.record_count) +
          " 条" +
          (dataset.problems && dataset.problems.length ? " · 有问题" : "");
        list.appendChild(element("li", null, line));
      });
      els.datasetBody.appendChild(element("h3", null, "已导入的包"));
      els.datasetBody.appendChild(list);
    }

    if (stateName !== "active") {
      els.datasetBody.appendChild(
        element(
          "p",
          "hint",
          "没有可用地名包时，新的 GPS 解析会被阻塞（BLOCKED）；地点目录、人工归属与筛选仍然可用。" +
            "「没有建议」不等于「已归类」：候选只来自当前活动包。"
        )
      );
    }
    els.datasetBody.appendChild(
      element("p", "hint", "本页面不会触发后台解析，只显示目录里已有的机器产物。")
    );
  }

  // -------------------------------------------------------------------- directory
  function directoryParams(cursor) {
    var params = new URLSearchParams();
    params.set("limit", String(state.directory.limit));
    var name = els.dirName.value.trim();
    if (name) {
      params.set("name", name);
    }
    if (els.dirKind.value) {
      params.set("kind", els.dirKind.value);
    }
    if (els.dirLifecycle.value) {
      params.set("lifecycle", els.dirLifecycle.value);
    }
    var parent = integerOrNull(els.dirParent.value);
    if (parent !== null) {
      params.set("parent", String(parent));
    }
    var ancestor = integerOrNull(els.dirAncestor.value);
    if (ancestor !== null) {
      params.set("ancestor", String(ancestor));
    }
    if (cursor) {
      params.set("cursor", cursor);
    }
    return params;
  }

  function loadDirectory(reset) {
    if (state.directory.loading) {
      return;
    }
    state.directory.loading = true;
    if (reset) {
      state.directory.cursor = null;
      state.directory.hasMore = false;
      state.directory.count = 0;
      clear(els.directoryList);
    }
    setStatus(els.directoryStatus, reset ? "正在读取目录…" : "正在读取下一页目录…");
    request("/places?" + directoryParams(state.directory.cursor).toString())
      .then(function (result) {
        var body = result.body || {};
        (body.places || []).forEach(function (place) {
          state.placeIndex[String(place.place_id)] = place;
          els.directoryList.appendChild(buildPlaceRow(place));
        });
        state.directory.count += (body.places || []).length;
        state.directory.cursor = body.next_cursor || null;
        state.directory.hasMore = Boolean(body.has_more);
        state.directory.loading = false;
        setStatus(
          els.directoryStatus,
          state.directory.count
            ? "已加载 " +
                state.directory.count +
                " 个地点" +
                (state.directory.hasMore ? "，还有更多。" : "，已经到底。")
            : "没有匹配的地点。可以先用下面的表单新建一个地点。"
        );
        els.dirMore.hidden = !state.directory.hasMore;
        text(
          els.directoryRevision,
          "目录 revision #" + body.catalog_filter_revision + "（游标绑定该版本）"
        );
      })
      .catch(function (failure) {
        state.directory.loading = false;
        els.dirMore.hidden = true;
        var prefix =
          failure.code === "CATALOG_FILTER_REVISION_STALE"
            ? "目录已变化，需重新加载："
            : "读取目录失败：";
        setStatus(els.directoryStatus, prefix + describe(failure), "error");
      });
  }

  function buildPlaceRow(place) {
    var item = element("li", "card place-row");
    item.setAttribute("data-place-id", String(place.place_id));
    var head = element("p", "place-head");
    head.appendChild(element("span", "chip chip-kind", placeKindText(place)));
    head.appendChild(element("strong", null, place.canonical_name));
    head.appendChild(element("span", "chip chip-id", "#" + place.place_id));
    head.appendChild(element("span", "chip", LIFECYCLE_TEXT[place.lifecycle] || place.lifecycle));
    head.appendChild(element("span", "chip", "origin " + place.origin));
    item.appendChild(head);

    var chain = element("p", "chain", "");
    paintChain(chain, place);
    item.appendChild(chain);

    var facts = element("dl", "media-list");
    [
      ["父级", place.parent_place_id === null ? "无" : "#" + place.parent_place_id],
      ["revision", String(place.revision)],
      ["别名", (place.aliases || []).length ? String((place.aliases || []).length) + " 个" : "无"],
      ["创建时间", formatMoment(place.created_at)],
    ].forEach(function (pair) {
      facts.appendChild(element("dt", null, pair[0]));
      facts.appendChild(element("dd", null, pair[1]));
    });
    item.appendChild(facts);

    if (place.aliases && place.aliases.length) {
      var aliases = element("ul", "log");
      place.aliases.forEach(function (alias) {
        aliases.appendChild(
          element(
            "li",
            null,
            String(alias.text) +
              (alias.language ? "（" + alias.language + "）" : "") +
              " · 来源 " +
              String(alias.source)
          )
        );
      });
      item.appendChild(aliases);
    }

    var actions = element("p", "row-actions");
    var edit = element("button", null, "编辑此地点");
    edit.type = "button";
    edit.addEventListener("click", function () {
      loadPlaceIntoEditor(place.place_id);
    });
    actions.appendChild(edit);
    var descendants = element("button", null, "只看它的后代");
    descendants.type = "button";
    descendants.title = "只看该地点及其后代（会清空名称与父级条件）";
    descendants.addEventListener("click", function () {
      // A subtree view starts from a clean text filter: keeping "name=同名地点" would
      // silently hide the other descendants of the very place the user picked.
      els.dirName.value = "";
      els.dirParent.value = "";
      els.dirAncestor.value = String(place.place_id);
      loadDirectory(true);
    });
    actions.appendChild(descendants);
    item.appendChild(actions);
    return item;
  }

  function paintChain(node, place) {
    var parts = [];
    var current = place;
    var unknownId = null;
    var guard = 0;
    while (current && guard < 64) {
      guard += 1;
      parts.unshift(placeLabel(current));
      var parentId = current.parent_place_id;
      if (parentId === null || parentId === undefined) {
        break;
      }
      var parent = state.placeIndex[String(parentId)];
      if (parent) {
        current = parent;
        continue;
      }
      unknownId = parentId;
      break;
    }
    var suffix = unknownId === null ? "" : " › （#" + unknownId + "）";
    text(node, "祖先链：" + parts.join(" › ") + suffix);
    if (unknownId !== null) {
      fetchPlaceById(unknownId).then(function (parent) {
        if (parent) {
          state.placeIndex[String(parent.place_id)] = parent;
          paintChain(node, place);
        }
      });
    }
  }

  //: ``GET /places?ancestor=<id>&limit=1`` is the only one-place read the frozen API
  //: offers (there is no ``GET /places/{id}``); the descendant expansion is inclusive and
  //: the ordering is smallest-first, so the first row is the place itself. A parent that
  //: is not loaded is shown as its stable ``#id`` instead of a guessed name.
  function fetchPlaceById(placeId) {
    var key = String(placeId);
    if (Object.prototype.hasOwnProperty.call(state.placeIndex, key)) {
      return Promise.resolve(state.placeIndex[key]);
    }
    var params = new URLSearchParams();
    params.set("ancestor", key);
    params.set("limit", "1");
    return request("/places?" + params.toString())
      .then(function (result) {
        var rows = (result.body && result.body.places) || [];
        var found = null;
        rows.forEach(function (row) {
          if (Number(row.place_id) === Number(placeId)) {
            found = row;
          }
        });
        state.placeIndex[key] = found;
        return found;
      })
      .catch(function () {
        return state.placeIndex[key] || null;
      });
  }
  // ----------------------------------------------------------------------- create
  function submitCreate(event) {
    event.preventDefault();
    setError(els.createError, "");
    var name = els.createName.value.trim();
    if (!name) {
      setError(els.createError, "规范名称不能为空。");
      return;
    }
    var parentRaw = els.createParent.value.trim();
    var parent = integerOrNull(parentRaw);
    if (parentRaw && parent === null) {
      setError(els.createError, "父级 place_id 必须是数字，或留空。");
      return;
    }
    var payload = {
      kind: els.createKind.value,
      canonical_name: name,
      parent_place_id: parent,
      aliases: aliasPayload(els.createAliases.value),
    };
    request("/places", { method: "POST", body: payload })
      .then(function (result) {
        var place = result.body || {};
        setStatus(
          els.directoryStatus,
          "已创建 " +
            placeKindText(place) +
            "「" +
            place.canonical_name +
            "」#" +
            place.place_id +
            "。"
        );
        els.createForm.reset();
        loadDirectory(true);
      })
      .catch(function (failure) {
        setError(
          els.createError,
          "创建失败：" +
            describe(failure) +
            (failure.code === "PLACE_PARENT_CYCLE"
              ? "\n父级必须存在、启用，且类型组合合法（国家不能有父级）。"
              : "")
        );
      });
  }

  // ------------------------------------------------------------------------- edit
  function loadPlaceIntoEditor(placeId) {
    var target = integerOrNull(placeId);
    if (target === null) {
      setError(els.editError, "place_id 必须是数字。");
      els.editForm.hidden = true;
      return;
    }
    els.editPlaceId.value = String(target);
    setError(els.editError, "");
    fetchPlaceById(target).then(function (place) {
      if (!place) {
        els.editForm.hidden = true;
        setError(els.editError, "没有找到 place_id #" + target + "（PLACE_NOT_FOUND）。");
        return;
      }
      state.edit.place = place;
      els.editForm.hidden = false;
      els.editName.value = place.canonical_name;
      els.editAliases.value = aliasLines(place);
      els.editRevision.value = String(place.revision);
      paintEditHeading(place);
      els.editForm.scrollIntoView({ block: "nearest" });
    });
  }

  function paintEditHeading(place) {
    text(
      els.editHeading,
      "正在编辑 " +
        placeKindText(place) +
        "「" +
        place.canonical_name +
        "」#" +
        place.place_id +
        " · 当前 revision " +
        place.revision +
        " · 生命周期 " +
        place.lifecycle
    );
  }

  function submitEdit(event) {
    event.preventDefault();
    setError(els.editError, "");
    if (!state.edit.place) {
      setError(els.editError, "请先载入一个地点。");
      return;
    }
    var revision = integerOrNull(els.editRevision.value);
    if (revision === null) {
      setError(els.editError, "expected_revision 必须是数字。");
      return;
    }
    var payload = {
      canonical_name: els.editName.value.trim(),
      aliases: aliasPayload(els.editAliases.value),
      expected_revision: revision,
    };
    var placeId = state.edit.place.place_id;
    request("/places/" + encodeURIComponent(placeId), { method: "PATCH", body: payload })
      .then(function (result) {
        var place = result.body || {};
        state.placeIndex[String(place.place_id)] = place;
        state.edit.place = place;
        els.editRevision.value = String(place.revision);
        els.editAliases.value = aliasLines(place);
        paintEditHeading(place);
        setStatus(
          els.directoryStatus,
          "已保存「" +
            place.canonical_name +
            "」#" +
            place.place_id +
            "（revision " +
            place.revision +
            "）。"
        );
        loadDirectory(true);
      })
      .catch(function (failure) {
        var details = failure.details || {};
        var detail = "";
        if (failure.code === "PLACE_ASSIGNMENT_CONFLICT") {
          detail =
            "\n当前 revision 是 " +
            (details.current_revision === undefined ? "未知" : details.current_revision) +
            "，本次提交的 expected_revision 是 " +
            (details.expected_revision === undefined ? revision : details.expected_revision) +
            "。请点「重新载入」后重试。";
        } else if (failure.code === "VALIDATION_ERROR") {
          detail = "\n服务端拒绝了这次修改（例如 reparent：本版本不开放改父级，错误如实显示）。";
        } else if (failure.code === "PLACE_PARENT_CYCLE" || failure.code === "PLACE_REFERENCED") {
          detail = "\n父级或引用约束不满足，目录关系没有改变。";
        }
        setError(els.editError, "保存失败：" + describe(failure) + detail);
      });
  }
  // ------------------------------------------------------------------ suggestions
  function suggestedParams(useSnapshotRevision) {
    var params = new URLSearchParams();
    params.set("place_state", "suggested");
    params.set("order", "asset_id");
    params.set("limit", String(state.suggested.limit));
    if (state.suggested.cursor) {
      params.set("cursor", state.suggested.cursor);
    }
    if (useSnapshotRevision && state.suggested.revision !== null) {
      params.set("suggestion_revision", String(state.suggested.revision));
    }
    return params;
  }

  function loadSuggested(reset, useSnapshotRevision) {
    if (state.suggested.loading) {
      return;
    }
    state.suggested.loading = true;
    if (reset) {
      state.suggested.cursor = null;
      state.suggested.hasMore = false;
      state.suggested.count = 0;
      state.suggested.revision = null;
      state.suggested.frozen = null;
      state.selection = {};
      clear(els.suggestedList);
    }
    setStatus(els.suggestedStatus, reset ? "正在读取待确认建议…" : "正在读取下一页建议…");
    request("/assets?" + suggestedParams(Boolean(useSnapshotRevision)).toString())
      .then(function (result) {
        var body = result.body || {};
        (body.assets || []).forEach(function (asset) {
          els.suggestedList.appendChild(buildSuggestionRow(asset));
        });
        state.suggested.count += (body.assets || []).length;
        state.suggested.cursor = body.next_cursor || null;
        state.suggested.hasMore = Boolean(body.has_more);
        state.suggested.revision = body.suggestion_revision;
        state.suggested.loading = false;
        els.suggestedMore.disabled = !state.suggested.hasMore;
        setStatus(
          els.suggestedStatus,
          (state.suggested.count
            ? "已加载 " + state.suggested.count + " 个待确认建议"
            : "当前没有待确认建议（这不等于「已归类」：可能没有 GPS、没有包，或候选已被排除）。") +
            " 快照 suggestion_revision #" +
            (body.suggestion_revision === null ? "无" : body.suggestion_revision) +
            (state.suggested.hasMore ? "，还有更多。" : "，已经到底。")
        );
        updateSelectionSummary();
      })
      .catch(function (failure) {
        state.suggested.loading = false;
        els.suggestedMore.disabled = true;
        var prefix =
          failure.code === "PLACE_SUGGESTION_REVISION_STALE"
            ? "建议集合已变化，需重新加载："
            : "读取建议失败：";
        setStatus(els.suggestedStatus, prefix + describe(failure), "error");
      });
  }

  function buildSuggestionRow(asset) {
    var assetId = String(asset.asset_id);
    var item = element("li", "card place-row");
    item.setAttribute("data-asset-id", assetId);
    var head = element("p", "place-head");
    var label = element("label", "check-line");
    var box = document.createElement("input");
    box.type = "checkbox";
    box.setAttribute("data-asset-id", assetId);
    box.checked = Boolean(state.selection[assetId]);
    box.addEventListener("change", function () {
      state.selection[assetId] = box.checked;
      updateSelectionSummary();
    });
    label.appendChild(box);
    label.appendChild(
      element(
        "span",
        null,
        asset.current_location && asset.current_location.relative_path
          ? String(asset.current_location.relative_path)
          : "资产 #" + assetId
      )
    );
    head.appendChild(label);
    head.appendChild(element("span", "chip chip-id", "资产 #" + assetId));
    head.appendChild(
      element("span", "chip", asset.availability === "missing" ? "原文件不可用" : "在库")
    );
    head.appendChild(element("span", "chip chip-suggest", "SUGGESTION_ONLY"));
    item.appendChild(head);

    var facts = element("dl", "media-list");
    [
      ["加入时间", formatMoment(asset.created_at) + "（入库时间）"],
      ["位置数量", String(asset.location_count)],
    ].forEach(function (pair) {
      facts.appendChild(element("dt", null, pair[0]));
      facts.appendChild(element("dd", null, pair[1]));
    });
    item.appendChild(facts);

    var holder = element("div", "suggestion-detail");
    var actions = element("p", "row-actions");
    var expand = element("button", null, "查看候选与当前归属");
    expand.type = "button";
    expand.addEventListener("click", function () {
      showAssetPlace(holder, assetId);
    });
    actions.appendChild(expand);
    var confirmOne = element("button", null, "确认第一条建议（origin=CONFIRMED_SUGGESTION）");
    confirmOne.type = "button";
    confirmOne.addEventListener("click", function () {
      confirmSingleSuggestion(assetId, holder);
    });
    actions.appendChild(confirmOne);
    item.appendChild(actions);
    item.appendChild(holder);
    return item;
  }

  function showAssetPlace(holder, assetId) {
    clear(holder);
    holder.appendChild(element("p", "hint", "正在读取该资产的当前地点状态…"));
    request("/assets/" + encodeURIComponent(assetId) + "/place")
      .then(function (result) {
        var body = result.body || {};
        clear(holder);
        var assignment = body.assignment;
        var effective = body.place;
        var facts = element("dl", "media-list");
        facts.appendChild(element("dt", null, "当前有效归属"));
        facts.appendChild(
          element(
            "dd",
            null,
            effective
              ? placeKindText(effective) +
                  "「" +
                  effective.canonical_name +
                  "」#" +
                  effective.place_id +
                  (effective.ancestors && effective.ancestors.length
                    ? "（祖先：" +
                      effective.ancestors
                        .map(function (ancestor) {
                          return ancestor.canonical_name + "#" + ancestor.place_id;
                        })
                        .join(" › ") +
                      "）"
                    : "")
              : "无（只有建议，或允许机器继续建议）"
          )
        );
        facts.appendChild(element("dt", null, "控制行"));
        facts.appendChild(
          element(
            "dd",
            null,
            assignment
              ? (MODE_TEXT[assignment.mode] || assignment.mode) +
                  " · origin " +
                  (ORIGIN_TEXT[assignment.origin] || assignment.origin) +
                  " · revision " +
                  assignment.revision +
                  " · 人工锁 " +
                  (assignment.manual_lock ? "是" : "否")
              : "无控制行（expected_revision 0）"
          )
        );
        facts.appendChild(element("dt", null, "GPS 状态"));
        facts.appendChild(
          element("dd", null, GPS_STATE_TEXT[body.gps_state] || String(body.gps_state))
        );
        facts.appendChild(element("dt", null, "revision"));
        facts.appendChild(
          element(
            "dd",
            null,
            "catalog_filter_revision #" +
              body.catalog_filter_revision +
              " · suggestion_revision #" +
              body.suggestion_revision
          )
        );
        holder.appendChild(facts);

        var suggested = body.suggested || {};
        holder.appendChild(
          element(
            "p",
            "hint",
            "建议状态 " +
              String(suggested.state) +
              (suggested.assumed_datum ? " · 假设 WGS84（必须人工确认）" : "") +
              (suggested.requires_confirmation ? " · 候选仅供确认（SUGGESTION_ONLY）" : "") +
              " · 已被排除候选 " +
              String(
                suggested.excluded_candidate_count === undefined
                  ? 0
                  : suggested.excluded_candidate_count
              )
          )
        );

        var candidates = suggested.candidates || [];
        if (!candidates.length) {
          holder.appendChild(
            element("p", "hint", "没有可见候选（可能没有活动包、没有 GPS，或候选已被排除）。")
          );
          return;
        }
        var list = element("ul", "log candidate-list");
        candidates.forEach(function (candidate) {
          list.appendChild(
            element(
              "li",
              "candidate",
              "候选 " +
                String(candidate.rank) +
                " · " +
                String(candidate.display_name) +
                (candidate.ancestor_names && candidate.ancestor_names.length
                  ? "（" + candidate.ancestor_names.join(" › ") + "）"
                  : "") +
                " · " +
                String(candidate.kind) +
                " · 距离 " +
                Math.round(Number(candidate.distance_m)) +
                " m · " +
                String(candidate.match_basis) +
                " / " +
                String(candidate.confidence_basis) +
                " · " +
                String(candidate.disposition) +
                (candidate.local_place_id === null || candidate.local_place_id === undefined
                  ? " · 未映射本地地点"
                  : " · 本地 place #" + candidate.local_place_id)
            )
          );
          if (candidate.local_place_id !== null && candidate.local_place_id !== undefined) {
            var use = element("button", null, "填入目标 place_id #" + candidate.local_place_id);
            use.type = "button";
            use.addEventListener("click", function () {
              els.targetPlaceId.value = String(candidate.local_place_id);
            });
            list.appendChild(use);
          }
        });
        holder.appendChild(list);
      })
      .catch(function (failure) {
        clear(holder);
        holder.appendChild(element("p", "error", "读取地点状态失败：" + describe(failure)));
      });
  }

  function confirmSingleSuggestion(assetId, holder) {
    request("/assets/" + encodeURIComponent(assetId) + "/place")
      .then(function (result) {
        var body = result.body || {};
        var candidates = (body.suggested || {}).candidates || [];
        var target = null;
        for (var index = 0; index < candidates.length; index += 1) {
          var local = candidates[index].local_place_id;
          if (local !== null && local !== undefined) {
            target = Number(local);
            break;
          }
        }
        if (target === null) {
          throw {
            code: "NO_LOCAL_CANDIDATE",
            message: "可见候选里没有已映射到本地地点的条目，无法确认归属",
            request_id: "",
          };
        }
        var assignment = body.assignment;
        var expected = assignment ? Number(assignment.revision) : 0;
        return request("/assets/" + encodeURIComponent(assetId) + "/place-assignment", {
          method: "PUT",
          body: {
            action: "assign",
            target_place_id: target,
            request_id: newRequestId("ui-confirm"),
            expected_revision: expected,
            origin: "CONFIRMED_SUGGESTION",
          },
        }).then(function (assigned) {
          return { target: target, result: assigned.body || {} };
        });
      })
      .then(function (outcome) {
        var message =
          "已确认建议：资产 #" +
          assetId +
          " → place #" +
          outcome.target +
          "，operation " +
          String(outcome.result.operation_id) +
          "（origin=CONFIRMED_SUGGESTION，operation_revision " +
          String(outcome.result.operation_revision) +
          "）。该资产应已离开建议列表。";
        clear(holder);
        holder.appendChild(element("p", "state-block", message));
        // The suggestion list is reloaded right away, so the same result is also kept in the
        // result log where it stays readable (the row itself is gone once it is assigned).
        appendResultLog("单条确认：" + message, false);
        state.suggested.frozen = null;
        loadSuggested(true, false);
        loadDirectory(true);
        loadOperations(true);
      })
      .catch(function (failure) {
        clear(holder);
        holder.appendChild(
          element(
            "p",
            "error",
            "确认失败：" +
              describe(failure) +
              (failure.code === "PLACE_ASSIGNMENT_CONFLICT"
                ? "\n该资产的控制行 revision 已变化，请刷新后重试。"
                : "")
          )
        );
      });
  }

  function updateSelectionSummary() {
    var ids = selectedIds();
    var frozen = state.suggested.frozen;
    var line = "已选择 " + ids.length + " 个资产";
    if (frozen) {
      line += "（冻结快照 " + frozen.length + " 个）";
    }
    text(els.selectionSummary, line);
  }

  function selectedIds() {
    return Object.keys(state.selection)
      .filter(function (key) {
        return state.selection[key];
      })
      .map(function (key) {
        return Number(key);
      })
      .sort(function (left, right) {
        return left - right;
      });
  }

  function selectPage() {
    Array.prototype.forEach.call(
      els.suggestedList.querySelectorAll("input[type=checkbox]"),
      function (box) {
        box.checked = true;
        var id = box.getAttribute("data-asset-id");
        if (id) {
          state.selection[String(id)] = true;
        }
      }
    );
    updateSelectionSummary();
  }

  //: ``全选结果`` must freeze an asset_id snapshot instead of re-running the query later.
  function freezeAll() {
    var ids = [];
    var params = new URLSearchParams();
    params.set("place_state", "suggested");
    params.set("order", "asset_id");
    params.set("limit", String(SNAPSHOT_PAGE_LIMIT));
    if (state.suggested.revision !== null) {
      params.set("suggestion_revision", String(state.suggested.revision));
    }
    function step(cursor) {
      if (cursor) {
        params.set("cursor", cursor);
      }
      return request("/assets?" + params.toString()).then(function (result) {
        var body = result.body || {};
        (body.assets || []).forEach(function (asset) {
          if (ids.length < SNAPSHOT_CAP) {
            ids.push(Number(asset.asset_id));
          }
        });
        if (body.has_more && body.next_cursor && ids.length < SNAPSHOT_CAP) {
          return step(body.next_cursor);
        }
        return ids;
      });
    }
    setStatus(els.suggestedStatus, "正在冻结建议结果快照…");
    step(null)
      .then(function (frozen) {
        state.suggested.frozen = frozen;
        state.selection = {};
        frozen.forEach(function (assetId) {
          state.selection[String(assetId)] = true;
        });
        setStatus(
          els.suggestedStatus,
          "已冻结 " +
            frozen.length +
            " 个 asset_id 快照（suggestion_revision #" +
            state.suggested.revision +
            "）。提交时按这批 id 分批，不会重跑动态查询。" +
            (frozen.length >= SNAPSHOT_CAP ? "（已达本页上限 " + SNAPSHOT_CAP + "）" : "")
        );
        updateSelectionSummary();
      })
      .catch(function (failure) {
        setStatus(
          els.suggestedStatus,
          (failure.code === "PLACE_SUGGESTION_REVISION_STALE"
            ? "建议集合已变化，需重新加载："
            : "冻结快照失败：") + describe(failure),
          "error"
        );
      });
  }

  function runBatch(action) {
    setError(els.batchError, "");
    var ids = selectedIds();
    if (!ids.length) {
      setError(els.batchError, "请先勾选资产（或用「全选本页」/「冻结全部结果快照」）。");
      return;
    }
    var target = null;
    if (action === "assign") {
      target = integerOrNull(els.targetPlaceId.value);
      if (target === null) {
        setError(els.batchError, "批量确认归属必须填写目标 place_id（可先用名称搜索填入）。");
        return;
      }
    }
    clear(els.batchLog);
    setStatus(els.suggestedStatus, "正在读取所选资产的 revision…");
    collectItemRevisions(ids)
      .then(function (states) {
        var chunks = [];
        for (var index = 0; index < states.length; index += MAX_BATCH) {
          chunks.push(states.slice(index, index + MAX_BATCH));
        }
        setStatus(
          els.suggestedStatus,
          "已冻结 " +
            states.length +
            " 个资产；按每批 ≤" +
            MAX_BATCH +
            " 项提交，共 " +
            chunks.length +
            " 批。"
        );
        return chunks.reduce(function (chain, chunk, chunkIndex) {
          return chain.then(function (stopped) {
            if (stopped) {
              appendBatchLog(
                chunkIndex + 1,
                chunk.length,
                "未提交（前一批失败，后续批次已停止）",
                true
              );
              return true;
            }
            var items = chunk.map(function (item) {
              return {
                asset_id: item.asset_id,
                action: action,
                target_place_id: action === "assign" ? target : null,
                expected_revision: item.expected_revision,
              };
            });
            return request("/place-assignments/batch", {
              method: "POST",
              body: { request_id: newRequestId("ui-batch"), items: items },
            })
              .then(function (result) {
                var body = result.body || {};
                appendBatchLog(
                  chunkIndex + 1,
                  chunk.length,
                  "已提交 " +
                    String(body.operation_type) +
                    " · operation " +
                    String(body.operation_id) +
                    " · changed " +
                    (body.changed ? "是" : "否") +
                    " · 重放 " +
                    (body.replayed ? "是" : "否"),
                  false
                );
                return false;
              })
              .catch(function (failure) {
                appendBatchLog(
                  chunkIndex + 1,
                  chunk.length,
                  "失败 " + describe(failure) + "（整批未部分提交）",
                  true
                );
                return true;
              });
          });
        }, Promise.resolve(false));
      })
      .then(function () {
        state.suggested.frozen = null;
        loadSuggested(true, false);
        loadDirectory(true);
        loadOperations(true);
      })
      .catch(function (failure) {
        setError(els.batchError, "批量操作失败：" + describe(failure));
      });
  }

  function collectItemRevisions(ids) {
    var states = [];
    var chain = Promise.resolve();
    ids.forEach(function (assetId) {
      chain = chain.then(function () {
        return request("/assets/" + encodeURIComponent(assetId) + "/place").then(function (result) {
          var body = result.body || {};
          var assignment = body.assignment;
          states.push({
            asset_id: Number(assetId),
            expected_revision: assignment ? Number(assignment.revision) : 0,
          });
        });
      });
    });
    return chain.then(function () {
      return states;
    });
  }

  function appendBatchLog(index, size, message, failed) {
    els.batchLog.appendChild(
      element("li", failed ? "error" : null, "第 " + index + " 批（" + size + " 项）：" + message)
    );
  }

  function appendResultLog(message, failed) {
    els.batchLog.appendChild(element("li", failed ? "error" : null, message));
  }
  // ------------------------------------------------------------------ operations
  function loadOperations(reset) {
    if (state.operations.loading) {
      return;
    }
    state.operations.loading = true;
    if (reset) {
      state.operations.cursor = null;
      state.operations.hasMore = false;
      clear(els.operationsList);
    }
    setStatus(els.operationsStatus, "正在读取操作日志…");
    var params = new URLSearchParams();
    params.set("limit", String(DEFAULT_LIMIT));
    if (state.operations.cursor) {
      params.set("cursor", state.operations.cursor);
    }
    request("/place-operations?" + params.toString())
      .then(function (result) {
        var body = result.body || {};
        var operations = body.operations || [];
        operations.forEach(function (operation) {
          els.operationsList.appendChild(buildOperationRow(operation));
        });
        state.operations.cursor = body.next_cursor || null;
        state.operations.hasMore = Boolean(body.has_more);
        state.operations.loading = false;
        els.operationsMore.disabled = !state.operations.hasMore;
        setStatus(
          els.operationsStatus,
          operations.length
            ? "已加载 " + operations.length + " 条操作" + (body.has_more ? "，还有更多。" : "。")
            : "还没有地点操作记录。"
        );
      })
      .catch(function (failure) {
        state.operations.loading = false;
        els.operationsMore.disabled = true;
        setStatus(els.operationsStatus, "读取操作日志失败：" + describe(failure), "error");
      });
  }

  function buildOperationRow(operation) {
    var item = element("li", "card place-row");
    var head = element("p", "place-head");
    head.appendChild(element("span", "chip", String(operation.operation_type)));
    head.appendChild(element("span", "chip chip-id", "rev " + operation.operation_revision));
    head.appendChild(element("span", "chip", String(operation.status)));
    if (operation.undo_of_operation_id) {
      head.appendChild(element("span", "chip", "撤销自 " + operation.undo_of_operation_id));
    }
    item.appendChild(head);

    var facts = element("dl", "media-list");
    [
      ["operation_id", String(operation.operation_id)],
      ["request_id", String(operation.request_id)],
      ["提交时间", formatMoment(operation.committed_at || operation.created_at)],
      ["catalog_filter_revision", "#" + operation.catalog_filter_revision],
      [
        "撤销了",
        operation.undo_of_operation_id ? String(operation.undo_of_operation_id) : "不是撤销操作",
      ],
    ].forEach(function (pair) {
      facts.appendChild(element("dt", null, pair[0]));
      facts.appendChild(element("dd", null, pair[1]));
    });
    item.appendChild(facts);

    var actions = element("p", "row-actions");
    var undo = element("button", null, "撤销此操作");
    undo.type = "button";
    undo.addEventListener("click", function () {
      undoOperation(operation);
    });
    actions.appendChild(undo);
    item.appendChild(actions);
    return item;
  }

  function undoOperation(operation) {
    appendUndoLog(
      "正在撤销 " +
        String(operation.operation_id) +
        "（expected_operation_revision " +
        operation.operation_revision +
        "）…",
      false
    );
    request("/place-operations/" + encodeURIComponent(operation.operation_id) + "/undo", {
      method: "POST",
      body: {
        request_id: newRequestId("ui-undo"),
        expected_operation_revision: Number(operation.operation_revision),
      },
    })
      .then(function (result) {
        var body = result.body || {};
        appendUndoLog(
          "撤销成功：新操作 " +
            String(body.operation_id) +
            "（" +
            String(body.operation_type) +
            "）补偿了 " +
            String(operation.operation_id) +
            "，changed " +
            (body.changed ? "是" : "否") +
            "。",
          false
        );
        loadDirectory(true);
        loadSuggested(true, false);
        loadOperations(true);
      })
      .catch(function (failure) {
        appendUndoLog("撤销失败：" + describe(failure), true);
        if (failure.code === "PLACE_OPERATION_NOT_UNDOABLE") {
          appendUndoLog(
            "该操作已被撤销，或它的目标已被后续编辑改变，整批都不再可撤销；" +
              "旧日志不会被删除，也不会覆盖新状态。已刷新目录与建议。",
            true
          );
          loadDirectory(true);
          loadSuggested(true, false);
        } else if (failure.code === "PLACE_IDEMPOTENCY_CONFLICT") {
          appendUndoLog(
            "本次 request_id 已绑定不同的请求内容，请重新点击撤销（会生成新的 request_id）。",
            true
          );
        }
      });
  }

  function appendUndoLog(message, failed) {
    els.undoLog.appendChild(element("li", failed ? "error" : null, message));
  }

  // ---------------------------------------------------------------------- picker
  function searchPlacesForPicker() {
    var name = els.pickerName.value.trim();
    clear(els.pickerResults);
    els.pickerResults.appendChild(element("li", "hint", "正在搜索地点…"));
    var params = new URLSearchParams();
    params.set("limit", "20");
    if (name) {
      params.set("name", name);
    }
    request("/places?" + params.toString())
      .then(function (result) {
        var body = result.body || {};
        var places = body.places || [];
        clear(els.pickerResults);
        if (!places.length) {
          els.pickerResults.appendChild(element("li", "hint", "没有匹配的地点。"));
          return;
        }
        places.forEach(function (place) {
          state.placeIndex[String(place.place_id)] = place;
          var row = element("li", "card place-row");
          row.appendChild(
            element(
              "p",
              null,
              placeKindText(place) + "「" + place.canonical_name + "」#" + place.place_id
            )
          );
          var chain = element("p", "chain", "");
          paintChain(chain, place);
          row.appendChild(chain);
          var use = element("button", null, "选为目标 place_id");
          use.type = "button";
          use.addEventListener("click", function () {
            els.targetPlaceId.value = String(place.place_id);
            setStatus(
              els.suggestedStatus,
              "目标地点已设为 " +
                placeKindText(place) +
                "「" +
                place.canonical_name +
                "」#" +
                place.place_id +
                "。"
            );
          });
          row.appendChild(use);
          els.pickerResults.appendChild(row);
        });
      })
      .catch(function (failure) {
        clear(els.pickerResults);
        els.pickerResults.appendChild(
          element("li", "error", "搜索地点失败：" + describe(failure))
        );
      });
  }

  // ------------------------------------------------------------------------- boot
  function boot() {
    els = {
      appMeta: document.getElementById("app-meta"),
      notice: document.getElementById("notice"),
      datasetBody: document.getElementById("dataset-body"),
      datasetReload: document.getElementById("dataset-reload"),
      dirName: document.getElementById("dir-name"),
      dirKind: document.getElementById("dir-kind"),
      dirLifecycle: document.getElementById("dir-lifecycle"),
      dirParent: document.getElementById("dir-parent"),
      dirAncestor: document.getElementById("dir-ancestor"),
      dirLimit: document.getElementById("dir-limit"),
      directoryFilters: document.getElementById("directory-filters"),
      directoryStatus: document.getElementById("directory-status"),
      directoryList: document.getElementById("directory-list"),
      dirMore: document.getElementById("dir-more"),
      directoryRevision: document.getElementById("directory-revision"),
      createForm: document.getElementById("create-form"),
      createKind: document.getElementById("create-kind"),
      createName: document.getElementById("create-name"),
      createParent: document.getElementById("create-parent"),
      createAliases: document.getElementById("create-aliases"),
      createError: document.getElementById("create-error"),
      editPlaceId: document.getElementById("edit-place-id"),
      editLoadButton: document.getElementById("edit-load-button"),
      editForm: document.getElementById("edit-form"),
      editHeading: document.getElementById("edit-heading"),
      editName: document.getElementById("edit-name"),
      editAliases: document.getElementById("edit-aliases"),
      editRevision: document.getElementById("edit-revision"),
      editReload: document.getElementById("edit-reload"),
      editError: document.getElementById("edit-error"),
      suggestedLimit: document.getElementById("suggested-limit"),
      suggestedLoad: document.getElementById("suggested-load"),
      suggestedMore: document.getElementById("suggested-more"),
      suggestedFreeze: document.getElementById("suggested-freeze"),
      suggestedStatus: document.getElementById("suggested-status"),
      suggestedList: document.getElementById("suggested-list"),
      suggestedSelectPage: document.getElementById("suggested-select-page"),
      suggestedClear: document.getElementById("suggested-clear"),
      selectionSummary: document.getElementById("selection-summary"),
      targetPlaceId: document.getElementById("target-place-id"),
      pickerName: document.getElementById("picker-name"),
      pickerSearch: document.getElementById("picker-search"),
      pickerResults: document.getElementById("picker-results"),
      batchAssign: document.getElementById("batch-assign"),
      batchUnassign: document.getElementById("batch-unassign"),
      batchRestore: document.getElementById("batch-restore"),
      batchError: document.getElementById("batch-error"),
      batchLog: document.getElementById("batch-log"),
      operationsLoad: document.getElementById("operations-load"),
      operationsMore: document.getElementById("operations-more"),
      operationsStatus: document.getElementById("operations-status"),
      operationsList: document.getElementById("operations-list"),
      undoLog: document.getElementById("undo-log"),
    };

    els.directoryFilters.addEventListener("submit", function (event) {
      event.preventDefault();
      state.directory.limit = Number(els.dirLimit.value) || DEFAULT_LIMIT;
      loadDirectory(true);
    });
    document.getElementById("dir-reset").addEventListener("click", function () {
      els.directoryFilters.reset();
      state.directory.limit = Number(els.dirLimit.value) || DEFAULT_LIMIT;
      loadDirectory(true);
    });
    els.dirMore.addEventListener("click", function () {
      loadDirectory(false);
    });
    els.createForm.addEventListener("submit", submitCreate);
    els.editLoadButton.addEventListener("click", function () {
      loadPlaceIntoEditor(els.editPlaceId.value);
    });
    els.editForm.addEventListener("submit", submitEdit);
    els.editReload.addEventListener("click", function () {
      if (state.edit.place) {
        delete state.placeIndex[String(state.edit.place.place_id)];
      }
      loadPlaceIntoEditor(els.editPlaceId.value);
    });
    els.datasetReload.addEventListener("click", loadDatasets);

    els.suggestedLimit.addEventListener("change", function () {
      state.suggested.limit = Number(els.suggestedLimit.value) || DEFAULT_LIMIT;
    });
    els.suggestedLoad.addEventListener("click", function () {
      state.suggested.limit = Number(els.suggestedLimit.value) || DEFAULT_LIMIT;
      loadSuggested(true, false);
    });
    els.suggestedMore.addEventListener("click", function () {
      loadSuggested(false, true);
    });
    els.suggestedFreeze.addEventListener("click", freezeAll);
    els.suggestedSelectPage.addEventListener("click", selectPage);
    els.suggestedClear.addEventListener("click", function () {
      state.selection = {};
      state.suggested.frozen = null;
      Array.prototype.forEach.call(
        els.suggestedList.querySelectorAll("input[type=checkbox]"),
        function (box) {
          box.checked = false;
        }
      );
      updateSelectionSummary();
    });
    els.pickerSearch.addEventListener("click", searchPlacesForPicker);
    els.batchAssign.addEventListener("click", function () {
      runBatch("assign");
    });
    els.batchUnassign.addEventListener("click", function () {
      runBatch("unassign");
    });
    els.batchRestore.addEventListener("click", function () {
      runBatch("restore_auto");
    });
    els.operationsLoad.addEventListener("click", function () {
      loadOperations(true);
    });
    els.operationsMore.addEventListener("click", function () {
      loadOperations(false);
    });

    state.directory.limit = Number(els.dirLimit.value) || DEFAULT_LIMIT;
    state.suggested.limit = Number(els.suggestedLimit.value) || DEFAULT_LIMIT;
    updateSelectionSummary();
    probeCapabilities();
    loadDatasets();
    loadDirectory(true);
    loadOperations(true);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
