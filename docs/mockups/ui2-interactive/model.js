/* Deterministic synthetic UI data; no filesystem, application API, model or network. */
(function (root) {
  "use strict";
  const people = [{ id: "zhang", name: "张三", color: "#b9d4c6", initials: "张" }, { id: "li", name: "李四", color: "#edcfad", initials: "李" }, { id: "lin", name: "小林", color: "#b6c9da", initials: "林" }, { id: "an", name: "小安", color: "#dfbed0", initials: "安" }];
  const places = [{ id: "shenzhen", name: "深圳市", parent: "广东省", scene: "city" }, { id: "guangzhou", name: "广州市", parent: "广东省", scene: "street" }, { id: "hangzhou", name: "杭州市", parent: "浙江省", scene: "lake" }, { id: "beijing", name: "北京市", parent: "中国", scene: "mountain" }];
  const collections = [{ id: "family", name: "家庭合集", people: ["zhang", "li", "an"] }, { id: "friends", name: "和朋友的日子", people: ["lin", "an"] }];
  const scenes = ["beach", "mountain", "city", "lake", "garden", "street"];
  const tags = { beach: ["海边", "旅行", "日落", "蓝色"], mountain: ["山", "旅行", "徒步", "绿色"], city: ["城市", "夜景", "旅行", "建筑"], lake: ["湖", "旅行", "水", "春天"], garden: ["花", "公园", "春天", "绿色"], street: ["街道", "旅行", "建筑", "散步"] };
  const records = Array.from({ length: 180 }, (_, i) => {
    const scene = scenes[i % scenes.length];
    const members = [people[i % 4].id];
    if (i % 3 === 0) members.push(people[(i + 1) % 4].id);
    const year = [2026, 2025, 2024, 2023, 2022, 2020][Math.floor(i / 30)];
    const month = [10, 9][Math.floor((i % 30) / 15)];
    const day = 12 - Math.floor((i % 30) / 15);
    const captured = `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    const added = `2026-${String(10 - Math.floor(i / 36)).padStart(2, "0")}-${String(26 - Math.floor((i % 36) / 6)).padStart(2, "0")}`;
    return { id: i + 1, scene, tags: tags[scene], people: members, place: places[i % 4].id, capture: captured, added, status: i % 10 === 7 || i % 10 === 8 ? "unknown" : i % 10 === 9 ? "pending" : "trusted", sources: i % 7 === 0 ? ["demo", "travel"] : [i % 2 === 0 ? "demo" : "travel"], hour: 9 + i % 12, title: ["海风里的午后", "山间的慢时光", "城市微光", "湖畔散步", "春日花园", "街角的光"][i % 6] };
  });
  function members(scope, data = records) {
    if (!scope || scope.type === "all") return data.slice();
    if (scope.type === "person") return data.filter(a => a.people.includes(scope.id));
    if (scope.type === "collection") { const c = collections.find(x => x.id === scope.id); return c ? data.filter(a => a.people.some(p => c.people.includes(p))) : []; }
    if (scope.type === "place") return data.filter(a => a.place === scope.id);
    if (scope.type === "source") return data.filter(a => a.sources.includes(scope.id));
    return [];
  }
  function counts(scope) { const data = members(scope); return { total: data.length, trusted: data.filter(a => a.status === "trusted").length, unknown: data.filter(a => a.status === "unknown").length, pending: data.filter(a => a.status === "pending").length }; }
  function dateOf(a, basis, bucket) { return basis === "added" || bucket !== "trusted" ? a.added : a.capture; }
  function timeline(scope, settings) {
    const bucket = settings.bucket || "trusted";
    let data = members(scope).filter(a => settings.basis === "added" || a.status === bucket);
    if (settings.year) data = data.filter(a => dateOf(a, settings.basis, bucket).startsWith(settings.year));
    if (settings.month) data = data.filter(a => dateOf(a, settings.basis, bucket).slice(5, 7) === settings.month);
    return data.sort((a, b) => dateOf(b, settings.basis, bucket).localeCompare(dateOf(a, settings.basis, bucket)) || (settings.basis === "capture" && bucket === "trusted" ? b.hour - a.hour : 0) || b.id - a.id);
  }
  function period(a, basis, bucket, group) { const date = dateOf(a, basis, bucket); return date.slice(0, group === "year" ? 4 : group === "month" ? 7 : 10); }
  function parse(query, excluded = []) {
    const text = String(query || "").trim();
    if (/拍摄.*(?:入库|导入)|(?:入库|导入).*拍摄.*年/.test(text) && /年/.test(text)) return { error: "这个演示暂不支持同一句话同时指定拍摄和入库年份，请分别查询。" };
    const terms = [];
    if (!excluded.includes("date")) { const year = text.match(/(20\d{2})\s*年?/); if (year) terms.push({ key: "date", basis: /入库|导入|加入图库/.test(text) ? "added" : "capture", value: year[1], label: `${/入库|导入|加入图库/.test(text) ? "入库" : "拍摄"}时间：${year[1]}年` }); }
    if (!excluded.includes("status") && /没有拍摄日期|日期未知|无拍摄日期/.test(text)) terms.push({ key: "status", value: "unknown", label: "拍摄日期未知" });
    if (!excluded.includes("status") && /待就绪|待处理|未提取/.test(text)) terms.push({ key: "status", value: "pending", label: "日期待就绪" });
    people.forEach(p => { if (!excluded.includes(p.id) && text.includes(p.name)) terms.push({ key: p.id, kind: "person", value: p.id, label: `人物：${p.name}` }); });
    places.forEach(p => { if (!excluded.includes(p.id) && text.includes(p.name.replace("市", ""))) terms.push({ key: p.id, kind: "place", value: p.id, label: `地点：${p.name}` }); });
    collections.forEach(c => { if (!excluded.includes(c.id) && text.includes(c.name)) terms.push({ key: c.id, kind: "collection", value: c.id, label: c.name }); });
    const keywords = ["海边", "旅行", "日落", "蓝色", "徒步", "绿色", "城市", "夜景", "建筑", "春天", "公园", "散步", "山", "湖", "花"].filter(x => text.includes(x));
    let remainder = text.replace(/20\d{2}\s*年?/g, "");
    people.forEach(p => { remainder = remainder.split(p.name).join(""); });
    places.forEach(p => { remainder = remainder.split(p.name.replace("市", "")).join(""); });
    collections.forEach(c => { remainder = remainder.split(c.name).join(""); });
    remainder = remainder.replace(/没有拍摄日期|无拍摄日期|拍摄日期|日期未知|日期待就绪|日期待处理|待就绪|待处理|未提取|加入图库|入库|导入|照片|图片|拍摄|的|在|市|找|一下|所有|全部|我|想看|看看|玩耍|\s/g, "");
    return { terms, keywords, unmatched: keywords.length === 0 && remainder.length > 0 ? remainder : "", kind: keywords.length || remainder.length ? "content" : "filter" };
  }
  function search(query, scope, advanced = {}, excluded = []) {
    const plan = parse(query, excluded);
    if (plan.error) return { ...plan, data: [] };
    let data = members(scope);
    for (const t of plan.terms) {
      if (t.key === "date") data = data.filter(a => (t.basis === "added" || a.status === "trusted") && (t.basis === "added" ? a.added : a.capture).startsWith(t.value));
      else if (t.key === "status") data = data.filter(a => a.status === t.value);
      else if (t.kind === "person") data = data.filter(a => a.people.includes(t.value));
      else if (t.kind === "place") data = data.filter(a => a.place === t.value);
      else if (t.kind === "collection") data = members({ type: "collection", id: t.value }, data);
    }
    if (advanced.source) data = data.filter(a => a.sources.includes(advanced.source));
    if (advanced.person) data = data.filter(a => a.people.includes(advanced.person));
    if (advanced.place) data = data.filter(a => a.place === advanced.place);
    if (advanced.year) data = data.filter(a => a.status === "trusted" && a.capture.startsWith(advanced.year));
    if (plan.unmatched) data = [];
    if (plan.keywords.length) data = data.filter(a => plan.keywords.every(word => a.tags.includes(word)));
    data.sort((a, b) => b.id - a.id);
    return { ...plan, data };
  }
  function artwork(scene, seed = 0) {
    const shift = seed % 5;
    const sky = ["#b9d9e2", "#f4c2a0", "#d6dec7", "#bec8e2", "#c4ded0"][shift];
    const variants = {
      beach: `<path d="M0 235Q200 195 420 238T900 210V520H0Z" fill="#60a9b1"/><path d="M0 300Q200 270 410 320T900 305V520H0Z" fill="#468c94"/><path d="M0 390Q180 320 430 410T900 350V600H0Z" fill="#e4c7a2"/><path d="M0 380Q180 315 430 397T900 339" fill="none" stroke="#d7e9dc" stroke-width="12"/><path d="M700 440Q670 280 719 180" fill="none" stroke="#535e42" stroke-width="17"/><path d="M712 189Q665 132 580 156Q668 163 712 201Q766 129 840 164Q764 169 716 201Q724 102 671 101Q702 133 712 189Z" fill="#446b58"/>`,
      mountain: `<path d="M0 390L150 140 290 320 465 85 700 340 820 165 900 265V600H0Z" fill="#789998"/><path d="M350 220L465 85 583 214 513 190 472 150 420 205Z" fill="#edf0df"/><path d="M0 450L220 250 400 430 620 243 900 420V600H0Z" fill="#456f64"/><path d="M0 540L360 400 660 515 900 452V600H0Z" fill="#234c40"/><path d="M430 600Q530 470 420 450" fill="none" stroke="#d7c59c" stroke-width="20"/>`,
      city: `<path d="M0 390V245H98V195H192V288H240V150H342V260H420V102H480V87H505V310H572V228H640V285H700V179H825V325H900V600H0Z" fill="#526c77"/><path d="M0 457V335H133V293H231V399H360V316H494V420H587V280H750V399H900V600H0Z" fill="#344f61"/><path d="M0 530Q330 410 900 510V600H0Z" fill="#b4c8c0"/><path d="M0 535Q390 430 900 520" fill="none" stroke="#efc696" stroke-width="9"/>${Array.from({ length: 20 }, (_, i) => `<rect x="${24 + i * 43}" y="${330 + (i % 3) * 28}" width="9" height="18" fill="#e4bc84" opacity=".8"/>`).join("")}`,
      lake: `<path d="M0 250Q180 155 350 220T900 180V430H0Z" fill="#709887"/><path d="M0 320Q330 260 900 320V600H0Z" fill="#71adb0"/><path d="M0 427Q200 365 320 454T900 400" fill="none" stroke="#aed0bf" stroke-width="6"/><path d="M0 600V520L230 400 390 535 550 510 900 565V600Z" fill="#456e58"/><path d="M615 345L650 370 730 370 755 345Z" fill="#925f49"/><path d="M690 335V242L750 335Z" fill="#f1ebd8"/><path d="M110 405Q65 250 127 130" fill="none" stroke="#695d45" stroke-width="20"/><path d="M126 139Q212 153 183 240M121 157Q47 196 52 275M129 161Q229 200 227 308" fill="none" stroke="#537d58" stroke-width="17"/>`,
      garden: `<path d="M0 270Q230 210 450 290T900 220V600H0Z" fill="#7da282"/><path d="M0 450Q240 310 510 420T900 335V600H0Z" fill="#507752"/><path d="M460 600Q700 450 550 360" stroke="#cbbf95" stroke-width="55" fill="none"/>${Array.from({ length: 17 }, (_, i) => `<g transform="translate(${40 + i * 48},${390 + i % 4 * 47})"><path d="M0 80V0" stroke="#2e6243" stroke-width="5"/><circle r="18" fill="${["#efd2bb", "#e6b2aa", "#e9dba1"][i % 3]}"/><circle r="5" fill="#a0784d"/></g>`).join("")}`,
      street: `<path d="M350 600L438 250 520 250 650 600Z" fill="#c9bbae"/><path d="M0 0H220L385 245V600H0Z" fill="#cead92"/><path d="M900 0H720L541 245V600H900Z" fill="#87938d"/><path d="M18 88L154 150V405L18 475Z" fill="#5a746f"/><path d="M222 224L295 266V375L222 398Z" fill="#688277"/><path d="M650 207L724 145V416L650 380Z" fill="#d2c3a5"/><path d="M774 78L870 20V484L774 440Z" fill="#4d6769"/><path d="M450 540L480 420 500 540" fill="none" stroke="#e6d9bd" stroke-width="6"/>`
    };
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 600"><defs><linearGradient id="s" x2="0" y2="1"><stop stop-color="${sky}"/><stop offset="1" stop-color="#efe4ce"/></linearGradient><filter id="grain"><feTurbulence baseFrequency=".55" numOctaves="2" seed="${seed % 13}"/><feColorMatrix type="saturate" values="0"/><feComponentTransfer><feFuncA type="linear" slope=".065"/></feComponentTransfer></filter></defs><rect width="900" height="600" fill="url(#s)"/><circle cx="${600 + shift * 32}" cy="${104 + shift * 10}" r="49" fill="#ffecbc" opacity=".9"/><path d="M75 133Q160 110 248 141M280 82Q340 62 410 82" stroke="#fff6e9" stroke-width="17" stroke-linecap="round" opacity=".5" fill="none"/>${variants[scene] || variants.beach}<rect width="900" height="600" filter="url(#grain)" opacity=".7"/></svg>`;
  }
  const api = { people, places, collections, records, members, counts, dateOf, timeline, period, parse, search, artwork };
  root.DemoModel = api;
  if (typeof module !== "undefined") module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
