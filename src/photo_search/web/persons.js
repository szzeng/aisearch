/* Local person-curation page for the read-only API plus the frozen P3-03..P3-05 write
 * surface.
 *
 * Ground rules this file keeps (P3-06):
 *  - every piece of data comes from the session-protected /api/v1 endpoints; the page never
 *    opens the catalog, a source root or a file;
 *  - the CSRF token is read from the ps_csrf cookie and sent in X-CSRF-Token only: it is
 *    never stored, never logged and never put in a URL;
 *  - the page never receives or renders face geometry, an embedding, a score, an internal
 *    naming key, an operation record's stored values, or a local absolute path;
 *  - the page never decides a conflict: same-name merging, revision checks, idempotency,
 *    split eligibility and undo compensability are all answers the server gives;
 *  - nothing is loaded from the network except this same origin.
 */
(function () {
  "use strict";

  var API = "/api/v1";
  var PERSON_LIMIT = 25;
  var FACE_LIMIT = 50;
  var OPERATION_LIMIT = 25;
  var MAX_BATCH = 500;

  var VIEW_TEXT = {
    named: "已命名人物",
    unnamed: "未命名分组",
    manual_unassigned: "待手动归类",
  };
  var MODE_TEXT = {
    //: ``AUTO_ALLOWED`` means "no human lock on this face" - the machine may still have
    //: linked it to somebody, which the row states separately as 有效归属.
    AUTO_ALLOWED: "自动（没有人工锁）",
    MANUAL_ASSIGNED: "人工指定（带锁）",
    MANUAL_UNASSIGNED: "人工取消归类（带锁）",
  };
  var OPERATION_TEXT = {
    RENAME: "保存名字",
    MERGE_BY_NAME: "同名合并",
    MERGE: "合并",
    BATCH_MERGE: "批量合并",
    UNDO: "撤销",
    FACE_ASSIGN: "指定归属",
    FACE_UNASSIGN: "取消归类",
    FACE_RESTORE_AUTO: "恢复自动",
    BATCH_FACE_EDIT: "批量脸纠错",
    SPLIT: "拆分",
  };
  var STATUS_TEXT = { COMMITTED: "已提交", UNDONE: "已撤销（已被补偿）" };
  var SESSION_HINT = "请使用启动器打印的一次性链接建立会话。";
  //: A drive-absolute or UNC spelling inside server-side text is never rendered.
  var PATH_LIKE = /(?:[A-Za-z]:[\\/])|(?:\\\\[^\s'"])/;
  var PATH_NOTE = "错误详情包含本机路径，已省略。";

  //: A write whose refusal means "the state moved while you were looking at it": the page
  //: reloads the affected lists instead of leaving a half-updated view behind.
  var CONFLICT_CODES = {
    PERSON_REVISION_CONFLICT: true,
    PERSON_IDEMPOTENCY_CONFLICT: true,
    PERSON_NOT_FOUND: true,
    PERSON_REDIRECT_CYCLE: true,
    PERSON_SPLIT_CONFLICT: true,
    FACE_ASSIGNMENT_CONFLICT: true,
    FACE_IDEMPOTENCY_CONFLICT: true,
    FACE_STATE_CONFLICT: true,
    FACE_NOT_FOUND: true,
    PERSON_OPERATION_NOT_UNDOABLE: true,
    FACE_OPERATION_NOT_UNDOABLE: true,
    INVALID_CURSOR: true,
  };

  var state = {
    sessionMissing: false,
    requestCounter: 0,
    view: "named",
    persons: { limit: PERSON_LIMIT, cursor: null, hasMore: false, loading: false },
    faces: {
      personId: null,
      canonical: null,
      revision: null,
      limit: FACE_LIMIT,
      cursor: null,
      hasMore: false,
      loading: false,
    },
    unassigned: { limit: FACE_LIMIT, cursor: null, hasMore: false, loading: false },
    operations: { limit: OPERATION_LIMIT, cursor: null, hasMore: false, loading: false },
    index: {},
    faceSelection: {},
    mergeSelection: {},
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

  function safeText(value) {
    if (value === null || value === undefined) {
      return "";
    }
    return PATH_LIKE.test(String(value)) ? PATH_NOTE : String(value);
  }

  function errorText(code) {
    if (code === "PERSON_REVISION_CONFLICT") {
      return "人物 revision 已过期，需重新加载后再提交";
    }
    if (code === "FACE_ASSIGNMENT_CONFLICT") {
      return "脸的控制 revision 已过期，需重新加载后再提交";
    }
    if (code === "PERSON_IDEMPOTENCY_CONFLICT") {
      return "request_id 已绑定不同的请求内容";
    }
    if (code === "FACE_IDEMPOTENCY_CONFLICT") {
      return "request_id 已绑定不同的请求内容";
    }
    if (code === "FACE_STATE_CONFLICT") {
      return "该脸当前的人工控制状态与请求不一致";
    }
    if (code === "FACE_OPERATION_NOT_UNDOABLE") {
      return "该脸级操作已被后续人工编辑改变，不能整批撤销";
    }
    if (code === "PERSON_OPERATION_NOT_UNDOABLE") {
      return "该操作已被后续编辑改变，不能整批撤销";
    }
    if (code === "PERSON_SPLIT_CONFLICT") {
      return "拆分被拒绝：所选脸已变化或不满足拆分条件";
    }
    if (code === "PERSON_BATCH_TOO_LARGE" || code === "FACE_BATCH_TOO_LARGE") {
      return "单批超过 500 项，请拆分";
    }
    if (code === "PERSON_NAME_INVALID") {
      return "名字不能为空或过长";
    }
    if (code === "PERSON_NOT_FOUND" || code === "FACE_NOT_FOUND") {
      return "人物或人脸不存在（可能已被合并或重新检测）";
    }
    if (code === "INVALID_CURSOR") {
      return "分页游标无效，需重新加载";
    }
    return "";
  }

  function describe(failure) {
    var base = "[" + failure.code + "] " + safeText(failure.message);
    var hint = errorText(failure.code);
    if (hint) {
      base += "（" + hint + "）";
    }
    var reason = failure.details && failure.details.reason;
    if (reason) {
      base += "（reason: " + safeText(reason) + "）";
    }
    return failure.request_id ? base + "（请求编号 " + failure.request_id + "）" : base;
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

  //: One printable-ASCII idempotency key per submitted write.
  function newRequestId(prefix) {
    state.requestCounter += 1;
    var random = Math.floor(Math.random() * 0x100000000).toString(36);
    var parts = [prefix, Date.now().toString(36), state.requestCounter.toString(36), random];
    return parts.join("-").slice(0, 64);
  }

  function integerOrNull(value) {
    var trimmed = String(value === undefined || value === null ? "" : value).trim();
    if (!/^[0-9]+$/.test(trimmed)) {
      return null;
    }
    var number = Number(trimmed);
    return isFinite(number) && number > 0 ? number : null;
  }

  function handleConflict(failure, node) {
    // The refusal is shown with the server's own code and reason; the caller always refreshes
    // the affected lists afterwards, so a stale view is never left on screen and a failed
    // write is never rendered as a success.
    setError(node, describe(failure));
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
        var faces = body.faces || {};
        text(
          els.appMeta,
          "版本 " +
            body.app_version +
            " · 图库 schema " +
            schema +
            "（" +
            body.schema_status +
            "） · 人脸事实资产 " +
            (faces.assets_with_faces === undefined ? "未知" : faces.assets_with_faces) +
            " · 只读受控访问 + 本机人工整理"
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

  // ------------------------------------------------------------------ person list
  function selectView(view) {
    state.view = view;
    var views = ["named", "unnamed", "manual_unassigned"];
    views.forEach(function (name) {
      var tab = els.tabs[name];
      tab.setAttribute("aria-selected", name === view ? "true" : "false");
    });
    var manual = view === "manual_unassigned";
    els.personsList.hidden = manual;
    els.facesList.hidden = !manual;
    els.personsMore.disabled = true;
    if (manual) {
      loadUnassigned(true);
    } else {
      loadPersons(true);
    }
  }

  function loadPersons(reset) {
    if (state.persons.loading) {
      return;
    }
    if (reset) {
      state.persons.cursor = null;
      state.persons.hasMore = false;
      state.index = {};
    }
    state.persons.loading = true;
    var limit = state.persons.limit;
    var query = "?kind=" + encodeURIComponent(state.view) + "&limit=" + limit;
    if (!reset && state.persons.cursor) {
      query += "&cursor=" + encodeURIComponent(state.persons.cursor);
    }
    setStatus(
      els.personsStatus,
      reset ? "正在读取" + VIEW_TEXT[state.view] + "…" : "正在读取下一页…"
    );
    request("/persons" + query)
      .then(function (result) {
        var body = result.body || {};
        var rows = body.persons || [];
        if (reset) {
          clear(els.personsList);
          els.personsList.setAttribute(
            "aria-label",
            VIEW_TEXT[state.view] + "（不含已合并的旧 id）"
          );
        }
        rows.forEach(function (person) {
          state.index[String(person.person_id)] = person;
          els.personsList.appendChild(buildPersonCard(person));
        });
        state.persons.cursor = body.next_cursor || null;
        state.persons.hasMore = Boolean(body.has_more);
        els.personsMore.disabled = !body.has_more;
        setStatus(
          els.personsStatus,
          rows.length === 0
            ? reset
              ? VIEW_TEXT[state.view] + "：没有记录（这里是空视图，不是读取失败）"
              : "没有更多记录"
            : "本页 " +
                rows.length +
                " 条 · " +
                VIEW_TEXT[state.view] +
                (body.has_more ? " · 还有更多" : " · 已到末尾")
        );
      })
      .catch(function (failure) {
        setStatus(els.personsStatus, "读取人物列表失败：" + describe(failure), "error");
        if (CONFLICT_CODES[failure.code]) {
          refreshAll();
        }
      })
      .then(function () {
        state.persons.loading = false;
      });
  }

  function avatarNode(person) {
    var holder;
    if (!person.thumbnail_url) {
      holder = element("div", "avatar-placeholder", "暂无头像");
      return holder;
    }
    var image = element("img", "avatar");
    image.alt = (person.name || "未命名人物") + " 的头像";
    image.loading = "lazy";
    image.src = person.thumbnail_url;
    // A refused crop (offline source, bad media, a face that moved) shows a placeholder
    // instead of a broken image, and never a path.
    image.addEventListener("error", function () {
      var fallback = element("div", "avatar-placeholder", "头像不可用");
      if (image.parentNode) {
        image.parentNode.replaceChild(fallback, image);
      }
    });
    return image;
  }

  function buildPersonCard(person) {
    var item = element("li", "person-card");
    item.appendChild(avatarNode(person));
    var body = element("div");
    var head = element("p", "card-head");
    head.appendChild(
      element(
        "strong",
        null,
        person.name === null || person.name === undefined
          ? "未命名分组"
          : safeText(person.name)
      )
    );
    head.appendChild(element("span", "chip chip-id", "#" + person.person_id));
    head.appendChild(element("span", "chip", "revision " + person.revision));
    body.appendChild(head);
    body.appendChild(
      element(
        "p",
        "hint",
        "有效成员：脸 " +
          person.face_count +
          " · 照片 " +
          person.asset_count +
          "（不含人工取消归类的脸与已排除的脸）"
      )
    );
    var actions = element("p", "row-actions");
    var choose = element("button", null, "载入此人的脸");
    choose.type = "button";
    choose.addEventListener("click", function () {
      els.facesPersonId.value = String(person.person_id);
      loadFaces(person.person_id, true);
    });
    var name = element("button", null, "保存名字 / 同名合并");
    name.type = "button";
    name.addEventListener("click", function () {
      els.namePersonId.value = String(person.person_id);
      els.nameRevision.value = String(person.revision);
      els.nameValue.value = person.name === null || person.name === undefined ? "" : person.name;
      els.nameValue.focus();
    });
    var merge = element("button", null, "加入批量合并选择");
    merge.type = "button";
    merge.addEventListener("click", function () {
      state.mergeSelection[String(person.person_id)] = person;
      updateMergeSummary();
    });
    actions.appendChild(choose);
    actions.appendChild(name);
    actions.appendChild(merge);
    body.appendChild(actions);
    item.appendChild(body);
    return item;
  }

  // ------------------------------------------------------- manual-unassigned queue
  function loadUnassigned(reset) {
    if (state.unassigned.loading) {
      return;
    }
    if (reset) {
      state.unassigned.cursor = null;
      state.unassigned.hasMore = false;
      clear(els.facesList);
    }
    state.unassigned.loading = true;
    var query = "?assignment_state=manual_unassigned&limit=" + state.unassigned.limit;
    if (!reset && state.unassigned.cursor) {
      query += "&cursor=" + encodeURIComponent(state.unassigned.cursor);
    }
    setStatus(els.personsStatus, reset ? "正在读取待手动归类…" : "正在读取下一页…");
    request("/faces" + query)
      .then(function (result) {
        var body = result.body || {};
        var rows = body.faces || [];
        rows.forEach(function (face) {
          els.facesList.appendChild(buildFaceCard(face, "unassigned"));
        });
        state.unassigned.cursor = body.next_cursor || null;
        state.unassigned.hasMore = Boolean(body.has_more);
        els.personsMore.disabled = !body.has_more;
        setStatus(
          els.personsStatus,
          rows.length === 0
            ? "待手动归类：没有记录（这里是空视图，不是读取失败）"
            : "本页 " +
                rows.length +
                " 张待手动归类脸" +
                (body.has_more ? " · 还有更多" : " · 已到末尾")
        );
      })
      .catch(function (failure) {
        setStatus(els.personsStatus, "读取待手动归类失败：" + describe(failure), "error");
      })
      .then(function () {
        state.unassigned.loading = false;
      });
  }

  // ------------------------------------------------------------------ face lists
  function loadFaces(personId, reset) {
    var id = integerOrNull(personId);
    if (id === null) {
      setStatus(els.facesStatus, "请输入正整数 person_id。", "error");
      return;
    }
    if (state.faces.loading) {
      return;
    }
    if (reset) {
      state.faces.personId = id;
      state.faces.cursor = null;
      state.faces.hasMore = false;
      state.faceSelection = {};
      clear(els.personFaces);
      els.facesMore.disabled = true;
    }
    state.faces.loading = true;
    var query = "?person_id=" + id + "&limit=" + state.faces.limit;
    if (!reset && state.faces.cursor) {
      query += "&cursor=" + encodeURIComponent(state.faces.cursor);
    }
    setStatus(els.facesStatus, reset ? "正在读取此人的成员脸…" : "正在读取下一页…");
    request("/faces" + query)
      .then(function (result) {
        var body = result.body || {};
        var rows = body.faces || [];
        state.faces.canonical = body.canonical_person_id;
        state.faces.revision = body.person_revision;
        rows.forEach(function (face) {
          els.personFaces.appendChild(buildFaceCard(face, "person"));
        });
        state.faces.cursor = body.next_cursor || null;
        state.faces.hasMore = Boolean(body.has_more);
        els.facesMore.disabled = !body.has_more;
        var summary =
          "#" +
          body.requested_person_id +
          (body.redirected ? " 已合并到 #" + body.canonical_person_id : "") +
          " · 本页 " +
          rows.length +
          " 张脸 · 人物 revision " +
          body.person_revision +
          (body.has_more ? " · 还有更多" : " · 已到末尾");
        text(els.facesSummary, summary);
        setStatus(
          els.facesStatus,
          rows.length === 0 ? "这个人物当前没有有效成员脸（空视图，不是读取失败）。" : summary
        );
        updateFaceSelectionSummary();
      })
      .catch(function (failure) {
        setStatus(els.facesStatus, "读取脸列表失败：" + describe(failure), "error");
        if (CONFLICT_CODES[failure.code]) {
          refreshAll();
        }
      })
      .then(function () {
        state.faces.loading = false;
      });
  }

  function buildFaceCard(face, origin) {
    var item = element("li", "person-card face-card");
    item.dataset.faceId = String(face.face_id);
    var image = element("img", "avatar");
    image.alt = "人脸 " + String(face.face_id).slice(0, 12) + " 的缩略图";
    image.loading = "lazy";
    image.src = face.thumbnail_url;
    image.addEventListener("error", function () {
      var fallback = element("div", "avatar-placeholder", "脸图不可用");
      if (image.parentNode) {
        image.parentNode.replaceChild(fallback, image);
      }
    });
    item.appendChild(image);

    var body = element("div");
    var head = element("p", "card-head");
    head.appendChild(
      element("span", "chip chip-id", "face " + String(face.face_id).slice(0, 10) + "…")
    );
    head.appendChild(element("span", "chip chip-lock", MODE_TEXT[face.mode] || face.mode));
    head.appendChild(element("span", "chip", "控制 revision " + face.control_revision));
    if (face.suggestions_available) {
      head.appendChild(element("span", "chip chip-suggest", "有机器建议待确认"));
    }
    body.appendChild(head);
    body.appendChild(
      element(
        "p",
        "hint",
        "所属照片 asset #" +
          face.asset_id +
          " · 有效归属 " +
          (face.assigned_person_id === null || face.assigned_person_id === undefined
            ? "无"
            : "#" + face.assigned_person_id) +
          (face.assignment_revision === null || face.assignment_revision === undefined
            ? ""
            : " · 归属 revision " + face.assignment_revision)
      )
    );

    var actions = element("p", "row-actions");
    var check = element("input");
    check.type = "checkbox";
    check.id = "pick-" + origin + "-" + face.face_id;
    check.checked = Boolean(state.faceSelection[face.face_id]);
    check.addEventListener("change", function () {
      if (check.checked) {
        state.faceSelection[face.face_id] = face;
      } else {
        delete state.faceSelection[face.face_id];
      }
      updateFaceSelectionSummary();
    });
    var label = element("label", "check-line", "选择这张脸");
    label.setAttribute("for", check.id);
    var pick = element("p", "check-line");
    pick.appendChild(check);
    pick.appendChild(label);
    actions.appendChild(pick);

    var single = element("button", null, "只对这一张脸…");
    single.type = "button";
    single.addEventListener("click", function () {
      state.faceSelection = {};
      state.faceSelection[face.face_id] = face;
      updateFaceSelectionSummary();
      showNotice(
        "已把选择收缩为这一张脸（face " +
          String(face.face_id).slice(0, 10) +
          "…）。同一张照片的其它脸不会被一起改动。"
      );
    });
    actions.appendChild(single);
    body.appendChild(actions);
    item.appendChild(body);
    return item;
  }

  function selectedFaces() {
    var ids = Object.keys(state.faceSelection);
    ids.sort();
    return ids.map(function (id) {
      return state.faceSelection[id];
    });
  }

  function updateFaceSelectionSummary() {
    var rows = selectedFaces();
    var fromLoaded = 0;
    rows.forEach(function (face) {
      if (state.faces.personId !== null && face.assigned_person_id === state.faces.canonical) {
        fromLoaded += 1;
      }
    });
    text(
      els.facesSummary,
      "已选择 " + rows.length + " 张脸" + (fromLoaded ? "（其中 " + fromLoaded + " 张属于 #" + state.faces.canonical + "）" : "")
    );
  }

  // ------------------------------------------------------------------ name form
  function submitName(event) {
    event.preventDefault();
    setError(els.nameError, "");
    text(els.nameResult, "");
    var personId = integerOrNull(els.namePersonId.value);
    var revision = integerOrNull(els.nameRevision.value);
    var name = String(els.nameValue.value || "").trim();
    if (personId === null || revision === null || !name) {
      setError(els.nameError, "请填写 person_id、姓名和 expected_revision。");
      return;
    }
    if (!els.nameSameConfirm.checked) {
      setError(
        els.nameError,
        "请先确认「同名时会执行合并」；页面不会替你静默合并，也不会自己判断同名。"
      );
      return;
    }
    request("/persons/" + personId + "/name", {
      method: "PUT",
      body: {
        request_id: newRequestId("p3-06-name"),
        name: name,
        expected_revision: revision,
      },
    })
      .then(function (result) {
        var body = result.body || {};
        text(
          els.nameResult,
          "操作 " +
            body.operation_id +
            " 已记录（" +
            (OPERATION_TEXT[body.operation_type] || body.operation_type) +
            "，revision " +
            body.operation_revision +
            "，changed=" +
            body.changed +
            (body.replayed ? "，replayed=true（同一 request_id 的重放）" : "") +
            "）。"
        );
        // The server owns the same-name rule: the page reads the id back and shows where it
        // now points instead of reproducing the server's name comparison itself.
        return request("/persons/" + personId).then(function (lookup) {
          var person = lookup.body || {};
          var line =
            "#" +
            personId +
            (person.redirected
              ? " 已同名合并到 #" + person.canonical_person_id + "（后续都会显示合并后的人物）"
              : " 的姓名已保存为「" + safeText(person.name) + "」（revision " + person.revision + "）");
          text(els.nameResult, (els.nameResult.textContent || "") + " " + line);
        });
      })
      .catch(function (failure) {
        handleConflict(failure, els.nameError);
      })
      .then(function () {
        refreshAll();
      });
  }

  // ------------------------------------------------------------------ merge form
  function updateMergeSummary() {
    var ids = Object.keys(state.mergeSelection);
    ids.sort();
    if (!ids.length) {
      text(els.mergeSummary, "尚未选择任何人物。");
      return;
    }
    var parts = ids.map(function (id) {
      var person = state.mergeSelection[id];
      var label = person.name === null || person.name === undefined ? "未命名" : person.name;
      return "#" + id + "（" + label + "，revision " + person.revision + "）";
    });
    text(els.mergeSummary, "合并来源：" + parts.join("、"));
  }

  function submitMerge(event) {
    event.preventDefault();
    setError(els.mergeError, "");
    text(els.mergeResult, "");
    var target = integerOrNull(els.mergeTarget.value);
    var ids = Object.keys(state.mergeSelection);
    if (target === null) {
      setError(els.mergeError, "请填写目标 person_id。");
      return;
    }
    var sources = [];
    ids.forEach(function (id) {
      if (Number(id) === target) {
        return;
      }
      var person = state.mergeSelection[id];
      if (!person) {
        return;
      }
      sources.push({ person_id: person.person_id, expected_revision: person.revision });
    });
    if (!sources.length) {
      setError(els.mergeError, "请至少选择一个人物作为合并来源（目标不能同时是来源）。");
      return;
    }
    var targetPerson = state.index[String(target)];
    if (!targetPerson) {
      setError(
        els.mergeError,
        "目标人物不在当前列表里：请先在人物列表里载入它，再重试（服务器仍会校验 revision）。"
      );
      return;
    }
    request("/persons/merge", {
      method: "POST",
      body: {
        request_id: newRequestId("p3-06-merge"),
        target_person_id: target,
        target_expected_revision: targetPerson.revision,
        sources: sources,
      },
    })
      .then(function (result) {
        var body = result.body || {};
        text(
          els.mergeResult,
          "合并操作 " +
            body.operation_id +
            "（revision " +
            body.operation_revision +
            "，changed=" +
            body.changed +
            "）：" +
            sources
              .map(function (source) {
                return "#" + source.person_id;
              })
              .join("、") +
            " → #" +
            target
        );
        state.mergeSelection = {};
        updateMergeSummary();
      })
      .catch(function (failure) {
        handleConflict(failure, els.mergeError);
      })
      .then(function () {
        refreshAll();
      });
  }

  // ------------------------------------------------------- face batch operations
  function runFaceBatch(action) {
    setError(els.faceBatchError, "");
    var rows = selectedFaces();
    if (!rows.length) {
      setError(els.faceBatchError, "请先勾选至少一张脸。");
      return;
    }
    if (rows.length > MAX_BATCH) {
      setError(els.faceBatchError, "单批最多 " + MAX_BATCH + " 张脸，请分批提交。");
      return;
    }
    var target = null;
    if (action === "assign") {
      target = integerOrNull(els.batchTargetPersonId.value);
      if (target === null) {
        setError(els.faceBatchError, "指定归属需要目标 person_id。");
        return;
      }
    }
    var items = rows.map(function (face) {
      return {
        face_id: face.face_id,
        action: action,
        target_person_id: action === "assign" ? target : null,
        expected_revision: face.control_revision,
      };
    });
    request("/face-assignments/batch", {
      method: "POST",
      body: {
        contract_version: "p3-face-correction-contracts.v1",
        request_id: newRequestId("p3-06-faces"),
        items: items,
      },
    })
      .then(function (result) {
        var body = result.body || {};
        setStatus(
          els.faceBatchError,
          "批量操作 " +
            body.operation_id +
            "（revision " +
            body.operation_revision +
            "，changed=" +
            body.changed +
            "，清空排除 " +
            (body.items || [])
              .map(function (item) {
                return (item.exclusions && item.exclusions.deactivated
                  ? item.exclusions.deactivated.length
                  : 0);
              })
              .reduce(function (left, right) {
                return left + right;
              }, 0) +
            "）"
        );
        state.faceSelection = {};
      })
      .catch(function (failure) {
        handleConflict(failure, els.faceBatchError);
      })
      .then(function () {
        refreshAll();
      });
  }

  function submitSplit(event) {
    event.preventDefault();
    setError(els.splitError, "");
    text(els.splitResult, "");
    var rows = selectedFaces();
    if (!rows.length) {
      setError(els.splitError, "请先勾选要拆出的脸。");
      return;
    }
    if (rows.length > MAX_BATCH) {
      setError(els.splitError, "一次最多拆 " + MAX_BATCH + " 张脸，请分批提交。");
      return;
    }
    if (state.faces.personId === null || state.faces.revision === null) {
      setError(els.splitError, "请先载入源人物的脸列表（拆分需要该人物的 revision）。");
      return;
    }
    if (!els.splitConfirm.checked) {
      setError(els.splitError, "请先确认要把所选脸拆成一个新的未命名人物。");
      return;
    }
    var faces = [];
    for (var index = 0; index < rows.length; index += 1) {
      var face = rows[index];
      if (
        face.assignment_revision === null ||
        face.assignment_revision === undefined ||
        face.control_revision === undefined ||
        face.control_revision === null
      ) {
        setError(
          els.splitError,
          "选中的 face " +
            String(face.face_id).slice(0, 10) +
            "… 没有有效归属 revision，不能拆分；请先恢复自动或重新指定归属。"
        );
        return;
      }
      faces.push({
        face_id: face.face_id,
        expected_control_revision: face.control_revision,
        expected_assignment_revision: face.assignment_revision,
      });
    }
    request("/persons/" + state.faces.personId + "/split", {
      method: "POST",
      body: {
        contract_version: "p3-person-split-contracts.v1",
        request_id: newRequestId("p3-06-split"),
        source_expected_revision: state.faces.revision,
        faces: faces,
      },
    })
      .then(function (result) {
        var body = result.body || {};
        text(
          els.splitResult,
          "拆分操作 " +
            body.operation_id +
            "（revision " +
            body.operation_revision +
            "）已把 " +
            faces.length +
            " 张脸从 #" +
            body.canonical_source_person_id +
            " 拆到新的未命名人物 #" +
            body.new_person_id +
            "。"
        );
        state.faceSelection = {};
        if (body.new_person_id) {
          els.facesPersonId.value = String(body.new_person_id);
        }
      })
      .catch(function (failure) {
        handleConflict(failure, els.splitError);
      })
      .then(function () {
        refreshAll();
      });
  }

  // ------------------------------------------------------------------- operations
  function loadOperations(reset) {
    if (state.operations.loading) {
      return;
    }
    if (reset) {
      state.operations.cursor = null;
      state.operations.hasMore = false;
      clear(els.operationsList);
    }
    state.operations.loading = true;
    var query = "?limit=" + state.operations.limit;
    if (!reset && state.operations.cursor) {
      query += "&cursor=" + encodeURIComponent(state.operations.cursor);
    }
    setStatus(els.operationsStatus, reset ? "正在读取最近操作…" : "正在读取更早的操作…");
    request("/person-operations" + query)
      .then(function (result) {
        var body = result.body || {};
        var rows = body.operations || [];
        rows.forEach(function (operation) {
          els.operationsList.appendChild(buildOperationRow(operation));
        });
        state.operations.cursor = body.next_cursor || null;
        state.operations.hasMore = Boolean(body.has_more);
        els.operationsMore.disabled = !body.has_more;
        setStatus(
          els.operationsStatus,
          rows.length === 0
            ? "还没有人物操作记录（这里是空视图，不是读取失败）"
            : "本页 " + rows.length + " 条" + (body.has_more ? " · 还有更早的" : " · 已到最早")
        );
      })
      .catch(function (failure) {
        setStatus(els.operationsStatus, "读取操作日志失败：" + describe(failure), "error");
      })
      .then(function () {
        state.operations.loading = false;
      });
  }

  function buildOperationRow(operation) {
    var item = element("li", "place-row");
    var head = element("p", "card-head");
    head.appendChild(
      element("strong", null, OPERATION_TEXT[operation.operation_type] || operation.operation_type)
    );
    head.appendChild(element("span", "chip", STATUS_TEXT[operation.status] || operation.status));
    head.appendChild(element("span", "chip", "revision " + operation.operation_revision));
    if (operation.changed) {
      head.appendChild(element("span", "chip chip-lock", "有实际改动"));
    }
    item.appendChild(head);
    item.appendChild(
      element(
        "p",
        "hint",
        "" +
          formatMoment(operation.committed_at) +
          " · 影响 " +
          operation.item_count +
          " 项" +
          (operation.person_ids && operation.person_ids.length
            ? " · 人物 " +
              operation.person_ids
                .map(function (id) {
                  return "#" + id;
                })
                .join("、")
            : "") +
          (operation.face_ids && operation.face_ids.length
            ? " · 人脸 " + operation.face_ids.length + " 张"
            : "") +
          (operation.undo_of_operation_id
            ? " · 补偿 " + operation.undo_of_operation_id
            : "") +
          (operation.undone_by_operation_id
            ? " · 已被 " + operation.undone_by_operation_id + " 补偿"
            : "")
      )
    );
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

  function appendUndoLog(message, failed) {
    var line = element("li", failed ? "error" : null, message);
    els.undoLog.insertBefore(line, els.undoLog.firstChild);
  }

  function undoOperation(operation) {
    request("/person-operations/" + encodeURIComponent(operation.operation_id) + "/undo", {
      method: "POST",
      body: {
        request_id: newRequestId("p3-06-undo"),
        expected_operation_revision: operation.operation_revision,
      },
    })
      .then(function (result) {
        var body = result.body || {};
        appendUndoLog(
          "已撤销 " +
            operation.operation_id +
            "（新操作 " +
            body.operation_id +
            " revision " +
            body.operation_revision +
            "，changed=" +
            body.changed +
            (body.replayed ? "，replayed=true" : "") +
            "）",
          false
        );
        // The compensated operation now reads UNDONE, so the log is reloaded instead of
        // leaving a stale "已提交" on screen.
        loadOperations(true);
      })
      .catch(function (failure) {
        appendUndoLog("撤销被拒绝：" + describe(failure), true);
        if (CONFLICT_CODES[failure.code] || failure.status === 409) {
          refreshAll();
        }
      });
  }

  // ---------------------------------------------------------------------- refresh
  function refreshAll() {
    if (state.view === "manual_unassigned") {
      loadUnassigned(true);
    } else {
      loadPersons(true);
    }
    if (state.faces.personId !== null) {
      loadFaces(state.faces.personId, true);
    }
    loadOperations(true);
  }

  // ------------------------------------------------------------------------- boot
  function boot() {
    els = {
      appMeta: document.getElementById("app-meta"),
      notice: document.getElementById("notice"),
      tabs: {
        named: document.getElementById("tab-named"),
        unnamed: document.getElementById("tab-unnamed"),
        manual_unassigned: document.getElementById("tab-unassigned"),
      },
      personsLimit: document.getElementById("persons-limit"),
      personsReload: document.getElementById("persons-reload"),
      personsMore: document.getElementById("persons-more"),
      personsStatus: document.getElementById("persons-status"),
      personsList: document.getElementById("persons-list"),
      facesList: document.getElementById("faces-list"),
      nameForm: document.getElementById("name-form"),
      namePersonId: document.getElementById("name-person-id"),
      nameValue: document.getElementById("name-value"),
      nameRevision: document.getElementById("name-revision"),
      nameSameConfirm: document.getElementById("name-same-confirm"),
      nameError: document.getElementById("name-error"),
      nameResult: document.getElementById("name-result"),
      facesForm: document.getElementById("faces-form"),
      facesPersonId: document.getElementById("faces-person-id"),
      facesSelectPage: document.getElementById("faces-select-page"),
      facesClear: document.getElementById("faces-clear"),
      facesSelectMerge: document.getElementById("faces-select-merge"),
      facesStatus: document.getElementById("faces-status"),
      facesMore: document.getElementById("faces-more"),
      facesSummary: document.getElementById("faces-summary"),
      personFaces: document.getElementById("person-faces"),
      batchTargetPersonId: document.getElementById("batch-target-person-id"),
      batchAssign: document.getElementById("batch-assign"),
      batchUnassign: document.getElementById("batch-unassign"),
      batchRestore: document.getElementById("batch-restore"),
      faceBatchError: document.getElementById("face-batch-error"),
      splitForm: document.getElementById("split-form"),
      splitConfirm: document.getElementById("split-confirm"),
      splitError: document.getElementById("split-error"),
      splitResult: document.getElementById("split-result"),
      mergeForm: document.getElementById("merge-form"),
      mergeTarget: document.getElementById("merge-target"),
      mergeClear: document.getElementById("merge-clear"),
      mergeSummary: document.getElementById("merge-summary"),
      mergeError: document.getElementById("merge-error"),
      mergeResult: document.getElementById("merge-result"),
      operationsLoad: document.getElementById("operations-load"),
      operationsMore: document.getElementById("operations-more"),
      operationsStatus: document.getElementById("operations-status"),
      operationsList: document.getElementById("operations-list"),
      undoLog: document.getElementById("undo-log"),
    };

    Object.keys(els.tabs).forEach(function (name) {
      els.tabs[name].addEventListener("click", function () {
        selectView(name);
      });
    });
    els.personsLimit.addEventListener("change", function () {
      state.persons.limit = Number(els.personsLimit.value) || PERSON_LIMIT;
      state.unassigned.limit = state.persons.limit;
      if (state.view === "manual_unassigned") {
        loadUnassigned(true);
      } else {
        loadPersons(true);
      }
    });
    els.personsReload.addEventListener("click", function () {
      selectView(state.view);
    });
    els.personsMore.addEventListener("click", function () {
      if (state.view === "manual_unassigned") {
        loadUnassigned(false);
      } else {
        loadPersons(false);
      }
    });
    els.nameForm.addEventListener("submit", submitName);
    els.facesForm.addEventListener("submit", function (event) {
      event.preventDefault();
      loadFaces(els.facesPersonId.value, true);
    });
    els.facesMore.addEventListener("click", function () {
      loadFaces(state.faces.personId, false);
    });
    els.facesSelectPage.addEventListener("click", function () {
      // "全选本页脸" applies to the list the user is currently looking at: the person's
      // members, or the manual-unassigned queue.
      var list = els.personsList.hidden ? els.facesList : els.personFaces;
      var boxes = list.querySelectorAll("input[type=checkbox]");
      Array.prototype.forEach.call(boxes, function (box) {
        box.checked = true;
        box.dispatchEvent(new Event("change"));
      });
      updateFaceSelectionSummary();
    });
    els.facesClear.addEventListener("click", function () {
      state.faceSelection = {};
      Array.prototype.forEach.call(
        els.personFaces.querySelectorAll("input[type=checkbox]"),
        function (box) {
          box.checked = false;
        }
      );
      Array.prototype.forEach.call(
        els.facesList.querySelectorAll("input[type=checkbox]"),
        function (box) {
          box.checked = false;
        }
      );
      updateFaceSelectionSummary();
    });
    els.facesSelectMerge.addEventListener("click", function () {
      var id = Number(state.faces.canonical);
      var person = state.index[String(id)];
      if (!person) {
        showNotice("请先在人物列表里载入这个人物，再加入合并选择。");
        return;
      }
      state.mergeSelection[String(id)] = person;
      updateMergeSummary();
      showNotice("已把 #" + id + " 加入批量合并选择。");
    });
    els.batchAssign.addEventListener("click", function () {
      runFaceBatch("assign");
    });
    els.batchUnassign.addEventListener("click", function () {
      runFaceBatch("unassign");
    });
    els.batchRestore.addEventListener("click", function () {
      runFaceBatch("restore_auto");
    });
    els.splitForm.addEventListener("submit", submitSplit);
    els.mergeForm.addEventListener("submit", submitMerge);
    els.mergeClear.addEventListener("click", function () {
      state.mergeSelection = {};
      updateMergeSummary();
    });
    els.operationsLoad.addEventListener("click", function () {
      loadOperations(true);
    });
    els.operationsMore.addEventListener("click", function () {
      loadOperations(false);
    });

    probeCapabilities();
    selectView("named");
    loadOperations(true);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
