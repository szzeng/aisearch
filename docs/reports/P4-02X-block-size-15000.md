# P4-02X — bounded block-size 最小产品收敛（`SCOPE_BLOCK_SIZE` 5000 → 15000）

**状态：IMPLEMENTED / INDEPENDENT REVIEW PENDING（正确性与性能验收均已达标，待独立 DS-H 窄受影响面主审）。**
性能结论：30 万档真实中文 CLIP + CUDA + 真实 HTTP 暖机文字 **n=30 P95 = 3760.2 ms ≤ 5000 ms ⇒ PASS，保留 15000**。

- 基线：`b42c341`（`master`，开工工作树干净；卡见 [P4-02X](../tasks/P4-02X.md)）。Python 3.12.10 / `.venv`，schema 16。
- 依据：[P4-02M bounded block-size 复测](P4-02M-bounded-block-size-retest.md)（`08e012c`）。
- 本轮改动（3 文件，`src/**` 仅 1 个常数）：`src/photo_search/search/retrieval.py`（+11/−1）、
  `tools/diagnostics/p4_02f_stage0.py`（+25/−2）、`tools/diagnostics/p4_02w_personal_trial.py`（+45/−1）。
  schema/迁移、依赖、`uv.lock`、ADR/companion、API 信封/错误码/游标 **零改动**。
  `retrieval.py` SHA256 = `23749299d85a2688978feb867c7ac85f6edc9f8d3bbf7761e215d1e0954b9426`。

## 1. 产品改动

`SCOPE_BLOCK_SIZE = 5000` → `15000`（唯一产品语义改动点），并在常数旁写明依据：P4-02M 把每块**返回量**压到
`top_k + 1`，P4-02X 因此可以放大**块长**（30 万档 60 → 20 次引擎调用），恰好那一块边界并列被截断时仍回退穷举；
选 15000 而非略快的 30000，是因为回退块的代价随块长增长（§4.3 给了实测）。

逐字未改：`search_candidates_bounded` / `resolved` / exhaustive fallback 逻辑、SQLite authority、
generation/space/revision 绑定、`(distance, asset_id)` 全序、RRF/BM25、信封/错误码/游标、
schema/迁移、scheduler/busy timeout、A3/ANN/第二后端/模型。

## 2. 最小诊断调整（只观测，不改产品语义）

- `p4_02f_stage0.py`：`PRODUCT_BLOCK_SIZE` 改为**导入** `retrieval.SCOPE_BLOCK_SIZE`（防止工具与它声称测量的
  常数再次漂移）；`stages` 模式新增 bounded 引擎桶/调用计数（P4-02M 复审 F2 记的埋点缺口）。
- `p4_02w_personal_trial.py`：`attribution` 场景新增 `_BoundedCallLedger`（只包装产品自己的两个入口），
  记录 `bounded_calls`/`fallback_calls` —— HTTP 信封只有 `engine_blocks`，这两个计数无法从 HTTP 观测。

## 3. 验证 A：正常 bounded 路径（30 万夹具，`top_k=20`）

命令 `tools/diagnostics/p4_02f_stage0.py --modes identity,stages,bounded-blocks --top-k 20
--bounded-candidates 5000 --bounded-rounds 2 --output test-results/p4-02x-offline-equivalence.json` ⇒ exit 0。

| 口径 | 结果 |
|---|---|
| `engine_blocks`（产品默认 = 15000） | **20** |
| `bounded_calls` | **20**（== engine_blocks，非 0 ⇒ 走 bounded 路径） |
| `fallback_calls` | 0 |
| 每块请求 / 实际返回行数 | 21 / 21 = `top_k + 1` |
| `scanned_assets` / `truncated` | 300000 / **false** |
| 最终 `(asset_id, distance)` vs 改动前 5000 基线 | **逐项相同**（`stages` 与 `bounded-blocks` 两侧都比过） |
| 同档重复样本页面 | 每档内一致 |
| 夹具 | 300000 CONFIRMED，Lance `version` 1526 → 1526（只读） |

交错 A/B（同夹具、同会话，`15000 → 5000 → 15000 → 15000 → 5000 → 15000`）：
`vector_channel` 中位 15000 = 2929.9 ms、5000 = 3473.9 ms；service 中位 15000 = 3226.0、5000 = 3705.7；
5000 相对 15000 的配对比值 = **1.1504 / 1.1503** ⇒ 本会话新常数快约 **15%**，与 P4-02M 轮的 −13.6%（service 配对）同向。

## 4. 验证 B：边界并列 / `resolved=False` → exhaustive fallback

### 4.1 真的触发了回退（30 行全等距夹具，`top_k=2`）

命令 `tmp/p4-02x-fallback-probe.py`（复用 `benchmark.build_fixture` + 与既有测试相同的全并列 upsert），
输出 `test-results/p4-02x-fallback-probe.json`：

| block_size | blocks | bounded_calls | **fallback_calls** | page | == 穷举参考 | truncated | scanned |
|---:|---:|---:|---:|---|---|---|---:|
| 5 | 6 | 6 | **6** | `[[1,0.0],[2,0.0]]` | 是 | false | 30/30 |
| 8 | 4 | 4 | **4** | `[[1,0.0],[2,0.0]]` | 是 | false | 30/30 |
| 15000 | 1 | 1 | **1** | `[[1,0.0],[2,0.0]]` | 是 | false | 30/30 |

读数：三档**每个块都回退**（`fallback_calls == blocks`），回退后页面与穷举路径逐项相同、`(distance, asset_id)`
全序不变、候选未被丢弃（scanned = 30 = 全部候选）、没有提前截断。既有用例
`test_a_block_of_tied_distances_cannot_be_decided_by_the_engine_cut`（含 5000/1000/8/5 档，断言
`blocks == [6,4,1,1]` 与同一个有序 top-2）也在 §5 的 38/0/0/0 里通过。

### 4.2 回退块的代价（30 万夹具，单块、背靠背、冷进程、每档 1 样本）

| block_size | 穷举/回退单块 | 返回行数 | bounded 单块 | 返回行数 |
|---:|---:|---:|---:|---:|
| 5000 | 69.6 ms | 5000 | 57.3 ms | 21 |
| 15000 | 183.9 ms | 15000 | 166.4 ms | 21 |
| 30000 | 630.2 ms | 30000 | 462.0 ms | 21 |

**全量回退最坏情形外推**：60 × 69.6 ≈ **4.18 s**（5000）、20 × 183.9 ≈ **3.68 s**（15000）、
10 × 630.2 ≈ **6.30 s**（30000）⇒ 即便整条通道退化成穷举，**15000 也不比 5000 慢，而 30000 会明显变慢**。
这就是本卡选 15000 而不选更快档位的量化依据（回退代价随块长超线性增长）。该外推只用单块成本 × 块数，
不是一次真实的全回退端到端测量，已如实标注。

## 5. 最小回归

| 项 | 结果 |
|---|---|
| 定点：`test_p4_01c_retrieval_blocks.py` + `test_p4_01_index_scale.py` + `test_p2_search_authority.py` + `test_p2_search_topup.py` + `test_p4_02g_a3_contracts.py` | **38 tests / 0 failures / 0 errors / 0 skipped**（`test-results/p4-02x-focused.xml`，JUnit 实读） |
| B 级：`pytest -q tests/unit tests/integration` | **1806 tests / 0 failures / 0 errors / 48 skipped**（257.2 s，`test-results/p4-02x-full.xml`，JUnit 实读） |
| `ruff check .` / `uv lock --check` / `git diff --check` | 全绿 |

环境事实（如实记录，非产品失败）：沙箱内 pytest 的 basetemp 被拒（`WinError 5` 于 `cleanup_dead_symlinks` /
临时目录创建），属既有环境限制；按正常权限路由重跑（basetemp 固定在项目内 `tmp/p4-02x-pytest/*`），未修改 ACL。

## 6. 真实性能验收（P4-02W 场景 A 口径，§5）

口径：同一 30 万夹具 `tmp/p4-02w/warm/data`、同一查询、`top_k = page_size = 20`、真实中文 CLIP
`vit-b-16`、RTX 4060 Laptop / CUDA、真实 `python -m photo_search` HTTP、暖机、30 个正式样本 + 4 个 smoke。

| 运行 | n | P50 | P95 | max | 失败 | `engine_blocks` | 判定 |
|---|---:|---:|---:|---:|---:|---|---|
| **15000（本卡交付状态）** | 30 | **3473.7** | **3760.2** | 3770.8 | 0 | 20（每样本） | **PASS**（门槛 5000，余量 −1239.8 ms） |
| 5000（同会话对照，临时改回后恢复） | 30 | 3906.8 | 4058.4 | 4175.8 | 0 | 60（每样本） | 本会话对照 |
| 5000（历史 P4-02W 基线，另一会话） | 30 | 4508.0 | 5076.4 | 5172.4 | 0 | 60 | 历史 |

- 15000 轮：全部 HTTP 200 / `state=OK` / `hits=20`；`vector.engaged=true, candidates=20, truncated=true`；
  `authoritative.total=300000`；`fixture_before == fixture_after`；**冷启动单列 6161.6 ms**；
  `semantic_channel=READY`（模型目录 = D 盘已准备权重）。
- **同会话对照结论**：把常数临时改回 5000 后 P95 = 4058.4 ms，**也低于 5000**；因此本轮 P95 3760.2 与
  P4-02W 的 5076.4 之差**不能全部归因于本改动**（机器/缓存/会话状态差异约占大头）。
  本改动的受控效果 = 本会话 **−11.1% P50 / −7.3% P95**，离线交错配对 **−13.6% service / −14.8% vector**。
- 归因（真实模型、in-process、5 轮，`test-results/p4-02x-attribution.json`）：
  `vector_ms` p50 **3163.7**（P4-02W 同口径为 4598.8）、`engine_blocks=20`、**`bounded_calls=20`、`fallback_calls=0`**、
  `encode 69.1`、`fts 0.8`、`authority 21.1`、`pending 245.3`、`full_service` p50 3547.4。
  （`fallback_calls` 只能由 in-process 轮观测；HTTP 信封不含该字段 —— 已如实标注。）

## 7. 判定与停止点

1. 正确性：A（正常 bounded 逐项等价）与 B（回退真的触发且逐项等价）**均成立**；
2. 性能：P95 = 3760.2 ms ≤ 5000 ms ⇒ **PASS，保留 15000**；
3. 本卡**不**继续改 20000/30000，**不**启动 A3/ANN/第二后端；后台 D、浏览器首屏、上传、40 万、GTX 1660、
   冷缓存、真实图库与 soak **未测**（P4-02W 的 D FAIL / B BLOCKED 结论不被本卡改变）；
4. 状态 = **IMPLEMENTED / INDEPENDENT REVIEW PENDING**：实现自检不是独立审查，本卡尚缺 §7 要求的
   独立 DS-H **窄受影响面工程主审**（`SCOPE_BLOCK_SIZE → vector_channel → search_candidates_bounded →
   resolved=False → exhaustive fallback → tied-distance/全序/truncated → 受影响测试 → 30 万真实性能证据`）；
   无 ADR/schema/API/数据安全语义变化 ⇒ 预计 `SOL_NOT_REQUIRED`。

## 8. 命令与产物

```powershell
# 验证 A（30 万夹具，产品默认 15000 vs 5000）
& .\.venv\Scripts\python.exe tools\diagnostics\p4_02f_stage0.py --root "W=tmp/p402f-阶段0/scale-300000-wave/data" `
  --modes identity,stages,bounded-blocks --order W --top-k 20 --samples 3 `
  --bounded-candidates 5000 --bounded-rounds 2 --output test-results/p4-02x-offline-equivalence.json   # exit 0
# 验证 B（并列 + 回退代价）
& .\.venv\Scripts\python.exe tmp\p4-02x-fallback-probe.py test-results/p4-02x-fallback-probe.json     # exit 0
# §5 验收（30 样本，真实模型/CUDA/HTTP）+ 同会话 5000 对照 + 归因
& .\.venv\Scripts\python.exe tools\diagnostics\p4_02w_personal_trial.py --git-head b42c341 --samples 30 --smoke 4 `
  --top-k 20 --page-size 20 --output test-results/p4-02x-warm-text.json warm-text `
  --data-root "tmp/p4-02w/warm/data" --threshold-ms 5000                                             # exit 0, PASS
& .\.venv\Scripts\python.exe tools\diagnostics\p4_02w_personal_trial.py --git-head b42c341 --top-k 20 `
  --output test-results/p4-02x-attribution.json attribution --data-root "tmp/p4-02w/warm/data" --rounds 5  # exit 0
# 回归
uv run --no-sync pytest -q tests/unit tests/integration --junitxml=test-results/p4-02x-full.xml `
  --basetemp=tmp/p4-02x-pytest/full                                                                    # exit 0
& .\.venv\Scripts\python.exe -m ruff check . ; uv lock --check ; git diff --check                        # 全绿
```

产物：`test-results/p4-02x-offline-equivalence.json`、`test-results/p4-02x-fallback-probe.json`、
`test-results/p4-02x-warm-text.json`、`test-results/p4-02x-warm-text-control-5000.json`、
`test-results/p4-02x-attribution.json`、`test-results/p4-02x-focused.xml`、`test-results/p4-02x-full.xml`、
`tmp/p4-02x-*.log`、`tmp/p4-02x-fallback-probe.py`。