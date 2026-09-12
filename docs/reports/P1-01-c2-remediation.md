# P1-01 — C2 复核返修（DS-H）

任务：P1-01 — 资产 / 位置 / 来源 / 扫描最小持久任务 / 迁移与约束
阶段：**stage 4 — DS-H 返修**（对应 `docs/reports/P1-01-c2-focused-review.md` 的 B1～B4 与 R1/R2）
状态：**FIX IMPLEMENTED / D 级复验完成 / 等待 C2 final focused re-review**（本报告不代表 PASS）
执行模型：由用户在当前 Codex 会话手动选择 DeepSeek 高推理档；未切换、未声称切换模型
复修基线：`83adb65`（C2 复核提交，工作树干净）
冻结契约：`docs/decisions/ADR-0002-catalog-identity-migration-scan-contract.md`（含 2026-09-12 C2 窄幅修订，未改动）

## 1. 改动清单

| 复核项 | 实现改动 |
|---|---|
| **B1** 增量持久结果缺少提交资格校验 | `ingestion/scanner.py`：新增 `_Execution`（token/revision/processor + 当前租约）与 `_require_submission()`；每个文件的 `observe_location` 短事务与 `asset upsert + bind` 短事务都在写之前执行 ADR-0002 §6.5 资格校验；最终 success 事务保留校验。`LEASE_CONFLICT / STALE_LEASE / STALE_INPUT_REVISION / PROCESSOR_MISMATCH / JOB_NOT_FOUND` 现在直接中止且**不改变 scan/job 状态**（迟到执行者既不写结果也不覆盖新执行的状态）。心跳改为**按租约剩余时间续租**（剩余不足一半即续），避免“按文件数心跳”在大文件/慢盘上丢失资格 |
| **B2** `attempts / max_attempts` 语义 | `jobs/queue.py`：`RUNNING -> RETRY_WAIT` 与 `RUNNING -> FAILED` 均 `attempts += 1`；可重试失败达到 `max_attempts` 时直接进入 `FAILED`（`last_error_code=RETRY_EXHAUSTED`）；过期租约接管在事务内把上一次丢失执行计为一次失败（`attempts += 1`）并在达到上限时把任务置 `FAILED`（`last_error_code=LEASE_EXPIRED`）后抛 `RETRY_EXHAUSTED`；`claim_next` 先批量把“预算耗尽的过期执行”置 `FAILED` 再挑下一个可领取任务；`BLOCKED` 仍不消耗次数；`claim_job` 现在按 `run_after` 处理 `RETRY_WAIT`（到期提升为 `QUEUED` 再领取） |
| **B3** `migrate=False` 绕过只读预检 | `catalog/db.py`、`ingestion/service.py`：移除该入口。`Catalog.open()` / `CatalogService.open()` 现在**总是**先执行只读兼容预检（`migrate_database` → `inspect_database`），更高 `schema_version` 在建立 RW/WAL 之前统一返回 `SCHEMA_TOO_NEW`；新增签名断言测试锁死“没有旁路” |
| **B4** stale hash 仍可能让扫描 `SUCCEEDED` | `ingestion/scanner.py`：hash 之后重新 stat 文件；与观测不一致时丢弃该 hash 并重新观测+重算（每个文件最多 3 次内联重试）。仍不稳定时不再“计入 pending 继续”，而是把 job 置 `RETRY_WAIT`（`INPUT_UNSTABLE`，scan_run 保持 `RUNNING`，ADR §3.3）并抛出结构化错误；最终 success 事务内再加一道 `count_unbound_locations == 0` 防御校验，`locations_pending_content` 成功时恒为 0 |
| **R1** 泛化 `scan_runs RUNNING -> RUNNING` 自环 | `catalog/store.py`：`ALLOWED_SCAN_TRANSITIONS["RUNNING"]` 去掉 `RUNNING`；租约接管只让 scan_run 保持原状态，不再经通用转移函数写自环 |
| **R2** `last_seen_scan_id` 同来源保证 | `catalog/store.py`：`observe_location()` 与 `reconcile_missing()` 在事务内校验 `scan_runs.source_id` 与传入 `source_id` 一致，否则抛 `INVALID_STATE_TRANSITION` |

其他随动改动：

- `catalog/errors.py` 新增补充错误码 `RETRY_EXHAUSTED`、`INPUT_UNSTABLE`、`LEASE_EXPIRED`（冻结最小集合之外，语义不重叠）。
- `ingestion/service.py` 新增 `retry_backoff_ms`（默认 `jobs.DEFAULT_RETRY_BACKOFF_MS = 5000`）与 `ScanOutcome.unstable_retries` 字段；`catalog scan --json` 输出新增 `unstable_retries`，既有字段与退出码语义不变。
- 未改：资产/来源身份、`(source_id, path_key)` 唯一性、五字段 job 逻辑键、迁移原子性与备份规则、WAL 要求、
  missing 判定原子边界、原图只读、`models/runtime/workers` 契约、`pyproject.toml`/`uv.lock`（仍只用标准库）。

## 2. 逐项证据

| 复核项 | 测试证据 |
|---|---|
| B1 写前资格（最终提交边界） | `tests/integration/test_catalog_scan.py::test_expired_lease_result_is_refused_and_recovered_later`（`lease_seconds=0`：`locations == 0`、`assets_total == 0`、scan 仍 RUNNING、无 MISSING；过期后新执行恢复成功且 `attempts == 1`）；`tests/unit/test_catalog_jobs.py::test_illegal_transitions_are_refused`（`STALE_INPUT_REVISION` / `PROCESSOR_MISMATCH`） |
| B1 写前资格（observe 边界） | `test_positive_writes_require_a_live_submission`：在第 1 个资格校验点注入失败 → `locations == 0`、`assets_total == 0`、scan 保持 RUNNING；解除注入并越过租约后重扫成功 |
| B1 写前资格（asset upsert + bind 边界） | `test_content_binding_requires_a_live_submission`：在第 2 个资格校验点注入失败 → 位置行存在但未绑定（`locations_pending_content == 1`）、`assets_total == 0`（资产未被创建）、scan 保持 RUNNING |
| B1 长扫描资格保持 | `test_long_scan_renews_its_lease_before_it_expires`：`lease_seconds=1`、每文件耗时 600 ms 的 6 文件扫描仍 `SUCCEEDED`、`attempts == 0`（时间型续租生效） |
| B2 retry 计数与上限 | `tests/unit/test_catalog_jobs.py::test_retry_wait_respects_run_after`（`attempts == 1`、`run_after` 生效）、`test_retryable_failure_exhausts_the_attempt_ceiling`（`max_attempts=2`：第 1 次失败 `RETRY_WAIT`/attempts=1，第 2 次失败直接 `FAILED`/attempts=2，终态不可再 claim） |
| B2 过期接管计入失败与上限 | `test_expired_lease_takeover_respects_the_retry_ceiling`（`max_attempts=1`：接管时置 `FAILED`、attempts=1、`LEASE_EXPIRED`、清空租约、抛 `RETRY_EXHAUSTED`）、`test_scheduler_fails_expired_executions_without_budget`（`claim_next` 侧批量失败后返回 `None`） |
| B2 `BLOCKED` 不消耗次数 | `test_blocked_job_is_not_claimable_until_it_is_resumed`（`attempts == 0`）、`tests/integration/...::test_offline_source_blocks_the_scan_without_missing_marks`（BLOCKED job `attempts == 0`、租约已清空） |
| B3 无迁移旁路 | `tests/unit/test_catalog_migrations.py::test_catalog_open_exposes_no_migration_bypass`、`test_too_new_schema_is_refused_before_any_write_side_effect`（更高版本：数据库字节与目录项不变、无 `-wal/-shm`、`catalog_info` 只读返回 `schema_too_new`/`writable=false`） |
| B4 丢弃 stale hash | `test_input_change_during_hashing_discards_the_stale_hash`：hash 期间替换内容 → `unstable_retries == 1`、绑定到新内容的 SHA-256、全局资产仅 1 个（旧 hash 未成为资产） |
| B4 不稳定不得成功 | `test_persistent_instability_requeues_instead_of_succeeding`：连续不稳定 → `INPUT_UNSTABLE`、scan 保持 RUNNING 且 `finished_at` 为空、job `RETRY_WAIT`（attempts=1、`last_error_code=INPUT_UNSTABLE`、租约清空）、无 MISSING、`last_successful_scan` 不前进；停止变更并越过退避窗口后重扫同一 `scan_id` 成功且 pending=0 |
| R1 无 scan_run 自环 | `tests/unit/test_catalog_constraints.py::test_scan_run_has_no_generic_running_self_loop`（`QUEUED -> RUNNING` 合法且写 `started_at`；`RUNNING -> RUNNING` 抛 `INVALID_STATE_TRANSITION`） |
| R2 同来源校验 | `test_observed_scan_must_belong_to_the_same_source`（跨来源 `scan_id` 被拒，且未写入任何位置行；`reconcile_missing` 同样拒绝） |
| WAL 预检边界（C2 §5 修订） | `test_preflight_reads_a_live_wal_database_without_changing_it`（活动 `-wal` 下只读预检可读版本，主库字节不变）、`test_too_new_schema_is_refused_before_any_write_side_effect`（干净库不因预检产生 `-wal/-shm`） |

## 3. 实际运行的命令与退出码

| 命令 | 退出码 | 结果 |
|---|---|---|
| `uv run pytest -o addopts="" -q tests/unit tests/integration` | 0 | `117 passed, 1 skipped in 77.50s`（1 skipped 为既有 GPU-opt-in；C2 复核前的基线为 106 passed + 1 skipped，本轮新增/改写 11 例） |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `git diff --check` | 0 | 无空白错误 |
| `uv lock --check` | 未重跑 | 未改依赖 |

CLI 抽查（临时夹具，非真实照片库）：

| 命令 | 退出码 | 结果 |
|---|---|---|
| `... catalog init --json` | 0 | `{"status":"ok","previous_version":0,"schema_version":1,"applied":[1]}` |
| `... catalog add-source ... --json` | 0 | `source_id=1`、`ONLINE` |
| `... catalog scan --source-id 1 --json` | 0 | `state=SUCCEEDED`、`files_seen=2`、`assets=1`、`locations=2`、`locations_pending_content=0`、`unstable_retries=0` |
| `... catalog scan --source-id 9 --json` | 1 | `{"status":"error","error":{"code":"SOURCE_NOT_FOUND","details":{"source_id":9}}}` |

## 4. 未验证项与限制

1. 本报告仍**不是 PASS**：ADR-0002 §12 的最终复核结论必须由 C2 在 final focused re-review 中给出。
2. P1-01 仍会在每次扫描重新 hash 全部文件；稳定等待窗口、hash 前后双重 stat 的**生产级**策略、文件系统 id
   移动加速与目录级断点游标属于 P1-02。
3. 单写协调器仍是进程内单写；跨进程并发只由 WAL + `BEGIN IMMEDIATE` + busy timeout 保护，未做跨进程压力测试。
4. `format/width/height` 在 P1-01 仍为 NULL（ADR §3.5 允许）。
5. 未跑打包（C 级）与 GTX 1660 实机（P4-07/P4-08，仍 BLOCKED）。
6. 内联不稳定重试上限（3 次/文件）与重试退避默认值（5 s）是本任务可配置的实现取值；P1-02 可调参，
   但“不稳定不得判定成功”的边界不得放松。

## 5. 交接记录

```text
任务 ID / 状态：P1-01 stage 4 / FIX IMPLEMENTED / D 级复验完成 / 等待 C2 final focused re-review（未 PASS）
执行模型：用户手动选择 DeepSeek 高推理档；未切换、未声称切换
复修基线提交：83adb65（C2 复核提交），返修起点工作树干净
已修复：B1 增量写前提交资格 + 时间型续租；B2 attempts/max_attempts 语义与过期接管上限；
  B3 移除 migrate=False 旁路；B4 stale hash 重新排队 + 成功前 pending 守卫；R1 去掉 scan_run 自环；
  R2 last_seen_scan_id 同来源校验
修改文件：src/photo_search/catalog/{errors,store,db}.py、src/photo_search/jobs/queue.py、
  src/photo_search/ingestion/{service,scanner}.py、
  tests/unit/test_catalog_{jobs,constraints,migrations}.py、tests/integration/test_catalog_scan.py、
  本报告、docs/development-status.md
数据库/接口变化：schema 未变（仍为 v1，无新迁移）；CLI scan 输出新增 unstable_retries 字段；
  错误码新增 RETRY_EXHAUSTED / INPUT_UNSTABLE / LEASE_EXPIRED（冻结集合之外的补充码）
实际命令与结果：uv run pytest -o addopts="" -q tests/unit tests/integration（0，117 passed 1 skipped，77.50s）、
  uv run ruff check .（0）、git diff --check（0）、catalog CLI init/add-source/scan（0，结构化 JSON）、
  catalog scan --source-id 9（1，SOURCE_NOT_FOUND）
未验证项：C2 final focused re-review；P1-02 生产扫描算法；跨进程调度压力；打包；GTX 1660 实机
失败修复轮次：2（1：per-file 资格错误被误判为确定性失败并置 FAILED，改为不改变状态的迟到提交语义；
  2：按文件数心跳在大文件场景下会丢失租约，改为按租约剩余时间续租）
下一任务：P1-01 final focused re-review（C2 / GPT-5.6 Sol / high）；复核通过后才可开始
  P1-02（DS-H，任务卡 docs/tasks/P1-02.md）
```
