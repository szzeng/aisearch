# P4-02M 新 bounded 检索路径的 block-size 复测（纯测量，产品零改动）

**性质**：只回答一个问题 —— P4-02M 之后的 **bounded** 向量通道里，产品常数
`SCOPE_BLOCK_SIZE = 5000`（30 万档 ≈ 60 次引擎调用）是否仍合理，还是把块改大（30/20/15/10/6 次调用）
更快。不重做 P4-02M、不启用 A3/ANN、不改搜索语义、**不改产品代码**。

- 基线：`ae7074a`（`master`，开工 `git status --porcelain` 空）。Python 3.12.10 / `.venv`，schema 16。
- 本轮唯一仓库改动：`tools/diagnostics/p4_02f_stage0.py`（+367/−1：新增 `bounded-blocks` 模式与 bounded 埋点）。
  `src/**`、`tests/**`、`docs/decisions/**`、`pyproject.toml`/`uv.lock` **零改动**（`git diff --name-only` 为空）。
- 证据：`test-results/p4-02m-bounded-blocks-300k.json`（正式，18 个 5000 基线样本 + 每候选档 3 样本 ×
  `vector_channel` 与完整 service）；`test-results/p4-02m-bounded-smoke.json`（走新算法确认 + 1 轮趋势）。

## 1. 先确认测量走的是新算法（否则整轮 INVALID）

`tools/diagnostics/p4_02f_stage0.py` 原来的埋点只挂 `LanceVectorIndex.search_candidates`（穷举路径），
**没有覆盖 P4-02M 新增的产品方法 `LanceVectorIndex.search_candidates_bounded`**（P4-02M 复审的 F2 就是
这一点：当时 engine 桶≈0、3530 ms 只能反推）。按本轮卡面第 1 条，这属于「诊断工具未覆盖 ⇒ 先修最小
诊断埋点再测」，因此先补了三个**只观测、不重实现**的埋点：`TimedIndex.search_candidates_bounded`（计时 +
记录每块实际返回行数/请求 limit/resolved）、`TimedIndex.search_candidates` 的 fallback 计数、以及
`_BlockSizeOverride`（仅把 `block_size` 注入 `search/service.py` 自己那次 `vector_channel` 调用的进程内
包装）。产品文件未改。

| 检查 | 5000 | 10000 | 15000 | 20000 | 30000 | 50000 |
|---|---|---|---|---|---|---|
| `blocks`（= `engine_blocks`） | 60 | 30 | 20 | 15 | 10 | 6 |
| `bounded_calls`（每个样本） | 60 | 30 | 20 | 15 | 10 | 6 |
| `fallback_calls`（每个样本） | **0** | **0** | **0** | **0** | **0** | **0** |
| 每块请求 limit | 21 | 21 | 21 | 21 | 21 | 21 |
| 每块实际返回行数 | 21 | 21 | 21 | 21 | 21 | 21 |
| `scanned_assets` | 300000 | 300000 | 300000 | 300000 | 300000 | 300000 |

读数：`bounded_calls == engine_blocks`（不是 0 ⇒ 走的是新算法），每块恰好 `top_k + 1 = 21` 行，
`resolved` 恒为真（该夹具无边界并列）⇒ **本轮不是 INVALID**；`fallback_calls` 全 0 意味着这 18+15 个样本
一次都没有回退穷举。

## 2. 口径

- 夹具：`tmp/p402f-阶段0/scale-300000-wave/data`（300000 assets / 300000 CONFIRMED；Lance 表
  `version` 测量前后均为 **1526**，`vector_probe_identical=true`，只读、未建库、未扫真实图库）。
- 查询/模型口径：同一服务查询文本 + 同一 `DeterministicEncoder` 替身（**无固定真实权重**），`top_k = 20`
  （产品首屏页大小，与 P4-02W 归因口径一致；不是 P4-02M §8 的 `top_k=200`）。
- 交错执行：`5000 → 候选项 → 5000 → 候选项 …`，3 轮 =
  `[5000,10000,5000,15000,5000,20000,5000,30000,5000,50000] × 3`，即每个候选档前后各有一个 5000 控制样本
  （共 18 个 5000 样本、每候选 3 个），另有每形态 1 次预热（不计入结果）。
- **漂移已记录**：最后 4 个 5000 基线样本约 3.87–3.91 s，而前 14 个约 3.34–3.51 s（+14%）。因此除原始
  中位数外，另报**配对比值**（每个候选样本 ÷ 它前后两个 5000 控制样本的均值），避免把漂移当成块大小收益。

## 3. 结果

按卡面要求的表（`vector ms` / `service ms` 为原始中位数；括号内为配对比值中位数）：

|block\_size|blocks|vector ms|service ms|fallback|结果等价|
|---:|---:|---:|---:|---:|---|
|5000|60|3421.8|3696.8|0/样本|基线|
|10000|30|3075.0 (×0.904)|3362.0 (×0.916)|0/样本|逐项相同|
|15000|20|3006.8 (×0.859)|3228.6 (×0.864)|0/样本|逐项相同|
|20000|15|2966.2 (×0.845)|3205.7 (×0.855)|0/样本|逐项相同|
|**30000**|**10**|**2832.2 (×0.833)**|**3138.0 (×0.852)**|0/样本|逐项相同|
|50000|6|3096.7 (×0.923)|3249.7 (×0.882)|0/样本|逐项相同|

配对比值逐样本（`vector`）：10000 `[0.903,0.904,0.915]`、15000 `[0.903,0.859,0.827]`、
20000 `[0.866,0.845,0.808]`、30000 `[0.833,0.839,0.797]`、50000 `[0.923,0.880,0.935]`；
`service` 同序为 `[0.916,0.919,0.900]`、`[0.864,0.850,0.958]`、`[0.869,0.855,0.803]`、
`[0.853,0.852,0.813]`、`[0.882,0.893,0.818]`。**每一档、每一个样本都 < 1.0**。

引擎/SQLite 分解（中位 ms，bounded 调用自身已按块计时）：

|block\_size|bounded 引擎合计|回退引擎合计|SQLite 候选 walk|
|---:|---:|---:|---:|
|5000|3132.1|0|277.8|
|10000|2822.0|0|231.9|
|15000|2767.4|0|220.9|
|20000|2711.0|0|219.9|
|30000|2601.5|0|211.2|
|50000|2871.1|0|205.3|

读数：收益来自两点 —— ①每次调用的固定成本（表句柄/plan/整代过滤建立）乘上一次调用数；
②SQLite keyset walk 的语句数（`LIMIT 5000` × 60 → `LIMIT 30000` × 10，−24%）。
`50000/6` 处过滤宽度开始反噬，所以**不是**「调用越少越好」，最优在 10 次附近。

## 4. 等价性与 truncated

- 6 档 × 2 形态（`vector_channel` 与完整 `search_assets`）的最终 `(asset_id, distance)` 页面（`top_k=20`，
  距离 9 位小数）**与 5000 基线逐项相同**；同一档内 3 个重复样本的页面也完全一致
  （`all_pages_match_baseline=true`、`*_pages_stable_within_size=true`）。
- `truncated` 在 vector 与 service 两侧**全为 false**；`blocks` 随档位 60/30/20/15/10/6；`scanned_assets`
  恒为 300000；服务侧融合命中序列（`ranked_hits`）各档一致。
- 基线页与历史 P4-02M 记录一致（首三行 `[35104,1.629788756] [62188,1.630331993] [37205,1.639065266]`）。

### 4.1 直接对照：bounded vs 穷举（同夹具、同一档位）

只比「5000 基线 vs 各档」还不够 —— 还要证明**各档之间**等价，以及 P4-02F §13 的「减少调用数无收益」
是否适用于新路径。做法：在同一夹具上，把 5000 与 30000 分别用 ①产品 bounded 调用、②只暴露
`search_candidates` 的替身（强制走 P4-02M 之前的穷举路径）各测一次（冷进程、每路径 1 样本、同 case 内背靠背，
只用于**相对**读数；`tmp/p4-02m-bounded-vs-exhaustive.py`，输出
`test-results/p4-02m-bounded-vs-exhaustive.json`）：

|block\_size|bounded ms|穷举 ms|bounded/穷举|页面逐项相同|blocks|
|---:|---:|---:|---:|---|---|
|5000|4493.2|4510.1|0.996|是|60 / 60|
|30000|3033.9|4032.9|**0.752**|是|10 / 10|

读数：①两档的 bounded 页面与穷举页面**逐项相同**（等价不只相对于 5000 基线成立）；
②在 5000 档 bounded 与穷举基本同速（0.996），但在 30000 档 bounded 比穷举快 **24.8%** —— 也就是说
**P4-02F §13「合并调用数无法安全受益」的结论是穷举路径（`limit = len(ids)`、每块物化整块）专属的**；
P4-02M 把每块返回量压到 `top_k + 1` 之后，减少调用数才第一次变得有收益。这正是本轮要复测的原因。

## 5. 结论（卡面四问）

1. **最快的是 30000 / 10 blocks**：`vector_channel` 中位 2832.2 ms（配对 ×0.833），完整 service 中位
   3138.0 ms（配对 ×0.852）。15000/20 与 20000/15 紧随（×0.859 / ×0.845 vector）；50000/6 反而回升
   （×0.923 vector、×0.882 service），说明收益在 10 次调用附近饱和。
2. **相比当前 5000**：`vector_channel` **−16.7%**（配对；原始中位 −17.2%，≈ −0.59 s），完整 service
   **−14.8%**（配对；原始中位 −15.1%，≈ −0.56 s）。全部 5 档都优于 5000，但只有 20000/30000 稳定在
   ×0.85 以下；10000 只拿到约 −9%。**没有任何「稳定劣于 5000」的档位**，所以卡面的停止分支
   （「简单减少 block 数无明显收益」）**不成立**，不应据此直接转 A3/结构性方案。
3. **是否完整结果等价：是**。6 档全部与 5000 基线逐项相同，`truncated=false`，`scanned=300000`，
   `fallback_calls=0`（该夹具无边界并列）。块大小在本实现里仍然只是访问路径参数，不是召回参数。
4. **是否值得修改产品常数：值得立一张有界产品改动卡，但本轮不动**，且必须带三条边界：
   - 证据是**合成 wave 夹具 + 替身编码器**（无固定真实权重、无边界并列 ⇒ `resolved=False` 的**回退路径
     一次都没被触发**）。真实语料是否存在边界并列组，本轮没有测。
   - 大块会**放大回退块的最坏代价**：一次 `resolved=False` 会用 `search_candidates(limit=len(ids))` 物化整块，
     P4-02F §13 的穷举读数正是 20000→6416.7 ms、50000→7377.9 ms 引擎时间 ⇒ 回退代价随块近似线性增长。
   - 因此常数选择是折中：15000/20 已拿到约 −14%，回退最坏开销只有 30000/10 的一半；30000/10 再多约 2–3 个
     百分点收益。**推荐在卡面里比较 15000 与 30000 两档**，并要求在「含边界并列」的夹具上复测回退分支。
   - 另外（**外推，未验证**）：P4-02W 真实路径 `vector_channel` p50 4598.8 / full service p50 4921.1，而
     §12.2 暖机文字门限是 P95 ≤ 5000，实测 P95 5076.4 只超 76.4 ms；若同比例收益能迁移到真实路径
     （−15% ≈ 0.7 s），可能刚好覆盖该缺口 —— 这属于外推，**不得当成 §12.2 PASS**。

## 6. 限制 / 未验证

- 3 样本/档 + 18 个基线样本 ⇒ 只支持**趋势**判断，不是 ≥30 样本 P95，也不构成 §12.2 达标。
- §4.1 的 bounded/穷举对照是**冷进程、每路径 1 样本**的 spot-check（仅用于相对读数）；它与 P4-02F §13
  的绝对数字不同（§13 用的是另一口径/另一档 `top_k` 与更早的索引状态），本轮不改写 §13 的历史结论。
- 替身编码器 + 合成 wave 夹具；真实模型/API 端到端、真实图库、GTX 1660、冷缓存、后台并发、打包、
  40 万档、`top_k=200` 形态均**未测**。
- 未启动 A3/ANN/第二后端，未改搜索语义/信封/schema/依赖；`SCOPE_BLOCK_SIZE` 仍为 5000。
- 本轮唯一的 diff 是诊断工具（`tools/diagnostics/p4_02f_stage0.py`），只做测量；按 V3.1 §2.4 它属「只证据
  分支」，**没有**做独立审查。若后续要改产品常数，那是产品改动，必须在独立 DS-H 完整受影响面主审下进行。

## 7. 命令与产物

```powershell
# 走新算法确认 + 趋势（1 轮）
& .\.venv\Scripts\python.exe tools\diagnostics\p4_02f_stage0.py --root "W=tmp/p402f-阶段0/scale-300000-wave/data" `
  --modes identity,bounded-blocks --order W --top-k 20 `
  --bounded-candidates 10000,15000,20000,30000,50000 --bounded-rounds 1 `
  --output test-results/p4-02m-bounded-smoke.json          # exit 0
# 正式（3 轮交错）
& .\.venv\Scripts\python.exe tools\diagnostics\p4_02f_stage0.py --root "W=tmp/p402f-阶段0/scale-300000-wave/data" `
  --modes identity,bounded-blocks --order W --top-k 20 `
  --bounded-candidates 10000,15000,20000,30000,50000 --bounded-rounds 3 `
  --output test-results/p4-02m-bounded-blocks-300k.json    # exit 0
& .\.venv\Scripts\python.exe -m ruff check .               # exit 0
uv lock --check                                            # exit 0
git diff --check                                           # exit 0
```

产物：`test-results/p4-02m-bounded-blocks-300k.json`、`test-results/p4-02m-bounded-smoke.json`、
`test-results/p4-02m-bounded-vs-exhaustive.json`（bounded vs 穷举 spot-check，脚本 `tmp/p4-02m-bounded-vs-exhaustive.py`）、
`tmp/p4-02m-bounded-rounds3.log`、`tmp/p4-02m-bounded-smoke.log`（`test-results/`、`tmp/` 均被忽略，不入库）。