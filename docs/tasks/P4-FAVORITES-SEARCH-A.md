# P4-FAVORITES-SEARCH-A — 收藏范围搜索（组合过滤与独立版本保护）

- **状态**：IMPLEMENTED / ACCEPTANCE PARTIAL；收藏限定搜索、受影响测试与用户指定 Luna High 独立复核已完成，浏览器中的“收藏 + 人物/地点控件 + 改词/取消”闭环仍 PENDING，故本卡不标 PASS。详见 [执行报告](../reports/P4-FAVORITES-SEARCH-A.md)。
- **优先级**：P2；**推荐执行模型**：GPT-6 High（由用户在 Codex 手动选择）；共享检索协议/版本边界按 V3.1 §2.4 做独立 **DS-H 完整受影响面工程审查**，不因本卡而自动启动其它任务。
- **设计基准**：`szzeng/aisearch`，`master @ 9adbad5bcc1a783025f2d8474fdb8956af403fb6`（2026-10-19）；仓库已有 `0034_asset_favorites.sql` 与 `0035_cr2_job_recovery.sql`。**实施前重新核对最新 HEAD、工作树、有关报告及实际测试结果**，不覆盖并行 CR2、viewer、合集和收藏 UX 修改。
- **前置依赖**：`P4-FAVORITES-CORE-A` 已于 2026-10-19 标为 PASS（组合证据），**硬前置已满足**；`P4-FAVORITES-UX-B` 已于 2026-10-19 标记 PASS；其星标/批量/排序仍属 B 的独立范围，本卡不重做这些功能。普通照片合集和智能照片合集均不是本卡前置依赖。
- **阅读范围**：`AGENTS.md` → `docs/development-status.md` → `docs/testing.md`、`docs/performance.md` → 本卡 → `docs/tasks/P4-FAVORITES-CORE-A.md` 与报告、`P4-UI2-SEARCH-SCOPE-UNIFY-A` 与报告；按直接调用链阅读 `src/photo_search/{favorites.py,search/retrieval.py,search/service.py,search/sessions.py,search/contracts.py,api/models.py,api/routes.py,web/app.js,web/search.js,web/search-scope-entry.js}`。主架构仅查搜索/版本/性能相关章节、执行策略 V3.1 §2.4、§3.2/3.4、§4.2。

## 1. 用户目标与范围定义

让“收藏”成为现有搜索中的**独立、可组合、由服务端权威校验**的条件。整个图库有 30 万照片、其中 2,000 张收藏时，输入“海边的日落”并勾选“仅搜索收藏”，应在收藏照片集合内运行现有文字/语义排名，不能先取全库 TopK 后再过滤收藏。

**交付两处入口**：

1. `/?view=favorites` 收藏照片页，页头唯一搜索框旁显示 **「搜索收藏」**（也可用“搜索此收藏”，全产品文案保持一致）。点击将**当前关键词 + 收藏限定 + execute=true** 一次性交接到 `/search`，立即执行一次搜索；空关键词明确执行一次 `FILTER_ONLY`。页头默认全库搜索按钮/Enter 延续既有行为，不暗带收藏限定。
2. `/search` 原有搜索表单内增加 **「仅搜索收藏」** 勾选项；它与既有来源、人物、地点、日期、高级筛选以及既有固定人物/地点/合集范围按 **AND** 求交，允许用户直接在全局搜索页勾选。结果页显示**一个**明确的收藏限定标签/状态，能单独取消，不复制条件、不覆盖其它固定范围。

**范围成员口径**：唯一真值为现有 `asset_favorites.asset_id`；保持搜索原有的 ACTIVE/图片资格规则 `photo_asset_sql`、已索引覆盖与原图离线展示语义；多路径同一 asset 只出现一次。取消收藏只改变成员资格，不删除照片；重导入产生新 asset_id 不自动继承收藏。收藏不是人物合集、照片合集成员、AI 自动标签或全局筛选 revision。

**不做**：新收藏表/migration、额外向量索引或重算模型、AI 自动收藏、将输入“收藏”文字当系统指令、图片上传的收藏限定检索、智能合集的规则持久化/自动刷新、普通照片合集的保存流程、全图库人工状态改写、重构 UI 2.0、30 万整库正式容量认证。已有 B 卡覆盖的星标与批量操作仍由 B 实施。

## 2. 现有代码事实与直接风险

- 收藏已经实现：`src/photo_search/favorites.py` 以 `metadata['favorites_revision']` 提供独立递增版本，`asset_favorites(asset_id PRIMARY KEY, added_at_ms)` 持久成员；`GET /favorites`、`GET /favorites/states`、`PUT/DELETE /assets/{asset_id}/favorite` 已存在。收藏写入**不推动 `catalog_filter_revision`**；不得为了本卡改变这一不变量。
- 搜索统一入口：前端 `web/search.js::sendTextSearch()` 调用 `POST /api/v1/search/place-aware`，附加模型 `PlaceAwareSearchRequestModel` 允许 `search_scope`，但当前 `search_scope.kind` 仅处理 person/collection/place；source 单独交给 `source_id`。基础 `POST /api/v1/search` 使用冻结的 `SearchRequest` 信封，**不要修改冻结合同**。
- 范围管线：`search/retrieval.py::SearchScope.build/scope_condition` 被 FTS、向量候选扫描、纯条件权威成员与结果页复核共用；`search/service.py::search_request` 向 `search_assets` 传范围。必须在**候选排名前**限成员，不能从既有全库排名结果中裁切。
- 纯条件搜索：`api/routes.py::_filter_only_search_page` 使用 SQLite Keyset + COUNT，不是可用的进程内 `SearchSession`；选定地点还有 `_place_album_search_page` 独立分页。`search/service.py::_continue_session` 有会话续页及修订校验；`GET /search/sessions/{id}/time` 有**独立的候选拍摄时间分页/版本检查**。这些路径必须一起覆盖。
- 前端已用 `web/search-scope-entry.js` 的 `sessionStorage` 一次性 `scope_entry`、`search.js::consumeEntryScope/restoreOrStartSearch`、`conditionFingerprint`、预览/返回 `saveSearchSnapshot/restoreSearchSnapshot`。当前 `app.js::updateSearchScopeAction` **显式隐藏收藏页范围按钮**；`configure.getScope()` 也只读取 source，不能仅修改文字而忽略这些真实守卫。
- 搜索会话最多保留 `MAX_SESSION_IDS=5000` 个排名候选、TTL 600 秒；FTS/COMBINED 的 `top_k` 与覆盖不足提示仍按原合同。收藏限定不能绕过模型/profile/generation、结果截断或索引追赶的现有说明。

## 3. 产品交互合同

| 场景 | 行为及必须保持的边界 |
| --- | --- |
| 收藏页有词点击「搜索收藏」 | 直接 `/search`，恰好一次收藏限定检索，显示收藏限定；当前输入框的默认全库提交/Enter 仍是全库搜索 |
| 收藏页空词点击范围按钮 | 恰好一次 `FILTER_ONLY`，返回全部符合搜索资格的收藏照片、按服务端 Keyset 分页，不加载语义编码器 |
| 搜索页勾选「仅搜索收藏」 | 下一次明确提交才执行；已有结果/游标立即标为过期并清理，不把原结果解释为限定搜索 |
| 搜索页取消该勾选/标签 | 仅取消收藏限制，保留原关键词、固定人物/地点/合集等独立条件；清空旧结果/分页，**不自动重新执行扩大范围的全库搜索** |
| 同时选择人物、地点、日期、来源等 | 取交集；原有 any/all 人物语义、固定相册身份及地点消歧不变；非空或空交集均正确，不重复 chip |
| 搜索结果改词、二次提交、继续翻页、切换时间浏览 | 继续携带收藏布尔限定与正确版本；源/人物/地点等其它条件也保持，不暗中清除或放大范围 |
| 在限定结果中收藏/取消收藏 | 复用权威写入接口；写成功导致版本变化时立刻使本次收藏范围结果/续页失效，提示“收藏已变化，重新搜索”；不拿旧排名继续增删卡片凑数 |
| 搜索会话失效、修订变化、旧请求迟到 | 明确显示需重搜；不自动回退全库、不静默返回旧候选、不让旧请求覆盖新页面 |
| 搜索结果预览/完整详情/浏览器 Back | 可信地还原收藏限定、搜索状态及合理滚动；恢复时先正确识别版本/会话失效，过期结果不得冒充当前结果 |
| 范围限定后切换上传图片搜索 | 与现有固定范围策略一致，**明确阻止**尚不支持的限定上传搜图；未限定上传搜图照常可用 |
| 空收藏、纯文字、综合语义模型缺失或索引追赶 | 分开显示“范围内没有收藏”“无匹配”“模型不可用”“索引未覆盖”等真相，不把部分索引误写成全集零命中 |

**同一布尔条件仅有一个客户端真值**：推荐 `state.search.favoritesOnly`。它是独立 overlay，不放入现有互斥的 `state.search.selectedScope`，因此可以同时选固定人物/地点/人物合集。来自收藏页的一次性交接允许本地识别 `scope.kind='favorites'`，但在消费时立即转为 `favoritesOnly=true`，**不得把 `kind=favorites` 原样送入后端 `search_scope`**；若以后页面已另有固定范围，两者不会互相覆盖。checkbox 与 chip 同步到一个布尔状态；复用当前取消/清理函数，勿注册两套监听。

## 4. 最小 API / 检索实现

### 4.1 加性请求与权威回显

- 推荐仅在 `api/models.py::PlaceAwareSearchRequestModel` 增加 `favorites_only: StrictBool = False`（按现有 Pydantic 约束选等价严格布尔类型）；未提交时为 `false`，字符串/数字伪布尔不应默默当真。
- `api/routes.py::_place_aware_search_worker` 取该开关；在 `payload.model_dump(exclude=...)` 构建原冻结 `SearchRequest.from_envelope()` 前**明确排除 `favorites_only`**，作为独立、服务端可信的附加参数送给执行路径。基础 `/api/v1/search` 与上传图片请求合同不变。
- 正常成功响应在 `/search/place-aware` **回显** `favorites_only: true|false`，启用时另回显本次钉住的 `favorites_revision`（未启用可以省略/null）；原 `search_scope` 仍用于真实人物/地点/合集固定范围，不能借用/覆盖其字段。响应由服务端生成，不相信客户端提交的版本。
- 任意自动自然语言解析、同名人物/地点消歧以及继续分页必须保留该 flag；不改变词义解析器，把收藏条件显式留在检索域层，不根据输入“收藏”自动猜测启用。

### 4.2 唯一权威 SQL 条件，加入候选前

- `search/retrieval.py::SearchScope` 新增 `favorites_only: bool=False`；在 `SearchScope.build()`、`scope_condition()` 使用现有 `asset_favorites` 主键成员条件，并适配 `alias`、`bounded_candidates`：一般召回可采用 `a.asset_id IN (SELECT asset_id FROM asset_favorites)` 或经实测等价且计划更好的 JOIN / EXISTS；小量结果页复核可使用按 `asset_id` 主键探测的 `EXISTS`，避免在每张待确认照片上反复生成整份成员集。
- 确保 `search/service.py::search_request`、`search_assets` 和 FTS、Lance/向量候选扫描、`_verified_ids`、pending-projection 检查以及纯 FILTER 的 `_filter_only_search_page`、地点 `_place_album_search_page` 都使用**同一个 SearchScope 的收藏成员语义**。需要调用边界传 bool，不可从自由拼接的请求 SQL 或客户端 asset_ids 建范围。
- 保留 `a.state`、`photo_asset_sql`、原有来源多路径去重、人脸成员关系、地点活动包与可信拍摄时间的既有过滤规则。`TEXT/COMBINED` 的 FTS/向量排序必须基于收藏候选集，不得“全库 TopK → 过滤收藏 → 不足则假装搜完”。
- FILTER 搜索在收藏为空时直接返回真正的 0，避免无谓模型/向量；`FILTER_ONLY` 应持续使用服务端 Keyset（默认沿用搜索页 page_size），不能一次向浏览器发送全部收藏，也不能用收藏列表的“最近收藏”游标冒充搜索游标。纯 FILTER 与排名的总数/候选截断语义保持区别。

### 4.3 独立 revision 与会话/游标绑定（本卡最重要的不变量）

- 只读取现有 `metadata['favorites_revision']`（建议复用 `favorites.py` 的权威读取/校验逻辑，必要时提炼公共只读函数）；不新增持久版本、不额外 bump `catalog_filter_revision`，也不因收藏搜索读取改写数据库。
- **仅当 `favorites_only=true`** 时，在首次检索前锚定收藏版本；排名完成、结果页权威复核之后、准备发布会话/响应之前重新读取。并发写入期间不可发布跨版本混合结果；可在现有有界重试预算内重算，仍冲突则以结构化 `409 FAVORITES_REVISION_STALE` 拒绝，绝不降级为不带收藏条件的结果。
- 首次查询的 `query_hash` / 会话范围身份必须包含标准化 `favorites_only`，并单独保存被钉住的 `favorites_revision`。后续 `search/service.py::_continue_session` **先校验当前收藏版本再读取旧排序/继续分页**；未开启收藏限制的全库搜索**绝不能**因为收藏写入而失效。新增收藏、取消收藏、批量收藏只要有真实变化，限定搜索旧游标均返回 409；幂等 no-op 不应使会话过期。
- 纯 FILTER 游标（`_filter_only_search_page`）与地点 FILTER 游标（`_place_album_search_page`）也需纳入 bool、版本和原有 query/过滤 fingerprint；旧游标跨范围或跨版本不能被接受。不能只把修订号拼到 `time_identity` 然后忽略成员 SQL，或只在前端失效。
- 候选“按拍摄时间浏览” GET `/api/v1/search/sessions/{id}/time` 必须从可信 session 取收藏限定及其 pinned revision，在获取后续时间页之前做相同 409 检查；即使 `album_scope_revision` 被复用，**不能误触发现有地点 `album_membership_sql` 的固定位置索引、人物合集 marker 解析或改成“地点发生变化”错误**。建议显式增加可读的 favorites revision slot/专用校验函数，而不是向异构元组盲目插值。
- 版本/范围、授权和错误码在新查询、并发收藏写入、续页、返回恢复、时间浏览保持同一行为。现有 `FAVORITES_REVISION_STALE` 错误码已用于收藏列表，可复用其语义，但不能让现有普通搜索错误码发生变化。

## 5. UI 接线与兼容范围

1. `web/app.js::updateSearchScopeAction()` 和 `SearchScopeEntry.configure.getScope()` 同时适配 `state.browse.scope==='favorites'`，使收藏页展示唯一“搜索收藏”按钮，读 `#global-search-query` 与 `scope_entry` 一次性交接；保留现有 `/?view=favorites` 网格、缩略图、滚动恢复、导航高亮和 viewer。不得另建收藏搜索 HTML。
2. `web/search-scope-entry.js::validScope()` 可识别零 ID 的结构化 `{kind:'favorites'}`，禁止依赖显示文字或伪造收藏 asset_id 列表；一次性 token、浏览器会话存储失败、非法/旧 token 的可见错误合同保持。消费后 `favoritesOnly=true`，`selectedScope` 只存真正固定的人物/地点/合集/来源身份。
3. `search.html` + `search.js` 在现有唯一表单中加入 checkbox（带明确可访问中文标签）；将 `favoritesOnly` 接入 `conditionFingerprint`、`sendTextSearch`、结果回显、chip/checkbox 同步、筛选变化时清理旧 session/cursor/timeBrowse/可见结果与 generation、`saveSearchSnapshot/restoreSearchSnapshot`、预览返回和已经存在的恢复校验。
4. `search.js::renderIntentChips()` 可以取消“仅搜索收藏”，但保留其它条件；无收藏限制时不显示收藏 chip。避免 checkbox 与 chip 相互重新执行、重复事件监听或两个标签。切换后等待用户主动提交，不静默扩大查询。
5. 结果页连续提交、自然语言歧义选择、固定范围取消、来源筛选和回退不得丢失收藏开关；非空 q 的 `text` 模式也必须带布尔限定。图片上传遇 `favoritesOnly` 为真即按当前 scoped upload 策略明确阻止，不能偷偷全库查。
6. 搜索页星标若由 B 已实现，收藏改变后利用现有状态变更钩子及时标记当前限定结果失效并提示重新搜索；如果 B 未实现，本卡只依赖 A 的 viewer 单图收藏回调及从返回/刷新获取最新版本，不替 B 重构全站缩略图状态。
7. 页面可见时若检测到当前限定收藏结果所钉的版本已经不同，停止旧续页/时间浏览，提示刷新；不加长期 polling，也不要求跨标签实时同步。内置浏览器需核查页头操作位置、单次执行、窄屏换行与按键可访问性；视觉使用已有 UI2.0 token。

## 6. 与照片合集/智能合集的隔离

- 收藏不等于 `photo_collection_members` 或 `person_collections`；收藏搜索只能读 `asset_favorites`，不改合集成员/排序/排除/动态规则，不创建搜索快照。
- 当前 `P4-PHOTO-COLLECTION-A` 与 `P4-PHOTO-SMART-COLLECTION-A` 仍是任务卡，不应预先依赖它们尚未实施的 `source_token` 或保存 API。将来普通合集支持“保存本次结果”时，必须以服务器受信搜索来源和本次收藏版本复核全部候选，不能从 DOM 取已呈现的 50 张冒充全集。
- 将来智能合集如允许基于收藏创建规则，必须由其独立任务把 `favorites_only` 纳入持久规则签名、刷新/候选过滤和收藏修订失效；**本卡不新增这一能力**。如果智能合集保存入口先落地却不能忠实保留收藏条件，在收藏限定搜索中应明确禁用该保存动作或提示规则不支持，绝不悄悄把收藏限制丢弃而创建全库智能合集。

## 7. 性能设计（三行默认 + 关键访问路径检查）

- **链路与风险｜初始 PERF-BOUNDED**：公共检索路径加了一个收藏成员谓词，风险是 30 万 `assets` 全库倒扫、FTS/MATCH 重复求值、向量先全库取 TopK 再过滤、纯 FILTER 分页重复 `COUNT` / 深页 offset 计数、逐图收藏状态查询和首屏被同步 SQL 阻塞。
- **做法与边界**：只在 `favorites_only=true` 时加一个由现有 PK 索引支持的成员条件，保留现有 FTS/向量候选分块与排名、Keyset 和批量投影；不物化所有收藏 ID 进 Python/JS、不生成收藏专用向量表、不额外模型推理、不增加全站每次查询的收藏 JOIN。非收藏搜索的 SQL、修订、热路径保持原状。
- **验证与停止**：编码前列出 N（总资产）、F（收藏成员）、K（检索候选）、P（页）及实际索引访问方向；用 F=0、少量、稀疏/密集（例如合成 F≈100、2,000、50,000）、交集低/高命中，检查 SQL `EXPLAIN QUERY PLAN`、请求/SQL 数、FTS 与向量候选是否先限收藏、COUNT/offset 路径及是否有每图 N+1。基于受影响定向计时或小型对照发现真实 30 万规模风险才依 `docs/performance.md` 升级 PERF-SCALE；**不强制整库 30 万/GPU 压测，不凭小数据宣称 30 万达标**。完成必要功能与浏览器验收后停止，不自行追加优化卡。

不可把“有主键/EXPLAIN 不显示 SCAN”当成速度证明；不同收藏密度下选择 `IN`/`EXISTS` 的最佳路径应有执行计划与正确性证据。尽量避免初始检索之外为同一次搜索做额外多次全量 count；若保留现有精确计数合同，需说明成本并对照旧非限定路径。不要为加速减少原有 `top_k`、降低模型质量或省略权威再校验。

## 8. 最小验收与测试矩阵

**后端/正确性**（推荐新增 `tests/integration/test_p4_favorites_search.py`、必要 `tests/unit/test_p4_favorites_search_scope.py`，复用现有 search fixture、fake encoder/index；按实际代码调整）：

1. `favorites_only=false/缺省` 与旧普通查询等价；普通搜索进行时收藏增删不导致其结果/游标无端失效；冻结基础 `/search`、图片上传 API 不变，严格布尔拒绝非法值。
2. `F=0`、1、同资产多路径、离线、非图片、刚添加/删除/幂等写入；收藏范围只有有效照片、去重且空态正确；无需模型的空词 FILTER_ONLY 不触发 GPU 或查询编码。
3. 独立检验 `FILTER_ONLY` 的 50 张 Keyset/深页、相同页面游标只使用同一个条件 fingerprint；组合 source、person（any/all）、已选地点、人物合集与 capture 条件；不同查询排序/条件/范围不可复用旧 cursor。
4. `TEXT` FTS + `COMBINED` FTS/向量/融合结果由收藏候选集排名；人工 oracle 放置“收藏图全库排名大于 TopK”样例，证明未采用全库 TopK 后过滤；`candidates_truncated`、index pending、`semantic_ready` 和无模型提示不失真。
5. 初次检索过程中并发加入/取消收藏：发布前版本变化须有界重试或 409，无跨版本混合结果；首次分页、续页、时间浏览、返回恢复都验证 `FAVORITES_REVISION_STALE`；幂等 no-op 不误失效，未限定会话不误失效。
6. 收藏 + 固定人物/地点/人物合集范围同时存在；自然语言消歧前后/重复提交不丢收藏条件，取消收藏仅取消收藏，取消其它固定范围也不清收藏；旧请求迟到不回填，失效条件不默默变全库。
7. 登录会话、错误结构/状态码、身份越界、错误后 UI 恢复；保存候选时间浏览与普通收藏页 `GET /favorites` 不混淆版本/排序；不修改旧图片/原人工状态。

**前端/产品浏览器**：

- 通过 `tests/unit/test_p4_ui_search_entry.py`、`test_search_text_mode_ui.py`、`test_p4_search_nl_ui.py` 及已有 scope/return 定向测试覆盖当前表单、一次性 token、checkbox/chip、拼接请求、取消、快照恢复、上传限制、键盘/IME；需要时新增专门 UI fixture，而不是只靠字符串断言。
- 按 `AGENTS.md` 用 `127.0.0.1`、隔离合成数据、一次性本机会话链接在 Codex 内置浏览器验证 **2～3 条关键闭环**：① 收藏页有词/空词按钮 → `/search` → 唯一限定结果；② 全局搜索勾收藏 + 人物/地点筛选 → 再改词/取消限定 → 不自动全库重搜；③ 搜索结果 viewer 取消收藏 → 过期提示/重搜 → 预览返回与滚动状态。可用时仅保存必要截图到忽略的 `test-results/screenshots/`；无法实测时明确 `BROWSER_PENDING`，不能拿 Node 测试冒充。
- 按 `docs/testing.md` 和 V3.1 §3.2/3.4 判断受影响完整 unit/integration/API/Node 范围；共享搜索调用链、会话版本和跨路线消费方必须覆盖，影响面不明时扩大测试并做独立 DS-H 复核。可复用身份匹配的既有全量基线+本轮增量证据；不得把失败、跳过或组合重跑宣称为一次全仓通过。
- 新增专项 pytest 示例：`uv run --no-sync pytest -q tests/unit/test_p4_favorites_search_scope.py tests/integration/test_p4_favorites_search.py --basetemp=.pytest-tmp/P4-FAVORITES-SEARCH-A --junitxml=test-results/P4-FAVORITES-SEARCH-A-focused.xml`；实际运行先确认用例文件存在，并记录真实命令、退出码与 JUnit。必要 JS 文件 `node --check`、受影响 Ruff/`py_compile`、`git diff --check` 都必须有真实结果。

## 9. 建议实施顺序与停止条件

1. **复核依赖与冲突**：核对最新 HEAD、A 的真实 `favorites.py`、B 是否实施、旧 scope + 时间浏览 + viewer 回调路径；只做本卡范围的变化。
2. **先做收藏布尔字段与唯一权威谓词**：模型 additive、搜索服务 `SearchScope`，三个执行路径接通；先建 `FILTER/TEXT/COMBINED` oracle，证明在检索前做成员限定。
3. **补版本一致性**：首次排名锚定、发布前复核、排名续页、两种 FILTER cursor、候选时间浏览、409 结构与原有非收藏会话隔离；先解决实际版本/游标问题，再接 UI。
4. **接入现有 UI 入口**：收藏页范围按钮、搜索页单一 checkbox/chip、自动一次提交、取消及回退/恢复；复用 viewer、滚动分页及事件代次；不抢做 B 的批量 UI。
5. **做受影响测试、真实浏览器、SQL 路径核对和独立 DS-H 审查**：修复所有本卡必须闭合的 finding，保留真实失败及限制；报告清楚区分“专项功能 PASS”和“未做 30 万/模型/正式发布验收”。

**交付产物（实施阶段）**：必要源码、定向/回归测试、`docs/reports/P4-FAVORITES-SEARCH-A.md`、精简 `docs/development-status.md` 更新；原始 JUnit/JSON/日志/截图留在项目忽略的 `test-results/` / `.tmp/`。不预占 migration，不新建照片合集/智能合集卡，不自动启动后续任务。**必需正确性、版本/恢复、受影响测试、浏览器及独审未完成时只能写 `IMPLEMENTED / VALIDATION PENDING`，不得标 PASS。**


## 10. 2026-10-19 执行收口

- 状态：**IMPLEMENTED / ACCEPTANCE PARTIAL**；实现、160 项受影响 pytest、Ruff/py_compile/Node/diff-check 均通过。最终 JUnit 与浏览器路径、限制、性能边界、Luna High 首轮及跟进复核见 [执行报告](../reports/P4-FAVORITES-SEARCH-A.md)。
- 用户指定的 Luna High 子代理完成完整受影响面独立复核；初审 P2 已修复，跟进无新增 finding。该复核按用户要求记录，不冒称 DS-H。
- 浏览器已验证收藏页交接、FILTER_ONLY/文字查询、viewer 取消收藏后的立即失效、预览返回；高级地点筛选折叠区无法由本轮内置浏览器自动化打开，导致收藏与人物/地点浏览器组合闭环仍 PENDING。未满足卡片 PASS 门槛。
- 性能证据限于合成 SQL 计划检查，无 30 万延迟/容量结论；语义模型未就绪，真实图库未访问。
