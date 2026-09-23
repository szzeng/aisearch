# P4-01C 独立 DS-H 全量工程主审报告

被审对象：**P4-01C — 按裁决实现瓶颈修复（块大小/标量索引、批量维护、修复审计、过滤谓词计划）**，
交付物为未提交的 `src/photo_search/search/retrieval.py`、`src/photo_search/indexing/store.py`、
两个同步测试断言、新增专项用例与实现报告。

本报告是 `docs/tasks/P4-01C.md` 审查路由要求的**独立 DS-H / DeepSeek V4.1 Flash / 高推理全量工程主审**。
主审原则：**不引用被审报告的任何结论作为证据**。下表每一行都来自本会话真实执行的命令与产物文件；
被审报告的数字只用于对比是否一致。

**结论：`ENGINEERING REVIEW PASS / SOL FOCUSED REVIEW PENDING`**（见 §9）。

---

## 1. 观察基线与被审交付物

- Git 基线：`cd84bcb`（P4-01B 补充分析收口）。主审开始时工作树：**6 modified + 2 untracked**，
  与 P4-01C 报告 §1/§4 的自述一致（另有受限令牌无法枚举的 `.pytest-tmp-p308c-sol-20260922a/`，
  `git status` 打印 `Permission denied` 警告；本主审未改任何 ACL）。
- 生产面改动（允许范围内）：`retrieval.py`（`SCOPE_BLOCK_SIZE` 1 000 → 5 000；`source`/`person`
  谓词改为集合成员判断）、`store.py`（`upsert_batch`、`process_batch`、定点 `has()`、
  `ensure_scalar_index*`）。
- 测试/工具面：`tests/integration/test_p4_01c_retrieval_blocks.py`（新，5 用例）、
  `tests/unit/test_p3_person_search_scope.py` 与 `tests/integration/test_p4_01_index_scale.py`（同步断言）、
  `tools/diagnostics/ann_scale_benchmark.py`（探针复用）。
- 文档面：`docs/reports/P4-01C-bottleneck-implementation.md`、`docs/development-status.md`。
- 环境事实（非产品缺陷）：本机受限令牌下 `--basetemp` / `%TEMP%` 写入必然得到
  `WinError 5` 环境失败；本主审的两次完整测试运行分别按「先建父目录」与「正常 Windows 权限路由」处置，
  未改代码、未改 ACL。

## 2. 独立复现的命令与结果

| 项目 | 命令（工作目录 `D:\SZ\project\aisearch`） | 主审独立结果 |
|---|---|---|
| B 级全量 | `uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration --basetemp=.pytest-basetemp-p401c-review2/full --junitxml=test-results/p4-01c-review-full.xml` | **1644 / 0 / 0 / 48**，exit 0，202.449 s（`test-results/p4-01c-review-full.xml`） |
| 本卡专项 | 同上，目标 `tests/integration/test_p4_01c_retrieval_blocks.py` | **5 / 0 / 0**，exit 0（`test-results/p4-01c-review-focused.xml`） |
| Lint / 空白 / 锁 | `uv run --no-sync ruff check .`；`git diff --check`；`uv lock --check` | 全部 exit 0；Ruff `All checks passed!`（受限令牌下仍打印一条既有 `os error 5` 遍历警告）；lock `Resolved 96 packages` |
| 禁止面差分 | `git diff --stat cd84bcb -- src/photo_search/catalog/migrations docs/decisions src/photo_search/taken_time src/photo_search/web src/photo_search/api src/photo_search/persons src/photo_search/face PhotoSearch.spec tools/packaging pyproject.toml uv.lock` | **为空**（§6） |
| 基线源码树 | `git archive cd84bcb src` 解出到一次性目录，作为「修复前」真代码 | 主审自建 before/after 对照（§3.1、§5.1） |

48 skip 全部为既有环境跳过（缺 buffalo_l 人脸权重 23、缺 PP-OCRv4 权重 19、缺 P0-03 模型 5、
GPU 路径 1）；无失败、无错误。

## 3. A 段检索面逐条复核

### 3.1 不变量 1：同一查询的 top-k 逐条不变（主审用基线真代码对照）

主审把 `cd84bcb` 的 `src` 树整棵解出并强制置于 `sys.path[0]`，用**同一段探针**（同一夹具、
同一查询向量 `synthetic_vectors(seed+71)`、同一 `top_k=200`）在**同一物理索引状态**（仅 `asset_id`
标量索引）下跑四个组合，并把结果与 numpy 暴力 top-20 对照：

| 组合 | 代码树 | 块大小 | blocks | 扫描资产 | top-200 摘要 | 暴力 top-20 |
|---|---|---:|---:|---:|---|---|
| 修复前 | `cd84bcb` | 1 000（旧默认） | 301 | 300 005 | `900de11d88a6d9691e7f911881a931ca` | 20/20 |
| 修复前 | `cd84bcb` | 5 000 | 61 | 300 005 | `900de11d88a6d9691e7f911881a931ca` | 20/20 |
| 修复后 | 被审工作树 | 5 000（新默认） | 61 | 300 005 | `900de11d88a6d9691e7f911881a931ca` | 20/20 |
| 修复后 | 被审工作树 | 1 000 | 301 | 300 005 | `900de11d88a6d9691e7f911881a931ca` | 20/20 |

四行的 top-20 `asset_id` 序列逐条相同（`249405, 129632, 177409, 288895, …`），且与暴力 top-20 集合
完全相等 ⇒ **不变量 1 成立**；块大小确实只是访问路径（块数 301 ↔ 61）。
过滤档位同样对照：`narrow`（地点）在两侧都是 `b0ea7c6f0ea5c644a2568c15cb466b6f`、6 块、30 000 候选、20/20。
证据：`test-results/p4-01c-review-walk-before.json`、`-walk-after.json`。

主审另按任务卡「必须保持的不变量 1」的口径，复核了实现报告 §2.1 的 12 行块大小扫描
（3 种索引状态 × 4 种块大小，300 005 行真表）：**在两种精确状态（无索引 / 仅标量索引）下，
12 行里的 8 行 top-20 与基线和暴力结果完全一致**；ANN 列另见 §7-R2（近似，不一致是 B1 的既定结论）。

### 3.2 谓词等价的独立证据

1. **合成边界夹具（本卡专项用例，主审重跑）**：含 `file_locations.asset_id IS NULL`、
   同一资产重复位置、`MANUAL_UNASSIGNED`、`active=1` 人工排除、`SUGGESTED` 与 `MANUAL` 链路，
   旧相关 `EXISTS` 与新 `asset_id IN (SELECT …)` 逐条给出相同资产集合（`source`、`availability`、
   `person any/all` 及三者 AND）。
2. **真实 30 万数据摘要相等**：同一 `ORDER BY asset_id LIMIT 2000` 分页
   - `half`（来源 + 地点后代，正是被改写的 `source` 谓词）两侧同为
     `c76226b46381a3d05fbfbdf81e66b4c9`；
   - `narrow`（未改动的谓词，作为对照）两侧同为 `83c716354a85538f1ea15dbcd2d66e27`。
3. **`thin`（地点 + 人物 all）**：修复前该档在 30 万上连「限定 `a.asset_id <= 40000` 的计数」
   都在 **240 s** 内不返回；修复后同一限定计数 **266**（698.9 ms），全域计数 **2 000**（1 108 ms）。
   `thin` 的真实数据集合等价未能直接测量（修复前跑不完），由 §3.2.1 的边界夹具等价 + `faces.asset_id`
   `NOT NULL` 的语义论证 + §3.2.2 的 `half` 真实摘要相等共同支撑，本主审接受该边界并在 §8 记明。
4. **成员口径唯一**：`person_condition` 仍逐字复用 `persons/members.py` 的 `MEMBER_PREDICATE`
   （主审读码 + `tests/unit/test_p3_person_search_scope.py` 的 `all` 形状断言），未复制第二份成员定义。
5. **参数顺序未变**：`scope_condition` 的绑定顺序仍是 `source → availability → person → capture → place`，
   主审逐字段核对；`IN` 与 `EXISTS` 在 NULL 上同为「不匹配」，无 `NOT IN` 引入的语义变化
   （`availability` 分支保持 `NOT EXISTS`，未被改写）。

### 3.3 过滤分布与 §12.4 第 32 条

主审**不依赖查询计划**再算一次候选数：把每个条件单独执行（`source` 独立、`person` 按人独立、
`place` 独立、`capture` 独立），在 Python 里按 `any`/`all` 组合后与生产 `scope_condition` 的
`COUNT(*)` 对照：

| 档位 | 条件 | 独立集合计数 | 生产 SQL 计数 | 一致 |
|---|---|---:|---:|:--:|
| wide | capture | 215 998 | 215 998 | ✔ |
| half | source=1 + place | 114 000 | 114 000 | ✔ |
| narrow | place | 30 000 | 30 000 | ✔ |
| thin | person all [1,2] + place | 2 000 | 2 000 | ✔ |

即：候选数不是某个计划恰好算出来的产物。生产侧同档位计数耗时（本主审 `p4-01c-review-probe-after.json`）：
wide 426 ms、half 657 ms、thin 1 108 ms（修复前 half/thin 均 `TIMEOUT>120 s`）；`narrow` 本次未重测计数，
被审设计探针记录为 97.2 ms。
§12.4 第 32 条组合校验（地点 + 人物 + 时间 + 语义 AND）在 `test-results/p4-01c-ann-scale.json`
中确有 `scoped_candidates = hand_computed_count = authority_total = 6397`、`sql_agrees=true`、
`walk_hits_outside_authority=[]`、`fts_hits_outside_authority=[]`、
`projection_lag.in_topup=true / returned_as_hit=false`，主审逐字段核对成立。

## 4. B 段维护面逐条复核（读码 + 专项用例 + 真实夹具）

1. **领取/确认顺序**：`process_batch` 先用一次短写事务把 `PENDING`/`IN_FLIGHT` 一起置为
   `IN_FLIGHT`（`attempts+1`、清 `last_error_code`），写引擎后**在一个写事务里逐事件确认**
   （`index_entries` 确认条件与 `_make_generation_ready` 都与 `process_next` 相同）。主审逐行比对
   `process_next` 与 `process_batch`，未发现被放宽的判定。
2. **不产生部分确认**：整批写在同一个 `with catalog.write()` 内，任一事件确认失败即整批回滚，
   异常分支把该批 `IN_FLIGHT` 行全部改回 `PENDING` 并写错误码；`_InjectedCrash(after_lance)` 原样抛出，
   停在「引擎已写、未确认」这一文档化崩溃窗口。专项用例断言 25 事件 → **一次**引擎写
   （`batches == [25]`）、全部 CONFIRMED、全部 `semantic_ready`、Lance 行数不变；崩溃窗口后
   `{CONFIRMED: 25, IN_FLIGHT: 5}` → 重放 `{CONFIRMED: 30}`、65 行 65 个唯一资产（零逻辑重复）。
3. **批量键唯一性（主审新增分析）**：一次 `merge_insert` 内不可能出现重复 `record_id`。
   `index_outbox` 有 `UNIQUE(space_id, asset_id, generation_id, projection_revision, event_type)`；
   同一资产的较低 `projection_revision` 会被 `_is_superseded`（`MAX(revision) > revision`）判为
   只确认不写；`DELETE_VECTOR` 目前**没有任何生产者**（全仓库仅 `0003` 的 CHECK 词表出现该字面量），
   因此同一 (generation, asset) 在一批里最多产生一条待写记录。若将来新增 `DELETE_VECTOR` 生产者，
   批内必须先去重（记入 §7-R6）。
4. **`has()` 定点化**：改为 `count_rows(filter="asset_id = <int>")`，字面量由 `int()` 生成，
   `_table` 调用在 try 之外（表缺失/维度不符仍然抛），只有引擎拒绝过滤器时才回退到全量物化扫描；
   `audit_and_schedule_repairs` 的判定逻辑与语义未变（`entry.state == 'CONFIRMED' and confirmed`）。
5. **标量索引是可选维护产物**：`ensure_scalar_index` 四态（`no_table`/`created`/`present`/`unavailable`）
   且**从不抛错**；只用 `BTree` 配置，不使用 `replace=True`；唯一调用点 `ensure_scalar_indexes`
   只被 `process_all` 调用（主审 grep 全仓确认）。缺失或不可用时读路径只是「正确但慢」，
   不静默返回不完整结果 ⇒ 不变量 4 成立。且主审实测：无索引状态（§5.2 第一列）与有索引状态的
   top-20 完全相同。
6. **`authoritative_page` 未改**：仍返回精确 `total`，字段集/语义未变（D1/D3 未实现）；
   `SearchFiltersEchoV2`、`query_hash`、游标/摘要、错误码集合、RRF/BM25/距离公式均未触碰。
7. **`upsert_batch` 语义**：逐条做 space/dimension 校验（与旧 `upsert` 相同），一次
   `merge_insert("record_id").when_matched_update_all().when_not_matched_insert_all()` 写整批；
   空批返回 0；`upsert` 现在就是「单元素批」，重放幂等性不变。

## 5. 主审额外独立探针（被审报告未覆盖）

### 5.1 用基线真代码做的 before/after 对照

`git archive cd84bcb src` 解出基线树，同一探针、同一夹具、同一索引状态跑修复前/后（§3.1 表）。
这比「同一份新代码换块大小」更强：它证明**被替换的实现本身**给出相同的 top-k，而不是只有新实现自比。

### 5.2 12 行块大小扫描的完整独立重跑（3 种索引状态 × 4 种块大小）

| 块大小 | 无任何索引 | + `asset_id` 标量索引 | + ANN 向量索引（IVF_PQ 585/64） |
|---:|---:|---:|---:|
| 1 000 | 88 018 ms（301 块） | 9 590 ms | 11 204 ms |
| 5 000 | 21 816 ms（61 块） | 4 639 ms | 4 842 ms |
| 25 000 | 9 097 ms（13 块） | 3 998 ms | 3 369 ms |
| 100 000 | 7 425 ms（4 块） | 4 003 ms | 3 231 ms |

- 两种**精确**状态下的 8 行 top-20 全部为 `900de11d…`，与基线、与暴力结果一致。
- ANN 列的 top-20 与精确结果重叠只有 **4/20、7/20、1/20、2/20**（ANN 建索引 21.1～22.2 s）——
  这正是 **B1（不把 ANN 作为主路径）** 的独立确认，也说明报告 §2.1 表格标题对 ANN 列不成立（§7-R2）。
- 量级与被审报告一致（被审：85 449 / 20 524 / 7 973 / 6 688 无索引；7 964 / 3 993 / 3 305 / 3 343 标量；
  9 359 / 3 612 / 2 467 / 2 527 ANN）。本会话有 CPU 争用（与全量套件并行），绝对值略高，
  相对顺序与拐点完全一致。

### 5.3 索引状态必须被显式钉住（工具行为发现）

主审第一次跑「对照」时得到的是**近似**结果：该夹具的索引状态会跨运行保留，
而 `drop_ann_index` 现在只删 `vector_idx`（标量索引保留），`block_size_sweep` 的三个相位是**顺序**执行的
（相位 2 建标量索引，相位 3 又把它删掉去建 ANN）。因此「无索引/有标量/有 ANN」三列只在
该次运行的相位顺序下成立。主审为此在探针里显式设置并记录索引状态（`test-results/p4-01c-review-sweep.json`
每行都带 `index_state`），并建议下一次触碰该工具时把索引状态显式记录进 JSON（§7-R4）。

## 6. ADR / 任务卡 / 冻结面一致性

- **允许范围**：改动只落在 `src/photo_search/search/retrieval.py`、`src/photo_search/indexing/store.py`、
  两个既有测试的同步断言、新增专项用例、诊断工具探针与报告/状态文件——均在任务卡「允许修改范围」内。
- **禁止面零改动**：`git diff --stat cd84bcb --`（`0001`～`0015` 与 `SUPPORTED_SCHEMA_VERSION`
  所在目录、`docs/decisions` 全部 ADR 与伴随物、`taken_time`、`web/**`、`api/**`、`persons/**`、
  `face/**`、`PhotoSearch.spec`、`tools/packaging/**`、`pyproject.toml`、`uv.lock`）**为空**；
  `schema_version` 仍 15；未引入 A3、未引入任何 ANN 方案、未新增依赖。
- **主架构 §9.3**：候选块扫描仍是「正确但较慢」的路径（第 3 条），没有变成「全库 Top-K 后过滤」
  （第 4 条）；块大小只改访问路径，`vector_channel` 仍是「权威先行 → 候选块 → 引擎排序 → 合并 → 复核」。
- **主架构 §9.2/§9.5**：Top 200/RRF/距离公式未动；`AUTHORITY_PAGE_LIMIT`、
  `authoritative_page` 的 `total/truncated` 语义未动。
- **ADR-0009 §3.1/§3.2/§3.3**：照片级口径（至少一张有效成员脸）、`all`/`any` 只收窄候选、
  与其他条件一律 AND 全部保持；**成员谓词仍只有一份定义**（复用 `persons/members.py`）。
  唯一差异是 SQL 形状由 ADR 正文示例的 `EXISTS` 变成等价的集合成员判断（§7-R3）。
- **ADR-0005 §10**：地点/来源先得权威候选、禁止全库 Top-K 后过滤、投影滞后补查三条路径未变
  （`place_condition`、`pending_projection_page` 未改）。
- **ADR-0011**：`capture_condition` 未改，`taken_time` 语义与 13 键 echo 未改。
- **P2-03 提交协议**：`index_outbox` 逐事件幂等、`semantic_ready`/`index_generations` 状态机、
  `after_lance` 崩溃窗口均保持（§4）。

## 7. 主审发现

**阻断级：无。** 以下 6 条为非阻断发现，其中 R1/R2 是**主审已就地更正的事实性表述错误**
（只改误述，不改任何数字与结论），R3～R6 是留给后续任务的处理项。

### R1（主审已更正）报告 §2.3 把 `process_next` 的成绩归给批处理

报告 §2.3 原文把「重放 5 个事件（30 万夹具）」的 `50.55 → 38.18 ms/事件` 标注为
「1 次批写 + 首次标量索引创建」。主审读码确认这一行来自 `ann_scale_benchmark.maintenance_report`，
它用的是 `while … coordinator.process_next(...)`（**逐事件**路径，5 次引擎写，本卡未改其结构，
`upsert` 只是变成单元素批），既没有批写、也不会在那里创建标量索引；38.18 ms/事件与
「逐行 `merge_insert` 43.69 ms/行」是同一量级，恰好印证它是逐条写。
更正后该行只保留真实可比数字，并注明批处理收益的证据在专项用例（25 事件 = 1 次引擎写）与
`process_all` 路径，而不是这条 30 万逐事件重放。

### R2（主审已更正）报告 §2.1/§3.1 的「12 组合 top-20 全同」对 ANN 列不成立

`same_top20_as_first` 是**同一相位内**四行互比（工具 `block_size_sweep.phase()` 的语义），
不是「与精确基线相同」。被审报告自己的 JSON 里 ANN 相位 top-20 首项是 `139974`，而精确相位是 `249405`；
主审重跑得到 ANN 列与暴力结果重叠仅 4/20、7/20、1/20、2/20。已把 §2.1 表格标题与 §3.1 第 1 条
限定为「两种精确索引状态下的每一行与基线逐条相同；ANN 列只保证该列内部一致，且与精确结果不同
—— 这正是 B1 不采用 ANN 的依据」。这条更正只让报告与它自己的证据一致。

### R3（P3，非阻断，ADR 形状差异需在下次触碰 ADR 时记录）

ADR-0009 §3.1 正文以相关 `EXISTS` 为例说明照片级口径；实现现在是等价的
`asset_id IN (SELECT f.asset_id …)`。**语义未变**（成员谓词仍是唯一一份；`faces.asset_id` `NOT NULL`、
`file_locations.asset_id` 可 NULL 时两种写法同样不匹配；边界夹具与 `half` 真实数据摘要都相等），
但契约正文与代码形状出现差异。本卡明确禁止改 ADR，实现侧没有改是正确处置；建议在下一张触碰
ADR-0009/检索协议的卡里，把「等价集合成员形式」作为注释写进 ADR（不改变冻结语义）。
**不影响本卡通过。**

### R4（P3，非阻断，诊断工具的可重复性）

`--mode design` 的三相位是顺序且共享同一张表：相位 2 建 `asset_id` 标量索引，相位 3 又把它删掉建 ANN，
而 `drop_ann_index` 现在只删 `vector_idx`。于是「无任何索引」列的成立取决于这次运行恰好从干净状态开始
（被审的 300 000 档确实是这种状态，主审已逐行核对 `indices_before`/`dropped`）。
建议下一次触碰该工具时：把每相位开始/结束的索引名写进 JSON（主审本次就是在自己的探针里这么做的），
或提供 `--index-state` 以显式固定。**不影响本卡通过。**

### R5（P3，非阻断，A2 收益的生产兑现点）

A2 的 `asset_id` 标量索引只在维护侧（`process_all` → `ensure_scalar_indexes`）创建。
主审 grep 全 `src/`：目前**没有任何生产模块**调用 `IndexCoordinator.process_all` / `process_next`
（只有 `places/revision.py` 用 `schedule_projection_refresh`，`scheduler/projection.py` 只读计数，
`search/service.py`/`retrieval.py` 只读向量表）。这是 P2-03 起就存在的接线状态，**不是本卡引入的缺陷**，
但它决定「30 万单次搜索 3.5～4 s（有标量索引）」是否会在真实运行中出现：
在接线之前，真实运行只能拿到 A1 的 **112 s → 19.5 s**（无索引）这一截收益。
请把「语义 outbox 的生产维护入口」列为 **P4-02 正式容量验证的前置项**（P4-02 若只测基准脚本，
会把未接线误读成已达成）。**不影响本卡通过。**

### R6（P4，信息项，前瞻）

批内「同一 (generation, asset) 只有一条待写记录」今天由 `index_outbox` 的 UNIQUE 键 +
`_is_superseded` + 「没有 `DELETE_VECTOR` 生产者」共同保证（§4.3）。若将来新增删除向的 outbox 生产者，
`process_batch` 必须在此之前按 `record_id` 去重，否则一次 `merge_insert` 会拿到重复键。
建议在实现 `DELETE_VECTOR` 的卡里把这条写成前置断言/用例。

## 8. 未验证项（继承实现报告，主审未推翻，仍不得当 PASS）

1. **真模型向量**：本机无 pinned 权重，全部数字来自确定性合成向量（clustered 为主）；
   真实 Chinese-CLIP 空间上的块合并/召回尚未复核。
2. **ANN 质量与 §12.3 的 0.95**：本构建内 30 万档 ANN（IVF_PQ/IVF_FLAT/IVF_SQ，含 585 与 16 分区）
   Recall@20 仍只有 0.08～0.15 量级（主审本次实测 ANN 列与精确 top-20 重叠 1～7/20），
   `nprobes`/`ef_search` 未暴露；索引策略按 B1 保持不变，该项仍未验证。
3. **§12.2 性能目标**：30 万单次搜索在无标量索引下 19.5 s、有标量索引下约 3.5～4 s，
   距「API P95 ≤ 1 s」仍远；本卡未声称达成，属 P4-02 容量验证范围。
4. **A3（无过滤时一次全局查询）未实现**：未获授权，仍是可选优化。
5. **多世代混批注入**：`process_batch` 按 generation 分组各写一次，只在代码层覆盖，未做专项注入。
6. **GTX 1660 6 GB 实机**：BLOCKED（本机无该卡）。
7. **`thin` 档真实数据集合等价**：只能间接证明（见 §3.2.3）。
8. 主审未做：真实 `--mode scale` 全量重跑（30 万档 1 000 随机 + 50 边界查询集），
   而以「12 行块大小扫描 + 四档过滤 + 集合计数交叉 + 基线真代码对照」覆盖了同一批结论；
   被审的 `p4-01c-ann-scale.json` 由主审逐字段核对成立。

## 9. 结论

- **P4-01C：`ENGINEERING REVIEW PASS / SOL FOCUSED REVIEW PENDING`。**
  判定依据：任务卡 §「必测验收（B + D）」与 §「必须保持的不变量」1～5 全部由本主审独立复现或独立证明
  （B 全量 1644/0/0/48、专项 5/0/0、Ruff/diff-check/lock 干净、禁止面零改动、
  基线真代码 before/after top-k 逐条相同、块大小只是访问路径、谓词等价含 NULL 与人工状态、
  批处理不产生部分确认且崩溃窗口不变、标量索引缺失有确定性回退），且**无阻断级发现**。
- **Sol high 聚焦终审必选且尚未进行**：本卡改了 `IndexCoordinator` 的写路径（V3.1 §11
  「幂等键、提交顺序、outbox」）与检索参数，按任务卡审查路由，只有 **C2 / GPT-5.6 Sol / high**
  聚焦终审通过后才能标 **P4-01C PASS** 并生成 **P4-02**。本阶段**不标总任务 PASS、不生成 P4-02**。
- **V3.1 §2.2 六类条件逐条核对**：无「跨模块异常而无法定根因」（两条引擎级异常一条已被本卡的谓词
  改写消除、一条是 ANN 建索引工具侧现象且本卡未触碰）；transaction/revision/undo 无矛盾
  （批处理的领取/确认/回退与逐事件路径逐行等价）；测试与 ADR 无冲突（R3 是形状注释差异，语义一致）；
  未出现第二/第三处回归；不存在多状态机耦合的长链推演；无待 Sol 回答的大范围反查问题。
  ⇒ **本主审不推荐 DS-MAX**，按由 C2 做聚焦终审的既定路径继续。
- 两条环境/引擎级异常仍建议按 §2.2 分流 **C1**：同表连续 `create_index(replace=True)` 无进展
  （本卡的生产代码已避开 `replace=True`，主审本次也未复现该现象）、以及 `COUNT(*)`/相关 `EXISTS`
  的查询计划退化（后者已被本卡改写消除，机制层面仍属引擎/计划问题）。

## 10. 本主审产出的证据文件

| 文件 | 内容 |
|---|---|
| `test-results/p4-01c-review-full.xml` | B 级全量 1644 / 0 / 0 / 48（202.449 s） |
| `test-results/p4-01c-review-focused.xml` | 本卡专项 5 / 0 / 0 |
| `test-results/p4-01c-review-walk-before.json` | 基线 `cd84bcb` 真代码：全库/窄档候选遍历 top-200 摘要与暴力对照 |
| `test-results/p4-01c-review-walk-after.json` | 被审工作树：同一探针、同一索引状态 |
| `test-results/p4-01c-review-sweep.json` | 12 行块大小扫描（显式索引状态）+ 每行暴力 top-20 |
| `test-results/p4-01c-review-probe-before.json` | 基线真代码：`half`/`thin` 计数超时、`half` 分页摘要 |
| `test-results/p4-01c-review-probe-after.json` | 被审工作树：四档计数/分页耗时与摘要 |
| `test-results/p4-01c-review-probe-equiv.json` / `-equiv-before.json` | `thin` 受限计数（266 vs 超时）、`narrow` 分页摘要两侧相同 |
| `test-results/p4-01c-review-sets.json` | 独立集合计数交叉（四档全 `agrees=true`） |

一次性材料（`.p4-01c-review-scratch/`：基线源码树 + 探针脚本）已由主审清理；
`.pytest-basetemp-p401c-review*/` 是 pytest 的一次性 basetemp，由 `.gitignore` 覆盖（`.pytest-basetemp-*/`），
不进入版本库。两者都不含交付物。

## 11. 主审对交付物的最小更正（只改误述，不改数字/结论）

| 文件 | 位置 | 更正 |
|---|---|---|
| `docs/reports/P4-01C-bottleneck-implementation.md` | §2.1 表格标题 | 「结果每行 top-20 与基线逐条相同 ⇒ 纯访问路径」→ 限定为两种**精确**索引状态；ANN 列只保证列内一致且与精确结果不同（B1 依据） |
| `docs/reports/P4-01C-bottleneck-implementation.md` | §2.3 第 5 行 | 去掉「（1 次批写 + 首次标量索引创建）」，改为「同一 `process_next` 逐事件路径（本卡未改结构；批处理收益见专项用例的 `batches == [25]`）」 |
| `docs/reports/P4-01C-bottleneck-implementation.md` | §3.1 第 1 条 | 与 §2.1 标题同口径的限定，避免读者以为 ANN 也复现基线 top-20 |

除上表外，主审未改动被审交付物的任何数字、结论或状态行；报告的状态仍停在
`IMPLEMENTATION + ACCEPTANCE COMPLETE`，由本报告给出 `ENGINEERING REVIEW PASS / SOL FOCUSED REVIEW PENDING`。

## 12. 后续记录：聚焦终审结果与主审最小修复（2026-09-23，同日）

本报告的 `PASS` 是**主审阶段**结论。随后由 **C2 / GPT-5.6 Sol / high** 做的聚焦终审为
**`SOL FOCUSED REVIEW FAIL`**（[终审报告](P4-01C-sol-focused-review.md)），C 段（SQL 谓词等价）PASS，
A/B 段给出三条本主审阶段**未覆盖**的真实最小反例，并按 V3.1 §11 交回本主审修复：

1. **A1**：`process_next` 可重领 `IN_FLIGHT`（无 claim token），第二个协调器在批写中途重领并确认一条后，
   批的确认报 `INDEX_SUBMISSION_FAILED` → 本主审「批内不会混合确认」的判断在**可交错**前提下不成立；
2. **B1**：`search_candidates` 让引擎按 `top_k` 先截断、再在 Python 排序，**完全并列距离**时块大小会改变
   返回的 `asset_id` 序列 → 本主审 §3.1 的「块大小只是访问路径」在并列档位不成立（主审的 30 万合成夹具
   没有并列距离，专项用例也没有）；
3. **B2**：`ensure_scalar_index` 的 `list_indices()` 在 try 之外，目录被拒时直接抛出并中断 `process_all` →
   本主审 §4.5「从不抛错」的核对只覆盖了 `create_index` 与过滤器两条拒绝路径。

三条已按最小范围修复（只改 `src/photo_search/indexing/store.py` 与新用例），复现/修复/验证记录见
[实现报告 §7](P4-01C-bottleneck-implementation.md)：三条新用例在临时回退修复后 **3/3 失败**，修复后专项
**8/0/0**、终审受影响测试面 **42/0/0**、全量 **1647/0/0/48**，30 万重测与修复前同量级且两种精确索引状态下
的 top-20 逐条相同。

**对主审结论的修正**：本报告的 `ENGINEERING REVIEW PASS` 应理解为「主审阶段未发现阻断级问题」，
其中 §3.1（不变量 1 的覆盖范围）与 §4.5（拒绝即报告的覆盖范围）的**证据边界**过宽，已由上述修复补齐；
P4-01C 的 PASS 仍取决于 Sol 短复审。此记录不改动本报告上文的任何数字与结论。

**第二轮短复审（同日后续）**：同一份 Sol 报告的 §7 判定 **`SOL SHORT RE-REVIEW FAIL`** —— B1（并列距离）
与 B2（索引目录拒绝）**PASS**，A1 仍阻断并给出更强反例「旧写者最后落盘」：甲的 `_is_superseded` 与引擎写
之间被插入较新 revision 后，甲仍把旧 revision 写进 Lance 并跳过确认 ⇒ SQLite 说 revision 2 已确认而派生行
停在 revision 1，检索会发布这条旧向量。根因是本主审 §4.1 所检查的「领取与确认是同一事务」并不等于
「**检查与写入**在同一事务」。第二轮修复把「权威复读 → supersede 检查 → 引擎写 → 确认」收进同一个
`BEGIN IMMEDIATE` 事务（`process_next` 与 `process_batch` 同一纪律，领取仍单独提交以保留崩溃窗口），
复现与验证见 [实现报告 §8](P4-01C-bottleneck-implementation.md)。这再次说明本报告 §4 的核对范围
（当时按「逐事件语义等价」比对）没有覆盖「跨事务的检查→写入窗口」，该缺口已由第二轮修复闭合。
