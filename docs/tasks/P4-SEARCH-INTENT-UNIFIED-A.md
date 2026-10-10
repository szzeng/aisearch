# P4-SEARCH-INTENT-UNIFIED-A — 个人相册自然语言搜索统一控制层

**状态：READY / 待实施（仅任务卡已登记；产品代码未修改、测试未执行）**  
**立卡日期：2026-10-10**  
**仓库：** `szzeng/aisearch`，`master`；立卡参考 HEAD `d3595416110a2eff9995fbe91ab16bfb31bc3799`（含最新 OSM 优先地点解析修复；开工仍须重新检查 HEAD 与工作树）。  
**推荐实施：GPT-6 High**，一张主卡、同一会话完成设计落地、必要修复和定向测试。**性质：搜索业务控制层重构，不是搜索引擎或数据库重写。**

## 1. 唯一目标和完成边界

将分阶段删除原文的「合集、人物、地点、拍摄时间」解析整理为**一次候选收集、一次冲突决策、一次剩余内容提取**的统一自然语言搜索，并增加「已登记图库来源」这一可选类型。个人搜索入口形成唯一已生效查询计划；服务端先验证，再复用既有 SQLite / FTS / Vector / RRF 搜索。

完成后用户可以用「2024年张三在济南拍的日落」「同事合集里的裙子」「手机备份图库里张三的照片」等表达搜索，系统显示真正生效的条件；歧义先选择，不会误删内容词。取消任意标签只取消该项，续页、详情返回和失败重试不使旧条件复活。

**本卡明确不做：** 新 LLM/分词框架、任意布尔查询、多个合集交并差、地点数据包重建、地点成员范围全面 SQL 化、重写 FTS/Vector/RRF、Schema/migration、照片归属/人工锁/撤销/人脸/OCR/向量索引变更、搜索页面视觉重做。不得读取或修改真实 `PhotoSearchData`、真实照片、运行中的个人服务。

## 2. 开工前核对及必须保留的契约

按 `AGENTS.md → docs/development-status.md → 本卡 → 当前 Git HEAD/工作树 → 直接调用链` 阅读；保留并行修改与未跟踪文件。只查阅架构 V3.0 §9.1～9.5、§10 搜索相关条款，执行策略 V3.1 §2.2/2.4、§3.2/3.4、§9.3，及实际触及的 ADR-0009（人物/组合过滤）、ADR-0011（拍摄时间）、ADR-0016（CN/来源地点身份）。若实际版本条款变化，以仓库最新规则为准并在报告说明。

重点核对：

- `src/photo_search/api/{routes,models}.py`：`post_place_aware_search`、`PlaceAwareSearchRequestModel`、`_filter_only_search_page`、`_place_album_search_page`。
- `src/photo_search/search/{natural_intent,service,retrieval,sessions}.py`：合集、人物/日期解析，`SearchScope`，候选前置过滤、会话摘要。
- `src/photo_search/places/{natural_search,choices,albums,cn_facets}.py`，`src/photo_search/persons/{collections,collection_sql}.py`，`catalog/store.py` 中来源读取。
- `src/photo_search/web/{search.js,search-place-entry.js,search.html}`：`intentPlan`、`placeAware`、高级筛选、异步响应、条件标签、快照及续页。
- `docs/tasks/P4-SEARCH-COLLECTION-A.md`、`P4-SEARCH-NL-UX-A.md`、`P4-SEARCH-PLACE-NL-A.md`，及尚待实施的 `P4-UI-SEARCH-C.md`。

保持冻结的 `POST /api/v1/search` 和图片上传搜索协议完全不变；**只允许在现有加性 `POST /api/v1/search/place-aware` 内扩展计划校验、响应选择状态和内部服务**。不新增第二次网络往返的 `/search/interpret` 必选接口。已实现的合集 `collection_id + instance_token + revision` 校验、同一照片 `asset_id` 去重、普通人物 `MAX_PERSON_IDS=100`、纯条件完整 SQLite 分页、语义 Top5000、FTS/Vector 范围内召回、已有计数/排序与错误码均保持。

## 3. 建议实现结构（以最小真实改动为准）

1. **`search/unified_intent.py`（新增）**：同一完整原句收集五类候选，原文字区间及上下文判断，跨类型/重叠冲突，最终一次性计算 `content_query`。复用已有各类底层识别 helper，不能长期保留新旧两套对外解析逻辑。
2. **`search/intent_plan.py`（新增）**：定义有界可校验的客户端计划与服务端已验证计划；包含计划版本、原文、已确认条件、用户选择来源、待选择项和错误状态。服务端计划只能由权威对象重新验证后生成。
3. **`search/personal_search.py`（新增）**：PersonalSearchCoordinator，执行计划校验、范围冲突处理、现有 SQL/语义路径选择、版本/游标复核与返回映射。把路由中的业务分支及必要薄 helper 移出，避免路由与协调器循环依赖；**不复制一套 SQL 成员口径**。
4. **原模块**：`natural_intent.py` / `places/natural_search.py` 只保留被复用的时间、人物、地点识别/资料 helper，必要时小幅调整；`routes.py` 最终仅做请求适配和响应，不再重复解析。
5. **前端**：`search.js` 收敛成一个 `committedPlan`（真正生效条件）和一个可编辑 `draftText`。保留现有 DOM/页面样式，不引入新状态框架。

不用强制为接口再加一层抽象；若三个模块中有一个只是转发，可在报告说明后精简，目标是**唯一状态、唯一控制路径**，不是凑文件数量。

## 4. 五类意图与原文处理

- **人物合集 `collection`**：只匹配真实 `person_collections`，用 ID/实例/修订标识；保留 NFC/空白/casefold 现有语义。只在明确合集上下文中成为过滤。首版**至多一个**合集；100 人上限不得作用于合集成员。
- **人物 `person`**：真实有效 canonical 人物名/别名；最终由现有 canonical 解析，允许明确 `any/all`。不把普通子词或长姓名中的短姓名直接当独立对象。
- **地点 `place`**：仅活动包和已存在的真实来源候选，保留 `kind + key + source/selection_ref + dataset/package revision + membership_mode`。**区分**有效地点归属筛选（`place_ids/place_state`）与地点相册成员（GPS 附近城市、CN 行政区、人工归属等），不能凭同名合并或替换成员资格。遵守现有已验证 OSM/GeoNames/CN 映射及消歧策略。**同时保留 P4-PLACE-UNIFIED-A-FIX1 的同名地点优先级**：同一命中存在有效 OSM 候选时优先 OSM，无 OSM 才回退 GeoNames；已验证父链约束优先，不同 OSM 真实对象同名仍需选择，不得在重构后重新无条件显示跨源重复候选。
- **拍摄时间 `time`**：沿用左闭右开、不附加时区的拍摄时间区间；包括合法的四位年份（适当支持 1900～2099 老照片）、年月日、季节、去年/今年等，相对时间在首次搜索冻结。首版至多一个连续时间范围；两段独立 OR 时间不静默只取一段。
- **图库来源 `source`**：从 `source_roots` 轻量读取已登记 `display_name`，仅在「某图库/来源/备份里的」等明确语境中形成 `source_id`。同名来源要求选择，改名不改变 ID；不能按“手机拍摄”“相机照片”等内容描述臆测来源；来源离线不改变既有 `availability` 含义。

**候选格式**至少包括类型、真实对象 ID/Key、原文 `start/end`、原始命中文字、上下文判据及 `confirmed / needs_choice / unsupported / none` 状态。规范化映射必须能准确回溯原文 Unicode 字符边界；英文避免单词内部匹配，中文避免更长名称/道路名的误截断。

**先决策再删词：** 五种识别器读取原始完整句子，不能按阶段逐次 `replace`；相同/重叠区间先做跨类型决策。只有**已确认且互不重叠**的对象片段和明确的查询框架词可以剔除；其余 OCR/视觉描述保留，最终仅构造一次 `content_query`。不能全局清除“照片”“在”“的”“2024年”等任意文本。

## 5. 语义关系、冲突与失败策略

- `confirmed`：明确类型和身份，进入硬过滤。
- `needs_choice`：同名人物/合集/地点/来源或不确定 `any/all`，响应待选择选项；**不执行可能错误的部分搜索**。选一次后使用已确认 plan，不能再靠原文猜一次。
- `unsupported`：用户明确要求“排除张三”、多个地点 OR、两个不同时间范围 OR、复杂跨合集关系，但现有执行器不能准确满足；阻止错误范围执行，提示改写或用明确筛选。只识别有限稳定句式，不开发全量否定语法。
- `none`：没有可信结构化条件，维持普通内容检索，不把无法归类的视觉/OCR 描述误解释为硬过滤。

首版关系：`张三或李四` → `any`；`张三和李四的合照/同框` → `all`；`张三和李四的照片` → 明确解释并提供 `any/all` 选择（不得仅凭“和”强制 `all`）。`张三与同事合集的合照` 不能用简单 AND 冒充“另一个不同的人同框”。

自然语言地点相册范围与手工 `place_ids/place_state` 同时存在时，按现有受控 AND 交集，不悄悄覆盖；自动识别条件与用户显式设置的同类型条件不一致时请求确认，不采用静默优先级。不同类型默认取 AND。**空合集、合法但没有成员的地点返回空集合，不退化为全图库；失效身份给明确 stale / not-found，不能当空条件处理。**

## 6. SearchIntentPlan 与版本/信任边界

计划采用有界明确字段（Pydantic/现有等价验证方式均可），至少有：`plan_version`、`original_query`、`content_query`、已确认合集 `(id, token, revision)`、canonical 人物 ID 和 `person_match`、地点 `(kind,key,source,member_mode,package/revisions)`、拍摄时间区间、来源 ID、条件来源（`auto / explicit_choice / manual`）、`needs_choice/unsupported` 及小型候选集。原文字区间仅为本次解析/诊断，**不作为客户端可决定 SQL 的证据**。上界服从现有查询文本 256 字及原过滤限制；候选条数/嵌套对象也须有界，超出提示缩小范围，不能截掉歧义后自动使用第一条。

客户端 `intent_plan` **不是可信执行指令**：后端重新验证所有对象真实存在、canonical/合集实例/修订、地点来源与活动包/成员语义、来源有效 ID、日期区间，以及计划与实际执行条件的一致性。仅将验证后的 `ValidatedSearchPlan` 编译到已有 `SearchRequest`、`SearchScope` 和地点相册成员读取；不拼客户端任意 SQL、成员 ID、字段名。

首屏执行前固定必要版本，结果发布前核验；续页沿用首次冻结的解析计划、时间边界、范围/成员身份和过滤摘要，**不重新自然语言解析**。续页仍进行必要的当前版本与成员资格核验：不能为了省时取消已有地点成员重算/哈希或会话安全检查。版本变化拒绝旧游标并提示重新搜索；不要用新的 plan 偷偷继续旧排序。计划版本不兼容的旧 sessionStorage 快照可展示旧结果，但新续页应清楚要求重新搜索，不静默猜测迁移。

客户端编辑框文字可以与已提交筛选不同：取消 chip 只编辑 `committedPlan`，不重解旧 `original_query`；用户主动提交新的完整草稿时才重新解析。计划来源冲突要可见，不能由隐藏高级筛选悄悄覆盖。请求序号/过期响应保护：旧响应、旧消歧和旧失败不能覆盖新一次搜索；搜索失败保留当前草稿及明确的最新状态，不复活先前已取消条件。

## 7. 保留现有三种执行路径

1. **纯条件（无实质内容、无地点相册范围）：** `_filter_only_search_page` 或等价原实现；SQLite 完整去重计数＋`asset_id` keyset 分页，逐页 `page_size+1`，不调用编码器/向量，不受语义 Top5000 截断。
2. **纯地点相册条件：** `_place_album_search_page` 或等价原实现；地点相册成员资格与浏览一致，保留 CN `members_sql()` 和旧 GeoNames/GPS/人工成员口径，和合集、人物、时间、来源取受控交集。
3. **含内容词：** 原 `search_request → search_assets → FTS / Vector / RRF`，所有有效范围必须在召回前施加；不先全库 Top-K 再过滤；保持原 Top5000、统计、排序、session/revision 保护。

只做同一请求内地点候选/来源数据的合理复用，避免 `place_choices.choices()` 有参和无参无谓地反复加载；开发期可简单记录解析、地点准备、实际搜索三段耗时。**不承诺未经测量的性能提升，不在本卡实施统一地点 SQL 化、全量缓存或修改成员归属。**

## 8. 最小 UI 范围

- 单一已生效 plan 渲染合集/人物/地点/拍摄时间/来源 chip；同名选择统一使用现有区域，展示类型＋地点上级/来源＋必要区别，不增加新页面。
- 用户取消一项 → 其它已确认条件保持 → 新搜索；搜索框同步展示可编辑的剩余内容/状态，不让用户误以为原始语句仍完整生效。
- 重新提交草稿 → 清理失效的选择和旧游标；快速提交仅最新响应能更新结果。
- 续页使用首次已确认计划；详情返回恢复条件、当前结果与滚动。保留既有纯条件“只存当前页”的内存/快照限制。
- 明确区分“无照片”“无法解析限制”“模型不可用”“条件/游标已过期”；不自动扩大范围。

**与 `P4-UI-SEARCH-C` 分工：** 本卡修改必要搜索逻辑和状态，后续视觉任务只排版/简化搜索 UI。不得并行分别改 `search.js`，不另建前端解析器。若 UI 规划文件需要变更，仅加依赖说明，不顺便实施视觉改版。

## 9. 实施顺序与停止点

- **A：统一候选与计划。** 完成 `unified_intent`、有界 plan、冲突/原文 span 决策；先跑少量解析定向用例。
- **B：个人协调层与执行接线。** 将现有三个出口接到同一验证计划，保持权威 scope、错误、完整分页及版本校验；完成后跑核心 API 小夹具。
- **C：前端单一已生效状态。** 完成选择/取消/重搜/异步防旧写回/续页/详情返回；跑最小 DOM 及受影响静态检查。

三个工作包属于**同一张任务**，不是三个等待重新授权的开发卡。若发现非本卡可控的地点成员 SQL 重构、冻结 `POST /search` 修改、migration、全量新语法要求，**停止扩大范围**，报告缺口，保持已完成代码可验证，不自行加做。

## 10. 必要定向测试（个人项目，拒绝测试膨胀）

隔离的小 SQLite 数据、合成地点/合集/人物和 fake encoder/向量；**不调用真实 GPU，不访问 `PhotoSearchData`**。优先复用现有测试；新增测试文件可为 `tests/unit/test_p4_search_intent_unified.py`、`tests/integration/test_p4_search_intent_unified.py`（确有必要才建立），将几种场景合并到少数参数化测试中。

**最低验证范围：**

| 类别 | 最低场景（不要求组合矩阵） |
| --- | --- |
| 解析约 12～16 句 | `AI的裙子`、`AI技术照片`、合集/人物/地点同名，`张三和李四的合照`、`张三和李四的照片`、`张三或李四`、`照片上写着2024年`、`张三在上海拍的日落`、`去年夏天的海边`、来源重名/误命中、否定/多地点、短英文名及重叠姓名、合法旧年份 |
| 后端约 4～5 组 | 五类条件映射/AND、合照去重和空范围、纯条件完整计数/跨页、地点相册与搜索成员一致、FTS/假 Vector 范围内召回、修改合集/地点包/人物身份后的旧游标拒绝（允许在组合用例中完成） |
| 前端约 2 组 | 消歧选择/取消 chip/重搜/旧响应乱序；续页冻结相对时间、详情返回、失败后旧状态不复活 |
| 性能轻核查 | 查看一次请求地点候选是否重复加载；仅记简单解析/候选准备时间或查询次数，不做大库基准 |

**建议执行命令**（若将用例补在现有文件而非新建，按实际测试文件替换不存在的两个路径；必须记录实际命令和 JUnit）：

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
uv run --no-sync pytest -q `
  tests/unit/test_p4_search_nl_parse.py `
  tests/unit/test_p4_search_nl_ui.py `
  tests/unit/test_search_place_nl_ui.py `
  tests/integration/test_p4_search_collection.py `
  tests/unit/test_p4_search_intent_unified.py `
  tests/integration/test_p4_search_intent_unified.py `
  --basetemp=.pytest-tmp/P4-SEARCH-INTENT-UNIFIED-A `
  --junitxml=test-results/P4-SEARCH-INTENT-UNIFIED-A.xml
# 可根据实际触及面，额外选择 1～2 个地点搜索/会话关键节点，不默认跑整仓。
$PyFiles = @(
  'src/photo_search/api/routes.py',
  'src/photo_search/api/models.py',
  'src/photo_search/search/natural_intent.py',
  'src/photo_search/search/unified_intent.py',
  'src/photo_search/search/intent_plan.py',
  'src/photo_search/search/personal_search.py',
  'src/photo_search/places/natural_search.py',
  'tests/unit/test_p4_search_nl_parse.py',
  'tests/unit/test_p4_search_intent_unified.py',
  'tests/integration/test_p4_search_intent_unified.py'
) | Where-Object { Test-Path $_ }
uv run --no-sync ruff check @PyFiles
node --check src/photo_search/web/search.js
node --check src/photo_search/web/search-place-entry.js
git diff --check
```

Ruff 清单为预期可能改动的文件，执行者还应补入实际改动但未列出的 Python 文件。`node --check` 仅对存在且受影响的 JS 文件执行。**最终 JUnit 必须核对真实通过数量和失败。** 既有报告记载旧地点包 schema 18 上限等 5 个夹具失败；若相关节点失败，先与基线区分，不能冒充本卡通过，也不擅自修改无关夹具。Windows `WinError 5` 视为环境限制按正常权限途径复测，不改 ACL。

不默认跑全仓 pytest、30 万照片、真实浏览器矩阵、真实语义模型、GPU、OCR/人脸重建、打包；若测试揭示本次改动确实触及冻结协议或高风险身份不变量，按现行 V3.1 规定补齐对应受影响证据，不以“个人项目”免除必要复核。

## 11. 完成定义、审查与交付

**代码完成条件：** 五类可识别的真实对象不误命中；冲突有明确选择；不支持的明确限制不错误执行；已确认条件一次生效且可逐项取消；所有执行路径正确使用权威 scope；纯筛选完整分页、语义范围内召回/Top5000/去重不变；旧游标、重命名、成员变化不混入新结果；传统搜索 API/图片搜索不变。

**工程验收：** 本卡必需的定向 JUnit、受影响文件 Ruff/Node/diff-check 均通过；实施者自己完成必要调用链复核。依据 `AGENTS.md` / V3.1 §2.4，本卡涉及过滤、身份和续页，**应由独立 DS-H High 做一次完整新增受影响面工程主审**，只审本卡 diff、相关契约和必要证据，不默认重跑无关全仓。只有真正碰到冻结协议/新高风险未决项时才考虑 Sol High 聚焦终审；DS max 仅按明确升级条件。独立主审未做不得写“独立审查 PASS”，但不阻止明确标注“实现及定向测试完成／待独立审查／待个人试用”。

交付：`docs/reports/P4-SEARCH-INTENT-UNIFIED-A-implementation.md`（短架构决定、实际变更、少量关键场景与测试证据、已知旧失败、未验项），同步本任务文件状态与 `docs/development-status.md`。开工时重新核对 HEAD，不覆盖并行工作；未经用户授权**不自动 push、不运行/修改真实图库、不顺带实现 `P4-UI-SEARCH-C`、不继续下一张任务**。

**用户少量现场试用建议：** `AI的裙子` → `2024年张三在济南拍的日落` → 选择同名地点 → 取消人物 → 翻页 → 查看照片再返回。现场表现与工程定向测试分开记录。