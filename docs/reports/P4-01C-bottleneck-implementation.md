# P4-01C — 瓶颈修复的最小实现（块大小/标量索引、批量维护、修复审计、谓词计划）

任务卡：[docs/tasks/P4-01C.md](../tasks/P4-01C.md)；设计输入：[P4-01B 决策报告](P4-01B-bottleneck-decision.md)。
本卡按用户「按推荐继续」执行 P4-01B §10 的推荐：**A1 + A2**（不加 A3）、**B1**（不引入 ANN 作为主路径）、
**C1 + C2**、**D2 优先**。

> **状态：IMPLEMENTATION + ACCEPTANCE COMPLETE / INDEPENDENT DS-H ENGINEERING REVIEW PENDING。**
> 按任务卡审查路由，`IndexCoordinator` 写路径与检索参数都已改动，**Sol high 聚焦终审必选**；在独立 DS-H
> 全量主审（`docs/reports/P4-01C-dsh-engineering-review.md`）与随后 Sol 终审通过前**不标总任务 PASS**、
> **不生成 P4-02**。

## 1. 基线、改动清单与不变量

起始基线：`cd84bcb`（P4-01B 补充分析收口）。

| 文件 | 改动 | 对应决策 |
|---|---|---|
| `src/photo_search/search/retrieval.py` | `SCOPE_BLOCK_SIZE` 1 000 → 5 000（访问路径参数，结果不变） | A1 |
| `src/photo_search/search/retrieval.py` | `scope_condition` 的 `source` 条件与 `person_condition` 的 `any`/`all` 由相关 `EXISTS` 改为 `asset_id IN (SELECT ...)` | D2 |
| `src/photo_search/indexing/store.py` | 新增 `ASSET_SCALAR_INDEX`/`ensure_scalar_index`（`BTree` 配置、幂等、拒绝即报告）＋ `IndexCoordinator.ensure_scalar_indexes`，只在 `process_all`（维护侧）调用 | A2 |
| `src/photo_search/indexing/store.py` | `has()` 改为 `count_rows(filter="asset_id = ?")` 定点查询，引擎拒绝过滤器时才回退到原 `rows()` 扫描 | C2 |
| `src/photo_search/indexing/store.py` | 新增 `LanceVectorIndex.upsert_batch`（一次 `merge_insert` 多行）与 `IndexCoordinator.process_batch`（一批事件一次批写、一起确认、失败则全部回 `PENDING`），`process_all` 改走批处理 | C1 |
| `tests/integration/test_p4_01c_retrieval_blocks.py` | 新增 5 个用例（块大小=访问路径、标量索引四态、`has()` 快慢路径、批量重放+崩溃窗口、谓词等价） | A1/A2/C1/C2/D2 |
| `tests/unit/test_p3_person_search_scope.py` | 同步 `all` 形式的形状断言（`l.person_id = ?` 计数），语义断言不变 | D2 |
| `tests/integration/test_p4_01_index_scale.py` | 块数断言改用 `retrieval.SCOPE_BLOCK_SIZE` | A1 |

**未改**：`0001`～`0015` migration 与 `SUPPORTED_SCHEMA_VERSION`（仍 15）、ADR-0001～0011 与全部伴随物、
`SearchFiltersEchoV2`（11 + 2）、`search_projection` 键集、`POST /api/v1/search` 信封/`query_hash`/游标/错误码、
RRF/BM25/距离公式、`taken_time`/`capture_desc` 语义、P1～P3 人物/地点信封与人工状态、
`PhotoSearch.spec`、`tools/packaging/**`、`packaged_api_smoke.py`。**未引入 A3**（无过滤时一次全局查询）
与任何 ANN 方案改动（B1）。

## 2. 修复前后对照（30 万夹具，同机同查询集）

证据：`test-results/p4-01c-ann-scale.json`（生产路径）、`test-results/p4-01c-design.json`（EXPLAIN + 块扫描 +
批量写对照）。修复前数据取自 P4-01/P4-01B 的同一夹具与同一查询集。

### 2.1 检索（`vector_channel` 生产遍历，全库无过滤）

| 指标 | 修复前 | 修复后 |
|---|---:|---:|
| 单次搜索 P50（无 ANN 向量索引） | **112 177 ms**（300 块） | **19 489 ms**（60 块，块 5 000） |
| 冷缓存首查 | 110 939 ms | 19 365 ms |
| blocks | 300 | 60 |
| Recall@20 / @50 | 1.000 / 1.000 | **1.000 / 1.000** |
| 引擎扫描 ↔ 暴力 top-20 交叉校验 | 160/160 | **160/160**（`all_equal=true`） |
| 加 `asset_id` 标量索引后同查询 | 3 602 ms（块 5 000） | 3 993 ms（块 5 000，重建后同量级） |
| 索引状态（测量的起点） | — | `indices_before=[]`，工具只删 `vector_idx`，标量索引保留 |

块大小扫描（30 万；**两种精确索引状态**（无索引 / 仅 `asset_id` 标量索引）下的每一行 top-20 与基线逐条相同
⇒ 这两列是纯访问路径；**ANN 列只保证列内一致**，其 top-20 与精确结果不同——这正是 B1 不采用 ANN 的依据
（独立 DS-H 主审更正的表述，数字未变））：

| 块大小 | 无任何索引 | + `asset_id` 标量索引 | + ANN 向量索引 |
|---|---:|---:|---:|
| 1 000 | 85 449 ms（301 块） | 7 964 ms | 9 359 ms |
| **5 000（新默认）** | **20 524 ms**（61 块） | **3 993 ms** | 3 612 ms |
| 25 000 | 7 973 ms | 3 305 ms | 2 467 ms |
| 100 000 | 6 688 ms | 3 343 ms | 2 527 ms |

（对照 P4-01B 修复前的同表：块 1 000 = 92 195 ms、块 5 000 = 19 757 ms、+标量索引 = 3 602 ms。
两轮数值的差异来自夹具行数 300 000 → 300 005 与页缓存状态，量级一致。）

### 2.2 过滤：四档全部跑通，组合校验可完成

| 档位 | 修复前（scope / walk） | 修复后（scope / walk） | 候选数 | blocks |
|---|---|---|---:|---:|
| wide（72%） | 1 082 ms / 8 236 ms | 612 ms / 2 406 ms | 215 998 | 44 |
| **half（来源 + 地点后代）** | **TIMEOUT >60 s / TIMEOUT >60 s** | **1 430 ms / 3 536 ms** | 114 000 | 23 |
| narrow（10%） | 1 031 ms / 1 513 ms | 203 ms / 523 ms | 30 000 | 6 |
| **thin（地点 + 人物 all）** | **TIMEOUT >60 s / TIMEOUT >60 s** | **3 740 ms / 991 ms** | 2 000 | 1 |

`EXPLAIN QUERY PLAN` + 计时（预算 60 s）：

| 语句 | 修复前 | 修复后 |
|---|---:|---:|
| half `COUNT(*)` | **>20 s 中断** | **135 ms** |
| half 分页 | 0.2 ms | 51.9 ms |
| thin `COUNT(*)` | **>20 s 中断** | **888 ms** |
| thin 分页 | **>20 s 中断** | **797 ms** |

**§12.4 第 32 条组合校验**（地点 + 人物 + 时间 + 语义 AND，P4-01 因超时无法测量）：修复后完成，
候选 6 397、手写等价 SQL 计数一致（`sql_agrees=true`）、向量与 FTS 命中均无越权
（`walk_hits_outside_authority=[]`、`fts_hits_outside_authority=[]`），投影滞后补回仍成立
（`in_topup=true` / `returned_as_hit=false`）。

### 2.3 维护：批量写入与定点审计

| 指标 | 修复前 | 修复后 |
|---|---:|---:|
| 逐行 `merge_insert` | 48.87 ms/行 | 43.69 ms/行（同一探针，API 未变） |
| 一次写 500 行 | 0.115 ms/行 | 0.118 ms/行 → **369×** |
| `process_all` 重放 25 个事件 | 25 次引擎写 | **1 次引擎写**（专项测试断言 `batches == [25]`） |
| 重放 5 个事件（30 万夹具，仍是 `process_next` **逐事件**路径） | 50.55 ms/事件 | 38.18 ms/事件（本卡未改该路径；批处理收益的证据是专项用例的 `batches == [25]` 与 `process_all` 路径） |
| `has()` | `rows()` 全量物化，200 资产 13.6 ms/次 ⇒ 审计 `O(N²)` | `count_rows(filter=...)` 定点查询；拒绝过滤器才回退 |
| `asset_id` 标量索引创建 | 不存在 | 60.1 ms，幂等（`created` → `present`） |

## 3. 必须保持的不变量：证据

1. **检索结果不变**：块大小扫描在**两种精确索引状态**下的每一行都与基线逐条相同，且与 numpy 暴力 top-20 全等
   （`same_top20_as_first=true` 的 12 组合是**同一相位内**四行互比，是工具语义；ANN 列的 top-20 与精确结果不同，
   独立主审实测重叠只有 1～7/20，这正是 B1 的依据）。生产遍历 Recall@20/@50 = 1.000，
   引擎扫描与 numpy 暴力 top-20 `160/160` 全等。
2. **逐事件 outbox 幂等与确认顺序不变**：`process_batch` 先在一次写事务里领取（`PENDING`/`IN_FLIGHT` →
   `IN_FLIGHT`），写完引擎后在**一个**事务里逐事件确认；任一步失败则整批回 `PENDING` 并带错误码。
   专项测试：25 事件 → 1 次批写 → 全部 CONFIRMED、全部 `semantic_ready`、Lance 行数不变。
3. **崩溃窗口不变**：`failpoint="after_lance"` 抛错后 outbox 停在 `{CONFIRMED: 25, IN_FLIGHT: 5}`、
   5 个资产 `semantic_ready=false`；重放后 `{CONFIRMED: 30}`，Lance 行数 `65`、唯一资产 `65`（零逻辑重复）。
4. **标量索引是可选维护产物**：`ensure_scalar_index` 返回 `no_table`/`created`/`present`/`unavailable`
   四态且**从不抛错**；测试用引擎拒绝（`RuntimeError`）验证 `unavailable`，且检索仍正确。
5. **`authoritative_page` 计数语义未改**：仍返回精确 `total`；`total`/`truncated` 的含义与字段集不变
   （D1/D3 未实现）。
6. **谓词等价**：新谓词与旧相关 `EXISTS` 形式在同一份含 NULL 位置行、重复位置、`MANUAL_UNASSIGNED`、
   人工排除、`SUGGESTED` 的夹具上逐条给出相同资产集合（专项测试 `test_the_rewritten_predicates_select_the_same_photos`）。

## 4. 验收命令与结果

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path
uv run --no-sync python -m pytest -q -p no:cacheprovider tests/integration/test_p4_01c_retrieval_blocks.py `
  --basetemp=.pytest-basetemp-p401c3/focused --junitxml=test-results/p4-01c-focused.xml
uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration `
  --basetemp=.pytest-basetemp-p401c4/full --junitxml=test-results/p4-01c-full.xml
uv run --no-sync python tools/diagnostics/ann_scale_benchmark.py --mode scale --scale 300000 `
  --reuse-fixture --work-dir "tmp/p4-01-基准 空格" --output test-results/p4-01c-ann-scale.json `
  --query-budget-seconds 60
uv run --no-sync python tools/diagnostics/ann_scale_benchmark.py --mode design --scale 300000 `
  --work-dir "tmp/p4-01-基准 空格" --output test-results/p4-01c-design.json --query-budget-seconds 60
uv run --no-sync ruff check . ; git diff --check ; uv lock --check
```

| 验收 | 结果 |
|---|---|
| 本卡专项 | **5 / 0 / 0**（`test-results/p4-01c-focused.xml`，4.9 s） |
| 全量 B 级 | **1644 / 0 / 0 / 48**（`test-results/p4-01c-full.xml`，最终状态重跑 202.5 s；48 为既有未装权重环境跳过） |
| 30 万档 | 四档过滤 + 组合校验 + 维护全部完成（`test-results/p4-01c-ann-scale.json`） |
| 设计探针 | 8 条 EXPLAIN + 12 条块扫描 + 批量写对照（`test-results/p4-01c-design.json`） |
| Ruff / `git diff --check` / `uv lock --check` | 全部 exit 0 |

## 5. 未验证项（不得当 PASS）

- **独立 DS-H 全量工程主审未做**（本卡停止点），**Sol high 聚焦终审必选且未做**；
  因此本卡只到 `IMPLEMENTATION + ACCEPTANCE COMPLETE`。
- **真模型向量下的效果未测**：本机无 pinned 权重，全部数据是合成向量；真实 Chinese-CLIP 空间上的
  块合并/ANN 召回需要在真夹具上复核。
- **A3 未实现**（未授权）：无过滤时一次全局查询（30 万 374 ms）仍是可选优化。
- **索引策略未调整**（B1）：本构建内 ANN 方案在 30 万上 Recall@20 仅 ~0.15，且 `nprobes`/`ef_search`
  未暴露；`IVF_FLAT`/`IVF_SQ` 只在 20 000 行上优于 `IVF_PQ`。§12.3 的 0.95 仍未验证。
- **批处理的更大幅度**：本卡用 200 行/批；500 行批写的 0.118 ms/行 说明还有空间，但未做端到端对照。
- **`process_batch` 在多世代混批**下的表现只在代码层面覆盖（按 generation 分组各写一次），未做专项注入。
- **GTX 1660 6 GB 实机**：BLOCKED（本机无该卡）。
- 30 万档夹具在维护阶段增长了 5 行（300 000 → 300 005），块数与候选数相应 +1；已在文中标注。

## 6. 下一阶段

1. **另开独立新会话（DS-H / DeepSeek V4.1 Flash / 高推理）做全量工程主审**
   （`docs/reports/P4-01C-dsh-engineering-review.md`）：完整 diff、块合并精确性、标量索引回退、
   `process_batch` 的领取/确认/幂等边界、谓词等价（含 NULL 与人工状态）、测试真实性、
   与 ADR-0005/0009/0011/§9.2/§9.3 的一致性、未验证项。主审通过后状态写
   `ENGINEERING REVIEW PASS / SOL FOCUSED REVIEW PENDING`。
2. **C2 / GPT-5.6 Sol / high 聚焦终审**（§11 幂等/提交顺序 + 检索参数）：只读压缩交接包、
   关键 diff、最小反例与受影响测试；通过后才标 **P4-01C PASS** 并生成 **P4-02**。
3. 两条引擎/环境级异常仍建议转 **C1**：同表连续第三次 `create_index(replace=True)` 无进展、
   `COUNT(*)`/相关 EXISTS 计划退化（后者已由本卡改写消除，但机制本身属查询计划层面）。

## 7. 聚焦终审（C2 / Sol / high）失败边界的最小修复（2026-09-23）

终审报告：[P4-01C-sol-focused-review.md](P4-01C-sol-focused-review.md) —— **`SOL FOCUSED REVIEW FAIL`**，
给出三条真实最小反例（A1 并发重领导致批的假失败、B1 完全并列距离时块大小改变返回序列、B2 索引目录读取被拒
直接抛错）。本节按 V3.1 §11「把最小反例交回 DS-H 做最小修复」执行：**未改** `0001`～`0015`/
`SUPPORTED_SCHEMA_VERSION`、任何 ADR/伴随物、`SearchFiltersEchoV2`/`search_projection`、
`POST /api/v1/search` 信封/`query_hash`/游标/错误码、RRF/BM25/距离公式、打包面，`schema_version` 仍 15。
改动只落在 `src/photo_search/indexing/store.py` 与 `tests/integration/test_p4_01c_retrieval_blocks.py`。

### 7.1 先复现（把三处修复临时回退，三条新用例必须失败）

`test-results/p4-01c-fix-prefix-sensitivity.xml`：**3 tests / 3 failures**。

| 反例 | 修复前实测（同一断言/同一注入） |
|---|---|
| B1 | `assert {(4, 5), (7, 8), (29, 30)} == {(1, 2)}`：块 5 → `(4,5)`、块 8 → `(7,8)`、块 1000/5000 → `(29,30)`，答案由块大小/引擎截断决定 |
| A1 | `CatalogError(INDEX_SUBMISSION_FAILED): outbox claim was lost before confirmation`：批内一条被第二个协调器确认后，本批被报成失败 |
| B2 | `RuntimeError: index metadata unavailable`：`list_indices()` 被拒时直接抛出并中断 `process_all` |

### 7.2 修复（三处最小改动，全部在 `src/photo_search/indexing/store.py`）

1. **B1**：`search_candidates` 改为向引擎请求**每个候选 ID 一行**（`limit=len(ids)`，块上限由过滤条件自身
   界定），并在 `search()` 既有的 `(distance, asset_id)` 全序排序**之后**才截断 `[:limit]` —— 并列距离不再
   由引擎截断决定。同时新增 `CANDIDATE_ROW_COLUMNS` 投影（`_distance` 在列内），候选块不再回传 512 维向量，
   这是「每块返回整块」不付出传输代价的前提。
2. **A1**：`process_batch` 确认阶段遇到**已被其它协调器确认为 `CONFIRMED`** 的事件时跳过，而不是报
   `INDEX_SUBMISSION_FAILED`。`process_next` 可重领 `IN_FLIGHT` 是 P2-03 既有的崩溃恢复路径（协议无
   claim token），两个写者产生同一条派生行、逐事件确认幂等，因此「已经做完」才是诚实结论；本批自身的真实
   失败仍整批回 `PENDING`（回退只覆盖仍为 `IN_FLIGHT` 的行）。`process_batch` 的 docstring 现在写明
   「并发按事件安全、不是靠排他」的前提与边界。
3. **B2**：`ensure_scalar_index` 把 `list_indices()` 一并纳入 try，目录不可读时返回
   `{"state": "unavailable", "reason": ...}`；`_table` 仍在 try 之外（表缺失/维度不符仍是结构化失败）。

### 7.3 修复后的验证

| 项目 | 结果 |
|---|---|
| 本卡专项（8 用例＝原 5 + 新 3） | **8 / 0 / 0**（`test-results/p4-01c-fix-focused.xml`） |
| 终审受影响测试面（专项 + `test_p2_index_submission` + `test_p2_acceptance_recovery` + `test_p3_person_search_scope`） | **42 / 0 / 0**（`test-results/p4-01c-fix-sol-boundaries.xml`） |
| 全量 B 级 | **1647 / 0 / 0 / 48**（`test-results/p4-01c-fix-full.xml`，210.2 s；修复前 1644） |
| Ruff / `git diff --check` / `uv lock --check` | 全部 exit 0 |

30 万档重测（修复后，两种证据互补）：

**① 卡上原命令、重建夹具（300 000 行）、`test-results/p4-01c-fix-ann-scale.json`**：生产遍历
（无 ANN 向量索引、块 5 000）**19 653 ms / 60 块 / 300 000 候选 / Recall@20 = Recall@50 = 1.000**
（修复前 19 489 ms / 60 块 / 1.000）；引擎扫描 ↔ 暴力 top-20 交叉校验 `all_equal=true`；
四档过滤全部跑通（wide 2 139 ms/44 块、half 3 517 ms/23 块、narrow 446 ms/6 块、thin 926 ms/1 块；
候选数仍是 215 998 / 114 000 / 30 000 / 2 000）；§12.4 第 32 条组合校验 6 397 + `sql_agrees=true`；
维护逐行 `merge_insert` 40.6 ms/行、`process_next` 重放 38.83 ms/事件（同一路径，与修复前 35.8/38.18 同量级）。

**② 同夹具块大小扫描复查（`test-results/p4-01c-fix-design.json`）**：

| 块大小 | 无任何索引 | + `asset_id` 标量索引 | + ANN 向量索引 |
|---:|---:|---:|---:|
| 1 000 | 81 963 ms（301 块） | 7 145 ms | 9 091 ms |
| **5 000（新默认）** | **20 252 ms**（61 块） | **4 279 ms** | 3 437 ms |
| 25 000 | 8 467 ms | 3 899 ms | 2 566 ms |
| 100 000 | 7 186 ms | 4 130 ms | 2 548 ms |

与修复前同量级（修复前：85 449 / 20 524 / 7 973 / 6 688；7 964 / 3 993 / 3 305 / 3 343；
9 359 / 3 612 / 2 467 / 2 527），且**两种精确索引状态下的 8 行 top-20 与修复前 JSON 逐条相同**
（`same_list_to_old=true`）：修复没有改变「距离互不相同」查询的结果。
过滤计划（EXPLAIN + 计时）仍全部跑通：wide 81/1.8 ms、half 175/48.8 ms、narrow 90/0.1 ms、thin 822/730 ms。

**语义变化（唯一一处，且是本卡不变量 1 所要求的方向）**：完全并列距离时，结果现在被钉在
`(distance, asset_id)` 全序；修复前它由引擎截断决定、随块大小变化（块 5/8/1000/5000 分别给出
`(4,5)`/`(7,8)`/`(29,30)`/`(29,30)`）。距离互不相同的查询结果逐条不变（上表）。

### 7.4 新增用例

- `test_a_block_of_tied_distances_cannot_be_decided_by_the_engine_cut`（B1：真实 Lance 表、全部向量相同、
  块 5/8/1000/5000 必须给出同一 `(1, 2)`）
- `test_a_concurrent_reclaim_cannot_turn_a_batch_into_a_false_failure`（A1：第二个协调器在批写中途重领并
  确认一条，批必须返回 2、outbox 全部 CONFIRMED、无重复逻辑行）
- `test_a_refused_index_catalogue_never_blocks_the_outbox_drain`（B2：`list_indices` 拒绝时维护仍把 outbox
  排空）+ 既有状态用例内的 `list_indices` 拒绝注入

### 7.5 未验证项与工具面提示（追加）

- **并发面**：只覆盖「两个协调器在同一进程内确定性交错」。真正的多进程/多客户端并发、以及
  「单维护者 or 多维护者 + 逐事件幂等」的最终口径仍未定；协议层没有 claim token 或排他租约。
  R5 的「语义 outbox 生产维护入口」在接线时必须连同这个口径一起裁定并给证据（P4-02 前置项）。
- **工具面**：`--mode scale --reuse-fixture` 在夹具被维护阶段增长过（+5 行）后再跑，会在 `exact_top_k`
  处 `IndexError`（`matrix` 仍是旧行数）。本次 30 万重测按卡上原命令**不带 `--reuse-fixture`**（重建夹具）
  执行；建议 P4-02 前修掉该复用路径或让工具记录/校验 `assets == matrix` 行数（属工具面，不影响产品）。
- 其余未验证项与 §5 相同（真模型向量、ANN §12.3 的 0.95、§12.2 P95、A3、多世代混批注入、GTX 1660；
  并列距离只在合成夹具上验证）。

## 8. 第二轮短复审（Sol）失败边界的最小修复：旧 revision 回写（2026-09-23）

短复审结论见 [P4-01C-sol-focused-review.md](P4-01C-sol-focused-review.md) §7：
**B1/B2 PASS**，但 **A1 仍为阻断**，且给出了比第一轮更严重的反例——「较新 revision 先落盘、旧写者后落盘」：
甲在 `_is_superseded` 检查之后、引擎写入之前被暂停；此间乙处理了较新 revision 并确认；甲恢复后仍把
先前算好的**旧** revision 写进 Lance 并跳过确认 ⇒ SQLite 宣布 revision 2 已确认且 `semantic_ready`，
实际向量行却被回退到 revision 1，`vector_channel` 只按 `index_entries.state='CONFIRMED'` 取候选，
因此会发布这条旧向量行（违反任务卡不变量 2/3 与 P2-03「旧投影事件不得回滚最新状态」）。
Sol 的判断：「仅在确认阶段重查 SQLite 状态不足以阻止已发生的旧 Lance 写；修法需覆盖『旧写者最后落盘』的顺序」。

### 8.1 根因与最小修复

根因不是「跳过已确认事件」这一条，而是**检查与写入不在同一个事务里**：领取（`BEGIN IMMEDIATE`）随即
提交，随后的权威复读、`_is_superseded`、引擎写入、确认分处两个事务，中间窗口可被第二个写者（或
`publish_embedding` 产生的新 revision）插入。

修复（`src/photo_search/indexing/store.py`，`process_next` 与 `process_batch` 同一纪律）：

1. **领取仍单独提交**（保持 `after_lance` 崩溃窗口 = 行为 `IN_FLIGHT` 的既有语义与 P2-03 断言）；
2. **用同一个 `BEGIN IMMEDIATE` 事务覆盖「权威复读 → `_is_superseded` → 引擎写 → 逐事件确认 →
   `_make_generation_ready`」**。SQLite 的写锁在事务开始时即取得，因此：别的协调器无法在窗口内领取/确认；
   `publish_embedding` 这类写者也无法插入更新的 revision ⇒ **旧 revision 不可能最后落盘**；
3. 批路径在同一事务开头按 `state='IN_FLIGHT'` 过滤「仍属于自己的事件」，别人已确认的事件既不写也不确认，
   也不报假失败；本批真实失败仍是整批回滚（随后按既有语义回 `PENDING`）。

代价与边界：写锁在事务期间被持有（批写约 25～150 ms/批、30 万表单行约 40 ms；本机 `busy_timeout = 5 s`），
读路径走 WAL 不受影响。**未改** schema/migration/契约/错误码/排序公式/打包面。

### 8.2 复现（对上一提交 `a457cba` 的旧代码树）

`git archive a457cba src` 解出旧树并强制置于 `sys.path[0]`（探针断言加载的确是被归档的旧包），再用**当前**
测试文件跑两条直接回归：

```text
test_a_stale_projection_revision_can_never_land_after_a_newer_one: FAILED
    assert state["blocked"] is True          # 第二个写者能直接进入检查→写入窗口
test_a_concurrent_reclaim_cannot_turn_a_batch_into_a_false_failure: FAILED
    assert applied == added - 1              # 旧实现把被重领的事件也算成自己应用
```

### 8.3 修复后的验证

| 项目 | 结果 |
|---|---|
| 本卡专项（9 用例＝原 8 + 新 1） | **9 / 0 / 0**（`test-results/p4-01c-fix2-focused.xml`） |
| 受影响测试面（专项 + `test_p2_index_submission` + `test_p2_acceptance_recovery` + `test_p2_search_topup` + `test_p3_person_search_scope`） | **46 / 0 / 0**（`test-results/p4-01c-fix2-boundaries.xml`） |
| 全量 B 级 | **1648 / 0 / 0 / 48**（`test-results/p4-01c-fix2-full.xml`，214.5 s） |
| Ruff / `git diff --check` / `uv lock --check` | 全部 exit 0 |

新增用例 `test_a_stale_projection_revision_can_never_land_after_a_newer_one` 同时固定两件事：
① 在「检查→引擎写」之间，第二个 SQLite 连接（跨进程模型）与本进程发布者都**进不来**；
② 较新 revision 最终一定落在派生行上（`index_entries.confirmation_revision == Lance.projection_revision == 2`、
outbox 全确认、`semantic_ready` 对应最新派生行、存储向量与最新向量同向）。
30 万档重测见 §8.4：与修复前同量级，结果、候选数、召回与状态机全部不变。

### 8.4 30 万档重测（修复后，卡上原命令、重建夹具，`test-results/p4-01c-fix2-ann-scale.json`）

| 指标 | 本轮（第二轮修复后） | 上一轮（第一轮修复后） | 原始 P4-01C |
|---|---:|---:|---:|
| 生产遍历（无 ANN、块 5 000） | **18 080 ms / 60 块** | 19 653 ms / 60 块 | 19 489 ms / 60 块 |
| Recall@20 / @50 | **1.000 / 1.000** | 1.000 / 1.000 | 1.000 / 1.000 |
| 引擎扫描 ↔ 暴力 top-20 | `all_equal=true` | `true` | `160/160` |
| 四档过滤 walk（wide/half/narrow/thin） | 1 878 / 3 295 / 399 / 887 ms | 2 139 / 3 517 / 446 / 926 ms | 2 406 / 3 536 / 523 / 991 ms |
| 候选数（wide/half/narrow/thin） | 215 998 / 114 000 / 30 000 / 2 000 | 同 | 同 |
| §12.4 第 32 条组合校验 | 6 397 + `sql_agrees=true` | 同 | 同 |
| 维护：逐行 `merge_insert` / `process_next` 重放 | 31.25 ms/行 / 28.39 ms/事件 | 40.6 / 38.83 | 35.8 / 38.18 |
| `semantic_ready` / generation | 5/5、READY + active | 同 | 同 |

把引擎写纳入写事务**没有**带来可测的维护或检索回归（同一量级，差异在夹具重建与页缓存噪声内）。
本轮只改写路径，检索面的 12 行块大小扫描沿用 §7.3 的修复后结果（本轮 scale 复测与之相符）。

### 8.5 未验证项（追加）

- 并发保护依赖**同一个 SQLite 目录库的写锁**：这一点对「同进程多线程、跨进程多客户端」都成立
  （后者由文件锁保证，本次用第二个连接做了模型化验证），但**没有**真正的多进程端到端注入；
  协议层仍没有 claim token 或租约，`process_next` 仍允许重领 `IN_FLIGHT`（崩溃恢复语义不变）。
- R5（生产维护入口、单/多维护者口径）仍是 P4-02 前置项；本次修复使「多维护者」在语义上安全，
  但没有把它变成已接线的事实。
- **同模式的其它协调器（本卡范围外，建议后续复核）**：`src/photo_search/face/index.py` 的
  `FaceIndexCoordinator` 使用同样的「短领取事务 → 引擎写 → 独立确认事务」形态，理论上存在同一类
  「检查→写入窗口」。P4-01C 的范围是图像向量索引（`indexing/store.py`），本卡**未改**面索引；
  建议在下一张触碰面索引/维护协议的卡里按本次同一纪律复核，并在 P4-02 的维护口径里明确
  「单维护者 or 多维护者 + 原子写窗口」。
