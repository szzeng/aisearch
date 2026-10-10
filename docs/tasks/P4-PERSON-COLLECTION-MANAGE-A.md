# P4-PERSON-COLLECTION-MANAGE-A — 人物卡展示所属合集与未加入合集筛选

日期：2026-10-10。状态：**PLANNED / 仅任务卡，尚未实施与验收**。核对基线：`master / c948bad4`（实施时须重读最新 HEAD）。目标：个人相册一张小功能卡；**显示当前人物属于哪些合集，并一键找出尚未加入任何合集的人物**。推荐 **GPT-6 High** 实施，按 V3.1 §2.4 进行受影响面独立定向工程复核，不默认全仓/高成本审计。

## 1. 接手与已有事实

先读 `AGENTS.md` → `docs/development-status.md` → 本卡；按需阅读主架构 §8.3、§10，策略 §2.4、§3.2、§9.4、§12，以及 [合集原卡](P4-PERSON-COLLECTION-A.md)、[人物小修](P4-PERSON-SMALL-FIX-A.md)、[人物 UI 规划](P4-UI-PEOPLE-D.md) 和直接关联实现/测试。**不重新设计人物识别或合集成员协议**。

已核对现有代码：

- `persons/albums.py::people_page` 支持 `kind/q/visibility/quality_view/sort=photos_desc`，服务端分页/总数；页面 `/persons/browse` 使用每页 40 人和 `readLoadedPages` 恢复已打开页及滚动位置。
- `persons/collections.py::_canonical_cte()` 已将合集成员锚点沿 `persons.merged_into` 解析到当前 canonical 人物；一个人可在多个合集，合并/撤销后不可依赖成员表原始 person_id 直接判断。
- `person_collection_members` 及 `person_collections`（含 `instance_token/revision/name`）已存在；`person_collection_members(person_id,collection_id)` 有索引。
- `web/persons-albums.js::loadPeople` 已绘制头像、名字、照片数及批量选择；`loadCollections` 仅按页加载合集，**不是完整归属数据源**。
- `P4-UI-PEOPLE-D` 是另一个人物布局任务，开工须核对是否已落地；本卡局部修改、避免并行重做卡片 UI。

## 2. 用户可见交互

### 2.1 人物卡片增加合集标签

在现有**头像 → 名称 → 照片数**下增加所属合集的小型只读标签：

- 一个合集展示一个名称；多个合集全部可见、按 `collection_id` 稳定排序并去重，自动换行；长名称不撑破窄屏人物网格，提供完整名称的无障碍文本说明。
- 无合集返回空数组，前端不显示“未加入”占位文字；不额外增加无效卡片高度。
- 覆盖已命名、未命名及已忽略人物三种现有列表；归属与隐藏状态独立。
- 整个人物卡的打开相册、批量勾选、键盘操作保持；**不在当前卡片 button 内嵌新的交互按钮**。标签首版不提供点击跳转、移出或重命名。
- 仅显示当前有效合集，不显示已删合集、重复锚点、内部 ID/revision。

### 2.2 “只看未加入合集”开关

人物搜索框附近增加复选框：**只看未加入合集**（默认关闭）。

- 开启后，仅显示**当前 canonical 人物不属于任何现存人物合集**的结果；无论该人物是否命名或忽略。
- 与现有 `kind=named|unnamed|all`、`visibility=visible|hidden|all`、名字/别名子串 `q`、`quality_view` 和 `sort=photos_desc` 取 AND；结果总人数、页面内容和“更多人物”一致。
- 整理一下中的**未命名人物**参与；`faces/inbox` 里尚无有效 person_id 的单脸/候选组**不参与合集过滤，不被隐藏**。
- 切换“人物 / 整理一下 / 已忽略”时保留开关状态；进入人物详情再返回也保留。无需跨服务重启持久化。
- 切换筛选须从第一页重新加载并失效旧请求/游标；若正在跨页批量选择，**清空原选择并提示**，避免选择已不可见的人，同时保留批量管理模式和当前工具展开状态。
- 空结果提示“当前没有尚未加入合集的人物”，不要误报“图库没有人物”。
- 批量加入合集成功后，在当前筛选开启时从服务端重新读取（已加入者退出列表、总数变化）；关闭筛选时人物仍保留，标签更新。失败不能提前移除卡片。

## 3. 服务端最小改动

**只扩展** `GET /api/v1/persons/browse` 与其 `people_page`，增加可选 `collection_state=all|none`，默认 `all`。不变更旧 `GET /persons`、人脸、搜索或成员写入 API。

- `all`：保持原过滤、排序、总数和既有游标字节兼容；每个当前页 person 的 dict 加只读 `collections: [{collection_id:int, name:str}, ...]`，无合集 `[]`。
- `none`：将“该 canonical 人物不存在于任何当前有效合集”的条件**推入 SQL 候选集合**，并在 `COUNT(*)`、排序和分页前应用。禁止先取 40 人再 JS 过滤，禁止遍历每人 HTTP 查合集、禁止按集合全量展开照片/向量 ID。
- 复用 `_canonical_cte` 或实测等价的递归终点 SQL；不能仅 `NOT EXISTS WHERE m.person_id=p.person_id`，否则合并人物误判。成员锚点可多个，返回人物与合集均须去重。
- 人物列表每页最多 40 人，**页内一次批量归属投影**（或固定少量集合查询），只按页中 `person_id` 找合集；不可循环 `_album_summary` / API 查询合集形成 N+1。避免引入依赖和持久缓存。
- `none` 的游标须与 `collection_state`、既有人物过滤版本和当前合集成员版本绑定。建议用按 `collection_id` 排序的 `(collection_id,instance_token,revision)` 生成确定性摘要：涵盖加入/移出/删后重建，避免旧游标跨版本继续；人物合并/撤销继续关联现有 identity/filter revision。摘要只进 `none` 新游标，不改默认 `all` 原格式。版本失效返回现有 409 刷新语义。
- 新筛选期间数据改变时，首屏/后页不能拼接两种不同归属状态；如同次多页重读中发生变更，优先提示刷新，不把旧数据当新结果。
- 复用现有索引，必要时看一份 `EXPLAIN QUERY PLAN`；如果全量 recursive CTE 影响明显，改成候选限定的等价批量 SQL，但不得牺牲合并/撤销语义。不得为本需求做无关性能改造或数据库迁移。

## 4. 前端接入与刷新边界

主要范围：`web/persons-albums.js`、`web/persons.html`、`web/persons.css`；后端 `persons/albums.py`、`persons/collections.py`、`api/routes.py`，直接测试文件（按实际最小改动）。

- 将开关纳入 `loadPeople` 的查询值、列表 binding/请求序号，切换时清旧列表游标并保证**仅最新响应落地**；不靠 `collectionById` 的第一页合集中途填充标签。
- 复用既有 `refreshPeopleViews`、已加载页数回放及滚动锚点逻辑；返回人物相册时筛选保留，筛选变化则新查第一页。保留 `more` 翻页、搜索、已忽略入口和批量勾选逻辑。
- 加入、移出、改名、删除合集、人物合并/撤销后按现有通知/刷新路线重新读取受影响卡片，异步旧响应不得覆盖新标签、计数与筛选。
- 仅在成功写入后更新列表；失败保持选中、原卡、位置与错误提示。不得意外更改 `hide_after_add` 或“移出合集并恢复可见”的现有业务语义。
- `P4-UI-PEOPLE-D` 如已实施，复用当时 DOM/样式；不再改全局布局。

## 5. 边界和最小验收

**严禁扩范围：** 不改 schema/migration、成员写协议、身份/人工状态/撤销事务、识别模型/8张代表脸、GPU、向量/FTS、原图与缩略图。个人相册小优化，不接入自动分类、按指定合集筛选、标签点击移出等新需求。

用隔离 SQLite 合成数据、定向 Node DOM 测试即可；不得访问或修改真实 `PhotoSearchData` 或在用服务。

1. **展示正确**：无合集/单合集/多合集，已命名/未命名/已忽略；长名称；同一合集多锚点只出现一次；改名/删除刷新正确。
2. **筛选和分页**：例如合成 85 人、每页 40 人，确保服务端 `none` 总数正确、跨页无重复遗漏，仍按照片数降序；与名字、质量、可见性 AND；旧 `all` 默认结果和游标兼容。
3. **合并和失效**：A 属合集，合并 A→B 后 B 不被算“未加入”；共属合集不重复；正式撤销后恢复；加入/移出/删除或实例变化后新查询正确，旧 `none` 游标不能悄悄续页。
4. **少量 DOM 交互**：开关 → 批量选择 → 加入合集 → 卡片退出筛选、数量更新；关闭开关显示标签；筛选变化清选择；详情返回状态保留；旧异步响应不覆盖；已经加载多页的刷新/返回行为未退化。

可复用 `tests/unit/test_p4_person_collection_ui.py`、`test_person_loaded_pages_ui.py`、`test_person_count_ui.py`、`test_person_sort_ui.py` 及合集相关定向集成（存在者选运行），新增最少几个覆盖点。执行实际变动所需 pytest（项目 `.uv-cache` / `.pytest-tmp`，输出 JUnit XML）、Ruff、`node --check`、`git diff --check`。不跑全仓、30万/GPU/模型/打包/浏览器矩阵；不虚构测试 PASS。

用户手测：人物加入“同学”“同事”后两个标签出现 → 开启“只看未加入合集”人物退出 → 批量加入“路人”后剩余人数减少 → 关闭筛选标签正常 → 从合集移出人物后重新进入未加入列表 → 已忽略和详情返回正常。

## 6. 模型、复核与交付

- **推荐 GPT-6 High** 一轮完成后端只读 SQL/API、UI 和定向自测；按 V3.1 §2.4 让独立 DS-H 覆盖**全部实际受影响调用链**（canonical 语义、总数/分页/旧游标、筛选状态、写后刷新），不是全仓审计。
- 若发现必须改变身份/成员写协议、持久 revision、数据库迁移或其他高风险数据状态边界，**停止扩大本卡，记录差异另议**；不要为了一个筛选引入新协议。
- 提交代码与必要测试、简明 `docs/reports/P4-PERSON-COLLECTION-MANAGE-A.md`、更新 `docs/development-status.md`，清楚标记独立审查和真实页面试用是否完成。此卡完成后停止，不自动接续智能分类或合集筛选新卡。
