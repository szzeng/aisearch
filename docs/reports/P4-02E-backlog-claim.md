# P4-02E — F1 语义积压领取有界化

| 项 | 值 |
| --- | --- |
| 任务卡 | [P4-02E](../tasks/P4-02E.md) |
| 基线 | `a0a55c0`（工作树干净；`SUPPORTED_SCHEMA_VERSION = 15`，migration 连续 `0001`～`0015`） |
| 变更面 | `src/photo_search/jobs/queue.py`（`claimable_candidates` + 同文件私有辅助）、新增 `catalog/migrations/0016_jobs_claim_order.sql`、`catalog/schema.py`（15 → 16）、本卡测试/工具/报告/状态；两处机械版本钉扎 |
| 禁止面 | ADR-0001～0011 与 companion、公平公式与三个排序键、`MAX_CANDIDATES_PER_CLAIM`/aging 默认值、`0001`～`0015` 内容、outbox/entry 状态词表、检索语义/错误码信封/`search_projection` 键集、`api/**`、`web/**`、`PhotoSearch.spec`、`tools/packaging/**`、`pyproject.toml`、`uv.lock` 零 diff |
| 执行模型 | DS-H / DeepSeek V4.1 Flash / 高推理（用户手动选择） |
| 结论 | `IMPLEMENTED / VALIDATION COMPLETE（B + D + 迁移）`；**未标 PASS**，独立 DS-H 完整受影响面主审 PENDING，`SOL_NOT_REQUIRED`（未改公平、aging、领取窗口或饥饿上界） |

F1 的根因是**访问路径**而不是语义：`jobs.claimable_candidates` 返回的页面一直是对的，但它每次领取都
物化并排序**整个可运行积压**（`UNION ALL` 两侧都是 `USE TEMP B-TREE FOR ORDER BY`），于是「一次性投递整个
积压」的语义发布是二次成本。本卡把领取改成「**有界候选 + 精确裁决**」，并用 4 320 次页面比较逐项证明它
返回的是同一个页面（`job_id` 序列与 `effective_priority` 值都相同）。

测量还抓到三件任务卡与 P4-02B 主审都未记录的事：**同一条领取事务里还有两条随积压增长的全表/全索引
扫描**（`requeue_due_retries`、`fail_exhausted_leases`），以及**主审参考实现的 `SELECT DISTINCT priority`
形态在本产品（从不 `ANALYZE`）下会退化为 O(积压) 扫描**。三条都属本卡「证明写锁持有时间下降而不是把
成本转移给别的阶段」要求范围，已一并按**只新增索引**的方式修掉（未改任何被禁语义），详见 §2.2、§3.2。

## 1. 起始状态（复核）

| 事实 | 值 |
| --- | --- |
| `git log -1 --oneline` | `a0a55c0 [P4-02D DS-H 有界专项复核] BOUNDLED REVIEW PASS…`（`master...origin/master`，无本地领先） |
| `git status --short` | 空（开工前） |
| 迁移前实际 `schema_version` | 15；`SUPPORTED_SCHEMA_VERSION = 15`，migration 连续 `0001`～`0015` |
| 依赖证据（只读复用） | `test-results/p4-02b-dsh-f1-claim.json`、`tmp/p402b-dsh/f1_claim_probe.py` |
| 语义基线 | ADR-0003 §10 aging/租约/资格门；`MAX_CANDIDATES_PER_CLAIM = 256`、`DEFAULT_AGING_WINDOW_MS = 60_000` 均未改 |

## 2. 瓶颈证据（改动前）

### 2.1 领取本身（F1 主项）

新工具 `tools/diagnostics/claim_cost_probe.py`（把 DS-H 探针收编为常设探针，并补上真实产品入口
`Scheduler.claim_next` 的写事务计时）在真实 migration 建出的 catalog 上测 20 样本：

| 积压 | `claimable_candidates` 中位 | P95 | `Scheduler.claim_next`（写事务）中位 |
| --- | --- | --- | --- |
| 10 000 | 3.591 ms | 3.884 ms | 6.878 ms |
| 50 000 | 19.443 ms | 20.365 ms | 32.048 ms |
| 100 000 | 37.064 ms | 38.917 ms | 62.388 ms |
| 300 000 | 107.22 ms | 113.739 ms | 181.879 ms |

证据：`test-results/p4-02e-claim-cost-before.json`。改前冻结查询的计划逐字为：

```
MERGE (UNION ALL)
  LEFT   SEARCH jobs USING INDEX idx_jobs_schedulable (ANY(state) AND run_after<?)
         USE TEMP B-TREE FOR ORDER BY
  RIGHT  SCAN jobs USING INDEX idx_jobs_schedulable
         USE TEMP B-TREE FOR ORDER BY
```

### 2.2 领取事务里的第二个、第三个扫描（本卡新发现）

只把候选列举改有界**不够**：`Scheduler.claim_next` 在同一个 `BEGIN IMMEDIATE` 里先跑
`jobs.requeue_due_retries` 与 `jobs.fail_exhausted_leases`，这两条在 30 万积压下各要几十毫秒：

| 语句 | 30 万积压下的计划 | 中位耗时 |
| --- | --- | --- |
| `requeue_due_retries` | `SEARCH … idx_jobs_processor_state (ANY(processor_id) AND ANY(state) AND run_after<?)`（实为整索引扫描） | **26–35 ms**（本卡 35.1；主审同机同日复现 26.4） |
| `fail_exhausted_leases` | `SCAN jobs`（整表扫描） | **28–38 ms**（本卡 38.4；主审同机同日复现 27.9） |
| 两者同事务合计 | — | 72 ms（主审复现 54 ms；空写事务 0.007 ms） |

两处耗时按区间记录：同一夹具形状、同一台机器、同日不同 run 的页缓存状态会让它们相差 ±30%，
结论（整表/整索引扫描 → 微秒级）与量级不受影响。主审证据
`test-results/p4-02e-review-prefilter-300k.json`。

最小反例：同一张表、同一 SQL，加 `INDEXED BY idx_jobs_processor_state` 强制走 seek 后
`requeue_due_retries` 从 35.1 ms 降到 **0.020 ms**（计划变为 `(processor_id=? AND state=? AND run_after<?)`），
证明**数据可寻址、是规划器选了扫描**；`fail_exhausted_leases` 加 `(lease_expires_at) WHERE state='RUNNING'`
后从 38.4 ms 降到 0.013 ms。原始输出见 `test-results/p4-02e-claim-index-candidates-before2.json`。

根因（可复现，纯 sqlite3 3.49.1 也复现）：`state = ?` 是**绑定参数**，而该列在索引前导位置上只有
一个不同值（突发时全部是 `QUEUED`），规划器把「等值匹配」估成「匹配全部行」，于是选了看起来更便宜的
整索引扫描。新增索引无法改变这个估计，只有把索引本身限制成「只可能包含该语句能命中的行」才成立。

### 2.3 参考实现的 `DISTINCT` 形态在无统计信息下退化（本卡新发现）

P4-02B 主审验证的参考实现用 `SELECT DISTINCT priority` 枚举「类」。它**依赖 `ANALYZE` 统计**才能
变成「每个不同值一次 seek」：

| 30 万积压下的 `SELECT DISTINCT priority …` | 计划 | 中位 |
| --- | --- | --- |
| 建库后从未 `ANALYZE`（**产品的真实状态**） | `SEARCH … idx_jobs_claim_order (state=?)` | **57.9 ms** |
| 跑过一次 `ANALYZE` 之后 | 同一计划文本 | 0.004 ms |

VDBE 程序对比给出了机制：有统计时多出 `SeekGT`（直接跳到下一个不同值），无统计时退化为逐行遍历
（`tmp/p402e/diag_distinct_opcodes.py` 输出）。产品从不执行 `ANALYZE`，因此本卡改为**显式递增 seek**
的类枚举（`priority > ? … ORDER BY priority ASC LIMIT 1`），无统计信息下 30 万积压 0.014 ms（§4.2）。

## 3. 最小差异

### 3.1 `src/photo_search/jobs/queue.py`（签名、返回列、语义不变）

`claimable_candidates` 保持签名、返回类型与逐项语义，改为：

1. 对两个 runnable side（`QUEUED`/过期 `RUNNING`）沿 `idx_jobs_claim_order` 用
   `priority > ? … LIMIT 1` **递增 walk 出所有类**（每类一次 seek，无统计信息也成立）；
2. 每个 `(side, priority)` 类取最旧 `limit` 行
   （类内 `effective_priority = priority + ((now - created_at) / window)` 对 `created_at` 单调不增，
   故类内全局序就是 `created_at ASC, job_id ASC`；`INDEXED BY idx_jobs_claim_order` 让
   `(created_at, job_id)` 直接由索引满足，无临时 B 树）；
3. 汇总后仍用**原公式与三个原排序键**精确裁决：`effective_priority DESC, created_at ASC, job_id ASC`，
   取前 `limit`。

正确性论证与冻结公式逐字保留（`queue.py` 文档字符串），`UNION` 两侧的状态谓词与
`processor_id IN (...)` 过滤逐字不变，`MAX_CANDIDATES_PER_CLAIM`/aging 默认值未动。

`INDEXED BY` 是刻意选择：产品无统计信息时规划器会改走 `idx_jobs_processor_state` 并退回
`USE TEMP B-TREE FOR ORDER BY`（实测 30 万积压类查询 65.2 ms，见 `tmp/p402e/diag_pinned_plan.py`）。
把访问路径写进语句后，缺索引的构建会**响亮失败**而不是静默退化。

### 3.2 `src/photo_search/catalog/migrations/0016_jobs_claim_order.sql`（只新增索引）

```sql
CREATE INDEX idx_jobs_claim_order ON jobs(state, priority, created_at, job_id);
CREATE INDEX idx_jobs_retry_due ON jobs(run_after) WHERE state = 'RETRY_WAIT';
CREATE INDEX idx_jobs_expired_lease ON jobs(lease_expires_at)
    WHERE state = 'RUNNING' AND max_attempts <= attempts + 1;
```

* 前两条分别消除 §2.1（候选列举）与 §2.2（`requeue_due_retries`）的整索引扫描；
* 第三条是**故意收窄**的部分索引：谓词只覆盖 `fail_exhausted_leases` 才有的 `attempts` 下限，
  因此 `claimable_candidates` 借用不到它。若写成「所有 `RUNNING` 行」的宽部分索引，领取的 `RUNNING`
  侧会被抢走并退回 `USE TEMP B-TREE FOR ORDER BY`（实测，违反卡面要求 1），故不采用；
* 谓词写作 `max_attempts <= attempts + 1` 是**实测**结果：SQLite 会把查询里的
  `attempts + 1 >= max_attempts` 归一化成这一形式，只有这种写法才被蕴含检查接受；反过来写则永远不被
  使用（退回 `SCAN jobs`）。证据 `tmp/p402e/diag_lease_predicate.py`。

无表/列/行/触发器/CHECK/词表改动，不回填数据；`0001`～`0015` 内容零改动。

**语义不变性反证**（部分索引以绑定参数为谓词时的正确性）：`tmp/p402e/diag_partial_correctness.py` 在
20 000 行上分别以 `QUEUED`/`RETRY_WAIT`/`PAUSED` 绑定 `state = ?`：加索引前后计数完全一致
（20 000 / 7 / 0），`state='PAUSED'` 的 UPDATE 一行未改，`state='RETRY_WAIT'` 的 UPDATE 恰好改 7 行。
SQLite 会把谓词与绑定值比对，绑定到别的 state 时自动改用通用索引。

### 3.3 机械版本耦合

| 文件 | 改动 | 原因 |
| --- | --- | --- |
| `catalog/schema.py` | `SUPPORTED_SCHEMA_VERSION` 15 → 16 + 0016 说明段 | 卡面要求 |
| `tools/diagnostics/packaged_api_smoke.py` | 冻结包 taken-time schema 断言 15 → `supported_schema_version()`（读源码真值） | 否则任何 16 库都会被误报为问题 |
| `tests/integration/test_p4_taken_time_faults.py` D1 | 用 `migrations_upto(15)` + monkeypatch 钉扎 15 | 该用例是「0015 步骤」本身，随移动的版本尖端会误报 |
| `tests/integration/test_p4_taken_time_migration.py` | 链条用例改为「到达 0015 并继续到当前尖端」；v14→v15 用例断言 `applied == (16,)`；旧构建拒绝用例用 `migrations_upto(15)` 钉扎 | 同上：这三例是 0015 步骤的证据，不随尖端漂移 |
| `tools/places/examples/mini-cities.{spec,manifest}.json` | `compatible_catalog_schema.maximum` 15 → 16 | 0016 只改 `jobs` 索引，地点数据集兼容面未变；否则工具会以「数据集与目录 schema 不兼容」拒绝自带示例包 |
| `tools/diagnostics/capacity_validation.py` | `semantic_stage` 文档字符串更新 F1 已修 | 原文断言「只有波次形状才便宜」，已不成立 |
| `docs/tasks/P4-03.md` | 迁移编号 `0016` → `0017`、（16 → 17），并加「开工先复核」提示 | `0016` 已被本卡占用 |

## 4. 前后对照

### 4.1 成本曲线（20 样本，真实 migration 建出的 catalog）

| 积压 | 改前 `claimable_candidates` 中位 / P95 | 改后中位 / P95 | 改前 `claim_next` 中位 | 改后中位 |
| --- | --- | --- | --- | --- |
| 10 000 | 3.591 / 3.884 ms | 0.888 / 1.061 ms | 6.878 ms | 1.760 ms |
| 50 000 | 19.443 / 20.365 ms | 0.937 / 1.037 ms | 32.048 ms | 1.640 ms |
| 100 000 | 37.064 / 38.917 ms | 0.994 / 1.050 ms | 62.388 ms | 1.755 ms |
| 300 000 | 107.22 / 113.739 ms | 1.480 / 1.622 ms | 181.879 ms | 2.613 ms |

改后曲线与积压**不再相关**（0.89～1.48 ms），`claim_next`（含资格判定与写事务）同样平坦。
证据：`test-results/p4-02e-claim-cost-after.json`；另在**产品真实状态（从不 `ANALYZE`）**下复测
（`test-results/p4-02e-claim-cost-noanalyze.json`）：10 000 档 0.91 ms、300 000 档 1.04 ms，
`claim_next` 1.75 ms，结论不变。

### 4.2 计划证据（要求 1）

改后（两侧各三句，均**不含** `TEMP B-TREE` 且都命中 0016）：

| 语句 | `QUEUED` 侧 | 过期 `RUNNING` 侧 |
| --- | --- | --- |
| 类枚举（首个） | `SEARCH jobs USING INDEX idx_jobs_claim_order (state=?)` | 同左 |
| 类枚举（下一个） | `SEARCH … (state=? AND priority>?)` | 同左 |
| 类内取最旧 `limit` 行 | `SEARCH … (state=? AND priority=?)` | 同左 |

冻结查询仍两侧 `USE TEMP B-TREE FOR ORDER BY`（改前反例保留在报告 §2.1 与工具 JSON 中）。
领取预筛选改后的计划与耗时（30 万积压）：`requeue_due_retries` → `SEARCH … idx_jobs_retry_due
(run_after<?)` 0.024 ms；`fail_exhausted_leases` → `SEARCH … idx_jobs_expired_lease
(lease_expires_at<?)` 0.016 ms；同事务合计 0.036 ms（改前 71.8 ms）。

### 4.3 写锁持有时间（要求 4）

`Scheduler.claim_next` 是 `catalog.write()`（`BEGIN IMMEDIATE` → `COMMIT`），上表的 `claim_next`
中位即该写事务的持有时长：30 万积压 **181.879 ms → 2.613 ms**。分解显示成本没有被转移到别的阶段：
空写事务 0.006 ms、预筛选 0.036 ms、候选列举与精确裁决 1.48 ms，其余为资格判定与 `claim_job` 更新。
维护侧 `drain` 的 `lock_wait_ms`/`write_hold_ms` 由容量工具逐波记录（§8）。

## 5. 等价性证据规模（要求 2、4）

* 随机状态矩阵（DS-H 夹具收编为正式用例）：**240 状态 × 6 个 `limit` × 3 个 aging 窗口 = 4 320 次页面比较**，
  逐项相同（`job_id` 顺序与 `effective_priority` 值都相同），`identical = true`，证据
  `test-results/p4-02e-claim-cost-after.json`；正式用例
  `tests/integration/test_p4_02e_backlog_claim.py::test_the_bounded_claim_is_item_for_item_the_frozen_page_on_every_random_state`。
* 覆盖变量：混合优先级（含负数与高值）、同毫秒 `created_at`、跨多个 aging 窗口、`created_at` 在未来、
  `run_after` 在未来、过期与未过期租约、非目标 `processor_id`、`processor_ids` 为 `None`/空集/多个/不存在、
  `limit ∈ {1,2,3,7,64,256}`、aging 窗口 ∈ {1, 60 000, 86 400 000}。
* **并发/重启语义不变**：跨 catalog 重启后同状态仍返回同一序列
  （`test_the_same_state_and_sequence_survive_a_restart`）。
* **领取序列逐项相同（序列级，不止单页）**：两个行级相同的库各走一次真实领取路径
  （`requeue_due_retries` + `fail_exhausted_leases` + `claim_job`，`limit=32`）：产品页与冻结页驱动的
  领取序列**逐项相同**，结束时 `state`/`attempts` 快照也相同
  （`test_the_claimed_sequence_is_identical_to_the_frozen_sequence`；主审在 2 580 行夹具上独立复现
  2 513 / 2 513 次领取全等，见 `test-results/p4-02e-review-sequence.json`）。
* **资格与租约边界**：一次领取仍会把其候选页内的不合格处理器全部 `_block_unqualified`
  （300 行积压下正好 `BLOCKED = 256 = MAX_CANDIDATES_PER_CLAIM`，不消耗 `attempts`）；过期租约 takeover
  仍计一次失败并在预算耗尽时以 `LEASE_EXPIRED` 终结；暂停态仍以 `SCHEDULER_PAUSED` 拒绝领取。

## 6. 迁移证据（沿用 P4-00B 口径）

`tests/integration/test_p4_02e_migration.py`（6 例，全绿）：

* 链条连续：`0001`～`0016` 恰好覆盖 `1..SUPPORTED_SCHEMA_VERSION(16)`，前 15 个文件名逐个钉扎，`0016` 为第 16 个；
* `0016` 脚本解析后**恰好三条 `CREATE INDEX`**、与设计逐字相同，且不含
  `CREATE TABLE/ALTER TABLE/DROP/INSERT/UPDATE/DELETE/CREATE TRIGGER/PRAGMA`；
* 真实 v15 库（含 assets/file_locations/jobs 五种状态/scheduler_state/catalog_filter_revision）
  → v16：`previous_version=15`、`applied=(16,)`、备份为 v15 且不含新索引、`verify_schema(16)`、
  `PRAGMA foreign_key_check` 为空、`jobs` 列集与**所有预存行逐字不变**、`metadata` 除
  `schema_version` 外逐字不变；
* 失败的 0016 完全回滚（版本仍 15、三个索引不存在、预存行不变）；
* 只支持 15 的旧构建对 16 库 `SCHEMA_TOO_NEW` 且零写入（文件字节不变）。

## 7. 变异测试（要求 6）

`test_the_equivalence_case_is_not_vacuous` 在同一状态上同时验证：删掉候选上界（只取最低优先级类）、
去掉 `job_id` 决胜键、把 `created_at` 提到首键、把 `job_id` 排到 `created_at` 之前 —— 四个变异页面
都与冻结页面**不同**，而产品页面与冻结页面**相同**，证明等价性用例非空转。

### 7.1 验收命令与结果

| 命令 | JUnit | 结果 |
| --- | --- | --- |
| `pytest -q tests/integration/test_p4_02e_backlog_claim.py` | `test-results/p4-02e-claim-focused.xml` | **10 / 0 / 0 / 0**（含主审放行条件新增的序列级用例与强化后的预筛选守卫） |
| `pytest -q tests/integration/test_p4_02e_migration.py` | `test-results/p4-02e-migration-focused.xml` | **6 / 0 / 0 / 0** |
| 版本耦合回归（P4-00B 迁移/D1、place dataset 工具与清单、catalog 迁移、P4-00A 契约） | `test-results/p4-02e-pincoupling.xml` | **88 / 0 / 0 / 0** |
| `pytest -q tests/unit tests/integration` | `test-results/p4-02e-full.xml` | **1698 / 0 / 0 / 48**（48 为环境跳过；本卡净增 16 例） |
| 主审独立复跑（聚焦 66 例、全量、成本探针、10 000 档突发） | `test-results/p4-02e-review-*.{xml,json}` | 见 [主审报告 §2](P4-02E-dsh-engineering-review.md) |
| `ruff check .`、`git diff --check`、`uv lock --check` | — | 全 exit 0（`uv.lock` 零 diff，依赖未变） |

说明：仓库门禁是 `ruff check`（已全绿）。`ruff format --check src/photo_search/jobs/queue.py`
会报需重排，但 `git show HEAD:… | ruff format --check` 证明**同样的偏差在改动前就存在**
（`get_job`/`requeue_due_retries`/`finish` 的既有长行），本卡未把格式化重排混进差异。

一次全量运行中出现过 1 例与本卡无关的既有偶发失败
（`tests/integration/test_p3_ocr_lifecycle.py::test_the_executor_result_never_carries_a_path_or_text`：
结果信封里某个哈希恰好含子串 `999`，同用例单独复跑即通过；该断言是 P3-01 的既有随机哈希敏感断言，
不在本卡范围，未改）。

## 8. 30 万一次性突发回归（要求 3）

命令（卡面）：`--scales 300000 --burst-sample 300000 --publish-wave 300000 --keep --skip-thumbnails`，
另加 `--no-queries --no-http`：查询阶段量的是 F2/§12.2（P4-02F 范围），30 万档需 80 分钟以上，
且它在语义阶段**之后**运行，不影响本条吞吐口径。证据 `test-results/p4-02e-burst-300k.json`、
`test-results/p4-02e-burst-300k-log.txt`。

| 指标 | 改前（P4-02B / DS-L） | 本卡（一次性全量突发） |
| --- | --- | --- |
| execute 吞吐 | 11.3 件/秒（截断样本） | **98.3 件/秒**（300 000 件 / 3 050 960.5 ms） |
| 参考：稳态波次同规模 | ≈79 件/秒 | 98.3 件/秒（卡面门槛 ≥70 件/秒） |
| 波次形状 | `--publish-wave 5000` 分波 | 单波 300 000（`burst_sample=300000`、`waves=1`） |

阶段事实（同一 run）：夹具 272.3 s；扫描 748.6 s（400.7 件/秒）；投影 17.3 s；入队 4.77 s；
**execute 3 050 960.5 ms / 300 000 步**；drain 726.8 s（300 000 事件，412.8 事件/秒，2.423 ms/事件）；
outbox `PENDING 300000 → applied 300000`（1 500 批，returncode 0）；
`semantic_status`：`schema_version=16`、`artifacts PUBLISHED 300000`、`outbox CONFIRMED 300000`、
`entries CONFIRMED 300000`、`generations READY 1`、`semantic_ready 300000`、`pending 0`、
`pending_repair 0`；`asset_id_idx` 由第二次 drain 建成（`created`）。

维护侧事务口径（容量工具记录）：drain `lock_wait_ms` p50/p95 = 0.020 / 0.033 ms；
`write_hold_ms` p50/p95 = 292.8 / 542.3 ms（这是**维护 drain 自己的**事务，不是领取事务；
领取的写锁持有时长见 §4.3）。

同一命令在 10 000 档的前后对照（可快速复跑）：改前 10 000 件一次性突发 96.2 件/秒
（`test-results/p4-02b-burst-partial.json`），改后 **106.3 件/秒**（`test-results/p4-02e-burst-10k.json`，
`schema_version=16`、`semantic_ready=10000`、`pending=0`）。

D 级注入（工具默认块，同一 run）：`interrupted_drain` 中断后恢复 `applied=780`、
`outbox_after CONFIRMED 900`、`engine_rows 900`、`all_confirmed=true`；`after_lance` 重放 1 次；
`content_change` 新向量 rank 1 且旧向量不再是首位；`offline` 与 `pending_vector` 均正常完成。

**未达标项（非本卡范围）**：该 run 的「维护期间前台检索」P95 = **34 945.94 ms**（5 样本，
目标 ≤1.5 s），与 P4-02D/P4-02B 的 F2/F3 结论一致，属 P4-02F。本条不因 F1 收口而改变。

**替身边界**：与 P4-02B/P4-02D 相同，编码器是声明的确定性替身（本机无固定中文 CLIP 权重），
因此 98.3 件/秒量的是「领取 + 产物/outbox 发布 + 作业终结」这一侧，不含真实模型前向。

## 9. 未验证项与 BLOCKED（不得记 PASS）

* **真实中文 CLIP 权重端到端**：本机无固定权重，容量与探针全程使用声明的确定性替身执行器，
  真实模型吞吐/显存仍 BLOCKED（与 P4-02B 相同）。
* **冻结包（C 级）**：`0016` 是打包数据资源，`PhotoSearch.spec` 的
  `collect_data_files('photo_search')` 会自动带上它，但本卡**未重建 onedir、未重跑打包 smoke**；
  `dist/PhotoSearch` 仍是 schema 15 的旧包，属于本卡有意留白的未验证项（下一张打包/升级卡或 P4-03 一并做）。
* **优先级类数 K 的无界场景（已量化，见下表）**：今天所有生产 `enqueue` 调用点都不传 `priority`
  （默认 0，`rg` 在 `src/`、`tools/` 中找不到传参点），API 只**读** `priority`，故 K = 1；本实现的
  成本是 O(K) 次索引 seek + O(K × limit) 行候选，**在固定 K 下不随积压增长**，但常数随 K 上升：

| K（10 万积压、limit 256、ANALYZE） | 本实现中位 | 冻结查询中位 | 备注 |
| --- | --- | --- | --- |
| 1（产品当前形态） | 0.863 ms | 23.962 ms | 28× 更快 |
| 10 | 9.431 ms | 24.390 ms | 2.6× 更快 |
| 50 | 37.7 → 51.1 ms（积压 1 万 → 30 万） | 4.021 → 72.957 ms | 固定 K 下只随积压微增；小积压时比旧实现慢 |
| 100 | 113.084 ms | 26.067 ms | 比旧实现慢 4.3× |
| 1 000 | 466.737 ms | 26.952 ms | 比旧实现慢 17× |

  **触发条件（写入后续卡面前置）**：任何引入非零优先级的任务，必须先在开工时复测本表，并先把类枚举
  改成按类游标的堆式多路归并（或给出 K 的有界规则），否则不得以「F1 已修」为前提放行。量化由
  [主审](P4-02E-dsh-engineering-review.md) §8 N2 给出（`test-results/p4-02e-review-classes.json`、
  `p4-02e-review-classes-backlog.json`）。
* **与 F2/F3 的关系**：本卡只关闭 F1（领取路径）。30 万档检索 P95、`asset_id_idx` 覆盖率、
  ANN Recall 0.95 仍归 P4-02D/P4-02F。
* 30 万档 HTTP、冷 OS 缓存与长时 soak 未测（与 P4-02B 相同）。

## 10. 停止点与下一步

* 本卡**未触碰**公平公式、aging 语义、领取窗口、饥饿上界、`MAX_CANDIDATES_PER_CLAIM` 与三个排序键，
  也不新增持久优先级/Redis/Celery/第二向量后端 → 按卡面路由保持 **`SOL_NOT_REQUIRED`**。
* 下一步交**另一位 DS-H / 高推理**做**完整受影响面工程主审**：领取谓词与逐项等价性、revision/幂等键、
  租约与资格门、`0016` 迁移、计划与变异证据、以及 §2.2/§2.3 两条新发现是否被正确收口；主审不得只核 diff。
* 一并移交 [P4-02F](P4-02F.md) 的前置结论更新：本卡不改变 F2/F3 的归因与闸门，
  但 30 万档突发吞吐已回到稳态量级（§8）。**P4-03 的迁移编号已从 `0016` 改为 `0017`**
  （`0016` 已被本卡占用；卡片同时加了「开工先复核当前 `SUPPORTED_SCHEMA_VERSION` 与 migration 目录」的提示）。

## 11. 真实 Git 状态与提交边界

```text
git status --porcelain=v1 -b
## master...origin/master
 M docs/development-status.md
 M docs/tasks/P4-02F.md
 M docs/tasks/P4-03.md
 M src/photo_search/catalog/schema.py
 M src/photo_search/jobs/queue.py
 M tests/integration/test_p4_taken_time_faults.py
 M tests/integration/test_p4_taken_time_migration.py
 M tools/diagnostics/capacity_validation.py
 M tools/diagnostics/packaged_api_smoke.py
 M tools/places/examples/mini-cities.manifest.json
 M tools/places/examples/mini-cities.spec.json
?? docs/reports/P4-02E-backlog-claim.md
?? src/photo_search/catalog/migrations/0016_jobs_claim_order.sql
?? tests/integration/test_p4_02e_backlog_claim.py
?? tests/integration/test_p4_02e_migration.py
?? tools/diagnostics/claim_cost_probe.py
?? tools/diagnostics/p4_02f_stage0.py     # 非本卡文件：并行 P4-02F 会话创建，本卡未改动
```

* `master` 未领先 `origin/master`，本卡**未提交、未推送**（用户未授权）。
* `tmp/`（本卡全部探针与两个可丢弃 data root）与 `test-results/` 不在 Git 跟踪范围内。
* `uv.lock` 零 diff（依赖未变，96 包）。

## 12. 主审放行条件落实（2026-09-25）

| 条件 | 处置 |
| --- | --- |
| N1 预筛选守卫在 5 000 行夹具下空转 | **已修**：守卫改为 `test_the_prefilters_of_the_claim_use_their_partial_indexes`——50 000 行**单一状态**（突发形状，统计信息让规划器真的会考虑整索引/整表路径）+ `ANALYZE`；断言两条计划**点名** `idx_jobs_retry_due`/`idx_jobs_expired_lease`，并加 DROP 对照（计划必须变化、到期侧必须出现 `SCAN jobs`）证明夹具可判定。迁移用例里那条同源的空转断言已删除并注明原因 |
| N2 K 交叉点未量化 | **已写入** §9 表格 + 触发条件（引入非零优先级前必须复测并改堆式多路归并/给出 K 有界规则） |
| §2.2 耗时建议写成区间 | **已改**为 26–35 ms / 28–38 ms 并注明主审同日复现值 |
| C 级冻结包 | 维持**未验证**标记：主审已证 `collect_data_files('photo_search')` 含 `0016`（28 资源 / 16 迁移），但 `dist/` 仍是 schema 15，重建与打包 smoke 留待下次打包任务 |
| 注释里那条最有力证据的固化 | 主审探针的「领取序列逐项相同」**已提升为正式用例** `test_the_claimed_sequence_is_identical_to_the_frozen_sequence`（两个同夹具库、真实领取路径、`limit=32`），序列级等价不再是探针独有 |

落实后复跑：聚焦 **10 / 0 / 0 / 0**、迁移 **6 / 0 / 0 / 0**、全量 **1698 / 0 / 0 / 48**；
`src/**` 与 `0016` 的 SHA-256 **与主审开审快照逐字相同**（本轮只改测试与文档，产品代码零改动）。
