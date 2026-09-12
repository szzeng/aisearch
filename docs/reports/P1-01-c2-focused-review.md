# P1-01 — C2 聚焦复核

任务：P1-01 — 资产 / 位置 / 来源 / 扫描最小持久任务 / 迁移与约束  
复核角色：**C2 / GPT-5.6 Sol / high**  
复核基线：`92ca7566aeeb007c1e442f19612b932412c07a73`  
冻结契约：`docs/decisions/ADR-0002-catalog-identity-migration-scan-contract.md`  
实现证据：`docs/reports/P1-01-catalog-identity-and-scan.md`

## 1. 结论

**C2 REVIEW FAIL — FIX REQUIRED。P1-01 不得标记 PASS，P1-02 不得开始。**

DS-H 已完成 P1-01 的初始实现和 D 级故障注入，现有回归基线为 `106 passed, 1 skipped`，但测试通过不等于冻结契约满足。聚焦复核发现 4 个阻断问题，其中 ADR-0002 §12 的 16 项最低检查有 2 项不能通过；另有 2 个应随本轮返修一并收紧的边界问题。

本次 C2 只提交审核结论、开发状态和一个窄幅 WAL 只读预检契约修订；**不修改实现代码和测试**。返修由 DS-H 执行，返修后重新跑 D 级验证，再回 C2 做 final focused re-review。

## 2. ADR-0002 §12 最低 16 项复核

| # | 结论 | C2 复核意见 |
|---|---|---|
| 1 | PASS | `assets.sha256` 由数据库唯一约束保护。 |
| 2 | PASS | `(source_id, path_key)` 唯一性在数据库层存在。 |
| 3 | PASS | 五字段 job 逻辑键均为 `NOT NULL` 且有唯一约束。 |
| 4 | PASS | 关键状态、attempts/max_attempts、progress 等值域有数据库约束；但运行时 attempts 语义另见阻断项 B2。 |
| 5 | PASS | 迁移版本推进与 DDL 位于受控事务，已有故障注入覆盖。 |
| 6 | **FAIL** | `Catalog.open(migrate=False)` 可绕过只读兼容预检并直接建立 RW/WAL 连接；因此不能证明“更高 schema 在任何业务写副作用前都被拒绝”。活动 WAL 的只读 sidecar 边界已由本次 C2 窄幅修订，不再单独作为失败理由。 |
| 7 | PASS | 版本与核心 schema 不一致会返回 `SCHEMA_INCONSISTENT`。 |
| 8 | PASS | 正常 catalog 运行连接确认 WAL。 |
| 9 | PASS | 中断/离线不会直接执行最终 missing 对账。 |
| 10 | PASS | missing 对账、scan success 与 `last_successful_scan` 位于最终原子事务。 |
| 11 | PASS | 同字节多位置共享同一资产。 |
| 12 | PASS | 同路径换内容改绑位置，旧资产保留。 |
| 13 | **FAIL** | 过期/迟到执行者在最终提交前虽会被拒绝，但 `observe_location` / `bind_location_content` 的增量持久写入前没有重新校验提交资格；现有 `lease_seconds=0` 测试甚至允许过期执行者留下 `locations_present == 2`。 |
| 14 | PASS | `BLOCKED -> QUEUED` 恢复没有在 claim 前把 scan_run 提前标为 RUNNING。 |
| 15 | PASS | 中文、空格、长路径等使用临时 fixture 覆盖。 |
| 16 | PASS | 测试使用临时目录，没有读取真实照片库。 |

最低 16 项结论：**14 PASS / 2 FAIL**。因此单凭 §12 已不能通过 P1-01。

## 3. 阻断返修项

### B1. 增量持久结果缺少 lease / revision / processor 提交资格校验

涉及：`src/photo_search/ingestion/scanner.py`、`src/photo_search/jobs/queue.py`。

当前每文件处理路径会先持久化 `observe_location(...)`，完整 hash 后再持久化 `bind_location_content(...)`，但这两个写事务没有在写入前调用等价于 `jobs.validate_submission(...)` 的资格校验。最终 success 事务中的校验发生得太晚，不能撤销已经留下的正向结果。

ADR-0002 §6.5 冻结的是“任何会改变持久业务结果的 worker/执行者提交”都必须在写事务内重新验证 `job_id / RUNNING / lease_token / lease_expires_at / input_revision / processor_id`。因此过期执行者不能先写位置/资产绑定、最后再因 stale lease 失败。

返修要求：

- `observe_location` 对本次 worker 产生的正向事实，在对应短写事务内先验证当前提交资格；
- `asset upsert + location binding` 同一短事务内先验证当前提交资格；
- 最终 success 事务继续保留资格校验；
- 更新 stale/expired token、revision、processor 测试，证明无资格执行者不会写入新的持久业务结果。

### B2. `attempts / max_attempts` 运行时语义不符合 ADR-0002 §6.2

涉及：`src/photo_search/jobs/queue.py` 及对应 unit tests。

当前普通可重试失败进入 `RETRY_WAIT` 时没有 `attempts += 1`；现有测试还把再次领取后的 `attempts == 0` 当作预期。过期 lease takeover 会增加 attempts，但没有在原子接管前强制 `max_attempts` 上限，因此同一失败配置仍可能持续接管。

返修要求：普通可重试失败计数 +1；达到 `max_attempts` 后进入 `FAILED` 而不是继续 `RETRY_WAIT`；过期 lease 接管同样必须在事务内计入上一次丢失执行并执行上限判断；`BLOCKED` 继续不消耗普通重试次数。补齐阈值、终态后不可再 claim 的测试。

### B3. `migrate=False` 绕过 schema 兼容只读预检

涉及：`src/photo_search/catalog/db.py` / 对外 catalog 打开路径。

当前 `Catalog.open(migrate=False)` 可跳过 `migrate_database/inspect_database`，直接打开 RW 连接并确认 WAL。即使当前仓库暂时没有业务调用者，这仍是公开的业务写入口旁路，与 ADR-0002 §7.1/§7.4 冲突。

返修要求：要么移除该不安全业务入口，要么在 `migrate=False` 时仍先执行真正的只读兼容检查，并仅允许与当前程序支持版本完全兼容的 schema 进入 RW/WAL 阶段。更高版本必须在 RW/WAL 副作用之前统一返回 `SCHEMA_TOO_NEW`。

### B4. stale hash / 绑定失败仍可能让扫描进入 `SUCCEEDED`

涉及：`src/photo_search/ingestion/scanner.py`、catalog store。

当 hash 完成后 `bind_location_content(...)` 因当前位置已不再匹配本次 observation 而返回 false 时，当前实现只增加 `pending_content` 后继续，最后仍可能执行 missing reconciliation 并把 scan/job 标成 `SUCCEEDED`。

ADR-0002 §5.4 要求输入变化时丢弃 stale hash 并重新排队；§5.2 要求本次需要的正向位置/资产写入完成后才允许成功。

返修要求：绑定观察失效必须阻止当前扫描成功，并进入契约允许的重试路径；不得把“存在 pending content”当作完整扫描成功。新增可重复触发 observation 在 hash 后变化的测试。

## 4. 随本轮一并收紧的边界

### R1. 不接受普通 `scan_runs RUNNING -> RUNNING` 自循环

ADR 只允许 job 的 `RUNNING(expired lease) -> RUNNING(new lease)` 特殊接管，不把它定义为普通 scan_run 自循环。当前 `store.ALLOWED_SCAN_TRANSITIONS` 接受泛化 `RUNNING -> RUNNING`，范围过宽，而且现有恢复流程并不需要借此改变 scan_run 状态。建议删除普通自循环，或把任何必要的特殊路径收进明确的恢复函数和前置条件。

### R2. `file_locations.last_seen_scan_id` 必须显式保证同来源

ADR-0002 §3.4/§3.7 要求 `last_seen_scan_id` 指向同一 `source_id` 的 scan。普通外键只能证明 scan_id 存在，不能证明同来源。当前中心写 helper 接收 `source_id + scan_id` 时应在事务内校验来源一致，并增加负向测试，避免未来调用者绕过扫描器正常路径制造跨来源引用。

## 5. WAL 活动数据库只读预检：C2 窄幅修订

本次复核接受一个**仅针对“预检开始前已经存在 `-wal`”的窄幅例外**：为了只读地看到数据库当前完整状态，SQLite 可以读取已有 WAL，并使用或重建派生的 WAL index / `-shm`。这不等价于允许业务写入。

该例外的边界：

- 干净数据库（预检前没有 `-wal`）仍不得因为兼容预检创建 `-wal/-shm`；
- 已有 `-wal` 时，只允许为读取当前 WAL 状态使用/重建派生 `-shm`；
- 预检阶段不得修改主数据库内容、不得修改已有 `-wal` 内容、不得切换 journal mode、不得写 metadata、不得执行 checkpoint、不得创建业务数据；
- `SCHEMA_TOO_NEW` 仍必须阻断所有业务 RW/WAL 初始化路径。

该修订只解决 SQLite 活动 WAL 的只读观察边界，不改变资产/来源身份、迁移原子性、schema 兼容规则、job 状态机、lease 提交资格或 missing 原子边界。

## 6. 返修后的最低复核证据

DS-H 修复后至少需要重新提供：

- `uv run pytest -o addopts="" -q tests/unit tests/integration` 全量结果；
- `uv run ruff check .`；
- `git diff --check`；
- 无资格 lease/token/revision/processor 在每个正向写边界都不能留下新结果；
- 普通 retry 与 expired takeover 的 `attempts/max_attempts` 阈值测试；
- `migrate=False` 或替代入口面对更高 schema 的“先只读拒绝、无 RW/WAL 副作用”测试；
- stale hash/observation 变化不会让 scan 进入 `SUCCEEDED`；
- R1/R2 对应负向测试。

修复和 D 级验证完成后，切回 **C2 / GPT-5.6 Sol / high** 做短版 final focused re-review。只有复核通过后才能把 P1-01 标记 PASS 并开始 P1-02。

## 7. 本次提交边界

本次 C2 提交不修改 `src/**`、不修改 `tests/**`、不声称重新运行测试，也不改写 DS-H 的历史实现报告。它只记录当前实现相对冻结契约的审核结果、下一步返修入口和 WAL 预检边界修订。