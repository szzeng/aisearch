# P4-02D DS-H 有界专项复核

日期：2026-09-24

基线：`9089799`（P4-02D 实现，父提交 `5c158b5`）；被审产物：`tools/diagnostics/p4_02d_attribution.py`（新增）、
`tools/diagnostics/capacity_validation.py`（只新增 `--label`）、[P4-02D 报告](P4-02D-attribution.md)、
`test-results/p4-02d-*.json|xml`、`tmp/p402d/*.py` 探针

性质：**有界专项复核**（按 [P4-02D 任务卡 §审查路由](../tasks/P4-02D.md)：只审本卡新增工具/探针/报告的完整
diff 与机器可读证据，核对「只改测量面 / 两 root 同夹具 / 交替顺序受控 / 索引覆盖被记录」）。本轮未改
产品代码、未改被审工具、未改被审报告；只新增本复核报告、两个复核探针/复跑 JSON 与状态条目。

## 1. 结论

**`BOUNDED REVIEW PASS`**：变更面只含测量工具与文档；两条 data root 确为同一夹具、同一检索；
交替顺序与索引覆盖都被机器可读地记录，且报告的关键结论可由证据复算。**3 条非阻断文档/数字修正建议**
（§7）不改变任何结论。未触发「改 `src/**` 或测量绕开产品入口」的扩审条件，因此**不扩大为完整受影响面主审**；
本卡维持 `SOL_NOT_REQUIRED`。

| 指定核对项 | 结论 |
|---|---|
| 只审新增工具/探针/报告 | 通过：`git diff 5c158b5 9089799` 只有 4 个文件（`docs/development-status.md`、报告、两个 `tools/diagnostics/` 文件），`src/**`、`0001`～`0015`、`SUPPORTED_SCHEMA_VERSION`、ADR/companion、信封/错误码、打包面、`pyproject.toml`、`uv.lock` **零 diff** |
| 两 root 是否同夹具 | 通过：同 seed、同夹具字节、同 assets 摘要、同 `index_entries` 摘要、64 点实存向量逐位相同、84 次服务调用的向量页与融合页唯一（§4.2） |
| 交替顺序是否受控 | 通过（有一处局限已披露）：`order` 被写入 JSON 并被校验，主对为 `A,B,B,A`、复采样为 `B,A,A,B`，每轮各有暖机与 20 样本；同 root 两轮漂移（+17.7%/−0.4%）远小于效应量（3.6×）（§4.3） |
| 覆盖是否被记录 | 通过：`layout_before`/`layout_after` 含 `num_indexed_rows`/`num_unindexed_rows`/`covered_fraction`/`size_bytes`/`index_uuid`，另有 `f4_contrast` 的 before/after；机制在 `src/photo_search/indexing/store.py` 中被我独立读实（§4.4） |

## 2. 变更面核对

```
M docs/development-status.md
A docs/reports/P4-02D-attribution.md
M tools/diagnostics/capacity_validation.py   (+27 −2)
A tools/diagnostics/p4_02d_attribution.py    (+639)
```

`capacity_validation.py` 的改动逐行读过：新增 `scale_directory()` 与 `--label`，只把每个 scale 的
目录名由 `scale-<n>` 改成 `scale-<n>-<label>`（`injection_fixture` 同理），**不参与夹具、种子、查询、
写入形状或任何计时口径**；未传 `--label` 时行为与 P4-02B 完全一致。P4-02B 的 11 条 focused 用例与
全量套件在 P4-02D 中仍为 `1682 / 0 / 0 / 48`（`test-results/p4-02d-full.xml`，我读回并核对计数）；
主审另按正常 Windows 权限路由**自跑**该 focused 文件一次，`test-results/p4-02d-dsh-focused.xml`
**11 / 0 / 0 / 0**（11.4 s）——`--label` 改动没有改变既有工具行为。`git show --check 9089799` exit 0。

## 3. 新工具审计（`tools/diagnostics/p4_02d_attribution.py`）

逐段读完 639 行后的判定：

- **走产品入口**：查询经 `photo_search.search.service.search_assets`；
  `search/service.py:284` 显示 `vector_index` 缺省时用产品自己的 `default_vector_index(catalog)`，
  本工具传入的 `TimedIndex` 只是把 `search_candidates`/`_table` 计时后**原样委托**给真实
  `LanceVectorIndex`；SQLite 侧通过实例级包装 `catalog.read_all` 计时（并用 SQL 特征拆出候选 keyset walk）。
  没有平行复刻查询、没有改产品代码。
- **唯一的状态变化**：`replace_asset_index()` 的 `create_index(asset_id, BTree, replace=True)`，
  只作用于 `tmp/` 下可丢弃 root，工具 docstring 与报告 §5 都显式披露；且**两条 root 都做了**
  （A 为 full→full 对照），是单变量对照的正确做法。
- **卡面约束被执行**：`--samples < 20` 直接 `SystemExit`；`--order` 只接受 A/B；
  每轮有独立暖机；`identity()`/`vector_probe()`/`layout()` 都把「同一检索」写成可核对的摘要。
- **探针（`tmp/p402d/*.py`，未入库）**：`mechanism_probe`、`residual_probe`、
  `fragment_mapping_probe`、`index_path_probe`、`probe_lance_api` 全部调用产品
  `search_candidates`/`search_assets` 与真实 Lance 索引操作，未发现硬编码或伪造数字；
  它们产出的 JSON 与报告引用逐条一致（§6）。

## 4. 四项指定核对

### 4.1 只审新增工具/探针/报告

见 §2、§3。补充两点边界核对：① `tmp/` 与 `test-results/` 都被 `.gitignore` 忽略，探针与 data root
不随提交入库（`git check-ignore` 已确认）；② 报告 §11「下一轮最小变量」提到的 `pylance` 被明确标为
**新依赖、不在本卡授权内**，没有偷偷安装。

### 4.2 两 root 同夹具（读 `p4-02d-wave.json` / `p4-02d-burst.json` + `p4-02d-attribution.json`）

| 项 | A（burst） | B（wave） |
|---|---|---|
| `--seed` | 20260924 | 20260924 |
| 夹具文件数 / 字节 | 100 000 / 323 423 372 | 100 000 / 323 423 372 |
| 夹具样本 sha256 去重 | 32 / 32 | 32 / 32 |
| 写入形状 | `wave_size=100000, burst_sample=100000, waves=1` | `wave_size=5000, burst_sample=0, waves=20` |
| 终态 | `entries=CONFIRMED 100000`、`semantic_ready=100000`、`pending=0` | 同左 |
| `scalar_index.state`（建库报告） | `created` | `present` |
| `assets` 摘要 sha256 | `9674ad7d…7436c` | **同左** |
| `index_entries` 摘要 sha256 | `b6935884…b085` | **同左** |
| 64 点实存向量距离 | 见 `vector_probe.A` | **逐位相同**（`vector_probe_identical=true`） |

两条 root 只有写入形状一个变量；夹具内容、资产集合、确认集合与向量都相同。

### 4.3 交替顺序是否受控

- 主对：`order=["A","B","B","A"]`、`samples_per_round=20`，每轮先暖机再采 20 次（JSON `rounds[].warmup_service_ms`）；
  逐轮中位 3 760.5 / 15 085.7 / 15 024.6 / 4 427.6 ms。
- 复采样（等覆盖）：`order=["B","A","A","B"]`，逐轮中位 2 351.0 / 4 474.9 / 3 592.4 / 1 493.8 ms。
- 漂移对照：同一 root 两轮之间 A +17.7%（3 760.5→4 427.6）、B −0.4%（15 085.7→15 024.6），
  远小于 A/B 之间的 3.6× 效应 ⇒「单调漂移冒充 root 效应」的解释被排除。
- **已披露局限**（我认可披露充分，非缺陷）：交替粒度是「每轮 20 次」，不是逐样本交替；
  一轮 B 约 5 分钟，期间若有缓慢漂移会部分混入。报告 §10 已用「等覆盖复采样里同 root 两轮最多差 36%」
  自行标注该不确定性，并据此不把某一次倍数当常量。

### 4.4 覆盖是否被记录

- JSON 字段：`layout_before/after[<root>].indices[].{num_indexed_rows,num_unindexed_rows,covered_fraction,size_bytes,index_uuid}`
  + `fragment_files`/`fragment_bytes`；主对读到 **A=`100000/0`（100%）**、**B=`5000/95000`（5%）**，
  复采样后两条都是 `100000/0`。
- `f4_contrast` 在同一张表上记录 before/after 覆盖、重建耗时（A 69.9 ms / B 70.6 ms）、
  before/after 服务中位与 `page_identical`。
- **机制在代码中被我独立读实**：`IndexCoordinator.process_all`（`store.py:640-655`）在循环**之前**
  调 `ensure_scalar_indexes()`；`ensure_scalar_index`（`store.py:214-245`）见到同名索引即返回
  `present`、空表返回 `no_table`。因此：波次形状在第 2 波（表内 5 000 行）建索引 → 5% 覆盖率；
  突发形状在唯一一次 drain 时表尚不存在（`no_table`），建库末尾的 `drain --limit 0` 空跑才在完整表上
  建索引 → 100% 覆盖率。报告 §5 的解释与代码一致。

## 5. 主审独立复现

### 5.1 部分覆盖的「覆盖块」是否更快（主审自建 10 000 档 root）

两份 10 万 root 已被报告自己的 `replace` 覆盖，无法回到部分覆盖状态，因此我用**本会话早先自建**
的 10 000 档产品 root（`tmp/p402b-dsh 复核 验证/scale-10000`，真实部分覆盖 `5000/5000`）做同型检验
（探针 `tmp/p402b-dsh/f4_coverage_probe.py` → `test-results/p4-02d-dsh-coverage-check.json`）：

| 块 | 部分覆盖（5000/5000） | 重建后（10000/0） | 变化 |
|---|---:|---:|---:|
| 覆盖块 ids 1..5 000 | **84.638 ms** | 59.263 ms | −30.0% |
| 未覆盖块 ids 5 001..10 000 | **83.396 ms** | 58.925 ms | −29.4% |

覆盖块只比未覆盖块慢 1.5%（`covered/uncovered = 1.015`），即**部分覆盖对任何块都不生效**；
重建到 100% 后两块同时变快，且 `top20_identical_before_after=true`。这与报告 §7 的核心论断一致，
并说明它不是 10 万档特有现象。作为旁证：DS-L 的 `p4-02d-mechanism-10k.json` 里同一代
partial→full 的**两块**引擎耗时从 `90.987/83.681 → 58.598/59.433 ms`。

### 5.2 在存活的两个 10 万 root 上重跑归因工具

```
uv run --no-sync python tools/diagnostics/p4_02d_attribution.py \
  --burst-root "tmp/p402d 归因 验证/scale-100000-burst/data" \
  --wave-root  "tmp/p402d 归因 验证/scale-100000-wave/data" \
  --order A,B --samples 20 --no-rebuild --output test-results/p4-02d-dsh-rerun.json
```

- 返回页哈希 `25ac38b4aecce070e951c8abcf95b2d8bc7172bb2860ad0a6a02a303770d47e6`
  **与 DS-L 主对完全相同**；`assets`/`index_entries` 摘要、64 点向量也相同 ⇒ 同一检索可跨会话复现。
- 两条 root 现在都是 100% 覆盖（报告 §8 重建的既成状态）：A（突发）中位 3 602.6 ms、
  B（波次）中位 1 456.82 ms，**B/A = 0.404**，方向与报告 §9 的「等覆盖下 B 更快」一致
  （DS-L 得 0.507；同一 root 的会话间漂移报告已披露为最多 36%）。
- 即：报告未定位的「第二个变量」在**本会话也被独立观察到**，且我认为它在 P4-02F 里必须被当作
  已知输入，而不是被当作噪声忽略。

## 6. 报告数字与证据复算（抽核）

| 报告位置 | 报告值 | 我从 JSON 复算 | 判定 |
|---|---|---|---|
| §6 逐轮中位 / 合并 P50 / P95 | 3 760.5·15 085.7·15 024.6·4 427.6 / 4 200.4→15 055.4 / 4 468.5→15 335.6 | 一致（`rounds[].summary`、`pooled`） | ✅ |
| §6 比值 | 3.68 / 3.58 / 3.43 | 一致（`wave_over_burst_ratio`、`pooled_ratio_*`） | ✅ |
| §5 覆盖 | A `100000/0`、B `5000/95000`、索引字节 718 006 / 33 513 | 一致（`layout_before`） | ✅ |
| §7 阶段拆分（A `4 596.0`、B `14 897.3`、B 重建后 `2 444.7` ms，walk 两侧 278–291 ms） | — | 一致（`f4_contrast.before/after`）；SQLite 侧确实无差异 | ✅ |
| §7 每块引擎耗时（B `723.1`→`102.7`、A `206.3`→`198.7`） | — | 一致（`engine_block_ms_median`） | ✅ |
| §7 读盘（A 480.5 / B 3 852.4 / 重建后 278.8 MiB 每查询） | — | 一致（`disk_read_bytes_total` ÷ 样本数） | ✅ |
| §8 F4 单变量 | `14 897.3 → 2 444.7 ms（6.09×）`、页相同 | 一致（`f4_contrast.B`、`page_identical=true`） | ✅ |
| §9 等覆盖复采样 | 2 351.0 / 4 474.9 / 3 592.4 / 1 493.8，P50 4 052.5 vs 2 053.6，`A/B=0.507` | 一致（`postrebuild`） | ✅ |
| §9 候选形状表 | 1.48 / 1.88 / 1.02 / 1.00 / 1.07 | 一致（`candidate-shapes.json`） | ✅ |
| §9 残余 | 整代 pass A 50.6 vs B 76.2；每块 A 慢 2.01× | 一致（`residual.json`：50.598/76.228，199.294/98.948） | ✅ |
| §10 P4-02B 原始对的形状 | 10 万波次档 `--burst-sample 20000` | 一致（`p4-02b-capacity.json`: `wave_size=5000, burst_sample=20000, waves=17`） | ✅ |
| §12 预筛 | 190.9 → 134.2 ms（1.42×） | 一致（`mechanism-10k.json`） | ✅ |
| §12 验收 | `1682 / 0 / 0 / 48` | 一致（`p4-02d-full.xml`） | ✅ |
| §7 「block#1 中位 730.2 ms，blocks#2–20 中位 721.3 ms」 | — | **不能复算**：该段引用的 f4_contrast 只存聚合值、不存逐样本数组；可复算的轮级数字是 round 2 `741.9` vs `724.4`、round 3 `729.1` vs `727.4`（结论相同，见 §7） | ⚠ 非阻断 |
| §6 「共 84 次查询 + 2 次暖机」 | — | **计数不准**：工具每轮一次暖机，4 轮 = 84 次**服务调用**（其中 4 次暖机、80 次样本）；页一致性检查覆盖 80 次样本 | ⚠ 非阻断 |
| §9 「两者相加与实测 +7.3 s 吻合」 | — | **算术不符**：按报告自己的数字，覆盖率 +12.45 s 与残余 −2.15 s 相加为 **+10.3 s**（`14 897.3 − 4 596.0`），轮中位口径为 +11.0 s | ⚠ 非阻断 |
| §4 「84 次采样返回页（含融合后的 hit_reason）」 | — | 工具自身的页哈希只覆盖**向量页**（80 样本）；我另行核对全部 80 条 `fused_hits` 唯一 ⇒ 论断**成立**，但工具并未计算它 | ⚠ 表述收紧 |

## 7. 非阻断修正建议（不影响结论，建议随 P4-02F 一次性回填）

1. §7 的逐块「block#1 vs 其余」请改用**可复算**的轮级数字（round 2 `741.9` vs `724.4 ms`、
   round 3 `729.1` vs `727.4 ms`），或让工具把 `f4_contrast` 的逐样本 `engine_block_ms` 也落盘。
2. §6 的样本计数改为「80 次采样 + 4 次暖机 = 84 次服务调用」；§4 把「含 `hit_reason`」标注为
   **主审另核**（工具自身只对向量页做哈希）。
3. §9 末段改为「覆盖率 +12.45 s、残余 −2.15 s，二者相加 ≈ +10.3 s（与 `B_before − A_full` 一致）」，
   并统一 §7 的 MB/MiB 口径（`3 852` 是 MiB；按十进制 MB 计的因子是 0.955 而非 0.91）。

## 8. 对后续卡（P4-02F 阶段 0）的移交结论

1. **F3 的主因已被量化并独立验证**：`asset_id_idx` 覆盖率是主导项（同一张表、同一查询、同一页、
   仅 `replace` 重建即 14 897.3 → 2 444.7 ms）；部分覆盖对**任何**候选块都不生效（DS-L 的 10 万
   round 2/3 与我的 10 000 档覆盖块 84.6 vs 未覆盖块 83.4 两处独立证据）。
2. **不要把它当成 F2 的唯一修复**：即使覆盖率 100%，10 万档每次查询仍需 20 个引擎 pass
   （A 3 602.6 ms / B 1 456.8 ms，本会话复现），并保留一个未定位的残余变量（等覆盖下 A 比 B 慢
   约 1.9–2.5×，只在「小规模连续候选集 + 索引」路径上）。P4-02F 的阶段 0 必须把这两项当作
   已知输入分别复测，而不是只改索引覆盖率就宣称 F2 关闭。
3. 覆盖率修复属于产品维护路径（`ensure_scalar_indexes` 的语义），**不是**本卡授权的改动；
   它落在 [P4-02F](../tasks/P4-02F.md) 的闸门 A 范围内，并需给出重建时机与维护预算。

## 9. 路由、环境与状态

- 未触发扩审条件：无 `src/**` 改动，测量全程走产品入口 ⇒ 维持**有界专项复核**，
  不扩大为完整受影响面主审；本卡维持 **`SOL_NOT_REQUIRED`**（无协议/ADR 裁决）。
- 受限令牌下 pytest `--basetemp` 的 `WinError 5`/`WinError 3` 仍按正常 Windows 权限路由处理，
  **未修改任何 ACL**。
- 本轮新增证据：`test-results/p4-02d-dsh-coverage-check.json`、`p4-02d-dsh-rerun.json`、
  探针 `tmp/p402b-dsh/f4_coverage_probe.py`（均在忽略目录内）。

**当前结论：`P4-02D BOUNDED REVIEW PASS（3 条非阻断文档修正）`
+ `F3 主因 = asset_id 索引覆盖率（已量化、已独立验证）`
+ `残余变量仍开放，交 P4-02F 阶段 0` + `SOL_NOT_REQUIRED`。**
