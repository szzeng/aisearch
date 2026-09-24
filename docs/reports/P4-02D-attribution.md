# P4-02D — F3 同规模检索时延受控归因（纯测量）

**结论：`F3 归因完成：主因是 asset_id 标量索引的覆盖率（产品「建一次不再重建」的语义）` +
`实测第二个反向小变量：等覆盖下两种建库形状的索引路径仍差约 2 倍（未定位到物理层，已列出排除项与下一最小变量）`
+ `本卡不产生协议/ADR 裁决 ⇒ SOL_NOT_REQUIRED`。**

本卡只做测量，`src/**`、migration、`SUPPORTED_SCHEMA_VERSION`、ADR/companion、信封/错误码、
检索语义、打包面、`pyproject.toml`、`uv.lock` 全部 **零 diff**。

---

## 1. 口径与冻结变量

| 项目 | 值 |
|---|---|
| 种子 | `--seed 20260924`（两条 data root 相同） |
| 夹具 | `capacity_validation.build_library`，`320x240`，100 000 张，内容 sha256 各不相同 |
| 替身编码器 | `ann_scale_benchmark.DeterministicEncoder` + 确定性向量（**无真实权重**） |
| 查询 | `西湖 sunset 全家福`，`top_k=20` |
| 空间/代 | `chinese-clip-vit-b-16:c8ac42d3907c57b0` / `image-gen-5bcba729dfda4897a94974b7` |
| A（突发） | `--burst-sample 100000 --publish-wave 100000`（一次性投递，一次 drain） |
| B（波次） | `--burst-sample 0 --publish-wave 5000`（20 波，每波 drain 完再发下一波） |
| 其余建库参数 | 完全相同：`--scales 100000 --no-queries --skip-thumbnails --skip-injections --keep` |

两条 root 只有**写入形状**一个变量；`--no-queries/--skip-thumbnails/--skip-injections` 对两条完全相同
（本卡测的是向量通道的候选块路径，不读缩略图，也不属于 P4-02B 的 D 级注入集）。执行顺序：
先建 A，再建 B（B 首轮与 A 并行，发现工具字段缺陷后 B 单独重跑，重跑数字与首轮一致，见 §3）。

**机器**：Windows 11 26200、Python 3.12.10、32 逻辑核、31.7 GiB、LanceDB 0.38.0、pyarrow 25.0.1、
RTX 4060 Laptop 8188 MiB（**非 GTX 1660**）。采样期间无其他负载；两个 root 都在同一会话内先建后测，
因此对两者都是暖缓存，A/B/B/A 交替本身就是对「单调漂移」的对照。

## 2. 命令（实际执行，工具名与参数按卡面替换）

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path

# 1) 两形状建库（除写入形状外参数完全相同）
uv run --no-sync python tools/diagnostics/capacity_validation.py --scales 100000 `
  --burst-sample 0 --publish-wave 5000 --no-queries --skip-thumbnails --skip-injections --keep `
  --label wave --work-dir "tmp/p402d 归因 验证" --output test-results/p4-02d-wave.json
uv run --no-sync python tools/diagnostics/capacity_validation.py --scales 100000 `
  --burst-sample 100000 --publish-wave 100000 --no-queries --skip-thumbnails --skip-injections --keep `
  --label burst --work-dir "tmp/p402d 归因 验证" --output test-results/p4-02d-burst.json

# 2) A/B/B/A 交替采样（≥20 样本/轮）+ 分阶段计时 + 索引覆盖对照
uv run --no-sync python tools/diagnostics/p4_02d_attribution.py `
  --burst-root "tmp/p402d 归因 验证/scale-100000-burst/data" `
  --wave-root  "tmp/p402d 归因 验证/scale-100000-wave/data" `
  --order A,B,B,A --samples 20 --output test-results/p4-02d-attribution.json

# 3) 等覆盖（两条都 100%）复采样，隔离第二个变量
uv run --no-sync python tools/diagnostics/p4_02d_attribution.py `
  --burst-root "tmp/p402d 归因 验证/scale-100000-burst/data" `
  --wave-root  "tmp/p402d 归因 验证/scale-100000-wave/data" `
  --order B,A,A,B --samples 20 --no-rebuild `
  --output test-results/p4-02d-attribution-postrebuild.json

# 4) 既有业务套件（受限令牌下 --basetemp 会 WinError 5，走正常权限路由重跑，未改 ACL）
uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration `
  --basetemp=.pytest-basetemp-p402d/full --junitxml=test-results/p4-02d-full.xml
uv run ruff check .; git diff --check; uv lock --check
```

`capacity_validation.py` 本卡只新增 `--label`（让同规模的两种形状共存在同一 `--work-dir` 下），
不改变任何既有行为；其余测量全部由新工具 `tools/diagnostics/p4_02d_attribution.py` 完成。
一次性探针（`tmp/p402d/*.py`）与可丢弃 data root 在 `tmp/` 内，`tmp/` 被 `.gitignore` 忽略。

## 3. 两条 root 的构建结果（同夹具）

| 项 | A（突发） | B（波次） |
|---|---|---|
| 建库 fixture | 113.5 s | 119.8 s |
| scan | 296 548.9 ms（337.2 件/秒） | 310 227.9 ms（322.3 件/秒） |
| catalog catch-up | 5 399.0 ms | 8 131.2 ms |
| semantic execute | 2 842 613.8 ms | 1 079 210.5 ms |
| drain 合计 | 100 000 事件 / 161 897.5 ms | 100 000 事件 / 175 060.0 ms |
| 终态 | `entries=CONFIRMED 100000`、`semantic_ready=100000`、`pending=0` | 同左 |
| 向量目录 | 224 920 158 B | 224 304 970 B |

（B 首轮与 A 并行构建时 execute 1 076 208.3 ms、scan 310 255.9 ms，与上表单独重跑的数字一致；
该首轮 JSON 已被本卡修正版工具的输出覆盖。）

## 4. 同一检索的证明

| 证据 | A | B |
|---|---|---|
| assets 数 / min / max | 100 000 / 1 / 100 000 | 100 000 / 1 / 100 000 |
| assets 列表 sha256 | `9674ad7df14fb57d38cc9574128085af7435c0a22bed8bdb1bd4041b7377436c` | 同左 |
| `index_entries` sha256（asset_id/generation/artifact/confirmation_revision/state） | `b693588489749c7f9faeac1c79618a3e04b56ebd675bddde37dbca02dc59b085` | 同左 |
| 64 个均匀散布 id 的实存向量距离 | 见 `vector_probe` | **逐位相同**（`vector_probe_identical=true`） |
| 全部 84 次采样返回页 | `page_sha256=25ac38b4aecc…` | **同一个 hash**（`distinct_pages_across_all_rounds=1`） |

Top-20（两条 root、四轮、每次采样都相同）：

```
35104 1.629789  62188 1.630332  37205 1.639065  59600 1.645336  80519 1.645695
90261 1.654314  49692 1.655125  36862 1.659053   2145 1.661074  21864 1.667139
95849 1.669714  32495 1.669969  22877 1.670166  32990 1.670795  69365 1.675232
88695 1.675499  73173 1.675732   5225 1.676522  65524 1.678281  38246 1.679211
```

语义上与 P4-02B 的同查询页比对：`test-results/p4-02b-*.json` **只保存聚合时延，不保存命中页**，
因此「同一检索」由本卡自证（同夹具摘要 + 64 点向量 + 84 次采样同页），并用聚合量级与 P4-02B 对照：
P4-02B 两条 root 的向量目录 224 459 788 B / 224 920 154 B，本卡 224 304 970 B / 224 920 158 B。

## 5. 布局证据：两条 root 唯一的状态差异是索引覆盖率（F4 变量）

| | A（突发） | B（波次） |
|---|---|---|
| 行数 / Lance 表 version（采样时） | 100 000 / 503 | 100 000 / 502 |
| fragment 文件数 / 单块字节 | 500 / 423 061–423 125 B | 500 / 422 869–423 125 B |
| fragment 总字节 | 211 531 012 B | 211 551 300 B |
| `asset_id_idx` 类型 / 段数 | BTree / 1 | BTree / 1 |
| `num_indexed_rows` / `num_unindexed_rows` | **100 000 / 0** | **5 000 / 95 000** |
| 覆盖率 / 索引文件大小 | **100%** / 718 006 B | **5%** / 33 513 B |

根因读代码即可确认：`IndexCoordinator.process_all` 在每批 drain 前调用 `ensure_scalar_indexes`，
而 `ensure_scalar_index` 只要目录里已存在 `asset_id_idx` 就返回 `present`，**从不重建**。
波次形状在第 2 波 drain 时（表内只有 5 000 行）建索引，之后 19 波追加的行全部落在未索引 fragment 上；
突发形状的索引由建库末尾的 `drain --limit 0` 空跑在**完整表**上建立，因此 100% 覆盖。
（行数、fragment 数、fragment 字节、表 version 都相同 ⇒ 4.5 节 DS-H 指出的候选变量在本卡被单独量化。）

**披露**：A 采样时的索引 UUID `11ae7e14…` 是本卡诊断 `replace` 对「产品建立的全覆盖索引」做的一次
full→full 重建（产品原 UUID `582c80cb…`，两者同为 718 006 B；`test-results/p4-02d-index-path.json`
记录 as-built 150.9 / replace 145.7 / create 146.8 ms·block⁻¹，差异在噪声内）。B 采样时的 UUID
`97e8ccff…` 是产品建立的**部分覆盖**索引本身，`replace` 只发生在采样之后的对照步骤。

## 6. A/B/B/A 受控采样（20 样本/轮，共 84 次查询 + 2 次暖机）

| 轮 | root | 中位 | P95 | min | max |
|---:|---|---:|---:|---:|---:|
| 1 | A 突发 | 3 760.5 ms | 4 396.5 ms | 3 632.5 ms | 4 410.6 ms |
| 2 | B 波次 | 15 085.7 ms | 15 341.7 ms | 9 395.1 ms | 15 429.8 ms |
| 3 | B 波次 | 15 024.6 ms | 15 270.9 ms | 13 319.6 ms | 15 335.5 ms |
| 4 | A 突发 | 4 427.6 ms | 4 492.3 ms | 3 200.1 ms | 4 581.5 ms |

- 轮中位比值 **B/A = 3.68**；合并样本 P50 4 200.4 → 15 055.4 ms（**3.58×**）、P95 4 468.5 → 15 335.6 ms（**3.43×**）。
- 同一 root 两轮之间本身有漂移：A 3 760.5 → 4 427.6 ms（+17.7%），B 15 085.7 → 15 024.6 ms（−0.4%）。
- 84 次采样返回**同一个页**（含融合后的 `hit_reason`），见 §4。

## 7. 阶段耗时拆分：差异全部落在 Lance 引擎层

每轮 20 个候选块（`SCOPE_BLOCK_SIZE=5000`，`scanned_assets=100 000`），5 样本中位数：

| 阶段 | A（全覆盖） | B（5% 覆盖） | B 重建后（100%） |
|---|---:|---:|---:|
| SQLite `read_all` 合计 | 290.8 ms | 281.3 ms | 278.1 ms |
| └ 其中候选 walk（`FROM index_entries … CONFIRMED`） | 283.8 ms | 279.5 ms | 276.0 ms |
| **Lance 引擎合计（20 次调用）** | **4 232.2 ms** | **14 518.4 ms** | **2 120.0 ms** |
| └ `_table()` 固定开销（20 次） | 139.5 ms | 139.0 ms | 131.5 ms |
| 其余（FTS/融合/渲染/校验） | 39.2 ms | 39.3 ms | 37.4 ms |
| **service 中位** | **4 596.0 ms** | **14 897.3 ms** | **2 444.7 ms** |
| 每块引擎耗时（中位） | 206.3 ms | 723.1 ms | 102.7 ms |
| 每次查询进程读盘 | 480.5 MB | 3 852.4 MB | 278.8 MB |
| 每次查询 CPU 时间 | 21.3 s | 40.7 s | 3.5 s |

（读盘为 `GetProcessIoCounters` 的传输字节，5 样本合计的精确值：A 2 519 402 480 B；
B 部分覆盖 20 197 802 465 B；B 重建后 1 461 888 980 B。工作集峰值按轮记录，本进程为
948–951 MB（进程生命周期高水位，只增不减，故按轮不重复变化）。）

**关键证据（逐块）**：B 的 20 个块耗时几乎完全相同——包含**索引确实覆盖的那一块**
（ids 1..5000）：block#1 中位 730.2 ms（min 426.4 / max 836.6），block#2..20 中位 721.3 ms。
A 的 block#1 中位 185.5 ms。⇒ **部分覆盖的标量索引不是「只慢一点」，而是完全不生效**：
每个候选块都退化成对整代的过滤扫描。

字节核算与之吻合：B 每次查询 3 852 MB ≈ 20 块 × 211.55 MB（整代 fragment 总字节）× 0.91；
A 480 MB、B 重建后 279 MB 只有它的 1/8–1/14，即大部分候选块不再读整代。

## 8. F4 单变量对照：只把索引覆盖率从 5% 改到 100%

在同一张 Lance 表上做诊断 `replace` 重建（**只为归因，不是产品维护路径**，产品修法属 P4-02F）：

| | B（波次，部分 → 全） | A（突发，全 → 全，对照） |
|---|---|---|
| 覆盖率 | `5000/95000` → `100000/0` | `100000/0` → `100000/0` |
| 索引字节 | 33 513 → 707 126 | 718 006 → 718 006 |
| 表 version | 502 → 503 | 503 → 504 |
| 重建耗时 | 70.6 ms | 69.9 ms |
| service 中位 | **14 897.3 → 2 444.7 ms（6.09×）** | 4 596.0 → 4 366.0 ms（−5.0%，噪声） |
| 引擎合计 | 14 518.4 → 2 120.0 ms | 4 232.2 → 4 049.8 ms |
| 每块引擎耗时 | 723.1 → 102.7 ms | 206.3 → 198.7 ms |
| 返回页 | **完全相同** | **完全相同** |

同一张表、同一份数据、同一次查询、同一个返回页，只改索引覆盖率即把成本移动 6 倍；对已全覆盖的
A 做同样重建则没有变化。这是「覆盖率 → 候选块成本」的单变量因果证据。

## 9. 第二个变量：等覆盖下两种建库形状的索引路径仍差约 2 倍

`replace` 之后两条 root 都是 100% 覆盖，但 B 反而**比 A 快**。等覆盖复采样
（`--order B,A,A,B`，20 样本/轮，`test-results/p4-02d-attribution-postrebuild.json`）：

| 轮 | root | 中位 | P95 |
|---:|---|---:|---:|
| 1 | B 波次 | 2 351.0 ms | 2 382.6 ms |
| 2 | A 突发 | 4 474.9 ms | 4 531.2 ms |
| 3 | A 突发 | 3 592.4 ms | 3 676.4 ms |
| 4 | B 波次 | 1 493.8 ms | 1 788.0 ms |

合并 P50 4 052.5（A）vs 2 053.6（B）→ **A/B = 0.507**（等覆盖下 B 快约 2 倍），返回页与
§4 仍逐位相同。该比值用 `test-results/p4-02d-candidate-shapes.json` 的对称协议（两条 root 都
先 `drop` 再 `create`，消除「索引实例」这个混杂变量）复核：

| 候选形状 | A 突发 | B 波次 | A/B |
|---|---:|---:|---:|
| 200 个连续 id（约 1 个 fragment） | 39.2 ms | 26.5 ms | 1.48 |
| 5 000 个连续 id（产品实际块） | 163.3 ms | 86.9 ms | **1.88** |
| 5 000 个散布 id（触及全部 fragment） | 156.8 ms | 153.8 ms | 1.02 |
| 100 000 个 id（整代一次过滤） | 2 532.6 ms | 2 528.3 ms | 1.00 |
| 5 000 连续 id，**删掉索引** | 904.9 ms | 845.9 ms | 1.07 |

`test-results/p4-02d-residual.json` 同时给出：整代无过滤 pass 反而 **A 更快**（50.6 vs 76.2 ms），
而「每块」路径 A 慢 2.01×；fragment 文件数、单文件字节、整代字节都相同。

⇒ 已定位：差异只在**「小规模连续候选集 + 标量索引」这条路径**上；一旦候选集覆盖全部 fragment
（散布/整代）或删掉索引（纯扫描），两条 root 就一致。**未定位**其物理原因，因此本卡不把它写成
「Lance 布局敏感」的结论。

**对 F3 的量化贡献**：本卡受控对里覆盖率贡献约 **+9.0 s/次查询**（B 12.45 s→2.44 s 中的主体），
第二个变量以**相反方向**贡献约 **−2.0 s**（等覆盖下 B 更快）；两者相加与实测 +7.3 s 吻合。
即观测差异由这两个方向相反的变量叠加而成，覆盖率是主导项。

## 10. 与 P4-02B 原始 F3 数字（3 484.4 vs 8 317.8 ms，2.39×）的差异说明

本卡受控对得到 3.68×（合并 P50 3.58×），比 P4-02B 的 2.39× 更大。**机制相同，倍数受环境影响**：

1. **P4-02B 的那一对不是受控对，但落在同一机制上**。其 10 万波次档用的是工具默认
   `--burst-sample 20000`（`test-results/p4-02b-capacity.json`：`wave_size=5000, burst_sample=20000`），
   即波次档前面还有一个 2 万件突发；索引因此在**表内 2 万行**时建立，覆盖率 20%，而本卡归一化后是
   5%。两者都是「部分覆盖」，按 §7 的逐块证据，部分覆盖的索引对**任何**块都不生效、每块都退化为整代
   过滤扫描，因此**覆盖率是 5% 还是 20% 并不改变代价**——差的只是这一对原本就混进了第二个变量
   （突发档 `--burst-sample 100000`）。原始 data root 未保留，无法重测其覆盖率，这一点只能作为
   记录差异、不能作为已证的量化结论。
2. **同一确定性工作量在不同会话的墙钟差异很大，且对慢的一侧放大更多**。本卡等覆盖复采样里，同一
   root 同一查询在第 1 轮与第 4 轮相差最多 36%（B 2 351.0 → 1 493.8 ms）；而进程读盘字节在同一
   root/同一覆盖下是可复现的结构性数字（部分覆盖 ≈3.85 GB/查询、全覆盖 ≈0.28–0.48 GB/查询）。
   慢的那一侧（部分覆盖）每次查询要多读近一个数量级的字节，因此对 OS 缓存/磁盘状态更敏感，
   会话间的倍数因此在 2.39×–3.68× 之间浮动。本卡据此按「阶段拆分 + 布局 + 字节」下结论，
   不把某一次倍数当成常量。

## 11. 已排除 / 未排除 / 下一轮最小变量

**已排除（有直接测量）**

- SQLite 侧：`read_all` 合计与候选 keyset walk 在两种形状、两种覆盖下都是 276–291 ms，无差异。
- 候选集内容：两条 root 的候选数、块数、扫描资产数、返回页、64 点向量全部相同。
- fragment 布局：fragment 文件数（500）、单文件字节、整代字节、表 version 量级相同。
- 索引是否存在被使用：删掉索引后 5 000 连续块的耗时从 163.3→904.9 ms（A）/86.9→845.9 ms（B），
  证明正常路径确实走索引。
- 索引「实例/来源」：`test-results/p4-02d-index-path.json` 在突发副本上 as-built 150.9 / replace 145.7 /
  create 146.8 ms·block⁻¹，以及本卡对称协议（两条 root 都 drop+create）都复现同一差距。
- 整代扫描路径：无过滤 pass 与「删索引后的 5 000 连续块」两条 root 都在 7% 以内。
- 结果集与融合/渲染：`other` 阶段 37–39 ms，两者无差异。

**未排除 / 未定位**

- 「小连续候选集 + 索引」路径上 A 比 B 慢约 1.9× 的物理原因（索引内部页/段结构？该 5 000 id 对应
  fragment 的 row-address 分布？），4.5 节 DS-H 的 F4 只解释了覆盖率，解释不了这一项。

**下一轮最小变量（交 P4-02F 阶段 0，不在本卡范围）**

1. 在同一代上比较「索引覆盖全部 fragment」与「索引只由一个连续 id 段建立」两种 `num_indexed_rows`
   相同但 `_indices` 内部结构不同的表，读取索引页/段与命中 fragment 的对应关系（需 `pylance`；
   本机未安装，`pip install pylance` 属新依赖，不在本卡授权内）。
2. 只改**建库写入形状**、不改索引覆盖率（例如两者都在完整表上建索引），确认 1.9× 是否随形状消失。
3. 覆盖率是主因这一点不需要新变量：`replace` 单变量对照已给出 6.09× 与同页结果。

## 12. 成本、状态与停止点

- **成本说明**：本卡按卡面首选用与差异同规模的 **10 万**档完成，未使用 3 万档预筛；
  另外用 10 000 档做了机制预筛（`test-results/p4-02d-mechanism-10k.json`：同一代 partial→full
  190.9→134.2 ms，同页，1.42×），它**只是预筛**，不能代替 10 万结论。
- **验收**：B 级不回归 `tests/unit + tests/integration` = **1682 / 0 / 0 / 48**（48 为既有环境跳过），
  JUnit 见 `test-results/p4-02d-full.xml`；`ruff check .`、`git diff --check`、`uv lock --check` 全部 exit 0。
- **未验证**：真实中文 CLIP 权重/吞吐/显存、含真实文本塔的完整 API P95、GTX 1660 最低配置、
  ANN Recall 0.95、冷 OS 缓存与长时 soak —— 均沿用 P4-02B 的 `BLOCKED`，本卡未测；本卡也**不授予**
  V3.1 §12.2 达标结论（本卡是归因，不是达标验证）。
- **停止点**：未命中 V3.1 §2.2 六类条件，不升 DS-MAX；未出现不可解释的 Windows/系统级异常，
  无需转 C1；本卡未改产品代码即完成归因，无需交回。
- **路由**：独立审查 = **DS-H 有界专项复核**（审本卡新增工具/探针/报告 diff、两 root 是否同夹具、
  交替顺序与索引覆盖是否被记录）；一旦改动 `src/**` 或测量绕开产品入口即扩大为完整受影响面主审。
  `SOL_NOT_REQUIRED`（无协议/ADR 裁决）。

## 13. 真实 Git 状态与产物

- 基线：`b32d33a`（HEAD，P4-02B Sol 审查）/ `0808c64`（P4-02B 实现）。
- 本卡新增：`tools/diagnostics/p4_02d_attribution.py`；修改 `tools/diagnostics/capacity_validation.py`
  （仅新增 `--label`）；本报告；`docs/development-status.md` 条目。
- 机器可读证据（`test-results/`）：`p4-02d-wave.json`、`p4-02d-burst.json`、`p4-02d-attribution.json`、
  `p4-02d-attribution-postrebuild.json`、`p4-02d-mechanism-10k.json`、`p4-02d-residual.json`、
  `p4-02d-candidate-shapes.json`、`p4-02d-index-path.json`（探索性，含被本卡重跑替换的旧 root）、
  `p4-02d-full.xml`。
- `tmp/` 内探针：`tmp/p402d/mechanism_probe.py`、`tmp/p402d/residual_probe.py`、
  `tmp/p402d/fragment_mapping_probe.py`、`tmp/p402d/index_path_probe.py`、`tmp/p402d/probe_lance_api.py`；
  可丢弃 data root 在 `tmp/p402d 归因 验证/`、`tmp/p402d 归因/`（`tmp/` 被忽略，不随提交入库）。
- 受限令牌下 `--basetemp` 的 `WinError 5` 已按正常 Windows 权限路由重跑，**未修改任何 ACL**。
