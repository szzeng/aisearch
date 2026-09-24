# P4-02E 独立 DS-H 完整受影响面工程主审

| 项 | 值 |
| --- | --- |
| 审查对象 | [P4-02E](P4-02E.md) 的实现与证据；实现报告 [P4-02E-backlog-claim.md](P4-02E-backlog-claim.md) |
| 基线 | `a0a55c0`（`master...origin/master`，未领先）；实现**未提交**，审查针对工作树状态 |
| 被审文件（SHA-256 前 16 位） | `src/photo_search/jobs/queue.py` `e7af7d630b1f258a`；`src/photo_search/catalog/schema.py` `73f07d2685c06fc2`；`src/photo_search/catalog/migrations/0016_jobs_claim_order.sql` `067505b70e87642a`；`tests/integration/test_p4_02e_backlog_claim.py` `fb25105c965c3754`；`tests/integration/test_p4_02e_migration.py` `39271617e46ddbc4`；`tools/diagnostics/claim_cost_probe.py` `f91ef239098136c0` |
| 审查模型 | DS-H（用户指派）。**独立性限制见 §0** |
| 结论 | **`ENGINEERING REVIEW PASS`**（含 1 条提交前应修、2 条记录性发现）；`SOL_NOT_REQUIRED` 维持；C 级打包仍未验证 |

## 0. 独立性限制（必须先说明）

本次审查与实现**在同一会话内完成**（用户在本会话追加“继续做完整受影响面工程主审”），因此
“独立”只体现在：主审自己重跑测试与探针、自己重写冻结查询做对照、自己设计对抗性探针并**找到了
实现者没有找到的问题**（§9 N1/N2）。这不是卡面与 V3.1 §2.4 期望的“另一位 DS-H 会话/模型”的
严格独立审查：**若需要严格独立性，请另开会话用另一模型重跑 §2 与 §9 的探针**（探针已常设，
复跑成本约 5 分钟 + 20 分钟全量）。

## 1. 变更面审计

| 检查 | 结果 |
| --- | --- |
| 变更清单 | 11 改 5 新（另有 1 个**非本卡**未跟踪文件 `tools/diagnostics/p4_02f_stage0.py`，并行 P4-02F 会话创建，本卡未动） |
| 允许面 | `jobs/queue.py`（仅 `claimable_candidates` 及其私有辅助 + 注释）、新增 `0016`、`catalog/schema.py` 版本号、测试/工具/报告/状态、两处文档与四处机械版本钉扎 |
| 禁止面零 diff | `docs/decisions/**`、`src/photo_search/{scheduler,search,api,web,ingestion,indexing}/**`、`0001`～`0015`、`PhotoSearch.spec`、`tools/packaging/**`、`pyproject.toml`、`uv.lock` —— `git diff --stat` 与 `git status --porcelain` 均为空输出 |
| 公平/aging/租约语义 | `queue.py` 的 diff **只**含 `_CLAIM_SIDES`/`_CLAIM_INDEX`/`_claim_priority_sql`/`_claim_class_sql` 新增与 `claimable_candidates` 改写；`requeue_due_retries`、`fail_exhausted_leases`、`claim_job`、`_start_running`、`finish`、`heartbeat` 的 SQL 文本**逐字未改**（因此“只新增索引”的说法成立） |
| 新依赖/新后端 | 无（`uv lock --check` 96 包零 diff；无 Redis/Celery/第二向量后端） |

## 2. 主审独立复现（B + D）

全部由主审自跑，未引用实现者的 JUnit：

| 复跑 | 证据 | 结果 |
| --- | --- | --- |
| 聚焦（claim 9 + migration 6 + scheduler/jobs 40 + migrations 11） | `test-results/p4-02e-review-focused.xml` | **66 / 0 / 0 / 0**（主审复跑发生在本轮 N1 修复**之前**；修复后同一集合为 67，见 §8/§9 与本报告 §9 的落实后复跑） |
| 全量 `tests/unit tests/integration` | `test-results/p4-02e-review-full.xml` | **1697 / 0 / 0 / 48**（48 环境跳过） |
| 领取成本探针（4 档、20 样本） | `test-results/p4-02e-review-claim-cost.json` | 中位 0.952 / 0.898 / 0.927 / **1.654** ms（10k/50k/100k/300k）；`claim_next` 1.695 / 1.670 / 2.878 / 2.972 ms；等价性 4 320 次比较 `identical=true` |
| 真实产品入口 10 000 档一次性突发 | `test-results/p4-02e-review-burst-10k.json` | execute **99.6 件/秒**（10 000 件 / 100 372.7 ms），`schema_version=16`、`semantic_ready=10000`、`pending=0`；与实现者的 106.3 件/秒同量级（±6% 机器波动） |
| 30 万档一次性突发 | **未重跑**（约 1.5 h），只做核读；`test-results/p4-02e-review-burst-artifact.json` | 内部自洽：`steps=wave_assets=300000`、`waves=1`、`burst_sample=300000`、`applied=300000`、`batches=1500`、`returncode=0`、`98.3 件/秒`（用 ms 反算一致）、`drain 412.8 事件/秒`、`schema_version=16`、`semantic_ready=300000`、`pending=0`、`pending_repair=0`、五个 D 级注入全 ok、日志末行与 JSON 一致 |

计划证据复核（读 `test-results/p4-02e-claim-cost-*.json` + 主审自跑 EXPLAIN）：改后两侧三句都是
`SEARCH jobs USING INDEX idx_jobs_claim_order (state=?[/priority>?/priority=?])`，**无** `TEMP B-TREE`；
冻结查询两侧都仍是 `USE TEMP B-TREE FOR ORDER BY`（改前反例成立）。报告数字逐项复算一致
（`test-results/p4-02e-review-evidence-audit.json`）。

## 3. 领取谓词与等价性（主审自设探针）

| 检查 | 设计 | 结果 |
| --- | --- | --- |
| 大表等价性 | 30 万行、两 runnable side 共 5 个类（QUEUED 0/2/-3 + 过期 RUNNING 0/4）、3 个 aging 窗口 | 三种窗口**页面逐项相同**；产品 4.482 ms vs 冻结 70.909 ms（`p4-02e-review-equivalence-large.json`） |
| **领取序列**（卡面要求 2） | 两个同夹具库（行级 fingerprint 相同），各跑一次真实领取路径（`requeue_due_retries` + `fail_exhausted_leases` + `claim_job`，limit=64）直到无可领取 | **2 513 / 2 513 次领取，`job_id` 序列逐项相同**，结束时 `state`/`attempts` 快照也相同（`p4-02e-review-sequence.json`） |
| `processor_ids` 边界 | `None` / `[]` / 单值 / 多值 / 不存在 | 与冻结查询逐项相同；`[]` 不领取任何作业，`None` 含外来处理器行 |
| 幂等/重复领取 | 序列探针内每次 `claim_job` 都在同一 `BEGIN IMMEDIATE` 内做带 `state`/`run_after` 守卫的 UPDATE（未改） | 2 513 次领取无重复、无丢失；五字段逻辑键与 `input_revision` 语义零改动 |

冻结对照由主审在探针内**独立重写**（不复用产品 SQL），因此上表不是同义反复。

## 4. 租约与资格门（卡面要求 5）

| 边界 | 主审复核 |
| --- | --- |
| 不合格处理器 | 300 行积压 + 无 capability 时，一次 `claim_next` 把候选页内 **256 条**全部 `_block_unqualified`（`attempts` 不变、`blocking_reason=RUNTIME_CAPABILITY_UNAVAILABLE`），第 257 条仍是 `QUEUED`；这是**冻结语义**（领取广度 = `MAX_CANDIDATES_PER_CLAIM`），本卡未改 |
| 过期租约 takeover | `max_attempts=1` 的过期执行仍被计一次失败并以 `LEASE_EXPIRED` 终结，随后领取继续到下一个候选；`attempts` 语义与 `_start_running`/`fail_exhausted_leases` 文本未改 |
| 暂停态 | `scheduler.pause()` 后 `claim_next` 仍抛 `SCHEDULER_PAUSED`，300 条一行未动 |
| 观察到的行为变化（计划侧，非语义） | `fail_exhausted_leases` 的**计划**由 `idx_jobs_processor_state`/`SCAN jobs` 变为 `idx_jobs_expired_lease`（§8）；返回行集合在四种配置下逐项相同（§6.3） |

## 5. 迁移 `0016`（卡面要求 3）

* **脚本内容**：解析后恰好 3 条 `CREATE INDEX`，与设计逐字相同；无 `CREATE TABLE/ALTER/DROP/INSERT/UPDATE/DELETE/TRIGGER/PRAGMA`。
* **连续/备份/回滚/旧构建**：主审重跑实现者的 `test_p4_02e_migration.py`（6 例）全绿，并复核其断言确实覆盖
  `previous_version=15 → applied=(16,)`、备份为 v15 且不含新索引、`verify_schema(16)`、
  `foreign_key_check` 空、`jobs` 列集与**所有预存行逐字不变**、`metadata`（除版本键）不变、
  失败脚本完全回滚、旧构建 `SCHEMA_TOO_NEW` 零写入。
* **索引被真正使用**（主审自跑，`ANALYZE` + 规模扫描）：

| 规模 | 有 0016 | 无 0016 |
| --- | --- | --- |
| 5 000 | `requeue`→`idx_jobs_processor_state`、`fail`→`idx_jobs_expired_lease` | 两条都是 `idx_jobs_processor_state`（**没有** `SCAN jobs`） |
| 50 000 | `requeue`→`idx_jobs_retry_due`、`fail`→`idx_jobs_expired_lease` | `requeue`→`idx_jobs_processor_state`、`fail`→**`SCAN jobs`** |
| 100 000 / 200 000 / 300 000 | 同上（均命中两个部分索引） | 同上（`fail` 均为 `SCAN jobs`） |

* **部分索引不改变结果**（主审自跑 `p4-02e-review-prefilter.json`）：同一批含 `attempts`/`max_attempts`
  边界（`(0,1)`、`(2,3)`、`(3,3)`、`(0,3)`）与 `RETRY_WAIT`/`RUNNING`/终态混合的行，在
  「有索引 / 无索引 / 无索引且无统计信息 / 再加回索引」四种配置下，`requeue_due_retries` 的
  `rowcount`、`fail_exhausted_leases` 的失败行集合与各自 `attempts`、以及最终状态直方图**完全一致**
  （一次异常的比较口径由主审自行修正后复算通过——见 §9 说明）。

## 6. 对实现者两条新发现的独立复核

| 发现 | 主审复现（`ANALYZE`、30 万行、无 0016 → 有 0016） |
| --- | --- |
| F1c `fail_exhausted_leases` 整表扫描 | `SCAN jobs` 27.864 ms → `SEARCH … idx_jobs_expired_lease (lease_expires_at<?)` **0.008 ms** |
| F1b `requeue_due_retries` 整索引扫描 | `idx_jobs_processor_state (ANY(processor_id) AND ANY(state) AND run_after<?)` 26.417 ms → `SEARCH … idx_jobs_retry_due (run_after<?)` **0.010 ms** |
| F1d `SELECT DISTINCT priority` 依赖统计 | 主审复核实现者结论：本实现改用 `priority > ? … LIMIT 1` 递增 walk 后，无统计信息下 300 万以内 4 档成本仍平坦（§2 探针 `--skip-analyze` 结果 0.91 / 1.04 ms） |

口径差异（非缺陷）：实现者报告 §2.2 记 35.1 / 38.4 ms，主审复现 26.4 / 27.9 ms；同一台机器、不同
夹具/页缓存状态，量级与结论一致。建议报告把该两处写成区间或注明 ±30% 环境波动。

其它 `jobs` 语句的计划回归（`p4-02e-review-other-plans.json`，5 万行）：`p1_claim_queued`、
`p1_claim_running`、`recover_blocked`、`finish_update` 的计划**均未变**；唯一变化是
`fail_exhausted_leases` 改用新的部分索引（预期）。

## 7. 变异与证据审计

* 变异（实现者的正式用例，主审重跑）：只取最低优先级类、去掉 `job_id` 决胜键、把 `created_at`
  提为首键、把 `job_id` 排在 `created_at` 之前 —— 四个变异页都与冻结页不同，产品页与冻结页相同。
* 证据审计（主审从 JSON/JUnit 反算）：成本表（改前 3.591/19.443/37.064/107.22 与 P95、改后
  0.888/0.937/0.994/1.48 与 P95、`claim_next` 181.879 → 2.613）、无统计信息复测（0.91 / 1.04）、
  等价性 240×400 / 4 320、30 万突发（3 050 960.5 ms / 98.3 件/秒 / 412.8 事件/秒 / schema 16 /
  `pending=0`）、四份 JUnit 计数（1697/9/6/88）**逐项一致**。

## 8. 主审新发现（实现者未记录）

### N1（非阻断，**建议提交前修** —— 已于 2026-09-25 修复，见下）两处“预筛选不得扫描”的守卫在自身夹具规模下是空转的

最小反例（主审自跑）：在实现者守卫用例的规模（5 000 行、含 20 条过期 `RUNNING`、`ANALYZE`）上
**删掉 `idx_jobs_retry_due` 与 `idx_jobs_expired_lease`** 后，两条语句的计划仍是
`SEARCH jobs USING INDEX idx_jobs_processor_state (processor_id=? AND state=…)`，
`SCAN jobs` **不会出现** ⇒ 现有断言（“计划不以 `SCAN jobs` 开头”）在索引整个缺失时依然通过。
只有到 **50 000 行 + `ANALYZE`** 才会出现「有索引→命中部分索引；无索引→`SCAN jobs`」的可判定差异
（上表 §5）。

影响面：产品正确性不受影响（结果不变，只是变慢）；受影响的是**回归保护**——将来若迁移/规划器变化
使部分索引失效，`SCAN jobs` 会在 30 万档以约 26–28 ms/次领取重新出现（突发吞吐约降到 1/3），
而 CI 不会报警。

最小修法（交回实现者）：把 `test_the_prefilters_of_the_claim_never_scan_the_backlog` 与迁移用例中的
同类断言移到 **≥50 000 行 + `ANALYZE`** 的夹具，并断言两条计划**分别点名**
`idx_jobs_retry_due` / `idx_jobs_expired_lease`；可再加一条对照（先 `DROP INDEX` 再断言出现
`SCAN jobs`，随后建回）证明断言非空转。夹具成本约 +0.4 s。

**修复与复验（2026-09-25）**：实现者按上述修法改为
`test_the_prefilters_of_the_claim_use_their_partial_indexes`——50 000 行**单一状态**夹具（主审复核：
混合状态会让规划器改选 `idx_jobs_processor_state`，反而测不到部分索引）+ `ANALYZE`，断言两条计划点名
各自的部分索引，并用 DROP 对照（两条计划都必须变化、到期侧必须出现 `SCAN jobs`）证明夹具可判定；
迁移用例里那条同源的空转断言已删除并注明原因。主审复跑：聚焦 **10 / 0 / 0 / 0**、迁移 **6 / 0 / 0 / 0**、
全量 **1698 / 0 / 0 / 48**；`src/**` 与 `0016` 的 SHA-256 与开审快照**逐字相同**（本轮修复只动测试与文档）。**N1 关闭。**

**主审“测试测试”复验**（同一 50 000 行单一状态夹具）：把两个部分索引换成**逻辑等价的普通索引**
（`repl_retry (state, run_after)`、`repl_lease (state, lease_expires_at)`）后，两条语句都退回
`ANY(...)` 形式的扫描路径（`idx_jobs_processor_state (ANY(processor_id) AND ANY(state) AND run_after<?)`、
`repl_lease (ANY(state) AND lease_expires_at<?)`），强化后的守卫会对**两条**都报警 ⇒ 守卫非空转；
同时这也独立证明了实现者的设计选择——**普通复合索引救不了 F1b/F1c，必须是谓词受限的部分索引**。

### N2（非阻断，带触发条件）K（不同优先级类数）的交叉点已量化

主审自跑（10 万积压、`limit=256`、`ANALYZE`）：

| 优先级类数 K | 本实现（中位） | 冻结查询（中位） | 结论 |
| --- | --- | --- | --- |
| 1 | 0.863 ms | 23.962 ms | 28× 更快（**产品当前形态**） |
| 10 | 9.431 ms | 24.390 ms | 2.6× 更快 |
| 50 | ~37–51 ms（固定 K 下只随积压 10k→300k 从 37.7→51.1 ms） | 4.021→72.957 ms | 交叉：小积压时本实现慢，30 万档反超 |
| 100 | 113.084 ms | 26.067 ms | 本实现 4.3× 慢 |
| 1 000 | 466.737 ms | 26.952 ms | 本实现 17× 慢 |

事实基础：所有生产 `enqueue` 调用点都用默认优先级 0（`rg` 在 `src/`、`tools/` 中找不到传
`priority=` 的调用），API 只**读** `priority`，故今天 K=1；卡面要求（“不随积压显著增长”）在固定 K
下成立（表内 K=50 一行即证据）。**触发条件**：任何引入非零优先级的后续任务，必须在开工时复测本表，
并先把类枚举改成按类游标的堆式多路归并（或给出 K 的有界规则），否则不得以“F1 已修”为前提放行。
实现者的 `queue.py` 文档字符串与报告 §9 已定性记录该风险，本审补充了上表量化。

### N3（状态记录）冻结包仍是 schema 15，C 级未验证

主审核验 `PyInstaller.utils.hooks.collect_data_files('photo_search')` 返回 **28 个数据资源 / 16 个
迁移脚本，含 `0016_jobs_claim_order.sql`**（`p4-02e-review-bundle.json`）⇒ 资源收集路径没有漏项；
但 `dist/PhotoSearch` 仍是 schema 15 的旧包，本卡未重建、未跑打包 smoke。结论保持
「C 级未验证」，在下次打包/升级任务中一并收口；在此之前不得用旧 `dist/` 做 16 库的验收。

### N4（信息）`INDEXED BY` 与未来的 `jobs` 重建迁移

`claimable_candidates` 显式点名 `idx_jobs_claim_order`：若将来有迁移像 `0012`/`0013` 那样重建 `jobs`
（DROP + CREATE），必须重建该索引，否则领取会**响亮报错**（“no query solution”）而不是静默变慢。
主审认为这是可接受的取舍（fail-loud），且新测试在迁移后立刻跑领取路径，能在 CI 阶段抓到；建议把这条
写进后续任何涉及 `jobs` 重建的任务卡（本卡 `0016` 头注释已含同等说明）。

## 9. 结论与放行条件

**`ENGINEERING REVIEW PASS`**

1. 变更面合规：只动 `claimable_candidates` 取数路径 + `0016`（纯新增索引）+ 版本号；禁止面零 diff；
   公平公式、三个排序键、`MAX_CANDIDATES_PER_CLAIM`、aging 默认值、预筛选与租约函数的 SQL 文本逐字未改。
2. 等价性达标：4 320 次页面比较 + **2 513 次真实领取序列逐项相同** + 30 万行 5 类 3 窗口页面相同；
   主审自设探针独立重写了冻结查询，非“同义反复”。
3. 性能目标达成（当前形态 K=1）：领取 107.22→1.48 ms（P95 113.74→1.62），`claim_next` 写事务
   181.879→2.613 ms；30 万一次性突发 98.3 件/秒（主审 10 000 档独立复跑 99.6 件/秒同量级）。
4. 迁移与索引语义达标：`0016` 三条索引被实测使用；部分索引在有/无/无统计/再加回四种配置下返回
   完全相同的行集合（含 `attempts` 边界）。
5. 不触发 V3.1 §2.2 升级条件，维持 **`SOL_NOT_REQUIRED`**（未改公平/aging/窗口/饥饿上界，
   未引入 ADR 裁决）。

**放行条件（交回实现者，非阻断本结论）**

* **N1**：按 §8 的最小修法强化两处预筛选守卫（`≥50 000 行 + ANALYZE`，断言点名两个部分索引）。
  → **已落实并复验通过（§8 N1 修复与复验）**。
* **N2**：把 K 交叉点表写进报告 §9，并把“引入优先级必须先做堆式多路归并/有界规则”写成后续卡面前置条件。
  → **已落实**（实现报告 §9 表格 + 触发条件）。
* **N3**：保持 C 级未验证标记；下次打包任务必须重建 `dist/` 并跑打包 smoke。→ **维持标记**。
* 报告 §2.2 的两处耗时建议改写为区间（26–38 ms）或注明环境波动。→ **已改为区间并注明主审复现值**。
* 主审那条最有力证据（2 513 次领取序列逐项相同）原为探针独有 → **已提升为正式用例**
  `test_the_claimed_sequence_is_identical_to_the_frozen_sequence`（两个同夹具库、真实领取路径、`limit=32`，
  断言序列与结束状态都相同），序列级等价从此由 CI 守护。

**落实后状态**：聚焦 **10 / 0 / 0 / 0**、迁移 **6 / 0 / 0 / 0**、全量 **1698 / 0 / 0 / 48**；
产品代码（`queue.py`/`schema.py`/`0016`）SHA-256 与开审快照逐字相同。四条放行条件全部关闭，
仅 **N3 的 C 级冻结包**仍是卡面外的未验证项。

**仍未验证（不得记 PASS 的部分）**：真实中文 CLIP 权重端到端吞吐/显存、含真实文本塔的 API P95、
GTX 1660 最低配置、ANN Recall 0.95、30 万档 HTTP/冷 OS 缓存/长时 soak、以及 C 级冻结包。
30 万档突发为**核读 + 内部自洽校验**（未重跑，成本约 1.5 h）；如需严格独立复现，建议另开会话重跑该命令。
