/* Personal albums reuse the protected API and its authoritative person operations. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById("pa-" + id);
  const state = { organize: false, person: null, listCursor: null, inboxCursor: null,
    photoCursor: null, photoIds: [], photoListSeq: 0, previewAssetId: null, previewListSeq: 0,
    previewMoving: false, listSeq: 0, inboxSeq: 0, photoSeq: 0, previewSeq: 0, editSeq: 0,
    choiceSeq: 0, choiceCursor: null, choiceQuery: "", rematchAfter: 0, edit: null, chosen: null,
    detailBusyAssets: new Set(),
    operation: null, undoTimer: null, messageTimer: null, detailBatchTimer: null, detailBatchSeq: 0,
    detailBatch: null, detailControlsLoaded: false, detailControlsPromise: null, writing: false, coverMode: false, searching: false, searchBody: null,
    deepLinked: false, linkSeq: 0, busy: new Set() };
  function node(tag, value, className) {
    const item = document.createElement(tag);
    if (value !== undefined) item.textContent = value;
    if (className) item.className = className;
    return item;
  }
  function button(label, action, className) {
    const item = node("button", label, className); item.type = "button";
    item.addEventListener("click", action); return item;
  }
  function name(person) { return person.name || "未命名的人"; }
  function photoId(item) { return Number(item.asset_id); }
  function photoURL(id, kind) { return "/api/v1/assets/" + id + "/" + kind; }
  // Old responses declared these reusable id URLs immutable for a year. A new
  // URL bypasses those already-fresh browser entries; new responses revalidate.
  function mediaURL(url) { return url + "?v=person-media2"; }
  function picture(url, label, className, priority) {
    const wrap = node("span", undefined, "pa-picture " + (className || ""));
    const fallback = node("span", url ? "正在加载图片…" : "没有可用人脸", "pa-image-fallback");
    wrap.append(fallback);
    if (url && /^\/api\/v1\/(faces\/[a-f0-9]{64}\/thumbnail|assets\/\d+\/(thumbnail|preview))$/.test(url)) {
      const img = node("img"); img.alt = label; img.loading = priority ? "eager" : "lazy";
      if (priority) img.fetchPriority = "high";
      img.addEventListener("load", () => wrap.classList.add("pa-image-ready"));
      img.addEventListener("error", () => { img.hidden = true; fallback.textContent = className && className.includes("avatar") ? "头像暂时无法加载" : "图片暂时无法加载"; }); img.src = mediaURL(url); wrap.append(img);
    }
    return wrap;
  }
  function requestId() { return "album-" + crypto.randomUUID(); }
  function errorText(error) {
    if (error.status === 401 || error.status === 403) return "会话已失效，请从启动器的一次性链接重新打开。";
    if (error.status === 409) return "整理状态有更新，请刷新后再试。";
    if (error.code && /MODEL|RUNTIME|PROFILE|SPACE|GENERATION/.test(error.code)) return "搜索模型尚未就绪，仍可点“全部照片”浏览相册。";
    return "这次操作没有完成，请重试。" + (error.code ? "（" + error.code + "）" : "");
  }
  async function api(path, body, method) {
    const headers = { Accept: "application/json" };
    if (body) {
      headers["Content-Type"] = "application/json";
      const csrf = document.cookie.split(";").map((part) => part.trim()).find((part) => part.startsWith("ps_csrf="));
      if (!csrf) { const e = new Error(); e.status = 401; throw e; }
      headers["X-CSRF-Token"] = decodeURIComponent(csrf.slice(8));
    }
    const response = await fetch("/api/v1" + path, { method: method || (body ? "POST" : "GET"),
      credentials: "same-origin", cache: "no-store", headers, body: body ? JSON.stringify(body) : undefined });
    const result = await response.json();
    if (!response.ok) {
      const e = new Error(); e.status = response.status;
      e.code = result.code; e.details = result.details; throw e;
    }
    return result;
  }
  function message(value, transient) {
    clearTimeout(state.messageTimer); state.messageTimer = null;
    $("status").textContent = value; $("status").hidden = !value;
    if (transient) state.messageTimer = setTimeout(() => {
      state.messageTimer = null; $("status").textContent = ""; $("status").hidden = true;
    }, 3000);
  }
  function params(values) { return "?" + new URLSearchParams(values); }
  function requestedPersonId() {
    const search = typeof window !== "undefined" && window.location ? window.location.search : "";
    const value = new URLSearchParams(search || "").get("person_id");
    if (!value || !/^[1-9]\d*$/.test(value)) return null;
    const id = Number(value);
    return Number.isSafeInteger(id) ? id : null;
  }
  async function openPersonById(personId) {
    const seq = ++state.linkSeq;
    state.deepLinked = true;
    $("list-note").textContent = "正在打开这个人的照片……";
    try {
      const detail = await api("/persons/" + encodeURIComponent(personId));
      if (seq !== state.linkSeq) return;
      const avatar = /^[a-f0-9]{64}$/.test(String(detail.avatar_face_id || ""))
        ? "/api/v1/faces/" + detail.avatar_face_id + "/thumbnail"
        : null;
      openAlbum({
        person_id: Number(detail.person_id || detail.canonical_person_id),
        name: detail.name,
        revision: detail.revision,
        asset_count: detail.asset_count,
        face_count: detail.face_count,
        thumbnail_url: avatar,
      });
    } catch (error) {
      if (seq !== state.linkSeq) return;
      state.deepLinked = false;
      $("library").hidden = false;
      $("album").hidden = true;
      loadPeople(false);
      message("无法打开人物照片：" + errorText(error));
    }
  }
  function clearPersonRoute() {
    const url = new URL(window.location.href);
    url.searchParams.delete("person_id");
    window.history.replaceState(null, "", url.pathname + url.search + url.hash);
  }
  async function loadPeople(more) {
    if (more && state.busy.has("people")) return;
    const seq = ++state.listSeq; state.busy.add("people"); $("more").disabled = true;
    const values = { kind: state.organize ? "unnamed" : "named", q: $("query").value.trim(), sort: $("person-sort").value || "photos_desc", limit: 40 };
    if (more && state.listCursor) values.cursor = state.listCursor;
    else { $("cards").replaceChildren(); state.listCursor = null; }
    $("list-note").textContent = "正在找人物……";
    try {
      const page = await api("/persons/browse" + params(values)); if (seq !== state.listSeq) return;
      page.persons.forEach((person, index) => {
        const card = button("", () => openAlbum(person), "pa-person-card");
        card.append(picture(person.thumbnail_url, name(person), "pa-avatar", index < 4), node("strong", name(person)), node("span", person.asset_count + " 张照片", "hint"));
        $("cards").append(card);
      });
      state.listCursor = page.next_cursor; $("more").hidden = !page.has_more;
      $("list-note").textContent = $("cards").children.length ? (state.organize ? "未命名的人：点开看看，给熟悉的人取名。" : "点一个人，查看全部照片。") : (values.q ? "没有找到这个名字，试试“整理一下”。" : state.organize ? "未命名分组已整理完。" : "还没有命名的人，点“整理一下”给熟悉的人取名。导入新照片后，请先完成人脸分析。" );
    } catch (error) { if (seq === state.listSeq) $("list-note").textContent = errorText(error); }
    finally { if (seq === state.listSeq) { state.busy.delete("people"); $("more").disabled = false; } }
  }
  function organizationText(face) {
    const reason = face.organization_reason || face.quality_reason;
    if (face.mode === "MANUAL_UNASSIGNED") return "已移出人物，等你指定";
    const labels = { SMALL: "脸太小，需确认", BLURRY: "脸有些模糊，需确认",
      LOW_DETECTION_SCORE: "人脸不够明确，需确认", LOW_QUALITY: "清晰度不足，需确认",
      NEAR_TIE: "像多个人，需确认", SAME_IMAGE_CONFLICT: "合照里的人选有冲突，需确认",
      WAITING_INDEX: "人脸结果正在整理", NO_MATCH: "还没有可靠的人物匹配" };
    if (labels[reason]) return labels[reason];
    if (reason === "BORDERLINE" && face.suggestions && face.suggestions.length) {
      return "像“" + name(face.suggestions[0]) + "”，需确认";
    }
    return "还没确定是谁";
  }
  function inboxSummaryText(summary) {
    const reasons = summary.reasons || {};
    const pieces = ["待整理 " + summary.total + " 张脸"];
    const labels = { SMALL: "小脸", BLURRY: "模糊", LOW_DETECTION_SCORE: "人脸不明确",
      LOW_QUALITY: "清晰度不足", BORDERLINE: "相似待确认", NEAR_TIE: "人选相近",
      SAME_IMAGE_CONFLICT: "合照冲突", MANUAL_UNASSIGNED: "已移出", WAITING_INDEX: "等待整理",
      NO_MATCH: "暂无匹配", OTHER_SUGGESTION: "其他建议" };
    Object.keys(labels).forEach((key) => { if (reasons[key]) pieces.push(labels[key] + " " + reasons[key]); });
    if (summary.held) pieces.push("另有 " + summary.held + " 张脸等待识别更新确认");
    return pieces.join(" · ");
  }
  async function loadInbox(more) {
    if (more && state.busy.has("inbox")) return;
    const seq = ++state.inboxSeq; state.busy.add("inbox"); $("inbox-more").disabled = true;
    const values = { limit: 30, kind: $("inbox-kind").value || "all" };
    if (more && state.inboxCursor) values.cursor = state.inboxCursor;
    if (!more) { $("inbox-cards").replaceChildren(); state.inboxCursor = null; }
    try {
      const [page, summary] = await Promise.all([
        api("/faces/inbox" + params(values)), more ? Promise.resolve(null) : api("/faces/inbox/summary"),
      ]);
      if (seq !== state.inboxSeq) return;
      if (summary) $("inbox-note").textContent = inboxSummaryText(summary);
      const selectedId = state.person && state.person.person_id;
      page.faces.sort((a, b) => Number(b.assigned_person_id === selectedId) - Number(a.assigned_person_id === selectedId));
      page.faces.forEach((face) => {
        const card = node("div", undefined, "pa-person-card");
        const view = button("", () => preview(face.asset_id), "pa-face-button");
        view.append(picture(face.thumbnail_url, "待整理人脸", "pa-avatar"));
        card.append(view, node("span", organizationText(face), "hint"), button("这是谁？", () => editFace(face)));
        $("inbox-cards").append(card);
      });
      state.inboxCursor = page.next_cursor; $("inbox-more").hidden = !page.has_more;
      if (!$("inbox-cards").children.length) $("inbox-cards").append(node("p", "暂时没有待归组的人脸。", "hint"));
    } catch (error) { if (seq === state.inboxSeq) message(errorText(error)); }
    finally { if (seq === state.inboxSeq) { state.busy.delete("inbox"); $("inbox-more").disabled = false; } }
  }
  async function rematchFaces() {
    if (state.writing || state.busy.has("rematch")) return;
    state.writing = true; state.busy.add("rematch"); $("rematch").disabled = true;
    message("正在利用已认出的人补匹配，请稍候……");
    try {
      const result = await api("/faces/rematch", { limit: 20, after_asset_id: state.rematchAfter });
      state.rematchAfter = result.next_after_asset_id || 0;
      $("rematch").textContent = result.has_more ? "继续补匹配剩余人脸" : "补匹配未归类人脸";
      await Promise.all([loadPeople(false), loadInbox(false)]);
      message("这批处理了 " + result.processed + " 张照片，归入已有人物 " + result.merged
        + " 张脸，新增未命名人物 " + result.new_groups + " 个。"
        + (result.has_more ? "可继续处理剩余人脸。" : "仍未归类的脸可按原因整理。")
        + (result.deferred ? "有 " + result.deferred + " 张照片还在等待分析结果。" : ""));
    } catch (error) { message("补匹配没有完成：" + errorText(error)); }
    finally { state.writing = false; state.busy.delete("rematch"); $("rematch").disabled = false; }
  }
  function detailBatchNote(batch) {
    if (!batch || batch.state === "IDLE") return "一键处理当前全部图库中机器待归类、有人物建议或原图像素更充分的小脸照片；合照按照片去重。没有检测到脸的照片不在此批次内。";
    const labels = { QUEUED: "排队中", RUNNING: "正在处理", PAUSED: "已暂停", COMPLETE: "已完成", PARTIAL_FAILED: "已完成（有失败可重试）" };
    return (labels[batch.state] || batch.state) + " · 照片 " + batch.processed + "/" + batch.total
      + " · 成功 " + batch.succeeded + " · 跳过 " + batch.skipped + " · 失败 " + batch.failed
      + " · 待确认人脸 " + batch.needs_review;
  }
  function updateDetailBatchControls(batch) {
    const previous = state.detailBatch;
    state.detailBatch = batch && batch.batch_id ? batch : null;
    $("detail-batch-note").textContent = detailBatchNote(batch);
    const stateName = batch && batch.state;
    const active = stateName === "QUEUED" || stateName === "RUNNING";
    const paused = stateName === "PAUSED";
    $("detail-batch").disabled = active || paused;
    if (previous && ["QUEUED", "RUNNING"].includes(previous.state)
        && ["COMPLETE", "PARTIAL_FAILED"].includes(stateName)) {
      loadPeople(false); if (state.organize) loadInbox(false);
    }
    $("detail-pause").hidden = !active;
    $("detail-resume").hidden = stateName !== "PAUSED";
    $("detail-retry").hidden = !(stateName === "PARTIAL_FAILED" && Number(batch.failed) > 0);
    if (active) scheduleDetailBatchPoll();
    else if (state.detailBatchTimer) { clearTimeout(state.detailBatchTimer); state.detailBatchTimer = null; }
  }
  function scheduleDetailBatchPoll() {
    if (state.detailBatchTimer) return;
    state.detailBatchTimer = setTimeout(async () => {
      state.detailBatchTimer = null;
      const seq = state.detailBatchSeq;
      try {
        const result = await api(detailBatchURL());
        if (seq === state.detailBatchSeq) updateDetailBatchControls(result);
      } catch (error) {
        if (seq === state.detailBatchSeq) {
          $("detail-batch-note").textContent = "队列状态暂时无法读取；任务仍保存在本机，正在重试。";
          scheduleDetailBatchPoll();
        }
      }
    }, 1800);
  }
  async function loadDetailBatch() {
    const seq = ++state.detailBatchSeq;
    try {
      const batch = await api(detailBatchURL());
      if (seq === state.detailBatchSeq) {
        updateDetailBatchControls(batch);
        if (!batch.batch_id || ["COMPLETE", "PARTIAL_FAILED"].includes(batch.state)) {
          const count = await api("/faces/source-detail-preview" + detailScopeQuery());
          if (seq === state.detailBatchSeq) $("detail-batch").textContent = "一键原图补识别（" + count.total + " 张）";
        }
      }
    } catch (error) { if (seq === state.detailBatchSeq) $("detail-batch-note").textContent = "队列状态暂时无法读取。"; }
  }
  async function startDetailBatch() {
    const seq = ++state.detailBatchSeq;
    $("detail-batch").disabled = true;
    try {
      const source = $("detail-source").value;
      const result = await api("/faces/source-detail-batches", {source_id: source ? Number(source) : null});
      if (seq === state.detailBatchSeq) updateDetailBatchControls(result);
      if (state.detailBatch) scheduleDetailBatchPoll();
    } catch (error) { $("detail-batch").disabled = false; message("原图补识别没有启动：" + errorText(error)); }
  }
  async function changeDetailBatch(action) {
    if (!state.detailBatch || !state.detailBatch.batch_id) return;
    const id = encodeURIComponent(state.detailBatch.batch_id);
    const seq = ++state.detailBatchSeq;
    try {
      const result = await api("/faces/source-detail-batches/" + id + "/" + action, {});
      if (seq === state.detailBatchSeq) updateDetailBatchControls(result);
    } catch (error) { message("队列操作没有完成：" + errorText(error)); }
  }
  function detailScopeQuery() {
    const source = $("detail-source").value;
    return source ? "?source_id=" + encodeURIComponent(source) : "";
  }
  function detailBatchURL() { return "/faces/source-detail-batches/active" + detailScopeQuery(); }
  async function loadDetailSources() {
    try {
      const response = await api("/sources");
      for (const source of response.sources || []) {
        const option = node("option", source.display_name || "图库 " + source.source_id);
        option.value = String(source.source_id); $("detail-source").append(option);
      }
      return true;
    } catch (error) { /* The all-library scope remains available. */ return false; }
  }
  async function loadDetailControls() {
    if (!state.organize) return;
    if (state.detailControlsPromise) return state.detailControlsPromise;
    state.detailControlsPromise = (async () => {
      if (!state.detailControlsLoaded) state.detailControlsLoaded = await loadDetailSources();
      if (state.organize) await loadDetailBatch();
    })().finally(() => { state.detailControlsPromise = null; });
    return state.detailControlsPromise;
  }
  function setView(organize) {
    state.organize = organize;
    if (!organize) {
      state.detailBatchSeq++;
      if (state.detailBatchTimer) { clearTimeout(state.detailBatchTimer); state.detailBatchTimer = null; }
    }
    $("people").setAttribute("aria-pressed", String(!organize));
    $("organize").setAttribute("aria-pressed", String(organize)); $("inbox").hidden = !organize;
    $("query").value = ""; loadPeople(false); if (organize) loadInbox(false);
    if (organize) loadDetailControls();
  }
  function albumHeading() {
    $("album-avatar").replaceChildren(picture(state.person.thumbnail_url, "当前相册的人", "pa-avatar"));
    $("title").textContent = name(state.person); $("name").textContent = state.person.name ? "改名字" : "取名字";
    $("cover").textContent = state.coverMode ? "取消选择封面" : "更换封面";
    $("album-note").textContent = state.person.asset_count + " 张照片 · 合照可出现在不同人的相册，下面的小头像标出这次看的是谁";
  }
  function openAlbum(person) {
    state.person = person; state.coverMode = false; $("library").hidden = true; $("album").hidden = false;
    $("photo-query").value = ""; state.searching = false; albumHeading(); loadPhotos(false);
    $("back").focus();
  }
  function renderPhotos(items) {
    items.forEach((item) => {
      const id = photoId(item); if (state.photoIds.includes(id)) return;
      state.photoIds.push(id);
      const card = button("", () => preview(id), "pa-photo-card");
      card.setAttribute("aria-label", "查看照片 " + id);
      card.append(picture(photoURL(id, "thumbnail"), "照片 " + id, "pa-photo", state.photoIds.length < 3));
      if (item.person_faces && item.person_faces.length) {
        const faces = node("span", undefined, "pa-photo-person");
        faces.append(node("span", "这张照片里的这个人"));
        item.person_faces.forEach((face) => faces.append(picture(face.thumbnail_url, "本相册人物的人脸", "pa-choice-avatar")));
        if (item.person_faces_has_more) faces.append(node("span", "更多人脸请点开查看"));
        card.append(faces);
      }
      const time = item.taken_time;
      if (time && time.taken_at) card.append(node("span", time.taken_at.slice(0, 10) + (time.label === "capture" ? "" : " · 文件时间"), "pa-photo-date"));
      $("photos").append(card);
    });
  }
  async function loadPhotos(more) {
    if (!state.person || (more && state.busy.has("photos"))) return false;
    const seq = ++state.photoSeq; state.busy.add("photos"); $("photos-more").disabled = true;
    const id = state.person.person_id;
    if (!more) {
      $("photos").replaceChildren(); state.photoCursor = null; state.photoIds = []; state.photoListSeq++;
      updatePreviewNavigation();
    }
    $("photo-note").textContent = "正在加载照片……";
    try {
      let page;
      if (state.searching) {
        const body = { ...state.searchBody };
        if (more && state.photoCursor) { body.cursor = state.photoCursor; body.search_session_id = state.searchSession; }
        page = await api("/search", body);
      } else {
        const values = { limit: 60 }; if (more && state.photoCursor) values.cursor = state.photoCursor;
        page = await api("/persons/" + id + "/photos" + params(values));
      }
      if (seq !== state.photoSeq || !state.person || id !== state.person.person_id) return false;
      if (state.searching) state.searchSession = page.search_session_id;
      if (page.person) { state.person = page.person; albumHeading(); }
      renderPhotos(page.assets || page.hits || []); state.photoCursor = page.next_cursor;
      $("photos-more").hidden = !page.has_more && !page.next_cursor;
      $("photo-note").textContent = $("photos").children.length ? (state.coverMode ? "点一张喜欢的照片，再选这个人的脸设为封面。" : state.searching ? "搜索结果仅包含这个人的照片。点“全部照片”返回相册。" + (page.candidates_truncated ? " 搜索结果较多，请缩小查询范围。" : "") : "点照片查看大图；认错了可选择对应的人脸更正。") : (state.searching ? "没有找到匹配的照片。" : "这个人物还没有照片。");
      return true;
    } catch (error) { if (seq === state.photoSeq) { $("photo-note").textContent = errorText(error); $("photos-more").hidden = true; } return false; }
    finally { if (seq === state.photoSeq) { state.busy.delete("photos"); $("photos-more").disabled = false; updatePreviewNavigation(); } }
  }
  function previewPosition() {
    if (!state.person || state.previewListSeq !== state.photoListSeq) return -1;
    return state.photoIds.indexOf(state.previewAssetId);
  }
  function updatePreviewNavigation() {
    const index = previewPosition(); const enabled = index >= 0 && $("preview").open;
    $("preview-nav").hidden = !enabled;
    $("preview-position").textContent = enabled ? "第 " + (index + 1) + " 张" : "";
    $("preview-prev").disabled = !enabled || index === 0 || state.previewMoving || state.writing;
    $("preview-next").disabled = !enabled || state.previewMoving || state.writing ||
      (index === state.photoIds.length - 1 && !state.photoCursor);
  }
  async function movePreview(step) {
    if (state.previewMoving || state.writing || !$("preview").open) return;
    const index = previewPosition(); if (index < 0 || index + step < 0) return;
    const listSeq = state.photoListSeq; const previewSeq = state.previewSeq;
    state.previewMoving = true; updatePreviewNavigation();
    try {
      if (index + step >= state.photoIds.length) {
        if (!state.photoCursor) return;
        const loaded = await loadPhotos(true);
        if (previewSeq !== state.previewSeq || listSeq !== state.photoListSeq || !$("preview").open) return;
        if (!loaded) { $("preview-note").textContent = "下一批照片没有加载成功，请再点“下一张”重试。"; return; }
      }
      if (listSeq !== state.photoListSeq || !$("preview").open) return;
      const next = state.photoIds[index + step];
      if (next !== undefined) await preview(next);
    } finally { state.previewMoving = false; updatePreviewNavigation(); }
  }
  async function preview(id) {
    const seq = ++state.previewSeq; $("preview-faces").replaceChildren();
    if (state.previewInfoMount) state.previewInfoMount.dispose();
    state.previewAssetId = id; state.previewListSeq = state.photoListSeq;
    $("review").hidden = true;
    $("preview-note").textContent = "正在读取这张照片的人脸……";
    const image = $("preview-image"); image.hidden = false;
    image.onerror = () => { if (seq === state.previewSeq) { image.hidden = true; $("preview-note").textContent = "大图暂时不可用；照片来源可能已离线。"; } };
    image.src = mediaURL(photoURL(id, "preview"));
    if (!$("preview").open) $("preview").showModal();
    if (typeof window !== "undefined" && window.AIPhotoInfo) state.previewInfoMount = window.AIPhotoInfo.render($("preview-photo-info"), id);
    updatePreviewNavigation();
    try {
      const page = await api("/assets/" + id + "/faces"); if (seq !== state.previewSeq || !$("preview").open) return;
      const selectedId = state.person && state.person.person_id;
      page.faces.sort((a, b) => Number(b.assigned_person_id === selectedId) - Number(a.assigned_person_id === selectedId));
      page.faces.forEach((face) => {
        if (state.coverMode && face.assigned_person_id !== selectedId) return;
        const card = node("div", undefined, "pa-person-card");
        if (face.assigned_person_id === selectedId) {
          card.classList.add("pa-selected-person"); card.append(node("strong", "本相册的人"));
        }
        card.append(picture(face.thumbnail_url, "选择这张脸", "pa-avatar"), node("span", face.assigned_person_name ? "当前：" + face.assigned_person_name : face.assigned_person_id ? "当前：未命名的人" : "还没确定是谁"));
        if (face.source_detail) card.append(node("span", "原图细节结果 · "
          + (face.assigned_person_id ? "已归入人物" : face.automatic_detail ? "待可靠匹配" : "待人工确认"), "hint"));
        if (state.person && face.assigned_person_id === selectedId) {
          const cover = button("设为封面", () => saveCover(face.face_id, cover));
          card.append(cover);
        }
        if (!state.coverMode) card.append(button("更正这张脸", () => { $("preview").close(); editFace(face); }));
        $("preview-faces").append(card);
      });
      const hasDetail = page.faces.some((face) => face.source_detail);
      $("face-detail").hidden = state.coverMode || hasDetail;
      $("face-detail").disabled = state.detailBusyAssets.has(Number(id));
      $("preview-note").textContent = state.coverMode ? ($("preview-faces").children.length ? "选择喜欢的人脸，设为这个人物的封面。" : "这里没有当前人物的可用人脸，请换一张照片。") : page.faces.some(face => face.automatic_detail) ? "已按原图完成识别；未可靠归类的人脸可继续更正。" : hasDetail ? "原图细节结果只供人工检查；请用“更正这张脸”手动指定人物，不会自动归组。" : page.faces.length ? "可为本相册的人设置封面；认错了也可更正对应人脸。" + (page.has_more ? "此处仅展示前 200 张脸。" : "") : "没有可整理的人脸；尚未分析、没有检测到或识别更新待确认时会出现这种情况。";
      $("review").hidden = page.faces.length !== 0;
    } catch (error) { if (seq === state.previewSeq) $("preview-note").textContent = errorText(error); }
  }
  async function sourceDetail() {
    const assetId = state.previewAssetId;
    const previewSeq = state.previewSeq;
    if (!assetId || state.detailBusyAssets.has(Number(assetId)) || !$("preview").open) return;
    state.detailBusyAssets.add(Number(assetId));
    const control = $("face-detail"); control.disabled = true;
    control.textContent = "已排队，等待原图处理……";
    $("preview-note").textContent = "正在排队处理这张照片。原图细节结果会单独显示，需你手动指定人物。";
    const stillCurrent = () => previewSeq === state.previewSeq && assetId === state.previewAssetId && $("preview").open;
    try {
      const queued = await api("/assets/" + assetId + "/face-detail", {}, "POST");
      if (queued.already_available) {
        control.hidden = true;
        $("preview-note").textContent = "这张照片已有原图细节识别结果；结果仅用于人工检查与指定人物。";
        if (queued.job_id && stillCurrent()) await preview(assetId);
        return;
      }
      const deadline = Date.now() + 180000;
      while (stillCurrent() && Date.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, 500));
        if (!stillCurrent()) return;
        const job = await api("/jobs/" + encodeURIComponent(queued.job_id));
        if (job.state === "SUCCEEDED") {
          control.hidden = true;
          $("preview-note").textContent = "原图细节识别完成。新结果需你手动指定人物，不会自动归组。";
          await preview(assetId);
          return;
        }
        if (["FAILED", "BLOCKED", "CANCELLED"].includes(job.state)) {
          control.disabled = false; control.textContent = "重试原图细节识别";
          $("preview-note").textContent = "原图细节识别没有完成（" + job.state + "），可重试；已有结果和人物归属未改动。";
          return;
        }
        $("preview-note").textContent = job.state === "RUNNING"
          ? "正在用原图细节处理这张照片……请稍候。"
          : "这张照片已排队等待原图细节处理……";
      }
      if (stillCurrent()) {
        control.disabled = false; control.textContent = "查看原图细节任务";
        $("preview-note").textContent = "任务仍在队列中；关闭照片不会取消它，稍后可重新查看状态。";
      }
    } catch (error) {
      if (stillCurrent()) {
        control.disabled = false; control.textContent = "重试原图细节识别";
        $("preview-note").textContent = "原图细节识别没有排入队列：" + errorText(error);
      }
    } finally {
      state.detailBusyAssets.delete(Number(assetId));
      if (!control.hidden && state.previewAssetId === assetId) control.disabled = false;
    }
  }
  async function saveCover(faceId, control) {
    if (!state.person || state.writing) return;
    const person = state.person; const previewSeq = state.previewSeq;
    state.writing = true; control.disabled = true; updatePreviewNavigation();
    try {
      const result = await api("/persons/" + person.person_id + "/cover",
        { face_id: faceId, expected_revision: person.revision }, "PUT");
      loadPeople(false);
      if (state.person && state.person.person_id === person.person_id) {
        state.person = result.person; state.coverMode = false; albumHeading(); loadPhotos(false);
        if (previewSeq === state.previewSeq) $("preview").close();
        message("已更换人物封面。", true);
      }
    } catch (error) { if (previewSeq === state.previewSeq) $("preview-note").textContent = errorText(error); }
    finally { state.writing = false; control.disabled = false; updatePreviewNavigation(); }
  }
  function editBase(edit, title, note) {
    if (state.writing) return false;
    state.edit = edit; state.chosen = null; state.editSeq++; state.choiceSeq++;
    $("edit-title").textContent = title; $("edit-note").textContent = note;
    $("edit-error").textContent = ""; $("choices").replaceChildren(); $("choice-more").hidden = true;
    $("edit-query").value = edit.mode === "name" ? edit.person.name || "" : "";
    $("edit-query").disabled = false;
    $("edit-find").hidden = edit.mode === "name"; $("edit-label").textContent = edit.mode === "name" ? "名字" : "搜索并选择人物";
    $("edit-submit").textContent = edit.mode === "name" ? "保存名字" : edit.mode === "merge" ? "确认并入所选人物" : "归到所选人物";
    $("edit-submit").disabled = edit.mode !== "name";
    $("edit").showModal(); $("edit-query").focus(); return true;
  }
  function editFace(face) {
    if (editBase({ mode: "face", face }, "这张脸是谁？", "只整理选中的这张脸。合照里的其他人保持原来的归属。")) {
      const crop = picture(face.thumbnail_url, "正在更正的人脸", "pa-avatar"); $("choices").append(crop);
      if (face.assigned_person_id) $("choices").append(button("不是这个人，移出人物", () => writeFace("unassign")));
      loadChoices(false);
    }
  }
  function editMerge(person) {
    if (editBase({ mode: "merge", person }, "这是同一个人", "选择已有的人物，把这个分组的照片并进去。确认后可撤销。")) loadChoices(false);
  }
  async function loadChoices(more) {
    if (!state.edit || state.edit.mode === "name" || state.writing) return;
    const seq = ++state.choiceSeq; const editSeq = state.editSeq;
    const query = $("edit-query").value.trim(); const values = { kind: "all", q: query, limit: 20 };
    if (more && state.choiceCursor && query === state.choiceQuery) values.cursor = state.choiceCursor;
    else $("choices").querySelectorAll(".pa-choice").forEach((item) => item.remove());
    state.choiceQuery = query; state.chosen = null; $("edit-submit").disabled = true;
    $("choice-more").disabled = true;
    try {
      const page = await api("/persons/browse" + params(values)); if (seq !== state.choiceSeq || editSeq !== state.editSeq) return;
      page.persons.forEach((person) => {
        if (state.edit.person && person.person_id === state.edit.person.person_id) return;
        const card = button("", () => {
          state.chosen = person; $("choices").querySelectorAll(".pa-choice").forEach((item) => item.setAttribute("aria-pressed", "false"));
          card.setAttribute("aria-pressed", "true"); $("edit-submit").disabled = false;
        }, "pa-choice"); card.setAttribute("aria-pressed", "false");
        card.append(picture(person.thumbnail_url, name(person), "pa-choice-avatar"), node("span", name(person) + " · " + person.asset_count + " 张")); $("choices").append(card);
      });
      state.choiceCursor = page.next_cursor; $("choice-more").hidden = !page.has_more;
      $("edit-error").textContent = page.persons.length ? "" : "没有找到人物，试试其他名字。";
    } catch (error) { if (seq === state.choiceSeq) $("edit-error").textContent = errorText(error); }
    finally { if (seq === state.choiceSeq) $("choice-more").disabled = false; }
  }
  function rememberOperation(operation, label) {
    state.operation = operation; $("undo-text").textContent = label;
    $("undo-bar").hidden = !operation.changed; $("undo").disabled = false;
    clearTimeout(state.undoTimer);
    if (operation.changed) state.undoTimer = setTimeout(() => {
      state.operation = null; $("undo-bar").hidden = true;
    }, 10000);
  }
  async function refreshAfterWrite(result, label) {
    rememberOperation(result, label); message(label, true); $("edit").close(); state.editSeq++; state.choiceSeq++;
    loadPeople(false); if (state.organize) loadInbox(false);
    if (state.person) {
      const id = state.person.person_id;
      try { const person = await api("/persons/" + id); if (!state.person || id !== state.person.person_id) return;
        state.person = person; albumHeading(); state.searching = false; $("photo-query").value = ""; loadPhotos(false); }
      catch (error) { message(label + "。" + errorText(error)); }
    }
  }
  async function write(path, body, method, label) {
    if (state.writing) return;
    state.writing = true; $("edit-submit").disabled = true; $("edit-cancel").disabled = true;
    $("edit-error").textContent = "正在保存……";
    try { const result = await api(path, body, method); await refreshAfterWrite(result, label); }
    catch (error) {
      if (error.details && error.details.reason === "NAME_IN_USE" && state.edit.mode === "name") {
        const source = state.edit.person;
        try {
          const target = await api("/persons/" + error.details.target_person_id);
          state.edit = { mode: "merge", person: source }; state.chosen = target;
          $("edit-title").textContent = "已有这个名字的人";
          $("edit-note").textContent = "“" + name(target) + "”已经存在。确认后会把这两组照片并成一个人物。";
          $("edit-query").disabled = true;
          $("edit-submit").textContent = "确认并入“" + name(target) + "”"; $("edit-error").textContent = "";
        } catch (lookupError) { $("edit-error").textContent = errorText(lookupError); }
      } else { $("edit-error").textContent = errorText(error) + (error.status === 409 ? " 关闭窗口，点刷新后重新选择；未保存你的更改。" : ""); }
    } finally { state.writing = false; $("edit-submit").disabled = state.edit && state.edit.mode !== "name" && !state.chosen; $("edit-cancel").disabled = false; }
  }
  function writeFace(action) {
    const edit = state.edit; if (!edit || edit.mode !== "face" || (action === "assign" && !state.chosen)) return;
    const body = { contract_version: "p3-face-correction-contracts.v1", request_id: requestId(),
      action, expected_revision: edit.face.control_revision };
    if (action === "assign") body.target_person_id = state.chosen.person_id;
    write("/faces/" + edit.face.face_id + "/assignment", body, "PUT", action === "assign" ? "已归到“" + name(state.chosen) + "”" : "已移出人物，之后可在“整理一下”找到这张脸");
  }
  function submitEdit(event) {
    event.preventDefault(); const edit = state.edit; if (!edit || state.writing) return;
    if (edit.mode === "name") {
      const value = $("edit-query").value.trim(); if (!value) { $("edit-error").textContent = "请填写名字。"; return; }
      write("/persons/" + edit.person.person_id + "/label", { request_id: requestId(), name: value, expected_revision: edit.person.revision }, "PUT", "已保存名字“" + value + "”");
    } else if (!state.chosen) loadChoices(false);
    else if (edit.mode === "face") writeFace("assign");
    else if (state.chosen) write("/persons/merge", { request_id: requestId(), target_person_id: state.chosen.person_id,
      target_expected_revision: state.chosen.revision, sources: [{ person_id: edit.person.person_id, expected_revision: edit.person.revision }] }, "POST", "已并入“" + name(state.chosen) + "”");
  }
  async function undo() {
    if (!state.operation || state.writing) return;
    const operation = state.operation; state.writing = true; $("undo").disabled = true;
    try {
      await api("/person-operations/" + encodeURIComponent(operation.operation_id) + "/undo", { request_id: requestId(), expected_operation_revision: operation.operation_revision });
      clearTimeout(state.undoTimer); state.operation = null; $("undo-bar").hidden = true; message("已撤销刚才的整理。", true);
      loadPeople(false); if (state.organize) loadInbox(false);
      if (state.person) { state.person = await api("/persons/" + state.person.person_id); albumHeading(); state.searching = false; loadPhotos(false); }
    } catch (error) { message("无法撤销：" + errorText(error)); }
    finally { state.writing = false; $("undo").disabled = false; }
  }
  function boot() {
    $("people").addEventListener("click", () => setView(false)); $("organize").addEventListener("click", () => setView(true));
    $("find").addEventListener("submit", (event) => { event.preventDefault(); loadPeople(false); });
    $("query").addEventListener("search", () => loadPeople(false));
    $("person-sort").addEventListener("change", () => loadPeople(false));
    $("refresh").addEventListener("click", () => { loadPeople(false); if (state.organize) loadInbox(false); });
    $("more").addEventListener("click", () => loadPeople(true)); $("inbox-more").addEventListener("click", () => loadInbox(true));
    $("inbox-kind").addEventListener("change", () => loadInbox(false));
    $("rematch").addEventListener("click", rematchFaces);
    $("detail-batch").addEventListener("click", startDetailBatch);
    $("detail-source").addEventListener("change", loadDetailBatch);
    $("detail-pause").addEventListener("click", () => changeDetailBatch("pause"));
    $("detail-resume").addEventListener("click", () => changeDetailBatch("resume"));
    $("detail-retry").addEventListener("click", () => changeDetailBatch("retry"));
    $("back").addEventListener("click", () => {
      state.person = null; state.photoSeq++; state.busy.delete("photos");
      $("library").hidden = false; $("album").hidden = true; $("people").focus();
      if (state.deepLinked) { state.deepLinked = false; state.linkSeq++; clearPersonRoute(); loadPeople(false); }
    });
    $("name").addEventListener("click", () => editBase({ mode: "name", person: { ...state.person } }, "给这个人取名字", "可用家人的称呼或你习惯的名字。"));
    $("merge").addEventListener("click", () => editMerge({ ...state.person }));
    $("cover").addEventListener("click", () => {
      if (state.writing || !state.person) return;
      state.coverMode = !state.coverMode; state.searching = false; $("photo-query").value = "";
      albumHeading(); loadPhotos(false);
    });
    $("photos-more").addEventListener("click", () => loadPhotos(true));
    $("search-photos").addEventListener("submit", (event) => {
      event.preventDefault(); const text = $("photo-query").value.trim(); if (!text) { state.searching = false; loadPhotos(false); return; }
      state.searching = true; state.searchBody = { contract_version: "p2-contracts.v1", request_id: requestId(), text, person_ids: [state.person.person_id], person_match: "any", page_size: 60, top_k: 5000 }; loadPhotos(false);
    });
    $("all-photos").addEventListener("click", () => { state.searching = false; $("photo-query").value = ""; loadPhotos(false); });
    $("preview-close").addEventListener("click", () => $("preview").close());
    $("face-detail").addEventListener("click", sourceDetail);
    $("preview-prev").addEventListener("click", () => movePreview(-1));
    $("preview-next").addEventListener("click", () => movePreview(1));
    $("review").addEventListener("click", () => { $("preview").close(); const advanced = document.getElementById("advanced-tools"); advanced.open = true; advanced.querySelector("summary").focus(); });
    $("preview").addEventListener("close", () => {
      state.previewSeq++; state.previewAssetId = null; $("preview-image").removeAttribute("src");
      if (state.previewInfoMount) state.previewInfoMount.dispose(); state.previewInfoMount = null;
      updatePreviewNavigation();
    });
    $("edit-form").addEventListener("submit", submitEdit);
    $("edit-find").addEventListener("click", () => loadChoices(false)); $("choice-more").addEventListener("click", () => loadChoices(true));
    $("edit-query").addEventListener("input", () => { if (state.edit && state.edit.mode !== "name") { state.chosen = null; state.choiceSeq++; $("edit-submit").disabled = true; } });
    $("edit-cancel").addEventListener("click", () => $("edit").close());
    $("edit").addEventListener("cancel", (event) => { if (state.writing) event.preventDefault(); });
    $("edit").addEventListener("close", () => { state.editSeq++; state.choiceSeq++; });
    $("undo").addEventListener("click", undo);
    $("undo-dismiss").addEventListener("click", () => { clearTimeout(state.undoTimer); state.undoTimer = null; state.operation = null; $("undo-bar").hidden = true; });
    const linkedPersonId = requestedPersonId();
    if (linkedPersonId) openPersonById(linkedPersonId);
    else loadPeople(false);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
