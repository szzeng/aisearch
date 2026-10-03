/* Personal albums reuse the protected API and its authoritative person operations. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById("pa-" + id);
  const state = { organize: false, person: null, listCursor: null, inboxCursor: null,
    photoCursor: null, listSeq: 0, inboxSeq: 0, photoSeq: 0, previewSeq: 0, editSeq: 0,
    choiceSeq: 0, choiceCursor: null, choiceQuery: "", edit: null, chosen: null,
    operation: null, undoTimer: null, writing: false, searching: false, searchBody: null, busy: new Set() };
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
  function picture(url, label, className) {
    const wrap = node("span", undefined, "pa-picture " + (className || ""));
    const fallback = node("span", url ? "正在加载图片…" : "没有可用人脸", "pa-image-fallback");
    wrap.append(fallback);
    if (url && /^\/api\/v1\/(faces\/[a-f0-9]{64}\/thumbnail|assets\/\d+\/(thumbnail|preview))$/.test(url)) {
      const img = node("img"); img.alt = label; img.loading = "lazy";
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
  function message(value) { $("status").textContent = value; }
  function params(values) { return "?" + new URLSearchParams(values); }
  async function loadPeople(more) {
    if (more && state.busy.has("people")) return;
    const seq = ++state.listSeq; state.busy.add("people"); $("more").disabled = true;
    const values = { kind: state.organize ? "unnamed" : "named", q: $("query").value.trim(), limit: 40 };
    if (more && state.listCursor) values.cursor = state.listCursor;
    else { $("cards").replaceChildren(); state.listCursor = null; }
    $("list-note").textContent = "正在找人物……";
    try {
      const page = await api("/persons/browse" + params(values)); if (seq !== state.listSeq) return;
      page.persons.forEach((person) => {
        const card = button("", () => openAlbum(person), "pa-person-card");
        card.append(picture(person.thumbnail_url, name(person), "pa-avatar"), node("strong", name(person)), node("span", person.asset_count + " 张照片", "hint"));
        $("cards").append(card);
      });
      state.listCursor = page.next_cursor; $("more").hidden = !page.has_more;
      $("list-note").textContent = $("cards").children.length ? (state.organize ? "未命名的人：点开看看，给熟悉的人取名。" : "点一个人，查看全部照片。") : (values.q ? "没有找到这个名字，试试“整理一下”。" : state.organize ? "未命名分组已整理完。" : "还没有命名的人，点“整理一下”给熟悉的人取名。导入新照片后，请先完成人脸分析。" );
    } catch (error) { if (seq === state.listSeq) $("list-note").textContent = errorText(error); }
    finally { if (seq === state.listSeq) { state.busy.delete("people"); $("more").disabled = false; } }
  }
  async function loadInbox(more) {
    if (more && state.busy.has("inbox")) return;
    const seq = ++state.inboxSeq; state.busy.add("inbox"); $("inbox-more").disabled = true;
    const values = { limit: 30 }; if (more && state.inboxCursor) values.cursor = state.inboxCursor;
    if (!more) $("inbox-cards").replaceChildren();
    try {
      const page = await api("/faces/inbox" + params(values));
      if (seq !== state.inboxSeq) return;
      const selectedId = state.person && state.person.person_id;
      page.faces.sort((a, b) => Number(b.assigned_person_id === selectedId) - Number(a.assigned_person_id === selectedId));
      page.faces.forEach((face) => {
        const card = node("div", undefined, "pa-person-card");
        const view = button("", () => preview(face.asset_id), "pa-face-button");
        view.append(picture(face.thumbnail_url, "待整理人脸", "pa-avatar"));
        card.append(view, node("span", face.mode === "MANUAL_UNASSIGNED" ? "已移出人物" : face.quality_reason ? "小脸或模糊，需确认" : "还没确定是谁", "hint"), button("这是谁？", () => editFace(face)));
        $("inbox-cards").append(card);
      });
      state.inboxCursor = page.next_cursor; $("inbox-more").hidden = !page.has_more;
      if (!$("inbox-cards").children.length) $("inbox-cards").append(node("p", "暂时没有待归组的人脸。", "hint"));
    } catch (error) { if (seq === state.inboxSeq) message(errorText(error)); }
    finally { if (seq === state.inboxSeq) { state.busy.delete("inbox"); $("inbox-more").disabled = false; } }
  }
  function setView(organize) {
    state.organize = organize; $("people").setAttribute("aria-pressed", String(!organize));
    $("organize").setAttribute("aria-pressed", String(organize)); $("inbox").hidden = !organize;
    $("query").value = ""; loadPeople(false); if (organize) loadInbox(false);
  }
  function albumHeading() {
    $("album-avatar").replaceChildren(picture(state.person.thumbnail_url, "当前相册的人", "pa-avatar"));
    $("title").textContent = name(state.person); $("name").textContent = state.person.name ? "改名字" : "取名字";
    $("album-note").textContent = state.person.asset_count + " 张照片 · 合照可出现在不同人的相册，下面的小头像标出这次看的是谁";
  }
  function openAlbum(person) {
    state.person = person; $("library").hidden = true; $("album").hidden = false;
    $("photo-query").value = ""; state.searching = false; albumHeading(); loadPhotos(false);
    $("back").focus();
  }
  function renderPhotos(items) {
    items.forEach((item) => {
      const id = photoId(item); const card = button("", () => preview(id), "pa-photo-card");
      card.setAttribute("aria-label", "查看照片 " + id);
      card.append(picture(photoURL(id, "thumbnail"), "照片 " + id, "pa-photo"));
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
    if (!state.person || (more && state.busy.has("photos"))) return;
    const seq = ++state.photoSeq; state.busy.add("photos"); $("photos-more").disabled = true;
    const id = state.person.person_id;
    if (!more) { $("photos").replaceChildren(); state.photoCursor = null; }
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
      if (seq !== state.photoSeq || !state.person || id !== state.person.person_id) return;
      if (state.searching) state.searchSession = page.search_session_id;
      if (page.person) { state.person = page.person; albumHeading(); }
      renderPhotos(page.assets || page.hits || []); state.photoCursor = page.next_cursor;
      $("photos-more").hidden = !page.has_more && !page.next_cursor;
      $("photo-note").textContent = $("photos").children.length ? (state.searching ? "搜索结果仅包含这个人的照片。点“全部照片”返回相册。" + (page.candidates_truncated ? " 搜索结果较多，请缩小查询范围。" : "") : "点照片查看大图；认错了可选择对应的人脸更正。") : (state.searching ? "没有找到匹配的照片。" : "这个人物还没有照片。");
    } catch (error) { if (seq === state.photoSeq) { $("photo-note").textContent = errorText(error); $("photos-more").hidden = true; } }
    finally { if (seq === state.photoSeq) { state.busy.delete("photos"); $("photos-more").disabled = false; } }
  }
  async function preview(id) {
    const seq = ++state.previewSeq; $("preview-faces").replaceChildren();
    $("review").hidden = true;
    $("preview-note").textContent = "正在读取这张照片的人脸……";
    const image = $("preview-image"); image.hidden = false;
    image.onerror = () => { if (seq === state.previewSeq) { image.hidden = true; $("preview-note").textContent = "大图暂时不可用；照片来源可能已离线。"; } };
    image.src = mediaURL(photoURL(id, "preview")); $("preview").showModal();
    try {
      const page = await api("/assets/" + id + "/faces"); if (seq !== state.previewSeq || !$("preview").open) return;
      const selectedId = state.person && state.person.person_id;
      page.faces.sort((a, b) => Number(b.assigned_person_id === selectedId) - Number(a.assigned_person_id === selectedId));
      page.faces.forEach((face) => {
        const card = button("", () => { $("preview").close(); editFace(face); }, "pa-person-card");
        if (face.assigned_person_id === selectedId) {
          card.classList.add("pa-selected-person"); card.append(node("strong", "本相册的人"));
        }
        card.append(picture(face.thumbnail_url, "选择这张脸", "pa-avatar"), node("span", face.assigned_person_name ? "当前：" + face.assigned_person_name : face.assigned_person_id ? "当前：未命名的人" : "还没确定是谁"), node("span", "更正这张脸")); $("preview-faces").append(card);
      });
      $("preview-note").textContent = page.faces.length ? "选中认错的那张脸，再指定是谁或移出人物。" + (page.has_more ? "此处仅展示前 200 张脸。" : "") : "没有可整理的人脸；尚未分析、没有检测到或识别更新待确认时会出现这种情况。";
      $("review").hidden = page.faces.length !== 0;
    } catch (error) { if (seq === state.previewSeq) $("preview-note").textContent = errorText(error); }
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
    rememberOperation(result, label); message(label); $("edit").close(); state.editSeq++; state.choiceSeq++;
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
      clearTimeout(state.undoTimer); state.operation = null; $("undo-bar").hidden = true; message("已撤销刚才的整理。");
      loadPeople(false); if (state.organize) loadInbox(false);
      if (state.person) { state.person = await api("/persons/" + state.person.person_id); albumHeading(); state.searching = false; loadPhotos(false); }
    } catch (error) { message("无法撤销：" + errorText(error)); }
    finally { state.writing = false; $("undo").disabled = false; }
  }
  function boot() {
    $("people").addEventListener("click", () => setView(false)); $("organize").addEventListener("click", () => setView(true));
    $("find").addEventListener("submit", (event) => { event.preventDefault(); loadPeople(false); });
    $("query").addEventListener("search", () => loadPeople(false));
    $("refresh").addEventListener("click", () => { loadPeople(false); if (state.organize) loadInbox(false); });
    $("more").addEventListener("click", () => loadPeople(true)); $("inbox-more").addEventListener("click", () => loadInbox(true));
    $("back").addEventListener("click", () => { state.person = null; state.photoSeq++; state.busy.delete("photos"); $("library").hidden = false; $("album").hidden = true; $("people").focus(); });
    $("name").addEventListener("click", () => editBase({ mode: "name", person: { ...state.person } }, "给这个人取名字", "可用家人的称呼或你习惯的名字。"));
    $("merge").addEventListener("click", () => editMerge({ ...state.person }));
    $("photos-more").addEventListener("click", () => loadPhotos(true));
    $("search-photos").addEventListener("submit", (event) => {
      event.preventDefault(); const text = $("photo-query").value.trim(); if (!text) { state.searching = false; loadPhotos(false); return; }
      state.searching = true; state.searchBody = { contract_version: "p2-contracts.v1", request_id: requestId(), text, person_ids: [state.person.person_id], person_match: "any", page_size: 60, top_k: 5000 }; loadPhotos(false);
    });
    $("all-photos").addEventListener("click", () => { state.searching = false; $("photo-query").value = ""; loadPhotos(false); });
    $("preview-close").addEventListener("click", () => $("preview").close());
    $("review").addEventListener("click", () => { $("preview").close(); const advanced = document.getElementById("advanced-tools"); advanced.open = true; advanced.querySelector("summary").focus(); });
    $("preview").addEventListener("close", () => { state.previewSeq++; $("preview-image").removeAttribute("src"); });
    $("edit-form").addEventListener("submit", submitEdit);
    $("edit-find").addEventListener("click", () => loadChoices(false)); $("choice-more").addEventListener("click", () => loadChoices(true));
    $("edit-query").addEventListener("input", () => { if (state.edit && state.edit.mode !== "name") { state.chosen = null; state.choiceSeq++; $("edit-submit").disabled = true; } });
    $("edit-cancel").addEventListener("click", () => $("edit").close());
    $("edit").addEventListener("cancel", (event) => { if (state.writing) event.preventDefault(); });
    $("edit").addEventListener("close", () => { state.editSeq++; state.choiceSeq++; });
    $("undo").addEventListener("click", undo);
    $("undo-dismiss").addEventListener("click", () => { clearTimeout(state.undoTimer); state.undoTimer = null; state.operation = null; $("undo-bar").hidden = true; });
    loadPeople(false);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
