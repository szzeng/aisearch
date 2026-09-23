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
