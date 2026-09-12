# P1-01 — C2 final re-review 返修（DS-H）

任务：P1-01 — 资产 / 位置 / 来源 / 扫描最小持久任务 / 迁移与约束
阶段：**stage 6 — DS-H 返修**（对应 `docs/reports/P1-01-c2-final-rereview.md` 的 F1 / F2）
状态：**FIX IMPLEMENTED / D 级复验完成 / 等待 C2 最终短复核**（本报告不代表 PASS）
执行模型：由用户在当前 Codex 会话手动选择 DeepSeek 高推理档；未切换、未声称切换模型
返修基线：`8393583`（C2 final re-review 提交，工作树干净）
冻结契约：`docs/decisions/ADR-0002-catalog-identity-migration-scan-contract.md`（本轮未修改）

## 1. 结论摘要

F1（恢复/耗尽的终态事务边界与 job↔scan_run 联动）与 F2（同一 `scan_id` 多次 execution 共用
`last_seen_scan_id` 污染 missing 判定）均已修复，并按 C2 §5 要求新增生产路径回归测试。
未修改资产/来源身份、`(source_id, path_key)` 唯一性、五字段 job 逻辑键、missing 原子边界、原图只读规则，
也未新增迁移（schema 仍为 v1）。

## 2. F1 修复

### 2.1 耗尽的 FAILED 不再被外层事务回滚

- `src/photo_search/ingestion/scanner.py`：`execute_scan()` 的领取步骤现在在**同一个写事务内**捕获
  `RETRY_EXHAUSTED`，在该事务内同步把 `scan_run` 置 `FAILED`（`store.ensure_scan_failed`），事务正常提交后
  才在事务外重新抛出异常。`jobs._start_running()` 写入的 job `FAILED`/attempts/清租约因此会真正落库，
  不再被 `Catalog.write()` 的回滚路径撤销。
- 非耗尽的领取错误（`LEASE_CONFLICT` 等）仍直接传播并回滚，不产生任何状态副作用。

### 2.2 job 终态必须同步 `scan_run`

- `src/photo_search/ingestion/scanner.py::_requeue()`：读取 `jobs.finish(...)` 的**真实返回状态**；若可重试失败
  达到上限被降级为 `FAILED`，在同一事务内调用 `store.ensure_scan_failed()` 把 `scan_run` 置 `FAILED`，
  并抛出 `RETRY_EXHAUSTED`（`details.reason_code` 保留原始原因，如 `INPUT_UNSTABLE`）。
- `src/photo_search/jobs/queue.py`：`fail_exhausted_leases()`（原 `_fail_exhausted_leases`，现为公开函数）
  先选出预算耗尽的过期执行，再更新 jobs，并在**同一事务**内通过 `_sync_subject_state()` 把
  `subject_type='scan_run'` 的对应 `scan_run` 同步为 `FAILED`（机器恢复元数据统计，不涉及业务身份）。
  `claim_next()` 使用该函数，因此通用调度路径也不会留下 `job=FAILED / scan_run=RUNNING`。
- `src/photo_search/catalog/store.py`：新增 `ensure_scan_failed()`（幂等）；`ALLOWED_SCAN_TRANSITIONS` 允许
  `QUEUED -> FAILED` 与 `BLOCKED -> FAILED`，以满足 ADR-0002 §3.3「job 进入终态时 scan_run 必须在同一业务
  提交路径进入同名终态」。普通 `RUNNING -> RUNNING` 自环仍然禁止。

## 3. F2 修复

### 3.1 coverage 改为“当前 execution 所有”

- `src/photo_search/catalog/store.py`：新增 `reset_execution_coverage(source_id, scan_id)`，
  `UPDATE file_locations SET last_seen_scan_id = NULL WHERE source_id = ? AND last_seen_scan_id = ?`。
- `src/photo_search/ingestion/scanner.py`：每次成功领取（即开始一次新的 execution）后，在**同一事务**内调用
  该函数；因此 `last_seen_scan_id = scan_id` 只可能表示“**本次 execution** 看到了它”。
- 只清除“扫描归属”标记：位置行、`size/mtime_ns`、`availability`、`asset_id` 绑定这些正向事实全部保留，
  与 ADR-0002 §5.1「正向事实可增量提交」不冲突；被清理的是**上一 execution 的覆盖标记**，不是观察事实本身。
- 由此：恢复执行重新完整枚举时，被删除/改名且本 execution 未看到的位置（包括上一 execution 只留下
  `asset_id IS NULL` 的行）都会在最终 `reconcile_missing()` 中正确进入 `MISSING`；
  最终 `count_unbound_locations()` 守卫只看本次 execution 的行，不会因上一 execution 的 pending 行永久卡住。
- 未新增 execution 表/列，未改变 `file_locations` 的冻结字段集合与 `(source_id, path_key)` 唯一性。

### 3.2 统计口径随动

- `catalog_counts()` 的 `locations_pending_content` 现在只统计 `availability='PRESENT' AND asset_id IS NULL`；
  `MISSING` 位置通过 `locations_missing` 报告（其 `asset_id` 是“最后已知内容”，可能为空）。这不改变任何
  数据身份或缺失判定语义，只让 status 输出与 `ScanOutcome.locations_pending_content`（成功恒为 0）一致。

## 4. 新增回归测试（C2 §5 要求的生产路径）

| C2 要求 | 测试 |
|---|---|
| 1. 真实 `execute_scan()` + expired lease + max_attempts（不在写事务内人为 catch） | `tests/integration/test_catalog_scan.py::test_expired_lease_exhaustion_fails_job_and_scan_on_the_production_path`：`run_scan(lease_seconds=0)` 后 job 仍 RUNNING；越过租约后真实 `run_scan()` 抛 `RETRY_EXHAUSTED`，**持久状态**为 job `FAILED`（attempts=1、`LEASE_EXPIRED`、租约清空）+ `scan_run FAILED`（`finished_at` 已写、`error` JSON code 为 `RETRY_EXHAUSTED`）；随后 `claim_job` 返回 `INVALID_STATE_TRANSITION`，`start_scan` 创建新的 `scan_id=2`（不复用失败身份） |
| 2. 连续 `INPUT_UNSTABLE` 直到 max_attempts，job 与 scan_run 同一路径进入 FAILED | `test_unstable_input_ceiling_fails_job_and_scan_together`：`RETRY_EXHAUSTED`（`reason_code=INPUT_UNSTABLE`）、job `FAILED`（attempts=1、lease 清空）、`scan_run FAILED`（`error` code `INPUT_UNSTABLE`）、无 MISSING、`last_successful_scan` 不前进 |
| 2b. 通用调度路径的联动 | `test_scheduler_fails_an_exhausted_scan_job_and_syncs_the_scan_run`：`claim_next()` 失败预算耗尽的过期执行后 job 与 `scan_run` 同时 `FAILED`，`claim_next` 返回 `None`（不再抛异常） |
| 3. 同一 scan_id 中断恢复的 missing 正确性 | `test_resume_marks_positions_deleted_before_recovery_as_missing`：第一次 execution 已绑定 `a.jpg` 后中断，恢复前删除 `a.jpg`；同一 `scan_id` 恢复成功后 `a.jpg` → `MISSING`（保留最后已知 `asset_id`）、`b.jpg` PRESENT、`assets_total=2` |
| 3b. 旧 execution 只留下 unbound location 的变体 | `test_resume_ignores_unbound_leftovers_from_a_killed_execution`：中断发生在 observe 之后、bind 之前（行 `asset_id IS NULL`），恢复前删除该文件；恢复成功（不被最终 success 守卫卡住），该位置 → `MISSING`，PRESENT 位置全部已绑定 |

## 5. 实际运行的命令与退出码

| 命令 | 退出码 | 结果 |
|---|---|---|
| `uv run pytest -o addopts="" -q tests/unit tests/integration` | 0 | `122 passed, 1 skipped in 75.38s`（1 skipped 为既有 GPU-opt-in；上一轮基线 117 passed + 1 skipped，本轮新增 5 例） |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `git diff --check` | 0 | 无空白错误 |
| `uv lock --check` | 未重跑 | 未改依赖 |

## 6. 未验证项与限制

1. 本报告**不是 PASS**；最终结论仍需 C2 短复核。
2. P1-01 仍会在每次 execution 重新 hash/重新枚举全部文件；稳定等待窗口、生产级 hash 前后双重 stat、
   文件系统 id 移动加速、目录级断点游标属于 P1-02。
3. `reset_execution_coverage` 的代价是一次按来源范围的 `UPDATE`；在 50 万文件规模下的开销与替代方案
   （execution generation 列）留待 P1-02/P4 评估，本任务只覆盖正确性。
4. 单写协调器仍是进程内单写；跨进程并发由 WAL + `BEGIN IMMEDIATE` + busy timeout 保护，未做压力测试。
5. `format/width/height` 仍为 NULL（ADR §3.5 允许）；未跑打包（C 级）与 GTX 1660 实机（P4，BLOCKED）。

## 7. 交接记录

```text
任务 ID / 状态：P1-01 stage 6 / FIX IMPLEMENTED / D 级复验完成 / 等待 C2 最终短复核（未 PASS）
执行模型：用户手动选择 DeepSeek 高推理档；未切换、未声称切换
返修基线提交：8393583（C2 final re-review 提交），返修起点工作树干净
已修复：F1 耗尽的终态事务边界（事务内持久化 + 事务外抛错）与 job/scan_run 终态联动（含通用 claim_next 路径）；
  F2 execution coverage 归属（领取时重置 last_seen 标记）与 unbound 遗留不再阻塞恢复
修改文件：src/photo_search/catalog/store.py、src/photo_search/jobs/queue.py、
  src/photo_search/ingestion/scanner.py、tests/integration/test_catalog_scan.py、
  本报告、docs/development-status.md
数据库/接口变化：schema 未变（仍为 v1，无新迁移）；新增 store.ensure_scan_failed / reset_execution_coverage、
jobs.fail_exhausted_leases（公开）；scan_run 允许 QUEUED→FAILED、BLOCKED→FAILED；
  status 的 locations_pending_content 口径改为“PRESENT 且未绑定内容”
实际命令与结果：uv run pytest -o addopts="" -q tests/unit tests/integration（0，122 passed 1 skipped，75.38s）、
  uv run ruff check .（0）、git diff --check（0）
未验证项：C2 最终短复核；P1-02 生产扫描算法；50 万规模下的 coverage 重置开销；跨进程调度压力；打包；GTX 1660 实机
失败修复轮次：1（locations_pending_content 口径与新语义冲突：MISSING 且未绑定的位置被计入 pending，
  已把统计改为只计 PRESENT 未绑定并补充注释）
下一任务：P1-01 final short re-review（C2 / GPT-5.6 Sol / high）；通过后才可开始 P1-02（DS-H，任务卡已备）
```
