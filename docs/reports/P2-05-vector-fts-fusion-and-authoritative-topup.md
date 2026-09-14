# P2-05 — 图文 + FTS 并行召回、RRF 融合、地点候选预过滤与权威补查报告

日期：2026-09-14。状态：**PASS（B 级完整 unit/integration + 确定性检索对照 + 与 P2-03
崩溃窗口联测）**。

本任务只实现主架构 §9.1/§9.2/§9.3/§9.5、§4.5.4、§7.1～§7.4 与 ADR-0003 §7/§8、
ADR-0005 §10/§11 已冻结的语义：通道划分、RRF 公式、并列规则、地点筛选授权与「投影滞后要
权威补查」都不是本任务重新定义的。全部夹具都在 `%TEMP%` 内，没有读取、移动或删除任何真实
图库，没有联网，没有新增依赖，也没有引入真实模型或 GPU。

## 1. 基线与交付边界

| 项 | 实际值 |
|---|---|
| 起始 HEAD | `fdfd13b`（P2-04），开始时 `git status --porcelain` 为空 |
| 前置 | `SUPPORTED_SCHEMA_VERSION = 6`，migration 连续 `0001`～`0006`，`search_documents`/FTS5/`search_projection_queue`、P2-03 `artifacts`/`index_outbox`/`index_entries`/`index_generations`、P2-02 调度/设备资格均已 PASS |
| 新 migration | **无**（本任务不需要；`SUPPORTED_SCHEMA_VERSION` 仍为 6） |
| 新生产模块 | `src/photo_search/search/{fusion,embedding,retrieval,service}.py` |
| 改动生产文件 | `indexing/store.py`（加性 `search`/`search_candidates`）、`scheduler/projection.py`（只读 `pending_projection_summary`）、`catalog/errors.py`（**新增 1 个 extra 码常量**，见 §11.1）、`search/__init__.py`（**仅文档字符串**说明新模块不在此导入） |
| 新增测试 | 4 个文件、**19 个用例**（fusion 6、channels 5、authority 5、topup 3）+ 共享夹具 `tests/p2_search_fixtures.py` |
| 新增 API/页面/前端 | **无**（本任务不实现 `POST /search`、会话或页面；也没有把新模块接入 API 导入图） |

未修改：ADR-0001～0005 与冻结 JSON schema、`0001`～`0006`、五字段 job 身份、lease/attempts/
迟到提交、资产/位置/缺失判定、P2-03A/B 的人工锁/排除/幂等/批量/撤销语义与触发矩阵、P2-04 的
tokenizer 世代/MATCH 策略/`place_text` 来源规则、`space_id`、`places/**`、`geo/**`、
`ingestion/**`、`workers/**`、打包入口、前端资源、`pyproject.toml`、`uv.lock`。

## 2. 通道契约

两条通道都**只**在权威条件之内召回，且各自给出有界初始候选
（`DEFAULT_TOP_K = 200`，请求可用 `top_k` 覆盖，硬上限 `MAX_TOP_K = 1000`）。

| 通道 | 查询 | 排名键 | 说明 |
|---|---|---|---|
| `fts` | `search_documents_fts MATCH <P2-04 expression>` JOIN `assets`，谓词与 `bm25()` 同一条语句 | `(bm25, asset_id)` | 复用 P2-04 的 `build_match`（每个 token 引号转义、绑定参数），未新增第二种匹配规则 |
| `vector` | `LanceVectorIndex.search_candidates`：`where asset_id IN (<块>) AND space_id = '<绑定 space>'` | `(distance, asset_id)` | 候选集 = 绑定 generation 的 **CONFIRMED** `index_entries` ∩ 权威条件；查询文本只编码一次并复用全部块 |

- **距离语义**：锁定 LanceDB 0.38.0 的默认度量是平方 L2（P0-02C 已实测，本次也复验
  `0.0 / 0.02 / 2.0`）。ADR-0003 的基础 processor 声明 `metric=cosine` + `normalization=L2`，
  在 L2 归一化空间里按 L2 距离排序与按余弦相似度排序一致，因此本层只排序、**不重新归一化、
  不把任何距离或 BM25 显示成“准确率/百分比”**。
- **`space_id` 隔离**：每行返回都核对 `generation_id` 与 `space_id`；维度相同但 space 不同的
  generation 不能互相混用（`search()` 在行级复核，`resolve_binding` 在请求级复核）。

## 3. RRF 公式与并列规则（可手算复算）

`score = Σ_i weight_i / (60 + rank_i)`，`rank_i` 从 1 开始，首版权重相等
（`DEFAULT_CHANNEL_WEIGHTS = {"fts": 1.0, "vector": 1.0}`，`RRF_K = 60`）。同一 `asset_id`
在两路命中只出现一次，`matched_by` 如实记录两路名次（例如 `{"fts": 3, "vector": 11}`）。
排序完全确定：**`score` 降序 → 命中通道数降序 → `asset_id` 升序**。

`search.fusion.reciprocal_rank_fusion` 是纯函数（只吃两路排名 + 权重），跨通道求和顺序按通道名
排序固定，因此重复运行按位一致。手算对照（`k=1` 用于制造二进制可精确表示的并列）：

| 用例 | 输入 | 手算 | 断言 |
|---|---|---|---|
| 单路 vs 双路 | `fts=[10,20,30]`, `vector=[20,40]` | 20=`1/62+1/61`；10=`1/61`；40=`1/62`；30=`1/63` | 顺序 `[20,10,40,30]`，`matched_by[20]={"fts":2,"vector":1}` |
| 同分同通道数 | `fts=[11,…]`, `vector=[21,…]` | 11=21=`1/2` | `11` 在 `21` 前（asset_id 兜底） |
| 同分不同通道数 | `17`=`1/8+1/8`，`13`=`1/4` | 均 `0.25` | `17`（2 路）在 `13`（1 路）前 |
| 通道内重复 | `fts=[5,7,5]` | 5=`1/61`，7=`1/62` | 只保留最好名次 |

拒绝：`k < 1`、权重为负/`NaN`/`Inf` → 结构化 `VALIDATION_ERROR`（不是静默忽略）。

## 4. 绑定、拒绝与错误码

查询只有在**显式**给出 `encoder + space_id + generation_id + dimension` 时才启用向量通道
（缺项 → `VALIDATION_ERROR`，并列出缺哪些），并且必须先有 `text` 才能编码。

| 情况 | 结果 |
|---|---|
| 未知 `generation_id` | `PROCESSOR_MISMATCH`，`details.reason=GENERATION_NOT_FOUND` |
| `generation` 的 space 与请求不同 | `SPACE_MISMATCH`（含请求值/实际值） |
| `generation` 的维度与请求不同 | `SPACE_MISMATCH`（含请求值/实际值） |
| `generation` 已 `RETIRED` | `PROCESSOR_MISMATCH`，`details.reason=GENERATION_RETIRED` |
| encoder 声明的 space/维度不符 | `SPACE_MISMATCH`；encoder 不是 embedding processor → `RUNTIME_RESOLUTION_MISMATCH` |
| encoder 返回 0 个/多个向量、维度不符、非有限值 | `RUNTIME_RESOLUTION_MISMATCH` |
| 查询向量维度与绑定不符（引擎层二次防御） | `SPACE_MISMATCH` |

`generation_state` 与 `semantic_complete` 原样返回：**PENDING 的 generation 不宣称新照片已进入
语义检索**，旧 READY generation 仍可显式绑定查询（§7 用例 5）。

## 5. 预过滤与回退策略（禁止「全库 Top-K 后过滤」）

1. **权威条件先行**：`source_id`/`availability` 与 ADR-0005 §10 的 `place_ids`（OR、默认展开
   后代）/`place_state` 组成一个 SQL 谓词（复用 `places.filter.place_condition`），该谓词被**写进
   通道查询本身**，不是对结果的后置过滤。
2. **向量通道按权威候选 ID 受限检索**（architecture §9.3 第 2/3 条、§7.4 的 candidate-ID 路径）：
   以 `index_entries`（CONFIRMED）JOIN 权威谓词做 keyset 分页，每块最多
   `SCOPE_BLOCK_SIZE = 1000` 个 id，逐块调用 `search_candidates`（`asset_id IN` 下推），按
   `(distance, asset_id)` 归并，得到的就是该候选集的**精确** Top-K（块内 top-k 的并集归并等价于
   全局 top-k）。
3. **代价**：向量通道的引擎查询次数 = `ceil(候选数 / 1000)`，不会随库规模线性放大到逐图查询；
   本轮只在 ≤4 资产的夹具上实测 1 个块，规模/延迟未测（§11）。
4. **空候选集**：`search_candidates([])` 直接返回空、连引擎都不打开，因此「没有权威候选」不可能
   退化成「全部」。
5. **非法过滤列**：`LanceVectorIndex.search(where="no_such_column = 1")` 在锁定版本抛
   `ValueError`，本层把它转成结构化 `FILTER_UNSUPPORTED`（0 行），**绝不回退为未过滤检索**；
   同一 generation 不带该过滤的调用仍正常返回 4 行（证明拒绝的是过滤而不是索引）。

## 6. 补查路径：滞后不是“删旧命中”

ADR-0005 §10 要求「只过滤旧结果只能移除失效命中，不能补回刚归类、刚改名或刚改父级的资产」，
因此本任务反向做：**把向量通道暂时无法代表的权威资产显式列出来**。

- 识别：绑定 generation 下缺少 `index_entries(state='CONFIRMED')` 且 `index_generations.state='READY'`
  的权威资产；给出 `entry_state`、`pending_events`（该 asset+generation 的未确认 outbox 数）、
  `generation_state`、`in_ranked_hits`。
- 边界：有文本查询时只列文本命中的资产（否则无法判断相关性）；`TOPUP_LIMIT = 200`，超出时
  `truncated=true`。
- 文本侧滞后沿用 P2-04：查询前 `search.documents.catch_up`（有界批），响应带
  `search_projection = {state, queued_assets, processed_assets, documents, stale_documents,
  tokenizer_version, fts_generation_id, terms, match_expression}`；权威过滤结果不受影响。
- 具名正例（§7 用例 4/5）：**改名/新归类后第一次查询即命中**（FTS 通道 + 权威补查），且该资产
  的向量行未确认时 `matched_by` 只有 `fts`、`semantic_ready=false`。

## 7. 确定性检索对照（本卡核心验收，逐项）

| # | 卡要求 | 实测（命令与断言） |
|---|---|---|
| 1 | 融合数学与并列规则 | `tests/unit/test_p2_search_fusion.py` 6 例：手算 RRF、去重、通道数/`asset_id` 兜底、通道内重复取最好名次、非法权重拒绝、行内不出现相似度百分比 |
| 2 | 确定性通道 + 真 LanceDB | `tests/integration/test_p2_search_channels.py::test_both_channels_rank_deterministically_and_fuse_with_rrf`：假 `EmbeddingAdapter` + 真实 generation（3 个已确认资产）→ FTS 名次 `[sunset]`、向量名次 `[sunset, IMG_0001, 西湖]`、距离 `0.0/0.02/2.0`、融合顺序 `[sunset, IMG_0001, 西湖]`、`matched_by {"fts":1,"vector":1}`；**同一查询两次 `json.dumps(..., sort_keys=True)` 完全相同**（`test_the_same_query_twice_is_byte_identical`），且查询文本只编码一次（`encoder.calls == ["sunset"]`） |
| 3 | 过滤正确性 | `tests/integration/test_p2_search_authority.py`：后代展开（国家→城市→景点，`expanded_place_ids` 正确）、`include_descendants=false` 只取本级、`place_ids` OR、`place_state=unassigned`/`suggested` 与 P2-03A/B 一致、`place_ids + suggested` → `INVALID_PLACE_FILTER`；`place_ids=[空地点]` 时向量通道与 FTS 通道**都是 0 行**（全库最近邻 `sunset` 不可达），非法 `where()` 列结构化失败 |
| 4 | 地点补查正例 | `test_p2_search_topup.py` 与 authority 用例：仅地点筛选（无 GPU、无 binding）立刻返回新归类资产；文本命中 + 向量行未确认时该资产仍出现（FTS 通道）并被 `pending_semantic` 指名（`entry_state=PENDING_REPAIR`、`pending_events=1`、`in_ranked_hits=true`） |
| 5 | space/generation 绑定 | `test_the_vector_binding_is_refused_structurally`（foreign space/错误维度/未知 generation/半绑定/非法 `top_k`）、`test_an_old_ready_generation_stays_usable_while_a_new_one_is_unconfirmed`（旧 READY 仍可查；新 PENDING 的向量通道为空、`reason=GENERATION_NOT_READY`、命中行一律 `semantic_ready=false`） |
| 6 | 既有不变量 | `test_a_query_is_a_pure_read_and_leaves_the_revisions_alone`：两次查询前后 `schema_version=6`、`catalog_filter_revision`、`suggestion_revision`、`index_outbox`/`index_entries`/`search_documents` 行数、每个 `assets.created_at` **逐项不变**；全部既有测试原样通过（未改任何既有断言） |

**D 补充（沿用 P2-03 注入点，不改 outbox 语义）**：
`test_the_crash_window_leaves_the_asset_out_of_semantic_recall_until_replay` 在
`process_next(failpoint="after_lance")` 后确认 3 件事：引擎里已有该行（`count == 1`）、
`index_outbox` 零 CONFIRMED（故查询的向量通道为空、`pending_semantic` 指名该资产）、
重启后重放同一逻辑事件成功且**不产生第二条逻辑向量**，此时该资产才进入向量通道并
`semantic_complete=true`。

## 8. 验收命令与结果

| 命令 | 结果 |
|---|---|
| `uv run --no-sync pytest -q -p no:cacheprovider tests/unit tests/integration` | exit 0；`--collect-only` 计 **618** 例，进度行 1 个 `s` → **617 passed / 1 skipped** |
| 本任务 4 个新文件 | exit 0，**19 passed**（fusion 6、channels 5、authority 5、topup 3） |
| `uv run --no-sync ruff check .` | exit 0，`All checks passed!` |
| `git diff --check` | exit 0，无输出 |
| `uv run --no-sync uv lock --check` | exit 0，`Resolved 95 packages`（依赖未变） |

本环境沿用既有事实：`uv` 默认缓存在沙箱内被拒绝（`os error 5`）、pytest basetemp ACL 同样被拒，
因此验证命令在沙箱外运行（无功能影响）。

## 9. 隐私与访问边界

- 结果结构里没有任何本机绝对路径、坐标或 `request_id`；用例对序列化后的整份响应断言
  `<library root>`、`C:\\`、`latitude`、`request_id` 均不出现（`latitude` 仍只在会话保护的
  `/assets/{id}/geo`）。
- 引擎错误只记录异常**类型名**，不把 LanceDB 的原始消息（可能含表名/列名/路径）带出去。
- 未新增任何 HTTP 端点，因此既有 session/Host/Origin/CSRF 边界不变。

## 10. 本任务**没有**实现（非目标复核）

`POST /search`、请求/响应协议、搜索会话、跨页游标、结果缓存、显式相关性反馈、SearchProfile
切换、任何页面、自然语言条件解析（年份/人物别名/显式筛选语法）都没有实现；服务层只接受已结构化
的条件。以上属于 P2-06/P2-07。

## 11. 未验证项与残留边界

1. **改动清单外的唯一一处**：`catalog/errors.py` 新增一个 extra 码常量
   `FILTER_UNSUPPORTED`（+5 行，未改动任何既有码、未改 `api/errors.py` 的映射）。理由：非法
   `where()` 列必须「结构化失败」，而既有冻结码里没有诚实分类；该文件自身的约定就是「extra codes
   只用于无法诚实归类的情况」。它当前**没有 HTTP 映射**（`status_for_code` 会返回 500），因为没有
   HTTP 端点使用它；P2-06 接入搜索 API 时必须同时补映射并重跑 C 级。
2. **C 级未触发（按卡）**：本任务没有新增依赖、没有改 `workers/**`/打包入口/package-data，也**没有**
   把新模块接入 API 或应用路径（`photo_search.search.__init__` 仍只导出 P2-04 的面，新模块以
   `photo_search.search.service` 等路径显式导入）。因此未做真实打包 smoke；P2-06 一接 API 就必须
   补 C 级构建 + `packaged_api_smoke.py`（含 `photo_search.search.service`/`retrieval` 是否入包）。
3. **规模与延迟未测**：候选分块扫描的代价是 `ceil(候选数/1000)` 次引擎查询，本轮只在 ≤4 资产、
   1 个块的夹具上实测；`MAX_VECTOR_BLOCKS = 1000`（约 100 万候选）是安全阀，触发时只把
   `vector_scan.truncated` 报为 true（向量通道不完整），**没有**在该规模上验证。
4. **质量未测**：没有真实模型、没有人工标注 holdout，因此不声称任何召回率/语义质量；BM25 与距离
   只作名次证据。
5. **归一化责任在 processor**：本层不重新归一化查询向量，也不校验其 L2 范数；若某个 space 的
   向量未归一化，排序仍是「按该引擎距离」，但不是余弦等价（需新 processor/space 才可改变语义）。
6. **混入外来 space 的拒绝路径**未构造夹具：`search()` 的行级 `space_id` 复核已实现，但造出
   「一个 generation 的表里混入外来 space 行」需要绕过生产路径（`upsert()` 会先拒绝），本任务没有
   真实注入；既有 P2-03 `count()` 的同类检查保持有效。
7. **中查询并发变更**：命中在返回前用最新 SQLite 权威谓词复核，`catalog_filter_revision` 变化会
   被报成 `state=FILTER_REVISION_CHANGED`；本任务**不**自动重跑召回补新命中，也没有并发写者用例
   （会话/过期语义按卡留给 P2-06）。
8. **纯筛选查询的边界**：无文本查询直接给「有界权威列表（≤1000 个 id，`truncated` 如实标注）」，
   `top_k` 只约束两条召回通道；分页/游标属于 P2-06。
9. `pending_semantic` 只能说明「该权威候选（有文本时要求文本命中）在绑定 generation 下没有已确认
   投射」，**不能**说明它语义上是否相似（没有向量就无法知道）。
10. `pending_projection_summary` 目前只是只读函数（tools/P2-06 可用），未接入
    `/api/v1/capabilities`；`text_column_matches` 每次请求多两条有界的列限定 MATCH。
11. 词法与匹配策略、`place_text` 来源规则、人工锁/排除/撤销语义、`space_id`、五字段 job 身份、
    lease/attempts/迟到提交、缺失判定均沿用前置任务，本轮未重新定义也未重新验收。
12. 夹具是合成 JPEG + 确定性假适配器；真实 Chinese-CLIP 查询塔、真实图库语义质量、GPU 路径不在
    本卡前置，未测。

## 12. 结论与交接

P2-05 的「双通道并行召回（有界 Top-K、确定性名次）+ 纯函数 RRF 融合 + 权威 SQLite 候选约束
（candidate-ID 受限检索，禁止全库 Top-K 后过滤）+ 投影滞后识别与权威补查 + 显式 space/generation
绑定」闭环已按 **B 级 + 确定性检索对照（含 P2-03 崩溃窗口联测）** 真实验收，因此标记 PASS。
`schema_version` 仍为 6，migration、ADR、冻结 JSON schema、依赖、`places/**`、`ingestion/**`、
`workers/**`、打包入口与前端均未改动。

下一任务是 **P2-06 — 搜索 API、会话/游标、`profile_id`/`generation_id` +
`catalog_filter_revision` 绑定与显式反馈**，推荐 **DS-H（DeepSeek V4.1 Flash / 高推理）**，
验收 **B（原子切换/恢复加 D，接入 API 后补 C）**；任务卡见
[docs/tasks/P2-06.md](../tasks/P2-06.md)。
