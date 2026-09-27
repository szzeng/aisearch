# P4-03A 修订面报告 — 阶段 B 失败边界的 DS-H 最小闭合

- 任务卡：[docs/tasks/P4-03A.md](../tasks/P4-03A.md)（阶段 A / 阶段 B）
- 阶段：**修订面（DS-H）**，闭合 [阶段 B 聚焦终审](P4-03A-sol-contract-freeze.md) 的 F-A～F-E 与 O-1～O-3
- 状态：**修订面完成，仍非 PASS**；ADR-0013 保持 **Proposed**，**未生成 `docs/tasks/P4-03B.md`**
- 被审基线：阶段 A 提交 `33b01a2`；阶段 B 报告基线 `d370022`；本轮从工作树（含 Sol 未提交的报告/卡/状态）继续
- 日期：2026-09-27

## 0. 真实 Git 状态（开工实测）

```text
HEAD = d370022（本地 master 比 origin/master 领先 3 个提交；未 push）
开工时未提交/未跟踪（全部保留，未修改其内容）：
  M docs/development-status.md          # 另一会话的「全项目重评」与「P4-03A 阶段 B FAIL」两节
  M docs/tasks/P4-03A.md                # 阶段 B 结果块
  ?? docs/reports/P4-03A-sol-contract-freeze.md
  ?? docs/reports/2026-09-27-project-search-reevaluation.md   # 无关并发工作
  ?? docs/tasks/P4-02L.md                                      # 无关并发工作
本轮只改：ADR-0013、companion、契约测试；另新增本报告与状态一节。
```

## 1. 结论表（逐条闭合）

| 编号 | 阶段 B 的阻断 | 修订点 | 证据 |
|---|---|---|---|
| **F-A** | plan 后新增 ACTIVE 资产缺覆盖但无 pending 行，门禁 ③ 仍放行 | ADR §4.2 新增第 5 条（plan 快照 ≠ 激活完整性）；§7 门禁 ③ 改为**在激活时重新评估当前差值集**（作用于全部 ACTIVE 资产）；companion `TargetSetRule.activation_reevaluates_current_difference_set` + `ActivationGateMatrix.activation_reevaluates_difference_set` | `test_activation_reevaluates_the_current_difference_set`（三例：plan 后新增资产 / 空差值快照后新增 / 已覆盖资产丢失覆盖；均拒绝且快照不变，补齐后才通过） |
| **F-B** | `request_id` 幂等无持久载体、无请求 schema、replay 与 revision 顺序未冻结 | 新增 ADR §2.3 `upgrade_control_requests` 表（列/键/三条 CHECK/索引）；§3.2 重写为「**先查找 `(run_id, request_id)`**，同义重放不做状态与 revision 判定；`changed=0` 的首次同义请求也落一行」；digest 字段闭集**不含** `expected_control_revision`；§10.2 冻结 `$defs/UpgradeControlRequest` 闭集请求；§11 的 `0017` 改为**三张表** | `test_control_request_schema_is_closed`、`test_control_request_idempotency_is_durable_and_replay_first`（R1 pause → R2 resume → 重放 R1：返回记录结果 + `replayed=true` + 零写入 + run 仍 RUNNING；同 key 换 intent 拒绝；`changed=0` 落行） |
| **F-C** | 到限后 resume 无法继续；连续 RUNNING 时秒预算永不触发；重启记账不唯一 | §5.2 重写为**两段账本**：`elapsed_ms`（结账段）+ `(now - resumed_at)`（仅 `clock_owner = 本会话` 时计入）⇒ §5.3 用 `effective_elapsed_ms`；**重启必须先重锚**且**停机区间不计入**；新增 `clock_owner` 列与「与 `resumed_at` 同生同灭」CHECK；§5.4 + §6 新增**显式额度授权**（`grant_targets`/`grant_seconds`，增量、同一事务、超上界拒绝、不重置已完成与 `elapsed_ms`）；§5.3 冻结批次边界**判定顺序**（完成优先于到限） | `test_budget_grant_and_wall_clock_ledger`（额度算术与三类拒绝；耗尽→裸 resume 领取 0；带 grant 的 resume 使同一 run 继续；两段账本与重锚；`settle_on_leave` 非 owner 拒绝；边界顺序四种组合） |
| **F-D** | companion 只限制词汇，未约束组合，也不要求矩阵成员完整 | `$defs/FailureRow` 改为 **7 个 `const` 行**的 `oneOf`；`$defs/ActivationGateRow` 改为 **5 个 `const` 行**（`refusals` 逐值列出该门禁允许的 `(code, details_reasons)`，门禁⑤保留两条真实分支）；`FailureMapping.rows`/`control_plane_refusals` 与 `ActivationGateMatrix.gates` 加 `uniqueItems` + 精确项数 ⇒ 重复/缺项/合法但错配都被拒 | `test_failure_mapping_uses_only_existing_error_codes`（原始负例：错码）、`test_activation_gate_matrix_pins_five_gates_plus_atomicity`（四类错配 + 重复行 + 缺项 + 词汇表缺项） |
| **F-E** | 过期租约同时被允许「零计次重放」与「计一次失败」 | `upgrade_targets` 新增 `lease_token`/`lease_expires_at` 与「仅 `IN_FLIGHT` 携带租约」的对称 CHECK；§4.4 明确 `IN_FLIGHT → PLANNED` **仅限租约仍有效**，过期接管**唯一路径**是 `IN_FLIGHT → FAILED(LEASE_EXPIRED)`；§4.5 把 `attempts` 收成**唯一自增点**：进入 `FAILED` 且 §9 该 `reason` 的 `counts_toward_attempts = true`；显式重试与有效租约重放都不加，重放同一接管计零 | `test_expired_lease_takeover_counts_exactly_once`（四类 `attempt_delta`；DDL 拒绝带租约离开 `IN_FLIGHT`；接管一次 +1；重放计零；显式重试不再加） |
| **O-1** | 门禁⑤复用的既有 readiness 拒绝的 `details` 键不在新闭集里 | `$defs/UpgradeApiErrorDetails` 按**真实错误对象**逐键补齐（`runtime_package_id`/`processor_id`/`capability_revision`/`health_status`/`role`/`profile_id`/`generation_state`/`image_generation_id`/`profile_space_id`/`generation_space_id`/`profile_processor_id`/`generation_processor_id`/`declared_method`/`declared_tokenizer_version`/`supported_tokenizer_version`/`declared_fts_generation_id`/`supported_fts_generation_id`）；ADR §7 明确复用拒绝**逐字保留**、不投影不裁剪 | `test_reused_refusal_details_keys_are_inside_the_frozen_error_details`（用真实 `search/profile.py` 的 8 条拒绝路径收集键集，断言 ⊆ 冻结键集） |
| **O-2** | 参考实现把门禁放在写事务外；可读性只 `is_file()`；无回滚证据 | ADR §7 明确**门禁与指针同一 `BEGIN IMMEDIATE` 事务**、禁止两段式；门禁④要求**真实打开读取**；参考实现改为显式 `BEGIN IMMEDIATE` + `COMMIT/ROLLBACK` + 故障注入点 | `test_activation_is_one_transaction_and_rolls_back_on_fault`（事务内搬完指针后抛异常 ⇒ 指针与 `is_active` 回滚）、`test_artifact_readability_is_a_real_read`（目录存在但不可作为文件读取 ⇒ `ARTIFACT_MISSING` 且零写入） |
| **O-3** | 文字禁 C1，正则只排除 C0 与 DEL | ADR §3.3 与 companion 全部四处 `pause_detail` 正则改为 `^[^\u0000-\u001F\u007F-\u009F/\\:]{1,200}$`；`PauseDetailRule.forbidden_characters` 增列 `C1` | `test_pause_detail_rule_is_frozen_by_the_companion`（新增 `\x7f`/`\x85`/`\x9f` 负例） |

## 2. 预期 / 实际差异（本轮的三处真修正）

1. **F-A 是语义缺口，不是措辞**：阶段 A 的门禁 ③ 以「plan 目标集被覆盖 + 全代无 pending」为界，实际必须
   以「**当前**差值集为空」为界 —— 后者能覆盖 plan 之后新增的资产，代价是切换前需要写入静默窗口
   （已写进 ADR §17 的代价节）。
2. **F-B 让 `0017` 从两张表变成三张表**：阶段 A 认为「用当前 state 反推幂等」足够，实际不行
   （`pause(R1)→resume(R2)→重放 R1` 会二次暂停）。持久载体是唯一的解，且 digest 必须排除
   `expected_control_revision`，否则旧 revision 的同义重放会被误判为冲突。
3. **F-C 暴露记账口径的空洞**：阶段 A 只在「离开 RUNNING」结账，导致连续 RUNNING 时秒预算
   永不触发；补两段账本后又暴露「重启把停机算成运行时间」的过度计数 —— 两者都由
   `clock_owner` + 重锚规则唯一确定。

另有一处由本轮新写的用例**反向发现**的 companion 漏洞：`UpgradeControlRequest` 的
「`PAUSE` 必须有 `pause_reason`」与「`ENVIRONMENT` 必须有 `pause_detail`」两条规则只写了 `properties`
约束，缺 `required`，因此「字段缺席」能通过；已补 `required`（这也是 F-B 要求「请求闭集」的直接含义）。

## 3. 修改面清单

| 文件 | 修改 |
|---|---|
| `docs/decisions/ADR-0013-*.md` | 状态行；§2.1（`clock_owner` + 三条跨列 CHECK）；§2.2（租约列 + `attempts` 语义 + 第二条 CHECK）；**新增 §2.3 `upgrade_control_requests`**（原 §2.3/§2.4 顺延为 §2.4/§2.5）；§3.2（重写）；§3.3（C1）；§4.2（第 5 条）；§4.4/§4.5（租约与计次）；§5.2（两段账本）；§5.3（`effective_elapsed_ms` + 判定顺序）；§5.4（额度授权）；§6（resume 可带授权 + replay 顺序 + 重锚）；§7（门禁③/④/⑤、同一事务、既有 details 逐字保留、回滚注入）；§10.2（请求闭集与顺序）；§11（三张表）；§12（新增 4 项注入）；§13（新增 9 条反例）；§15（阶段 B FAIL + 本轮修订面）；§16（新哈希 + 用例清单）；§17（三处代价） |
| `docs/decisions/contracts/p4-upgrade-contracts.v1.json` | 新 `$defs`：`ClockRule`/`LeaseRule`/`AttemptsRule`/`ControlIdempotencyRule`/`BudgetGrantRule`/`UpgradeControlRequest`/`UpgradeControlRequestRow`/`ContractIdentity` 已有；`ActivationGateRow` 与 `FailureRow` 改 `const` 并集；`ActivationGateMatrix` 增 `gate_vocabulary`/同一事务/真实读取/重评估三项；`BudgetRule` 增 `completion_precedes_budget_pause`；`TargetSetRule` 增 `activation_reevaluates_current_difference_set`；`UpgradeRunRow` 增 `clock_owner`；`UpgradeTargetRow` 增租约两列；`UpgradeApiErrorDetails` 补 17 个既有键；`MigrationContract` 增 `table_names`；四处 `pause_detail` 正则加 C1。SHA-256：`06e413754938ecdb9d1bda0136e186b8f29773069094cffcdeeb7f6cb510da48` |
| `tests/unit/test_p4_upgrade_contracts.py` | 参考 DDL 增 `clock_owner`/租约/第三张表；`coverage_complete` 改为**当前差值集**；`reference_activate` 改为单事务 + 故障注入；可读性改真实读取；新增 8 个用例与配套参考模型（控制幂等、额度、两段账本、计次、真实拒绝键集） |

**零改动**：`src/**`、迁移与 `SUPPORTED_SCHEMA_VERSION`、既有 ADR/companion、`POST /api/v1/search`
信封/`query_hash`/游标/错误码、RRF/BM25/距离、P1～P3 人工事实、`pyproject.toml`/`uv.lock`、打包面、§12.2。

## 4. 验收与命令

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path
uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit/test_p4_upgrade_contracts.py `
  --basetemp=.pytest-basetemp-p403a/repair-final `
  --junitxml=test-results/p4-03a-repair-contracts.xml
uv run --no-sync ruff check .
git diff --check
uv lock --check
```

- 契约 **31 / 0 / 0 / 0**（`tests="31" errors="0" failures="0" skipped="0"`，阶段 A 为 23）。
- `ruff check .` → All checks passed!；`git diff --check` → exit 0；`uv lock --check` → exit 0。
- `0017` 仍**不存在**；`SUPPORTED_SCHEMA_VERSION` 仍为 16（用例断言）。

## 5. 未验证项 / BLOCKED

- 本卡仍是**契约面**：D 级六项（现为十项）注入、真实迁移、真实并发与中途故障、真实磁盘不可读、
  30 万 plan 成本、真实模型权重与 GTX 1660 —— 全部留给 P4-03B / C1，均为 **BLOCKED 或未测**。
- 参考实现里的「事务内故障回滚」与「真实读取」只证明**契约层**可执行，不构成产品实现证据。
- **F2 未关闭、A3 未实现未启用、§12.2 未改、P4-02F 非性能 PASS** —— 本轮不改变这四项。

## 6. 阶段 B 第二轮短审的压缩交接包

- **修订面 diff 定位**：ADR-0013 的 §2.1/§2.2/§2.3（新）/§3.2/§3.3/§4.2/§4.4/§4.5/§5.2/§5.3/§5.4/§6/§7/§10.2/§11/§12/§13/§15/§16/§17；companion 的上述 `$defs`；测试的 `coverage_complete`/`reference_activate`/`activation_refusal` 与新增 8 个用例。不复制完整 diff。
- **必审边界**（只判是否闭合原失败点，不重推既有通过面）：
  1. F-A：门禁 ③ 的界从「plan 目标集」改成「**当前**差值集」是否足够、是否引入新歧义；§17 的「代价：需要写入静默窗口」是否可接受；
  2. F-B：第三张表的键/CHECK/索引与 digest 字段闭集（**排除** `expected_control_revision`）、replay 先于状态与 revision 判定、`changed=0` 也落行 —— 是否闭合原反例；
  3. F-C：两段账本 + `clock_owner` 重锚（停机不计、不得多记）、`effective_elapsed_ms` 判定、额度授权的算术/上界/同事务、批次边界「完成优先于到限」；
  4. F-D：`FailureRow`/`ActivationGateRow` 的 `const` 并集与 `uniqueItems` 是否真的拒绝「合法词汇 + 错配/重复/缺项」；
  5. F-E：唯一接管路径与 `attempts` 唯一自增点是否互斥且完备（含重放计零、显式重试不再加）；
  6. O-1：`UpgradeApiErrorDetails` 的键集是否覆盖真实 readiness/binding 拒绝（用例已对真实错误对象逐一核对）；
  7. O-2：门禁与指针同一事务、真实读取、回滚注入；
  8. O-3：C1。
- **最小反例**：见 §1 各行「证据」列点名的用例（均为可执行断言，失败即给出最小复现）。
- **明确禁止扩大范围**：不得要求本卡实现 `src/**`、迁移、API/UI、D 级注入或真实模型；不得重开
  F2/A3/§12.2 讨论；不得把本修订面当作 P4-03A PASS。若终审认为需要更大改动，按 V3.1 §2.2 第 6 条
  交回 DS-MAX/DS-H，而不是在本轮顺手扩大。

## 7. 停止点

- 修订面完成；**ADR-0013 保持 Proposed**、**P4-03A 未 PASS**、**未生成/未启动 P4-03B**；
- 下一轮为 **C2 / GPT-5.6 Sol / high 聚焦短审**（只审上述修订面与直接回归）；
  模型切换由用户在 Codex 中手动完成。

## 8. 第二轮修订 — 第二轮聚焦短审的 F-B1 / F-B2（2026-09-27）

触发：[P4-03A 阶段 B 报告](P4-03A-sol-contract-freeze.md) §6（第二轮短审）。该轮确认 F-A / F-D / F-E 文字面 /
O-1 / O-2 / O-3 **已闭合**，只剩两处与 F-B 直接相连的语义未闭合，外加一处措辞。

### 8.1 F-B1 — 重放响应的 `changed` 语义统一

- **问题**：ADR §3.2 与参考模型回 `changed = 原请求的 changed`，而 companion 的
  `ControlIdempotencyRule.replay_response` 与 `UpgradeControlResponse` 要求
  `changed = false, replayed = true` ⇒ 完整响应在 companion 处被判非法。
- **裁决（本次冻结）**：**响应的 `changed` 只表示「本次调用是否写了 run」**；「原请求是否改变过 run」
  由日志行的 `changed` 列单独保存（companion 新增 `response_changed_means` / `log_changed_means` 两个键把两者
  分开写死）。因此重放恒为 `changed = false, replayed = true`。
- **用例**：`test_control_response_changed_semantics` 把 首次 / no-op / 重放 三种**完整响应**分别送进
  `UpgradeControlResponse` 校验（三正例），并把 `changed=true ∧ replayed=true` 与
  `replayed=true` 的首次响应作为两负例；另断言日志行保留的是**原请求**的 `(changed=1, PAUSED, rev 2)`。

### 8.2 F-B2 — 首次同义 no-op 不得改 run；参数不一致必须拒绝

- **问题**：参考模型对 `changed = false` 的请求仍执行无条件 UPDATE ⇒ `PAUSED(BUDGET)` 上再 `pause(USER)`
  会把原因改成 USER、把 `updated_at` 推后；`RUNNING` 上 `resume(grant_targets=2)` 被接受为
  `changed=false` 并**静默丢弃授权**（digest 却已含该授权）。
- **裁决（本次冻结）**：
  1. **真正的首次同义请求**（目标状态已成立**且**参数与已成立状态一致）**只落日志行**，
     **不得 UPDATE `upgrade_runs` 的任何列**（含 `updated_at`/`pause_reason`/`pause_detail`）；
     同义的定义逐字冻结：`PAUSE` 比 `pause_reason`+`pause_detail`；`RESUME` 要求**不带** `grant_*`；
     `CANCEL` 无参数；
  2. 目标状态已成立但**参数不一致**（`PAUSED(BUDGET)` 上 `pause(USER)`、`RUNNING` 上带 `grant_*` 的 `resume`）
     ⇒ 既有 `INVALID_STATE_TRANSITION`（`details.reason = CONTROL_NOOP_ARGUMENT_MISMATCH`）、
     **零写入且不落日志行**，**禁止**「成功返回但静默丢弃参数」。
- **用例**：`catalog_snapshot` 改为**整行**快照（`SELECT * FROM upgrade_runs`），新增
  `control_log_rows()`；no-op 段断言「run 整行不变 + 日志恰好多一行」，两处参数不一致断言
  「run 整行不变 + 日志行数不变」。
- **§5.4 措辞**：把「不重置 `elapsed_ms`/`resumed_at`/`clock_owner`」改成
  「不重置**累计数**（已完成数、已结账 `elapsed_ms`）；`resumed_at`/`clock_owner` 由**本次**进入 `RUNNING`
  的会话按 §5.2 新建」，并补「`RUNNING` 上带 `grant_*` 必须拒绝」。

### 8.3 F-E 的补充执行（响应 Sol「参考用例未真正执行第二次接管」）

新增 `takeover_expired_lease()` 参考实现并真实调用两次：租约未到期 ⇒ `LEASE_STILL_VALID`（零计次重放）；
到期 ⇒ `IN_FLIGHT → FAILED(LEASE_EXPIRED)` 且 `attempts` 恰 +1；**第二次调用**同一行 ⇒
`TAKEOVER_NOT_ELIGIBLE`，`attempts` 仍为 1。

### 8.4 修改面（本轮）

| 文件 | 修改 |
|---|---|
| ADR-0013 | §2.3（日志 `changed` 语义）、§3.2（响应 `changed` 语义 + 首次同义的具体定义 + 参数不一致的拒绝码）、§5.4（累计数 vs 锚点措辞 + `RUNNING`+grant 拒绝）、§10.2（响应语义）、§13（新增 4 条反例 20～23）、§16（新哈希） |
| companion | `ControlIdempotencyRule.const` 增 `response_changed_means` / `log_changed_means` / `noop_rule` / `noop_parameter_mismatch`；SHA-256 `bdf8180af0d9f535d966ea02c05a3a2deab101140606d68fd5630e67888197b5` |
| 契约测试 | `catalog_snapshot` 改整行 + `control_log_rows()`；`apply_control_request` 返回**完整响应**、重放回 `changed=false`、新增 `noop_matches()` 与参数不一致拒绝、no-op 只写日志；新增 `test_control_response_changed_semantics`、`takeover_expired_lease()` 及其在 F-E 用例中的第二次真实接管 |

### 8.5 验收

```powershell
uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit/test_p4_upgrade_contracts.py `
  --basetemp=.pytest-basetemp-p403a/repair2-final `
  --junitxml=test-results/p4-03a-repair2-contracts.xml
uv run --no-sync ruff check .
git diff --check
uv lock --check
```

- 契约 **32 / 0 / 0 / 0**（`tests="32" errors="0" failures="0" skipped="0"`；第一轮修订为 31，阶段 A 为 23）。
- Ruff / `git diff --check` / `uv lock --check` 全 exit 0；`0017` 仍不存在；ADR §16 的哈希与文件一致
  （由用例断言）。
- 零改动：`src/**`、迁移、`SUPPORTED_SCHEMA_VERSION`、既有 ADR/companion、搜索信封/`query_hash`/错误码、
  P1～P3 人工事实、依赖与打包面、§12.2。

### 8.6 下一轮短审输入（只审这两点与直接回归）

1. **F-B1**：响应 `changed` 与日志 `changed` 的语义分离是否在 ADR / companion / 参考模型 / 用例四处一致；
   三种完整响应是否都能通过 `UpgradeControlResponse`；禁止配对是否被拒；
2. **F-B2**：首次同义 no-op 是否真的「run 整行不变、只多一行日志」；两处参数不一致（PAUSED 换 reason、
   RUNNING 带 grant）是否零写入且不落日志；§5.4 的锚点措辞是否唯一可判；
3. 只读直接回归：F-A / F-C / F-D / F-E / O-1～O-3 的既有用例仍应全绿（本轮未改它们的判据）。

## 9. 第三轮修订 — 第三轮短审的 F-B2-G（2026-09-27）

触发：[P4-03A 阶段 B 报告](P4-03A-sol-contract-freeze.md) §7（第三轮短审）。该轮确认 F-B1、F-B2 原反例、
§5.4 锚点措辞与 F-E 补充执行**均已闭合**，只剩一处直接漏分支。

### 9.1 F-B2-G — 额度授权缺「源状态必须是 PAUSED」的前置条件

- **问题**：ADR §5.4 与 companion 已写「带 `grant_*` 的 resume 只在 `PAUSED → RUNNING` 合法」，
  但参考模型只判 `changed and new_state == 'RUNNING'`、没核验**源状态**；`PLANNED → RUNNING`
  本身在通用转换矩阵内，于是 `PLANNED + grant_targets`（或 `grant_seconds`）被放行：预算 1 → 3、
  `control_revision` +1、并落一行带授权的日志。Sol 用数量与秒数两种夹具独立复现。
- **裁决（本次冻结）**：新增、带 `grant_*` 的请求在其状态判定之后、**任何 run/日志写入之前**必须核验
  **源状态 = `PAUSED`**；否则既有 `INVALID_STATE_TRANSITION`（409，
  `details.reason = GRANT_SOURCE_STATE_NOT_PAUSED`）、**零写入**。三种落空形态在 ADR §5.4 与
  companion `BudgetGrantRule.const` 内逐字冻结：
  ① 非 `PAUSED` 源 + `grant_*` ⇒ `GRANT_SOURCE_STATE_NOT_PAUSED`；
  ② 已是 `RUNNING` 再带 `grant_*` ⇒ 仍按 §3.2 的 `CONTROL_NOOP_ARGUMENT_MISMATCH`（不改变第二轮已冻结的行为）；
  ③ 两者都不得「成功返回并静默丢弃授权」。
  一般 `PLANNED → RUNNING` 启动转换**不变**；**已命中的同义重放不受影响**（重放查找仍在最前面，
  成功授权后在 `RUNNING` 重放同一 key 是零写重放）。
- **用例**：新增 `test_budget_grant_requires_a_paused_source_state`：
  `PLANNED + grant_targets` 与 `PLANNED + grant_seconds` 两例均拒绝且 **run 整行 + 日志行数不变**；
  同一 `PLANNED` 夹具上不带授权的 `resume` 仍能正常启动（证明规则没有误伤通用转换）；
  `PAUSED + grant_targets` 成功后上限 1 → 3，且用同一 key 在 `RUNNING` 重放 ⇒ `changed=false,
  replayed=true`、快照与日志不变、上限仍为 3（不二次授权）。

### 9.2 修改面（本轮）

| 文件 | 修改 |
|---|---|
| ADR-0013 | §5.4（源状态前置条件 + 三种落空形态）、§6 的 resume 行、§13 新增第 24 条反例、§16 哈希 |
| companion | `BudgetGrantRule.const` 增 `allowed_source_state: "PAUSED"` 与 `source_state_mismatch`（`INVALID_STATE_TRANSITION` + `GRANT_SOURCE_STATE_NOT_PAUSED` + 零写）；SHA-256 `f92dece59b1a16e896af68a2c0484b5b061ce004e62fbe3245a6827dea4469e8` |
| 契约测试 | `apply_control_request` 增源状态核验；新增 `_planned_fixture()` 与 `test_budget_grant_requires_a_paused_source_state` |

### 9.3 验收

```powershell
uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit/test_p4_upgrade_contracts.py `
  --basetemp=.pytest-basetemp-p403a/repair3-final `
  --junitxml=test-results/p4-03a-repair3-contracts.xml
uv run --no-sync ruff check .
git diff --check
uv lock --check
```

- 契约 **33 / 0 / 0 / 0**（`tests="33" errors="0" failures="0" skipped="0"`；第二轮 32，第一轮 31，阶段 A 23）。
- Ruff / `git diff --check` / `uv lock --check` 全 exit 0；`0017` 仍不存在；ADR §16 哈希与 companion 文件一致
  （由用例断言）。
- 零改动：`src/**`、迁移、`SUPPORTED_SCHEMA_VERSION`、既有 ADR/companion、搜索信封/`query_hash`/错误码、
  P1～P3 人工事实、依赖与打包面、§12.2。

### 9.4 下一轮短审输入（只审这一分支与直接回归）

1. `PLANNED`（及任何非 `PAUSED`）源状态 + `grant_targets` / `grant_seconds`：是否都按
   `GRANT_SOURCE_STATE_NOT_PAUSED` 拒绝且 run 整行与日志行数不变；
2. 不带授权的 `PLANNED → RUNNING` 是否仍正常启动；已是 `RUNNING` 再带 `grant_*` 是否仍是
   `CONTROL_NOOP_ARGUMENT_MISMATCH`；成功授权后同 key 在 `RUNNING` 重放是否零写且不二次授权；
3. 只读直接回归：F-A / F-B1 / F-B2 / F-C / F-D / F-E / O-1～O-3 的既有用例仍应全绿。
