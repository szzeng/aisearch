/* On-demand person organization. All writes use the existing protected protocols. */
(function () {
  "use strict";
  let ui;
  const $ = (id) => document.getElementById("pt-" + id);
  const state = { installed: false, faceSeq: 0, faceBusy: false, person: null, faceCursor: null,
    selected: new Map(), faceAnchor: null, reviewSeq: 0, reviewBusy: false, assetId: null, reviewCursor: null,
    historySeq: 0, historyBusy: false, historyCursor: null, summarySeq: 0,
    retention: null, retentionAfter: null, retentionBusy: false, retentionSeq: 0 };
  const names = { RENAME: "改名字", MERGE_BY_NAME: "同名合并", MERGE: "合并人物", BATCH_MERGE: "批量合并人物",
    FACE_ASSIGN: "更正人脸归属", FACE_UNASSIGN: "移出人物", FACE_RESTORE_AUTO: "恢复自动判断", FACE_REGROUP: "移出并重新智能分组",
    BATCH_FACE_EDIT: "批量更正人脸", SPLIT: "拆出新人物", FACE_MAPPING_CONFIRM: "确认识别更新",
    FACE_MAPPING_REJECT: "拒绝识别更新", CREATE_PERSON_FROM_FACE: "从一张脸新建人物", UNDO: "撤销操作" };
  function status(id, text) { $(id).textContent = text; }
  function errorText(error) {
    const reasons = { source_would_be_empty: "原人物必须保留至少一张有效人脸",
      face_owned_by_other_person: "所选人脸已属于其他人物", assignment_revision: "人脸归属已更新",
      review_already_decided: "这条识别更新已被处理", multiple_targets_without_single_inheritor: "人工归属最多继承给一张新脸",
      human_affirm_deny_conflict: "人工归属与排除冲突", group_changed: "人物随后已被修改", provisional_hold: "请先处理这张脸的识别更新待确认", index_not_ready: "已有候选索引仍需恢复，请稍后重试" };
    const reason = error.details && error.details.reason;
    return ui.errorText(error) + (error.code ? "（" + error.code + "）" : "") +
      (reason ? " · " + (reasons[reason] || reason) : "");
  }
  function show(dialog) { if (!dialog.open) dialog.showModal(); }
  function updateSelection() {
    status("face-count", "已选 " + state.selected.size + " / 500 张脸");
    $("organize").textContent = $("faces").hidden ? "整理照片" : "完成整理";
    $("organize").disabled = ui.isWriting();
    $("organize").setAttribute("aria-expanded", String(!$("faces").hidden));
    ["face-clear", "face-clear-all", "face-retry"].forEach((id) => { $(id).disabled = ui.isWriting(); });
    ["face-assign", "face-unassign", "face-regroup", "face-split", "face-restore"].forEach((id) => {
      $(id).disabled = ui.isWriting() || !state.selected.size;
    });
    Array.from($("face-list").children).forEach((card) => {
      if (card.pick) { const selected = state.selected.has(card.face.face_id);
        card.pick.checked = selected; card.pick.disabled = ui.isWriting(); card.setAttribute("data-selected", String(selected)); }
    });
  }
  function clearSelection() { state.selected.clear(); state.faceAnchor = null; updateSelection(); }
  function selectFaceRange(card, event) {
    if (!event.shiftKey || !state.faceAnchor || ui.isWriting()) return false;
    const cards = Array.from($("face-list").children).filter((item) => item.face && item.pick);
    const from = cards.findIndex((item) => item.face.face_id === state.faceAnchor), to = cards.indexOf(card);
    if (from < 0 || to < 0) { state.faceAnchor = card.face.face_id; return false; }
    const range = cards.slice(Math.min(from, to), Math.max(from, to) + 1);
    if (state.selected.size + range.filter((item) => !state.selected.has(item.face.face_id)).length > 500) {
      status("face-status", "这次连选会超过 500 张脸，未更改已有选择。"); updateSelection(); return true;
    }
    range.forEach((item) => state.selected.set(item.face.face_id, { ...item.face }));
    state.faceAnchor = card.face.face_id; updateSelection(); return true;
  }
  function cancelSelection() { if (!ui.isWriting()) clearSelection(); }
  async function loadFaces(more) {
    if (!state.person || (more && state.faceBusy)) return;
    const seq = ++state.faceSeq, personId = state.person.person_id;
    if (!more) { state.faceCursor = null; state.faceAnchor = null; clearSelection(); $("face-list").replaceChildren(); }
    state.faceBusy = true; $("face-more").disabled = true; status("face-status", "正在读取人脸……");
    const values = { person_id: personId, limit: 40 };
    if (more && state.faceCursor) values.cursor = state.faceCursor;
    try {
      const page = await ui.api("/faces?" + new URLSearchParams(values));
      if (seq !== state.faceSeq || !state.person || personId !== state.person.person_id || $("faces").hidden) return;
      if (more && state.person.revision !== page.person_revision) {
        clearSelection(); throw { code: "PERSON_REVISION_CONFLICT", status: 409 };
      }
      state.person = { ...state.person, person_id: page.canonical_person_id, revision: page.person_revision };
      page.faces.forEach((face) => {
        if (Array.from($("face-list").children).some((card) => card.face.face_id === face.face_id)) return;
        const card = ui.node("div", undefined, "pa-person-card"); card.face = face;
        const crop = ui.node("label", undefined, "pt-avatar-select");
        crop.append(ui.picture(face.thumbnail_url, "勾选这张人脸", "pa-avatar"));
        card.pick = ui.node("input"); card.pick.type = "checkbox"; card.pick.className = "pt-avatar-checkbox";
        card.pick.setAttribute("aria-label", "选择这张人脸");
        card.pick.addEventListener("click", (event) => {
          if (event.shiftKey && selectFaceRange(card, event)) {
            card.rangeChangePending = true;
            setTimeout(() => { card.rangeChangePending = false; updateSelection(); }, 0);
          }
        });
        card.pick.addEventListener("change", () => {
          if (ui.isWriting()) { updateSelection(); return; }
          if (card.rangeChangePending) { card.rangeChangePending = false; updateSelection(); return; }
          if (card.pick.checked && !state.selected.has(face.face_id)) {
            if (state.selected.size < 500) state.selected.set(face.face_id, { ...face });
            else status("face-status", "一次最多选择 500 张脸。");
            state.faceAnchor = face.face_id;
          } else if (!card.pick.checked) { state.selected.delete(face.face_id); state.faceAnchor = face.face_id; }
          updateSelection();
        });
        crop.append(card.pick); card.append(crop, ui.node("span", face.mode === "MANUAL_ASSIGNED" ? "已人工指定" : "机器归组"));
        if (face.suggestions_available) card.append(ui.node("span", "另有机器建议，尚未确认", "hint"));
        card.append(ui.button("查看整张照片", () => ui.preview(face.asset_id))); $("face-list").append(card);
      });
      state.faceCursor = page.next_cursor; $("face-more").hidden = !page.has_more;
      status("face-status", $("face-list").children.length ? "直接勾选头像可跨照片多选；只修改选中的脸。" : "没有可整理的成员脸。"); updateSelection();
    } catch (error) { if (seq === state.faceSeq) status("face-status", errorText(error) + " 请点刷新重新选择。"); }
    finally { if (seq === state.faceSeq) { state.faceBusy = false; $("face-more").disabled = false; } }
  }
  function openFaces(person) {
    if (ui.isWriting()) return;
    if (!person) { ui.message("请先打开一个人物相册。"); return; }
    state.person = { ...person }; $("faces").hidden = false; updateSelection(); loadFaces(false);
    $("faces").scrollIntoView({ block: "start" });
  }
  function hideFaces() {
    if (!ui || ui.isWriting() || $("faces").hidden) return false;
    state.faceSeq++; state.person = null; $("faces").hidden = true; clearSelection(); return true;
  }
  async function faceAction(action) {
    if (ui.isWriting() || !state.selected.size) return;
    const faces = Array.from(state.selected.values());
    if (action === "assign") { ui.editBatchFaces(faces); return; }
    if (!window.confirm(action === "split" ? "将所选 " + faces.length + " 张脸拆成一个新的未命名人物？原人物须保留有效成员，可撤销。" :
      action === "regroup" ? "移出所选脸并用已有向量重新智能分组？保留对此人物的排除；低质量、歧义或没有可靠匹配的脸仍需整理。每次操作独立保存，可撤销。" :
      action === "unassign" ? "仅移出所选脸并留待整理？不会自动归组。" : "恢复所选脸的自动判断？人工排除保留，不会立即重新检测。")) return;
    let path, body;
    if (action === "split" || action === "regroup") {
      path = "/persons/" + state.person.person_id + (action === "regroup" ? "/regroup" : "/split");
      body = { contract_version: action === "regroup" ? "p4-face-regroup.v1" : "p3-person-split-contracts.v1", source_expected_revision: state.person.revision,
        faces: faces.map((face) => ({ face_id: face.face_id, expected_control_revision: face.control_revision,
          expected_assignment_revision: face.assignment_revision })) };
    } else {
      path = "/face-assignments/batch";
      body = { contract_version: "p3-face-correction-contracts.v1", items: faces.map((face) => ({
        face_id: face.face_id, action, target_person_id: null, expected_revision: face.control_revision })) };
    }
    try { await ui.mutate(path, body, "POST", action === "regroup" ? "已移出并重新智能分组" : action === "split" ? "已拆成新的未命名人物" : action === "unassign" ? "已移出所选人脸，仍需整理" : "已恢复自动判断，原有排除保留"); }
    catch (error) { status("face-status", errorText(error)); }
    updateSelection();
  }
  async function summary() {
    const seq = ++state.summarySeq;
    if (!ui.isOrganizing()) return;
    try {
      const body = await ui.api("/capabilities");
      if (seq !== state.summarySeq || !ui.isOrganizing()) return;
      const count = Number((body.faces || {}).open_face_mappings || 0);
      $("review-entry").hidden = count === 0; $("review-entry").textContent = "识别更新待确认 · " + count;
      $("review-summary-retry").hidden = true; status("review-summary-status", "");
    } catch (error) { if (seq === state.summarySeq && ui.isOrganizing()) {
      $("review-summary-retry").hidden = false; status("review-summary-status", "待确认数量未读取成功。" + errorText(error));
    } }
  }
  function reviewPath(limit) {
    return state.assetId ? "/assets/" + state.assetId + "/face-review?limit=" + limit : "/face-mappings?state=open&limit=" + limit;
  }
  function openReview(assetId) {
    if (ui.isWriting()) return;
    state.assetId = assetId || null; show($("review"));
    $("review-title").textContent = assetId ? "这张照片的识别更新待确认" : "识别更新待确认";
    loadReviews(false);
  }
  async function loadReviews(more) {
    if (more && state.reviewBusy) return;
    const seq = ++state.reviewSeq, assetId = state.assetId;
    if (!more) { state.reviewCursor = null; $("review-list").replaceChildren(); }
    state.reviewBusy = true; $("review-more").disabled = true; status("review-status", "正在读取待确认……");
    try {
      const page = await ui.api(reviewPath(20) + (more && state.reviewCursor ? "&cursor=" + encodeURIComponent(state.reviewCursor) : ""));
      if (seq !== state.reviewSeq || assetId !== state.assetId || !$("review").open) return;
      page.groups.forEach((group) => {
        const card = ui.node("div", undefined, "pa-person-card");
        card.append(ui.picture(group.thumbnail_url, "原来的人脸", "pa-avatar"));
        card.append(ui.node("span", group.control_mode === "MANUAL_ASSIGNED" ? "原脸已人工指定人物" : group.control_mode === "MANUAL_UNASSIGNED" ? "原脸已人工移出人物" : "原脸允许自动判断；不复制机器归属"));
        if (group.has_active_exclusions) card.append(ui.node("span", "原脸有人工排除记录，确认时按协议保留"));
        card.append(ui.button("查看整张照片", () => { $("review").close(); ui.preview(group.asset_id); }));
        const detail = ui.node("div", undefined, "pt-candidates");
        card.append(ui.button(assetId ? "审核这张旧脸" : "审核这张照片", () => assetId ? loadCandidate(group, detail, seq) : openReview(group.asset_id)), detail);
        $("review-list").append(card);
      });
      state.reviewCursor = page.next_cursor; $("review-more").hidden = !page.has_more;
      status("review-status", page.groups.length ? "按原来的人脸分别审核，不会自动选中候选。" : "当前没有待确认的识别更新。");
      // An asset opens the first review directly, while all other old faces remain individually reachable.
      if (assetId && !more && page.groups.length) {
        const card = $("review-list").children[0]; await loadCandidate(page.groups[0], card.lastElementChild, seq);
      }
    } catch (error) { if (seq === state.reviewSeq) status("review-status", errorText(error)); }
    finally { if (seq === state.reviewSeq) { state.reviewBusy = false; $("review-more").disabled = false; } }
  }
  async function loadCandidate(group, detail, seq) {
    const assetId = state.assetId;
    const detailSeq = (detail.requestSeq || 0) + 1; detail.requestSeq = detailSeq;
    detail.textContent = "正在读取候选……";
    try {
      const current = await ui.api("/face-mappings/" + group.old_face_id);
      if (seq !== state.reviewSeq || assetId !== state.assetId || !$("review").open || detailSeq !== detail.requestSeq) return;
      detail.replaceChildren();
      if (current.state !== "OPEN") { detail.textContent = "这条记录已被处理，请刷新。"; return; }
      if (current.assigned_person_id) {
        const assignedNote = ui.node("p", "原来的人工归属：未命名的人"); detail.append(assignedNote);
        ui.api("/persons/" + current.assigned_person_id).then((person) => {
          if (seq === state.reviewSeq && detailSeq === detail.requestSeq && $("review").open) assignedNote.textContent = "原来的人工归属：" + (person.name || "未命名的人");
        }).catch(() => { assignedNote.textContent = "原人物名字暂时不可用，人工归属记录仍保留"; });
      }
      const selected = new Map();
      if (!current.candidates.length) detail.append(ui.node("p", "重新检测没有找到对应人脸"));
      current.candidates.forEach((candidate) => {
        const row = ui.node("div");
        const check = ui.node("input"); check.type = "checkbox"; check.className = "pt-avatar-checkbox";
        check.setAttribute("aria-label", "选择这张新脸");
        const label = ui.node("label", undefined, "pt-avatar-select");
        label.append(ui.picture(candidate.thumbnail_url, "新检测的人脸候选", "pa-avatar"), check);
        row.append(label, ui.node("span", "选择对应的新脸"));
        let inherit;
        if (current.control_mode === "MANUAL_ASSIGNED") {
          inherit = ui.node("input"); inherit.type = "checkbox";
          const inheritLabel = ui.node("label", "把原来的人工归属继承给这张脸"); inheritLabel.prepend(inherit); row.append(inheritLabel);
        }
        const sync = () => {
          if (check.checked) selected.set(candidate.new_face_id, { new_face_id: candidate.new_face_id, inherits_person: Boolean(inherit && inherit.checked) });
          else selected.delete(candidate.new_face_id);
        };
        check.addEventListener("change", sync); if (inherit) inherit.addEventListener("change", sync);
        detail.append(row);
      });
      if (current.candidates.length) detail.append(ui.button("是同一张脸，确认所选候选", () => {
        if (!selected.size) { status("review-status", "请明确选择对应的新脸。"); return; }
        if (Array.from(selected.values()).filter((item) => item.inherits_person).length > 1) { status("review-status", "原人工归属最多继承给一张新脸。"); return; }
        decide(current, Array.from(selected.values()));
      }));
      detail.append(ui.button("不是同一张脸，拒绝这条映射", () => decide(current, [])),
        ui.button("暂不处理", () => status("review-status", "未提交更改，待确认保护继续保留。")));
      detail.append(ui.node("p", "原脸的人工排除按服务端协议转移；拒绝不指定人物，可能解除这条映射的待确认保护。", "hint"));
    } catch (error) { if (seq === state.reviewSeq && detailSeq === detail.requestSeq) detail.textContent = errorText(error) + " 请重试审核。"; }
  }
  async function decide(group, targets) {
    if (ui.isWriting()) return;
    try {
      await ui.mutate("/face-mappings/decisions", { contract_version: "p3-face-redetection-contracts.v1", asset_id: group.asset_id,
        decisions: [{ old_face_id: group.old_face_id, expected_review_revision: group.revision,
          decision: targets.length ? "confirm" : "reject", targets }] }, "POST", targets.length ? "已确认识别更新" : "已拒绝映射，未指定人物");
    } catch (error) { status("review-status", errorText(error)); }
  }
  function openHistory() { if (ui.isWriting()) return; show($("history")); loadHistory(false); }
  async function loadHistory(more) {
    if (more && state.historyBusy) return;
    const seq = ++state.historySeq;
    if (!more) { state.historyCursor = null; $("history-list").replaceChildren(); }
    state.historyBusy = true; $("history-more").disabled = true; status("history-status", "正在读取操作历史……");
    try {
      const page = await ui.api("/person-operations?limit=30" + (more && state.historyCursor ? "&cursor=" + encodeURIComponent(state.historyCursor) : ""));
      if (seq !== state.historySeq || !$("history").open) return;
      page.operations.forEach((operation) => {
        const row = ui.node("article", undefined, "pt-operation");
        const nonUndoable = operation.operation_type === "CREATE_PERSON_FROM_FACE" || operation.operation_type === "UNDO";
        const text = operation.status === "UNDONE" ? "已撤销" : operation.expired ? "已过期 / 快照已清理，不可撤销" : nonUndoable ? "已完成，不支持撤销" : "已完成，撤销时检查后续冲突";
        row.append(ui.node("strong", names[operation.operation_type] || "人物整理"), ui.node("p", new Date(operation.committed_at).toLocaleString() + " · " + text),
          ui.node("p", "影响 " + (operation.face_ids || []).length + " 张脸、" + (operation.person_ids || []).length + " 个人物 · " + operation.item_count + " 项记录"));
        if (!nonUndoable && !operation.expired && operation.status === "COMMITTED") row.append(ui.button("撤销此操作", async () => {
          if (ui.isWriting()) return;
          try { await ui.mutate("/person-operations/" + operation.operation_id + "/undo", { expected_operation_revision: operation.operation_revision }, "POST", "已撤销操作"); }
          catch (error) { const reason = "撤销未完成：" + errorText(error); row.append(ui.node("p", reason, "error")); status("history-status", reason); }
        }));
        $("history-list").append(row);
      });
      state.historyCursor = page.next_cursor; $("history-more").hidden = !page.has_more;
      status("history-status", page.operations.length ? "历史来自服务器，不能撤销的记录仍然保留。" : "还没有操作记录。");
    } catch (error) { if (seq === state.historySeq) status("history-status", errorText(error)); }
    finally { if (seq === state.historySeq) { state.historyBusy = false; $("history-more").disabled = false; } }
  }
  async function retentionPreview(preserve) {
    if (state.retentionBusy) return;
    if (!preserve) state.retentionAfter = null;
    const seq = ++state.retentionSeq;
    state.retention = null; $("retention-confirm").disabled = true;
    try {
      const page = await ui.api("/person-operations/retention-preview");
      if (seq !== state.retentionSeq || !$("history").open) return;
      state.retention = page;
      status("retention-status", "候选 " + page.candidate_operation_count + " 条，可清理 " + page.safe_operation_count + " 条，受保护跳过 " + page.skipped_operation_count + " 条；预计净节省 " + page.estimated_net_savings_bytes + " 字节。");
      $("retention-confirm").disabled = !(page.safe_operation_count > 0);
    } catch (error) { if (seq === state.retentionSeq) status("retention-status", errorText(error)); }
  }
  async function retentionCleanup() {
    if (state.retentionBusy || ui.isWriting() || !state.retention || !state.retention.safe_operation_count) return;
    if (!window.confirm("确认清理一批过期撤销快照？清理后这些操作不能再撤销。")) return;
    state.retentionBusy = true; $("retention-confirm").disabled = true;
    try {
      const result = await ui.api("/person-operations/retention-cleanup", { after_operation_revision: state.retentionAfter });
      if (!result.failed_operations) state.retentionAfter = result.next_after_operation_revision || null;
      state.retention = null;
      status("retention-status", "本批清理 " + result.cleaned_operations + " 条，跳过 " + result.skipped_operations + " 条，失败 " + result.failed_operations + " 条。" + (result.has_more ? "预览后可继续确认下一批。" : "当前批次完成。"));
      loadHistory(false);
    } catch (error) { state.retention = null; status("retention-status", "清理结果未确认，请重新预览：" + errorText(error)); }
    finally { state.retentionBusy = false; }
  }
  function refreshVisible() {
    state.retention = null; $("retention-confirm").disabled = true;
    if (state.person && !$("faces").hidden) loadFaces(false);
    if ($("review").open) loadReviews(false);
    if ($("history").open) loadHistory(false);
    if (ui.isOrganizing()) summary();
  }
  function route() {
    const hash = window.location.hash;
    if (hash === "#panel-operations") openHistory();
    else if (hash === "#panel-mappings") { const id = Number(new URLSearchParams(window.location.search).get("asset_id")); openReview(id > 0 ? id : null); }
    else if (hash === "#panel-faces") { if (ui.currentPerson()) openFaces(ui.currentPerson()); else ui.organizeView(); }
    else if (hash === "#panel-persons" || hash === "#panel-name" || hash === "#panel-merge") ui.peopleView();
  }
  function install(context) {
    if (state.installed) return; state.installed = true; ui = context;
    $("organize").addEventListener("click", () => $("faces").hidden ? openFaces(ui.currentPerson()) : hideFaces());
    $("face-more").addEventListener("click", () => loadFaces(true));
    $("face-retry").addEventListener("click", () => { if (!ui.isWriting()) loadFaces(false); }); $("face-clear").addEventListener("click", cancelSelection);
    $("face-clear-all").addEventListener("click", cancelSelection);
    ["assign", "unassign", "regroup", "split", "restore"].forEach((action) => $("face-" + action).addEventListener("click", () => faceAction(action === "restore" ? "restore_auto" : action)));
    $("review-entry").addEventListener("click", () => openReview(null)); $("review-summary-retry").addEventListener("click", summary);
    $("history-entry").addEventListener("click", openHistory);
    $("review-retry").addEventListener("click", () => loadReviews(false)); $("review-more").addEventListener("click", () => loadReviews(true));
    $("history-retry").addEventListener("click", () => loadHistory(false)); $("history-more").addEventListener("click", () => loadHistory(true));
    $("retention-preview").addEventListener("click", () => retentionPreview(true)); $("retention-confirm").addEventListener("click", retentionCleanup);
    ["review", "history"].forEach((kind) => {
      const dialog = $(kind); $(kind + "-close").addEventListener("click", () => { if (!ui.isWriting() && !state.retentionBusy) dialog.close(); });
      dialog.addEventListener("cancel", (event) => { if (ui.isWriting() || state.retentionBusy) event.preventDefault(); });
      dialog.addEventListener("close", () => { state[kind + "Seq"]++; if (kind === "history") { state.retentionSeq++; state.retention = null; } });
    });
    window.addEventListener("beforeunload", (event) => {
      if (ui.isWriting()) { event.preventDefault(); event.returnValue = ""; }
    });
    window.addEventListener("hashchange", route); updateSelection(); route();
  }
  window.AIPersonTools = { install, openFaces, hideFaces, openReview, openHistory, summary, refreshVisible, route,
    writeStateChanged: updateSelection };
})();
