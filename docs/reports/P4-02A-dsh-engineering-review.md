# P4-02A 独立 DS-H 全量工程主审报告

被审对象：**P4-02A 语义 outbox 生产维护入口接线**（提交 `9e42b66`，工作树干净）。
依据任务卡 [docs/tasks/P4-02A.md](../tasks/P4-02A.md) 的「审查路由」与 V3.1 §2.2/§3.2/§3.4/§11。

**结论：`ENGINEERING REVIEW FAIL`。** 本卡范围内有 **1 条最小修复项（A1，直接可复现）**，
另有 **2 条既有面未决项（A2/A3，非本卡引入、超出本卡允许改动范围，须另立卡或列为 P4-02B 前置）**，
并有 1 处被审实现报告的事实性误述（F1 离散度表述，已就地更正，只改表述；另 1 处经复核无需更正）。
因此本轮**未标 P4-02A PASS、未进入 C2 / Sol 聚焦终审、未启动 P4-02B**。

## 1. 观察基线与审查范围

- 观察基线：`9e42b66`（`git log -1 --oneline`），`git status --porcelain` 为空。
- 被审交付物：`src/photo_search/indexing/{store.py,maintenance.py,cli.py}`、
  `tools/diagnostics/index_maintenance_probe.py`、
  `tests/integration/test_p4_02a_maintenance_entry.py`、`docs/reports/P4-02A-maintenance-entry.md`、
  `docs/development-status.md`、`docs/tasks/P4-02B.md`。
- 本轮主审自身新增：本报告、`test-results/p4-02a-review-*` 证据、两处实现报告表述更正与状态更新。
  **未修改任何 `src/`、测试或工具代码**（结论见 §6/§11）。

## 2. 独立复现（不引用被审报告的结论）

全部命令在本机以正常 Windows 权限路由执行（受限令牌下沙箱内 `--basetemp` 会得到 `WinError 5`，
未改 ACL）：

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path
# 1) 证据工具（新的空工作目录，独立于被审运行）
uv run --no-sync python tools/diagnostics/index_maintenance_probe.py --mode all `
  --work-dir "tmp/p4-02a-review 空格" --assets 400 --events 300 `
  --injection-assets 200 --injection-events 24 --foreground-writes 30 `
  --output test-results/p4-02a-review-maintenance.json
# 2) 受影响面 + 本卡新增 focused
uv run --no-sync python -m pytest -q -p no:cacheprovider `
  tests/integration/test_p4_01c_retrieval_blocks.py tests/integration/test_p2_index_submission.py `
  tests/integration/test_p2_acceptance_recovery.py tests/integration/test_p2_search_topup.py `
  tests/integration/test_p4_02a_maintenance_entry.py `
  --basetemp=.pytest-basetemp-p402a-review/focused-set --junitxml=test-results/p4-02a-review-focused.xml
# 3) 全量
uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration `
  --basetemp=.pytest-basetemp-p402a-review/full --junitxml=test-results/p4-02a-review-full.xml
uv run --no-sync ruff check . ; git diff --check ; uv lock --check
```

| 主审自测项 | 结果 |
|---|---|
| 全量 `test-results/p4-02a-review-full.xml` | **1658 / 0 / 0 / 48**（204.5 s）|
| focused `test-results/p4-02a-review-focused.xml` | **35 / 0 / 0 / 0**（23.3 s）|
| 证据工具 `test-results/p4-02a-review-maintenance.json` | 五场景 `ok=true`，进程退出码 0 |
| Ruff / `git diff --check` / `uv lock --check` | 全 exit 0 |

工具场景关键数字（主审自测，与被审报告同量级但不相同）：

| 场景 | 主审观测 |
|---|---|
| `entry`（400 资产 + 300 真实工件） | applied 300、2 批、208.5 ms；锁等待 p50 0.013 / p95 0.017 ms；持锁 p50 2.086 / p95 121.639 ms；第二次调用 applied 0；标量索引 `created` |
| 派生状态指纹 | 中断重启 `2ba50ddd…` = 不中断 `2ba50ddd…` = crash 窗口重放 `2ba50ddd…`（三者逐字节一致）|
| `two_maintainers`（25 事件） | A：applied 25 / 19 事务；B：applied 0 / 1 claim 事务，锁等待 **434.698 ms**（确被 A 的写事务串行化后跳过）；合计 25 = 事件数；outbox `{CONFIRMED 25}`；目标资产 revision 1 = entry revision = MAX(事件) |
| `contention`（前台 30 次修订写 + 10 次发布） | drain applied 300（收尾补 10）；锁等待 p95 0.014 ms；持锁 p95 148.168 ms；前台 30+10 次全部提交、0 拒绝；`catalog_filter_revision` 0→30 与事件行 0→30 与提交次数逐一相等；目标资产 401 revision 28 = entry = MAX(事件)，新向量距离 0.0、被取代向量 2.023 |
| `busy_timeout` | 1 次结构化拒绝（`CATALOG_UNREADABLE` / `database is locked`，phase=claim）等待 **5 480.6 ms**；有界重试后 applied 24、`{CONFIRMED 24}`、无重复；总 9 243.3 ms |
| `cli` | `status` / `drain` / 缺少子命令（exit 2）三个真实子进程调用全部符合契约，`drain` 返回 applied 24 |

## 3. 冻结面与依赖审计

- `git diff --name-only 3386f39..HEAD` 仅 8 个文件（§1 列表），**对以下禁止面为空**：
  migration 目录、`docs/decisions/**`（含 contracts）、`src/photo_search/{api,web,face,persons,search,places,scheduler,artifacts}`、
  `PhotoSearch.spec`、`tools/packaging/**`、`pyproject.toml`、`uv.lock`。
- `SUPPORTED_SCHEMA_VERSION = 15`（未变）；`0015_taken_time.sql` 仍是最新迁移；无新增依赖（`uv lock --check` 96 packages，diff 0 行）。
- 入口可达性核对：`photo_search.indexing.cli`/`maintenance` **不被** `__main__.py` 或任何生产模块导入
  （`rg` 结果仅 CLI 自身、诊断工具、测试），故被审报告 F3 的「不在冻结包可见入口内」表述成立；
  `ensure_scalar_indexes` 的唯一调用点仍是 `process_all`（维护侧）。

## 4. 逐文件 diff 审查

**`src/photo_search/indexing/store.py`（+71 / −7）——可观测性，语义未改。**
新增 `MAX_WRITE_SAMPLES`、`write_samples`、`write_transaction_counts`、`write_rejections` 与
`_timed_write(phase)` + `_record_write_sample`；`process_next`/`process_batch` 的 6 处
`with self.catalog.write()` 被逐处替换为 `with self._timed_write("claim"|"apply"|"revert")`，
**事务边界、返回路径、`_InjectedCrash` 分支、revert 分支、`_make_generation_ready` 顺序全部逐行等价**
（已按 diff 逐块比对，并在 §8 用变异验证）。
`audit_and_schedule_repairs`/`_schedule_existing`/`_is_superseded`/`_record`/检索面**零改动**（A2/A3 因此属既有面）。
计数取自未上限的 `write_transaction_counts`，样本取自上限 4096 的列表并另报
`timing_samples`/`timing_samples_truncated`：不会把「截断后的样本数」误读为事务数（设计正确）。

**`src/photo_search/indexing/maintenance.py`（新增 235 行）——入口与并发口径。**
`drain_semantic_outbox` 以「多维护者可并发、不加排他锁」为口径，直接复用 P4-01C 第二轮写协议；
`repair` 默认关闭并给出理由；`batch_size` 只改每批事件数（逐事件确认与整批原子性不变）；
`is_busy_rejection` 只把「`CATALOG_UNREADABLE` + database is locked」判为可重试；
`semantic_index_status` 只读且不创建。
**主审发现 A1（§6.1）：该函数在「重试前已有批次落地」时报告的 `applied` 会少于真实确认数。**

**`src/photo_search/indexing/cli.py`（新增 187 行）——受控入口。**
退出码 0/1/2 与 JSON 形态与 `face.cli`/`ocr.cli` 一致；参数校验（负数、`--batch-size < 1`）在 CLI 与入口两层；
`status` 不创建数据库（§5 复核）。无新服务/线程/依赖。

**`tools/diagnostics/index_maintenance_probe.py`（新增 1110 行）——证据工具。**
夹具真实（真实 catalog/工件/outbox/Lance 行），`fresh_fixture` 拒绝复用半 drain 目录；
并发场景把前台写者放在**自己的连接与线程**（否则会退化成 Python 锁竞争而不是 SQLite 单写者竞争，这点实现正确）；
`engine_delay` 只加长引擎写（即事务持锁）时间并显式声明；`two_maintainers` 的「第二个维护者在前者确认首批后加入」
使其必然与 A 的写事务相撞（主审两次运行分别观测到 313.2 / 434.7 ms 锁等待）。
**主审复核：工具的断言不是自证式的**——指纹比较、距离比较、`applied` 与确认数比对都独立于被测代码的自报字段。

**`tests/integration/test_p4_02a_maintenance_entry.py`（10 用例）**：见 §8（变异 + 重复性）。

## 5. 不变量复核

| 不变量 | 主审结论与依据 |
|---|---|
| 1 逐事件幂等与确认顺序 | 成立。P2-03/P4-01C 三个既有文件 + P4-01C 专项在主审全量里原样通过；`two_maintainers` 合计 applied 25 = 事件数、outbox 全 `CONFIRMED`、Lance 行数 = 唯一资产数 |
| 2 `after_lance` 窗口 = `IN_FLIGHT`、重放零重复 | 成立。`crash_window` 场景：崩溃后 `{IN_FLIGHT 3, PENDING 21}`、Lance 203 行 → 重放后 224 = 唯一 224，指纹与不中断一致 |
| 3 旧 revision 不回滚最新 | 成立。`content_change`（顺序）与 `contention`/`two_maintainers`（并发，含前台真实发布）三处均读到 `stored = entry = MAX(事件)`，新向量距离 0.0、被取代向量 > 0 |
| 4 状态机/`semantic_ready`/标量索引 | 成立。未改 READY 逻辑；`asset_id_idx` 仅由 `process_all` 创建并报告四态 |
| 5 读路径不受影响 | 成立。`retrieval.py`/`service.py` 未改；入口场景用生产候选遍历读回新资产；无 ANN 主路径 |
| 6 维护期间前台写不静默失败 | 成立但**覆盖面有限**（见 §7 观察 O2）：`catalog_filter_revision` 与事件行数与已提交次数严格相等；拒绝样本固定为结构化 `CATALOG_UNREADABLE`+locked；本轮前台侧实测拒绝数为 0，drain 侧实测 1 次 |

## 6. 未通过项（最小反例）

### 6.1 A1（本卡范围内，须修）——重试后 `applied` 少报真实确认数

探针输出：`test-results/p4-02a-review-probes.json` 的 `p2_refusal_after_partial_progress`。

**反例（确定性）**：5 个待确认事件、`batch_size=2`、`retry_limit=1`；用只拒绝「第 4 次写事务」
（第 2 批的 apply）的探针迫使 drain 在**第 1 批已确认之后**遇到一次 `busy_timeout` 拒绝。

```
report_applied = 3      # 只有成功那一次尝试的返回值
report_confirmed_delta = 5   # 本次调用真实净确认数
report_batches = 3      # 含被拒尝试的 apply 事务
report_write_transactions = 9   # 另有 1 次被拒写事务，只记在 write_rejections，不进入事务样本
report_rejections = {count: 1, retries: 1}
outbox_after = {CONFIRMED: 5}   # 状态正确，无丢失/无重复
```

即：**状态语义正确，但入口自报的 `applied` 会低于真实值**——而这正是本入口专门要报告的
`busy_timeout` 场景；P4-02B 会直接引用该字段，被审实现报告 §2/§5 也把 `applied` 当作「本次应用的事件数」。
根因：重试循环用 `applied = coordinator.process_all(...)` **覆盖**上一次的计数，而不是累加
（每次失败前已确认的事件由 store 累积在各自批次里，入口看不到）。

**最小修复范围（一行级）**：改为累加并对重试传剩余额度，例如
`applied += coordinator.process_all(now_ms=..., limit=int(limit) - applied)`；
`confirmed_delta` 保留为净校验值；`applied` 的 docstring/CLI 帮助明确「本次调用确认的事件数（含重试前已落地的批次）」。
**复核口径**：重跑本报告 §6.1 的探针（期望 `applied == confirmed_delta == 5`）+ `--mode busy-timeout`
（该场景第一次尝试未确认任何事件，修复后 `applied` 仍应为 24）+ focused 套件。

### 6.2 A2（既有面；非本卡引入，须另立卡）——一个不可读工件会**永久**卡住整条 drain

探针输出：`test-results/p4-02a-review-probes.json` 的 `p1_corrupt_artifact`。

**反例（确定性）**：3 个待确认事件，把其中 `review-p1-31` 对应的工件文件内容破坏（长度/摘要不符）后：

```
drain                -> ARTIFACT_MISSING "published artifact content failed verification"
outbox_after_drain_1 -> {PENDING: 3}      # 零进展
drain（第二次）      -> 同样的结构化错误；outbox 仍 {PENDING: 3}
drain --repair       -> 审计把工件置 RECLAIMED、条目置 PENDING_REPAIR 并删除坏字节，
                        随后仍在 drain 阶段以 ARTIFACT_MISSING "content is unavailable" 失败
```

明细（主审直接读库）：三条事件全部停在 `PENDING` 且 `last_error_code=ARTIFACT_MISSING`、
`attempts=4`——因为 `process_batch` 是「整批原子」且领取总是取最早事件，
**同批的三个事件互相拖累**，被回收后事件仍留在 outbox。行为是「失败关闭 + 无静默丢失」，
但**没有任何受支持的生产恢复路径**：架构 §7.2 所述的恢复是「重新执行同一逻辑任务」，
而 `ArtifactStore.republish_reclaimed` 目前**只有测试调用点**，没有生产入口。

**性质**：`_record` 的读取失败与「整批回滚」属 P2-03/P4-01C 既有语义（本次 diff 未触碰，见 §4），
改它属于「改状态机/确认语义」——超出 P4-02A 的允许修改范围。**建议**：另立小卡
（例如 P4-02A-fix 或 P4-02B 前置）给「工件不可读且已 RECLAIMED」的事件一个**结构化终态或隔离**
（写 `last_error_code` 后不再无限重试、并保留后续事件可继续 drain），同时给 `republish_reclaimed`
一个生产入口或明确运维流程。

### 6.3 A3（既有面；与 A2 同卡）——每次 `--repair` 都会为未确认条目**新增**一个事件

同一反例的 outbox 明细（主审读库）显示，连续两次 `drain --repair` 之后
`PENDING` 从 3 → 5 → 7：`audit_and_schedule_repairs` 对「条目未 CONFIRMED」的工件调用
`_schedule_existing`，而后者在审计路径上以 `MAX(projection_revision)+1` 分配新 revision，
于是每次审计都为同一 (artifact, generation) 造出一个新事件。drain 正常时这些事件会被
`_is_superseded` 收敛（不产生额外写入），但**在 drain 被卡住（A2）时它会无界放大 backlog**，
而 `--repair` 是本卡新暴露给运维的开关，因此两条发现必须一起决策。

**直接测量**（在同一卡住夹具上单独调用一次 `audit_and_schedule_repairs`）：
返回 `scheduled=["review-p1-32","review-p1-33"]`、`requires_reexecution=["review-p1-31"]`，
`pending` **7 → 9**，新增两行 `revision=3 / attempts=0`；结合前两次 `--repair`（3 → 5 → 7），
即**每次审计为每个未确认条目新增 1 个事件、revision 逐代递增**（0→1→2→3…）。
新事件排在队尾，而 drain 始终从头失败，因此 backlog 只增不减。

探针输出：`test-results/p4-02a-review-audit-growth.json`（本次审计的返回值与事件明细）。

## 7. 非阻断观察

- **O1 实现报告 F1 的离散度表述不准确**：被审报告写「同一代码的重复运行观察到约 2 倍离散」，
  但主审在同一代码（`9e42b66`）上复测为 **3 164.8 ms / 7.912 ms 每工件**，与被审的
  3 196.9 ms / 7.99 ms 每工件几乎一致；13.7 ms 那一笔来自**改动前**的中间构建。
  已就地更正该句（只改表述，不改结论：审计成本量级为个位数 ms/工件、默认关闭、30 万档未测）。
- **O2 前台侧未实测到真实拒绝**：`contention` 与用例中前台写 0 次被拒（持锁窗口仅 ~0.15 s，远小于 5 s
  busy_timeout）。前台与 drain 走的是**同一条** `Catalog.write()` 拒绝路径（drain 侧已实测 1 次并给出
  5 480.6 ms 等待与重试结果），因此不变量 6 的结论成立，但「前台被拒」这一子场景本轮**未实测**，
  记入未验证项。
- **O3 测试阈值的窄窗口**：`test_two_maintainers_confirm_every_event_exactly_once` 在
  「第二维护者 applied == 0」时要求锁等待 ≥ 10 ms。理论上存在极窄抖动窗口（第二维护者的 claim 恰好落在
  A 提交前最后一刻）。本卡内 5 次重复均未复现（§8），故不阻断；建议下一轮把该断言改为主审更稳的
  「`write_transactions ≥ 1` 且（applied > 0 或锁等待 ≥ 注入延迟/2）」，与工具判据一致。
- **O4 `--repair` 的成本默认不可见**：入口把审计成本（个位数 ms/工件）留给 `--repair` 使用者自测，
  报告已写明，主审认为口径诚实。

## 8. 测试强度（变异）与可重复性

**变异测试（主审自建临时 pytest 插件，不改仓库文件）**：对交付代码做 3 处语义破坏，新用例必须失败——

| 变异 | 内容 | 结果 |
|---|---|---|
| m1 | `is_busy_rejection` 恒为 False（重试能力被去掉） | `test_a_busy_timeout_refusal_is_structured_and_retried` **FAILED** ✅ |
| m2 | `_timed_write` 退化为 `self.catalog.write()`（可观测性被去掉） | `test_the_entry_bounds_events_and_reports_its_transactions` **FAILED** ✅ |
| m3 | `IndexCoordinator.process_all` 恒返回 0（入口不再应用事件） | `test_the_cli_entry_drains_a_pending_outbox` **FAILED** ✅ |

（JUnit：`test-results/p4-02a-review-mutation-{m1,m2,m3}.xml`，各 1 用例 1 失败。）

**可重复性**：并发最敏感的两个用例（`two_maintainers`、`foreground_writer`）连跑 5 轮 =
10 次执行，**0 失败**（`test-results/p4-02a-review-repeat-{1..5}.xml`，各 2/0/0）。

**未覆盖/未验证**（主审口径）：`--repair` 的审计路径只在夹具规模（200～700 工件）验证；
30 万档审计成本、容量/吞吐、真实模型向量、ANN/§12.3、A3、GTX 1660、冻结包内该 CLI 入口的可达性
均**未验证**（与实现报告未验证项一致；A2/A3 的修复方案本身也未被验证）。

## 9. 被审实现报告的事实性更正（只改表述）

1. §10 F1 的「同一代码重复运行约 2 倍离散」→ 改为同一代码两测（7.99 / 7.912 ms 每工件）一致，
   13.7 ms 一笔来自改动前构建。结论（量级、默认关闭、30 万未测）不变。
2. §7 的 `busy_timeout` 等待值**无需更正**：该处已只引用最终工件值（5 490.8 ms），主审复测
   5 480.6 ms，两者一致（差 10 ms ≈ 忙等待粒度）。

## 10. 主审结论与停止点

- **`ENGINEERING REVIEW FAIL`**：A1 是**本卡交付物自身**的可复现缺陷（报告字段少报），修完并复核
  §6.1 的两个口径前，不写 `ENGINEERING REVIEW PASS`。
- A2/A3 是本卡接线后**才被暴露**的既有面缺口（本次 diff 未触碰相关代码），不属于 P4-02A 允许修改范围，
  须另立小卡或作为 P4-02B 的前置决策；在 A2 上，语义索引在「工件不可读」时会**永久停住且无生产恢复路径**，
  建议优先级高于 P4-02B 的容量宣称。
- 主审未命中 V3.1 §2.2 六类升级条件（根因明确、无跨模块矛盾、无测试/ADR 冲突、无二次回归）；
  无需 DS-MAX，也无需转 C1。
- **下一步（修复轮，仍在 P4-02A 任务内）**：按 §6.1 做最小修复 → 重跑
  §6.1 探针、`--mode busy-timeout`、focused 套件与全量 → 更新报告/状态 → 再由
  **C2 / GPT-5.6 Sol / high** 聚焦终审「入口真实性、并发口径、旧 revision 不回滚、busy-timeout 边界」；
  A2/A3 另立卡后并行推进。模型切换始终由用户在 Codex 中手动完成。
