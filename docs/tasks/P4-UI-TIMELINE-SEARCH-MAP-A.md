# P4-UI-TIMELINE-SEARCH-MAP-A — 搜索结果与地图照片时间模式接入

> **2026-10-13 最新视觉验收入口（20万＋大图库）**：[17页图集/离线HTML](../mockups/ui2-large/README.md)；本卡参考 [12-natural-search](../mockups/ui2-large/12-natural-search.svg)、[13-import-search](../mockups/ui2-large/13-import-search.svg)、[14-unknown-search](../mockups/ui2-large/14-unknown-search.svg)、[11-map](../mockups/ui2-large/11-map.svg)。本目录 SVG 为按最新方案重制的仓库可预览矢量设计稿，并非聊天高清 PNG 原件；原 `ui2/` 参考降为历史视觉对照。视觉稿不替代业务和性能证据。


> **2026-10-13 新视觉参照：** [UI 2.0 全页面索引](../mockups/ui2/README.md)；本卡对应 [09-search](../mockups/ui2/09-search.svg)、[08-map](../mockups/ui2/08-map.svg)。最新 SVG 是仓库内的轻量静态效果稿；旧版 PNG 仅作导航/色彩对照，不得据旧图删掉时间轴能力。产品数据契约、布局取舍及真实功能以 [UI 2.0 规范](../plans/P4-UI-TIMELINE-V2.md) 与本卡正文为准。此处并非实施截图。


日期：2026-10-13。状态：**PLANNED / 未实施**。前置 [CORE](P4-UI-TIMELINE-CORE-A.md)、[B](P4-UI-PHOTOS-B.md)、[C](P4-UI-SEARCH-C.md)；地图接入另依赖 [E](P4-UI-PLACES-E.md)。本卡仅是最后的适配，不重构检索或地图算法。

推荐 **GPT-6 / High**；搜索候选与成员/游标契约受影响，按 V3.1 B 类必要定向验证、独立 DS-H 全受影响面核查；若要改变排名/TopK 或地图身份与写协议，停止并升级单独设计。

## 前置核对

`AGENTS.md` → `docs/development-status.md` → 本卡 → [UI 2.0 统一规范](../plans/P4-UI-TIMELINE-V2.md) → [搜索 C](P4-UI-SEARCH-C.md)/[地点 E](P4-UI-PLACES-E.md) 已完成报告 → 直接相关 `web/search.js`、`web/map.js`、`web/search.html`、`api/queries.py`、当前 `/search/place-aware`/纯条件查询/地图范围与 member_version 实现及必要契约。特别核对用户最近的统一自然语言解析和纯条件不受 1000 上限、语义候选仍有截断的事实；别混淆为统一“全库时间排序”。

## 唯一闭环

### 搜索结果
- 默认**相关度**排序与当前候选语义原样保留。仅在已有搜索结果 UI 上增加「按时间查看」模式；清楚显示“按时间整理**当前检索候选**”还是“纯条件完整结果按时间浏览”，避免用户误认为两个模式命中集相同。
- 对**纯权威过滤**（人物/合集/地点/时间/图库等无需语义评分）尽量沿用全量数据库成员过滤 + 时间轴服务端时间页；与原完整分页一致，不受浏览端/TopK 截断。纯地点分页与修订语义照旧。
- 对**FTS/向量/融合语义结果**：不得仅按当前前端已加载页排序后叫“整个搜索结果按时间”；先证明能用冻结且有界的 session 候选快照/游标在服务端正确分页，且清楚标记候选截断/上限。若缺该保证，**不要开放这个入口**，只维持原相关度结果并在报告记录此子项后置，不改原搜索算法补洞。
- 搜索页仅一个主输入框、可取消 intent chips、失败保留输入；范围/时间/人物取消后旧 time cursor 必须失效；搜索→照片详情→人物相册→返回恢复原模式、过滤、页面与滚动，不重复触发搜索。

### 20万图库搜索条件分流与日期语义（新增必要且有界的解析/只读扩展）

- **语义识别冻结**：沿用 `search/unified_intent.py`、`intent_plan`、`search/natural_intent.py` 现有“一次识别→一份计划”；仅增加确定性的 `time_basis=capture|added`、`capture_status=trusted|unknown|pending` 条件，名称可依现有风格调整。默认“2020年照片”按原可信拍摄时间；“2020年导入/入库/加入图库照片”仅按 `assets.created_at` 筛选；“没有拍摄日期的照片”严格匹配已提取但无可信来源，“日期待处理”匹配尚未提取/规则版本失配。**绝不在 UI/SQL 自动填拍摄日期或加入手动改日期**。
- **执行路径**：当解析后无实质内容词（如“2021年张三的照片”“2020年入库照片”“没有拍摄日期的家庭合集照片”），直接调用原权威 SQLite FILTER_ONLY/时间轴服务端全量分页；**不得调用文本图像 encoder、FTS/向量或 search_assets 的TopK排名路径**。含“海边、全家福”等剩余内容词时才走原 FTS/Vector/RRF，并在召回前通过同一权威时间/人物/来源/地点条件限制候选。
- **完整性**：纯条件不受语义 5000 上限，并可跨页取完权威匹配；语义查询保留原 TopK/截断/相关度与 `search_session_id`。若实现当前语义候选时间模式，必须使用服务端冻结候选全集与版本/游标并明确“仅当前候选”，不能浏览器排序已加载一页就冒充全库；缺安全候选快照则只保留相关度模式，报告 DEFERRED。
- **冲突与续页**：若一句话同时明确拍摄和导入年份、要求不连续 OR 等而尚无正确交集合同，返回明确 `unsupported` 说明而非吞掉任一条件；续页冻结首次解析的相对日期、时间依据、状态与 scope，人员/地点/包/修订变化返回现有冲突语义。时间范围继续左闭右开。用户取消任一标签需重算计划并抛弃旧 cursor/session 的相应范围，不能将状态词落到 FTS 搜图。
- **边界**：这属于 SEARCH-MAP 卡明确授权的**最小加性解析/只读合同**，允许核对必要 API request model/intent plan；不改旧 `capture_from/to_exclusive` API 含义、不重写全部解析器、不改向量/FTS/RRF、人工归属/自动地点确认、不建设新索引数据库。现有 source/person/collection/place 身份沿用权威资格。
- **性能**：在 CORE/E 共享的20万元数据夹具上验证拍摄年份、入库年份、可信未知、张三+年份、地点+年份等纯条件的实际 SQL/query plan、首50～60条 API 与直接下一页；至少≥20次暖机测量，结果与 SQL 全集/COUNT 在小fixture及规模集合抽样一致。纯条件**零编码器调用**可计数证明；语义路径仅做少量假向量/现有已验证搜索语义回归，不重跑30万 GPU/AI 基准。逐页不自动计算全部年月分布。不能达到 CORE/E 对应页目标，报告 PERF_PENDING/FAIL 而非借历史向量测速宣布达标。

### 地图范围照片
- 原地图聚合/瓦片/点位、bbox/zoom/member_version/点击点的相片、GPS 与当前范围统计完全不变。仅“当前范围照片列表”可适配时间轴组件；范围变动时必须按当前 map 的权威 bbox/member_version 获取成员并重置游标。
- 不能把地图当前显示的一页图片用前端时间排序当成整个 viewport 的时间轴；无法提供全范围权威日期页则保留现有分页，不强行落地。地图缺瓦片、照片离线时有原降级行为；来源与版权继续显示。
- 与原 `map.js` scope/事件状态机保持一致，不在地图页实现第二套导航或全新地图请求层。

## 权限和隔离

可做上述**最小加性确定性时间依据/未知状态的 intent plan、请求模型和只读 SQL 适配**，不更改旧搜索 API 字段的语义；其余只读查询/前端 adapter、既有统一意图解析的其他类别、FTS/vector/RRF、语义 TopK、snapshot/revision、防过期与地图协议全部保留。绝不根据前端几张图片推断总数/完整年月分布；模型缺失的搜索不可伪造结果。禁止真实 PhotoSearchData、模型或地图大数据重建。

## 最小验证与停止

合成 fixture：一个完整纯条件搜索跨两页按时间浏览；一个有上限的语义检索仍默认相关度、有提示且不能错误冒充全量；一组 intent 标签取消/再查询; 一个地图 bbox 缩放后照片集合变化；一组 409/过期 cursor；一个详情关闭/跨页面返回。若部分后端语义无法保证，则记录为**DEFERRED**，不要为赶进度降低正确性。

针对受影响测试文件执行 `pytest -q ... --basetemp=.pytest-tmp/P4-UI-TIMELINE-SEARCH-MAP-A --junitxml=test-results/P4-UI-TIMELINE-SEARCH-MAP-A.xml`，改动的 JS 全部 `node --check`、相关 Python Ruff/编译、`git diff --check`；小型隔离浏览器验证，杜绝无真实运行冒称现场 PASS。不默认全仓/GPU/大模型/用户照片库；**须复用CORE/E的20万元数据查询定向基准、30万有代表性组合查询复核，不能只做20张合成测试就宣布大图库性能通过**。

独立 DS-H 复核查询资格、候选冻结、排名未改、分页以及地图 bbox/member_version 与 UI 返回。报告 `docs/reports/P4-UI-TIMELINE-SEARCH-MAP-A.md` 与 status 只标实际完成范围；完成即停，下一建议 F。
