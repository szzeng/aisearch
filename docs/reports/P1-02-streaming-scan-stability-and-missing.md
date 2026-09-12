# P1-02 — 流式扫描、稳定等待、hash 前后校验、缺失判定

执行模型：**DS-H / DeepSeek V4.1 Flash / 高推理**（由用户在 Codex 中手动选择，本报告只记录事实，不声称任何自动切换）。
任务卡：`docs/tasks/P1-02.md`。基线：P1-01 C2 final PASS 收口提交 `e39a40c`。
冻结契约：`docs/decisions/ADR-0002-catalog-identity-migration-scan-contract.md`（本任务未修改）。

## 1. 交付物清单

| 文件 | 变更 |
|---|---|
| `src/photo_search/ingestion/scanner.py` | 扫描器重写为「流式枚举 + 稳定等待 + hash 前后双重 stat + 分块内容确认」两阶段执行；新增 `StabilityPolicy`、`iter_observations()`、取消/中断路径、进度写入、阶段计时；`collect_observations()` 保留为诊断用物化版本 |
| `src/photo_search/catalog/store.py` | 新增 `observe_location_detailed()`（返回 `LocationObservation`，并在流式枚举中检测 `PATH_KEY_COLLISION`）、`iter_seen_locations()`（keyset 分块读取本次 execution 的位置）、`count_seen_locations()`；`observe_location()` 保持原签名与语义 |
| `src/photo_search/jobs/queue.py` | 新增 `set_progress()`：同一事务内已通过提交资格校验的前提下写单调不减的 `jobs.progress`（非 RUNNING/旧 token/租约过期一律拒绝，不写任何值） |
| `src/photo_search/ingestion/service.py` | `CatalogService` 新增 `stability_window_ms` / `stability_wait_limit_ms` / `wall_clock_ms` / `stability_sleep` 注入点与 `stability_policy()`；`run_scan()` 默认注入服务级稳定策略 |
| `src/photo_search/ingestion/cli.py` | `catalog scan` 新增 `--stability-window-ms` / `--stability-wait-limit-ms`；新增结构化 `SCAN_INTERRUPTED` 错误路径（退出码 1）；既有字段与退出码语义不变 |
| `src/photo_search/catalog/errors.py` | 新增扩展错误码 `SCAN_INTERRUPTED` |
| `tests/integration/test_catalog_scan.py` | 夹具注入 `wall_clock_ms` / `stability_sleep`；文件名夹具默认 1 小时前的 `mtime`；中断用例更新为两阶段语义；绑定资格用例锁定「#3 = asset upsert + bind」；CLI 用例断言新增可观测字段 |
| `tests/integration/test_catalog_scan_streaming.py` | 新增 P1-02 场景套件（10 例） |
| `tests/unit/test_catalog_progress.py` | 新增 `jobs.set_progress` 单元测试（2 例） |
| `tools/diagnostics/scan_scale_probe.py` | 新增只读诊断/测量脚本：在临时目录构造合成夹具并测量峰值内存、各阶段耗时、`reset_execution_coverage` 代价（`--recovery`） |

未修改：`docs/decisions/ADR-0002-*.md`、`src/photo_search/models/**`、`runtime/**`、`workers/**`、
`tools/prepare_models/manifests/**`、`docs/reports/P0-*.md`、`docs/reports/P1-01-*.md`、`pyproject.toml`、`uv.lock`。
**未新增 schema，数据库仍为 `schema_version = 1`，无新依赖（仍只用标准库 + 既有 Pillow 等）。**

## 2. 设计要点（不改冻结语义）

### 2.1 流式、有界内存（架构 §5.1、§5.4）

- 枚举改为 `iter_observations()` 生成器：深度优先，单目录条目排序后逐条 `yield`，内存只与**单个目录的条目数 + 栈深度**有关，整库文件清单不再驻留内存。
- 内容确认阶段不再使用内存列表，而是用 `store.iter_seen_locations()` 以 keyset 分页（默认每块 256 行）回读本次 execution 已观察的位置；每块 `fetchall()` 完成后才写下一个短事务，因此连接上不会留下跨写事务的游标。
- 路径键碰撞检测从「先收集全量再比较」改为流式：`observe_location_detailed()` 发现同一 `path_key` 已被**本次 execution**以不同展示路径观察过时立刻抛 `PATH_KEY_COLLISION`。测试注入的 `observations=[...]` 列表仍走原有 `check_path_key_collisions()` 预检查（P1-01 行为不变）。
- 目录联接/符号链接、展示路径与 `path_key` 分离、来源不变量的规则与 P1-01 完全一致。

### 2.2 稳定等待与 hash 前后校验（架构 §5.1、§5.2、§12.4 第 5 条）

- 新增 `StabilityPolicy(window_ms, wait_limit_ms)`：**默认稳定窗口 1500 ms，单文件/整批等待上限 30000 ms**（可由 CLI/服务配置覆盖；测试可设 0 表示「不等待，直接重排队」）。
- 判定方式：文件 `mtime` 与**墙钟**（不是租约时钟）比较；若 `0 <= now - mtime < window`，先等待剩余窗口再重新 stat；等待期间文件又变化就重新计时。若 `mtime` 晚于墙钟（时钟偏移、相机日期错误），无法用静默期判定，此时**只**依赖 hash 前后的两次 stat 保护（见限制 §5.4）。
- 提交前至少两次间隔检查：静默期（文件最后一次写入 → 本次 stat），以及完整 SHA-256 **之前**与**之后**的两次 stat；两者与将要提交的 `size/mtime_ns` 不一致时丢弃该 hash 结果，重新观测并重算（每文件内联上限 `UNSTABLE_FILE_ATTEMPTS = 3`）。
- 仍不稳定时不固化半截结果：job 回到 `RETRY_WAIT`（`INPUT_UNSTABLE`）、`scan_run` 保持 `RUNNING`；达到 `max_attempts` 时 job 与 `scan_run` 在同一提交内一起 `FAILED`（沿用 P1-01 F1 边界）。
- 记录写入前再校验一次位置行观测（`bind_location_content` 的既有守卫），因此「hash 期间被替换」不会把旧内容绑到位置，也不会覆盖旧资产。
- **批量摊销**：枚举阶段记录的「最新的仍新鲜 mtime」用于一次有界 lead-in 等待，避免刚复制进来的大批文件逐文件各等 1.5 秒。实测 2 万文件时枚举耗时 14.9 s 已超过窗口，`stability_waits = 0`；小目录（500 文件，枚举 0.3 s）只等待一次窗口。

### 2.3 缺失判定与移动/改名（架构 §5.2、§5.4、§12.4 第 1/5/7 条）

- 缺失对账仍是唯一最终事务的一部分：`reconcile_missing()` + `scan_runs=SUCCEEDED` + `finished_at` + `source_roots.last_successful_scan` + `jobs.finish(progress=1.0)` 同一 `BEGIN IMMEDIATE` 提交；成功前仍有 `count_unbound_locations == 0` 防御校验。
- 移动/改名仍由内容身份保证：同 `sha256` 复用同一 `asset_id`，重命名的旧位置按规则转 `MISSING`（允许的「新位置 + 旧位置缺失」），资产与旧位置绑定都不删除。
- 本任务**没有**引入 file-id/文件系统标识的移动加速：任务卡允许「无法无歧义证明移动时新建位置 + 旧位置缺失」，而错误猜测文件标识可能合并不同内容，故按最小风险实现，明确记录为限制（§5.2）。
- 最后一次完整成功扫描之后新增的文件不会被计入缺失：缺失只针对已存在的 `file_locations` 行。

### 2.4 错误、恢复与可观测性（架构 §5.3、§6.3）

- 错误分类保持 P1-01：`PATH_KEY_COLLISION` / `DISK_SPACE_INSUFFICIENT` / 其他确定性错误 → `FAILED`；`SOURCE_OFFLINE` / `ACCESS_DENIED` / `FILE_READ_ERROR` → `BLOCKED`（不产生 MISSING、不推进 `last_successful_scan`）。
- 中断路径：`interrupt_after`（故障注入）、`cancel_check`（进程级取消回调）、`KeyboardInterrupt`（Ctrl-C）统一抛出 `ScanInterrupted`，**不写负向事实、job/`scan_run` 不进入 `SUCCEEDED`、租约原地保留**；恢复仍通过在租约到期后接管同一个 `scan_run`（ADR §5.3），不产生第二个并发 `scan_run` 或重复逻辑 job。
- 进度：枚举阶段 `0.45 · n/(n+2000)`，内容确认阶段 `0.45 + 0.54 · i/total`（上限 0.99），最终提交写 1.0；`set_progress()` 用 `MAX()` 保证单调不减，且只允许在已经通过提交资格校验的同一事务内写入（迟到执行者无法推进进度）。
- 心跳沿用 P1-01 的按剩余租约续租规则，且现在每次短写事务前都会检查；不会因为新增阶段或长等待丢失提交资格。
- CLI 新增字段：`enumerated`、`stability_waits`、`stability_wait_ms`、`prepare_ms`、`enumeration_ms`、`processing_ms`；既有字段与退出码（成功 0 / `CatalogError` 1 / 用法 2）不变。

## 3. 实际运行的命令与退出码

| 命令 | 退出码 | 结果 |
|---|---|---|
| `uv run pytest tests/unit tests/integration`（`pyproject.toml` 已含 `addopts = "-q"`；任务卡命令 `uv run pytest -q tests/unit tests/integration` 等价） | 0 | `133 passed, 2 skipped in 98.21s` |
| `.venv\Scripts\python.exe -m pytest -o addopts="" -q tests/unit tests/integration` | 0 | `134 passed, 1 skipped in 87.11s`（见下方说明） |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `git diff --check` | 0 | 无空白错误 |
| `uv lock --check` | 未重跑 | 本任务未改 `pyproject.toml` / `uv.lock`，依赖未变 |

两次 pytest 结果差异说明（环境事实，不是代码差异）：本机在 **`uv run pytest`** 进程上下文中执行
`cmd /c mklink /J` 返回 `Access is denied.`，因此 `test_directory_junction_is_never_followed` 的夹具无法建立并按其既有逻辑
`pytest.skip`；同一测试用 venv 解释器直接运行时可创建联接并通过（独立最小复现：`uv run pytest` 下 `rc=1 Access is denied`，
`.venv\Scripts\python.exe -m pytest` 下 `rc=0 Junction created ...`）。第二个 skip 是 P0 起就存在的 GPU-opt-in 用例
（`set PHOTO_SEARCH_GPU_TESTS=1`）。两种运行方式下 **P1-01/P1-02 扫描相关用例全部通过**。

规模/内存测量（真实执行，临时合成夹具，未读取任何真实照片库）：

| 命令 | 结果 |
|---|---|
| `uv run python tools/diagnostics/scan_scale_probe.py --files 5000 --json` | 5000 文件：总 13191 ms（枚举 3814 ms、内容确认 9371 ms、claim+coverage reset 1 ms）、379 文件/s、**峰值工作集 29.2 MB**（扫描前 25.4 MB，增量 3.6 MB）、`stability_waits=0` |
| `uv run python tools/diagnostics/scan_scale_probe.py --files 20000 --json` | 20000 文件：总 53391 ms（枚举 14851 ms、内容确认 38518 ms、claim+coverage reset 1 ms）、374.6 文件/s、**峰值工作集 30.1 MB**（扫描前 25.4 MB，增量 4.6 MB）、`stability_waits=0` |
| `uv run python tools/diagnostics/scan_scale_probe.py --files 20000 --recovery --json` | 中断执行 17152 ms；恢复执行 `prepare_ms = 31 ms`（= claim + `reset_execution_coverage` 覆盖 20000 个位置）、恢复总 51949 ms、峰值 30.7 MB |
| `uv run python tools/diagnostics/scan_scale_probe.py --files 500 --files 2000` | 500 文件 2456 ms / 峰值 25.9 MB；2000 文件 5482 ms / 峰值 26.8 MB（同一进程顺序执行，峰值为高水位，仅作量级参考） |

结论：文件数 ×4（5k → 20k）时，峰值工作集增量只从 3.6 MB 变为 4.6 MB（+1.0 MB，主要来自 SQLite 页缓存），
而枚举与内容确认时间近似线性增长 → **内存与目录规模解耦**。P1-01 遗留 follow-up「`reset_execution_coverage()` 在真实规模下的代价」
实测为 20000 位置 31 ms（新扫描时该 UPDATE 匹配 0 行，<1 ms；恢复执行时匹配全部位置，约 1.5 µs/行，按线性外推 50 万位置约 0.8 s，
仅作量级估计，非实测）。

## 4. D 级故障注入与恢复场景结果

任务卡要求的 8 个必测场景全部真实执行（`tests/integration/test_catalog_scan_streaming.py`、`test_catalog_scan.py`，均为临时夹具）：

| # | 场景 | 证据 |
|---|---|---|
| 1 | 稳定等待：正在写入的文件本次不被固化，稳定后再次扫描可正确绑定 | `test_a_continuously_written_file_is_requeued_without_any_content_binding`（等待上限 0 → 立刻 `RETRY_WAIT`，`locations_pending_content=1`、`assets_total=0`、无 MISSING、`last_successful_scan=None`；文件静默并退避后同一 `scan_id` 恢复为 `SUCCEEDED`）、`test_a_fresh_file_is_waited_for_and_then_committed`（真实等待 1500 ms 后绑定并写入 `progress=1.0`） |
| 2 | hash 前后 stat 变化：结果被丢弃并重排，不绑定旧内容、不覆盖旧资产 | `test_an_edit_during_hashing_never_binds_the_old_content`（`unstable_retries=1`，位置绑定到新内容，旧资产 `sha256/byte_size/ACTIVE` 不变）、`test_input_change_during_hashing_discards_the_stale_hash`、`test_persistent_instability_requeues_instead_of_succeeding` |
| 3 | 扫描中途进程被杀：重启后继续同一 `scan_run`，无重复逻辑 job、无半成品资产 | `test_processing_cancellation_resumes_the_same_scan_without_duplicate_jobs`（取消后 4 行已观察、2 个已确认、2 个 pending、`assets_total=2`；恢复后 `scan_id=1`、`jobs=1`、pending=0）、`test_keyboard_interrupt_keeps_the_lease_and_is_recoverable`（Ctrl-C 后 job 仍 RUNNING 且保留租约、无负向事实；租约过期后接管成功）、`test_interrupted_scan_resumes_after_restart_without_duplicate_jobs`（跨进程重启恢复） |
| 4 | 目录枚举/权限失败：`BLOCKED`，位置不批量转 `MISSING`，`last_successful_scan` 不前进 | `test_enumeration_permission_failure_blocks_without_missing_or_success`（`ACCESS_DENIED`、`scan_run=BLOCKED` 且 `finished_at` 为空、来源 `status=ERROR`、2 个位置仍 PRESENT、MISSING=0；修复后同一 `scan_run` 恢复并正确处理消失文件） |
| 5 | 完整成功扫描的缺失对账 + `SUCCEEDED` + `last_successful_scan` 仍为同一最终事务 | `test_complete_scan_commits_missing_last_success_and_succeeded_together`（成功后四者一致；随后 `BLOCKED` 扫描不改变其中任何一项）、`test_disk_space_shortage_at_the_final_commit_keeps_every_identity`（最终提交失败时无 MISSING、`last_successful_scan` 不前进）、`test_resume_marks_positions_deleted_before_recovery_as_missing` |
| 6 | 改名/同盘移动：`asset_id` 不变；无法证明移动时也不得把内容判成删除 | `test_a_move_into_a_subdirectory_keeps_the_asset_and_reports_both_truths`（新位置 PRESENT 且同一 `asset_id`，旧位置 MISSING 仍保留绑定，`assets_total=1` 无重复资产、无删除）、`test_scan_records_identity_for_hard_fixtures`（同目录改名） |
| 7 | 大目录流式：给出峰值内存/耗时实测与可复现命令，证明内存与文件数解耦 | §3 规模表（5k / 20k 实测 + 可复现命令）、`test_the_production_scan_streams_enumeration_and_position_reads`（生产路径不得调用 `collect_observations`；`iter_seen_locations` 以 2 行/块读取 5 行 = 3 次数据查询 + 1 次结束查询） |
| 8 | 路径键碰撞仍使扫描不能进入 `SUCCEEDED` | `test_streaming_enumeration_detects_a_path_key_collision_without_merging`（流式枚举中第二个路径触发 `PATH_KEY_COLLISION`，`scan_run=FAILED`、job `FAILED`、`last_successful_scan` 为空、先观察到的路径不被静默改写、只存在 1 行位置）、`test_path_key_collision_fails_the_scan_without_writing_locations`（注入列表路径，位置 0 行） |

补充覆盖（任务卡「正常/异常/边界」清单）：稳定窗口可配置且默认值已记录；`jobs.progress` 单调不减、跨事务被旧 token/过期租约拒绝
（`tests/unit/test_catalog_progress.py`）；CLI 新增字段与 `SCAN_INTERRUPTED` 结构化错误；中文/空格/长路径、只读文件、0 字节文件、
非图片文件、目录联接（见 §3 环境说明）、同内容跨来源共享资产、空目录/仅子目录等沿用 P1-01 用例并全部通过。

## 5. 未验证项、限制与「不可声称」

1. **C2 复核未执行。** 本报告是 DS-H 的实现与验证证据，不是复核结论。
2. **无 file-id 移动加速。** 移动/改名只通过完整 SHA-256 复用 `asset_id`；不读取文件系统标识，因此「同一内容被移动到新路径」会留下
   「新位置 PRESENT + 旧位置 MISSING」，不会删除资产，但也不会把两个位置自动合并成一次移动事件。这是本任务允许的取舍。
3. **无目录级断点游标。** 恢复执行会重新枚举整个来源（ADR §5.3 允许）；恢复成本 = 一次完整枚举 + 未确认内容的 hash。
4. **未来时间的 `mtime` 不参与静默期判定。** 当时钟偏移或相机日期晚于系统时钟时，该文件只受 hash 前后两次 stat 保护；
   这是「不能用不可比的时钟做静默期判断」的显式取舍，已在代码注释与本节记录。
5. **文件在枚举之后、内容确认之前消失** 仍按 P1-01 分类返回 `FILE_READ_ERROR` → `BLOCKED`（不产生 MISSING、不推进
   `last_successful_scan`），需要重跑该 `scan_run` 才能成功。这是保守选择（宁可阻塞也不把竞态当删除），属于后续可优化项。
6. **未做性能优化**：每个文件仍是 2 个短写事务（观察 + 绑定）+ 1 次节流后的进度更新，实测 4 KB 合成文件约 375 文件/s
   （其中约 1 ms 级 SQLite 提交为主）。按线性外推，10 万文件约 4.5 分钟（外推估计，非实测）。
7. **未验证**：真实数十万级/百万级图库、网络盘、外接盘反复插拔、跨进程并发调度压力、WAL 侧车与备份在真实规模下的行为；
   GTX 1660 6 GB 实机与语义检索质量仍属 P4 范围。`assets.format/width/height` 仍为 NULL（ADR §3.5 允许，P1-03 处理）。
8. **不可声称**：不得据本任务声称「扫描器已在大规模真实图库验证」「移动检测已完成」「跨进程调度已验证」或任何搜索/AI 质量结论。

## 6. 交接记录

```text
任务：P1-02 流式扫描、稳定等待、hash 前后校验、缺失判定
状态：PASS（D 级验收完成；C2 复核未执行，如需独立审查请按 V3.1 §11 指定 C2）
基线：e39a40c（P1-01 C2 final PASS 收口）
冻结契约：ADR-0002 未修改；schema_version 仍为 1；未新增依赖（pyproject.toml / uv.lock 未改）
稳定窗口默认值：1500 ms；等待上限：30000 ms；hash 前后 stat + 位置行提交守卫；不稳定 → RETRY_WAIT(INPUT_UNSTABLE)
进度：枚举 0.45·n/(n+2000) → 内容确认 0.45+0.54·i/total（≤0.99） → 最终提交 1.0，单调不减
实测：5k/20k 文件峰值工作集 29.2/30.1 MB；reset_execution_coverage 20000 位置 31 ms
下一任务：P1-03 方向、缩略图/预览、缓存分片、坏图隔离
下一任务推荐模型：DS-L / DeepSeek V4.1 Flash / 低推理（复杂媒体兼容可升级 DS-H）
```
内部入口真实运行（`%TEMP%` 临时夹具，中文/空格来源目录，命令与结果逐字来自本次会话）：

| 命令 | 退出码 | 结果 |
|---|---|---|
| `uv run python -m photo_search catalog init --json --data-root <tmp>/data` | 0 | `{"status":"ok","previous_version":0,"schema_version":1,"applied":[1],"backup_path":null}` |
| `... catalog add-source --name 照片库 --path "<tmp>/photos 库" --json` | 0 | `source_id=1`、`status=ONLINE`、`last_successful_scan=null` |
| `... catalog scan --source-id 1 --json` | 0 | `state=SUCCEEDED`、`enumerated=2`、`files_seen=2`、`assets=1`（同字节两位置共享）、`locations=2`、`missing=0`、`stability_waits=0`、`stability_wait_ms=0`、`prepare_ms=1`、`enumeration_ms=1`、`processing_ms=3` |
| `... catalog status --source-id 1 --json` | 0 | `last_successful_scan=1`、`locations=2`、`present=2`、`missing=0` |
| `... catalog scan --source-id 9 --json` | 1 | `{"status":"error","error":{"code":"SOURCE_NOT_FOUND","details":{"source_id":9}}}` |

规模/内存测量（真实执行，临时合成夹具，未读取任何真实照片库）：
