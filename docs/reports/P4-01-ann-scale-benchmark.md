# P4-01 — 10 万/30 万 exact/ANN 对照、索引维护、过滤分布与并发调度基准

任务卡：[docs/tasks/P4-01.md](../tasks/P4-01.md)。本报告只记录**本机真实运行产品自身代码**得到的
测量事实，并据此回答「30 万规模下到底有没有瓶颈」。本卡**未**修改任何生产代码、迁移、契约或打包面。

## 1. 起始状态与真实 Git 基线

| 项目 | 值 |
|---|---|
| 起始提交 | `0f4a47c P4-00C: record the closure commit in the reports and status`（工作树干净；P4-00C 收口已由用户入库） |
| 本卡新增 | `tools/diagnostics/ann_scale_benchmark.py`、`tests/integration/test_p4_01_index_scale.py`、本报告、`docs/development-status.md`、`docs/tasks/P4-01B.md` |
| 本卡未改 | `0001`～`0015`、`SUPPORTED_SCHEMA_VERSION`（仍为 15）、ADR-0001～0011 与全部伴随物、`search/**`、`indexing/store.py`、`RRF`/BM25/距离公式、`time_desc`/`capture_desc` 语义、`PhotoSearch.spec`、`tools/packaging/**`、`packaged_api_smoke.py` |

机器规格（工具在运行进程内读取，`GlobalMemoryStatusEx` + `nvidia-smi`）：

| 项目 | 值 |
|---|---|
| 系统 | Windows 11 `10.0.26200`，Python 3.12.10 |
| CPU | Intel64 Family 6 Model 183，32 逻辑核 |
| 内存 | 31.7 GB 物理（运行期可用约 7.4 GB） |
| GPU | RTX 4060 Laptop 8 GB（驱动 616.92） |
| 引擎 | lancedb 0.38.0、pyarrow 25.0.1（`uv.lock` 未变） |
| 磁盘 | D: 145.8 GB 可用（夹具全部建在项目内 `tmp/p4-01-基准 空格`，含中文与空格路径） |

**GTX 1660 6 GB 维度：BLOCKED。** 本机没有该卡，按主架构 §9.1 与 V3.1 §9.1 不用 RTX 4060 数据替代，
也不声称最低配置已支持。本卡不涉及真实照片库：全部数据是项目内临时目录里的合成夹具。

## 2. 夹具、种子与数据形状

工具：`tools/diagnostics/ann_scale_benchmark.py`（每个阶段都写机器可读 JSON；stdout 只在
`--output` 缺省时打印完整文档，进度另走 stderr）。落库口径是**真实**的：真 SQLite 目录、真 Lance
generation 表（生产 `_schema`/`_table_name`）、真 `ArtifactStore` 载荷文件与真协议行
（`jobs` + `artifacts` + `index_entries`），检索全部走 `search/retrieval.py` 与 `search/service.py`，
维护全部走 `ArtifactStore.persist_result` + `IndexCoordinator`。

| 项目 | 10 万 | 30 万 |
|---|---:|---:|
| `assets` / `file_locations` / `asset_metadata` / `search_documents` / `search_documents_fts` / `asset_place_assignments` / `index_entries` | 100 000 | 300 000 |
| `faces` = `face_person_links` | 58 333 | 175 000 |
| `index_outbox`（夹具落库后） | 0 | 0 |
| `index_generations` | 1（READY, is_active=1, dim=512） | 1（同上） |
| 夹具向量分布 | 200 个簇心 + 0.45 噪声，L2 归一（clustered） | 同左 |
| 种子 | `20260923`（向量）、`+7`（查询）、`+11`（边界查询） | 同左 |
| SQLite 各表写入耗时 | 旧版工具未逐阶段记录（整体包含在 12 min 级夹具构建内） | `assets` 1.2 s、`file_locations` 9.8 s、`search_documents` 12.7 s、`capture_times` 1.9 s、`place_assignments` 12.1 s、`persons` 9.4 s、**`index_rows`（jobs+artifacts+载荷文件+index_entries）716 s** |
| Lance 批量写入（生产 schema） | — | **1 533 ms / 300 000 行 = 0.0051 ms/行** |
| 落盘体积 | catalog 285.6 MB（另有 WAL）、vectors 401.8 MB、artifacts 1.08 GB | catalog 858.9 MB（另有 WAL）、vectors 618.9 MB、artifacts 3.23 GB |

查询集：**1 000 条固定种子查询向量 + 50 条「边界」查询**（边界查询是库内真实向量的 0.05 噪声扰动，
其真实最近邻就是那条资产），共 1 050 条，与库内向量分布一致。

生产身份来自 pinned 清单（非借用常量）：
`space_id=chinese-clip-vit-b-16:c8ac42d3907c57b0`、`processor_id=chinese-clip-vit-b-16@36e679e65c2a`、
`generation_id=image-gen-5bcba729dfda4897a94974b7`、512 维、cosine、L2。

**本机没有装 pinned 模型权重**，所以夹具向量是确定性的合成向量（clustered 为主分布，uniform 作为
最坏对照），编码器是工具内的 `DeterministicEncoder`（非模型）。这一点在下面每张表的口径里都适用。

### 2.1 三处必须写清的夹具口径

1. **精确基准是 numpy 暴力 top-k，并与引擎自身扫描逐条对齐**。30 万档的交叉校验
   （`engine_scan_cross_check`，8 条查询 × 20 个 id，**无 ANN 索引**）为 **160/160 全等**
   （`all_equal=true`），因此「exact」列的召回率可以作为 ANN 的分母。
2. **大夹具的 Lance 向量用生产 schema 的批量写入物化**，因为生产逐事件路径在 10 万/30 万上无法用于
   建库（见 §5）：逐事件成本单独测量并报告，没有被隐藏。
3. **ANN 索引不是产品当前行为**（`LanceVectorIndex` 明确「no ANN is created here」）。工具在派生表上
   自己建 IVF_PQ 索引，再让**未改动**的生产检索代码去打这张表，用来比较「同一份生产代码在有无 ANN
   索引时的行为」。

## 3. exact vs ANN 对照（10 万 / 30 万）

生产检索路径是 `search/retrieval.py::vector_channel`：按 `index_entries`（CONFIRMED）∩ 权威条件做
keyset 分块（`SCOPE_BLOCK_SIZE = 1000`），**每块发一次引擎查询**，再合并各块 top-k。因此
`blocks = ceil(候选数 / 1000)`，30 万全库查询就是 **300 次引擎查询**。

### 3.1 生产候选遍历 × 三个引擎状态（30 万）

| 路径 | 口径 | P50 | P95 | 冷缓存首查 | blocks | scanned | Recall@20 | Recall@50 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 精确（生产遍历，无 ANN 索引） | 与 numpy 暴力 top-20 对照 | **112 177 ms** | 112 177 ms* | 110 939 ms | 300 | 300 000 | **1.000** | **1.000** |
| 生产遍历 + IVF_PQ（585 分区/64 子向量） | 同上 | **10 965 ms** | 10 965 ms* | 12 178 ms | 300 | 300 000 | **0.100** | 0.107 |
| 引擎单次全库查询（无索引，`table.search`） | 1 次查询 = 1 个 blocks | **374 ms** | 392 ms | — | 1 | 300 000 | 1.000 | 1.000 |
| 引擎单次全库查询 + IVF_PQ | 同上 | **17.1 ms** | 20.7 ms | — | 1 | 300 000 | **0.077** | 0.084 |

\* 本卡的遍历样本是 `--walk-queries 3`（外加 1 次冷查），样本量小，故 P50＝P95；每个值都是
`vector_channel` 的整次调用（含 300 次 SQL keyset + 300 次引擎查询）。

### 3.2 同一对照（10 万）

| 路径 | P50 | 冷缓存首查 | blocks | Recall@20 | Recall@50 |
|---|---:|---:|---:|---:|---:|
| 精确（无 ANN 索引） | **11 520 ms** | 10 477 ms | 100 | **1.000** | **1.000** |
| 生产遍历 + IVF_PQ（195 分区/64 子向量） | **2 482 ms** | 2 591 ms | 100 | **0.067** | 0.127 |
| 引擎单次全库查询（无索引） | 102 ms | — | 1 | 1.000 | 1.000 |
| 引擎单次全库查询 + IVF_PQ | 13.4 ms | — | 1 | **0.109** | 0.124 |

索引本身：10 万档 `195 分区/64 子向量`，构建 15.0 s，`wait_for_index` 0.6 ms，表体积 410 MB；
30 万档 `585 分区/64 子向量`，构建 18.2 s，`wait_for_index` 0.5 ms，表体积 619 MB。
`index_stats` 均为 `num_indexed_rows=全部, num_unindexed_rows=0`。

### 3.3 召回损失的最小归因（单变量）

归因**不在** 10 万/30 万表上做重复重建：在该表上连续第三次 `create_index(replace=True)` 时进程长时间
几乎不消耗 CPU（>9 min 未完成），属于锁定引擎层面的异常（见 §8 C1 建议）。归因改为在**独立的新表**上
每个配置建一次索引（20 000 行夹具，1 000 查询集里的固定 200 条子样，queries=200）：

| 配置（单变量） | clustered Recall@20 | uniform Recall@20 | 建索引 | 无过滤查询 P50 |
|---|---:|---:|---:|---:|
| `num_sub_vectors=64, partitions=39`（默认） | 0.229 | 0.241 | 4.5 s | 14.7 ms |
| `num_sub_vectors=128`（唯一改动：量化更细） | **0.395** | **0.408** | 9.0 s | 17.9 ms |
| `num_sub_vectors=32` | 0.116 | 0.126 | 2.4 s | 15.4 ms |
| `partitions=9`（唯一改动：分区更少） | 0.276 | 0.283 | 4.5 s | 15.0 ms |

结论（最小归因）：

1. **数据分布不是主因**：同一配置在 clustered 与 uniform 上的 Recall@20 只差 ≤0.02。
2. **量化精度是主因**：`num_sub_vectors` 64→128 是单一变量里唯一的大幅提升（+0.17），
   而分区数从 39 降到 9 只带来 +0.05。
3. **即便如此，最好的单变量配置也只有 0.395 ≪ 0.95**（主架构 §12.3 初始目标）。锁定版
   `LanceQueryBuilder` **没有暴露 nprobes / refine_factor**（`dir()` 实测只有
   `where/limit/select/rerank/use_lsm/fast_search` 等），所以「提高探测数」这条通常在 IVF 上调召回的路
   在本卡锁定版本里不可用；这正是审查者需要的下一步设计输入，而不是本卡能顺手改掉的参数。

## 4. 过滤分布与组合条件

夹具的过滤维度贴近生产形状：2 个来源、1 个国家/10 城市/50 景点（三级 `places` + 手动 `MANUAL_ASSIGNED`）、
80% 有 `EXIF_ORIGINAL` 拍摄时间（另 20% 是诚实的「只有文件时间」）、人物 1（`%3==0`）与人物 2（`%4==0`）
（同时命中约 8.3%）。每个档位都用**权威 SQL**（`scope_ids` + `authoritative_page`）与生产遍历同时测量。

### 4.1 30 万档

| 档位（条件） | 候选数 | 选择性 | 权威 SQL 耗时 | 遍历 P50 | blocks（期望） | Recall@20 | 越权命中 |
|---|---:|---:|---:|---:|---:|---:|---:|
| wide：拍摄时间下界（≥90%） | 215 998 | 72.0% | 1 082 ms | 8 236 ms | 216 (216) | 0.100 | 0 |
| half：来源 1 + 城市 1..5 后代 | — | — | **>60 s 被中断** | — | — | — | — |
| narrow：城市 1 后代（≈10%） | 30 000 | 10.0% | 1 031 ms | 1 513 ms | 30 (30) | 0.050 | 0 |
| thin：城市 1 后代 + 人物 1&2（≈1%） | — | — | **>60 s 被中断** | — | — | — | — |

### 4.2 10 万档

| 档位 | 候选数 | 选择性 | 权威 SQL 耗时 | 遍历 P50 | blocks（期望） | Recall@20 | 越权命中 |
|---|---:|---:|---:|---:|---:|---:|---:|
| wide | 71 999 | 72.0% | 151 ms | 1 808 ms | 72 (72) | 0.100 | 0 |
| half | — | — | **>60 s 被中断** | — | — | — | — |
| narrow | 10 000 | 10.0% | 146 ms | 339 ms | 10 (10) | 0.100 | 0 |
| thin | — | — | **>60 s 被中断** | — | — | — | — |

**正确性**：所有跑完的档位 `hits_outside_authority` 均为空、`blocks` 与
`ceil(候选数/1000)` 完全一致，说明生产遍历没有退化为「先全库 Top-K 再过滤」。

**发现的瓶颈（新增）**：`half`（来源 + 地点后代 AND）与 `thin`（地点 + 人物 all）两档的**权威集合 SQL**
在两档规模上都超过 60 s 未返回（被工具的时间预算中断）。同一个夹具里 `wide`（仅拍摄时间）只要 151 ms、
`narrow`（仅地点后代）只要 146 ms，说明这是**该 AND 组合的查询计划退化**，与向量引擎无关。
在 30 万档上，它同时使 §12.4 第 32 条的组合语义校验（地点 + 人物 + 时间 + 语义 AND）**无法完成测量**：
`and_combination` 只记录到 `scope_ids`/`authoritative_page` 超时。按任务卡停止条件 2，这条作为
「无法解释的系统级（查询计划）异常」交回，本卡不擅自改写 SQL 或过滤语义。

### 4.3 投影滞后补回（§12.4 第 32 条的另一半）

在 200 资产的注入夹具上（组合条件可正常返回），把组合范围内的一个资产 `index_entries` 置为
`PENDING_REPAIR` 后：该资产出现在 `pending_projection_page`（`in_topup=true`）且**不**出现在向量命中里
（`returned_as_hit=false`），恢复 CONFIRMED 后又能命中。夹具没有绕过这条补回路径。

## 5. 索引维护与恢复（D 级）

生产逐事件路径：`ArtifactStore.persist_result`（真文件 + 真 outbox）→ `IndexCoordinator.process_next`
（`LanceVectorIndex.upsert` = 一次 `merge_insert`）。

| 指标 | 10 万 | 30 万 |
|---|---:|---:|
| 单条 `upsert` 中位 / P95 / 最大 | 39.3 / 160.4 / 189.9 ms | 50.9 / 144.7 / 166.6 ms |
| 单条写入吞吐 | 25.5 行/s | 19.6 行/s |
| 发布一个 embedding 产物（真文件 + 协议行） | 10.9 ms/资产 | 9.5 ms/资产 |
| `process_next` 重放一个 outbox 事件 | 41.0 ms/事件（24.4 事件/s） | 50.6 ms/事件（19.8 事件/s） |
| 重放后 outbox / `semantic_ready` | 全部 CONFIRMED，5/5 READY | 全部 CONFIRMED，5/5 READY |
| generation | READY + is_active=1 | READY + is_active=1 |

规模含义（算术外推，写明是外推）：按实测 19.6 行/s，30 万行从零建立这一层派生索引需要
**约 4.25 小时**；`process_next` 的 50.6 ms/事件只统计一次 `merge_insert`，而真实图谱里
每次内容/地点/人物变化都会产生事件。作为对照，同一份数据用生产 schema 的批量写入只要
**1.53 s / 30 万行**（0.0051 ms/行）——两者相差约 4 个数量级，这是本卡第二个确定性瓶颈。

### 5.1 四项故障注入（200 资产，全真路径）

| 注入 | 证据 |
|---|---|
| 1. 维护中途中断后重启 | 注入 `after_lance` 崩溃后 outbox = `{IN_FLIGHT: 1, PENDING: 1}`；重启 `process_all` 重放 2 个事件；Lance 行数 200、唯一资产 200（**零逻辑重复、零丢失**） |
| 2. Lance 已写入、outbox 未确认 | 把该事件改回 PENDING/`PENDING_REPAIR` 后重放，两次重放各 1 个事件，该资产 Lance 行仍为 1，outbox 全 CONFIRMED |
| 3. 同资产内容变更 | 变更前旧向量可命中；发布新产物后（entry=`PENDING_REPAIR`）**旧向量不可命中**；重放后新向量可命中 |
| 4. 位置离线后的幽灵向量 | 位置改 `MISSING` 后，该资产不在 `availability=present` 的权威集合中也不被返回，而 Lance 行**仍然存在**（说明去重靠权威候选流，不靠物理删除） |

### 5.2 修复审计（`audit_and_schedule_repairs`）

200 资产的夹具上：一次 `LanceVectorIndex.rows()` 13.6 ms、一次 `has()` 13.8 ms、**整次审计 3 052 ms**
（15.3 ms/资产）。原因是 `has()` 内部调用 `rows()`，而 `rows()` 会把**整张 generation 物化**，
因此一次审计是 `O(资产数 × 行数)`；30 万档上该路径不可运行（时间与内存都不可行）。本卡只在 200 资产上
实测并记录该形状，未在 10 万/30 万上执行。

## 6. 后台维护 + 前台查询并发（§12.2 ≤1.5 s 调度目标）

场景：3 万资产夹具，后台**独立进程**用真实 `IndexCoordinator` 重放 200 个真实 outbox 事件，前台同进程
执行真实 `search_assets`（`text="beach"` + 校验过的查询向量，FTS + 向量双通道 + RRF + 权威复检）。
前后台各用独立 `Catalog` 连接（WAL 一写多读），与产品「API 进程 + 索引进程」的布局一致。

| 指标 | 值 | 对照 §12.2 |
|---|---:|---|
| 前台搜索 P50 | **4 693 ms** | — |
| 前台搜索 P95 | **5 014 ms** | 目标 ≤1 500 ms → **未达标（3.3×）** |
| 后台重放吞吐 | 19.9 事件/s（200 事件 10.0 s） | — |
| 后台剩余 | 0 | — |

说明：3 万档的前台 P95 已经是 5.0 s；§3 已经给出 30 万档单次搜索 **112 s** 的实测，因此
「后台建库时普通文字搜索 P95 ≤ 1.5 s」在正式规模上缺失 **约 75×**。冷启动/冷缓存单独记录：
30 万档冷首查 110.9 s vs 热 112.2 s（该路径对 OS 缓存不敏感，因为每次块查询都重读整张 generation），
两条都在 JSON 里（`cold_ms` / `timings`）。另有两条**日志级**（不在最终 JSON 里）的同口径观察可作交叉
参考：在「刚写完该 generation 的同一个进程」里，同一遍历在 10 万档曾测得 1.6 s 与 3.2 s（两次被后续
修复取代的运行），说明该路径对进程内缓存状态敏感；最终报告采用的 10 万档数字是复用夹具（冷派生存储）
的 11.5 s，三个值没有互相替代。

## 7. 结论：30 万规模下有没有瓶颈

**有，而且是确定性的、可复现的，不是本机偶发。** 四条独立证据：

1. **检索路径本身是 `O(N²/1000`)**：候选遍历把「每 1000 个候选一次引擎查询」和「每次查询全表扫描」
   乘在一起。10 万档 11.5 s、30 万档 112.2 s（≈10×，正好是行数 3× 与块数 3× 的乘积）。主架构 §12.2
   的 1.5 s 目标在 30 万档差 **约 75 倍**。
2. **当前 IVF_PQ 参数下 ANN 不能替代它**：开启索引后遍历降到 11.0 s，但 Recall@20 只有 **0.10**
   （20 万档归因实验里最好也只有 0.395），距 §12.3 的 0.95 很远；且锁定版 API 不暴露 nprobes。
3. **索引维护的逐事件写入比批量写入慢 4 个数量级**（50.9 ms/行 vs 0.0051 ms/行），30 万行按实测吞吐
   约 4.25 小时。
4. **两条过滤组合的权威 SQL 直接退化到分钟级**（>60 s 未返回），并使 §12.4 第 32 条组合校验在 30 万档
   无法完成；并发场景下前台 P95 在 3 万档就已 5.0 s。

按任务卡**停止条件 1**：上述瓶颈的修复需要新增机制（批量/分片写入、索引维护策略、索引参数与召回
承诺、可能的检索路径选择），已经触及「改检索语义 / 扩大或收窄支持承诺」的边界，因此本卡
**没有**修改任何生产代码，只把最小反例与测量数据交回，由用户决定是否单开设计卡。本卡同时给出
`docs/tasks/P4-01B.md` 作为这张设计/决策卡的入口。

## 8. 审查路由与模型档位

- 本卡执行与取证：DS-H（DeepSeek V4.1 Flash / 高推理），阶段内未切换模型、未使用任何自动切换。
- 独立审查：完成后另开独立 DS-H 会话做全量工程主审（`docs/reports/P4-01-dsh-engineering-review.md`）。
- **Sol 是否必选**：本卡不涉及 §11 高风险清单（无迁移/事务/revision/人工状态/协议冻结），默认
  `SOL_NOT_REQUIRED`。若主审把「索引维护语义」升级为协议或 ADR 裁决，则改记 Sol high 聚焦终审必选。
- **C1 建议（按 §2.2 分流）**：本卡出现两条环境/引擎层面的异常，建议在后续卡片里交 C1：
  1. 同一张 10 万行表上连续第三次 `create_index(replace=True)` 进程几乎不消耗 CPU、>9 min 未完成
     （改为显式 `drop_index` + 新建后，同一张表 15.2 s 建好、复用表句柄查询 6.2 ms/次）；
  2. 特定 AND 组合的权威 SQL 从 150 ms 退化到 >60 s 未返回（只与 SQLite 查询计划相关）。
- 未命中 V3.1 §2.2 六类条件，未升 DS-MAX。

## 9. 验收命令与结果（B + D）

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path
# 10 万档（复用已物化夹具，避免重复写 10 万载荷文件）
uv run --no-sync python tools/diagnostics/ann_scale_benchmark.py --mode scale --scale 100000 `
  --reuse-fixture --work-dir "tmp/p4-01-基准 空格" --output test-results/p4-01-scale-100k.json `
  --query-budget-seconds 60
# 30 万档 + 归因 + D 级注入 + 并发（任务卡允许 30 万档拆成多次运行）
uv run --no-sync python tools/diagnostics/ann_scale_benchmark.py --mode all --scale 300000 `
  --work-dir "tmp/p4-01-基准 空格" --output test-results/p4-01-run-300k.json `
  --query-budget-seconds 60
# 合并成任务卡要求的一个机器可读文档
uv run --no-sync python -c "<见 §9.1>"
uv run --no-sync python -m pytest -q -p no:cacheprovider tests/integration/test_p4_01_index_scale.py `
  --basetemp=.pytest-basetemp-p401/scale --junitxml=test-results/p4-01-scale.xml
uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration `
  --basetemp=.pytest-basetemp-p401/full --junitxml=test-results/p4-01-full.xml
uv run --no-sync ruff check .
git diff --check
uv lock --check
```

### 9.1 合并步骤

```python
import json, pathlib
base = pathlib.Path("test-results")
merged = json.loads((base / "p4-01-run-300k.json").read_text(encoding="utf-8"))
merged["scales"] = [json.loads((base / "p4-01-scale-100k.json").read_text(encoding="utf-8"))["scales"][0]] + merged["scales"]
(base / "p4-01-ann-scale.json").write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
```

### 9.2 结果

| 验收 | 命令（要点） | 结果 | 证据 |
|---|---|---:|---|
| B 级全量 | `pytest -q tests/unit tests/integration` | **1639 / 0 / 0 / 48 skip**，265.9 s | `test-results/p4-01-full.xml` |
| 本卡专项 | `pytest -q tests/integration/test_p4_01_index_scale.py` | **6 / 0 / 0 / 0**，16.0 s | `test-results/p4-01-scale.xml` |
| 10 万档基准 | `--mode scale --scale 100000 --reuse-fixture` | 8 条过滤/检索/维护结论 | `test-results/p4-01-scale-100k.json` |
| 30 万档 + 归因 + D + C | `--mode all --scale 300000` | 21 条结论（scale/attribution/injections/concurrency） | `test-results/p4-01-run-300k.json` |
| 合并文档 | §9.1 | scales=[100000, 300000] + attribution + maintenance_injections + concurrency | `test-results/p4-01-ann-scale.json` |
| Ruff | `uv run --no-sync ruff check .` | `All checks passed!`（退出码 0；受限令牌下仍打印一条既有 `os error 5` 遍历警告） | — |
| 差异检查 | `git diff --check` | 退出码 0 | — |
| 依赖锁定 | `uv lock --check` | `Resolved 96 packages`（未变） | — |

48 个 skip 全部是既有的「未安装 pinned 权重」环境跳过，与本卡无关。
测试运行使用项目内 basetemp（`.pytest-basetemp-p401d/...`）；沙箱内建临时目录时本机出现
`PermissionError [WinError 5]`，按既有做法改用正常 Windows 权限路由执行，未改任何 ACL。

## 10. 失败修复记录与未验证项

### 10.1 工具自身的失败与修复（都是取证工具，不是产品缺陷）

1. 夹具 `index_entries.artifact_id` 有真外键 → 改为写入协议完整的 `jobs` + `artifacts` + 真载荷文件。
2. `faces.face_id` 必须是 64 位十六进制 → 夹具改用 sha256。
3. 人物 1/2 的构造周期与城市 1 的后代集合不相交 → `thin` 档一度为空集；改为 `%3`/`%4` 并集后
   交集约 0.67%（10 万）/1.0%（30 万）。
4. 组合校验的手写 SQL 未展开地点后代 → 与生产谓词不一致；改为显式列出子景点后
   `sql_agrees=true`。
5. 「无索引」档曾被上一轮遗留索引污染（10 万档首次测得 1.6 s / 3.2 s / 15.3 s 三个不同值） →
   工具改为在测量前显式 `drop_index` 并记录 `dropped`，同时把跨进程引擎扫描交叉校验移到清理之后。
6. ANN 查询回环曾重复开连接、并在同一张表上反复 `replace=True` 重建 → 改为批量复用表句柄 +
   每个配置用**全新表**建一次索引（详见 §3.3、§8）。

### 10.2 未验证项（不得当 PASS）

- **GTX 1660 6 GB 实机**：BLOCKED（本机无该卡），未做最低配置的吞吐/查询/显存/长时稳定性。
- **真实模型向量**：本机未装 pinned 权重，全部召回数字来自合成向量；真实 Chinese-CLIP 空间的
  ANN 召回需要一次真模型夹具复核。
- **30 万档的两条过滤档**（来源+地点后代、地点+人物 all）与 §12.4 第 32 条组合校验：权威 SQL 超时，
  未取得候选数/选择性/召回；组合语义只在 200 资产夹具上验证。
- **10 万档的引擎扫描交叉校验**：那一轮在清理索引之前测量（与 ANN 结果对比），
  30 万档的同一校验是干净的（160/160 全等）；10 万档需要一次重跑才能给出同口径数字。
- **35～40 万压力观察**：未做（主架构 §12.1 允许）。
- **多进程并发写入**、真实 30 万 txn 的磁盘耗尽/长时稳定性、Windows 冷启动、`fast_search` 语义、
  `IVF_HNSW`/其它索引方案：均未测（后者还需先有设计卡）。
- 30 万档夹具构建早期约 11 分钟有一个残留诊断进程同时运行（已发现并终止），因此
  **夹具写入阶段**的耗时应视为近似；检索/过滤/维护/并发阶段的测量是在单进程下完成的。
