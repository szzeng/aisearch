(function () {
  "use strict";
  const M = window.DemoModel;
  const main = document.getElementById("main");
  const query = document.getElementById("query");
  const scopeChip = document.getElementById("scope-chip");
  const viewer = document.getElementById("viewer");
  const editor = document.getElementById("edit-dialog");
  const pageSize = 24;
  const assets = new Map(M.records.map(a => [a.id, a]));
  let composing = false, toastTimer, observer, renameTarget, lastFocused, visibleIds = [];
  let peopleFilter = "", placesFilter = "", ignored = new Set(), confirmed = new Set(), lastFaceAction = null;
  const sourceNames = { demo: "我的演示相册", travel: "旅行备份" };
  const sourceUpdates = {};
  const selections = {};
  const pageCache = new Map();
  const blank = () => ({ page: "photos", id: "", basis: "capture", group: "day", bucket: "trusted", year: "", month: "", limit: pageSize, query: "", draft: null, scope: null, advanced: {}, excluded: [], advancedOpen: false, resultMode: "relevance", searched: false, organizing: false, scroll: 0, modal: null, mapPlace: "shenzhen", fromPhoto: false, organizeTab: "pending" });
  let state = blank();
  const esc = text => String(text == null ? "" : text).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const art = a => "data:image/svg+xml;charset=utf-8," + encodeURIComponent(M.artwork(a.scene, a.id));
  const findPerson = id => M.people.find(p => p.id === id);
  const findPlace = id => M.places.find(p => p.id === id);
  const findCollection = id => M.collections.find(p => p.id === id);
  function scopeName(scope) {
    if (!scope) return "全部照片";
    if (scope.type === "person") return `${findPerson(scope.id).name}的照片`;
    if (scope.type === "collection") return findCollection(scope.id).name;
    if (scope.type === "place") { const p = findPlace(scope.id); return `${p.name} · ${p.parent}`; }
    return sourceNames[scope.id] || "演示图库";
  }
  function albumScope() { return ["person", "collection", "place", "source"].includes(state.page) ? { type: state.page, id: state.id } : null; }
  function cacheKey(s = state) { return `${s.page}:${s.id || ""}`; }
  function clone(value) { return JSON.parse(JSON.stringify(value)); }
  function storeCurrent() {
    state.scroll = window.scrollY;
    pageCache.set(cacheKey(), clone(state));
    history.replaceState(clone(state), "", location.hash || "#photos");
  }
  function route(page, id = "", extra = {}) {
    storeCurrent();
    if (viewer.open) viewer.close();
    const cached = pageCache.get(`${page}:${id}`);
    state = Object.assign(cached ? clone(cached) : blank(), { page, id, modal: null, fromPhoto: false }, extra);
    history.pushState(clone(state), "", `#${page}${id ? "/" + encodeURIComponent(id) : ""}`);
    render(true);
  }
  function persist() { history.replaceState(clone(state), "", location.hash); }
  function resetList() { state.limit = pageSize; state.scroll = 0; persist(); render(false); }
  function notify(message) { const box = document.getElementById("toast"); box.textContent = message; box.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { box.hidden = true; }, 3800); }
  function avatar(p, small = false) { return `<span class="avatar${small ? " small" : ""}" style="background:${p.color}">${esc(p.initials)}</span>`; }
  function heading(title, subtitle, actions = "", back = "") { return `${back}<div class="page-heading"><div><p class="eyebrow">YOUR MOMENTS, COLLECTED</p><h1>${esc(title)}</h1><p class="subtitle">${esc(subtitle)}</p></div><div class="heading-actions">${actions}</div></div>`; }
  function photoCard(a) {
    const selection = state.page === "person" && state.organizing;
    const set = selections[state.id] || new Set();
    const selected = selection && set.has(`face:${a.id}:${state.id}`);
    return `<div class="photo-card${selected ? " selected" : ""}" data-card="${a.id}"><button class="photo-open" data-photo="${a.id}" aria-label="查看 ${esc(a.title)}，演示照片 ${a.id}"><img src="${art(a)}" alt="${esc(a.title)} · 合成插画" loading="lazy" decoding="async" width="900" height="600"><span class="photo-hint">${esc(a.title)}</span></button>${selection ? `<button class="face-check${selected ? " selected" : ""}" data-select-face="${a.id}" aria-pressed="${selected}" aria-label="选择演示照片 ${a.id} 中${esc(findPerson(state.id).name)}的这张脸">${selected ? "✓" : findPerson(state.id).initials}</button>` : ""}</div>`;
  }
  function options(items, value, first = "全部") { return `<option value="">${first}</option>${items.map(x => `<option value="${esc(x.id)}"${String(value || "") === String(x.id) ? " selected" : ""}>${esc(x.name)}</option>`).join("")}`; }
  function tools(scope) {
    const count = M.counts(scope);
    const added = state.basis === "added" || state.bucket !== "trusted";
    const years = [...new Set(M.timeline(scope, { basis: state.basis, bucket: state.bucket }).map(a => M.dateOf(a, state.basis, state.bucket).slice(0, 4)))].sort().reverse();
    const selected = selections[state.id] || new Set();
    return `<div class="toolbar"><label>时间依据 <select id="time-basis" aria-label="时间依据"><option value="capture"${state.basis === "capture" ? " selected" : ""}>拍摄时间</option><option value="added"${state.basis === "added" ? " selected" : ""}>加入图库时间</option></select></label><label>分组 <select id="group-by" aria-label="分组粒度"><option value="day"${state.group === "day" ? " selected" : ""}>按日</option><option value="month"${state.group === "month" ? " selected" : ""}>按月</option><option value="year"${state.group === "year" ? " selected" : ""}>按年</option></select></label><span class="separator"></span><label>筛选 <select id="year-filter" aria-label="筛选年份">${options(years.map(y => ({ id: y, name: `${y} 年` })), state.year, "全部年份")}</select></label>${state.year ? `<select id="month-filter" aria-label="筛选月份">${options(Array.from({ length: 12 }, (_, i) => ({ id: String(i + 1).padStart(2, "0"), name: `${i + 1} 月` })), state.month, "全部月份")}</select>` : ""}<button data-action="jump-dialog" title="定位到日期，不改变当前筛选">跳到年月 ↓</button>${state.year ? '<button class="text-button" data-action="clear-date">清除年月筛选</button>' : ""}${state.page === "photos" ? `<label>图库 <select id="source-filter" aria-label="选择演示图库">${options(Object.entries(sourceNames).map(([id, name]) => ({ id, name })), "", "全部图库")}</select></label>` : ""}</div><div class="date-links"><button data-bucket="trusted" class="${state.basis === "capture" && state.bucket === "trusted" ? "active" : ""}">可信拍摄日期 <span class="number">${count.trusted}</span></button><button data-bucket="unknown" class="${state.basis === "capture" && state.bucket === "unknown" ? "active" : ""}">日期未知 <span class="number">${count.unknown}</span></button><button data-bucket="pending" class="${state.basis === "capture" && state.bucket === "pending" ? "active" : ""}">日期待就绪 <span class="number">${count.pending}</span></button><button data-action="added" class="${state.basis === "added" ? "active" : ""}">按加入图库时间浏览 · ${count.total} 张</button></div>${state.organizing ? `<div class="selection-bar"><span>已选 ${selected.size} / 500 张脸 · 仅此人物的演示脸</span><button data-action="clear-selection">清空选择</button><button data-action="organize-toggle">完成整理</button></div>` : ""}${state.bucket !== "trusted" && state.basis === "capture" ? `<p class="search-note">${state.bucket === "unknown" ? "已提取，但没有可信拍摄日期。" : "时间数据尚未就绪。"}以下按<strong>加入图库日期</strong>分组；仍属于当前相册。</p>` : ""}`;
  }
  function prettyPeriod(key) { const parts = key.split("-"); return `${parts[0]} 年${parts[1] ? ` ${Number(parts[1])} 月` : ""}${parts[2] ? ` ${Number(parts[2])} 日` : ""}`; }
  function grouped(data, scope) {
    const shown = data.slice(0, state.limit);
    visibleIds = shown.map(a => a.id);
    const groups = new Map();
    shown.forEach(a => { const key = M.period(a, state.basis, state.bucket, state.group); if (!groups.has(key)) groups.set(key, []); groups.get(key).push(a); });
    const scopeYears = [...new Set(data.map(a => M.dateOf(a, state.basis, state.bucket).slice(0, 4)))];
    return `<div class="timeline-layout"><div>${[...groups].map(([key, rows]) => `<section class="group" data-period="${key}"><div class="group-heading"><h2>${prettyPeriod(key)}</h2><small>${state.basis === "added" || state.bucket !== "trusted" ? "加入图库日期" : "可信拍摄日期"} · 已载入 ${rows.length} 张</small></div><div class="photo-grid">${rows.map(photoCard).join("")}</div></section>`).join("")}${more(data.length)}</div><aside class="year-rail" aria-label="年份定位"><small>跳到年份<br>不改变筛选</small>${scopeYears.map(year => `<button data-jump-year="${year}">${year}</button>`).join("")}</aside></div>`;
  }
  function more(total) { return state.limit < total ? `<div class="more"><button data-action="more">加载更多</button><span>已载入 ${Math.min(state.limit, total)} / ${total} 张 · 演示每批 24 张</span><div id="load-sentinel" aria-hidden="true"></div></div>` : `<div class="end-line">已显示全部 ${total} 张演示记录</div>`; }
  function album() {
    const scope = albumScope();
    const count = M.counts(scope);
    let title = "全部照片", actions = "", back = "";
    if (state.page === "person") { title = findPerson(state.id).name; actions = `<button class="primary" data-action="scoped-search">搜索此相册</button><button data-action="organize-toggle">${state.organizing ? "完成整理" : "整理照片"}</button><button data-action="rename">改名</button>`; back = `<button class="text-button back" data-route="people">← 所有人物</button>`; }
    if (state.page === "collection") { title = findCollection(state.id).name; actions = '<button class="primary" data-action="scoped-search">搜索此合集</button><button data-action="collection-members">查看成员</button>'; back = '<button class="text-button back" data-route="people">← 人物与合集</button>'; }
    if (state.page === "place") { const p = findPlace(state.id); title = `${p.name} · ${p.parent}`; actions = '<button class="primary" data-action="scoped-search">搜索此地点</button><button data-action="place-map">在地图中看</button>'; back = '<button class="text-button back" data-route="places">← 所有地点</button>'; }
    if (state.page === "source") { title = sourceNames[state.id]; back = '<button class="text-button back" data-route="photos">← 全部照片</button>'; }
    if (state.fromPhoto) back = '<button class="text-button back" data-action="back">← 返回查看照片</button>';
    let html = heading(title, `${count.total} 张有效演示照片 · 可信 ${count.trusted} / 日期未知 ${count.unknown} / 待就绪 ${count.pending}`, actions, back) + tools(scope);
    const data = M.timeline(scope, state);
    if (!data.length) html += `<div class="empty"><div class="empty-icon">▧</div><h2>${state.year ? "这个年月暂无匹配照片" : "当前没有可信拍摄日期的照片"}</h2><p>相册成员仍在。可以清除筛选，或切换日期分区继续浏览。</p><div class="heading-actions"><button data-action="clear-date">清除年月筛选</button><button data-bucket="unknown">日期未知</button><button data-action="added">按加入图库时间浏览</button></div></div>`;
    else html += grouped(data, scope);
    return html;
  }
  function peoplePage() {
    const sorted = M.people.slice().sort((a, b) => M.counts({ type: "person", id: b.id }).total - M.counts({ type: "person", id: a.id }).total);
    const filtered = sorted.filter(p => p.name.includes(peopleFilter));
    const cards = filtered.map(p => `<button class="person-card" data-route="person" data-id="${p.id}">${avatar(p)}<strong>${esc(p.name)}</strong><small>${M.counts({ type: "person", id: p.id }).total} 张照片</small></button>`).join("");
    return heading("人物", "那些出现在生活里的人。按有效照片数排序，缺少拍摄日期也不会漏掉。", '<button data-route="organize">整理一下 <span class="number">12</span></button>') + `<label class="local-filter">筛选人物 <input id="people-filter" type="search" value="${esc(peopleFilter)}" placeholder="输入人物名字" aria-label="筛选人物目录"></label><div class="person-cards" id="people-cards">${cards || '<p class="subtitle">没有这个名字的人物。</p>'}</div><div class="section-title"><h2>人物合集</h2><span class="subtitle">按唯一照片计算，合照只出现一次</span></div><div class="folder-card-grid">${M.collections.map(c => { const cover = M.members({ type: "collection", id: c.id })[4]; return `<button class="album-card" data-route="collection" data-id="${c.id}"><div class="album-cover"><img src="${art(cover)}" alt="${esc(c.name)}演示封面"></div><div class="album-card-body"><strong>${esc(c.name)}</strong><small>${c.people.length} 个人物 · ${M.counts({ type: "collection", id: c.id }).total} 张唯一照片</small></div></button>`; }).join("")}</div>`;
  }
  function placesPage() {
    return heading("地点", "走过的地方，都留在照片里。", '<button data-route="map">⌖ 地图</button>') + `<label class="local-filter">筛选地名 <input id="places-filter" type="search" value="${esc(placesFilter)}" placeholder="输入城市或省份" aria-label="筛选地点目录"></label><div class="folder-card-grid" id="place-cards">${M.places.filter(p => (p.name + p.parent).includes(placesFilter)).map(p => `<button class="album-card" data-route="place" data-id="${p.id}"><div class="album-cover"><img src="${art({ scene: p.scene, id: M.places.indexOf(p) + 2 })}" alt="${esc(p.name)}演示封面"></div><div class="album-card-body"><strong>${esc(p.name)}</strong><small>${esc(p.parent)} · ${M.counts({ type: "place", id: p.id }).total} 张照片</small></div></button>`).join("")}</div><p class="inline-note">地点卡使用演示成员关联，不从照片图案推断真实地理位置。</p>`;
  }
  function advanced() {
    const adv = state.advanced;
    return `<section class="advanced"><div class="advanced-heading"><button data-action="advanced">高级筛选 ${state.advancedOpen ? "⌃" : "⌄"}</button><button data-action="image-mode"${state.scope ? " disabled" : ""}>以图搜图</button></div>${state.scope ? '<p class="inline-note">以图搜图暂不支持当前相册范围，请先取消范围。</p>' : ""}${state.advancedOpen ? `<form id="advanced-form"><div class="advanced-grid"><label>来源图库<select name="source">${options(Object.entries(sourceNames).map(([id, name]) => ({ id, name })), adv.source)}</select></label><label>人物条件<select name="person">${options(M.people, adv.person)}</select></label><label>地点条件<select name="place">${options(M.places, adv.place)}</select></label><label>拍摄年份<select name="year">${options([2026, 2025, 2024, 2023, 2022, 2020].map(y => ({ id: String(y), name: `${y} 年` })), adv.year)}</select></label></div><div class="advanced-actions"><button class="primary" type="submit">应用高级筛选</button><button type="button" data-action="clear-advanced">清除高级筛选</button></div><p class="inline-note">只清除这里的手动条件，保留上方固定相册范围与输入的查询条件。</p></form>` : ""}</section>`;
  }
  function searchPage() {
    let html = heading(state.scope ? `搜索${scopeName(state.scope)}` : "搜索照片", state.scope ? "已固定相册范围，输入内容后只在这些演示成员中查找。" : "用一句话，找回某个瞬间。") + advanced();
    if (!state.searched) return html + `<div class="empty"><div class="empty-icon">⌕</div><h2>${state.scope ? "范围已选好，等待输入" : "从人物、地点或一个瞬间开始"}</h2><p>${state.scope ? `当前范围：${esc(scopeName(state.scope))}。照片日期未知或待就绪，也在此范围内。` : "在页头输入并按 Enter。仅聚焦不会触发搜索。"}</p><div class="examples"><button data-example="海边">海边</button><button data-example="2022年张三的照片">2022年张三的照片</button><button data-example="没有拍摄日期的照片">没有拍摄日期的照片</button></div></div>`;
    const result = M.search(state.query, state.scope, state.advanced, state.excluded);
    if (result.error) return html + `<div class="error" role="alert">${esc(result.error)} 范围和输入已保留。</div>`;
    const chips = result.terms.filter(t => !(state.scope && t.kind === state.scope.type && t.value === state.scope.id));
    html += `<div class="chips">${chips.map(t => `<button class="chip" data-remove-term="${t.key}" aria-label="取消${esc(t.label)}">${esc(t.label)}<span class="cross">×</span></button>`).join("")}${result.keywords.length ? `<span class="chip">内容：${esc(result.keywords.join("、"))}</span>` : ""}${Object.entries(state.advanced).filter(([, value]) => value).map(([key, value]) => `<button class="chip" data-remove-advanced="${key}">手动条件：${esc(key === "year" ? value + "年" : key === "source" ? sourceNames[value] : key === "person" ? findPerson(value).name : findPlace(value).name)} ×</button>`).join("")}</div><div class="search-note"><strong>${result.data.length} 张演示结果</strong><span>${result.kind === "filter" ? "纯条件 · 完整演示成员" : "内容词 · 标签匹配模拟，非真实 AI 相关度"}</span><label>显示 <select id="result-mode"><option value="relevance"${state.resultMode === "relevance" ? " selected" : ""}>${result.kind === "filter" ? "默认顺序" : "演示相关度"}</option><option value="time"${state.resultMode === "time" ? " selected" : ""}>按时间查看${result.kind === "content" ? "当前演示候选" : "完整演示结果"}</option></select></label></div>`;
    if (!result.data.length) return html + '<div class="empty"><div class="empty-icon">⌕</div><h2>没有匹配的演示照片</h2><p>试试“海边”“旅行”“2022年照片”，或者取消一项条件。固定范围不会自动扩大。</p></div>';
    let data = result.data;
    if (state.resultMode === "time") {
      const timePlan = result.terms.find(t => t.key === "date");
      const basis = timePlan ? timePlan.basis : "added";
      data = data.slice().sort((a, b) => (basis === "capture" ? b.capture.localeCompare(a.capture) : b.added.localeCompare(a.added)) || b.id - a.id);
      const oldBasis = state.basis, oldBucket = state.bucket;
      state.basis = basis; state.bucket = basis === "capture" ? "trusted" : "all";
      html += `<p class="search-note">${result.kind === "content" ? "仅当前演示候选；不代表真实全图库匹配。" : "完整纯条件演示结果。"}按${basis === "added" ? "加入图库日期" : "可信拍摄日期"}浏览。</p>` + grouped(data, state.scope);
      state.basis = oldBasis; state.bucket = oldBucket;
    } else { visibleIds = data.slice(0, state.limit).map(a => a.id); html += `<div class="photo-grid">${data.slice(0, state.limit).map(photoCard).join("")}</div>` + more(data.length); }
    return html;
  }
  function mapPage() {
    const place = findPlace(state.mapPlace);
    const data = M.members({ type: "place", id: place.id }).slice(0, 8);
    visibleIds = data.map(a => a.id);
    return heading("地图相册", "选择一个地点，查看它的演示照片。地图是离线示意图。", '<button data-route="places">地点相册</button>') + `<div class="map-layout"><div class="map-surface"><svg viewBox="0 0 900 600" preserveAspectRatio="none" aria-hidden="true"><path d="M0 390Q260 280 510 380T900 285" stroke="#b8d1c1" stroke-width="84" fill="none"/><path d="M120 0L280 250 600 365 700 600M0 155L360 250 900 155M580 0L435 260 80 600" stroke="#f5f3e5" stroke-width="14" fill="none"/><path d="M0 430Q300 140 900 410" stroke="#d8c8a6" stroke-width="4" fill="none"/></svg><span class="map-label" style="top:24%;left:19%">山地公园</span><span class="map-label" style="top:61%;left:62%">城市绿地</span><span class="map-water">海湾</span>${M.places.map((p, i) => `<button class="map-pin${p.id === place.id ? " active" : ""}" data-map-place="${p.id}" style="left:${[58, 31, 65, 25][i]}%;top:${[65, 49, 28, 18][i]}%">${esc(p.name)} · ${M.counts({ type: "place", id: p.id }).total}</button>`).join("")}<div class="map-controls"><button data-action="map-next" aria-label="切换示意地图范围">切换范围</button><button data-action="map-reset">重置</button></div><div class="map-caption">示意地图 · 不代表真实坐标、行政区或地图视口检索</div></div><aside class="map-sidebar"><h2>${esc(place.name)}</h2><p class="subtitle">${esc(place.parent)} · 8 张演示预览</p><div class="photo-grid">${data.map(photoCard).join("")}</div><button class="primary" data-route="place" data-id="${place.id}">进入地点相册</button><p class="inline-note">切换地点后照片同步改变。真实地图范围成员与时间分页仍待产品实现。</p></aside></div>`;
  }
  function managementPage() {
    return heading("图库管理", "日常看照片在前面，来源和维护留在这里。", '<button class="primary" data-action="add-source">＋ 添加演示来源</button>') + `<div class="source-list">${Object.entries(sourceNames).map(([id, name]) => `<section class="source-card"><span class="source-icon">▤</span><div class="source-body"><h2>${esc(name)}</h2><p class="subtitle">${M.counts({ type: "source", id }).total} 张唯一演示照片 · ${esc(sourceUpdates[id] || "示意数据已就绪")}</p><p class="inline-note">仅当前页面的合成来源，不读取任何真实文件夹</p></div><div class="source-actions"><button class="primary" data-route="source" data-id="${id}">看照片</button><button data-refresh-source="${id}">模拟刷新</button></div></section>`).join("")}</div><section class="advanced" style="margin-top:26px"><button class="text-button" data-action="maintenance">存储与维护说明 ⌄</button><div id="maintenance-info" hidden><p class="inline-note">演示不执行备份、扫描、建库、移动、删除或存储维护。真实产品仍使用已有独立授权与预览流程。</p></div></section>`;
  }
  function organizePage() {
    const hidden = state.organizeTab === "ignored";
    const faces = Array.from({ length: 12 }, (_, i) => i).filter(i => hidden ? ignored.has(i) : !ignored.has(i) && !confirmed.has(i));
    return heading("整理一下", "演示逐脸建议与人工选择。这里的操作只改变页面内的合成状态。", '<button data-route="people">返回人物</button>') + `<div class="toolbar"><button data-organize-tab="pending" class="pill ${!hidden ? "active" : ""}">待整理 · ${12 - ignored.size - confirmed.size}</button><button data-organize-tab="ignored" class="pill ${hidden ? "active" : ""}">已忽略 · ${ignored.size}</button>${lastFaceAction ? '<button data-action="undo-face">撤销上一步演示操作</button>' : ""}</div>${faces.length ? `<div class="organize-grid">${faces.map(i => { const p = M.people[i % 4]; return `<article class="face-card">${avatar(p)}<h2>演示脸 ${i + 1}</h2><p>${hidden ? "已忽略，仍可恢复到待整理" : `建议归入 ${esc(p.name)}，等待你确认`}</p><div class="face-actions">${hidden ? `<button data-restore-face="${i}">恢复</button>` : `<button class="primary" data-confirm-face="${i}">归入${esc(p.name)}</button><button data-ignore-face="${i}">忽略</button>`}</div></article>`; }).join("")}</div>` : '<div class="empty"><h2>这一组已经整理完了</h2><p>可以切换到另一组，或撤销最近一次演示操作。</p></div>'}`;
  }
  function render(restore = false) {
    if (observer) observer.disconnect();
    visibleIds = [];
    const isSearch = state.page === "search";
    scopeChip.hidden = !isSearch || !state.scope;
    scopeChip.textContent = state.scope ? `${scopeName(state.scope)} ×` : "";
    query.value = isSearch ? (state.draft ?? state.query) : "";
    query.placeholder = isSearch && state.scope ? `在${scopeName(state.scope)}中查找，例如：海边` : "搜索照片、人物、地点、时间……";
    document.querySelectorAll("[data-nav]").forEach(b => { const section = ["person", "collection", "organize"].includes(state.page) ? "people" : ["place", "map"].includes(state.page) ? "places" : state.page === "source" ? "photos" : state.page; b.classList.toggle("active", b.dataset.nav === section); if (b.dataset.nav === section) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current"); });
    if (["photos", "person", "collection", "place", "source"].includes(state.page)) main.innerHTML = album();
    else if (state.page === "people") main.innerHTML = peoplePage();
    else if (state.page === "places") main.innerHTML = placesPage();
    else if (state.page === "search") main.innerHTML = searchPage();
    else if (state.page === "map") main.innerHTML = mapPage();
    else if (state.page === "management") main.innerHTML = managementPage();
    else if (state.page === "organize") main.innerHTML = organizePage();
    if (restore) requestAnimationFrame(() => window.scrollTo(0, state.scroll || 0));
    const sentinel = document.getElementById("load-sentinel");
    if (sentinel && "IntersectionObserver" in window && state.page !== "search") {
      observer = new IntersectionObserver(entries => { if (entries.some(e => e.isIntersecting)) { observer.disconnect(); loadMore(); } }, { rootMargin: "30px" });
      observer.observe(sentinel);
    }
    if (state.modal) showViewer(state.modal);
    else if (viewer.open) viewer.close();
  }
  function loadMore() { const scroll = window.scrollY; state.limit += pageSize; state.scroll = scroll; persist(); render(false); window.scrollTo(0, scroll); }
  function executeSearch(text, { resetExcluded = true } = {}) {
    if (composing) return;
    const q = String(text).trim();
    if (!q) { notify("输入关键词或日期条件后再搜索；空输入只保留当前范围。"); query.focus(); return; }
    if (state.page !== "search") route("search", "", { query: q, draft: q, scope: null, searched: true, scroll: 0 });
    else { storeCurrent(); state.query = q; state.draft = q; state.searched = true; state.limit = pageSize; if (resetExcluded) state.excluded = []; state.scroll = 0; history.pushState(clone(state), "", "#search"); render(true); }
  }
  function openPhoto(id) { lastFocused = document.activeElement; storeCurrent(); state.modal = { id, ids: visibleIds.slice(), origin: cacheKey(), scope: albumScope() || state.scope }; history.pushState(clone(state), "", `${location.hash.split("?")[0]}?photo=${id}`); showViewer(state.modal); }
  function showViewer(modal) {
    const a = assets.get(modal.id); if (!a) return;
    const index = modal.ids.indexOf(a.id), place = findPlace(a.place);
    viewer.innerHTML = `<button class="viewer-close" data-action="close-viewer" aria-label="关闭照片">×</button><div class="viewer-layout"><div class="viewer-stage"><img src="${art(a)}" alt="${esc(a.title)}，合成演示插画"><button class="viewer-arrow prev" data-viewer-step="-1" aria-label="上一张"${index <= 0 ? " disabled" : ""}>‹</button><button class="viewer-arrow next" data-viewer-step="1" aria-label="下一张"${index >= modal.ids.length - 1 ? " disabled" : ""}>›</button><div class="viewer-caption"><span>${index + 1} / ${modal.ids.length} 张已载入演示照片</span><span>Esc 关闭</span></div></div><aside class="viewer-side"><h2>${esc(a.title)}</h2><p class="subtitle">演示照片 ${a.id} · 合成插画</p><p class="info-label">拍摄日期</p><p class="info-value">${a.status === "trusted" ? prettyPeriod(a.capture) + ` ${a.hour}:00<br>来源：模拟 EXIF Original` : a.status === "unknown" ? "未知 · 已提取但无可信日期" : "待就绪 · 时间尚未提取"}</p><p class="info-label">加入图库日期</p><p class="info-value">${prettyPeriod(a.added)}</p>${a.status === "trusted" && !/^(search|map):/.test(modal.origin) ? '<button data-action="same-day">查看拍摄同一天</button>' : ""}<p class="info-label">照片中的人物</p><div class="viewer-people">${a.people.map(id => { const p = findPerson(id); return `<button data-route="person" data-id="${id}" data-from-photo="true">${avatar(p, true)}${esc(p.name)}</button>`; }).join("")}</div><p class="info-label">地点</p><button data-route="place" data-id="${place.id}" data-from-photo="true">⌖ ${esc(place.name)} · ${esc(place.parent)}</button><p class="info-label">文件与来源</p><p class="info-value">合成插画 · 900 × 600<br>${a.sources.map(id => esc(sourceNames[id])).join(" / ")}</p><p class="info-message">没有读取真实原图或EXIF。关闭后恢复原筛选与位置；左右切换仅限当前已载入集合。</p></aside></div>`;
    if (!viewer.open) viewer.showModal();
  }
  function closeViewer() { if (!viewer.open) return; history.back(); }
  function stepViewer(direction) { const modal = state.modal; if (!modal) return; const index = modal.ids.indexOf(modal.id) + direction; if (index < 0 || index >= modal.ids.length) return; modal.id = modal.ids[index]; persist(); showViewer(modal); }
  function openEditor(title, content, action = "") { editor.innerHTML = `<h2>${esc(title)}</h2>${content}${action || '<div class="edit-actions"><button data-action="close-editor">关闭</button></div>'}`; editor.showModal(); }
  function jumpTo(year, month = "") {
    const scope = state.page === "search" ? state.scope : albumScope();
    const data = state.page === "search" ? M.search(state.query, scope, state.advanced, state.excluded).data : M.timeline(scope, state);
    const index = data.findIndex(a => M.dateOf(a, state.basis, state.bucket).startsWith(year + (month ? "-" + month : "")));
    if (index < 0) { notify("当前筛选范围没有这个年月，跳转不会自动清除筛选。"); return; }
    state.limit = Math.max(state.limit, Math.ceil((index + 1) / pageSize) * pageSize); persist(); render(false);
    requestAnimationFrame(() => { const id = data[index].id; const card = main.querySelector(`[data-card="${id}"]`); if (card) card.closest(".group")?.scrollIntoView({ behavior: "smooth", block: "start" }); });
    notify(`已定位 ${year} 年${month ? " " + Number(month) + " 月" : ""}，当前筛选范围未改变。`);
  }
  function selectFace(id, force) { const set = selections[state.id] || (selections[state.id] = new Set()); const key = `face:${id}:${state.id}`; const wanted = force === undefined ? !set.has(key) : force; if (wanted && set.size >= 500) { notify("超过500张脸，选择没有改变。"); return; } if (wanted) set.add(key); else set.delete(key); const scroll = window.scrollY; render(false); window.scrollTo(0, scroll); }
  document.getElementById("search-form").addEventListener("submit", e => { e.preventDefault(); executeSearch(query.value); });
  query.addEventListener("compositionstart", () => { composing = true; });
  query.addEventListener("compositionend", () => { composing = false; });
  query.addEventListener("keydown", e => { if (e.key === "Enter" && (composing || e.isComposing || e.keyCode === 229)) e.preventDefault(); });
  scopeChip.addEventListener("click", () => { const draft = query.value; state.scope = null; state.query = draft; state.draft = draft; state.limit = pageSize; state.searched = Boolean(draft.trim()); persist(); render(false); notify("已解除固定相册范围，其它明确条件仍保留。"); query.focus(); });
  document.addEventListener("click", e => {
    const button = e.target.closest("button"); if (!button || button.disabled) return;
    if (button.dataset.nav) { route(button.dataset.nav); return; }
    if (button.dataset.route) { route(button.dataset.route, button.dataset.id || "", { fromPhoto: button.dataset.fromPhoto === "true" }); return; }
    if (button.dataset.photo) { openPhoto(Number(button.dataset.photo)); return; }
    if (button.dataset.example) { executeSearch(button.dataset.example); return; }
    if (button.dataset.bucket) { state.basis = "capture"; state.bucket = button.dataset.bucket; state.year = ""; state.month = ""; resetList(); return; }
    if (button.dataset.jumpYear) { jumpTo(button.dataset.jumpYear); return; }
    if (button.dataset.removeTerm) { state.excluded.push(button.dataset.removeTerm); state.limit = pageSize; persist(); render(false); return; }
    if (button.dataset.removeAdvanced) { delete state.advanced[button.dataset.removeAdvanced]; resetList(); return; }
    if (button.dataset.mapPlace) { state.mapPlace = button.dataset.mapPlace; persist(); render(false); return; }
    if (button.dataset.viewerStep) { stepViewer(Number(button.dataset.viewerStep)); return; }
    if (button.dataset.selectFace) { selectFace(Number(button.dataset.selectFace)); return; }
    if (button.dataset.refreshSource) { sourceUpdates[button.dataset.refreshSource] = "刚刚模拟刷新完成 · 未读取文件"; render(false); notify("演示刷新完成，没有扫描真实文件夹。"); return; }
    const faceOperation = ["confirmFace", "ignoreFace", "restoreFace"].find(key => button.dataset[key] !== undefined);
    if (faceOperation) { const id = Number(button.dataset[faceOperation]); lastFaceAction = { id, ignored: ignored.has(id), confirmed: confirmed.has(id) }; if (faceOperation === "confirmFace") confirmed.add(id); if (faceOperation === "ignoreFace") ignored.add(id); if (faceOperation === "restoreFace") ignored.delete(id); render(false); notify("演示状态已更新，可撤销；未修改真实人物归属。"); return; }
    if (button.dataset.organizeTab) { state.organizeTab = button.dataset.organizeTab; persist(); render(false); return; }
    switch (button.dataset.action) {
      case "more": loadMore(); break;
      case "back": history.back(); break;
      case "scoped-search": { const scope = albumScope(); route("search", "", { scope, query: "", draft: null, searched: false, advanced: {}, excluded: [], limit: pageSize, scroll: 0 }); query.focus(); break; }
      case "advanced": state.advancedOpen = !state.advancedOpen; persist(); render(false); break;
      case "clear-advanced": state.advanced = {}; resetList(); notify("高级手动条件已清除，固定范围和文字条件保留。"); break;
      case "added": state.basis = "added"; state.bucket = "trusted"; state.year = ""; state.month = ""; resetList(); break;
      case "clear-date": state.year = ""; state.month = ""; resetList(); break;
      case "place-map": route("map", "", { mapPlace: state.id }); break;
      case "map-next": state.mapPlace = M.places[(M.places.findIndex(p => p.id === state.mapPlace) + 1) % 4].id; persist(); render(false); break;
      case "map-reset": state.mapPlace = "shenzhen"; persist(); render(false); break;
      case "close-viewer": closeViewer(); break;
      case "close-editor": editor.close(); break;
      case "rename": renameTarget = state.id; openEditor("修改人物名称", `<p>只改变当前演示中的人物名称，不写入真实图库。</p><form id="rename-form"><label>人物名称<input name="name" maxlength="20" required value="${esc(findPerson(state.id).name)}"></label><div class="edit-actions"><button type="button" data-action="close-editor">取消</button><button class="primary" type="submit">保存演示名称</button></div></form>`); break;
      case "collection-members": openEditor("合集成员", `<p>有效唯一照片来自以下演示人物，重叠照片按资产去重。</p><div class="viewer-people">${findCollection(state.id).people.map(id => { const p = findPerson(id); return `<span>${avatar(p, true)} ${esc(p.name)}</span>`; }).join("")}</div>`); break;
      case "jump-dialog": { const years = [2026, 2025, 2024, 2023, 2022, 2020]; openEditor("跳到年月", `<p>只定位当前范围，不改变年份筛选或相册成员。</p><form id="jump-form"><label>年份<select name="year">${years.map(y => `<option>${y}</option>`).join("")}</select></label><label>月份<select name="month">${options(Array.from({ length: 12 }, (_, i) => ({ id: String(i + 1).padStart(2, "0"), name: `${i + 1} 月` })), "", "该年第一张照片")}</select></label><div class="edit-actions"><button type="button" data-action="close-editor">取消</button><button class="primary">跳转</button></div></form>`); break; }
      case "image-mode": if (state.scope) return; openEditor("以图搜图 · 演示入口", '<p>这个原型不读取上传图片、不调用模型。真实产品将保留已有本地上传流程与限制说明。</p><p>本页面可以直接体验文字示例与范围过滤。</p>'); break;
      case "add-source": openEditor("添加演示来源", '<p>这里仅添加一个空的合成来源，不打开或扫描真实文件夹。</p><form id="source-form"><label>演示来源名称<input name="name" maxlength="30" required placeholder="例如：新的旅行相册"></label><div class="edit-actions"><button type="button" data-action="close-editor">取消</button><button class="primary">添加</button></div></form>'); break;
      case "maintenance": { const info = document.getElementById("maintenance-info"); info.hidden = !info.hidden; break; }
      case "organize-toggle": state.organizing = !state.organizing; persist(); render(false); break;
      case "clear-selection": selections[state.id] = new Set(); render(false); break;


      case "undo-face": if (lastFaceAction) { const a = lastFaceAction; a.ignored ? ignored.add(a.id) : ignored.delete(a.id); a.confirmed ? confirmed.add(a.id) : confirmed.delete(a.id); lastFaceAction = null; render(false); notify("已撤销上一步演示操作。"); } break;
      case "same-day": { const a = assets.get(state.modal.id); const scope = state.modal.scope; route(scope ? scope.type : "photos", scope ? scope.id : "", { basis: "capture", bucket: "trusted", year: a.capture.slice(0, 4), month: a.capture.slice(5, 7), scroll: 0, limit: 180, organizing: false }); requestAnimationFrame(() => main.querySelector(`[data-period="${a.capture}"]`)?.scrollIntoView({ block: "start" })); break; }
    }
  });
  document.addEventListener("change", e => {
    switch (e.target.id) {
      case "time-basis": state.basis = e.target.value; state.bucket = "trusted"; state.year = ""; state.month = ""; resetList(); break;
      case "group-by": state.group = e.target.value; persist(); render(false); break;
      case "year-filter": state.year = e.target.value; state.month = ""; resetList(); break;
      case "month-filter": state.month = e.target.value; resetList(); break;
      case "source-filter": if (e.target.value) route("source", e.target.value); break;
      case "result-mode": state.resultMode = e.target.value; resetList(); break;
    }
  });
  document.addEventListener("input", e => {
    if (e.target.id === "people-filter") { const pos = e.target.selectionStart; peopleFilter = e.target.value; main.innerHTML = peoplePage(); const input = document.getElementById("people-filter"); input.focus(); input.setSelectionRange(pos, pos); }
    if (e.target.id === "places-filter") { const pos = e.target.selectionStart; placesFilter = e.target.value; main.innerHTML = placesPage(); const input = document.getElementById("places-filter"); input.focus(); input.setSelectionRange(pos, pos); }
    if (e.target.id === "query" && state.page === "search") { state.draft = e.target.value; if (!e.target.value) { state.query = ""; state.searched = false; state.excluded = []; persist(); render(false); query.focus(); } }
  });
  document.addEventListener("submit", e => {
    if (e.target.id === "advanced-form") { e.preventDefault(); state.advanced = Object.fromEntries(new FormData(e.target)); state.query = query.value.trim(); state.draft = state.query; state.searched = Boolean(state.query || Object.values(state.advanced).some(Boolean)); resetList(); }
    if (e.target.id === "rename-form") { e.preventDefault(); const name = String(new FormData(e.target).get("name")).trim(); if (!name) return; findPerson(renameTarget).name = name; findPerson(renameTarget).initials = name.slice(0, 1); editor.close(); render(false); notify("演示人物名称已保存。"); }
    if (e.target.id === "jump-form") { e.preventDefault(); const values = new FormData(e.target); editor.close(); jumpTo(values.get("year"), values.get("month")); }
    if (e.target.id === "source-form") { e.preventDefault(); const name = String(new FormData(e.target).get("name")).trim(); if (!name) return; sourceNames[`added-${Object.keys(sourceNames).length}`] = name; editor.close(); render(false); notify("已添加空的演示来源，没有读取文件夹。"); }
  });
  viewer.addEventListener("cancel", e => { e.preventDefault(); closeViewer(); });
  viewer.addEventListener("click", e => { if (e.target === viewer) { const r = viewer.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) closeViewer(); } });
  editor.addEventListener("click", e => { if (e.target === editor) { const r = editor.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) editor.close(); } });
  document.addEventListener("keydown", e => { if (viewer.open && e.key === "ArrowRight") { e.preventDefault(); stepViewer(1); } if (viewer.open && e.key === "ArrowLeft") { e.preventDefault(); stepViewer(-1); } if (e.key === "Escape" && !viewer.open && !editor.open && state.organizing) { state.organizing = false; render(false); notify("已退出演示整理模式，选择仍保留。"); } });
  window.addEventListener("popstate", e => { const closingId = state.modal?.id; if (editor.open) editor.close(); state = Object.assign(blank(), e.state || {}); render(true); if (!state.modal) requestAnimationFrame(() => { const button = closingId && main.querySelector(`[data-photo="${closingId}"]`); if (button) button.focus({ preventScroll: true }); else if (lastFocused?.isConnected) lastFocused.focus({ preventScroll: true }); }); });
  const initial = location.hash.slice(1).split("?")[0].split("/");
  if (["photos", "people", "places", "search", "map", "management", "organize"].includes(initial[0])) state.page = initial[0];
  else if (["person", "collection", "place", "source"].includes(initial[0]) && initial[1] && M.members({ type: initial[0], id: initial[1] }).length) { state.page = initial[0]; state.id = initial[1]; }
  history.replaceState(clone(state), "", `#${state.page}${state.id ? "/" + state.id : ""}`);
  render(true);
})();
