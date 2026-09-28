# P4-02X — 窄受影响面工程主审（独立 DS-H）

**结论：`ENGINEERING REVIEW PASS / SOL_NOT_REQUIRED`**；P4-02X 从 `IMPLEMENTED / INDEPENDENT REVIEW PENDING` 收口为 **`PASS`**。
附 1 项必须修正的报告数字（F1，**不影响判定**）与 1 项口径说明（F2）。

审查范围（只读；**未重跑任何性能测试**，未重做 P4-02M/P4-02W，未做全仓审查，未扩到后台 D / 浏览器 / 上传 / A3 / ANN）：
`295f7ad` 的 diff、`docs/tasks/P4-02X.md`、`docs/reports/P4-02X-block-size-15000.md`、`docs/development-status.md`、
`test-results/p4-02x-{offline-equivalence,fallback-probe,warm-text,warm-text-control-5000,attribution}.json`、`test-results/p4-02x-{focused,full}.xml`。

---

## 1. 产品实际改动是否仅为 `SCOPE_BLOCK_SIZE 5000 → 15000` —— 成立

- `git show --name-only 295f7ad`：6 文件；`src/**` **只有一个**：`src/photo_search/search/retrieval.py`（+11/−1）。
  该文件 diff 只有 `SCOPE_BLOCK_SIZE = 5000` → `15000` 与常数旁的依据注释。
- 实测 SHA256 = `23749299d85a2688978feb867c7ac85f6edc9f8d3bbf7761e215d1e0954b9426`，与报告/卡所记**逐字相同**。
- 检索语义未动：`MAX_VECTOR_BLOCKS`、`_CONFIRMED_BLOCK_SQL`、generation/space 绑定、`(distance, asset_id)` 排序、RRF/BM25 都不在 diff 内；`indexing/store.py` 本提交零改动。
- 另两处是诊断工具（`p4_02f_stage0.py` 把 `PRODUCT_BLOCK_SIZE` 改为导入产品常数 + 补 bounded 引擎桶；`p4_02w_personal_trial.py` 加只观测的 `_BoundedCallLedger`），卡面 §2 明确允许，且不改变产品语义。

## 2. `vector_channel → search_candidates_bounded → resolved=False → exhaustive fallback` 调用链 —— 仍正确（逐行核对当前源码）

`search/retrieval.py` 509–546 行（本提交未改动该段）：

- `bounded = getattr(index, "search_candidates_bounded", None)`；
- `bounded is None` ⇒ 走穷举 `search_candidates(..., limit=top_k)`（A3 替身/诊断路径仍可用）；
- 否则先 `hits, resolved = bounded(..., top_k=top_k)`，**仅当 `not resolved`** 才回退 `search_candidates(..., limit=top_k)`；
- 每块 `merged[asset_id] = row`，最终 `sorted(key=(distance, asset_id))`；
- 块控不变：`if len(ids) < block_size: break`、`if blocks >= max_blocks: truncated = True`。

⇒ 放大块长只把 300000 档的调用数从 60 降到 20，不改变任何判定分支。

## 3. 边界并列确实触发 fallback 且全序与旧路径一致 —— 成立

`test-results/p4-02x-fallback-probe.json`（30 行全等距夹具，`top_k=2`）：

| block_size | blocks | fallback_calls | page | == 穷举参考 | truncated | scanned |
|---:|---:|---:|---|---|---|---:|
| 5 | 6 | **6**（== blocks） | `[[1,0.0],[2,0.0]]` | 是 | false | 30/30 |
| 8 | 4 | **4** | 同上 | 是 | false | 30/30 |
| 15000 | 1 | **1** | 同上 | 是 | false | 30/30 |

`fallback_fired_at_least_once = true`；`bounded_rows = 3`（= `top_k+1`）、`fallback_rows = 2`（穷举按 `top_k` 返回）。
定点点名同时含 `test_a_block_of_tied_distances_cannot_be_decided_by_the_engine_cut` 与
`test_tied_distances_use_the_global_total_order_and_never_engine_truncation`（均在 38 例中通过）。
⇒ `resolved=False` 真被触发；回退后页面与穷举参考逐项相同、`(distance, asset_id)` 全序不变、不丢候选、不提前截断。

## 4. 30 万正常路径 `engine_blocks=20` / `truncated=false` / 逐项等价 —— 由证据支持

`test-results/p4-02x-offline-equivalence.json` 的 `bounded_blocks.W`（baseline=15000，candidate=5000，交错 `[15000,5000,15000,15000,5000,15000]`）：

- 15000：`engine_blocks=20`、`blocks=20`、`scanned_assets=300000`、`truncated=false`、`bounded_calls_all=[20,20,20,20]`、`fallback_calls_all=[0,0,0,0]`、`block_rows_distinct=[21]`（= `top_k+1`）；
- 5000：`blocks=60`、`bounded_calls_all=[60,60]`、同一页面；
- `all_pages_match_baseline=true`、`vector_page_matches_baseline=true`、`service_page_matches_baseline=true`、`vector_pages_stable_within_size=true`、`all_truncated_false=true`；
- 夹具只读：`layout_before.version = 1526 = layout_after.version`、`index_entries CONFIRMED = 300000`。
- 真实 HTTP 侧每样本 `engine_blocks=20`（见第 6 条）。

⇒ 「20 次调用 + 通道级 `truncated=false` + 页面与旧 5000 参数逐项相同」**均由 JSON 直接支持**，不是报告自述。

## 5. 已有 JUnit / B 级是否足够支撑本次常数改动 —— 足够

- 定点 `p4-02x-focused.xml`：**38 / 0 / 0 / 0**，覆盖 5 个直接相关模块
  （`test_p4_01c_retrieval_blocks` 9、`test_p4_01_index_scale` 6、`test_p2_search_authority` 5、`test_p2_search_topup` 3、`test_p4_02g_a3_contracts` 15），含并列/回退/全序/A3 契约。
- B 级 `p4-02x-full.xml`：**1806 / 0 / 0 / 48 skipped**（257.214 s，149 个 classname）。
- 本轮独立复跑静态门：`ruff check .` exit 0、`uv lock --check` exit 0（Resolved 96 packages）、`git diff --check` exit 0。
- 本次改动 = 一个访问路径常数 + 注释，上述覆盖足以支撑；卡面 §4 只要求「必要受影响回归 + 一次 B 级」，已满足。

## 6. 真实 30 样本 `P95 = 3760.2 ms` 的统计与口径 —— 正确

- `p4-02x-warm-text.json` 口径字段：`git_head=b42c341`、`data_root=tmp/p4-02w/warm/data`、`device=cuda`、
  `model_dir=D:\SZ\project\PhotoSearchData\models\chinese_clip\vit-b-16`、`binding` 与冻结夹具逐字相同、GPU = RTX 4060 Laptop。
- 独立复算（n=30，`sorted[ceil(0.95*30)-1] = sorted[28]`）：min 3350.9 / **P50 3473.7** / **P95 3760.2** / max 3770.8 / mean 3509.3 —— 与 JSON `stats` 逐字一致。
- 失败 0；30/30 HTTP 200、`state=OK`、`hits=20`、`engine_blocks=20`、`authoritative.total=300000`；`fixture_before == fixture_after`。
- 冷启动 6161.6 ms **单列**、4 个 smoke 不计入统计；`threshold_ms=5000`、`p95_rule` 与卡面定义一致。
- 同会话 5000 对照 `p4-02x-warm-text-control-5000.json`：P95 4058.4、每样本 `engine_blocks=60`、失败 0（可复算）。
- 报告已如实写出：该对照也低于门槛 ⇒ 与 P4-02W 历史 5076.4 ms 之差**不能全部归因本改动**。这一披露是正确的，也是必须保留的。

## 7. 是否存在 ADR / schema / API / 数据安全语义变化 —— 无

本提交未触碰 `docs/decisions/**`、schema/migrations、`src/photo_search/api/**`、`pyproject.toml`、`uv.lock`
（`git show --name-only` 已确认）。`SCOPE_BLOCK_SIZE` 仍是纯**访问路径参数**（不改变召回集合、排序、信封、错误码、游标），
与 P4-02M 主审对 ADR-0012 的既有裁决一致 ⇒ **`SOL_NOT_REQUIRED`**。

---

## 发现

### F1（非阻断，但必须改报告）§3 的「本会话交错 A/B」数字无法复算

报告 §3（并同步进任务卡与 development-status）写：
「`vector_channel` 中位 15000 = 2929.9 ms、5000 = 3473.9 ms；service 中位 15000 = 3226.0、5000 = 3705.7；
5000 相对 15000 的配对比值 = 1.1504 / 1.1503 ⇒ 本会话新常数快约 15%」。

核查：

- 这 6 个数字**在任何保留证据里都不存在**（全仓 + `tmp/` + `test-results/` 扫描；只出现在该提交的 commit-message 草稿与由它派生的报告/卡/status 文本里）。
- 所引 `p4-02x-offline-equivalence.json` 自身的分桶中位是：**vector 15000 = 4961.995**（n=4）/ **5000 = 4708.245**（n=2）；**service 15000 = 5476.689** / **5000 = 5133.319**。
- 该 run 的两个「15000 夹心」对照**互相矛盾**：`3521.624` vs 夹心均值 `4018.654`（0.876，5000 更快）与 `5894.866` vs `4967.945`（1.187，5000 更慢）。
- 任何标准聚合都得不出 1.1504（中位比 4708.245/4961.995 = 0.949；均值比 4708.245/4493.299 = 1.048）。

⇒ **本卡保留的离线 A/B 不足以支撑「本会话快约 15%」**。能由证据支持的是：
① 同会话真实 HTTP 对照（15000 vs 5000：P50 −11.1%、P95 −7.3%，两个 warm-text JSON）；
② P4-02M 复测 `08e012c` 的离线配对（15000：vector ×0.859 / service ×0.864，逐样本 3/3 < 1.0）⇒ 约 −13.6%/−14.1%。

处置（本轮已执行）：报告 §3、任务卡执行结论与 development-status 的该句改为
「本轮离线 A/B 不可比、不作结论；受控差值以同会话 HTTP 对照 + P4-02M 复测为准」，并标注 `DS-H 主审 F1 修正`。

**为何不阻断 PASS**：卡面 §3A 只要求 `engine_blocks` / `bounded_calls` / `truncated` / 逐项等价（第 3、4 条已独立复算），
卡面 §5 的门槛只看**真实 HTTP 30 样本 P95 ≤ 5000**（第 6 条已独立复算）。F1 只影响一句附带的性能对比叙述，不改变任何判定。

### F2（非阻断，口径）通道级 `truncated=false` 与信封 `channels.vector.truncated=true` 同名不同义

报告 §3 表写 `truncated=false`（= 未因 `MAX_VECTOR_BLOCKS` 截断），§6 写信封 `truncated=true`（= 候选数 ≥ `top_k`）。
两者各自成立，但同名易误读；建议像同批报告一样补一句口径（与 P4-02W 复审 R2-F3 同一问题）。

---

## 判定

- **`ENGINEERING REVIEW PASS / SOL_NOT_REQUIRED`**；P4-02X 收口为 **`PASS`**（仅「`SCOPE_BLOCK_SIZE` 常数收敛」这一条）。
- 未授予：后台 D、浏览器首屏、上传搜图、40 万、GTX 1660、冷缓存、真实图库、soak；不启动 A3 / ANN / 第二后端 / 新性能优化。
  P4-02W 的 `D FAIL` 与 `B BROWSER_TIMING BLOCKED` 结论不变。
- 若需要「15000 之后」的整体性能结论，另立卡复测 D/B/C，不在本卡扩展。
