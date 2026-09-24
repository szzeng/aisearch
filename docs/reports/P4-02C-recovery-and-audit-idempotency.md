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

## 11. 修复轮（主审 F1/F2 + 修复面复核 F3，2026-09-24）

**状态：`IMPLEMENTED / VALIDATION COMPLETE（B + D）`，`ENGINEERING REVIEW PASS（修复面）`，
`SOL FOCUSED REVIEW PENDING` —— 未标 P4-02C PASS、未启动 P4-02B。** 修复依据是主审报告
[P4-02C-dsh-engineering-review.md](P4-02C-dsh-engineering-review.md) 的 F1/F2，以及同报告
「修复面复核」R4 发现的 F3，改动全部落在
本卡允许修改面内：`src/photo_search/indexing/{store,cli}.py`、
`tools/diagnostics/index_maintenance_probe.py`、本卡测试；**未新增依赖、未改 schema/migration、
未改检索语义或 status/错误码信封**。

### 11.1 F1：审计决策收敛到「三元组一次」

`IndexCoordinator.audit_and_schedule_repairs` 现在先把「仍需投影」的工件按
`(space_id, asset_id, generation_id)` 分组，每个三元组只决定**一个**目标：该三元组**最新的工件行**
（按 `(created_at, artifact_id)` 定序），再把它交给 `_schedule_existing` 的审计路径。
`_schedule_existing` 的复用判据与 `schedule_projection_refresh` 的显式 revision 语义**未动**；
不可读工件的 `RECLAIMED`/`requires_reexecution` 分支逐字未动。

为什么这样修：一次投影是**三元组**级的事实，而一个资产在同一 generation 上合法地可以拥有多个
embedding 工件（P4-01C 内容变更发布第二个、第一个仍是 `PUBLISHED`）。主审实测「逐工件」判断在
该状态下每次审计各分配 1 条、revision 交替上升。改判后，「三元组的最新工件已有未确认事件」
与 `_schedule_existing` 的复用条件变成同一句话：重复审计零分配。真实缺失仍然排程——条目确认丢失 /
Lance 行缺失 / 工件被换时，该三元组的最新工件没有可复用的未确认事件。

修复前后对照（主审自建探针 `tmp/p402c-review/audit_growth_probe.py`，同一夹具同一状态）：

| 探针 | 修复前 | 修复后 |
|---|---|---|
| 变体 A：连续 4 次审计（无损坏、无 drain） | `PENDING 5 → 7 → 9 → 11` | **`3 → 3 → 3 → 3`**，`scheduled` 恒为 `[]` |
| 变体 B：`drain --repair` ×3（最早事件 payload 校验失败，drain 确实卡住） | `5 → 7 → 9` | **`3 → 3 → 3`**，拒绝恒为 `INDEX_SUBMISSION_FAILED` |

证据：`test-results/p4-02c-review-audit-growth-prefix.json`（修复前）与
`test-results/p4-02c-review-audit-growth.json`（修复后，均在 `tmp/p402c-review/` 的项目内临时夹具上）。

新增回归用例（`tests/integration/test_p4_02c_recovery_and_idempotency.py`）：

1. `test_one_generation_with_two_artifacts_keeps_the_audit_bounded`：内容变更 → 三元组两个
   `PUBLISHED` 工件、两个事件同 generation；连续三次审计零新增且 `scheduled` 为空；随后 `drain`
   只落**最新工件**的向量（`confirmation_revision == Lance.projection_revision == 1`、
   旧向量不可达、旧工件事件按 supersede 空确认）；再删除 `index_entries` 行，同三元组仍被
   排程一次并收敛（`scheduled == 1`）。
2. `test_a_stuck_drain_does_not_grow_through_repeated_repairs`：同一三元组 + 最早事件
   `payload_checksum` 失效（`_validate_event_payload` 的失败关闭守卫，审计不修复它），
   三次 `drain --repair` 行数不变、状态恒为 `{PENDING: 3}`、拒绝恒为 `INDEX_SUBMISSION_FAILED`。

反向验证（变异）：把审计退回修复前的「逐工件」实现（`tmp/p402c-review/mutation_m4.py`，
临时 pytest 插件，**不改仓库文件**）→ 上述两条用例**都失败**，其余用例亦按预期失败
（`test-results/p4-02c-fix-mutation-m4.xml`）。用例确实守住了 F1，而非空转。

### 11.2 F2：`--json` 机器可读契约与非 ASCII 路径

- `indexing/cli.py`：`--json` 输出改为 **ASCII 安全 JSON**（`_emit` / `_emit_error` 的
  `ensure_ascii=True`），并在模块 docstring 写明「机器可读输出不得依赖控制台代码页」。
  人类可读（非 `--json`）输出、退出码 0/1/2 语义与错误码**未改**。
- `tools/diagnostics/index_maintenance_probe.py::_run_cli`：给子进程注入 `PYTHONIOENCODING=utf-8`、
  以 `errors="replace"` 读取、并对空捕获做保护——不再出现
  `AttributeError: 'NoneType' object has no attribute 'strip'`。

修复前后对照（同一探针 `tmp/p402c-review/cli_encoding_probe.py`，均为真实 CLI 子进程的原始字节）：

| 输入 | 修复前 stdout 的 UTF-8 解码 | 修复后 |
|---|---|---|
| `--model-dir <ASCII>`（模型缺失） | ok（exit 1） | ok（exit 1） |
| `--model-dir <含中文>`（模型缺失） | `UnicodeDecodeError: 'utf-8' ... byte 0xd0 in position 158` | **ok**（exit 1），stdout 全为 ASCII |

证据：`test-results/p4-02c-review-cli-encoding-prefix.json`（修复前）与
`test-results/p4-02c-review-cli-encoding.json`（修复后）。

**卡面必测命令现在可按原文执行**：用 `tmp/p402c-review/run_recovery_zh.py`（工作目录名以
`\uXXXX` 转义构造，避免任何 shell/控制台编码介入）运行
`--mode recovery --work-dir "<项目内 中文 空格 目录>"` → **exit 0、`recovery.ok = true`、
10/10 检查为真**（`test-results/p4-02c-fix-recovery-zh.json`），审计行数 3 → 3 → 3。

新增回归用例：`test_the_json_entry_point_survives_a_non_ascii_path`——子进程 `--json` 输出
`isascii()` 为真、`json.loads(stdout.decode("utf-8"))` 成功、`model_dir` 详情回显中文路径、
拒绝仍发生在打开目录库之前（派生指纹不变），且工具侧 `_run_cli` 对同一调用不再抛异常。

变异证据：m7（用子进程 `sitecustomize` 把 `json.dumps` 强制回 `ensure_ascii=False`）→
该用例**失败 1 例**（子进程断言无法被进程内插件触及，故用子进程注入证明判别力）。

### 11.3 F3：等待重执行的最新工件不得被旧工件越权覆盖

修复面复核（主审报告「修复面复核」R4）发现的**新交互缺陷**：内容变更三元组里若「等待重执行」
的正是**最新工件**，第一版修复仍会把**较旧工件**作为修复目标并给它分配更高的 revision，
于是操作员刚重发布回来的最新工件反而被 supersede——搜索继续命中**变更前的向量**，
而 outbox / `index_entries` / `awaiting_reexecution` 与随后的审计全部显示健康（无任何信号）。

修补：审计决策改为以三元组**全部工件**（含刚置 `RECLAIMED` 的）确定「最新工件」，
只有当这个最新工件本身也在待修复集合里时才排程；否则该三元组只由 `requires_reexecution` 报告，
修复交给既有恢复入口。新增用例
`test_a_reclaimed_newest_artifact_outranks_the_older_one`。

修复前后对照（`tmp/p402c-review/f3_probe.py`，2 工件三元组 + 损坏最新工件，逐字节等价重执行）：

| | 第一版修复 | 现修订 |
|---|---|---|
| `drain --repair` 审计 | `scheduled = 1` / `requires_reexecution = 1` | **`scheduled = 0`** / `requires_reexecution = 1` |
| `reexecute` → `drain` 后的 entry | `f3-41`（旧工件）rev 2 | **`f3-41-changed`（最新工件）** rev 1 |
| 与最新向量 / 变更前向量的距离 | `2.02` / **`0.0`（旧向量仍在服务）** | **`0.0`** / `2.02` |

证据：`test-results/p4-02c-review-f3.json`；变异 m6（去掉该守卫）→ **恰好 1 例失败**，
即上述新用例（`test-results/p4-02c-fix-mutation_m6.xml`）。

### 11.4 修复轮验收（最终冻结工作树）

| 证据 | 结果 |
|---|---|
| focused `test-results/p4-02c-fix-focused.xml` | **11 / 0 / 0 / 0**（14.4 s；7 原有 + 4 条新用例） |
| 受影响面 `test-results/p4-02c-fix-boundaries.xml` | **36 / 0 / 0 / 0**（23.6 s） |
| 全量 `test-results/p4-02c-fix-full.xml` | **1670 / 0 / 0 / 48**（225.9 s；基线 1666 + 4 条新用例） |
| 变异 m1–m7 `test-results/p4-02c-fix-mutation_m*.xml` | 失败 **6 / 5 / 1 / 8 / 3 / 1 / 1** 例；m5（取最旧工件）命中 F1 两例 + F3，m6（去 F3 守卫）恰好命中 F3，m7（子进程 `ensure_ascii=False`）命中 F2 |
| `--mode all` `test-results/p4-02c-fix-probe-all.json` | 六场景 `ok=true`；entry applied 300 / 第二次 0 / 持锁 p95 124.196 ms / 审计 11.176 ms-per-artifact；busy_timeout 1 次拒绝后 `semantic_ready = 72`；injections 4/4；cli 5/5 |
| 中文+空格工作目录 `test-results/p4-02c-fix-recovery-zh.json` | 工具 exit 0，`recovery.ok = true`，10/10 检查为真 |
| 主审探针复跑（`p4-02c-review-isolation.json`、`p4-02c-review-dry-run.json`） | 11 个隔离事件 `count 11 / sample 10 / sample_truncated true`、字段集精确、无路径字段；多目标 `1 republished + 1 ARTIFACT_INVALID` 互不影响；`--dry-run` 快照 `identical=true`、SQLite trace 写语句 `[]` |
| `ruff check .` / `git diff --check` / `uv lock --check` | 全 exit 0 |

改动面（相对 `665109e`，`git diff --stat`）：`src/photo_search/indexing/store.py`（+60 行）、
`src/photo_search/indexing/cli.py`（12 行）、`tools/diagnostics/index_maintenance_probe.py`（15 行）、
本卡测试（+237 行，4 条新用例）。**禁止面仍零改动**：migration `0001`–`0015`、`catalog/schema.py`（`n = 15`）、
outbox/entry 状态词表、ADR/companion、`taken_time`/`capture_desc`/`SearchFiltersEchoV2`、
`search_projection` 键集、`search/retrieval.py`、`api/**`、`web/**`、`PhotoSearch.spec`、
`tools/packaging/**`、`packaged_api_smoke.py`、`pyproject.toml`、`uv.lock`。

### 11.5 仍未验证 / BLOCKED（沿用 §9 口径）

真实中文 CLIP 权重的端到端重执行仍 **BLOCKED**（两处默认模型目录实测不存在），
`republished` 结论仍是非真实模型的确定性注入证据；30 万档对领取谓词与审计决策成本的影响、
ANN/§12.3 的 0.95、API P95、GTX 1660、冻结包内该 CLI 入口的可达性均**未验证**。
本轮未处理主审非阻断观察 O1（结果不含适配器 `resolved_device`）与 O2（`--device-policy` 默认
`cpu_allowed` 而 `--device` 必填）；人类可读（非 `--json`）输出仍随控制台代码页，属既有行为，
本轮只收口机器可读契约。

### 11.6 停止点

修复轮的诚实状态是 `IMPLEMENTED / VALIDATION COMPLETE（B + D）` +
`ENGINEERING REVIEW PASS（修复面）`（见主审报告「修复面复核」R7），
**仍未标 P4-02C PASS**。按卡面路由，下一步是**新会话**的
**C2 / GPT-5.6 Sol / high 聚焦终审（必选，不得记 `SOL_NOT_REQUIRED`）**，只审 V3.1 §11 相关
不变量（领取谓词、revision/幂等键、旧 revision 不回滚、恢复链与失败关闭边界）及其直接回归；
通过后才标 **P4-02C PASS**，并把 **P4-02B** 的前置状态更新为「A2/A3 已收口」。
模型切换始终由用户在 Codex 中手动完成。

## 12. F4 修复轮（Sol 聚焦终审，2026-09-24）

**状态：`IMPLEMENTED / VALIDATION COMPLETE（B + D）`，`SOL SHORT RE-REVIEW PENDING`
—— 未标 P4-02C PASS、未启动 P4-02B。** 依据 [Sol 聚焦终审](P4-02C-sol-focused-review.md)
的 **F4（阻断）**；改动仍只落在本卡允许修改面内（`store.py` 的审计排序 + 本卡测试），
未新增依赖、未改 schema/migration、未改状态词表/检索语义/错误码信封。

### 12.1 F4 与最小反例

`audit_and_schedule_repairs` 用 `(artifact.created_at, artifact_id)` 选三元组的「最新工件」。
但 `artifacts.created_at` 是**毫秒**粒度，`artifact_id` 的字典序与发布顺序无关：同一毫秒内两次
合法内容发布（内容变更流程）时，审计会把**旧内容**当成当前工件、为它分配更高的
`projection_revision`，drain 之后 **旧向量成为搜索行**，而 outbox / `index_entries` /
审计/`awaiting_reexecution` 全部显示健康。

主审用 Sol 自己的探针 `tmp/p402c-sol/tie_probe.py` 独立复现（修复前）：

| | 修复前 | 修复后 |
|---|---|---|
| 审计结果 | `scheduled = ['p4-02c-41']`（旧内容） | **`scheduled = []`** |
| 事件序列（按 revision） | 旧 0 → 新 1 → **旧 2** | 旧 0 → 新 1 |
| drain 后 Lance `projection_revision` | 2 | **1** |
| 与旧向量 / 与新向量距离 | **0.0** / 1.8838 | 1.8838 / **0.0** |
| JUnit | `test-results/p4-02c-f4-repro.xml` **1 / 1** | `test-results/p4-02c-f4-fixed.xml` **1 / 0 / 0 / 0** |

### 12.2 修复

三元组的「当前工件」改为按**权威顺序**判定：每个工件以其**发布 revision**
（`MIN(projection_revision)`，即发布时盖上的 `catalog_filter_revision`，正是 drain 判定
「谁的投影获胜」的同一个值）排序；`job_id`、`created_at`、`artifact_id` 只作为
「该工件的发布事件已不在 outbox」时的确定性兜底。取 `MIN` 而不是 `MAX` 是关键：审计自己分配的
事件总是高于该三元组既有 revision，用 `MAX` 会让审计事件反过来改变排序。

### 12.3 回归与变异

- 新增用例 `test_a_same_millisecond_content_change_keeps_the_newest_vector`：同一毫秒、
  反向字典序 artifact_id（`p4-02c-41` vs `a-new-content`）、真实发布路径；断言审计零排程、
  drain 后 `confirmation_revision == Lance.projection_revision == 1`、新向量命中且旧向量不可达。
- 变异 m8（把排序改回 `(created_at, artifact_id)`）→ **恰好 1 例失败**，即该用例
  （`test-results/p4-02c-f4-mutation_m8.xml`：12 / 1 / 0 / 0）。
- Sol 要求的另两条同步复核（本轮重跑通过）：**F3**（等待重执行的最新工件不被旧工件越权覆盖，
  `test_a_reclaimed_newest_artifact_outranks_the_older_one`）与**真实缺失仍排程**
  （`test_repeated_audits_do_not_grow_the_backlog_but_a_real_loss_still_schedules`、
  `test_one_generation_with_two_artifacts_keeps_the_audit_bounded`）。

### 12.4 验收（最终冻结工作树）

| 证据 | 结果 |
|---|---|
| focused `test-results/p4-02c-f4-focused.xml` | **12 / 0 / 0 / 0**（14.9 s；7 原有 + 5 新用例） |
| 受影响面 `test-results/p4-02c-f4-boundaries.xml` | **36 / 0 / 0 / 0**（23.3 s） |
| 全量 `test-results/p4-02c-f4-full.xml` | **1671 / 0 / 0 / 48**（215.1 s = 基线 1666 + 5 条新用例） |
| Sol 反例探针 `p4-02c-f4-{repro,fixed}.xml` | 修复前 1/1 failure → 修复后 **1/0/0/0** |
| 变异 m8 `p4-02c-f4-mutation_m8.xml` | 12 / **1** / 0 / 0（命中的就是新 F4 用例） |
| `--mode all` `test-results/p4-02c-f4-probe-all.json` | 六场景 `ok=true`；entry applied 300 / 第二次 0 / 持锁 p95 119.944 ms / 审计 8.044 ms-per-artifact |
| F1/F3 反例复跑（`p4-02c-review-audit-growth.json`、`p4-02c-review-f3.json`） | 审计行数 A `3→3→3→3`、B `3→3→3`；F3 `scheduled = 0`、`requires_reexecution = 1`、恢复后 entry 为最新工件（新 0.0 / 旧 2.0193） |
| 隔离/多目标/dry-run/编码/中文工作目录 | 11 个隔离事件样本边界、`1 republished + 1 ARTIFACT_INVALID`、`--dry-run` 零写库、`--json` 两种路径均 UTF-8、`--mode recovery --work-dir "<中文 空格>"` exit 0 且 10/10 |
| `ruff check .` / `git diff --check` / `uv lock --check` | 全 exit 0 |

### 12.5 未验证 / 边界

若某工件的**发布事件本身被手工删除**（outbox 行被删，非受支持流程），权威顺序只能退化为
`job_id`/`created_at`/`artifact_id` 兜底；该分支未单独注入，记入未验证项。真实中文 CLIP 权重仍缺失
（端到端重执行 BLOCKED）、30 万档审计/领取成本、ANN 0.95、API P95、GTX 1660、冻结包内 CLI
可达性沿用 §11.5。

### 12.6 停止点

按 Sol 报告要求，修复后的 **Sol 短审只覆盖 F4 与上述直接回归**（不重走全量 diff）；
通过后才标 **P4-02C PASS**，并把 **P4-02B** 的前置更新为「A2/A3 已收口」。
模型切换始终由用户在 Codex 中手动完成。
