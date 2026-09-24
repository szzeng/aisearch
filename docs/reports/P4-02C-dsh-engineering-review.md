# P4-02C 独立 DS-H 全量工程主审报告

**结论：`ENGINEERING REVIEW FAIL`**（2 条本卡范围内未通过项）；未标 P4-02C PASS、未进入
C2 / Sol 聚焦终审、未启动 P4-02B、未改动 P4-02B 的依赖结论。

- 观察基线：**`665109e`**（P4-02C 实现提交；`git log -1 --oneline` 与 `git status --short`
  实测工作树干净）；逐文件 diff 的相对基线为 **`36b8d81`**（P4-02A 收口提交）。
- 主审**未修改任何 `src`/测试/工具/文档**：新增证据全部是项目内忽略目录
  （`tmp/p402c-review/**`、`.pytest-basetemp-p402c-review/**`）下的临时夹具与探针脚本，
  机器可读输出写入 `test-results/p4-02c-review-*.json|xml`。
- 命中 V3.1 §2.2 六类升级条件？**未命中**（无新依赖、无 schema/migration、无检索语义或协议扩大、
  无不可解释的系统级异常——`WinError 5` 已定位为受限令牌 + pytest 临时目录，属环境口径）。
  故**不建议 DS-MAX**，也**无需转 C1**。

**修复轮与修复面复核状态（同会话追加，2026-09-24）**：F1/F2 已按 §10 停止点修复，随后做了
第二轮独立复核（见文末「修复面复核」），结论为 `ENGINEERING REVIEW PASS（修复面）`；
复核期间另发现并当轮修复 1 条交互缺陷 **F3**（等待重执行的最新工件被旧工件越权覆盖）。
修复前后对照见 `test-results/p4-02c-review-audit-growth{,-prefix}.json`、
`p4-02c-review-cli-encoding{,-prefix}.json`、`p4-02c-review-f3.json`、
`p4-02c-fix-recovery-zh.json`，细节在实现报告 §11 与 `docs/development-status.md`。
本报告开头的 `ENGINEERING REVIEW FAIL` 结论针对**被审修订 `665109e`**；P4-02C 仍未标 PASS，
下一步是必选的 **C2 / Sol high 聚焦终审**。

## 1. 审查范围与读入

按任务卡「固定读取范围」读入：`AGENTS.md`、`docs/development-status.md`、`docs/tasks/P4-02C.md`；
主架构 §7.2/§9.2/§9.3/§12.4，V3.1 §2.2/§11/§12；实现面
`src/photo_search/indexing/{store,maintenance,cli}.py`、`src/photo_search/artifacts/store.py`、
`tools/diagnostics/index_maintenance_probe.py`、`tools/diagnostics/ann_scale_benchmark.py`
（夹具/发布路径）、`tests/integration/test_p4_02c_recovery_and_idempotency.py`；
前序报告 `P4-02A-maintenance-entry.md`、`P4-02A-dsh-engineering-review.md`、
`P4-02A-sol-focused-review.md`、`P4-02C-recovery-and-audit-idempotency.md`。
未重读两份当前文档全文，未扫描无关模块。

## 2. 独立复现（不引用被审报告的结论）

`$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path`；全部命令用主审自建的工作目录
（`.pytest-basetemp-p402c-review/**`、`tmp/p402c-review/**`），未复用被审运行产物。

| 命令/证据 | 主审结果 | 被审报告 |
|---|---|---|
| focused `test-results/p4-02c-review-focused.xml` | **7 / 0 / 0 / 0**（6.76 s） | 7/0/0/0（6.9 s）一致 |
| 受影响面 `test-results/p4-02c-review-boundaries.xml` | **36 / 0 / 0 / 0**（23.57 s） | 36/0/0/0（23.2 s）一致 |
| 全量 `test-results/p4-02c-review-full.xml` | **1666 / 0 / 0 / 48**（210.63 s） | 1666/0/0/48（217.7 s）一致 |
| 证据工具 `--mode all` → `test-results/p4-02c-review-probe-all.json` | exit 0，六场景 `ok=true` | exit 0，六场景 ok 一致 |
| 证据工具 `--mode recovery`（ASCII 工作目录）→ `test-results/p4-02c-review-recovery.json` | exit 0，**10/10** 检查为真 | exit 0，10/10 一致 |
| `ruff check .` / `git diff --check` / `uv lock --check` | 全 exit 0 | 全 exit 0 一致 |

主审自建补充探针（均走生产入口/生产函数，未自建 replay 循环）：

| 探针 | 结论 |
|---|---|
| `tmp/p402c-review/audit_growth_probe.py` → `p4-02c-review-audit-growth.json` | **F1 反例**：审计非幂等（5→7→9→11；卡住时 5→7→9） |
| `tmp/p402c-review/isolation_probe.py` → `p4-02c-review-isolation.json` | 11 个隔离事件：`count 11 / sample 10 / sample_truncated true`、字段集精确、无路径字段、`semantic_ready` 不受影响；多目标重执行互不影响 |
| `tmp/p402c-review/triple_probe.py` → `p4-02c-review-triple.json` | F1 反例的三元组事实：同一 asset/processor 的两条 `PUBLISHED` 工件，两个事件同 `(space, asset, generation)`、revision 0/1 |
| `tmp/p402c-review/dry_run_probe.py` → `p4-02c-review-dry-run.json` | `--dry-run` 零写库：快照 `identical=true`、SQLite trace 的写语句列表为 `[]` |
| `tmp/p402c-review/cli_encoding_probe.py` → `p4-02c-review-cli-encoding.json` | **F2 根因**：非 ASCII 路径下 `--json` 输出不是 UTF-8 |
| `tmp/p402c-review/reclaim_probe.py` → `p4-02c-review-reclaim.json` | 显式 `reclaim` **不能**隔离未确认事件（`ARTIFACT_CONFLICT`），见 §9 |

被审报告 §6 的回归量也逐项复现（`--mode all`）：`entry` applied **300**、第二次调用 **0**、
锁等待 p95 **0.018 ms**、持锁 p95 **120.254 ms**、审计 **7.85 ms/工件**（3 140.2 ms / 400 资产）；
`contention` 前台 30 次修订写 + 10 次发布全部提交、0 次真实拒绝；`busy_timeout` **1** 次结构化拒绝
后全部确认（`state_after.semantic_ready = 72`）；`injections` 四项 `ok=true`；`cli` 五项检查为真。

## 3. 冻结面与依赖审计

`git diff --name-only 36b8d81 665109e` 只有 8 个文件（`docs/development-status.md`、
本卡报告、本卡卡面、`indexing/{store,maintenance,cli}.py`、本卡新测试、诊断工具），
逐一比对基线：

- migration `0001`～`0015`、`catalog/schema.py`（**`n = 15`**，实测未变）、
  `index_outbox.state` 词表（`PENDING/IN_FLIGHT/CONFIRMED`）、`index_entries.state` 词表
  （`CONFIRMED/STALE/PENDING_REPAIR`）：**零改动**；
- ADR-0001～0011 与 companion、`taken_time/ capture_desc / SearchFiltersEchoV2`、
  `search_projection` 键集、`search/retrieval.py`、`POST /api/v1/search` 信封与错误码、
  `src/photo_search/api/**`、`web/**`、`PhotoSearch.spec`、`tools/packaging/**`、
  `packaged_api_smoke.py`、`pyproject.toml`、`uv.lock`：**零改动**（`uv lock --check` exit 0）；
- 无新依赖、无新表/列、无 Redis/Celery/第二向量后端/手工分片。

**真实模型权重（BLOCKED 复核）**：`C:\Users\28288\AppData\Local\PhotoSearchData\models` 与
`D:\PhotoSearchData` 实测**均不存在**，故报告 §9.1/§9.2 的「真实中文 CLIP 重执行」确为 BLOCKED；
`republished` 全部结论仍是**确定性注入编码器**证据（非真实模型），主审接受该口径。

## 4. 逐文件 diff 审查（语义）

1. `indexing/store.py`（+151/-…）：
   - 新增模块级 `CLAIMABLE_EVENT_PREDICATE`，`process_next` 与 `process_batch` **共用同一常量**
     （同一条纪律，非两套谓词）；领取 UPDATE、apply 事务、`_is_superseded`、
     `_record`、确认语义**未动**；
   - `_mark_entry_pending_repair` 抽出共用，复用分支与新分配分支落到同一 `PENDING_REPAIR` 终态；
   - `_schedule_existing` 审计路径（`projection_revision is None`）改为先取三元组
     `MAX(projection_revision)`（不再 +1），再查「同一 artifact、未确认」的最新事件；
     满足 `reusable >= newest` 则复用并返回 `False`（不插行、不抬高 revision），否则 `newest + 1`。
     `schedule_projection_refresh`（显式 revision）路径逐字未动。
2. `indexing/maintenance.py`（+257）：`awaiting_reexecution_summary`（只读、有界样本、无路径）、
   `reclaimed_artifacts`、`ImageEncoder` 协议（ADR-0003 §7 图像半边）、
   `reexecute_reclaimed_artifacts` + `_reexecute_one`（逐工件结构化结果，单目标失败不中断）、
   `drain` 报告新增顶层 `awaiting_reexecution`（`status`/`index_summary` 键集未改）。
3. `indexing/cli.py`（+126）：`reexecute` 子命令；`--model-dir/--manifest/--device` 未给足即
   退出码 2；模型加载失败在 `Catalog.open` **之前**返回结构化 `RUNTIME_CAPABILITY_UNAVAILABLE`
   （退出码 1、零写库）；`status`/`drain` 退出码语义未变。
4. `tests/integration/test_p4_02c_recovery_and_idempotency.py`（新增 475 行，7 例）：全部走真实夹具
   （`fresh_fixture`/`grow_library`/`persist_result` 的真实 outbox 事件）与生产入口
   （`maintenance.*`、`index_cli.main`、`IndexCoordinator`），无自建 replay。
5. `tools/diagnostics/index_maintenance_probe.py`（+219）：新增 `recovery` 场景与
   `StubImageEncoder`/`corrupt_artifact`/`give_the_asset_a_real_source_file`，输出机器可读 JSON。
6. 两份文档（卡面/报告/状态）：与实测一致，唯 §9.4 有一处表述不准确，见 §9。

## 5. 不变量复核

1. **失败关闭、无静默丢失**：`PUBLISHED` 但字节校验失败仍被领取并以 `ARTIFACT_MISSING`
   整批回滚（主审复现：`first_refusal = ARTIFACT_MISSING`、`outbox = {PENDING: N}` 零进展）；
   被隔离事件保持未确认行 + `last_error_code`，并由 `awaiting_reexecution` 上报。**成立**。
2. **旧 revision 不回滚最新状态**：`_is_superseded` 与「检查/引擎写/确认同一 `BEGIN IMMEDIATE`」
   未被 diff 触碰；P4-01C 三条注入与新谓词下全量套件均通过。**成立**。
3. **重执行必须逐字节等价**：`republish_reclaimed` 用工件行上的
   `checksum_sha256`/`byte_size` 走 `prepare` → `_payload_bytes` 的等价校验；主审注入
   +0.01 漂移 → `ARTIFACT_INVALID`，维度不足/NaN → `RUNTIME_RESOLUTION_MISMATCH`，
   三者之后工件仍 `RECLAIMED`、文件未落地、派生指纹未变。**成立**。
4. **确认语义不变**：`semantic_ready` 仍只由 `index_entries.state='CONFIRMED'` + READY generation
   决定。主审 11 个隔离事件后 `semantic_ready = 41 = 40 + 12 − 11`；重执行并 drain 后
   `= 52 = 40 + 12`。领取谓词不写任何行。**成立**。
5. **审计幂等**：**不成立**，见 §6 的 F1（三元组拥有 ≥2 个 embedding 工件时每次审计仍新增事件）。
6. **一个坏工件不得阻塞其余事件**：隔离只作用于 `RECLAIMED`（且实测该状态只能由修复审计写入，
   见 §9），`PUBLISHED` 内容损坏仍失败关闭。主审复现：11 个坏工件 → 第 12 个事件确认、
   11 个进入 `awaiting_reexecution`、坏工件零派生行；重发布后**同一条事件**被领取并确认
   （`final_applied = 11`、`awaiting = 0`）。**成立**。
7. **隔离谓词的形式化**：
   `state IN ('PENDING','IN_FLIGHT') AND NOT EXISTS (SELECT 1 FROM artifacts a
   WHERE a.artifact_id = index_outbox.artifact_id AND a.lifecycle_state = 'RECLAIMED')`。
   该谓词只影响领取集合，是纯读；事件行、确认语义、引擎写事务均不变。两条领取路径引用同一常量，
   变异测试证明去掉它会让隔离用例失败（§8 m1）。**成立**。

## 6. 未通过项（最小反例）

### F1（本卡 A3 交付面，须修）——三元组有 2 个可读 embedding 工件时审计**仍**无界增长

**可达状态**：P4-01C 的内容变更路径（`bump_filter_revision(cause="CONTENT_CHANGED")` +
  再次 `publish_embedding` 同一 asset/processor）会为同一 `(space_id, asset_id, generation_id)`
  留下**两个 `PUBLISHED` embedding 工件**（主审实测：同 `processor_id`、同 generation，
  两行 `lifecycle_state='PUBLISHED'`，`artifacts` 表没有「旧工件退场」的状态）。
  审计的行集是「`artifact_type='embedding'` 的所有工件」，因此会**为同一三元组访问两次**。

**反例 A（无需任何损坏，只重复审计）** `test-results/p4-02c-review-audit-growth.json`：
40 资产夹具 + 2 个待确认事件 + 资产 41 的第二个工件，随后连续调用
`IndexCoordinator.audit_and_schedule_repairs`：

| 审计次数 | outbox 行数 | 状态 | `scheduled` |
|---|---|---|---|
| 1 | 5 | `{PENDING: 5}` | `["rev-41","rev-41-changed"]` |
| 2 | **7** | `{PENDING: 7}` | 同上 |
| 3 | **9** | `{PENDING: 9}` | 同上 |
| 4 | **11** | `{PENDING: 11}` | 同上 |

同一三元组的两个工件互相「抢」`MAX+1`（A1a 复用不到 A1b 的最新 revision，A1b 也复用不到 A1a
刚新增的那条），每次审计各多分配 1 条、revision 交替上升 0,1 → 2,3 → 4,5 → 6,7 …。

**反例 B（drain 确实卡住时的同一增长）**：在同一夹具上把**最早一条事件**的
`payload_checksum` 改坏（`_validate_event_payload` 的失败关闭守卫，审计不修复它），
状态在三次测量之间除审计自身的新行外**没有任何变化**：

| `drain --repair` | 结构化拒绝 | outbox 行数 | 状态 |
|---|---|---|---|
| 1 | `INDEX_SUBMISSION_FAILED: outbox payload checksum is invalid` | 5 | `{PENDING: 5}` |
| 2 | 同上 | **7** | `{PENDING: 7}` |
| 3 | 同上 | **9** | `{PENDING: 9}` |

即 P4-02A 主审 A3 的失败模式（「drain 卡住 ⇒ 每次 `--repair` 无界放大 backlog」）
在「三元组拥有多个工件」时**原样保留**；这也正是内容变更（P4-01C）之后尚未确认的正常中间态。
任何使 drain 永久无法推进的原因（引擎/索引目录不可写、磁盘错误、DB 行不一致等）都会触发它。

**与卡面的差距**：实现**逐字**满足卡面 §「必须交付 1」那条判据的字面（卡面同句也要求
「且它指向同一 `artifact_id`」），也满足了卡面给的示例夹具（一个资产一个工件：审计实测
`3 → 3 → 3`）。不满足的是**同一张卡的不变量 5** 与**同一段的交付证据口径**
（「在 drain 被卡住的同一状态上重复执行审计，未确认事件数不得增长」）：那条字面判据只对
「一个三元组一个工件」成立，而产品可以通过受支持的内容变更路径进入「一个三元组两个工件」。
修复需要在卡面允许修改面内把判据从**逐工件**放宽到**逐三元组一次**，并在报告里写明这处扩宽
（它不改变 `schedule_projection_refresh` 的显式 revision 语义，也不放宽「工件被换必须重新排程」）。

**影响面**：outbox 无界增长（每次 `--repair` 每个多工件资产 +2 行）、审计成本随行数抬升；
多出的行主要是落后 revision，drain 恢复推进时按 `_is_superseded` **空确认**（不写引擎），
但会永久留在 outbox（`CONFIRMED` 行）、叠加写事务，并抬高审计成本。
**修法方向（留给定稿会话裁量）**：把复用判断收敛到「三元组一次」——以该三元组**最新的未确认
事件**（而不是「与当前工件同 id」的事件）判断该三元组是否已有代表其当前工件的待投影工作，
同时保留「条目确认丢失 / Lance 行缺失 / 工件确被替换 ⇒ 仍必须重新排程」。
**复核口径**：`tmp/p402c-review/audit_growth_probe.py` 的两个变体（A 不增长、B 卡住不增长）
+ 卡面 focused/受影响面/全量，且需补一条**多工件三元组**用例进入
`tests/integration/test_p4_02c_recovery_and_idempotency.py`。

### F2（本卡必测命令 + CLI 机器可读输出，须修）——非 ASCII 路径下证据工具崩溃、`--json` 不是 UTF-8

**卡面必测命令**（§「必测命令」第 4 条）要求用
`--work-dir "<项目内 中文 空格 目录>"` 运行恢复证据。主审实测该命令**无法完成**：

```text
uv run --no-sync python tools/diagnostics/index_maintenance_probe.py --mode recovery `
  --work-dir "tmp/p402c-review/复核 证据" --injection-assets 40 --output test-results/p4-02c-review-recovery.json
→ UnicodeDecodeError: 'utf-8' codec can't decode byte 0xb8 in position 129   （subprocess 读线程）
→ AttributeError: 'NoneType' object has no attribute 'strip'
   at tools/diagnostics/index_maintenance_probe.py:1234（_run_cli）
```

**根因（产品侧可独立复现）** `test-results/p4-02c-review-cli-encoding.json`：CLI 用
`json.dumps(..., ensure_ascii=False)` 把 JSON 打到**管道**，Windows 下流编码取 ANSI 代码页
（本机 `cp936`），而工具侧 `subprocess.run(..., text=True, encoding="utf-8")` 强制按 UTF-8 解码：

| 调用 | 退出码 | stdout 是否 UTF-8 |
|---|---|---|
| `reexecute --model-dir <ASCII> --json`（模型缺失） | 1 | **ok** |
| `reexecute --model-dir <含中文> --json`（模型缺失） | 1 | **`UnicodeDecodeError: ... byte 0xd0 in position 158`** |

触发条件是**错误详情里回显操作员自己的路径**（`model_dir`/`manifest`），而这在本机是**常态**：
文档给的默认模型目录就在用户 profile 下（`.codex`/`AppData` 路径，用户名为中文即中招）。
因此「`--json` 机器可读」这一 CLI 契约在非 ASCII 环境下不成立，卡面要求的
「中文+空格工作目录」证据也无法按原文执行。被审报告 §5 实际把该命令写成了 ASCII 的
`.pytest-basetemp-p402c/probe-work`，未说明这处替代。

**修法方向**：CLI 显式以 UTF-8 输出（例如在 `_emit/_emit_error` 前
`sys.stdout/stderr.reconfigure(encoding="utf-8")`，或等价地让机器可读输出不依赖控制台代码页），
并让诊断工具的 `_run_cli` 按子进程真实编码解码（或注入 `PYTHONIOENCODING=utf-8`）并在解码失败时
给出结构化失败而不是 `AttributeError`。**复核口径**：卡面原文的
`--mode recovery --work-dir "<中文 空格 目录>"` 命令必须 exit 0 且 10/10 检查为真，
外加 `p4-02c-review-cli-encoding.json` 两行都变成 `ok`。

## 7. 非阻断观察

- **O1** `reexecute` 的逐工件结果不含适配器实际解析出的设备（`resolved_device`），
  操作员从 JSON 无法确认「请求的设备是否被满足」。卡面未要求，建议随 F2 一起补（或在 P4-02B）。
- **O2** `--device-policy` 默认 `cpu_allowed`，而 `--device` 必填：主审确认**没有静默默认**
  （缺一即退出码 2），且策略语义允许适配器在请求设备不可用时解析到别的设备——这是既有
  设备资格契约，但与 O1 一样在 JSON 里不可见。
- **O3** `awaiting_reexecution` 的边界：11 个隔离事件时 `count=11`、`sample` 恰好 10 条、
  `sample_truncated=true`、样本字段集与卡面完全一致（`event_id/artifact_id/asset_id/space_id/
  generation_id/projection_revision/attempts/last_error_code`）且无任何路径字段。
- **O4** 新测试的 `build()` 只覆盖「每个资产一个工件」，F1 的状态不在其覆盖内；
  建议修复轮补入（见 F1 复核口径），这是本卡测试面的唯一显著空缺。

## 8. 测试强度（变异）与可重复性

主审用**临时 pytest 插件**（`tmp/p402c-review/mutation_m*.py`，通过 `-p` 注入，**不改仓库文件**）
做 3 处语义破坏，全部被本卡新用例抓住：

| 变异 | 内容 | 结果 |
|---|---|---|
| m1 | `CLAIMABLE_EVENT_PREDICATE` 退化为 `state IN ('PENDING','IN_FLIGHT')`（去掉隔离） | focused **5 失败**（含隔离/恢复与审计增长用例） |
| m2 | 审计路径恒取 `MAX(projection_revision)+1`（回到 P4-02A 语义，去掉复用） | focused **3 失败**（含审计幂等用例） |
| m3 | `ArtifactStore` 的 payload 校验旁路（重执行不看 checksum/byte_size） | focused **1 失败**（非逐字节等价必须被拒） |

JUnit：`test-results/p4-02c-review-mutation-mutation_m{1,2,3}.xml`。测试非空转，且覆盖面与
交付承诺一致（除 F1/O4 指出的多工件三元组）。可重复性：主审对最敏感的两条并发用例在
受影响面套件中重跑通过（`p4-02c-review-boundaries.xml`，36/0/0/0），全量亦 0 失败。

## 9. 被审实现报告的事实性更正（只改表述）

1. **§9.4 的边界不准确**：报告写「`reclaim` 的显式调用（非审计路径）同样会让事件被隔离」。
   主审实测 `ArtifactStore.reclaim` 对「存在未确认 outbox 行」的工件**直接拒绝**
   （`ARTIFACT_CONFLICT: an unconfirmed outbox or active generation still protects this artifact`，
   `test-results/p4-02c-review-reclaim.json`）；审计路径不走 `reclaim`，是它自己写
   `lifecycle_state='RECLAIMED'`。所以隔离事件的**唯一**入口就是 P2-03 修复审计——
   这比报告的表述更强，也更贴合卡面「运维必须先跑审计」的边界，但应改正原句。
   同一次探针里，审计之后对同一工件再 `reclaim` 返回 `False`（已 `RECLAIMED`，幂等）。
2. §6 的回归数字与主审复现一致（`entry` applied 300 / 第二次 0 / 持锁 p95 119.6 ms vs 主审
   120.254 ms；审计 9.24 vs 7.85 ms/工件，同量级）；§7 的逐条前后计数与主审独立复现一致；
   §9 的 BLOCKED 与未验证项口径诚实，无需更正。

## 10. 主审结论与停止点

- **`ENGINEERING REVIEW FAIL`**：F1 是本卡交付面（A3 审计幂等 / 不变量 5）的**可复现缺陷**，
  F2 使卡面必测命令无法按原文执行且 `--json` 在非 ASCII 环境不再是机器可读 JSON。
  两条都在本卡允许修改面内（`indexing/store.py`、`indexing/cli.py`、诊断工具、本卡测试），
  无需新增依赖、schema/migration 或检索语义变更，因此**不触发停止条件**。
- **停止点**：请在**新会话**（DS-H / DeepSeek V4.1 Flash / 高推理）做最小修复：
  ① 把复用判断收敛到三元组一次并补多工件用例（F1）；
  ② CLI UTF-8 输出 + 诊断工具解码健壮化（F2）；
  然后重跑卡面 focused/受影响面/全量 + `--mode recovery`（**必须用中文+空格工作目录**）
  + `tmp/p402c-review/audit_growth_probe.py`，更新实现报告与 `docs/development-status.md`。
  修复后先回到 **DS-H 全量工程主审**复核变更面（F1/F2 的反例必须由新用例覆盖并通过），
  再进入 **C2 / GPT-5.6 Sol / high 聚焦终审**（**必选**，不得记 `SOL_NOT_REQUIRED`）；
  两者都通过后才标 P4-02C PASS，并把 P4-02B 前置更新为「A2/A3 已收口」。
  模型切换始终由用户在 Codex 中手动完成。

## 附：证据文件清单（本次主审）

| 文件 | 内容 / 退出码 |
|---|---|
| `test-results/p4-02c-review-focused.xml` | 7/0/0/0（exit 0） |
| `test-results/p4-02c-review-boundaries.xml` | 36/0/0/0（exit 0） |
| `test-results/p4-02c-review-full.xml` | 1666/0/0/48（exit 0） |
| `test-results/p4-02c-review-probe-all.json` | 六场景 `ok=true`（工具 exit 0） |
| `test-results/p4-02c-review-recovery.json` | 10/10 检查为真（ASCII 工作目录，工具 exit 0） |
| `test-results/p4-02c-review-audit-growth.json` | F1 反例 A/B |
| `test-results/p4-02c-review-isolation.json` | 隔离样本边界 + 多目标重执行独立性 |
| `test-results/p4-02c-review-dry-run.json` | dry-run 零写库（`identical=true`，写语句 `[]`） |
| `test-results/p4-02c-review-cli-encoding.json` | F2 根因（ASCII ok / 非 ASCII UTF-8 失败） |
| `test-results/p4-02c-review-reclaim.json` | 显式 `reclaim` 拒绝对未确认事件隔离 |
| `test-results/p4-02c-review-mutation-mutation_m{1,2,3}.xml` | 变异 5/3/1 失败 |

## 修复面复核（独立 DS-H 第二轮，2026-09-24）

**结论：`ENGINEERING REVIEW PASS（修复面）`** —— F1、F2 已修复并通过独立复核；复核过程中另发现
一条**新的交互缺陷 F3**（「等待重执行的最新工件」被旧工件越权覆盖），当轮已修复并复核。
本卡**仍未标 PASS**：按卡面路由，下一步是 **C2 / GPT-5.6 Sol / high 聚焦终审（必选）**。

- 被复核修订：当前工作树（`665109e` + 修复轮，尚未提交；改动文件见下），复核期间未修改
  `src`/测试以外的任何面。
- 复核口径：不使用第一轮的结论，直接跑仓库套件 + 主审自建探针（`tmp/p402c-review/**`）+
  6 处语义变异（临时 pytest 插件与子进程 sitecustomize，**不改仓库文件**）。

### R1 修复面 diff 与冻结面

改动仅 4 个文件：`src/photo_search/indexing/store.py`（`audit_and_schedule_repairs` 的三元组决策
+ docstring）、`src/photo_search/indexing/cli.py`（`--json` 的 `ensure_ascii=True` 两处 + docstring）、
`tools/diagnostics/index_maintenance_probe.py`（`_run_cli` 注入 `PYTHONIOENCODING=utf-8`、
`errors="replace"`、空捕获保护）、`tests/integration/test_p4_02c_recovery_and_idempotency.py`
（+4 用例）。`_schedule_existing` 的复用判据、`schedule_projection_refresh` 的显式 revision 语义、
`maintenance.*`、`CLAIMABLE_EVENT_PREDICATE`、`reclaim`/`republish_reclaimed` 均**逐字未动**；
禁止面（migration、`catalog/schema.py` 的 `n = 15`、outbox/entry 状态词表、ADR/companion、
`taken_time`/`capture_desc`/`SearchFiltersEchoV2`、`search_projection`、`search/retrieval.py`、
`api/**`、`web/**`、`PhotoSearch.spec`、`tools/packaging/**`、`pyproject.toml`、`uv.lock`）
相对 `665109e` **零改动**，`uv lock --check` exit 0。

### R2 F1 复核（审计幂等）

主审自建反例在修复后的修订上重跑，形状与第一轮完全相同（2 工件/1 三元组，无损坏、无 drain）：

| 探针 | 第一轮（修复前） | 修复后 |
|---|---|---|
| 连续 4 次 `audit_and_schedule_repairs` | `PENDING 5 → 7 → 9 → 11` | **`3 → 3 → 3 → 3`**，`scheduled` 恒 `[]` |
| `drain --repair` ×3（最早事件校验失败，drain 卡住） | `5 → 7 → 9` | **`3 → 3 → 3`**，拒绝恒 `INDEX_SUBMISSION_FAILED` |

「真实缺失仍排程」在两条新用例里各自被断言（单工件三元组删除 `index_entries` → `scheduled = 1`；
双工件三元组 drain 收敛后再删除 → `scheduled = 1` 且最新工件向量仍为最终行）。
变异证据：m2（审计恒 `MAX+1`）→ 失败 5 例（含两条 F1 用例）；m4（退回逐工件）→ 失败 8 例；
m5（取三元组**最旧**工件）→ 失败 3 例（两条 F1 + 新 F3 用例）。

### R3 F2 复核（`--json` 契约与中文路径）

- 同一探针的真实子进程字节：`--model-dir <ASCII>` → `ok`；`--model-dir <含中文>` → `ok`，
  且 stdout 全为 ASCII（修复前为 `UnicodeDecodeError ... byte 0xd0 in position 158`）。
- **卡面必测命令可按原文执行**：`--mode recovery --work-dir "<项目内 中文 空格 目录>"`
  （`tmp/p402c-review/run_recovery_zh.py`，路径由 `\uXXXX` 转义构造）→ 工具 **exit 0**、
  `recovery.ok = true`、10/10 检查为真、审计行数 3 → 3 → 3。
- 变异证据：m7（用 `sitecustomize` 把子进程的 `json.dumps` 强制回 `ensure_ascii=False`）→
  `test_the_json_entry_point_survives_a_non_ascii_path` **失败 1 例**（该用例是子进程断言，
  普通进程内插件无法触及，故用子进程注入证明其判别力）。

### R4 新发现 F3（本轮发现，当轮修复）

**状态**：内容变更三元组（同一 generation 两个工件）中，当**最新工件**正是等待重执行的那一个时，
修复轮的第一版仍会把**较旧工件**作为修复目标并分配到比它更高的 revision，于是操作员刚重发布回来的
最新工件反而被 supersede：搜索继续命中**变更前的向量**，而 outbox、`index_entries`、
`awaiting_reexecution` 与随后的审计**全部显示健康**（无任何信号）。

主审反例（`tmp/p402c-review/f3_probe.py`，2 工件三元组 + 损坏最新工件）：

| | 第一版修复 | 修补后（现修订） |
|---|---|---|
| `drain --repair` 的审计结果 | `scheduled = 1` / `requires_reexecution = 1` | **`scheduled = 0`** / `requires_reexecution = 1` |
| `reexecute`（逐字节等价）→ `drain` 后的 entry | `f3-41`（**旧工件**），revision 2 | **`f3-41-changed`（最新工件）**，revision 1 |
| 与最新向量的距离 / 与变更前向量的距离 | `2.02` / **`0.0`（旧向量仍在服务）** | **`0.0`** / `2.02` |
| 随后的审计 | `scheduled = []`（看似干净） | `scheduled = []`（确实干净） |

**修补**：审计决策改为以三元组**全部工件**（含刚置为 `RECLAIMED` 的）确定「最新工件」，
只有当这个最新工件本身也在待修复集合里时才排程；否则该三元组只通过 `requires_reexecution`
报告，修复交给既有恢复入口。新增用例
`test_a_reclaimed_newest_artifact_outranks_the_older_one`（断言 `scheduled = 0`、
`requires_reexecution = 1`、恢复后 entry 指向最新工件且 `confirmation_revision == Lance.projection_revision`、
变更前向量不可达）。
变异证据：m6（去掉「最新工件等待重执行则不排程」的守卫）→ **恰好 1 例失败**，即该新用例。

**性质**：F3 是 A2（工件被回收/隔离）与 A3（审计排程）的交互，属本卡范围；修复不改协议、
不改 schema、不改确认语义，只在审计的「选谁」上收口。

### R5 不变量再核对

1. 失败关闭、无静默丢失：`PUBLISHED` 内容损坏仍 `ARTIFACT_MISSING` 零进展；被隔离事件保持
   `PENDING` + `last_error_code`，由 `awaiting_reexecution` 上报（探针 + 用例）。
2. 旧 revision 不回滚：`_is_superseded` 与「检查/引擎写/确认同一 `BEGIN IMMEDIATE`」未动；
   修复后 F3 场景的最终行是**最新工件**，revision 与 entry 一致。
3. 重执行逐字节等价：checksum/byte_size 校验未动；漂移/维度不足/NaN 三种注入仍被结构化拒绝。
4. 确认语义不变：隔离后 `semantic_ready = 41 = 40 + 12 − 11`，恢复后 `= 52`；F3 场景
   `entry.revision == row.projection_revision == 1`。
5. 审计幂等：两个反例变体均为常数行数（见 R2）。
6. 一个坏工件不阻塞其余事件：11 个坏工件的夹具里第 12 个事件确认、坏工件零派生行；
   重发布后**同一条事件**确认（`applied = 11`、`awaiting = 0`）。
7. 隔离谓词仍是两条领取路径共用的同一常量，且只影响领取集合（m1 变异 → 6 例失败）。

### R6 本轮验收与证据

| 证据 | 结果 |
|---|---|
| focused `test-results/p4-02c-fix-focused.xml` | **11 / 0 / 0 / 0**（14.4 s；7 原有 + 4 新用例） |
| 受影响面 `test-results/p4-02c-fix-boundaries.xml` | **36 / 0 / 0 / 0**（23.6 s） |
| 全量 `test-results/p4-02c-fix-full.xml` | **1670 / 0 / 0 / 48**（225.9 s = 基线 1666 + 4 新用例） |
| 变异 m1–m7 | 失败 **6 / 5 / 1 / 8 / 3 / 1 / 1** 例（`test-results/p4-02c-fix-mutation_m1..m7.xml`） |
| `--mode all` `test-results/p4-02c-fix-probe-all.json` | 六场景 `ok=true`；entry applied 300 / 第二次 0 / 持锁 p95 124.196 ms / 审计 11.176 ms-per-artifact |
| 中文+空格工作目录 `test-results/p4-02c-fix-recovery-zh.json` | 工具 exit 0，`recovery.ok = true`，10/10 |
| 主审探针 `p4-02c-review-audit-growth.json` / `-f3.json` / `-isolation.json` / `-dry-run.json` / `-cli-encoding.json` | 行数恒定、最新工件胜出、隔离样本边界正确、dry-run 零写库、JSON 均可 UTF-8 解码 |
| `ruff check .` / `git diff --check` / `uv lock --check` | 全 exit 0 |

### R7 结论与停止点

- **`ENGINEERING REVIEW PASS（修复面）`**：F1/F2 的修复通过复核，F3 当轮闭环，未发现新的未决项；
  V3.1 §2.2 六类升级条件仍未命中（不升 DS-MAX），无需转 C1。
- **仍未标 P4-02C PASS**：按卡面路由下一步是独立 **C2 / GPT-5.6 Sol / high 聚焦终审（必选，
  不得记 `SOL_NOT_REQUIRED`）**，只审 V3.1 §11 相关不变量（领取谓词、revision/幂等键、
  旧 revision 不回滚、恢复链与失败关闭边界）及直接回归；通过后才标 PASS 并更新 P4-02B 前置。
- 遗留（不影响本结论）：真实中文 CLIP 权重缺失 ⇒ 端到端重执行 BLOCKED；30 万档审计/领取成本、
  ANN 0.95、API P95、GTX 1660、冻结包内 CLI 可达性未验证（沿用实现报告 §9/§11.4）。
  另：两个工件 `created_at` **精确同毫秒**的并列未单独注入（需人工构造同毫秒发布），
  该并列由 `(created_at, artifact_id)` 确定性定序，推演上最多多一次分配即进入不动点，
  但**未实测**，记入未验证项。
  模型切换始终由用户在 Codex 中手动完成。
