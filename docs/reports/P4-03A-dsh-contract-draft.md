# P4-03A 阶段 A 报告 — UpgradeRun / 目标集 / 预算 / 暂停恢复 / profile 原子激活契约草拟与工程预审

- 任务卡：[docs/tasks/P4-03A.md](../tasks/P4-03A.md)（阶段 A）
- 阶段：**A（DS-H / DeepSeek V4.1 Flash / 高推理）**
- 状态：**`ENGINEERING CONTRACT REVIEW PASS / SOL FREEZE PENDING`**
- 基线：本地 `d6b6666`（P4-02J 收口提交；`origin/master` 仍停在 `ad37a26`，未 push）
- 日期：2026-09-27
- 下一阶段：**C2 / GPT-5.6 Sol / high** 新会话聚焦终审并冻结（**必选**，不得记 `SOL_NOT_REQUIRED`）

## 0. 真实 Git 基线（开工第一步实测）

```text
$ git log -1 --format='%H %ci'
d6b6666a01ac1d2bbf79748f525be1ddb75bd6a8 2026-09-26 12:46:55 +0800
$ git status --short --untracked-files=all
 M docs/development-status.md          # 用户预先写入的「P4-03A 已立卡」一节（保留）
?? docs/tasks/P4-03A.md                # 本卡（用户放入，未提交）
```

工作树里**没有任何** `src/**`、migration、`pyproject.toml`、`uv.lock`、`PhotoSearch.spec`、
`tools/packaging/**`、`web/**` 的改动。

## 1. 只读事实复核（卡面「开工前必须再复核一遍」）

| 卡面假设 | 本轮实测命令/位置 | 结论 |
|---|---|---|
| `0017` 空闲 | `catalog/schema.py::SUPPORTED_SCHEMA_VERSION == 16`；`migrations/` 最大 `0016_jobs_claim_order.sql` | ✅ `0017` 空闲 |
| `src` 内无升级表/模块 | `rg -i upgrade src` 只命中 `0010_face_core.sql` 的无关注释字串 | ✅ 全部新建 |
| `search_profiles` 引用保护 | `0007_search_profiles.sql`：`image_generation_id → index_generations ON DELETE RESTRICT`；`trg_search_profiles_immutable` 禁止 UPDATE | ✅ 写入一次 + RESTRICT |
| `index_generations` 状态机 | `0003_artifact_outbox_index.sql`：`state ∈ {PENDING,READY,RETIRED}`、`CHECK(is_active=0 OR state='READY')`、`uq_index_generations_active_space`（每 space 至多一个 active） | ✅ 与契约一致 |
| 原子激活地基 | `search/profile.py::activate_profile` 已是单事务同时搬指针与 `is_active`；`activation_hook` 是既有故障注入点 | ✅ 只需加前置门禁 |
| 既有错误码够用 | `catalog/errors.py` + `api/errors.py` 已有 `RUNTIME_CAPABILITY_UNAVAILABLE`/`SOURCE_OFFLINE`/`LEASE_EXPIRED`/`STALE_LEASE`/`ARTIFACT_MISSING`/`DISK_SPACE_INSUFFICIENT`/`SCHEDULER_PAUSED`/`INVALID_STATE_TRANSITION`/`PROFILE_GENERATION_MISMATCH` | ✅ 零新增错误码 |
| 真实权重 / GTX 1660 | 项目内无 `data` 目录/固定权重；本机 **RTX 4060** | ❌ 记 **BLOCKED** |

## 2. 实际改动文件（阶段 A 全部产出）

| 文件 | 类型 | 说明 |
|---|---|---|
| `docs/decisions/ADR-0013-upgrade-run-and-profile-activation-contract.md` | 新建（**Proposed**） | 18 节契约：表形态、run/目标行状态机、目标集差值谓词、预算与软停、控制动作、六条激活门禁、引用保护、失败分类映射、API/UI 最小契约、`0017` 可实施性证明、P4-03B 前置清单、最小反例、变更控制 |
| `docs/decisions/contracts/p4-upgrade-contracts.v1.json` | 新建 | 38 个 `$defs`、16 个公共 object，全部 `additionalProperties: false`；SHA-256 `ebf4553b26b458edcee4b668b9958d7478fa937695fab7f9a8ec7df04ac2f6ea` |
| `tests/unit/test_p4_upgrade_contracts.py` | 新建 | 23 个契约用例（含真实 SQLite 谓词/唯一键/零写入断言） |
| `docs/reports/P4-03A-dsh-contract-draft.md` | 新建 | 本报告 |
| `docs/development-status.md` | 追加一节 | 只写阶段 A 结论与「Sol 待冻结」 |

**未改动**（逐项核对 `git status --short --untracked-files=all`）：`src/photo_search/**` 生产代码、
任何 migration（含 `0017`）、`SUPPORTED_SCHEMA_VERSION`、`pyproject.toml`/`uv.lock`、
`PhotoSearch.spec`、`tools/packaging/**`、`web/**`、ADR-0001～ADR-0012 与**全部**既有 companion、
`taken_time`/`capture_desc`/`SearchFiltersEchoV2`、`POST /api/v1/search` 信封/`query_hash`/游标/错误码。

## 3. 结论表（卡面「契约必须冻结的内容」逐条）

| # | 卡面条目 | 冻结位置 | 结果 |
|---|---|---|---|
| 1 | 表与列形态（列名/类型/可空/默认/闭集 CHECK/PK/唯一键/FK+`ON DELETE`/索引/`control_revision` 语义/权威边界） | ADR §2.1～§2.4 | ✅ 含两条跨列 CHECK 与「不得双权威」表 |
| 2 | run 状态机 + 转换矩阵 + 幂等 + `PAUSE_REASON`/`detail` 规则 | ADR §3.1～§3.3 | ✅ 10 条允许转换 + 10 条点名拒绝；`detail` 匹配 `^[^\u0000-\u001F\u007F/\\:]{1,200}$`，`ENVIRONMENT` 必填 |
| 3 | 目标集差值谓词（既有 revision 口径）/ plan 快照 / `(run_id, asset_id)` 幂等 / `input_revision` 接口 / 目标行状态闭集 / `FAILED.reason` / 重试规则 | ADR §4.1～§4.5 | ✅ 谓词用 `index_entries.confirmation_revision` + `index_outbox.projection_revision`；`input_revision = assets.sha256`；追赶只留接口 |
| 4 | 预算语义（NULL=不限 / 墙钟 PAUSED 冻结 / 并发只界领取 / 任一先到即软停 / 到限不删行不失败 / 可 resume 不重置） | ADR §5.1～§5.4 | ✅ 判定点只在批次边界 |
| 5 | 暂停/恢复/取消（单事务 `BEGIN IMMEDIATE` + 状态谓词 + `control_revision` 自增 / 非法转换 / 幂等重放 / 取消不可 resume） | ADR §6 | ✅ 复用 `catalog/db.py::write()` 单写者事务 |
| 6 | 激活六条门禁 + 失败零写入 + 不新增信封字段 | ADR §7（①～⑤ 可评估 + ⑥ 原子性义务） | ✅ 拒绝码全部既有；零写入用真实前后快照断言 |
| 7 | 引用保护边界（五类引用 / 既有码 / 默认保留前一版 / 可配置 / 不清人工事实或原图） | ADR §8 | ✅ `INVALID_STATE_TRANSITION` + `details.reason=GENERATION_REFERENCED` |
| 8 | 失败分类映射表（零新增错误码、记录位置、可重试性、禁止隐式降级） | ADR §9 | ✅ 7 条 `reason` → 5 个既有码；`IMPLICIT_CPU_FALLBACK`/`SILENT_PROFILE_SWITCH` 明令禁止 |
| 9 | API/UI 最小契约（只读状态 + 控制入口字段闭集 / 错误信封逐字不变 / UI 只展示冻结状态） | ADR §10.1～§10.4 | ✅ 投影字段闭集；信封顶层恰 3 键（+ 可选 `details`）；完整设置页留给 P4-06 |
| 10 | 迁移与兼容契约（`0017`、`16 → 17`、`0001`～`0016` 逐字节不变、只读兼容、未用升级功能行为不变） | ADR §11 | ✅ 16 个迁移哈希由契约测试锁定 |
| 11 | P4-03B 验收前提清单（D 级六项 / B 级 / C 级条件 / BLOCKED） | ADR §12 | ✅ 六项注入与 BLOCKED 逐条写明 |

## 4. ADR 预期与实际的差异（阶段 A 内部的三处收敛）

草拟过程中有三处**预期与实际不符**，已在 ADR 与 companion 内收敛，均为**保守化**，不改变上游契约：

1. **目标集谓词的第二个子句方向**。首稿写成「该三元组没有未确认 outbox 行」作为**排除**条件，
   实际语义应为「未确认 outbox 行本身就让投影**未落定**」。已改为
   `settled = 存在当前 CONFIRMED 条目 ∧ 无未确认 outbox`，目标集是 `settled` 的补集
   （ADR §4.1 的 `NOT EXISTS(...) OR EXISTS(...)`；companion 性质名
   `requires_missing_or_stale_confirmed_entry` + `unsettled_by_unconfirmed_outbox_row`）。
   最小反例：`CONFIRMED @rev 9` + 同三元组 `PENDING(DELETE_VECTOR) @rev 9` —— 首稿会把它判为
   「已覆盖」而漏出目标集；现判为未落定（契约用例 `test_target_set_predicate_...` 的资产 4）。
2. **`VALIDATION_ERROR`/`INTERNAL_ERROR` 的归属**。companion 的 `ErrorCode` 闭集里这两个码不在
   `catalog/errors.py`，而在 `api/errors.py`。措辞已改为「既有结构化错误词汇表（catalog 或 API 层）」；
   契约测试用 `resolve_error_code()` 在两个真实模块里逐码核对，**不新增任何码**。
3. **`RUN_NOT_FOUND` 只能是既有码**。ADR-0002 §9 的最小错误集里没有「升级运行不存在」；
   为守住「零新增错误码」，ADR §10.1 冻结为 422 `VALIDATION_ERROR`（不可解析）与
   409 `INVALID_STATE_TRANSITION`（`details.reason=RUN_NOT_FOUND`，格式合法但不存在），
   并明确「P4-03B 若需要 404 语义必须走 ADR §14 变更控制申请新码」。

另有两次 companion 内部整理（不影响语义）：把 `RunTransitionMatrix`/`TargetTransitionMatrix` 的
`allowed`/`rejected` 从「数组 + 数量」改成**显式 `const` 列表**（逐值可核对），并新增
`ContractIdentity`，使 `RulesVersion`/`SearchContractVersion` 不再成为孤立定义（契约测试
`test_companion_has_no_orphan_definitions` 现在断言 `$defs` 无孤立项）。

## 5. 最小反例（已由契约测试真实执行）

| # | 反例 | 期望 | 用例 |
|---|---|---|---|
| 1 | `CANCELLED → RUNNING`、`SUCCEEDED → RUNNING`、`FAILED → RUNNING`、`PAUSED → SUCCEEDED`、`PLANNED → FAILED`、`RUNNING → PLANNED` | 全部拒绝（`INVALID_STATE_TRANSITION`） | `test_run_transition_matrix_is_exact`、`test_terminal_run_states_never_reopen` |
| 2 | `pause_detail ∈ {"C:\photos","a/b","a\nb","a\tb","a\x00b", 201 字符, ""}` | 全部拒绝 | `test_pause_detail_rule_is_frozen_by_the_companion` |
| 3 | `PAUSED(ENVIRONMENT)` 且 `pause_detail IS NULL` | 拒绝 | `test_run_row_contract_pins_pause_and_wall_clock_rules` |
| 4 | `budget_targets ∈ {0,-1,100000001}`、`budget_seconds=0`、`budget_concurrency ∈ {0,65}` | 全部拒绝；`NULL` 与边界值接受 | `test_budget_rule_boundaries_and_any_first_wins` |
| 5 | 目标行 `state='FAILED'` 且 `reason IS NULL`；`state='PLANNED'` 且 `reason` 非空 | 全部拒绝 | `test_target_rows_require_a_reason_exactly_when_failed` |
| 6 | 同一 plan 对同一资产写第二行；重放改成 `ON CONFLICT DO NOTHING` | 前者被 `PRIMARY KEY(run_id, asset_id)` 拒绝；后者行数与进度不变 | `test_plan_idempotency_is_enforced_by_the_primary_key` |
| 7 | 「已有当前 `CONFIRMED` 覆盖」的资产混进目标集 | 谓词必须不选它 | `test_target_set_predicate_...`（资产 1 在集合外） |
| 8 | 门禁 ①～⑤ 逐条破坏（含 `PENDING_REPAIR`、未确认 outbox、陈旧 `confirmation_revision`、别的 space 覆盖、不可读 artifact、运行时无资格、被取消的回退任务、profile 被换代、`run_id` 不存在） | 逐条返回冻结的 `(code, details.reason)` 且**前后 SQLite 快照逐字相同** | `test_every_activation_gate_refuses_with_zero_writes` |
| 9 | 被 `ACTIVE_PROFILE`/`LIVE_SEARCH_SESSION`/未终态 `UPGRADE_RUN_TARGET`/`ROLLBACK_TASK`/`BACKUP_MANIFEST` 引用的 generation 退役 | 全部被阻止 | `test_reference_protection_blocks_retire_and_cleanup` |
| 10 | 错误信封出现第四个顶层键 / 新增错误码 | 拒绝 | `test_api_projections_are_closed_and_the_error_envelope_adds_no_field`、`test_failure_mapping_uses_only_existing_error_codes` |
| 11 | `assets.state` 想改成非 `ACTIVE` | 被 `0001` 的 CHECK 拒绝（说明「可服务」不构成第二个可写维度） | `test_target_set_predicate_...` |

## 6. 真实命令与结果摘要

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path
uv run --no-sync python -m pytest -q -p no:cacheprovider `
  tests/unit/test_p4_upgrade_contracts.py `
  --basetemp=.pytest-basetemp-p403a/final2 `
  --junitxml=test-results/p4-03a-contracts.xml
# 23 passed  →  errors=0 failures=0 skipped=0 tests=23 (time 0.29s)

uv run --no-sync ruff check .      # All checks passed!  (exit 0)
git diff --check                   # exit 0
uv lock --check                    # Resolved 96 packages in 30ms  (exit 0)
```

- JUnit：`test-results/p4-03a-contracts.xml`（`tests="23" errors="0" failures="0" skipped="0"`）。
- 未跑 B 级全量：本卡**零产品代码改动**，卡面 A 级只要求相关 pytest + ruff + diff-check；
  全量回归留给 P4-03B（以及 Sol 若判定需要）。
- 环境事实：本沙箱内 `uv` 默认缓存目录不可写（`os error 5`），按仓库规则改用项目内 `.uv-cache`；
  pytest 使用全新项目内 `--basetemp`，**未改任何 ACL**。

## 7. 未验证项（不得当成 PASS / 不得由 Sol 扩大解释）

1. 真实模型权重与真实文本塔上的升级吞吐、显存峰值（项目内无权重 ⇒ **BLOCKED**）；
2. GTX 1660 6 GB 最低配置的任何实机数据（本机 RTX 4060 ⇒ **BLOCKED**，不得以 4060 数据替代）；
3. `0017` 在真实 30 万库上的迁移耗时、迁移前备份空间实测；
4. plan 在 30 万资产下的目标集计算耗时与内存；
5. 真实模型切换窗口内的前台争用（§12.2 不属本卡）；
6. 40 万扩容、崩溃窗口内重建幂等、P4-04 的旧版追赶与回退演练；
7. **F2 未关闭、A3 未实现未启用、§12.2 未改、P4-02F 非性能 PASS** —— 本轮不改变这四项。

## 8. 阶段 B 输入 — 压缩交接包（按 V3.1 §2.3）

### 8.1 审查基线与改动文件

- 基线 `d6b6666`；改动文件见 §2（5 个文件，其中 1 个是用户预先放入的任务卡、1 个是用户预先写入的
  `docs/development-status.md` 段落 — 两者都由本轮保留）。
- 结论表见 §3；三处「预期 / 实际」差异见 §4。

### 8.2 仍需 Sol 裁决 / 必须聚焦终审的高风险边界（卡面点名）

1. run 与目标行状态机闭集与转换（`allowed`/`rejected` 的**逐值**清单，尤其
   `PAUSED → SUCCEEDED` 被排除、`IN_FLIGHT → CANCELLED` 被排除、`FAILED → CANCELLED` 被允许）；
2. 目标集差值判定与幂等的**可实现性**：`settled` 的精确形式（ADR §4.1 的
   `NOT EXISTS(current CONFIRMED) OR EXISTS(unconfirmed outbox)`）、
   `PRIMARY KEY(run_id, asset_id)` 与 `ON CONFLICT DO NOTHING`、
   `target_set_digest` 的规范化（`sha256(canonical_json(...))`，按 `asset_id` 升序）；
3. 预算与 `PAUSED(BUDGET)`（墙钟只在 `RUNNING` 累加、`resumed_at` 与 `state` 的绑定、
   判定点只在批次边界、`budget_concurrency` 不是停止条件、resume 不重置）；
4. 取消不可恢复（终态三者的不可逆性）；
5. 激活六条门禁与「失败零写入 + 不新增信封」：①～⑤ 的拒绝码与 `details.reason` 逐条；
   特别请裁决 ADR §10.1 的 `RUN_NOT_FOUND ⇒ 409 INVALID_STATE_TRANSITION` 取舍；
6. 引用保护范围（五类引用，`GENERATION_REFERENCED`，默认保留前一版）；
7. `0017` / `16 → 17` 迁移契约与 `0001`～`0016` 不变承诺；
8. 零新增错误码（`ErrorCode` 闭集的 15 个码全部能在真实模块解析）。

### 8.3 相关契约条款与「预期 / 实际」差异

- ADR-0013 §2（表/列/CHECK/键）、§3.1（矩阵）、§4.1（谓词）、§5.3（软停判定）、§7（门禁）、
  §8（引用保护）、§9（失败映射）、§10.1（`RUN_NOT_FOUND`）、§11（迁移）；
- 差异见 §4 的三处（谓词子句方向、`ErrorCode` 归属措辞、`RUN_NOT_FOUND` 取值），
  均在 ADR/companion 内已收敛，Sol 只需判断收敛是否正确、是否有更保守的替代。

### 8.4 关键 diff 的文件与符号定位（不复制完整 diff）

| 文件 | 位置 | 内容 |
|---|---|---|
| `docs/decisions/ADR-0013-*.md` | §2.1 `upgrade_runs` 表 / §2.2 `upgrade_targets` 表 | 两条跨列 CHECK（`PAUSED ↔ pause_reason`、`RUNNING ↔ resumed_at`）与 `FAILED ↔ reason` |
| 同上 | §3.1 表 | 10 行允许转换 |
| 同上 | §4.1 代码块 | `TARGET_SET_SQL` 的 `NOT EXISTS(...) OR EXISTS(...)` |
| 同上 | §5.3 代码块 | `stop := targets_or_seconds` |
| 同上 | §7 表 | ①～⑤ 的拒绝码/`details.reason` + ⑥ 原子性 |
| 同上 | §10.1 | `RUN_NOT_FOUND` 的 409 取舍 |
| `docs/decisions/contracts/p4-upgrade-contracts.v1.json` | `$defs/RunTransitionMatrix`、`$defs/TargetTransitionMatrix` | 两个矩阵的 `const` 列表 |
| 同上 | `$defs/TargetSetPredicate`、`$defs/BudgetRule`、`$defs/ActivationGateMatrix`、`$defs/FailureMapping`、`$defs/ReferenceProtection`、`$defs/MigrationContract` | 语义均为 `const` |
| `tests/unit/test_p4_upgrade_contracts.py` | `TARGET_SET_SQL` / `coverage_complete` / `activation_refusal` / `retire_blockers` / `catalog_snapshot` | 参考模型与真实 SQLite 断言 |

### 8.5 最小反例与命令结果

见 §5（11 组）与 §6（23/0/0/0 + ruff/diff-check/lock-check 全 exit 0）。

### 8.6 环境事实与未验证项

见 §6 末段与 §7。

### 8.7 明确禁止 Sol 扩大的范围（阶段 B 只读）

- 不重扫全仓、不重读两份架构文档、不重跑 B 级全量成功套件（本卡零产品改动）；
- 不修改任何冻结面：`0001`～`0016` 字节、ADR-0001～ADR-0012、既有 9 个 companion、
  `taken_time`/`capture_desc`/`SearchFiltersEchoV2`、`POST /api/v1/search` 的任何字节、
  RRF/BM25/距离、P1～P3 人工事实、`pyproject.toml`/`uv.lock`、打包面；
- 不在阶段 B 顺手实现 `0017`、`src/photo_search/upgrade/**`、API/UI 或任何 D 级注入
  （那是 P4-03B，且必须在 P4-03A 通过后才允许生成该卡）；
- 不把本卡的「契约通过」表述成「升级功能可用」。

## 9. 停止点与下一步

- 阶段 A 结束；**ADR-0013 保持 `Proposed`**，**未标 P4-03A PASS**，**未生成 `docs/tasks/P4-03B.md`**；
- 请**手动**在 Codex 中切换到 **C2 / GPT-5.6 Sol / high** 新会话执行阶段 B 聚焦终审；
- 阶段 B 通过后才允许：ADR 转 Accepted、标 P4-03A PASS、生成 `docs/tasks/P4-03B.md`。

> 下一任务阶段 B 建议使用 C2 / GPT-5.6 Sol / high，请在 Codex 中手动切换模型/推理档后继续。

**§2.2 升级判定（阶段 A）**：未命中 V3.1 §2.2 六类具名条件；阶段 B 若提出需要大范围反查，
按第 6 条升 **DS-MAX**。
