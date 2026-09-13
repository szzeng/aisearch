# P2-02 — 持久调度、设备资格与阻塞恢复实现报告

日期：2026-09-13。状态：**PASS（D 级：完整 unit/integration + 具名故障注入/恢复用例）**。

执行模型由用户在 Codex 中手动设置为任务卡推荐档；本任务未自动切换模型、未启动子 Agent、
未切换客户端。`workers/**`、正式运行环境与打包入口均未修改，因此本卡未触发 C 级真实打包 smoke
（判定依据见 §9）。

## 1. 交付物与边界

| 交付物 | 路径 |
|---|---|
| 连续增量 migration | `src/photo_search/catalog/migrations/0002_scheduler_runtime.sql` |
| 稳定协议实现（`p2-contracts.v1`） | `src/photo_search/scheduler/contracts.py` |
| 运行时能力快照与处理器专项 evidence | `src/photo_search/scheduler/capability.py` |
| 冻结设备资格矩阵 | `src/photo_search/scheduler/eligibility.py` |
| 持久暂停 / 公平窗口 / 指数退避 | `src/photo_search/scheduler/state.py` |
| 调度闭环（claim/dispatch/submit/cancel/recover） | `src/photo_search/scheduler/engine.py` |
| 只读诊断投影 | `src/photo_search/scheduler/projection.py` |
| 专项单元用例（29） | `tests/unit/test_p2_scheduler.py` |
| 专项 D 级故障注入（10） | `tests/integration/test_p2_scheduler_recovery.py` |
| 专项 API 用例（8） | `tests/integration/test_p2_scheduler_api.py` |
| 本报告与状态 | 本文件、`docs/development-status.md` |
| 下一任务卡 | `docs/tasks/P2-03.md` |

改动过的既有文件（最小、必要）：

- `src/photo_search/catalog/schema.py`：`SUPPORTED_SCHEMA_VERSION` 1 → 2。
- `src/photo_search/catalog/errors.py`：新增 P2-02 稳定错误码。
- `src/photo_search/jobs/queue.py`：新增 `requested_device` 入参与 7 个派发快照列；
  抽出 `requeue_due_retries`；新增 `claimable_candidates`（公平排序）与
  `fail_exhausted_leases(processor_ids=...)` 过滤。五字段唯一键、七态状态机、lease/attempts
  语义与既有函数签名保持不变。
- `src/photo_search/api/{errors,models,queries,routes}.py`：jobs 派发摘要、capabilities 的
  scheduler/runtime 健康摘要、`POST /api/v1/scheduler/{pause,resume}` 与错误码 409 映射。
- 既有测试中 5 个文件的版本断言（见 §3.4）。
- `tools/diagnostics/frontend_smoke_fixture.py`：`--too-new` 版本号改为由
  `schema.SUPPORTED_SCHEMA_VERSION` 推导；顺带修掉该工具 `--help` 因 argparse 二次 `%`
  格式化而崩溃的历史缺陷（§8.2）。

未改动：`0001_initial.sql`、ADR-0001～ADR-0005、两份冻结 JSON Schema、
`pyproject.toml`、`uv.lock`、模型 manifest、`workers/**`、`src/photo_search/media/**`、
`src/photo_search/web/**`。`schema_version` 只通过连续 migration 推进；地点表、GPS、地名包、
artifact/outbox/LanceDB/SearchProfile 仍未实现。

## 2. 基线核对

| 项 | 实际值 |
|---|---|
| 起始 HEAD | `bf1a652`（P2-01A） |
| `git log -1 --format=%H -- docs/reports/P2-01A-place-contract.md` | `bf1a652c7024f8672beac85e87dfc60d92e23f1a` |
| 开始前 `git status --short` | 干净（无输出） |
| 开始前 migration 目录 | 只有 `0001_initial.sql`，序号未被占用 |
| 开始前 `SUPPORTED_SCHEMA_VERSION` | 1 |

前置条件 P2-01、P2-01A 均 PASS，ADR-0003/ADR-0005 与两份机器可读 schema 已冻结，与任务卡一致。
本仓库不存在需保留的重叠未提交修改，因此按任务卡直接执行，未重新设计任务。

## 3. 增量数据库与迁移证据

### 3.1 新增内容（`0002_scheduler_runtime.sql`）

| 对象 | 作用 | 数据库级约束 |
|---|---|---|
| `jobs` 新增 7 列 | `requested_device`、`resolved_device`、`runtime_provider`、`precision`、`runtime_capability_revision`（FK → `runtime_capabilities`）、`dispatch_spec_revision`、`blocking_reason` | `requested_device` NOT NULL DEFAULT `'auto'` + CHECK 枚举；device/provider/precision CHECK；FK `ON DELETE RESTRICT` |
| `runtime_capabilities` | 不可变运行时能力快照：revision、规范化 JSON、sha256、health、measured/checked time | PK revision；CHECK 枚举/非负/64 位小写 sha256/`json_valid`；BEFORE UPDATE+DELETE 触发器 `RAISE(ABORT)` 禁止改写与删除历史 |
| `runtime_qualification_evidence` | 该 processor/device/precision/provider/runtime package 的专项验收 evidence | PK evidence_id；device CHECK；`(processor_id, spec_revision, device, precision)` 索引 |
| `scheduler_state` | 单例持久暂停策略、revision、公平窗口、updated_at | PK CHECK(`state_id = 1`)、`paused IN (0,1)`、`revision >= 0`、`aging_window_ms > 0` |

这 7 个新列**不进入**五字段逻辑唯一键，也没有新增第六个身份字段；`0001_initial.sql` 未被修改。

### 3.2 v1 → v2 迁移与数据保全

`tests/integration/test_p2_scheduler_recovery.py::test_v1_upgrade_preserves_every_existing_row_and_writes_a_backup`
用真实的 0001 脚本 + `schema_version=1` 构造 v1 库，写入 1 个来源、1 次成功扫描、1 个资产、
1 个中文/空格相对路径位置和 2 个 job（其中 1 个带活动 lease 的 `RUNNING`），再执行真实迁移：

- `previous_version=1`、`schema_version=2`、`applied=(2,)`，备份文件写到
  `<data_root>/backups/catalog/catalog-*.db`，备份内 `schema_version=1` 且只有 6 张 v1 表。
- `job_rows(V1_JOB_COLUMNS)` 迁移前后逐行相等（含 `job_id`、五字段、状态、`priority`、
  `attempts`、`max_attempts`、`run_after`、`lease_token`、`lease_expires_at`、`worker_id`、
  `progress`、错误码/消息、时间戳）；`source_roots`/`scan_runs`/`assets`/`file_locations`
  全表 dump 也逐行相等。
- 新增列中只有 `requested_device` 取到安全默认 `'auto'`，其余 6 列在旧行上为 NULL；
  旧 `RUNNING` 行的 lease 三元组 `('legacy-lease-token', 999999, 'legacy-worker')` 原样保留，
  ADR-0002 的 lease 语义没有被迁移改写。
- 迁移后 `schema.verify_schema(connection, 2)` 通过；`test_the_v1_fixture_really_is_the_frozen_schema`
  反向锁定夹具确实是 0001 的 6 张表与 19 列，不是伪造形状。

### 3.3 回滚、缺件拒写与更高版本

- 中断回滚：注入 `0003_broken.sql`（先 `ALTER TABLE`，再写 metadata，再故意失败）后
  `MIGRATION_FAILED`；版本仍为 2，19 列逐行不变，`jobs` 没有 `probe_note` 列，metadata 没有
  半成品行 → 事务整体回滚。
- 目标 schema 缺件拒写：`DROP TABLE scheduler_state` 后 `inspect_database` 与 `Catalog.open`
  均返回 `SCHEMA_INCONSISTENT`，`missing_objects` 指名 `scheduler_state`，且主库字节与目录内容
  完全不变（未自动补洞、未建 `-wal`/`-shm`）。
- 更高版本：把 `metadata.schema_version` 提到 `SUPPORTED_SCHEMA_VERSION + 1` 后，
  `Catalog.open` 返回 `SCHEMA_TOO_NEW`，主库字节不变、无 `-wal`/`-shm` 产生。
  API 侧同样验证：`POST /api/v1/scheduler/{pause,resume}` 在过新库上返回 409
  `SCHEMA_TOO_NEW` 且零写副作用（`tests/integration/test_p2_scheduler_api.py`）。

### 3.4 因版本推进而必须同步的既有断言

| 文件 | 改动 | 原因 |
|---|---|---|
| `tests/unit/test_catalog_migrations.py` | `CORE_TABLES` 增 3 表；fresh 迁移断言 `applied=(1,2)`、`inspect_database=2`；`--too-new` 用 3 且 `supported_schema_version=2`；断链/备份用例改用 `0003_*` 与 `SUPPORTED_SCHEMA_VERSION=3`；`expected_versions=[1,2]` | 迁移序号与支持版本真实前进 |
| `tests/integration/test_catalog_scan.py` | `catalog init --json` 断言 `schema_version=2`、`applied=[1,2]` | 同上 |
| `tests/integration/test_api_access.py` | `_too_new_database` 改为 `SUPPORTED_SCHEMA_VERSION + 1` | 原硬编码 2 已变成合法版本 |
| `tests/integration/test_api_sources_assets.py` | `supported_schema_version` 1 → 2 | 同上 |
| `tests/integration/test_catalog_media.py` | 全表 dump 的 `CORE_TABLES` 增 3 表 | 让"媒体生成不改 catalog"的不变量覆盖新表 |

除上表外没有修改既有测试；`tests/unit/test_catalog_constraints.py`、`test_catalog_jobs.py`、
`test_p2_contracts.py`、`test_catalog_identity_lifecycle.py` 全部原样通过。

## 4. 调度、资格与公平规则

### 4.1 冻结设备资格矩阵（`scheduler/eligibility.py`）

纯函数 `resolve(spec, requested_device, capability, evidence)` 逐项实现 ADR-0003 §5：

| policy / 请求 | auto | cpu | gpu |
|---|---|---|---|
| `GPU_REQUIRED` | 健康且有 evidence 的 GPU，否则 `RUNTIME_CAPABILITY_UNAVAILABLE` | `DEVICE_POLICY_CONFLICT` | 同 auto，绝不转 CPU |
| `CPU_ALLOWED` | 合格 GPU 优先，否则合格 CPU，两者都不合格才阻塞 | CPU | 不转 CPU，不合格即阻塞 |
| `CPU_PREFERRED` | CPU | CPU | 仅该 processor 有 GPU 专项 evidence 且健康才允许 |

资格同时要求：`ProcessorSpec` 声明该 device/precision、capability 快照 revision 与其自身事实一致、
`runtime_package_id` 相符、存在 `available=true` 且 `health='healthy'` 的 provider、并且存在
`(processor_id, spec_revision, device, precision, provider_name, runtime_package_id)` 精确匹配的
evidence 行。已实测的"不合格"来源包括：无 CUDA provider、provider unhealthy、provider 不可用、
healthy 但无 evidence、只有别的 processor 的 evidence、spec_revision 已过期、runtime package 不匹配。

能力快照按 `runtime_package_id` 分区：`latest_capability(runtime_package_id=...)`。这样 ONNX Runtime
包的 GPU 故障只影响该包的 processor，不会阻塞纯 Python 的 non-model CPU 任务（架构 §6.4）。

### 4.2 claim 事务与阻塞语义

`Scheduler.claim_next` 在**一个** `BEGIN IMMEDIATE` 事务内完成：读取持久暂停状态 → 到期
`RETRY_WAIT → QUEUED` → 结算无预算的过期 lease → 按有效优先级取候选 → 逐个解析资格 →
合格者写 `resolved_*` 快照并领取 lease；不合格者走 `QUEUED → RUNNING → BLOCKED`（ADR-0002 §6.1
唯一合法的两种迁移）后继续看下一个候选。

- 阻塞不消耗普通重试：`attempts` 保持 0，事务结束时不留下任何可观察 lease。
- 已经 RUNNING 但 lease 过期、当下不可服务的 job **不**被接管：接管本身记一次丢失执行，
  不能为了写 `BLOCKED` 而消耗预算；它留待设备恢复或预算耗尽时按 ADR-0002 处理。
- 同队列混合场景由
  `test_an_unhealthy_gpu_does_not_stop_a_cpu_preferred_job` 与
  `test_a_capability_block_survives_a_restart_and_recovers` 实测：高优先级 `GPU_REQUIRED`
  阻塞的同时，`CPU_PREFERRED` non-model job 正常完成。
- 事务内不做解码、hash、推理、HTTP 或 sleep，与 ADR-0002 §8 一致。

### 4.3 退避与公平

- 退避是配置化的指数退避且有上限：`backoff_delay_ms(attempts, base, multiplier, max)`，
  默认 5s/×2/上限 300s。截止写入 `run_after`，因此"不到期不忙领"是数据库事实：
  测试用固定时钟先证 `claim_next(now=deadline-1) is None`，再证到点可领，并逐步核对
  `run_after = 11000 → 13000 → 17000 → 21000`（第 4 次 8000 被 4000 上限截断）。
- 公平：有效优先级 = `priority + (now_ms - created_at) // aging_window_ms`（在 SQL 中同一表达式
  排序，`ORDER BY effective_priority DESC, created_at ASC, job_id ASC`）。持续到达的高优先任务
  无法永久饿死旧 backlog：测试证明 10 个窗口后旧任务追平并按稳定 tie-break 被选中。
- `aging_window_ms` 持久在 `scheduler_state`，重启后沿用同一窗口，选择结果可复现；
  同优先级按 `created_at, job_id` 稳定选择。
- `BLOCKED` 只在阻塞条件（当前 capability revision 重新资格）通过后，经合法
  `BLOCKED → QUEUED` 恢复；`DEVICE_POLICY_CONFLICT` 是永久配置拒绝，不参与恢复。

## 5. 执行器边界与非向量结果

- 执行器是可注入的 `Callable[[dict], dict]`：收到普通 JSON `JobRequest`（由不可变 dataclass
  `to_envelope()` 产生，字段与 `p2-contracts.v1.json` 的 `required` 集合逐项相等），返回普通 JSON
  `JobResult`。契约测试把两端的键集合与伴随 schema 对比，防止协议漂移。
- 主进程是唯一持久写者；`JobResult` 必须先通过 ADR-0002 §6.5 提交资格（RUNNING、未过期 lease、
  revision、processor），再核对派发时的 `resolved_device/runtime_provider/precision` 语义。
  不一致返回 `RUNTIME_RESOLUTION_MISMATCH`，测试逐字节比较 job 行证明**零持久副作用**。
- 结果信封有界：`MAX_JOB_RESULT_CHARS = 262144`，超限或字段缺失/多余都在提交前被拒。
  契约不兼容（缺字段/未知字段）按 ADR-0003 §3 记为**确定性失败**（`FAILED`，
  `last_error_code=INVALID_JOB_RESULT`），不会无限重试；执行器抛异常则记为可重试的
  `SCHEDULER_EXECUTOR_ERROR`，且只记录异常类型、不记录可能带路径的 message。
- 非模型假执行器为 `non_model` + `CPU_PREFERRED` + `supported_devices=['cpu']`，无
  model/weights/dimension/metric/space_id；成功结果允许 `artifacts=[]`，结构化 JSON 结果也被接受。
  P2-02 **不持久化** artifact 内容：测试断言迁移后不存在任何 vector/embedding/artifact/outbox/lance
  表，且成功前后表集合不变，即本任务不产生 artifact/outbox/LanceDB/向量行。
- `artifacts=[]` 的 SUCCEEDED 是正常完成路径；`progress` 必须为 1 且 `error` 为 null。
- ADR-0002 §6.5 的进度单调性在调度边界保持：RETRY_WAIT/BLOCKED 信封即使报 `progress = 0.0`
  也不会抹掉本次执行已经到达的进度（`test_retry_backoff_is_exponential_capped_and_clock_driven`
  先把进度写到 0.5，再断言重试后仍为 0.5）。

## 6. API 与诊断投影

| 端点 | 变化 |
|---|---|
| `GET /api/v1/jobs`、`GET /api/v1/jobs/{id}` | `JobRecord` 增加 `requested_device`、`resolved_device`、`runtime_provider`、`precision`、`runtime_capability_revision`、`blocking_reason`；仍不含 lease token 与 worker id |
| `POST /api/v1/scheduler/pause` | 持久暂停，返回 `state/revision/aging_window_ms/updated_at/changed`；幂等 |
| `POST /api/v1/scheduler/resume` | 持久恢复，同上；幂等 |
| `GET /api/v1/capabilities` | 增加 `scheduler`（暂停策略 revision）、`runtime`（最新 capability revision 与 health）、`qualification_evidence` 计数；无本机路径、无 provider 内部细节 |

- 写接口完整走既有 session + Origin + Double-submit CSRF + Host 边界；测试覆盖
  401 `SESSION_REQUIRED`、403 `CSRF_INVALID`、403 `ORIGIN_NOT_ALLOWED`、403 `HOST_NOT_ALLOWED`
  与 200 成功路径。
- 新增稳定错误码全部映射 409：`DEVICE_POLICY_CONFLICT`、`RUNTIME_CAPABILITY_UNAVAILABLE`、
  `RUNTIME_RESOLUTION_MISMATCH`、`RUNTIME_CAPABILITY_REVISION_CONFLICT`、`SCHEDULER_PAUSED`、
  `INVALID_JOB_RESULT`、`SCHEDULER_EXECUTOR_ERROR`。
- `GET /api/v1/capabilities` 的 scheduler/runtime 摘要走独立只读连接；库不存在或仍是 v1 时返回
  `null`，不猜测、不迁移、不写库。
- 暂停经 API 落库后立即生效：`test_a_pause_from_the_api_stops_the_scheduler_immediately`
  先证明同一队列不再被领取，再 resume 后继续完成。

## 7. D 级验收逐项映射（任务卡 1～9）

| # | 要求 | 具名用例 |
|---|---|---|
| 1 | v1→v2 迁移、备份、中断回滚、缺件拒写、更高版本零副作用 | `test_v1_upgrade_preserves_every_existing_row_and_writes_a_backup`、`test_an_interrupted_upgrade_rolls_back_and_keeps_every_row`、`test_a_missing_target_schema_object_refuses_every_write`、`test_a_higher_version_database_is_untouched_by_the_scheduler`、`test_a_higher_schema_version_refuses_scheduler_writes`（API，参数化 pause/resume） |
| 2 | claim 后进程退出 / lease 到期接管；旧 worker 心跳与结果拒绝；attempt ceiling | `test_restart_takes_over_an_expired_lease_and_refuses_the_old_worker`、`test_an_expired_lease_without_budget_fails_with_lease_expired`、`test_a_late_execution_cannot_commit`、`test_an_expired_lease_takeover_respects_the_attempt_ceiling` |
| 3 | RETRY_WAIT 指数退避、截止前不忙领、到期恢复；BLOCKED 不耗 attempts | `test_retry_backoff_is_exponential_capped_and_clock_driven`、`test_backoff_helper_caps_and_is_monotone`、`test_retry_ceiling_still_terminates_the_job`、`test_blocked_capability_does_not_consume_a_retry_attempt` |
| 4 | pause→重启仍暂停且不领取；RUNNING 合法完成；resume 继续；重复幂等 | `test_pause_blocks_new_claims_but_lets_a_live_lease_finish`、`test_pause_and_resume_are_idempotent_and_persist_across_a_restart`、`test_pause_survives_a_restart_and_a_running_job_still_finishes`、`test_pause_and_resume_are_idempotent_and_persistent`（API）、`test_a_pause_from_the_api_stops_the_scheduler_immediately` |
| 5 | GPU 缺失/不健康/evidence 不匹配：GPU_REQUIRED 阻塞、显式 GPU 不转 CPU、同队列 CPU_PREFERRED 成功、新健康 revision 只恢复合格任务 | `test_gpu_required_blocks_when_no_qualified_gpu_is_available`、`test_an_unhealthy_gpu_does_not_stop_a_cpu_preferred_job`、`test_a_new_healthy_revision_recovers_only_the_qualified_jobs`、`test_a_capability_block_survives_a_restart_and_recovers` |
| 6 | 三种 policy/显式请求矩阵；JobResult 设备不匹配零副作用 | `test_explicit_cpu_on_gpu_required_is_a_policy_conflict`、`test_cpu_allowed_auto_prefers_gpu_and_never_silently_falls_back`、`test_cpu_preferred_needs_gpu_evidence_for_an_explicit_gpu_request`、`test_a_healthy_provider_or_another_processors_evidence_is_not_enough`、`test_runtime_package_mismatch_blocks_the_dispatch`、`test_result_mismatch_writes_nothing` |
| 7 | 持续高优先下的公平性、同优先级稳定顺序、重启后的持久选择状态 | `test_aging_gives_a_starved_backlog_a_bounded_turn`、`test_equal_priority_is_stable_by_created_at_then_job_id`、`test_fairness_and_ordering_survive_a_restart` |
| 8 | 取消/超时/错误信封、成功 `artifacts=[]`、结构化非向量结果、无向量行 | `test_cancel_is_terminal_and_refuses_a_late_result`、`test_cancel_works_from_queued_and_blocked`、`test_a_malformed_result_fails_deterministically`、`test_an_executor_crash_is_a_retryable_execution_failure`、`test_success_with_no_artifacts_is_normal_and_writes_no_vector`、`test_a_structured_non_vector_result_is_accepted`、"超时"由 lease 到期接管/耗尽两条用例覆盖 |
| 9 | scheduler API 的 session/Host/Origin/CSRF、字段脱敏、higher-schema 拒写 | `test_scheduler_endpoints_enforce_session_origin_csrf_and_host`、`test_jobs_projection_carries_the_dispatch_summary_without_credentials`、`test_jobs_projection_reports_the_resolved_device`、`test_capabilities_reports_the_scheduler_and_runtime_health`、`test_a_higher_schema_version_refuses_scheduler_writes` |

补充锁定：`test_the_job_request_and_result_match_the_frozen_schema` 把
`JobRequest`/`JobResult`/`RuntimeCapabilities` 的信封键集合与
`docs/decisions/contracts/p2-contracts.v1.json` 的 `required` 逐项对齐；
`test_capability_snapshots_are_immutable_and_revision_checked` 证明快照不可改写/删除（触发器）
且伪造 revision 被拒；`test_the_p0_runtime_probe_is_only_a_snapshot_source_not_a_qualification`
证明 P0 probe 只作为快照来源，provider 存在本身不构成资格。

## 8. 命令、退出码与实测结果

所有测试的临时目录、复制数据库与假输入都建在 pytest 的 `%TEMP%` 子目录；没有读取、移动或
删除任何真实图库。

### 8.1 必需验收

| 命令 | 结果 |
|---|---|
| `uv run pytest tests/unit tests/integration` | exit 0，`415 passed, 1 skipped in 131.90s`（基线 368 passed + 本卡新增 47） |
| `uv run pytest -q tests/unit/test_p2_scheduler.py` | exit 0，`29 passed` |
| `uv run pytest -q tests/integration/test_p2_scheduler_recovery.py` | exit 0，`10 passed` |
| `uv run pytest -q tests/integration/test_p2_scheduler_api.py` | exit 0，`8 passed` |
| `uv run pytest -q tests/unit/test_catalog_migrations.py tests/unit/test_catalog_jobs.py tests/unit/test_p2_contracts.py` | exit 0，`33 passed` |
| `uv run ruff check .` | exit 0，`All checks passed!` |
| `git diff --check` | exit 0，无输出 |
| `uv lock --check` | exit 0，`Resolved 95 packages`（依赖未变） |

### 8.2 诊断工具核对（因本卡改动了它）

| 命令 | 结果 |
|---|---|
| `uv run python tools/diagnostics/frontend_smoke_fixture.py --help` | exit 0，正常打印帮助（修复前该命令因 argparse 二次 `%` 格式化抛 `ValueError: unsupported format character 'T'`；属历史缺陷，非本卡引入） |
| `uv run python tools/diagnostics/frontend_smoke_fixture.py --work-dir <%TEMP% 唯一目录> --photos 3 --bulk 4 --corrupt --too-new --reset --json` | exit 0，生成夹具并打印启动命令与 10 步观察 |
| 读取该夹具 `PhotoSearchData 过新/catalog/catalog.db` | `schema_version=3`，`SUPPORTED_SCHEMA_VERSION=2`，`inspect_database` 返回 `SCHEMA_TOO_NEW` |

验证用夹具目录已删除（`%TEMP%\p2-02-fixture-check-*`），可恢复性为"不存在，需重新生成"。

## 9. 是否触发 C 级

任务卡规定：修改 `workers/**` 或正式运行环境/打包入口才追加 C 级真实构建与打包 smoke。
本卡**未**修改 `workers/**`、`__main__.py`、启动器、`tools/packaging/**` 或任何依赖；
稳定协议只用注入的假执行器验证，模型 worker 重构被有意留给后续任务。

因此 C 级未触发。相关静态事实：`tools/packaging/build_onedir.py` 已经使用
`--collect-data photo_search`，新增的 `0002_scheduler_runtime.sql` 与 0001 位于同一 package-data
目录，会随现有机制一起收集；并且 `schema.migration_files()` 要求 0001..supported 连续，
万一冻结包漏收任一迁移脚本会直接 `SCHEMA_INCONSISTENT` 失败而不是静默建空库
（`test_a_build_without_migration_scripts_fails_loudly`）。

## 10. 未验证项与后续

1. **真实设备/模型对接未做**：本卡只用确定性 non_model CPU 假执行器。真实 Chinese-CLIP 的
   `resolution` 快照写入、健康检查驱动的 capability revision 生成、OOM 分类与取消边界留待
   P2-03 及后续（P2-03 会接入真实 JobResult 提交与 artifact/outbox）。
2. **没有后台调度线程**：`Scheduler` 暴露 `claim_next`/`run_once`/`run_until_idle` 由调用方
   驱动，paused/backoff 都是数据库事实，不做 sleep 或忙轮询。进程内常驻工作池、
   有界解码池与多 worker 领取由 P2-03+ 接线；本卡不声称已有常驻调度线程。
3. **公平性只验证确定性规则**：`aging_window_ms` 默认 60s，未做长时压测；`claimable_candidates`
   每次 claim 最多检查 256 个可运行行，超大 backlog 的单次 claim 上限未做压力测试。
4. **跨进程并发未做**：单写协调器仍是进程内的；`BEGIN IMMEDIATE` + busy timeout 的跨进程写入
   （P1-02/P1-04 已记录的限制）本卡未新增承诺。
5. **evidence 行只增不改**：测试直接 `DELETE` 掉 evidence 来制造不合格；产品侧尚无撤销/失效
   evidence 的审计用例（P2-08 的综合验收候选）。
6. **`input_snapshot` 只填 subject revision**：asset SHA-256/location/size/mtime 为 NULL
   （ADR-0003 §2.2 允许"该类 subject 不适用该字段"）。真正需要读取原文件的 worker 仍须在
   P2-03 里补读前/读后检查。
7. **API 只在 ASGI 进程内验证**：本卡未做真实端口 smoke（P1-04/P1-05 已建立的真实端口与真实
   浏览器 smoke 仍有效，但未在本卡重跑）。
8. **既有工具历史缺陷**：`frontend_smoke_fixture.py --help` 的崩溃已修（§8.2），但该工具的
   浏览器核对清单仍是 P1-05B 的手工清单，本卡未重跑浏览器 smoke。

## 11. 结论

P2-02 完成了一个可独立恢复的通用调度闭环：连续 migration 与数据保全、scheduler-owned 设备
资格、持久暂停、指数退避、公平优先级、阻塞恢复、API/诊断投影与确定性非模型假任务验收全部
有具名自动化证据。所有必需验收实际执行并通过，因此标记 **PASS**。
