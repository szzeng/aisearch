# P4-SEARCH-INTENT-NL-COVERAGE-FIX-A — 自然语言句式覆盖与搜索状态闭环修复

**日期：** 2026-10-10  
**状态：READY / 待实施（仅任务卡设计；未修改或提交代码）**  
**仓库：** `szzeng/aisearch`，立卡参考 GitHub `master` HEAD `6076090284f8f9a146db20fe78aa4731bf9b4c0b`。**执行前必须重新核对 HEAD 和本地工作树；本次不以旧提交覆盖其它工作。**  
**推荐实施：** GPT-6 / High（用户在 Codex 手动选择）；一张任务卡、同一会话先复现，再修复、复测，满足验收即停止。  
**性质：** 已建统一解析框架上的“产品语义覆盖缺口”定向修复，**不是再做一次搜索架构重构**。

## 1. 已核对事实和唯一目标

已实现 `P4-SEARCH-INTENT-UNIFIED-A`，并有已完成的 `FIX1`（同一身份候选去重、选择项身份说明）及 `FIX2`（有验证父 ID 的 GeoNames 同名父子默认范围）。**这两个修复属于既有成果，不重做、不回退。** 最新 `master` 还新增独立待实施卡 `P4-PERSON-SEMANTIC-REPAIR-A`，解决人物相册索引缺失；本卡不碰人物相册向量维护。

用户实际复现：`AI的照片` 可以识别人物合集，`AI里的照片` 不行。代码证实 `search/natural_intent.py::parse_collection_intent()` 的合集上下文及 `search/unified_intent.py::_erase()` 中未对这些连接词形成相同规则。其它确定的待验证缺口：`今年10月` 易被仅识别为全年、`2024年济南的照片` 易丢地点过滤、多个合集的 OR 可能只识别一个、来源“的照片”变体不一致、`同框/一起拍` 的 `all` 关系识别不完整，以及 `web/search.js` 中搜索尚在 loading 时新提交先清空页面、`sendSearch` 随即返回的竞态。

**唯一目标：** 对下面列出的日常表达，保证**识别的硬过滤对象、剩余内容词、解析状态及执行分支正确**；不再出现“虽然返回照片，但根本没有套上用户说的条件”。不承诺改善真实 CLIP/Qwen 的内容识别准确率。

## 2. 开工路径与不可改变的边界

读取 `AGENTS.md → docs/development-status.md → 本卡 → 当前 HEAD/工作树 → 直接相关代码与测试`。参考：

- `src/photo_search/search/{natural_intent,unified_intent,intent_plan,personal_search}.py`
- `src/photo_search/places/{natural_search,choices}.py`
- `src/photo_search/api/{routes,models}.py`
- `src/photo_search/web/{search.js,search.html}`
- `tests/unit/{test_p4_search_intent_unified,test_p4_search_nl_parse,test_p4_search_nl_ui,test_search_place_nl_ui}.py`
- `tests/integration/{test_p4_search_collection,test_p4_place_search_flow}.py`
- 已完成的 `P4-SEARCH-INTENT-UNIFIED-A-FIX1/FIX2` 卡及报告；ADR-0009/0011/0016 与实际触及的 V3.0/V3.1 搜索条款。

**保持：** 加性 `/api/v1/search/place-aware` 为唯一个人自然语言入口；冻结 `POST /api/v1/search` 与上传搜图契约不改；现有 collection ID/token/revision、canonical 人物、地点 OSM 优先/GeoNames 回退、FIX2 有证据的同名父子归并及剩余真实歧义选择不退化；地点人工归属与地点相册成员语义不改；过滤前置于 FTS/Vector，语义 Top5000、纯条件完整 SQLite 分页、去重/游标/版本/来源 availability 行为不改。

不新增依赖、数据库 migration、LLM、分词器、地名包、用户对象别名、向量重建、人物身份改动、持续后台任务或第二套状态框架；不读取/更改真实 `PhotoSearchData`，不启动用户正在运行的服务，不扩展 `P4-UI-SEARCH-C` 视觉改版。

## 3. 修复设计：先明确语义，再写代码

### A. 统一“对象＋范围连接词”的识别与剥离

1. 人物合集以真实已登记名称为前提，支持“X的照片 / X合集里的照片 / X里的照片 / X里面的照片 / X中的照片 / X里面穿裙子的照片 / X里的裙子”。`里、里的、里面、里面的、中、中的、合集里、合集中的` 等**最长连接词优先**，只在明确的范围结构中消费；相同规则同时驱动识别、原文 span 和 `_erase`，禁止仅修改识别正则导致 `content_query` 残留“里面的”。
2. 已登记来源以真实 `source_id` 和名称为前提，支持“X里的照片 / X中的照片 / X的照片 / 来自X的照片”，但不得将“手机拍的照片”“AI风格的照片”等自由内容误识别为来源。来源的名称可以包含“图库/来源/备份”；无证据的简称不得自动关联。
3. 普通内容词不被吃掉：`AI技术照片`、`AI风格的照片`、`AI生成的图片`、`照片里写着AI` 不应仅因存在 AI 合集而硬过滤；没有真实同名对象时维持 `none`＋原文内容。`AI的裙子` 有明确合集上下文时保持既有支持。
4. 同一原句里的所有对象先收集、去重、检查重叠、消歧，再一次提取内容词。继续保留 FIX1 的“同一 canonical 姓名＝别名不产生重复按钮”与 FIX2 的身份约束。禁止全句替换 `里、的、照片` 等词；保留 Unicode 原文偏移，短英文名不得命中单词内部，中文短名不得轻易命中更长普通词。

### B. 组合、地点和时间

1. 时间支持既有绝对年/月/日、季节、去年、上个月；**新增** `今年10月`、`去年8月` 这类相对年份＋月份，使用左闭右开时间边界。小型固定语法下，可直接支持同一年的连续月段 `2024年10月到12月`（预期 `2024-10-01` 至 `2025-01-01`）；如果实施者确认现有时间契约无法安全表达，应明确给 `unsupported`，**不得只执行10月而无提示**。`2024年和2025年` 等不连续 OR 首版不做。
2. 当时间或人物已在前半句确认时，地点解析不得因其不是“句首”而直接丢失 `2024年济南的照片`、`去年济南的照片`。只在显式地点句式／已验证地名父链下识别；继续保护 `济南路上的夜景`、`巴黎风格的房间`，不把它们绑定成城市。必要时在现有地名解析器增加“已确认修饰语上下文”输入，而不是让地名逻辑推断全句任意字词。
3. `张三与李四同框的照片`、`张三和李四一起拍的照片` 应按两个真实人物 `all` 解释，并删除相应关系框架词；`张三或李四的照片` 为 `any`；`张三和李四的照片` 没有同框依据时保留可选择关系。
4. **禁止部分执行不支持的条件**：`AI或同事的照片` / `AI和同事的照片` 表示多合集关系时，首版必须明确 `unsupported`，不能只过滤后一个合集，也不能通过“选择其中一个”伪装执行整个 AND/OR；`济南或上海`、人物排除或跨人群“另一个不同人物同框”等继续明确拒绝而不是默默降级。此处只做必要的风险句式检测，不增加全功能布尔解析器。
5. 若集合、人名、地名或来源同名，优先按现有可信身份消歧，不凭字符相等就归并实体；OSM 优先、GeoNames FIX2 父子规则需有最小回归保护。

### C. 前端在途请求与已提交计划

1. 修复 `submitSearch` 在旧请求 `loading` 期间清空 UI，然后 `sendSearch` 直接返回导致新查询丢失的问题。采用**单调 request generation（推荐）**或取消旧请求＋失效判定；无论成功、错误、消歧响应还是结果页，只有最新有效请求能修改 `intentPlan/placeAware`、结果、计数、提示、游标与快照。允许同一会话快速重新提交，不能为了“防重复”吞掉新搜索。
2. `intentPlan` 是已提交条件，`queryText` 是草稿：用户主动提交新原句时才重新解析；点击取消人物、合集、地点、来源、时间只删该项，其他确认条件保持。**移除含 `all` 的人物条件后不能因服务端仍从旧原句推得 `all` 而返回 409**；但客户端不得自行加入原句不支持的硬过滤条件。
3. 消歧选择、继续分页、详情返回及旧快照严格绑定当次已确认计划。显式条件变化阻止旧游标混用，旧响应不能污染下一次搜索。无效或过期客户端计划应提示重新搜索，不能伪装“0张”。
4. 仅调整现有搜索 JS 状态和必要的纯搜索字段，不改搜索页面视觉层级、入口和上传搜图。

## 4. **必须先落地的自动验收样例**

前提：用隔离合成数据库创建人物合集 `AI`、`同事`（不同 ID）；人物 `张三`、`李四`；地点 `济南`、`上海`（各有可区分的真实选择 key/parent）；来源 `手机备份图库`；固定今天 `2026-10-10`。有些名字与内容描述同字，应验证只执行真实且明确的范围。以下是**预期行为契约**，不是宣称当前已经通过。

| ID | 输入 | 预期结构化范围 / 状态 | `content_query` |
|---|---|---|---|
| C01 | `AI的照片` | `ready`，合集=AI | `None` |
| C02 | `AI里的照片` | `ready`，合集=AI | `None` |
| C03 | `AI里面的照片` | `ready`，合集=AI | `None` |
| C04 | `AI中的照片` | `ready`，合集=AI | `None` |
| C05 | `AI合集里的照片` | `ready`，合集=AI | `None` |
| C06 | `AI的裙子` | `ready`，合集=AI | `裙子` |
| C07 | `AI里的裙子` | `ready`，合集=AI | `裙子` |
| C08 | `AI里面穿裙子的照片` | `ready`，合集=AI | `穿裙子` |
| C09 | `2024年AI里的照片` | `ready`，合集=AI AND 2024全年 | `None` |
| C10 | `AI技术照片` | 不允许自动生成合集范围 | 保留实质“AI技术” |
| C11 | `AI风格的照片` | 不允许自动生成合集范围 | 保留“AI风格”等实质内容 |
| C12 | `AI或同事的照片` | `unsupported`，不执行任一合集的单侧范围 | 不进入检索 |
| C13 | `AI和同事的照片` | `unsupported`，不执行单合集替代交集 | 不进入检索 |
| P01 | `张三的照片` | `ready`，人物=张三 | `None` |
| P02 | `张三或李四的照片` | `ready`，人物两人/`any` | `None` |
| P03 | `张三和李四的合照` | `ready`，人物两人/`all` | `None` |
| P04 | `张三与李四同框的照片` | `ready`，人物两人/`all` | `None` |
| P05 | `张三和李四一起拍的照片` | `ready`，人物两人/`all` | `None` |
| P06 | `张三和李四的照片` | `needs_choice`，任一/同框两种解释 | 保留其它内容 |
| L01 | `济南的照片` | `ready`，地点=济南 | `None` |
| L02 | `2024年济南的照片` | `ready`，地点=济南 AND 2024全年 | `None` |
| L03 | `去年济南的照片` | `ready`，地点=济南 AND 2025全年 | `None` |
| L04 | `2024年张三在济南拍的日落` | `ready`，人物＋时间＋地点 | `日落` |
| L05 | `济南路上的夜景` | 不因“济南”子串形成硬地点范围 | 保留原实质内容 |
| L06 | `济南或上海的照片` | `unsupported`；不得只取济南 | 不进入检索 |
| T01 | `2024年10月的照片` | `ready`，`2024-10-01`～`2024-11-01` | `None` |
| T02 | `今年10月的照片` | `ready`，`2026-10-01`～`2026-11-01` | `None` |
| T03 | `去年8月的照片` | `ready`，`2025-08-01`～`2025-09-01` | `None` |
| T04 | `去年夏天的照片` | `ready`，`2025-06-01`～`2025-09-01` | `None` |
| T05 | `2024年10月到12月的照片` | 连续月份区间；若不支持，明确 `unsupported`，不得变成仅10月 | 不得遗漏后半段 |
| T06 | `2024年或2025年的照片` | `unsupported`，不执行单一年份 | 不进入检索 |
| T07 | `照片上写着2024年` | 不形成拍摄时间过滤 | 原句内容必须保留 |
| S01 | `手机备份图库里的照片` | `ready`，来源=真实 source_id | `None` |
| S02 | `手机备份图库的照片` | `ready`，来源=真实 source_id | `None` |
| S03 | `来自手机备份图库的照片` | `ready`，来源=真实 source_id | `None` |
| S04 | `手机备份图库里的张三照片` | `ready`，来源 AND 人物 | `None` |
| S05 | `手机拍的照片` | 不因“手机”猜测来源 ID | 保留普通查询 |

**注意：** 上述 37 个参数化解析例句可以只放在**一到两个测试函数**中，数据表驱动断言 `state`、准确真实 ID/key、`person_match`、日期边界和剩余内容。测试条数多不代表要建 35 份重复夹具。`T05` 如采用 `unsupported`，须在报告解释支持边界，并在测试中固定该决定；绝不静默单月。

### 真实身份/反例保护（可合并测试函数）

- 同一人物正式名和同值别名：仅一个候选；两个不同人物共享姓名/别名：仍需选择，按钮区分 ID（既有 FIX1）。
- 同名真实 OSM 与 GeoNames：保留已验证 OSM 优先；仅 GeoNames 同名直接父子且父 ID / 活动包证据成立时采用 FIX2 默认范围；否则仍需选择。**不要按名称做 union。**
- 未登记合集/来源、同名缩写误识别、`Ann` 不命中 `Anna`、`照片里的文字` 不被删成空词。
- 同一张照片属于两个合集成员：结果按 `asset_id` 去重；合法空合集为0，不退化全图库。

## 5. **必须真实运行的最小端到端验证**

只使用隔离合成 SQLite、约8～15张合成照片的成员归属、fake encoder/假向量索引及模拟 DOM/延迟 Promise。复用既有夹具，不必拍摄/加载真实照片；下列 **4组 API + 2组 JS** 允许各组内部参数化：

1. **合集纯范围＋内容范围：** `AI里的照片` 返回 AI 范围所有照片、正确总数/小页无重复且**不调用 encoder**；`AI里的裙子` FTS/假 Vector 在 AI 范围内召回，AI 外高相似度资产不能进入；`AI或同事的照片` 不执行任何部分检索。
2. **时间、地点、人物、来源混合：** `2024年张三在济南拍的日落` / `2024年济南的照片` / `今年10月的照片` / `手机备份图库的照片` 的 `filters`、`intent_plan`、结果成员资格与时间左闭右开一致；纯条件路径不需要向量或模型。
3. **可信计划与状态安全：** 同名人物/地点选择后仍保留其它条件与内容；篡改 `intent_plan` 不得偷偷增加来源/人物/合集；不支持的否定、多范围 OR 不得执行；合集 revision/地点包变化后旧游标明确失效，原有正常续页和重搜保留。
4. **既有行为最小保护：** 普通无对象词搜索仍走原内容搜索；纯筛选可继续分页到底，语义最多Top5000；源/人物/地点显式筛选和图库离线口径不改；FIX1/FIX2 关键节点复用原测试。
5. **JS 在途竞态（延迟响应桩）：** A 搜索启动且尚未返回时提交 B，先返回 A 再返回 B，最终 B 必执行、页面只显示 B；重复倒序试一次，旧错误/旧消歧也不能覆盖 B。检查 `loading` 不吞掉新提交。
6. **JS 条件与往返：** `2024年AI里的照片` 先取消时间仍保留 AI；再重新搜索并取消合集仍保留2024年；人物 `all` 的条件取消不会导致服务器409；详情返回、续页、查询重试不恢复已取消条件；旧 session/cursor 不能混用。

**每组只需少量断言，不要把所有变体逐一做 API 或真实浏览器测试**。不重测 30 万、全仓、GPU、OCR、人物索引重建、真实模型或浏览器矩阵。本轮不强制实际浏览器；必须有真正执行 JS 状态变化的合成 DOM/Promise 验证，不能只断言 JS 包含某段代码文本。

## 6. 交给实施模型的执行流程：失败即修，直至核心表通过

1. **先写红灯用例**：优先 C02、C07、L02、T02、S02、P04、C12 和 JS 竞态；在隔离夹具中运行，记录失败时 `original_query / state / candidate 类型及 ID / content_query / resolved filters / execution path`。既有已通过项不要反复重测无关范围。
2. **按根因修复**：优先小型上下文/连接词 helper 与 span 删除统一规则，随后处理时间＋地点上下文、人物关系、来源，最后收敛前端提交 generation。不要把每个句子硬编码为一个单独 `if`，不引入新 parser 框架。
3. **循环验证**：修改后跑同一参数化解析表＋直接影响的 API/JS；失败则定位最小原因继续修复，再跑受影响节点。最终全部必测 PASS 后才做 Ruff/Node/diff-check。若某条涉及既定不支持能力，明确 `unsupported` 并固定断言，不能报“支持”。
4. **遇到冲突停止扩大**：若需要改冻结 `/search`、Schema、成员 SQL、身份/人工归属、模型/向量或无法保证客户端计划安全，则记录技术限制和最小复现，不偷偷扩展本卡。用户明确要求实施者自行测试/修复**本卡范围内的问题**，无需发现一个小错误就请求用户新开任务。

建议 PowerShell（调整到**实际修改的文件/存在的测试节点**，不要执行不存在的路径）：

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
$env:PYTHONPATH = 'src;tests;.'
uv run --no-sync pytest -q `
  tests/unit/test_p4_search_intent_unified.py `
  tests/unit/test_p4_search_nl_parse.py `
  tests/unit/test_search_place_nl_ui.py `
  tests/unit/test_p4_search_nl_ui.py `
  tests/integration/test_p4_search_collection.py `
  tests/integration/test_p4_place_search_flow.py `
  --basetemp=.pytest-tmp/P4-SEARCH-INTENT-NL-COVERAGE-FIX-A `
  --junitxml=test-results/P4-SEARCH-INTENT-NL-COVERAGE-FIX-A.xml
uv run --no-sync ruff check `
  src/photo_search/search/natural_intent.py `
  src/photo_search/search/unified_intent.py `
  src/photo_search/search/personal_search.py `
  src/photo_search/places/natural_search.py `
  src/photo_search/api/routes.py `
  tests/unit/test_p4_search_intent_unified.py `
  tests/unit/test_p4_search_nl_parse.py `
  tests/unit/test_search_place_nl_ui.py `
  tests/integration/test_p4_search_collection.py
node --check src/photo_search/web/search.js
git diff --check
```

如果 JS 行为测试位于独立 Node/harness 测试路径，按现有项目方式运行并记录实际命令及结果；新增的后端测试要纳入上面的定向 JUnit。`pytest --collect-only` 不是 PASS；Windows pytest 临时目录的权限异常记录为环境失败，正常权限复测，不擅自改 ACL。

## 7. 验收、报告与结束

验收不是“有照片返回”而是 **(A)真实对象硬过滤 (B)剩余内容不丢 (C)执行路径正确 (D)实际照片范围正确 (E)前端新旧状态不串** 同时满足。所有核心例句参数化测试和 4 API/2 JS 场景通过、直接受影响旧测试通过、Ruff/Node/diff-check 通过，才算工程实现及定向验证完成；已知旧夹具问题单列，不因历史失败冒称本卡PASS。

交付 `docs/reports/P4-SEARCH-INTENT-NL-COVERAGE-FIX-A.md`，只写：根因分类、决定支持/明确不支持的句式、核心样例的最终结果摘要、实际命令及 JUnit/JS 结果、变更文件、受影响边界与用户少量现场复测方法。更新本卡和 `docs/development-status.md`。

本卡属跨解析/API/前端查询状态的搜索业务改动，按当前 V3.1 §2.4 和 `AGENTS.md` 做一次**独立 DS-H High 全新增受影响面工程复核**（不默认跑全仓、不机械再做 Sol；只有冻结协议/未决高风险问题才按策略升级）。未做独立审查不得写其 PASS；现场个人试用另记 PENDING。

用户现场最少复测：`AI的照片` ↔ `AI里的照片`（相同合集条件）；`AI里的裙子`（合集＋内容）；`今年10月的照片`（正确月份）；`2024年济南的照片`（时间＋地点）；两次快速提交（后一次结果为准）；取消芯片再翻页。现场只使用用户自己选择的真实姓名/地点/来源；实施者不得擅自访问 `PhotoSearchData`。

**提交边界：** 用户已明确要求提交本任务卡；仅提交任务文档与开发状态登记，不代表代码已实现或测试已运行。达到本卡范围即停止，不自动开始人物索引恢复卡、UI视觉卡或其它工程任务。
