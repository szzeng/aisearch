# P4-02K — F2 目标预算拆解与 A3 决策分析（只读）

**性质**：只读分析卡。本报告不改 `src/**`、不改迁移与 `SUPPORTED_SCHEMA_VERSION`、不改任何 ADR 或
companion、不改 §12.2、不新增测试、不跑新的产品测量。它把 §12.2 的「30 万暖机普通文字搜索
API P95 ≤ 1 000 ms（含文本编码）」拆成互斥分量，代入仓库内已有测量，给出「A3 达标还差什么」的
条件式与「改 §12.2 实际放弃哪条承诺」的对照表，供用户裁决 F2 的去留。

**本报告不是 PASS，也不是 A3 授权**：`A3 未实现/未启用`、`§12.2 未改`、`P4-02F 非性能 PASS`、
`F2 未关闭` 全部维持。三选一结论见 §7。

- 基线：`d6b6666`（`git log -1 --oneline`），分支 `master`。
- 开工时 `git status --short --untracked-files=all` 已有他人未提交产物：`docs/decisions/ADR-0013-*.md`、
  `docs/decisions/contracts/p4-upgrade-contracts.v1.json`、`docs/reports/P4-03A-dsh-contract-draft.md`、
  `docs/tasks/P4-03A.md`、`docs/tasks/P4-02K.md`、`tests/unit/test_p4_upgrade_contracts.py`，以及
  `docs/development-status.md` 的既有改动。**本卡一律保留、不修改、不提交、不删除，也不计入本卡改动**。
- 本卡新增：`tools/diagnostics/p4_02k_budget.py`（只读重算脚本）、本报告、`test-results/p4-02k-budget.json`
  （`.gitignore` 内，不入库）、`docs/development-status.md` 追加一节。

本报告引用的每个数字都由 `tools/diagnostics/p4_02k_budget.py` 按「来源文件 + JSON 字段路径」实际读出
并写入 `test-results/p4-02k-budget.json`；脚本读不到字段即非零退出（本轮 8/8 恒等式通过）。

**与卡面字面口径的一处已声明偏离（请主审判定）**：卡面写脚本「只从 `test-results/*.json` 取字段」。
本脚本额外**只读**了一个仓库内机器可读文件 `docs/reports/P0-06-chinese-clip-gpu-check.json`（10 187 B，
未修改、未移动），因为真实文本编码 p95（37.32 ms）不在任何 `test-results/*.json` 里，而卡面同样要求
该数字**不得手抄**。该读取是只读、单文件、零写入，也不改变任何既有证据的口径；如主审认为必须严格
限定在 `test-results/`，处置方式是把 P0-06 的该项证据**只读复制**进 `test-results/` 后重算（本卡未做，
以免在收口前改动证据目录）。

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path
uv run --no-sync python tools/diagnostics/p4_02k_budget.py --output test-results/p4-02k-budget.json
```

被读证据指纹（SHA-256，脚本 `sources` 字段原样输出）：

| 文件 | bytes | sha256（前 16 位） |
|---|---:|---|
| `test-results/p4-02f-stage0-refined.json` | 44 628 | `5e598eee888f4dac` |
| `test-results/p4-02f-stage0-300k.json` | 67 659 | `e1c8ec37fef993fe` |
| `test-results/p4-02f-blocks-gate-a-ceiling.json` | 17 538 | `d22b453eaac85f88` |
| `test-results/p4-02j-stage0-300k.json` | 62 102 | `f48e114f9493443a` |
| `test-results/p4-02j-contention-close.json` | 8 870 | `777ad591bb6716cd` |
| `test-results/p4-02j-contention-unwarmed.json` | 8 831 | `35c2170590305bb3` |
| `test-results/p4-02j-contention-catchup-control.json` | 1 744 | `a2bc585c9aeb63cf` |
| `test-results/p4-02j-concurrency-300k.json` | 2 577 | `62727b15afb4597a` |
| `test-results/p4-02b-dsh-pilot.json` | 22 105 | `6f30a8e64896df14` |
| `docs/reports/P0-06-chinese-clip-gpu-check.json` | 10 187 | `b76ecae2cdb196b4` |

## 0. §12.2 原文与两条直接推论

主架构 §12.2 原文（逐字引用）：

> 30 万资产，已启动、模型已暖机的普通文字搜索 | API P95 ≤ 1 秒，包含文本编码、召回、融合和元数据读取

同一节随后写着：

> 以上正常模式目标须在 GTX 1660 6 GB 最低配置和 RTX 4060 开发配置分别记录，不能用较高配置结果
> 替代最低配置。最低配置实机测试最迟 P4 完成；未达目标时记录限制并作明确性能决策，**不自动放宽门槛**。

两条直接推论（本卡全部结论都建立在这两条上）：

1. 正确口径是 **API P95 ≤ 1 000 ms 且包含文本编码**，不是「A3 service < 1 s」，也不是「检索核心
   < 1 s 而编码/HTTP 另算」；
2. 「记录限制 + 作明确性能决策」是 §12.2 **自带的**路径，**不需要改 §12.2**，也不是 ADR 变更。

§12.2 同表其余行是**独立指标**，改「普通文字搜索」那一行**不会自动**改它们：
「第一屏图片可见 ≤ 2 s」（检索核心若 > 2 s 即连带失守）与「后台建库同时普通文字搜索 ≤ 1.5 s」
（最新真重叠实测 P95 **20 469.5 ms**，同样未达标）。§12.2 末段关于 35～40 万「非承诺压力观察」的
表述与本卡结论无关，**不得据此扩大支持承诺**。

## 1. 互斥装配表

来源：`test-results/p4-02f-stage0-refined.json` → `rounds[0].summary`（30 万、全覆盖、暖缓存、
替身编码器、同一次采样 2 样本）。

| 分量（互斥） | JSON 字段路径 | 统计量 | 样本量 | 值 |
|---|---|---|---:|---:|
| `sqlite_read_all` 合计 | `median_by_stage_ms.sqlite_read_all` | 每样本合计的中位 | 2 | **589.34 ms** |
| ├ 候选 keyset walk（A3 要替换的那条路） | `median_by_stage_ms.sqlite_candidate_walk` | 中位 | 2 | 229.014 ms |
| ├ pending 补页 | `median_by_stage_ms.sqlite_pending_topup` | 中位 | 2 | 359.012 ms |
| └ 其余 SQLite 读 | `median_by_stage_ms.sqlite_other_reads` | 派生残差 | 2 | 1.314 ms |
| Lance 引擎合计（60 块，**候选块路径**） | `median_by_stage_ms.engine_total` | 60 次调用的中位 | 2 | **4 686.572 ms** |
| `other_fusion_and_authority` | `median_by_stage_ms.other_fusion_and_authority` | 互斥残差 | 2 | **77.348 ms** |
| service 中位 | `service.median_ms` | 中位 | 2 | **5 353.26 ms** |
| service P95 | `service.p95_ms` | p95 | 2 | 5 530.48 ms |

**已复核的恒等式（脚本 `identities`，8/8 通过）**：

1. `sqlite_read_all + engine_total + other_fusion_and_authority == service.median_ms`
   → `589.34 + 4 686.572 + 77.348 = 5 353.26`（`identities[0].delta_ms = 0.0`）。这证明
   `other_fusion_and_authority` 是**互斥残差**，**不是**第三个可叠加的资源。
2. `sqlite_candidate_walk + sqlite_pending_topup + sqlite_other_reads == sqlite_read_all`
   → `229.014 + 359.012 + 1.314 = 589.34`。
3. 同一恒等式在 10 万对照轮与 P4-02J 修复覆盖后的 30 万轮同样成立；另有逐样本精确恒等式
   （`sqlite_ms + engine_ms + other_ms == service_ms`，30 万 2 样本与 P4-02J 5 样本，最大偏差 0.001 ms
   即 JSON 三位小数取整）。

产品阶段视图（`stage_medians_ms.*`：`authoritative_page` 28.717、`fts_channel` 0.712、
`reciprocal_rank_fusion` 0.041、`scope_view` 0.092、`_verified_ids` 0.153、`text_column_matches` 0.001、
`_rewrite_hit` 0.028、`pending_projection_page` 359.04、`documents.catch_up` 0.011）**与上面的桶重叠**，
只能用于归因，**不得叠加**——证据文件自带的 `environment.service_stage_instrumentation` 逐字声明：

> their wall time overlaps the sqlite and engine buckets and is an attribution view, not a third resource

**两条互斥路径（本卡最容易出错的一处）**：

- **候选块路径**（现行产品）：候选 keyset walk `229.014` + **引擎 60 块合计 4 686.572** + pending 补页
  `359.012` + 残差 `77.348`（其余 SQLite 读 1.314 已含在 `sqlite_read_all` 内）。
- **A3 路径**：**单次无约束引擎 pass**（§2 的 346.961 ms，5 样本）+ pending 补页 `359.012` + 残差
  `77.348`。

候选块的 `229.014` 与 `4 686.572` **属于 A3 要替换掉的对象**，**不得**与 A3 的引擎 pass 相加。
P4-02F §9 已声明单次 pass「不带任何候选约束、绕开产品语义下的权威候选集，不构成 A3 授权」，
本卡沿用该免责：它只用于预算量级，不用于等价性。

## 2. 余量与条件式

两种装配（30 万、暖缓存、替身编码器；**均不含 A3 证书**）：

| 装配 | 引擎侧 | + pending 补页 | + 残差 | + 文本编码 p95 | + HTTP 增量 | 合计 | 距 1 000 ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| 点估计（引擎取中位 346.961） | 346.961 | 359.012 | 77.348 | 37.32 | 22.13 | **842.771 ms** | **余量 157.229 ms** |
| 观测高值（引擎取 max 476.547） | 476.547 | 359.012 | 77.348 | 37.32 | 22.13 | **972.357 ms** | **余量 27.643 ms** |

（不含编码/HTTP 的 A3 下界分别为 **783.321 ms** 与 **912.907 ms**。）

字段来源与样本量：引擎单次 pass `test-results/p4-02f-stage0-300k.json` →
`single_pass.W.ms_median`（中位，5 样本）/`single_pass.W.ms_all[0]`（max，5 样本）；
pending 补页与残差同 §1（2 样本）；文本编码
`docs/reports/P0-06-chinese-clip-gpu-check.json` → `gpu.latency.text.p95_ms`（p95，20 样本，RTX 4060、
暖机、batch=1、52 token，该报告自带「不是 GTX 1660 承诺」）；HTTP 增量为
`test-results/p4-02b-dsh-pilot.json` → `measurements[0].query.http.warm.p95_ms` 减
`measurements[0].query.service.p95_ms` = `40.61 − 18.48 = 22.13 ms`，**夹具只有 800 资产、5 样本**，
30 万档 HTTP 层**未测**，只能当量级指示。

**条件式**。设 §12.2 目标为 1 000 ms，则 A3 端到端必须满足

```
T_engine_p95 + T_pending_p95  ≤  X
X = 1000 − T_residual − T_encode − T_http − T_certificate − M
```

其中 `M` 是**工程自设**的安全余量，`T_certificate` 是 A3 证书成本（见 §3）。把已实测残差 77.348、
编码 37.32、HTTP 增量 22.13（三项合计 136.798）代入，并**自设** `T_certificate = 10 ms`、
`M = 100 ms`（两个值都是本卡工程自选，**不是** §12.2 原文，需用户/Sol 认可才可作为门槛）：

```
X = 1000 − 136.798 − 10 − 100 = 753.202 ms
```

对照实测：`T_engine + T_pending` 中位 **705.973 ms**（余量 47.229 ms），观测高值 **835.559 ms**
（**已超 X 82.357 ms**）。

**自设值声明（必须随结论一起读）**：`X = 753.202 ms` 与 `M = 100 ms`、`T_certificate = 10 ms`
都是本卡的工程自设，**§12.2 原文没有任何分量门槛**；只有用户/Sol 认可后才能作为门槛使用。
本卡不据此宣布任何达标或未达标。

## 3. 证书成本：必须分成两个分支

| 分支 | 已有证据 | 可否外推 | 对总预算的影响 | 缺失机制 |
|---|---|---|---|---|
| **R（行级 M1 核对）** | 真实 Lance 表 **20 000 行**读全代每行 `(record_id, asset_id, space_id, projection_revision)` = **36.4–37.6 ms ≈ 一次整代 pass**；同夹具真实候选块路径 226.9–253.9 ms（≈6.5×）。探针是 **dim=2 合成向量** | **不可**外推为 30 万结论（原报告明确只用于同夹具相对比较） | 两份 P4-02H 报告的**量级判断**：30 万档 A3 自身 ≈0.37 s（原文引用，见脚本 `quotations`）叠行级核对 ≈0.35 s ⇒ **很可能吃掉预算** | 产品**没有**跨库栅栏/版本对：`src/photo_search/indexing/store.py:134` 的 `_table` 总是 `open_table` 取最新版本，无 version pin；SQLite 侧读走独立快照。M1 需**新增机制** |
| **F（代级栅栏）** | **无实现、无基准**，例：「该代无未确认 outbox 事件」这类**一次 SQLite 查询** + 写入次序不变式，使核对成为结构性判断 | 无从外推 | 若成本 ≪30 ms，点估计装配（842.771 ms）仍有 157 ms 余量；若 ≫100 ms，则落 §7 的 ③ | 机制本身、及其成本/崩溃恢复影响**均未评估**（P4-02H r2 §6.1；M2/M3 的下推等价与停止证书同样**未评估**） |

**本卡的硬约束**：任何后续数字必须写明「假定分支 R」还是「假定分支 F」；**不得**用「≤30 ms」这类
自设值代表证书成本，也不得把分支 R 的 ≈0.35 s 当作已经实测的 30 万结论。

## 4. P95 口径说明（为什么分量相加得不到 P95）

分量的**样本量、分布、会话状态与口径都不同**，所以「把中位相加」既不是 P95，也不是严格的数学下界：

| 分量 | 样本量 | 口径/来源 |
|---|---:|---|
| A3 单次引擎 pass | 5 | 同一会话、暖缓存、替身编码器、诊断性无约束查询 |
| pending 补页 / 残差 | 2 | P4-02F refined 一次采样 |
| 文本编码 | 20 | P0-06、RTX 4060、batch=1、52 token（**非** 30 万端到端，也非最低配置） |
| HTTP 增量 | 5 | **800 资产**小夹具 pilot |
| 回退时延 | 5 / 7 | 另一会话、另一形状（见 §5） |

另外三项使「相加」在方法上不成立：**口径重叠**（产品阶段视图与 sqlite/引擎桶重叠，§1 已声明）；
**串并行关系**（编码与检索、pending 补页与引擎扫描是否并行、是否可重叠，本卡未测）；
**测量漂移**（P4-02F §15 记录：同一对全覆盖 root 上 10 万 service 中位在 1 686.1 → 2 445.0 ms、
30 万在 8 804.6 → 5 353.3 ms 之间浮动）。

因此**唯一合法的达标判定是「同一混合负载下的端到端 API P95」**（V3.1 §9.1 与 P4-02G §5.2 同一要求）：
RTX 4060 与 GTX 1660 6 GB **分别**记录，缺一不得宣称最低配置 PASS。本卡给出的任何分量数字都只是
预算量级，不构成达标证据。

## 5. 回退门槛形式化

**形式化**。设回退率为 `r`（无筛选请求中因状态/窗口走完整候选块路径的比例）、A3 自身超 1 s 的比例
为 `s`，则 API P95 ≤ 1 000 ms 的**必要条件**是

```
P(回退 ∪ A3 自身 > 1s) ≤ 5%      ⇒（保守并集上界）      r + s ≤ 5%
```

该式**必要不充分**（还要求其余分量分布合格），但足以给出否决条件。

**回退时延的量级**（`test-results/p4-02j-*.json`）：

| 口径 | 中位 | P95 | 样本 |
|---|---:|---:|---:|
| 覆盖修复后同会话空闲 | 4 577.1 ms | 4 787.24 ms | 5 |
| 同会话稍后空闲 | 7 731.8 ms | 7 772.56 ms | 5 |
| 后台 drain 真重叠（overlap 112 930.7 ms） | 15 534.3 ms | 20 469.51 ms | 7 |
| （对照，仅空闲、零重叠）`--mode concurrency` | 4 849.75 ms | 4 907.97 ms | 8 |
| （背景，as-built 部分覆盖，**不是现行产品路径**） | 165 448.27 ms | 201 346.79 ms | 5 |

即：**一次回退 ≈ 4.787–7.773 s（空闲 P95）/ 20.47 s（后台重叠 P95）**，都远高于 1 s。
只要 `r` 不是远小于 5%，
`r + s ≤ 5%` 立刻失守。

**实现前不可测**（P4-02H r2 §6.2 与独立复跑 §4.2）：可测的只是**资格面**——240 组 `scope_condition`
交叉里只有 1 组合格（168 组可评估里 1 组 ≈0.6%），其余输入一律走完整路径；那是**资格面**，
**不是负载回退率**。真实会命中的回退源是「引擎有额外未确认行」（P4-02H §6 第 1/2 行），
其频率由**维护/发布与查询的并发密度**决定 ⇒ 必须由隔离实验卡在真实混合负载下报回退率与回退时延。
**不得**写成「回退率低」。

**附带发现（本卡只引用、不归因）**：投影队列未追赶（一次 3 万新资产突发）时前台搜索
**5 个样本里 4 次被拒**（`CATALOG_UNREADABLE`），无 drain 对照 12 轮 **0 次**被拒
（`p4-02j-contention-unwarmed.json` / `p4-02j-contention-catchup-control.json`）。它是 P4-02J 收口
新发现的未归因项，**不属于本卡结论**。

## 6. §12.2 选项对照表

| 选项 | 实际放弃/改变了哪条承诺 | 需要谁裁决 | 牵连的其它行与最低配置义务 |
|---|---|---|---|
| **① 改 1 s 数值**（如放宽到 5 s / 10 s） | 直接把「30 万暖机普通文字搜索 API P95 ≤ 1 s」的量化承诺换成现状值；把「初始验收目标」降级为「现状记录」 | **用户**；这是 §12.2 本体变更 ⇒ **必须走 ADR 裁决**，不能由实现方自行放宽 | 同表「第一屏图片可见 ≤2 s」连带失守（检索核心已 5.353 s）；GTX 1660 义务不变；「后台建库同时搜索 ≤1.5 s」需单列 |
| **② 改成「检索核心 ≤1 s、不含编码与 HTTP」** | 放弃原文明确写着的「**包含文本编码**」这一段；把判定从 **API** 口径降级为 **service** 口径 | 用户 + ADR | 即使这样，闸门 A 已测 7.378 s（§7），A3 在 783.321 ms 下仍无证书余量 ⇒ 该口径**只能缩小判定面，不能使现状达标**；「包含…元数据读取」需同步改写 |
| **③ 拆成两个独立指标**（编码指标 + 检索核心指标） | 放弃单一端到端承诺；必须同时冻结分量的样本量/口径与合成规则，否则会出现「分量都达标、端到端不达标」 | 用户 + ADR | 当前 37.32 ms 是 RTX 4060 单机 batch=1 的 20 样本，**不是** 30 万端到端，也不是最低配置口径；两个指标都要补 GTX 1660 记录 |
| **④ 仅记录现状并标未达标** | **没有放弃任何承诺**：§12.2 自身已要求「未达目标时记录限制并作明确性能决策」，故**不是** ADR 变更，也不是放宽门槛 | 用户（记录口径即可，无需改 §12.2） | 必须同时给 GTX 1660 6 GB 最低配置义务一个明确结论（**当前 BLOCKED**）；「后台建库同时搜索 ≤1.5 s」是独立指标，实测 20 469.5 ms，需单列未达标 |

## 7. 结论（三选一；**不是 PASS**）

**落点：①「可行但余量窄」**——A3 在**分支 F** 下有望进入 1 s，但没有余量容纳任何一项变慢；
若证书只能做成**分支 R**，结论立即翻转为 ③「与 §12.2 互斥 / 维持未达标」。

**前提（全部必须同时成立）**：

1. **P1 证书必须是分支 F**：代级/结构性判断，30 万档端到端成本 **≪30 ms**。当前无实现、无基准，
   成本未评估（§3）。
2. **P2 引擎侧 P95 要接近中位**：装配用的是 5 样本中位 346.961 ms；若真实 P95 落在观测高值
   476.547 ms 附近，点估计余量从 157.229 ms 掉到 **27.643 ms**。
3. **P3 pending 补页保持 359 ms 且不劣化**；它本身是未优化路径（30 万资产上的相关子查询 +
   `ORDER BY`），其优化空间属另一张卡的工程决策，不是本卡可顺手收的常数项。
4. **P4 编码 + HTTP 增量 ≤ 约 60 ms**：实测 37.32（RTX 4060、非最低配置）+ 22.13（800 档夹具），
   两项都不是 30 万端到端口径。
5. **P5 回退率 `r` 与 A3 自身超 1 s 比例 `s` 满足 `r + s ≤ 5%`**；`r` 实现前不可测，且一次回退
   是 4.787–7.773 s（空闲）/ 20.47 s（重叠）。

**最小反例 / 最小反驳条件（任一条成立即翻转结论）**：

- **R-1（翻转 ①→③）**：在 30 万档实测「一次行级 M1 核对」≈0.35 s 量级（当前只有 20 000 行 dim=2
  合成表上的 36.4–37.6 ms 与量级外推），则装配 ≥ 783.321 + 350 ≈ **1 133 ms > 1 000 ms**，
  **无需任何其它假设**即可否决。
- **R-2（翻转 ①→②/③）**：A3 引擎侧在负载下的 **P95** 落在观测高值附近（≥476.5 ms），
  则 972.357 ms 的余量只剩 27.6 ms，**任何**证书实现都无法容纳。
- **R-3（否决 P5）**：若真实混合负载下回退率 `r > 5%`，P95 由回退样本主导（一次回退 ≥4.787 s），
  API P95 ≤ 1 s 直接不成立。
- **R-4（否决 P3）**：若 pending 补页在 A3 形状下无法保持 359 ms（例如与全局 Top-K 串行且需重复
  权威复读），则预算从第一项起就不成立。

**本卡的明确边界**：以上**不授权** A3、**不修改** §12.2、**不授予** P4-02F 性能 PASS。若用户选择
④「仅记录现状」，本卡不构成任何新增承诺；若用户选择授权 A3 或修改 §12.2，按卡面路由必须补
**C2 / GPT-5.6 Sol / high 聚焦终审**（不得记 `SOL_NOT_REQUIRED`）。

## 8. 与 P4-02F §14 的差异表

P4-02F §14 给出的两个数字是 **813.46 ms**（分量下界，不含编码与 HTTP）与 **850.78 ms**
（= 813.46 + 37.32，加上真实文本编码 p95）。逐条修正如下：

| # | 项 | P4-02F §14 | 本卡 | 数值原因 | 是否改变结论 |
|---|---|---|---|---|---|
| 1 | 互斥装配和 | **813.46 ms** | **783.321 ms** | §14 在互斥残差 `77.348` 之外又加了 `authoritative_page` 28.717 与 fts/RRF/scope/verified ≈1.038 ⇒ **重复计入 29.783 ms**（按 §14 自己四舍五入的操作数复算为 813.46，重复计入 **30.139 ms**） | **否**（方向不变） |
| 2 | 含真实文本编码 | **850.78 ms** | **820.641 ms** | 同一重复计入；编码 p95 37.32 不变 | 否 |
| 3 | HTTP 增量 | 未列 | **+22.13 ms** | §14 未列 HTTP；本卡补上，并标注它来自 **800 资产** pilot、5 样本、30 万档未测 | 否 |
| 4 | 观测高值口径 | 未给 | **912.907 / 972.357 ms** | §14 只用引擎中位 346.961；本卡补上引擎 max 476.547 | 否，但把余量从 157.229 ms 压到 **27.643 ms** |
| 5 | 与 1 s 的余量 | 「约 0.19 s」（P4-02F §14 原文引用，见脚本 `quotations`） | **157.229 ms**（点估计）/ **27.643 ms**（观测高值） | 由 #1 与 #4 合成 | 否 |
| 6 | §14 的复算精度 | 813.46（按 346.96/359.0/28.7/1.5/77.3 取整） | 按精确字段复算 = **813.104 ms**，差 0.356 ms | §14 用三位小数操作数 | 否 |
| 7 | 恒等式 | 未复核 | `sqlite_read_all + engine_total + other_fusion_and_authority == service.median_ms`（8/8 通过，含逐样本精确式） | 本卡新增 | 否（但使 §1 预算表自洽） |

P4-02F §15 自己已披露的漂移（同一对全覆盖 root：10 万中位 1 686.1 → 2 445.0 ms、30 万 8 804.6 →
5 353.3 ms）同样适用于本卡：本卡所有绝对值都只作为量级，不当作常量；跨会话只比比值。

## 9. 未验证 / BLOCKED / 环境事实

- **真实中文 CLIP 权重**：本机无固定权重（BLOCKED）⇒ 真实文本塔延迟（含编码）与显存**未测**；
  §12.2 的「包含文本编码」一段在本机**无法判定**。表中 37.32 ms 是 P0-06 在 RTX 4060 上的 20 样本
  p95，**不是** GTX 1660 承诺。
- **GTX 1660 6 GB 最低配置**：**BLOCKED**（本机为 RTX 4060 Laptop）；按 §12.2 与 V3.1 §9.1，
  未实测最低配置不得宣称最低配置 PASS ⇒ 任何「记录现状」的选项都必须同时给这条义务一个明确结论。
- **端到端 API P95**：仅在 800 档有 pilot（service 18.48 / HTTP 40.61 ms，5 样本）；30 万档 HTTP 层
  **未测**。
- **A3 证书成本**：分支 R 只有 20 000 行 dim=2 合成表上的相对量级；分支 F 与 M2/M3 **无实现、
  无基准**。
- **回退率与回退时延**：A3 未实现 ⇒ **实现前不可测**；现有只是静态资格面与完整路径时延。
- **冷 OS 缓存、长时 soak、40 万扩容、并发写入与索引重建交互**：未测。
- **本卡零产品代码改动 ⇒ 不需要 B 级全量**；本卡只跑只读重算 + 静态检查（见 §10）。

## 10. 验收与 Git 状态

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path
uv run --no-sync python tools/diagnostics/p4_02k_budget.py --output test-results/p4-02k-budget.json
uv run --no-sync ruff check .
git diff --check
uv lock --check
git status --short --untracked-files=all
```

- 只读重算脚本：**8/8 恒等式通过**，输出 `test-results/p4-02k-budget.json`（`.gitignore` 内，不入库），
  报告引用数字可逐条按字段路径复算。
- `ruff check .`、`git diff --check`、`uv lock --check`：见 §10 的实际执行记录（下方「执行结果」）。
- `git status` 结束时相对基线 `d6b6666` 只应多出：`docs/reports/P4-02K-budget-and-a3-decision.md`、
  `tools/diagnostics/p4_02k_budget.py`、`docs/development-status.md` 的一节，以及开工时已存在的
  他人未提交产物。**`src/**`、迁移、既有报告、ADR/companion、`§12.2`、`tests/**`、`pyproject.toml`/
  `uv.lock`、`PhotoSearch.spec`、`tools/packaging/**`、`web/**` 必须零 diff。**

### 执行结果

| 命令 | 结果 |
|---|---|
| `uv run --no-sync python tools/diagnostics/p4_02k_budget.py --output test-results/p4-02k-budget.json` | exit 0；`identities: 8/8 ok`；`P4-02K budget: test-results\p4-02k-budget.json (27027 bytes)` |
| `uv run --no-sync ruff check .` | exit 0（`All checks passed!`） |
| `git diff --check` | exit 0（无输出） |
| `uv lock --check` | exit 0（`Resolved 96 packages in 1ms`） |
| `git status --short --untracked-files=all` | 见下方「本卡结束时的 Git 状态」 |

### 本卡结束时的 Git 状态

相对基线 `d6b6666`：`src/**`、迁移、既有报告、ADR/companion、`§12.2`、`tests/**`、
`pyproject.toml`/`uv.lock`、`PhotoSearch.spec`、`tools/packaging/**`、`web/**` **零 diff**
（`git diff --stat -- src docs/decisions docs/reports tests pyproject.toml uv.lock` 为空）。

```text
 M docs/development-status.md
?? docs/decisions/ADR-0013-upgrade-run-and-profile-activation-contract.md
?? docs/decisions/contracts/p4-upgrade-contracts.v1.json
?? docs/reports/P4-02K-budget-and-a3-decision.md
?? docs/reports/P4-03A-dsh-contract-draft.md
?? docs/tasks/P4-02K.md
?? docs/tasks/P4-03A.md
?? tests/unit/test_p4_upgrade_contracts.py
?? tools/diagnostics/p4_02k_budget.py
```

其中本卡新增的是 `docs/reports/P4-02K-budget-and-a3-decision.md`、`tools/diagnostics/p4_02k_budget.py`
与 `docs/development-status.md` 的一节；`ADR-0013-*`、`p4-upgrade-contracts.v1.json`、
`P4-03A-dsh-contract-draft.md`、`docs/tasks/P4-03A.md`、`tests/unit/test_p4_upgrade_contracts.py`
与 `docs/tasks/P4-02K.md` 是**开工时已存在**的他人产物（P4-03A 阶段 A 会话与本卡立卡），原样保留。
`test-results/p4-02k-budget.json` 已被 `.gitignore:15 test-results/` 忽略，**不入库**。

## 11. 审查路由

- 执行：**DS-H**（本卡为只读分析与只读工具，未改产品行为）。
- 独立审查：**独立 DS-H 容量证据专项审查**（V3.1 §2.4「仅新增测量工具/夹具/报告且不改产品行为」）：
  核每条数字的字段级可回溯性、恒等式、P95 口径、证书两分支表述与未验证项。本卡若越界改了产品
  行为 ⇒ 升级为**完整受影响面主审**并 FAIL。
- **Sol 是否必选**：本卡结论为「记录现状 + 余量与未达标」，**不支撑授权 A3、不改 §12.2**
  ⇒ 可记 `SOL_NOT_REQUIRED`；但若用户据本卡决定**授权 A3** 或**修改 §12.2**，则
  **C2 / GPT-5.6 Sol / high 聚焦终审必选**，不得记 `SOL_NOT_REQUIRED`（P4-02F 卡面已冻结这条）。
- 模型切换由用户在 Codex 中手动完成；本卡与 P4-03A/03B **互不阻塞**（P4-03A 卡不承担 §12.2，
  也不承担 P4-02J 收口的写锁拒绝归因）。

## 12. 下一任务

由本卡结论与用户裁决决定（接受现状 / 走 A3 / 改口径），本卡**不预生成**下一张卡。
