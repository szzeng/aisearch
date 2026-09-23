# P4-02A 实现报告 — 语义 outbox 的生产维护入口接线与长写事务争用边界

本报告对应任务卡 [docs/tasks/P4-02A.md](../tasks/P4-02A.md)。**结论：实现与 B + D 级验收全部真实完成，
证据见下；按任务卡的审查路由，状态停在「独立 DS-H 全量工程主审 PENDING」，未标 P4-02A PASS、
未在此基础上启动 P4-02B。**

## 1. 基线与改动清单

- 观察基线：`3386f39`（P4-01C 收口提交，「P4-01C 收口：Sol 短复审通过（P4-01C PASS）…」），
  执行前 `git status --short` 为空（工作树干净），与任务卡预期一致。
- 本轮改动（`git status --short`）：
  - `M src/photo_search/indexing/store.py`（+66 / -7，**仅可观测性**：见 §3）；
  - `A src/photo_search/indexing/maintenance.py`（生产维护入口）；
  - `A src/photo_search/indexing/cli.py`（受控 CLI 入口）；
  - `A tools/diagnostics/index_maintenance_probe.py`（真实证据工具，产出机器可读 JSON）；
  - `A tests/integration/test_p4_02a_maintenance_entry.py`（10 个新用例）；
  - `A docs/reports/P4-02A-maintenance-entry.md`（本报告）、`M docs/development-status.md`。
- **禁止面零改动**：`0001`～`0015` migration 与 `SUPPORTED_SCHEMA_VERSION`（仍 15）、ADR-0001～0011 与全部
  companion、`taken_time`/`capture_desc`/`SearchFiltersEchoV2`、`search_projection` 键集、
  `POST /api/v1/search` 信封/`query_hash`/游标/错误码、RRF/BM25/距离公式、P1～P3 人物/地点信封、
  `PhotoSearch.spec`、`tools/packaging/**`、`packaged_api_smoke.py`、`pyproject.toml`、`uv.lock`、
  `src/photo_search/face/index.py`（`FaceIndexCoordinator` 未触碰）。未新增依赖。

## 2. 生产入口与并发口径（必须交付事实 1）

**入口位置**：`src/photo_search/indexing/maintenance.py::drain_semantic_outbox`，
操作面命令为 `python -m photo_search.indexing.cli drain [--limit N] [--batch-size N] [--repair]
[--retry-limit N] [--retry-backoff-ms N] [--data-root DIR] [--json]`，只读面为同模块 `status`。
命令形态与本构建既有的两个受控入口（`photo_search.face.cli`、`photo_search.ocr.cli`）一致：
一个 JSON 文档、成功 0 / 结构化 `CatalogError` 1 / 用法错误 2、不启动线程或服务。

**调用时机**：embedding 工件提交（`ArtifactStore.persist_result` → `_persist_embedding_event`）在
`index_outbox` 写入 `UPSERT_VECTOR` 事件之后；以及崩溃/重启后需要把未确认事件补完时；以及任何
「语义阶段已完成」的声明之前。`semantic_ready`（`index_entries.state='CONFIRMED'` ∧
generation `READY`）只经由该路径推进。

**并发口径：多维护者可并发，不使用排他锁。** 保证不变量 3 的机制是 P4-01C 第二轮修复后的写协议，
不是入口层的锁：`process_batch` 的领取在**单独提交**的短事务里完成（这正是 `after_lance` 崩溃窗口停在
`IN_FLIGHT` 的原因），随后**同一个 `BEGIN IMMEDIATE` 事务**覆盖「权威复读 → `_is_superseded` →
引擎写 → 逐事件确认 → `_make_generation_ready`」。因此：

- 另一个维护者或前台发布者无法把更新的 `projection_revision` 插进「检查→写入」窗口，旧 revision
  不可能最后落盘；
- 别人已确认的事件在本批里被跳过，既不写入也不报「丢失领取」；
- 本批真实失败仍整批回滚并回到 `PENDING`。

**可验证证据（不是论证文字）**：§6 的 D-4 用两条真实连接（各自线程内 `Catalog.open`）并发 drain，
并叠加「同一资产较新 revision」；§7 用真实前台写者（自带连接的 `places.revision.bump_filter_revision`
+ 真实内容变更发布）与 drain 并发。两处都读到：事件恰好确认一次、Lance 行无重复、
`stored row revision == index_entries.confirmation_revision == MAX(事件 revision)`。

**入口参数**：`limit`（本批最多应用多少事件，可分批续跑）、`batch_size`（多少事件共享一次引擎写；
逐事件确认与整批原子性不变）、`repair`（默认**关闭**，理由见 §8 F1）、
`retry_limit`/`retry_backoff_ms`（只针对 `busy_timeout` 拒绝，见 §7）。

## 3. `store.py` 的改动只是可观测性

`IndexCoordinator` 新增 `write_samples` / `write_rejections` 与 `_timed_write(phase)`，
并把 drain 路径原有的 6 处 `with self.catalog.write()` 原样替换为
`with self._timed_write("claim"|"apply"|"revert")`（`process_next` 与 `process_batch` 各 3 处）。
`_timed_write` 转交同一个 `Catalog.write()`，只额外记录「获取锁等待」「持锁时长」，并把
**进入事务体之前**的拒绝（`busy_timeout` 到期）记为结构化样本。**状态机、确认顺序、检索参数、
错误码、提交语义零变化**；`audit_and_schedule_repairs` 既有的两处写事务未改动。
既有直接证据：P4-01C 专项 `tests/integration/test_p4_01c_retrieval_blocks.py` 全绿（含对 `Catalog.write`
调用次数做计数的并发重领用例），见 §9 的 focused 运行。

## 4. 命令与证据文件

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path
# 1) 真实证据工具（项目内 中文+空格 工作目录；夹具为 seeded 合成数据）
uv run --no-sync python tools/diagnostics/index_maintenance_probe.py --mode all `
  --work-dir "tmp/p4-02a-基准 空格" --assets 400 --events 300 `
  --injection-assets 200 --injection-events 24 --foreground-writes 30 `
  --output test-results/p4-02a-maintenance.json
# 2) 受影响的 P4-01C/P2-03 边界 + 本卡新增入口/并发用例
uv run --no-sync python -m pytest -q -p no:cacheprovider `
  tests/integration/test_p4_01c_retrieval_blocks.py tests/integration/test_p2_index_submission.py `
  tests/integration/test_p2_acceptance_recovery.py tests/integration/test_p2_search_topup.py `
  tests/integration/test_p4_02a_maintenance_entry.py `
  --basetemp=.pytest-basetemp-p402a/focused-set --junitxml=test-results/p4-02a-focused.xml
# 3) B 级全量
uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration `
  --basetemp=.pytest-basetemp-p402a/full --junitxml=test-results/p4-02a-full.xml
uv run --no-sync ruff check . ; git diff --check ; uv lock --check
```

机器事实（工具内 `machine_facts()`）：Windows-11-10.0.26200、Python 3.12.10、
Intel64 Family 6 Model 183（32 逻辑核）、34.04 GB RAM（可用 11.02 GB）、
NVIDIA GeForce RTX 4060 Laptop 8188 MiB、lancedb 0.38.0、pyarrow 25.0.1。
向量为 seeded 合成向量（**非真实模型**），本卡数字全部是 SQLite/Lance 维护面数字，不含模型吞吐。
注：本机受限令牌下 `--basetemp` 需要走正常 Windows 权限路由（沙箱内会得到 `WinError 5` 环境失败），
未改任何 ACL。
注：probe 的 `--work-dir` **必须是空目录**（工具会拒绝已存在夹具的目录，避免把「半 drain 的夹具」当成
新夹具测量）；本轮的 55 MB 合成夹具与临时日志在验证后已删除，证据以 `test-results/p4-02a-maintenance.json`
为准，上面的命令可原样重跑（重建夹具约 30 s）。

## 5. 入口真实性（工具 `entry` 场景）

夹具：400 个真实资产（真实 `assets`/`file_locations`，Lance generation 400 行）+
300 个真实 embedding 工件（走 `ArtifactStore.persist_result`），故 outbox 300 `PENDING`。

| 观测 | 值 |
|---|---|
| `drain_semantic_outbox`（默认 200/批） | applied **300**，`confirmed_delta` 300，`batches` 2，`duration_ms` **212.4** |
| 写事务 | 5 = claim 3 + apply 2（300 事件、每批 200：两次领取各有一次引擎写，第三次领取发现无待办即停）|
| 锁等待 | count 5（`timing_samples` 5，未截断），p50 **0.013 ms**，p95 **0.019 ms**，max 0.019 ms |
| 持锁时长 | p50 **1.957 ms**，p95/max **125.153 ms** |
| `busy_timeout` 拒绝 | 0 |
| `asset_id` 标量索引 | `state=created`（维护侧创建，仅此一处）|
| outbox/entries | before：`PENDING 300`、entries `CONFIRMED 400 / PENDING_REPAIR 300`、`semantic_ready 400` → after：`CONFIRMED 300`、`pending 0`、`pending_repair 0`、`semantic_ready 700` |
| generation | `READY` 且 `is_active=1`；Lance 行与唯一资产一一对应（场景断言 `one_row_per_asset=true`）|
| 读路径 | 新资产在其自身向量的生产候选遍历 top-20 中命中（精确路径，未引入 ANN 主路径）|
| 第二次调用 | applied **0**，`confirmed_delta` 0，仅 1 次 claim 事务（幂等空转）；场景总耗时 5 736.5 ms（含夹具构建）|

## 6. D 级注入（必须交付事实 3，逐条前后计数）

工具 JSON 键：`injections.restart` / `crash_window` / `content_change` / `two_maintainers`；
同一批边界另有 JUnit 用例（§9）。

**D-1 中途中断后重启**（24 事件；先 `limit=12`（applied 12，39.5 ms），再在新 `Catalog`/新协调器上续跑）：
中断时 `outbox {CONFIRMED 12, PENDING 12}`、Lance 212 行 / 唯一 212、`semantic_ready 212`；
重启后 applied 12 → `outbox {CONFIRMED 24}`、Lance 224 = 唯一 224、`semantic_ready 224`；
**派生状态指纹 `2ba50ddd…` 与「一次不中断 drain」的孪生夹具完全一致**（两者同为 224 行 / 24 事件 /
CONFIRMED 24）。逻辑重复 0、丢失 0。

**D-2「Lance 已写、outbox 未确认」重放**（24 事件，先 `process_batch(failpoint="after_lance")` 领取 3）：
崩溃后 `outbox {IN_FLIGHT 3, PENDING 21}`、Lance 203 行、`semantic_ready 200`；
经入口重放 applied 24（3 个写事务）→ `outbox {CONFIRMED 24}`、Lance 224 = 唯一 224、
`semantic_ready 224`，指纹同样为 `2ba50ddd…`（与 D-1 收口一致）。

**D-3 同 `asset_id` 内容变更后旧向量不再命中**（资产 1，`catalog_filter_revision` 0→1 后发布新工件）：
`outbox {PENDING 1}` → applied 1；Lance 行 `projection_revision=1` ==
`index_entries.confirmation_revision=1` == `MAX(事件 revision)=1`，entry `CONFIRMED`；
与**新向量**的引擎距离 **0.0**，与**被取代的旧向量**距离 **1.982**（旧向量不再以零距离命中）。

**D-4 两个维护者同时 drain**（25 事件 = 24 + 同一资产较新 revision 1 条；批量 3；
引擎写注入 30 ms 使窗口可见；第二个维护者在前者确认首批后加入）：
两条连接均无失败；维护者 A applied 25（19 个写事务），维护者 B applied 0（1 个 claim 事务，
**锁等待 313.2 ms** ⇒ 确实被 A 的写事务串行化，随后发现事件已 `CONFIRMED` 而跳过，未报假失败——
即 P4-01C A1 的既定语义）；`applied_total = 25`（等价于「每事件恰好被计数一次」），
后续 drain leftover 0；`outbox {CONFIRMED 25}`、Lance 224 = 唯一 224；
较新 revision 落盘：目标资产 `stored row revision = entry confirmation_revision =
MAX(事件 revision) = 1`，与新向量距离 0.0。**不变量 1 与不变量 3 同时成立。**

## 7. 争用与 `busy_timeout` 边界（必须交付事实 2 + 不变量 6）

**争用（工具 `contention` 场景）**：400 资产 + 300 事件；前台写者是**自己的连接、自己的线程**，
在 drain 期间执行 30 次真实的 `places.revision.bump_filter_revision`（单事务内同时改
`catalog_filter_revision` 与写 `catalog_filter_revision_events`）与 10 次真实内容变更发布
（对资产 401 走完整工件发布路径）；引擎写注入 30 ms 拉长持锁窗口。

| 侧 | 观测 |
|---|---|
| drain | applied **300**（本轮前台发布都在 drain 最后一次领取之后，由收尾调用补 10），duration **269.0 ms**，写事务 5 |
| drain 锁等待 | p50 **0.011 ms**，p95/max **0.014 ms** |
| drain 持锁 | p50 **2.437 ms**，p95/max **150.934 ms** |
| drain 拒绝 | 0（未触及 5 s busy_timeout）|
| 前台修订写 | 30 次全部提交；锁等待 p50 **0.009 ms** / p95 0.021 / max **263.230 ms**；持锁 p50 0.355 / max 0.764 ms |
| 前台内容变更发布 | 10 次全部提交；单次总耗时 p50 **4.98 ms** / p95/max **6.246 ms**（工件文件写在事务内）|
| 前台拒绝 | 0 次 |

**「不得静默失败」的实测口径**：`catalog_filter_revision.revision` 0 → **30**，
`catalog_filter_revision_events` 行 0 → **30**，与前台**已提交**次数逐一相等（未提交的写不会留下半条：
两个改动在同一事务内）。若发生拒绝，样本必定是 `CATALOG_UNREADABLE` + `database is locked`
（§7 下段与 JUnit 用例 `test_a_foreground_writer_is_never_silently_lost_during_maintenance` 断言此性质）。
并发下的不变量 3：目标资产 401 `stored row revision = entry revision = MAX(事件 revision) = 28`，
与新向量距离 0.0、与被取代向量距离 2.023；收尾调用再 applied 10（在 drain 最后一次领取之后才落地的
前台发布），最终 outbox `{CONFIRMED 310}`（300 初始 + 10 前台发布）、`PENDING/IN_FLIGHT` 均为 0、
Lance 行 = 唯一资产。

**`busy_timeout` 真实拒绝与重试（工具 `busy-timeout` 场景）**：竞争连接持有写锁 9 000 ms
（配置 `PRAGMA busy_timeout = 5000`）。drain 的第一次 claim 事务在 **5 490.8 ms** 后收到
结构化拒绝 `CATALOG_UNREADABLE` / `cannot start a catalog write transaction: database is locked`
（phase=`claim`）——此事务体未运行、未写任何行；入口按 `retry_limit` 做有界重试，
第二次尝试等待 3 461.6 ms 后成功：applied **24**、outbox `{CONFIRMED 24}`、无重复、
持锁者正常释放；总耗时 9 233.1 ms。实测有效期约 **5.5 s**（配置 5 s + SQLite 忙等待粒度），
同一界限另有 JUnit 用例在 6.6 s 持锁下独立复现（见 §9）。
另有 JUnit 用例断言「`retry_limit=0` 时该拒绝原样抛出且事件仍可领取，随后一次 drain 全部确认」。

## 8. 不变量对照

| # | 不变量 | 证据 |
|---|---|---|
| 1 | 逐事件幂等与确认顺序不变 | `test_p2_index_submission.py`/`test_p2_acceptance_recovery.py`/`test_p2_search_topup.py`/`test_p4_01c_retrieval_blocks.py` 原样全绿（focused 35/0/0/0）；D-4 `applied_total=25`、D-1 指纹一致 |
| 2 | `after_lance` 窗口＝`IN_FLIGHT`，重放零重复 | D-2：崩溃后 `{IN_FLIGHT 3, PENDING 21}` → 重放后 224 行 = 唯一 224，指纹与不中断一致 |
| 3 | 旧 revision 不得回滚最新状态 | D-3（顺序）、D-4 与 `contention`（并发，含前台真实发布）；`test_a_stale_projection_revision_can_never_land_after_a_newer_one` 继续绿 |
| 4 | `semantic_ready`/`index_generations` 状态机不变；标量索引仍只在维护侧创建 | §5：`asset_id_idx` state=`created`；各场景 generation 均 `READY`/`is_active=1`；未改 READY 逻辑 |
| 5 | 读路径不受影响，精确路径仍是默认 | 未改 `retrieval.py`/`service.py`；§5 用生产候选遍历读回新资产（top-20 命中），无 ANN 主路径 |
| 6 | 维护期间前台写不静默失败，拒绝结构化且可重试 | §7：revision/事件行计数与已提交次数严格相等；拒绝样本固定为 `CATALOG_UNREADABLE`+locked；busy-timeout 场景 1 次拒绝 + 1 次重试后全部确认 |

## 9. 验收结果

| 项 | 结果 |
|---|---|
| B 级全量 `test-results/p4-02a-full.xml` | **1658 / 0 / 0 / 48**（209.6 s；基线 P4-01C 为 1648 / 0 / 0 / 48，+10 为本卡新用例；48 条为既有环境跳过）|
| 受影响面+新增 focused `test-results/p4-02a-focused.xml` | **35 / 0 / 0 / 0**（25.0 s）|
| 真实证据 `test-results/p4-02a-maintenance.json` | 五个场景 `ok=true`，退出码 0 |
| `uv run --no-sync ruff check .` | `All checks passed!`，exit 0 |
| `git diff --check` | exit 0 |
| `uv lock --check` | Resolved 96 packages，exit 0 |
| `schema_version` | 仍 15（无迁移改动）|

## 10. 发现与未验证项

**F1（须交给 P4-02B 的前置测量）**：P2-03 的修复审计 `audit_and_schedule_repairs` 是
「每个 embedding 工件一次引擎点查询」的成本。最终工件实测：224 工件 **3 196.9 ms（7.99 ms/工件）**；
独立 DS-H 主审在同一提交上复测为 **3 164.8 ms（7.912 ms/工件）**，两次一致，量级为个位数 ms/工件。
按此推算 30 万工件的全库审计是分钟到几十分钟量级，
因此 `drain` 默认 `repair=False`，只提供显式 `--repair`；本卡**未**在 30 万档实测该审计，
P4-02B 若要把它纳入容量口径，必须先单独测量。

**F2（语义发现，非缺陷）**：冻结的领取查询是「按 `(created_at, event_id)` 最早的
`PENDING`/`IN_FLIGHT`」。因此第二个维护者通常会领取到第一个维护者**正在应用**的事件集合，
被其写事务阻塞（D-4 实测锁等待 313.2 ms），随后发现已 `CONFIRMED` 而跳过并以 0 结束。
正确性不受影响（不变量 1/3 均成立），但**并发维护者目前不分工、不提升吞吐**。若 P4-02B 想要
并行维护吞吐，需要单独裁决领取协议（排他领取或每维护者更大批量），本卡未改也不宣称吞吐。

**F3（打包面未验证）**：入口以 `python -m photo_search.indexing.cli` 形式提供，
与 `face.cli`/`ocr.cli` 同级；`PhotoSearch.spec` 的 Python 模块来自 `__main__.py` 的分析，
因此该 CLI 不在冻结包的可见入口内（把它接进启动器会触发 V3.1 §3.2 的 C 级验证）。
**未验证**：冻结包内该入口的可达性；本卡未触碰打包面。

**未验证项（本卡口径）**：正式容量/吞吐（1 万→10 万→30 万）与 §12.2 的 P95 目标属 P4-02B；
真实模型向量下的维护行为；ANN 与 §12.3 的 0.95；A3；GTX 1660 实机；多世代混批注入；
30 万档的 `--repair` 审计成本；并发维护吞吐（F2）。本卡所有数字来自合成夹具（非真实模型），
且只在本机（RTX 4060 Laptop / Windows 11）测量。

## 11. 停止点与下一步（审查路由）

任务卡要求：**独立 DS-H 全量工程主审 → Sol high 聚焦终审**。因此本轮停在
`IMPLEMENTED / VALIDATION COMPLETE`，**未标 P4-02A PASS、未启动 P4-02B**。

请在**新会话**用 **DS-H / DeepSeek V4.1 Flash / 高推理**做全量工程主审
（`docs/reports/P4-02A-dsh-engineering-review.md`：完整 diff、入口真实性、并发口径与不变量 3 的证据、
D 级注入真实性、与 P2-03/P4-01C 报告一致性、F1～F3 与未验证项），通过后状态写
`ENGINEERING REVIEW PASS / SOL FOCUSED REVIEW PENDING`；随后由 **C2 / GPT-5.6 Sol / high** 只审
「入口是否真正接线、并发口径、旧 revision 不回滚、busy-timeout 边界」及其直接回归；
通过后才标 **P4-02A PASS** 并启动 **P4-02B**。模型切换始终由用户在 Codex 中手动完成。
