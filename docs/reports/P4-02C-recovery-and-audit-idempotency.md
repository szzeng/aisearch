# P4-02C 实现报告 — 语义索引恢复面与审计幂等（P4-02B 前置）

**状态：`IMPLEMENTED / VALIDATION COMPLETE（B + D）`，`ENGINEERING REVIEW PENDING`
—— 未标 P4-02C PASS、未改动 P4-02B 的依赖结论、未启动 P4-02B。**

本卡收口 P4-02A 独立 DS-H 主审报告 §6.2/§6.3 的 **A2**（一个不可读工件会让整条 drain 永久
停住，且没有受支持的生产恢复路径）与 **A3**（每次 `--repair` 都为未确认条目新增事件，
backlog 无界放大），并按 V3.1 §11 的「revision、幂等键、提交顺序、outbox」高风险口径，
把独立 DS-H 全量工程主审与 **C2 / Sol high 聚焦终审（必选）** 留在本报告的停止点之后。

## 1. 基线与改动清单

- 观察基线：**`36b8d81`**（P4-02A 收口提交；执行前工作树干净，本次先入库该收口产物）。
- 交付文件（`git diff --stat`，未 push）：

| 文件 | 变化 | 内容 |
|---|---|---|
| `src/photo_search/indexing/store.py` | 151 行 | 领取谓词 `CLAIMABLE_EVENT_PREDICATE`（两条领取路径共用）、审计路径幂等复用、`_mark_entry_pending_repair` 抽共用 |
| `src/photo_search/indexing/maintenance.py` | 257 行 | `awaiting_reexecution` 报告块、`ImageEncoder` 边界、`reclaimed_artifacts`、`reexecute_reclaimed_artifacts` |
| `src/photo_search/indexing/cli.py` | 126 行 | 新增 `reexecute` 子命令（`--dry-run`/`--model-dir`/`--manifest`/`--device`/`--device-policy`/`--limit`） |
| `tools/diagnostics/index_maintenance_probe.py` | 219 行 | 新增 `recovery` 场景（A2/A3 机器可读证据） |
| `tests/integration/test_p4_02c_recovery_and_idempotency.py` | 新增 | 7 个聚焦用例（隔离、恢复、审计幂等、非确定性拒绝、CLI、P4-01C 纪律回归） |
| `docs/tasks/P4-02C.md` | 新增 | 本卡 |

`schema_version` 仍 **15**（`SUPPORTED_SCHEMA_VERSION = 15`，未改），migration 零改动；
`index_outbox.state` 词表仍 `PENDING/IN_FLIGHT/CONFIRMED`，`index_entries.state` 仍
`CONFIRMED/STALE/PENDING_REPAIR`，未新增列、未新增表、未新增依赖。

## 2. A2-a：隔离谓词（形式化）

`store.py` 里两条领取路径（`process_next`、`process_batch`）现在共用：

```sql
state IN ('PENDING', 'IN_FLIGHT') AND NOT EXISTS (
    SELECT 1 FROM artifacts AS a
     WHERE a.artifact_id = index_outbox.artifact_id AND a.lifecycle_state = 'RECLAIMED')
```

语义边界（与代码注释一致，是审阅重点）：

1. **只隔离 `RECLAIMED`**：该生命周期只由 P2-03 修复审计（或显式 `reclaim`）写入，代表
   「判定该工件的已发布字节不可再用，需要重执行」；`republish_reclaimed` 是唯一回路。
2. **不是「跳过坏文件」**：工件仍是 `PUBLISHED` 但字节校验失败时，事件照旧被领取并以
   结构化 `ARTIFACT_MISSING` 失败（失败关闭），运维必须先跑审计把它判成 `RECLAIMED`，
   隔离才开始生效。§7 的注入 1 的前两步正是这条边界。
3. **无静默丢失**：被隔离事件仍是未确认行（`PENDING` + `last_error_code`），
   由 `awaiting_reexecution` 块结构化上报；重发布后**同一条事件**被领取并确认。
4. **不改变确认语义**：`semantic_ready` 仍只由 `index_entries.state='CONFIRMED'` +
   READY generation 决定；领取谓词不写入任何行。

`drain` 报告新增块（`status` 与 `index_summary` 的既有键集未改）：
`awaiting_reexecution = {count, sample[{event_id, artifact_id, asset_id, space_id, generation_id,
projection_revision, attempts, last_error_code}], sample_truncated}`，样本上限 10，无路径字段。

## 3. A3：审计幂等

`_schedule_existing` 的**审计路径**（`projection_revision is None`）先取该三元组当前
`MAX(projection_revision)`，再看是否已存在「同一 artifact、未确认」的 `UPSERT_VECTOR` 事件；
若它就是最新的那个，则**复用**（不插入新行、不抬高 revision），并把 `index_entries` 归到
`PENDING_REPAIR`（诚实反映尚未重新投影）。`schedule_projection_refresh`（显式 revision 的权威
投影补查）语义未动；`audit_and_schedule_repairs` 的返回键集仍是
`{"scheduled", "requires_reexecution"}`，未改 P2-03 既有断言面。

真实缺失仍然排程：删除 `index_entries` 行、Lance 行缺失、工件被换三种情况下没有可复用的
未确认事件，审计照旧分配新 revision 并排程（§7 注入 2 后半段）。

## 4. A2-b：受支持的生产恢复入口

`maintenance.reexecute_reclaimed_artifacts(catalog, *, encoder, limit, now_ms, dry_run=0)`：

1. 只读列出 `artifact_type='embedding'` 且 `lifecycle_state='RECLAIMED'` 的工件
   （`reclaimed_artifacts`，按 `artifact_id` 定序、`limit` 受限）；
2. 经 `jobs` 要求 `subject_type='asset'`；经 `file_locations`（仅 `availability='PRESENT'`）+
   `source_roots.root_path` 解析媒体文件；缺位置/缺文件分别记 `NO_PRESENT_LOCATION` /
   `SOURCE_FILE_MISSING` 并跳过（不猜测、不扫描）；
3. 用 **ADR-0003 §7 适配器边界的图像半边** `encoder.encode_image(path)` 取确定性重执行输出，
   校验为有限数值且长度等于工件 dimension（不足/超长/非有限 → `RUNTIME_RESOLUTION_MISMATCH`，
   绝不裁剪或补齐）；
4. 走既有 `ArtifactStore.republish_reclaimed(payload={"vector": [...]})`，由 `prepare` 的
   canonical JSON `checksum_sha256` + `byte_size` 校验兜底「非确定性重执行」（不等价 →
   `ARTIFACT_INVALID`，工件仍 `RECLAIMED`、文件不落地）；
5. 逐工件结构化结果（`republished`/`refused`+code/message/`skipped`+reason），一个目标失败不影响
   其余目标；`dry_run` 只列目标（不编码、不写库，删除源文件时如实报 `skipped`）；
6. 结果里**不含任何库内路径**（只有 `source_id`/`asset_id`/`artifact_id`/processor/revision）。

CLI：`python -m photo_search.indexing.cli reexecute`（`--data-root`、`--limit`、`--dry-run`、
`--model-dir`、`--manifest`、`--device`、`--device-policy`、`--json`）。真实模型路径必须显式给出
`--model-dir`/`--manifest`/`--device`（缺一即退出码 2，**没有静默默认、没有静默 CPU 回退**）；
模型加载失败在打开目录库之前就返回结构化 `RUNTIME_CAPABILITY_UNAVAILABLE`（退出码 1，零写库）。

恢复后的收尾沿用既有入口：`drain --repair`（需要重新排程时）→ `drain`。

## 5. 命令与证据文件

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path
uv run --no-sync python -m pytest -q -p no:cacheprovider `
  tests/integration/test_p4_02c_recovery_and_idempotency.py `
  --basetemp=.pytest-basetemp-p402c/focused --junitxml=test-results/p4-02c-focused.xml
uv run --no-sync python -m pytest -q -p no:cacheprovider `
  tests/integration/test_p4_02a_maintenance_entry.py tests/integration/test_p2_index_submission.py `
  tests/integration/test_p2_acceptance_recovery.py tests/integration/test_p2_search_topup.py `
  tests/integration/test_p4_01c_retrieval_blocks.py `
  --basetemp=.pytest-basetemp-p402c/boundaries --junitxml=test-results/p4-02c-boundaries.xml
uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration `
  --basetemp=.pytest-basetemp-p402c/full --junitxml=test-results/p4-02c-full.xml
uv run --no-sync python tools/diagnostics/index_maintenance_probe.py --mode recovery `
  --work-dir ".pytest-basetemp-p402c/probe-work" --injection-assets 40 `
  --output test-results/p4-02c-recovery.json
uv run --no-sync python tools/diagnostics/index_maintenance_probe.py --mode all `
  --work-dir ".pytest-basetemp-p402c/probe-all" --output test-results/p4-02c-probe-all.json
uv run --no-sync ruff check .
git diff --check
uv lock --check
```

（本机受限令牌下创建 basetemp 报 `WinError 5`：按正常 Windows 权限路径重跑，**未改 ACL**。）

## 6. 验收结果（真实退出码 / JUnit）

| 证据 | 结果 |
|---|---|
| `test-results/p4-02c-focused.xml` | **7 / 0 / 0 / 0**（6.9 s） |
| `test-results/p4-02c-boundaries.xml` | **36 / 0 / 0 / 0**（23.2 s） |
| `test-results/p4-02c-full.xml` | **1666 / 0 / 0 / 48**（217.7 s；基线 1659 + 本卡 7 新用例，48 条为既有环境跳过） |
| `test-results/p4-02c-recovery.json` | tool exit **0**，`recovery.ok=true`，10/10 检查为真 |
| `test-results/p4-02c-probe-all.json` | tool exit **0**，六个场景全部 `ok=true`（entry/injections/contention/busy_timeout/recovery/cli） |
| `ruff check .` / `git diff --check` / `uv lock --check` | 全 exit 0 |

`--mode all` 回归（在新领取谓词下，与 P4-02A 同量级，未放宽门槛）：
`entry` applied **300**、第二次调用 applied **0**、锁等待 p95 **0.020 ms**、持锁 p95 **119.6 ms**、
审计 **9.24 ms/工件**；`contention` drain applied **301**、0 次真实拒绝；
`busy_timeout` 1 次结构化拒绝后全部确认（`state_after.semantic_ready = 72`）；
`injections` 的 restart / crash_window / content_change / two_maintainers 四项全部 `ok=true`；
`cli` 五项检查全部为真。

## 7. D 级注入（逐条前后计数）

证据：`test-results/p4-02c-recovery.json`（`--mode recovery`）与 §6 的聚焦/边界套件。

1. **A2 复现 → 隔离 → 恢复全链**（3 个待确认事件，破坏其中一个工件的字节）：
   首次 `drain` → `ARTIFACT_MISSING`（"published artifact content failed verification"），
   outbox `{PENDING: 3}`（零进展）；`drain --repair` → `requires_reexecution = 1`、
   `scheduled = 0`、applied **2**、outbox `{CONFIRMED: 2, PENDING: 1}`、
   `awaiting_reexecution.count = 1`（样本给出 `p4-02c-42`、`last_error_code=ARTIFACT_MISSING`）、
   坏工件零 Lance 行、条目 `PENDING_REPAIR`；`reexecute`（注入编码器，payload 与原件**逐字节相同**）
   → `republished = 1`、工件回到 `PUBLISHED`，且**派生状态指纹不变**（重发布本身不投影）；
   再 `drain` → applied **1**、`awaiting_reexecution.count = 0`、
   `confirmation_revision == Lance.projection_revision == 0`、`semantic_ready = 43`、审计干净。
2. **A3 幂等**：同一卡住状态连续三次 `drain --repair`：每次 applied 0、scheduled 0、
   `requires_reexecution = 1`、outbox 行数**恒为 3**、`{CONFIRMED: 2, PENDING: 1}` 不变
   （对照主审实测的 3 → 5 → 7 → 9）；随后制造一次真实缺失（删除 `index_entries` 行）→
   `scheduled = 1`、applied 1、行数 4，条目重新 `CONFIRMED` 且
   `confirmation_revision == MAX(projection_revision) == 1`。
3. **非确定性重执行必须被拒**：数值漂移（+0.01）→ `ARTIFACT_INVALID`；维度不足 →
   `RUNTIME_RESOLUTION_MISMATCH`；非有限值（NaN）→ `RUNTIME_RESOLUTION_MISMATCH`。
   三者之后工件仍 `RECLAIMED`、文件未落地、派生状态指纹未变、outbox 仍 `{CONFIRMED: 1, PENDING: 1}`、
   再 `drain` applied 0。
4. **目标不可用时跳过而非猜测**：夹具的合成位置（`D:\synthetic\...`，无真实文件）→
   `SOURCE_FILE_MISSING`、applied/写入为零、工件仍 `RECLAIMED`。
5. **CLI 边界**：`--dry-run`（无模型）退出码 0、`targets/planned` 与真实目标一致、指纹不变；
   缺 `--model-dir`/`--manifest`/`--device` 各退出码 2；模型目录不存在 → 退出码 1 +
   `RUNTIME_CAPABILITY_UNAVAILABLE`，且发生在打开数据库之前（`lifecycle_state` 仍 `RECLAIMED`）。
6. **P4-01C 纪律不回归**（新谓词下）：内容变更 + `after_lance` 崩溃窗口重放 →
   applied 1、`projection_revision == confirmation_revision == 1`、新向量命中、旧向量不再命中、
   outbox `{CONFIRMED: 4}`；两维护者并发（10 个待确认事件）合计确认恰好等于事件数、
   第二条维护者按主审建议的判据（`_contended`：applied>0 或事务数≥1 且锁等待≥注入延迟/2）证实争用、
   派生状态 `lance_rows == lance_unique_assets`、`entries_by_state == {CONFIRMED: N}`。
7. **前台写在维护期间不静默丢失**、**中途重启与不中断指纹一致**：由 P4-02A 既有用例在本次
   边界套件中重跑通过（`test_p4_02a_maintenance_entry.py` 的 inv.6 与 restart 用例，见
   `p4-02c-boundaries.xml`），本卡未改这两条路径的语义。

## 8. 禁止面核对（零改动）

`git diff --name-only` 只含 §1 表格中的 6 个文件；以下面**未触碰**：
migration `0001`～`0015`、`SUPPORTED_SCHEMA_VERSION`（仍 15）、ADR-0001～0011 与 companion、
`taken_time`/`capture_desc`/`SearchFiltersEchoV2`、`search_projection` 键集、
`search/retrieval.py` 排序与 RRF/BM25/距离、`POST /api/v1/search` 信封与错误码、
`src/photo_search/api/**`、`web/**`、`PhotoSearch.spec`、`tools/packaging/**`、
`packaged_api_smoke.py`、`pyproject.toml`、`uv.lock`（`uv lock --check` exit 0）。

## 9. 未验证项与 BLOCKED

1. **真实模型重执行：BLOCKED（权重缺失）**。默认模型目录
   `C:\Users\28288\AppData\Local\PhotoSearchData\models` 与 `D:\PhotoSearchData` 均不存在（实测），
   因此「中文 CLIP 真实编码 → 逐字节等价重发布」只能以**确定性注入编码器**证明同一入口的语义，
   报告中所有 `republished` 结论都是**非真实模型**证据；真实权重下的等价性、耗时与显存未验证。
2. **CLI 的真实模型正路径未端到端跑通**（同上）：已验证的是拒绝路径（缺模型/缺参数）与 dry-run。
3. **30 万档影响未测**：本卡未跑容量与性能；`--repair` 审计 9.24 ms/工件是 400 资产夹具数字
   （与 P4-02A 同量级），30 万档审计成本仍按 P4-02B 口径待测。领取谓词多一次
   `artifacts` 主键 `NOT EXISTS` 判定，其在大库上的成本同样留给 P4-02B 测量。
4. **隔离判据的边界**：`reclaim` 的显式调用（非审计路径）同样会让事件被隔离；本卡只验证了
   「审计 → RECLAIMED」这条链路，显式 `reclaim` 之后的重执行路径未单独注入。
5. 未做的其它项沿用既有口径：真实模型吞吐、ANN/§12.3 的 0.95、A3、API P95、GTX 1660、
   冻结包内该 CLI 入口（未触碰打包面）。

## 10. 停止点与审查路由

1. **独立 DS-H 全量工程主审（新会话）**：完整 diff、隔离谓词与审计幂等的语义审查、
   `awaiting_reexecution` 字段与失败关闭边界、D 级注入真实性、禁止面核对，并做变异测试
   （至少 3 处语义破坏必须让新用例失败：去掉领取谓词 / 去掉审计复用 / 让重执行不看 checksum）。
   通过后写 `docs/reports/P4-02C-dsh-engineering-review.md`，状态
   `ENGINEERING REVIEW PASS / SOL FOCUSED REVIEW PENDING` 并停止。
2. **C2 / GPT-5.6 Sol / high 聚焦终审（新会话、必选）**：只审 V3.1 §11 相关不变量——
   领取谓词、revision/幂等键、旧 revision 不回滚、恢复链与失败关闭边界及其直接回归；
   写 `docs/reports/P4-02C-sol-focused-review.md`。通过后才标 **P4-02C PASS**，
   并把 P4-02B 的前置状态更新为「A2/A3 已收口」。

压缩交接包（给阶段 1/2）：基线 `36b8d81`；改动见 §1 表；结论见 §6/§7；
必审不变量见 §2/§3；关键 diff 定位为 `store.py` 的 `CLAIMABLE_EVENT_PREDICATE` 与
`_schedule_existing`、`maintenance.py` 的 `reexecute_reclaimed_artifacts` 与
`awaiting_reexecution_summary`；最小反例与命令见 §5/§7；未验证项见 §9；
禁止扩大范围见本卡「本卡禁止修改」。
