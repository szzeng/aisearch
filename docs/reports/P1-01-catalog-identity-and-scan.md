# P1-01 — 资产 / 位置 / 来源 / 扫描最小持久任务 / 迁移与约束

任务状态：**IMPLEMENTED / D 级验证完成 / 等待 C2 聚焦复核**。本报告是 DS-H 实现阶段的交付证据；
ADR-0002 §12 的 16 项聚焦复核尚未由 C2 执行，因此 P1-01 **未 PASS**。

执行模型：由用户在当前 Codex 会话手动选择 DeepSeek 高推理档；本会话未切换、未声称切换模型。
基线：P0-07 完成提交 `0453200`（`git log -1 --format=%H -- docs/reports/P0-07-p0-summary.md`），
实现起点工作树干净（`8b7a588`，P1-01 状态同步提交）。
冻结契约：`docs/decisions/ADR-0002-catalog-identity-migration-scan-contract.md`（未修改）。

---

## 1. 交付物清单

新增实现：

| 文件 | 内容 |
|---|---|
| `src/photo_search/config.py` | 数据根解析（沿用 P0-04 的 `%LOCALAPPDATA%\PhotoSearchData` / `USERPROFILE` 优先级与 realpath 语义）、`catalog/catalog.db`、`backups/catalog` 路径；`PHOTO_SEARCH_DATA_ROOT` 为诊断/测试覆盖 |
| `src/photo_search/catalog/errors.py` | ADR-0002 §9 最小错误码 + 6 个不能归于冻结集合的补充码；错误对象 `{code, message, details}` 与紧凑 JSON |
| `src/photo_search/catalog/paths.py` | `normalize_root` / `root_key` / `is_same_or_ancestor` / `path_key`（相对路径、Windows 词法、大小写不敏感、无 Unicode 归一化、不 resolve） |
| `src/photo_search/catalog/migrations/0001_initial.sql` | 6 张表（`metadata`、`source_roots`、`scan_runs`、`assets`、`file_locations`、`jobs`）+ 唯一/CHECK/外键 + 局部唯一索引与普通索引 |
| `src/photo_search/catalog/schema.py` | 版本常量、迁移连续性校验、`expected_objects`/`schema_objects`/`verify_schema`（版本号与核心 schema 一致性）、`read_schema_version` |
| `src/photo_search/catalog/sqlite_io.py` | 只读预检连接、读连接（`foreign_keys=ON`、手动事务）、WAL 校验、`journal_mode_from_header` |
| `src/photo_search/catalog/migrate.py` | 版本发现、只读兼容预检、磁盘预算、迁移前备份、单事务原子迁移、`catalog_info` 只读诊断 |
| `src/photo_search/catalog/db.py` | `Catalog`：单写协调器（`BEGIN IMMEDIATE` + 锁）、事务边界、WAL 确认、schema_version |
| `src/photo_search/catalog/store.py` | 来源/扫描/位置/资产 SQL 与 `catalog_counts` |
| `src/photo_search/jobs/queue.py` | 五字段逻辑键、状态机、领取/接管、心跳、提交资格校验、`resume_blocked` |
| `src/photo_search/ingestion/scanner.py` | 最小确定性扫描：枚举（不跟随 reparse point）、完整 SHA-256、正向事实提交、最终原子缺失对账 |
| `src/photo_search/ingestion/service.py` | 可观测内部服务：注册来源、发起扫描、运行扫描、来源/资产计数、只读诊断 |
| `src/photo_search/ingestion/cli.py` | `catalog init/info/add-source/list-sources/scan/status` + `--json` 结构化输出与非零退出码 |

修改：

| 文件 | 改动 |
|---|---|
| `src/photo_search/__main__.py` | 仅新增 `catalog` 子命令入口；`demo-search` 与默认 `uvicorn` 行为不变 |

新增测试：

| 文件 | 覆盖 |
|---|---|
| `tests/unit/test_catalog_paths.py` | 20 例：分隔符/大小写/中文与空格/无 NFC 归一化、逃逸与绝对/UNC/驱动器拒绝、组件边界祖先判定 |
| `tests/unit/test_catalog_migrations.py` | 9 例：空库迁移、幂等、缺件 `SCHEMA_INCONSISTENT`、高版本零写副作用拒绝、迁移中断回滚、升级前备份、磁盘不足、WAL、活动 WAL 下只读预检 |
| `tests/unit/test_catalog_constraints.py` | 7 例：`sha256` 唯一与格式、`UNIQUE(source_id, path_key)`、五字段非空逻辑键、状态/availability/attempts/progress CHECK、lease 列约束、每来源唯一非终态扫描、外键 RESTRICT |
| `tests/unit/test_catalog_jobs.py` | 8 例：领取生成新租约、幂等入队、心跳续租与过期/旧 token/异 worker 拒绝、终态与清租约、非法迁移、过期接管与迟到提交拒绝、`RETRY_WAIT` 退避、`BLOCKED` 不可领取与恢复 |
| `tests/integration/test_catalog_scan.py` | 11 例：真实夹具全流程、来源边界与数据根、离线阻塞、重启续扫、过期租约最终提交被拒与恢复、同路径换内容、目录联接、路径键碰撞、磁盘不足、跨来源共享资产、CLI JSON 契约 |

未改动：`src/photo_search/models/**`、`runtime/**`、`workers/**`、`tools/prepare_models/manifests/**`、
`docs/reports/P0-*.md`、`pyproject.toml`、`uv.lock`（本任务未新增依赖，只用标准库 `sqlite3`/`pathlib`/`ntpath`）。

## 2. 冻结契约的落地方式

数据身份与约束（全部下沉到数据库，Python 只做更早的错误信息）：

- `assets.sha256 TEXT NOT NULL UNIQUE CHECK(length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*')`；
  同一 SHA-256 命中不同 `byte_size` 报 `ASSET_SIZE_MISMATCH`（不覆盖）。
- `file_locations UNIQUE(source_id, path_key)`；`asset_id` 允许为空，未完成完整 hash 的位置只停留在位置层。
- `jobs UNIQUE(subject_type, subject_id, input_revision, task_type, processor_id)`，五字段均 `NOT NULL` 且 `CHECK(... <> '')`。
- `scan_runs` 局部唯一索引：`(source_id)` 仅在 `QUEUED/RUNNING/BLOCKED` 内唯一。
- `jobs` 的 lease 列约束为：非 `RUNNING` 状态时 `lease_token`/`lease_expires_at`/`worker_id` 必须全部为空，
  离开 `RUNNING` 时同一事务清空，与 ADR §3.6 的表述一致。
- `ON DELETE RESTRICT`：来源、资产、扫描历史不能被级联删除。

迁移（实现模型未改动语义，仅补不确定项）：

- 版本权威来源是 `metadata.schema_version`；迁移器只用只读连接读取版本与核心 schema。
- 只读预检的实现细节：数据库干净（无 `-wal` sidecar）时用 `mode=ro&immutable=1` 读取，主库即权威内容且
  SQLite 不会为“读一下 schema”创建空的 `-wal`/`-shm`；存在 `-wal` 时用普通 `mode=ro`（必须读 WAL 才正确），
  此时侧车文件本来就已经存在。该选择是为满足 ADR §7.4“判定 `SCHEMA_TOO_NEW` 前不得建立 sidecar”所做的实现决定。
- 每个版本升级用 `BEGIN IMMEDIATE; <migration>; <schema_version 提升>; COMMIT;` 一个事务；失败即回滚，版本号不前进。
- 升级前顺序：兼容检查通过 → 打开写连接并确认 WAL → 磁盘预算（当前逻辑库大小 + 64 MiB）→ SQLite backup API 写
  `backups/catalog/catalog-<UTC 时间戳>.db` → 迁移。空库不做无意义备份，但仍检查磁盘。
- 迁移后再次校验目标版本核心 schema，并确认 `journal_mode=wal`。

扫描与缺失判定：

- 正向事实（看到位置、size/mtime、完整 hash 绑定、同内容多位置）可增量提交；负向事实只在最终事务产生。
- 每次扫描对枚举到的文件计算完整 SHA-256（不做 mtime 快捷跳过），因此同路径同 size/mtime 替换也能被识别为新内容；
  P1-02 再补稳定等待与生产级 hash 前后校验，P1-01 不保留已明确过期的旧绑定。
- 最终事务一次性完成：缺失对账 → `scan_runs=SUCCEEDED` + `finished_at` → `source_roots.last_successful_scan` → job `SUCCEEDED`；
  提交前先做租约/输入 revision/processor 资格校验，任何不匹配都抛 `STALE_LEASE` 等错误且不写负向事实。
- 中断或来源离线：`scan_run`/job 进入 `BLOCKED`，不写 `finished_at`，不产生 MISSING；恢复时 job 先回 `QUEUED`，
  只有真正 claim 才把 `scan_run` 置 `RUNNING`。
- 权限失败/读取失败按可重试的外部条件处理为 `BLOCKED`；路径键碰撞与磁盘不足是确定性失败，进入 `FAILED`。

实现模型自行决定（ADR §11 允许的“实现可自行决定”范围），供 C2 复核确认：

1. `immutable=1` 只读预检（见上）。
2. 补充错误码 `INVALID_PATH`、`CATALOG_UNREADABLE`、`MIGRATION_FAILED`、`JOB_NOT_FOUND`、`FILE_READ_ERROR`、`ASSET_SIZE_MISMATCH`。
3. `scan_runs` 允许 `RUNNING -> RUNNING` 自环，仅用于“旧租约过期后新租约接管、逻辑扫描不重启”。
4. 来源视图的 `assets` 只统计该来源位置可达的资产，另有全库 `assets_total`；两者都不删除退役资产。
5. 缺失位置保留最后一次确认的 `asset_id`（“最后已知内容”），`availability='MISSING'` 只表示可用性。
6. 扫描夹具与故障注入钩子：`interrupt_after`（模拟进程被杀）与 `observations`（注入路径键碰撞清单）。
7. `PHOTO_SEARCH_DATA_ROOT` 环境覆盖（诊断/测试用，不改变正常运行的默认数据根）。

## 3. 实际运行的命令与退出码

| 命令 | 退出码 | 结果 |
|---|---|---|
| `uv run pytest -o addopts="" -q tests/unit tests/integration` | 0 | `106 passed, 1 skipped in 72.53s`（1 skipped 为 P0 起就存在的 GPU-opt-in 用例；P0 基线为 51 passed + 1 skipped，本任务新增 55 例） |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `git diff --check` | 0 | 无空白错误 |
| `uv lock --check` | 未重跑 | 本任务未改 `pyproject.toml`/`uv.lock`（只用标准库），依赖未变 |

内部入口真实运行（临时夹具目录，非真实照片库；命令与结果逐字来自本次会话）：

| 命令 | 退出码 | 结果 |
|---|---|---|
| `uv run python -m photo_search catalog init --json --data-root <tmp>/data` | 0 | `{"status":"ok","previous_version":0,"schema_version":1,"applied":[1],"backup_path":null}` |
| `... catalog add-source --name photos --path <tmp>/photos --json` | 0 | `source_id=1`、`status=ONLINE`、`last_successful_scan=null` |
| `... catalog scan --source-id 1 --json` | 0 | `state=SUCCEEDED`、`files_seen=2`、`assets=1`（同字节两文件共享）、`locations=2`、`missing=0` |
| `... catalog status --json` | 0 | `sources=1`、`assets=1`、`locations=2`、`present=2`、`missing=0` |
| `... catalog scan --source-id 9 --json` | 1 | `{"status":"error","error":{"code":"SOURCE_NOT_FOUND","details":{"source_id":9}}}` |

CLI 非 JSON 错误路径（`... catalog status --source-id 9`）退出码 1、stderr 含 `SOURCE_NOT_FOUND`，
见 `tests/integration/test_catalog_scan.py::test_cli_reports_json_results_and_structured_errors`。

## 4. D 级故障注入与恢复场景结果

任务卡要求的 8 个必测场景全部真实执行（临时夹具，Windows 真实路径语义）：

| # | 场景 | 证据 |
|---|---|---|
| 1 | 空库迁移成功 / 重复运行幂等 / 高版本在任何写副作用前拒绝 / 版本与 schema 不一致报 `SCHEMA_INCONSISTENT` | `test_fresh_database_migrates_to_the_supported_version`、`test_repeated_migration_is_idempotent`、`test_too_new_schema_is_refused_before_any_write_side_effect`、`test_versioned_database_with_missing_core_object_is_inconsistent`、`test_tables_without_a_usable_version_are_inconsistent` |
| 2 | 扫描中断后重启继续，无半成品资产、无重复逻辑任务 | `test_interrupted_scan_resumes_after_restart_without_duplicate_jobs`（中断点 2/5 文件；重启后 `scan_id` 不变、job 仍 1 条、`attempts=1`） |
| 3 | 租约过期可重领；旧 token/旧 revision 迟到提交被拒且不覆盖新结果 | `test_expired_lease_can_be_taken_over_and_the_old_result_is_refused`、`test_illegal_transitions_are_refused`（`STALE_INPUT_REVISION`/`PROCESSOR_MISMATCH`）、`test_expired_lease_result_is_refused_and_recovered_later` |
| 4 | 同路径内容替换：生成新资产，旧资产与人工信息不被覆盖 | `test_same_path_content_change_rebinds_without_losing_the_old_asset`（含同 size、mtime 还原后的替换） |
| 5 | 同字节两位置共享资产；移除其中一个位置后另一个仍可用 | `test_scan_records_identity_for_hard_fixtures`、`test_two_sources_share_one_asset_and_survive_losing_one_location` |
| 6 | 中文 / 空格 / 长路径来源可扫描；重叠来源被拒绝 | `test_scan_records_identity_for_hard_fixtures`（`照片 库` + 深度 >220 字符的中文长路径）、`test_source_registration_rejects_overlap_and_the_data_root` |
| 7 | 来源离线不导致整批位置被判定缺失 | `test_offline_source_blocks_the_scan_without_missing_marks`（BLOCKED、`finished_at` 为空、位置全 PRESENT、`last_successful_scan` 不变） |
| 8 | 正常 catalog 使用 WAL；`BLOCKED -> QUEUED` 恢复不会提前把 `scan_run` 标为 RUNNING | `test_fresh_database_migrates_to_the_supported_version`（`journal_mode=wal`）、`test_offline_source_blocks_the_scan_without_missing_marks`（`start_scan` 复用后仍为 BLOCKED）、`test_blocked_job_is_not_claimable_until_it_is_resumed` |

额外执行的边界与注入（任务卡“异常/边界”清单）：目录联接不被跟随（含自指联接）、路径键碰撞导致 `FAILED` 且不写位置、
最终提交前磁盘不足导致 `FAILED` 且不产生 MISSING、0 字节/非图片/只读文件可扫描、数据根位于来源内部时拒绝注册、
活动 WAL 数据库的只读诊断不修改主库数据、迁移失败回滚后版本号不变。

ADR-0002 §12 的 16 项聚焦复核对照（供 C2 使用）：

| ADR §12 | 证据 |
|---|---|
| 1 `assets.sha256` 由数据库唯一约束保护 | `test_asset_sha256_is_the_unique_content_identity` |
| 2 `UNIQUE(source_id, path_key)` 数据库层生效 | `test_location_uniqueness_is_per_source_and_path_key` |
| 3 五字段 job 逻辑键全部非空 | `test_job_logical_key_has_five_non_null_fields` |
| 4 状态/availability/attempts/max_attempts/progress 由 CHECK 防护 | `test_job_logical_key_has_five_non_null_fields`、`test_location_uniqueness_is_per_source_and_path_key`、`test_a_source_has_at_most_one_active_scan_run` |
| 5 迁移失败不出现“表建一半、版本已前进” | `test_failed_migration_rolls_back_and_keeps_the_old_version` |
| 6 高版本在写副作用前被拒绝 | `test_too_new_schema_is_refused_before_any_write_side_effect`（断言数据库字节与目录项均未变） |
| 7 版本号与核心表/列/唯一索引/约束一致 | `test_versioned_database_with_missing_core_object_is_inconsistent`（`verify_schema` 对比由迁移脚本生成的期望 schema） |
| 8 正常连接确认 WAL | `test_fresh_database_migrates_to_the_supported_version`、CLI `catalog info` |
| 9 中断/离线不批量标 MISSING | `test_offline_source_blocks_the_scan_without_missing_marks` |
| 10 成功缺失对账与 `last_successful_scan` 同一最终事务 | `test_disk_space_shortage_at_the_final_commit_keeps_every_identity`（最终事务失败时无 MISSING、`last_successful_scan` 不前进） |
| 11 同字节多位置只产生一个资产 | `test_scan_records_identity_for_hard_fixtures`、`test_two_sources_share_one_asset_and_survive_losing_one_location` |
| 12 同路径换内容只改位置绑定，旧资产仍存在 | `test_same_path_content_change_rebinds_without_losing_the_old_asset` |
| 13 旧 token/过期 token/旧 revision/错误 processor 不写入结果 | `test_expired_lease_can_be_taken_over_and_the_old_result_is_refused`、`test_illegal_transitions_are_refused`、`test_expired_lease_result_is_refused_and_recovered_later` |
| 14 `BLOCKED -> QUEUED` 不提前置 RUNNING | `test_blocked_job_is_not_claimable_until_it_is_resumed`、`test_offline_source_blocks_the_scan_without_missing_marks` |
| 15 中文/空格/长路径使用真实 Windows 语义 | `test_scan_records_identity_for_hard_fixtures`（>220 字符路径 + 中文 + 空格）、`test_directory_junction_is_never_followed` |
| 16 测试只用临时夹具 | 所有用例使用 `tmp_path`；本任务未读取任何真实照片目录 |

## 5. 未验证项、限制与“不可声称”

1. **C2 聚焦复核未执行。** 本报告只提供证据，不是复核结论；P1-01 在复核前不得标 PASS。
2. **P1-02 范围未实现**：无稳定等待窗口、无 hash 前后双重 stat、无 file-id 移动加速、无目录级断点游标、
   无生产级缺失对账性能优化。当前扫描会重新 hash 全部文件，只用于验证身份与恢复语义，不代表 P1-02 的生产算法。
3. **单写协调器是进程内单写**：跨进程并发由 SQLite WAL + `BEGIN IMMEDIATE` + busy timeout 保护，
   但本任务没有做跨进程调度压力测试。
4. **只读预检的 sidecar 例外**：数据库存在 `-wal` 时使用普通只读连接，SQLite 可能在该目录创建/沿用侧车文件；
   干净数据库路径已用 `immutable=1` 避免。该取舍需 C2 确认是否满足 ADR §7.4 的意图。
5. **未运行打包（C 级）**：P1-01 不涉及打包，未重跑 PyInstaller；`dist/PhotoSearch` 未新增运行数据
   （数据根仍在用户数据目录）。
6. **未验证项**：`GTX 1660 6 GB` 实机（仍 P4-07/P4-08 BLOCKED）、语义检索质量（需人工标注保留集）、
   解码池与缩略图前置（P1-02/P1-03）；`format/width/height` 在 P1-01 一律为 NULL（ADR §3.5 允许）。
7. **不可声称**：不得据本任务声称“扫描算法已完成”“迁移在真实大库上验证过”“跨进程调度已验证”或任何搜索质量结论。

## 6. 交接记录

```text
任务 ID / 状态：P1-01 / IMPLEMENTED / D 级验证完成 / 等待 C2 聚焦复核（未 PASS）
执行模型 / reasoning：用户在当前 Codex 会话手动选择 DeepSeek 高推理档；本会话未切换、未声称切换
基线提交号：0453200（P0-07 完成）+ 8b7a588（P1-01 状态同步），实现起点工作树干净
已完成行为：按 ADR-0002 落地版本化迁移与 schema 校验、最小数据契约（6 表 + 约束）、
  Windows 路径键与来源边界、最小持久扫描任务与租约/迟到提交规则、内部 CLI 入口与结构化错误；
  新增 55 个测试并真实执行 8 个必测故障注入/恢复场景
修改文件：src/photo_search/{config.py,__main__.py}、src/photo_search/catalog/**、
  src/photo_search/ingestion/**、src/photo_search/jobs/**、
  tests/unit/test_catalog_{paths,migrations,constraints,jobs}.py、tests/integration/test_catalog_scan.py、
  本报告、docs/development-status.md、docs/tasks/P1-02.md
数据库/接口变化：新增 catalog schema v1（6 张表）与 CLI `catalog` 子命令；未新增 HTTP API，
  GET /api/v1/health 行为不变；未改 ProcessorSpec/RuntimeConfig/device_policy
实际命令与结果：uv run pytest -o addopts="" -q tests/unit tests/integration（0，106 passed 1 skipped，72.53s）、
  uv run ruff check .（0）、git diff --check（0）、catalog CLI init/add-source/scan/status（均 0，--json 结构化）、
  catalog scan --source-id 9（1，SOURCE_NOT_FOUND）
未验证项与阻塞：C2 聚焦复核；P1-02 生产扫描算法；跨进程调度压力；GTX 1660 实机（P4）；
  docs/reports/P0-06-model-evaluation.json 的历史不一致保持只记录、不修改
失败修复轮次：3 轮（catalog_counts 参数绑定错误、job lease CHECK 用例本身写错、
  immutable 只读连接下 PRAGMA journal_mode 误报 delete 改为读取数据库文件头）
下一任务：P1-01 的 C2 聚焦复核（C2 / GPT-5.6 Sol / high），随后 P1-02 流式扫描、稳定等待、
  hash 前后校验、缺失判定（DS-H / DeepSeek V4.1 Flash / 高推理），任务卡 docs/tasks/P1-02.md
独立审查者与结果：待 C2；本报告已按 ADR-0002 §12 逐条列出证据
```
