# P4-02B DS-H 容量证据专项审查与修复设计

日期：2026-09-24

基线：`b32d33a`（Sol 性能架构审查提交）；被审实现 `0808c64`（P4-02B 三档容量验证），其起点为 `0d13023`

性质：**独立 DS-H 容量证据专项审查（只读产品面）+ 修复设计**。本报告不授予 P4-02B PASS：
F2 未达标是能力事实，F1 的突发二次成本已在真实入口复现，A3/ANN 仍未授权未验证，真实模型与
GTX 1660 仍 BLOCKED。审查依据 [任务卡](../tasks/P4-02B.md)、[Sol 性能架构审查](P4-02B-performance-architecture-review.md)、
[实现报告](P4-02B-capacity-validation.md) 与主架构 §12.1～§12.4、V3.1 §2.2/§2.4/§9.1/§11。

## 1. 结论摘要

| 项 | 结论 |
|---|---|
| 变更面 | **通过**。`0d13023 → 0808c64` 只新增工具/测试/报告/下一卡与状态；禁止面（`src/**`、`0001`～`0015`、`SUPPORTED_SCHEMA_VERSION`、ADR/companion、信封/错误码、打包面、`pyproject.toml`、`uv.lock`）**零 diff** |
| 测量是否走产品入口 | **通过**。scan/catalog/缩略图/语义/查询/D 级注入均调用产品函数或产品 CLI（见 §3） |
| B 级与本卡 focused | **已复现**：主审自跑全量 `1682 / 0 / 0 / 48`、本卡 `11 / 0 / 0 / 0`，与 DS-L 留存的 JUnit 逐数相同 |
| 三档真实完成 | **仅核读**（DS-L 产物内部自洽：30 万 `PUBLISHED/CONFIRMED/semantic_ready = 300 000`、generation `READY`、`pending=0`）；主审未重跑 10 万/30 万 |
| 产品入口可复现性 | **已复现**：主审在另一中文+空格工作目录自跑 800 档全阶段 + 五项 D 级注入，全部指标成立（§4.3） |
| F1 语义积压二次成本 | **已复现并定位**（`EXPLAIN` 两侧 `USE TEMP B-TREE FOR ORDER BY`；30 万积压每次领取 117.95 ms ≈ 8.5 件/秒）（§4.4） |
| F2 查询未达标 | **成立**（30 万 service P95 125 174.8 ms / 3 样本，目标 ≤ 1 s）；主审在自建的 10 000 档真实产品 data root 上把分块路径拆开测量（§4.5） |
| F3 同规模 2.4 倍差异 | **成立且未归因**；主审给出受控归因设计（[P4-02D](../tasks/P4-02D.md)）并新增一条可验证变量（§7 F4） |
| 新增发现 F4 | `asset_id` 标量索引**只覆盖创建当刻的行**：真实 10 000 档产品 data root 的索引报 `num_indexed_rows=5000 / num_unindexed_rows=5000`（§7） |
| 路由 | F1 → DS-H 完整受影响面主审（Sol 仅在改公平/窗口时必选）；**F2 的修复设计命中协议/策略裁决 ⇒ Sol high 聚焦终审必选**，状态停在 `ENGINEERING REVIEW PASS / SOL FOCUSED REVIEW PENDING`；未命中 V3.1 §2.2 六类条件，不升 DS-MAX |

## 2. 变更面与禁止面核对

`git diff --name-only 0d13023 0808c64`：

```
docs/development-status.md
docs/reports/P4-02B-capacity-validation.md
docs/tasks/P4-03.md
tests/integration/test_p4_02_capacity_validation.py        (新增 244 行)
tools/diagnostics/capacity_validation.py                   (新增 1522 行)
```

`git diff --stat 0d13023 0808c64 -- src tools/packaging PhotoSearch.spec pyproject.toml uv.lock docs/decisions`
输出为空，即禁止面零改动；工作树在审查开始与结束时都干净（`git status --short` 无输出）。
这意味着本卡符合卡面「只新增测量工具/夹具/测试/报告」的边界，**不触发**扩大为完整受影响面主审的条件；
主审因此只按需抽查未改的产品调用链，未重复审 P4-02A/P4-02C 已通过的实现。

## 3. 测量口径与产品入口核对

逐段读 `tools/diagnostics/capacity_validation.py` 后的结论（不是只看报告自述）：

| 阶段 | 代码中的真实调用 | 判定 |
|---|---|---|
| 发现 scan | `CatalogService.open(...).register_source/run_scan` | 产品入口 |
| 目录可查 | `search.documents.queue_depth/catch_up/projection_summary` + `search_documents_fts` 行数 | 产品入口 |
| 缩略图 | `CatalogService.generate_thumbnail`（P1-03 媒体缓存） | 产品入口 |
| 语义发布 | `jobs.enqueue`（产品队列）→ `engine.Scheduler.run_until_idle`（真实调度器 + `RuntimeCapabilities` 资格门）→ `JobResult` 信封 → `ArtifactStore.prepare/persist_result` | 产品入口 |
| 语义维护 | 子进程 `python -m photo_search.indexing.cli drain --data-root ... --json`（`PYTHONIOENCODING=utf-8`） | 产品 CLI 入口 |
| 标量索引 | 同一 drain 的 `scalar_index` 字段 + 独立一次 `--limit 0` 复跑 | 产品维护路径 |
| 查询 | `search.service.search_assets`（service 层）与真实 ASGI `create_app` + `uvicorn` + `httpx`（session/CSRF 全走） | 产品入口 |
| D 级注入 | 杀真子进程 drain、`IndexCoordinator.process_batch(failpoint="after_lance")`、`maintenance.drain_semantic_outbox`、`bench.publish_embedding`（真实 outbox/artifact 协议）、`search_assets` | 产品函数 |

诚实限制的核对：报告 §3.4 明确声明「没有真实模型权重，向量由确定性替身产生，space/processor 身份沿用
pinned `chinese-clip-vit-b-16`（`space_id=chinese-clip-vit-b-16:c8ac42d3907c57b0`，
`processor_id=chinese-clip-vit-b-16@36e679e65c2a`，512 维、cosine）」，与
`tools/diagnostics/ann_scale_benchmark.py:90-93`、`DeterministicEncoder` 一致；`p4-02b-*.json` 的
`blocked` 数组与 §10 表逐条对应。**没有发现「用替身数字宣称模型吞吐」或「绕开产品入口」的情形。**

## 4. 本轮独立复现结果

### 4.1 B 级全量（主审自跑）

受限令牌下 `--basetemp` 会得到 `WinError 5`/`FileNotFoundError`（已在 §8 记为环境事实，未改 ACL）；
按正常 Windows 权限路由重跑：

```
uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration \
  --basetemp=.pytest-basetemp-p402b-dsh/full --junitxml=test-results/p4-02b-dsh-full.xml
```

`test-results/p4-02b-dsh-full.xml`：**1682 tests / 0 failures / 0 errors / 48 skipped**（263.4 s），
与 DS-L 的 `p4-02b-full.xml`（259.2 s）逐数相同。

### 4.2 本卡 focused（主审自跑）

`test-results/p4-02b-dsh-scale.xml`：**11 / 0 / 0 / 0**（11.2 s），与 `p4-02b-scale.xml`（12.5 s）相同。
11 条用例覆盖夹具可复现、阶段完整性、标量索引/READY/`semantic_ready`、§12.2 service+HTTP 目标、
后台争用、五项 D 级注入；断言读的是工具产出的 JSON 字段，不是空转。

### 4.3 800 档产品入口 pilot + 五项 D 级注入（主审自跑，中文+空格工作目录）

```
uv run --no-sync python tools/diagnostics/capacity_validation.py --scales 800 --injection-scale 800 \
  --work-dir "tmp/p402b-dsh 复核 验证" --queries 5 --warm-seconds 0 \
  --output test-results/p4-02b-dsh-pilot.json
```

`status=ok`，`seed=20260924`，`work_dir="tmp\\p402b-dsh 复核 验证"`：scan `SUCCEEDED` 800（444.5 件/秒）、
目录 `SYNCED` 800 文档 / FTS 800 行、语义 `applied=800`、`semantic_ready=800`、`pending=0`、
generation `READY`、`asset_id_idx=created`；query cold 34.7 ms、service P95 18.5 ms、HTTP P95 40.6 ms；
维护同时前台 P95 14.25 ms（25 样本、重叠 338.1 ms）。

五项注入在 796 资产上全部成立：中途杀 drain `mid_state={CONFIRMED 100, IN_FLIGHT 10, PENDING 686}`、
恢复 `applied=696`、`index_entries=engine_rows=796`、`all_confirmed=true`；`after_lance` 崩溃后
`{CONFIRMED 796, IN_FLIGHT 1}` → 恢复 `applied=1` → `{CONFIRMED 797}`（无重复行）；内容变更后
`entry_artifact=p4-02b-change-new-401`、`confirmation_revision=1`、Lance 与新向量距离 `0.0`、与旧向量 `0.9587`；
离线位置 `no_ghost_hit=true`（派生行仍在且 `visible=true`，产品检索面无幽灵命中）；未确认新向量
`hit_before=false → drain 后 hit_after=true`。**结论：三档之外的入口、口径与故障面由第二个操作者独立复现。**

### 4.4 F1 独立复现与最小修验证（`tmp/p402b-dsh/f1_claim_probe.py` → `test-results/p4-02b-dsh-f1-claim.json`）

探针在**真实迁移后的 catalog**（真实 `jobs` DDL 与索引）里插入合成可运行积压，直接调用产品
`jobs.claimable_candidates`，并与其「候选上界 + 原公式精确裁决」的最小修形式对比。

| 积压 | 现行领取（中位） | 有界领取 + 新索引 | 有界领取、无新索引 | 现行领取的查询计划 |
|---:|---:|---:|---:|---|
| 10 000 | 2.302 ms | 0.947 ms | 2.821 ms | 两侧 `USE TEMP B-TREE FOR ORDER BY` |
| 50 000 | 11.128 ms | 1.020 ms | 18.124 ms | 同上 |
| 100 000 | 21.845 ms | 0.918 ms | 34.589 ms | 同上 |
| 300 000 | 117.950 ms | 0.938 ms | 157.857 ms | 同上 |

- **根因证据**：现行 SQL 两侧都是 `SEARCH jobs USING INDEX idx_jobs_processor_state (...)` **加
  `USE TEMP B-TREE FOR ORDER BY`** —— 每次领取都物化并排序整个可运行积压。300 000 积压下
  117.95 ms/次 ≈ **8.5 件/秒**，与 DS-L 观测的 11.3 件/秒（截断样本）同量级；10 万积压 21.8 ms/次
  ≈ 45.8 件/秒，与突发的 47.3 件/秒吻合，即突发模式在 10 万档的吞吐可由每次领取成本直接解释。
- **最小修方向已被独立验证**：按 `(side, priority)` 类取「该类最旧 `limit` 行」作为候选上界
  （同类内 `effective` 对 `created_at` 单调，故该类的全局序就是 `created_at ASC, job_id ASC`），
  再用原公式与三个冻结排序键精确裁决；`ORDER BY effective_priority DESC, created_at ASC, job_id ASC`
  在带新索引时输出**完全相同**的领取序列。
- **等价性**：240 组随机状态（混合优先级含负数与 5、`created_at` 跨过去/未来、同毫秒、`run_after`
  未来、RUNNING 过期/未过期、非目标 processor、SUCCEEDED/RETRY_WAIT）× 6 个 `limit`（1/2/3/7/64/256）
  × 3 个 aging 窗口（1/60 000/86 400 000）= **4 320 次页比较全部逐项相同**（`job_id` 顺序与
  `effective_priority` 值都相同）。等价性在两份数据库上分别成立（等价性库**没有**新索引），
  说明新索引只影响成本、不影响语义。
- **新索引是必需的**：无 `(state, priority, created_at, job_id)` 覆盖索引时，有界形式比现行更慢
  （30 万积压 157.9 ms）；带索引后计划变为 `SEARCH jobs USING INDEX probe_idx_jobs_claim (state=? AND priority=?)`。
  因此 F1 的最小修**必须新增一次 migration**，属卡面「新增索引/migration ⇒ 另立实现卡」的边界。

### 4.5 F2 在真实 data root 上的分块刻画（`tmp/p402b-dsh/f2_block_probe.py` → `test-results/p4-02b-dsh-f2-blocks.json`）

主审自建 10 000 档**真实产品** data root（`tmp/p402b-dsh 复核 验证/scale-10000`，
scan 26.9 s/372 件/秒、语义 86.5 s、drain 5.6 s、暖机 service 202.2 ms），然后在同一代上用产品
`retrieval.vector_channel` 扫 `block_size`：

| `block_size` | 块数 | 总耗时（中位） | Lance 引擎合计 | SQLite 候选 walk | 返回行 | 与最小块的页是否相同 |
|---:|---:|---:|---:|---:|---:|---|
| 250 | 40 | 722.3 ms | 633.7 ms | 51.5 ms | 40×20 | 相同 |
| 1 000 | 10 | 282.6 ms | 282.2 ms | 7.2 ms | 10×20 | 相同 |
| 5 000 | 2 | 162.1 ms | 144.7 ms | 5.8 ms | 2×20 | 相同 |
| 20 000 | 1 | 155.5 ms | 144.4 ms | 6.3 ms | 1×20 | 相同 |

- **候选块约束是访问路径参数**：四种块大小返回**完全相同的 `(distance, asset_id)` top-20 页**，
  证明「每块 top-k 精确归并 = 候选集全局 top-k」在当前实现里成立（这是 F2 修法可依赖的前提）。
- **成本结构**：引擎耗时对块数近似 `≈ 131 ms + 12.6 ms × 块数`（1 块 144 ms、40 块 634 ms），即
  每个引擎调用有约 130 ms 的**固定开销**（`search_candidates` 每次都经 `_table()` 重新
  `lancedb.connect` + `open_table`），另有随块数增加的边际成本；SQLite keyset walk 本身很便宜
  （40 块 51.5 ms）。
- **同代单次 pass 对照**：不带候选约束的一次引擎查询 12.48 ms（中位），返回 20 行，其原始 top-20
  与块路径页面相同。该数字**只是诊断下界**：它不等于产品语义下的等价检索（见 §6 F2 的 A3 反例），
  也不是 A3 的实现证据。
- 服务层 `search_assets` 同查询 187.4 ms（10 000 档），与 DS-L 报告 §5 的 200.3 ms 一致。
- 「块数 × 每块一次引擎 pass」可以把差距解释到 10 万档；30 万档的 125 s 明显超过这条线性外推
  （60 块 × 约 0.37 s ≈ 22 s），说明还有第二个乘数，见 §7 F4 与 §6 F2 的阶段 0 清单。

### 4.6 仅核读的 DS-L 产物（主审未重跑的部分）

| 产物 | 主审读回的关键事实 |
|---|---|
| `p4-02b-capacity.json` / `p4-02b-capacity-10k-100k.json` | 1 万/10 万完整结构化结果；10 万暖机 service P95 8 317.8 ms、HTTP P95 8 719.1 ms、维护同时前台 12 622.7 ms（1 样本） |
| `p4-02b-capacity-300k.json` | 30 万档：`mode=reuse-data-root`、cold 89 129.4 ms、P50 125 050.5 ms、**P95 125 174.8 ms（3 样本）**、`meets_target=false`、查询工作集峰值 2 118 094 848 B、`index` 全为 300 000 且 `pending=0` |
| `p4-02b-burst-partial.json` | **突发模式**（同一工具、同一替身、一次性投递）：1 万 `execute` 103 943.8 ms / 96.2 件/秒；10 万 2 113 674.5 ms / **47.3 件/秒**；10 万档 burst 查询 P95 3 484.4 ms、HTTP P95 3 474.5 ms |
| `p4-02b-injections.json` | 五项注入的机器可读前后计数（与 §4.3 的主审复现同构） |
| `p4-02b-full.xml` / `p4-02b-scale.xml` | 1682 / 0 / 0 / 48 与 11 / 0 / 0 / 0 |
| `machine` 数组 | Windows 11 26200、Python 3.12.10、32 逻辑核、31.7 GiB、LanceDB 0.38.0、RTX 4060 Laptop（**非 GTX 1660**）、模型权重目录不存在 |

F3 的两次 10 万测量因此是**同一工具、同一查询、同一替身**在「突发写入」与「17 波写入」两种建库形状下的
`3 484.4 ms` 与 `8 317.8 ms`（约 2.39 倍），两组各 40 样本；差异未归因，只保留为待查变量。

## 5. 逐项判定

**已复现（主审本会话独立执行）**

1. 变更面只含工具/测试/报告/文档，禁止面零 diff。
2. 全量 `1682/0/0/48`、本卡 `11/0/0/0`。
3. 800 档全阶段 + 五项 D 级注入在另一中文+空格工作目录成立（含 `asset_id_idx` 创建、`semantic_ready`、
   崩溃窗口、内容变更取代旧向量、离线无幽灵命中、确认后立即可检索）。
4. F1 的 O(积压) 领取成本与 `USE TEMP B-TREE` 计划；最小修的 300 000 积压平化（0.94 ms）与 4 320 次等价性。
5. F2 的分块结构：块大小不改变结果页；引擎成本 = 每调用固定开销 + 块数边际成本；单次 pass 12.48 ms。
6. 10 000 档真实 data root 的 `asset_id` 标量索引覆盖不全（5 000 / 5 000）。

**仅核读（未独立重跑，或只能读产物）**

- 10 万/30 万三档的阶段时间、内存峰值、库大小与生成状态（DS-L 产物内部自洽，未重跑）。
- F3 的两次 10 万对照数值（读 `p4-02b-burst-partial.json` 与 `p4-02b-capacity.json`）。
- 30 万档的 `scalar_index.state` 与语义阶段内存峰值（DS-L 已自列为未记录）。

**未测 / BLOCKED（沿用实现报告 §10，主审未解除任何一条）**

- 真实图文/人脸/OCR 权重与吞吐、显存峰值、含真实文本塔的完整 API P95；
- GTX 1660 6 GB 最低配置；
- 30 万档 HTTP 层、30 万档「维护同时前台」有意义窗口、冷 OS 缓存、长时 soak；
- §12.3 ANN Recall@20 ≥ 0.95（产品不建 ANN，B1），A3 未授权未实现；
- §12.4 第 4/32 条（已由 P2-03/P4-01/P3-07 覆盖，本卡未重测）。

**未达标 / 失败（不得记 PASS）**

- §12.2「30 万暖机文字搜索 ≤ 1 s」：实测 P95 125 174.8 ms（3 样本），差约 125 倍。
- §12.2「后台建库同时前台搜索 ≤ 1.5 s」：10 万档单样本 12 622.7 ms（主审认定应改称「单次超标观察」）。
- F1 能力事实：一次性投递全部积压时领取吞吐随积压下降（96.2 → 47.3 → 11.3 件/秒）。

## 6. 修复设计（拆成三张卡）

三张卡的完整范围、不变量、必测证据与停止条件见任务卡；本节只给决策摘要。

### F1 → [P4-02E](../tasks/P4-02E.md)「语义积压领取有界化」

设计：保留 ADR-0003 §10 的公平公式与三个排序键不变，把「一次领取」拆成
**候选上界（按 `(side, priority)` 取各类最旧 `limit` 行，索引已覆盖）**+
**精确裁决（原公式 + `created_at, job_id`）**；新增覆盖索引需一次 additive migration，
`SUPPORTED_SCHEMA_VERSION 15 → 16`，卡面必须显式授权。**不得**引入领取窗口、持久优先级或改变
「谁先领取」；一旦需要，先冻结契约（ADR）再实现。主审已给出可直接转成正式用例的等价性夹具
（240 状态 × 6 limit × 3 窗口）与成本门槛（300 000 积压每次领取 P95 不得随积压增长）。
审查路由：DS-H 完整受影响面工程主审；Sol 仅在被升级为公平/窗口契约变更时必选。

### F2 → [P4-02F](../tasks/P4-02F.md)「检索延迟（候选块 / A3）」

分两段：**阶段 0 受控归因**（同 data root 分阶段计时、块数与每块耗时、标量索引覆盖率、
每调用固定开销、单次 pass 对照、10 万突发 vs 波次 A/B/B/A）→ **阶段 1 决策闸门**：
能不改权威语义就让 30 万 ≤ 1 s 则按无契约变更实施；否则必须走 A3 的策略化裁决
（资格谓词、并发窗口、回退、以及 Sol §4 的 A/B 反例逐条关闭），并形成 ADR/补遗，
**未证明等价前不授权实现**。若两条路都达不到 ≤ 1 s，停止并交回用户做性能决策，
不得自动放宽 §12.2。

### F3 → [P4-02D](../tasks/P4-02D.md)「同规模检索时延受控归因」

固定同一批资产、向量、查询与 `top_k`，保留两个可丢弃 data root（突发 vs 分波），交替
A/B/B/A，每轮记录候选数、块数、各块耗时、Lance 表 fragment/version/索引覆盖、内存、磁盘与 CPU，
再用「只改写入形状」的复建和结果 ID/距离摘要证明测的是同一检索。该卡是纯测量卡；如果操作者选择
把它的两次 10 万复建并到 [P4-02F](../tasks/P4-02F.md) 的阶段 0 一次做完，允许合并，但不得因此
省掉受控变量。

## 7. 主审新发现：F4 `asset_id` 标量索引只覆盖创建当刻的行

证据：`test-results/p4-02b-dsh-f2-blocks.json` 的 `lance_table.indices[0]`（真实 10 000 档产品
data root，Lance 表 `version=52`、10 000 行）：

```
asset_id_idx (BTree, columns=["asset_id"], num_indexed_rows=5_000, num_unindexed_rows=5_000)
```

原因读代码即可确认：`IndexCoordinator.process_all` 在每批 drain 前调用
`ensure_scalar_indexes`（`src/photo_search/indexing/store.py:214-245, 656-668`），而
`ensure_scalar_index` 只要目录里已存在 `asset_id_idx` 就返回 `present`，**从不重建、也不覆盖后来
追加的 fragment**。产品是「持续分波写入」形状，因此索引在第一批（本卡 5 000 件/波）drain 时建立，
之后所有行都落在未索引 fragment 上；对照 P4-01 的基准夹具用的是
`table.create_scalar_index("asset_id", index_type="BTREE", replace=True)`
（`tools/diagnostics/ann_scale_benchmark.py:1477, 1723`）——在**完整表**上建索引，覆盖 100% 行。

这是一条**性能事实而非正确性问题**（未索引 fragment 由引擎暴力扫描，召回与排序不变）。它至少要改写三处口径，
并且是下面这条差距的**首要待验变量**（尚未证明它是唯一原因）：

1. DS-L 报告里「10 万档 drain 报 `present`」**不能**等同于「候选查询走索引」；
2. P4-01 量到的 3.6 s 是在**完整覆盖**的标量索引上取得的，而 P4-02B 30 万档的 125 s 是在**部分覆盖**
   的索引上取得的，两者的索引覆盖率不同，不能直接对比；
3. 30 万档成本超过「块数 × 单次 pass」的线性外推，与「大量未索引 fragment 每块都被暴力扫描」一致。

F4 因此写入 [P4-02D](../tasks/P4-02D.md) 的必测变量与 [P4-02F](../tasks/P4-02F.md) 的阶段 0 归因项；
其修复（重建/替换索引的时机与维护预算）属于 P4-02F 范围，若选择「周期性重建」还需在本卡记录维护成本，
不得静默扩大维护预算。

## 8. 审查路由、环境与状态

- **不升 DS-MAX**：未命中 V3.1 §2.2 六类条件（无协议冲突、无并发数据损坏、无不可解释的系统级异常）。
- **Sol 是否必选**：F1 走 DS-H 完整受影响面主审即可（执行计划替换，逐项保持公平/租约/资格语义）；
  **F2 的修复设计命中「协议/ADR 裁决」与 §12.2 承诺口径**（A3、结果完整性、查询目标），
  按卡面与 V3.1 §2.4 必须改记 **Sol high 聚焦终审必选**，状态停在
  `ENGINEERING REVIEW PASS / SOL FOCUSED REVIEW PENDING`；在等价性未证明前不授权 F2 的实现。
- **环境事实**：受限令牌下 `--basetemp` 直接 `WinError 5`、`pytest --basetemp` 父目录不存在时报
  `FileNotFoundError [WinError 3]`；均按正常 Windows 权限路由重跑，**未修改任何 ACL**。
  `tmp/p402b-dsh-run1`（上一会话遗留）当前对本令牌不可读，未做清理，也不影响本次结论。
- **本轮未改任何产品代码/测试/文档之外的交付**：只新增本报告、`tmp/p402b-dsh/` 两个探针、
  三张任务卡与状态条目；`test-results/` 新增主审自跑的 JUnit 与 JSON 证据。
  工作目录里另有上一会话遗留的 `test-results/p4-02b-dsh-focused.xml`、`p4-02b-dsh-rerun.xml`
  与 `tmp/p402b-dsh-run1/`（本令牌不可读）——本报告**不引用**它们，本轮结论全部来自上面
  明确列出的自跑证据；清理与否不影响结论，留给用户决定。

## 9. 停止点

1. **P4-02B 不标 PASS**：F1/F2 未收口前，30 万容量结论保持「跑得动但未达标」；真实模型、GTX 1660、
   ANN 0.95、A3 继续 BLOCKED/未验证。
2. 下一步按 **P4-02D（归因，纯测量）→ P4-02E（F1）→ P4-02F（F2）** 独立推进，不占用 P4-03 范围。
3. F2 在阶段 0 之后必须先冻结决策再实现；F1 若被迫改变公平语义或引入领取窗口，先做 ADR 冻结。
4. 模型切换与是否先做 P4-02D 由用户手动决定；本报告不声称任何自动切换或 PASS。

**当前结论：`DS-H EVIDENCE REVIEW PASS（变更面 / 产品入口 / B+D 证据已独立复现）`
+ `F1 已复现并给出最小修设计` + `F2 未达标且修复设计待 Sol high 聚焦终审`
+ `P4-02B NOT PASS`。**
