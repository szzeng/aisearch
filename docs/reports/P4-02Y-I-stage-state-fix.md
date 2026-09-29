# P4-02Y-I — 来源卡分析阶段状态修复（未开始 / 待处理）

触发：用户报告「新增来源未执行任何建库任务时，所有分析阶段不得显示『完成』，应显示『未开始』；
确保状态字段与真实任务状态一致」。基线 = 提交 `3a21505`（P4-02Y-I 已由复审 `2235ab3` 记 PASS 之后的工作树）
+ 本次修复 diff；本改动落在 PASS 之后，属新变更，需按同样方式做一次窄复核（或由用户确认接受）。
schema 仍 **16**、无迁移、无 ADR 变更、无 API 形状变化（`progress.stages[*]` 只多了一个加性计数字段）。

## 1. 现象与复现

探针 `tmp/p4-02y-i-review/probe_new_source_stage_states.py`（一次性，gitignore）在**同一夹具**下打印
来源卡的阶段状态，修复前后对照：

| 卡片情形 | 修复前 | 修复后 |
| --- | --- | --- |
| 只 `注册来源`（没扫描、没建库） | `拍摄时间=COMPLETE`、`定位信息=COMPLETE`（**假「完成」**），其余 `UNAVAILABLE` | `拍摄时间=NOT_STARTED`、`定位信息=NOT_STARTED`，其余 `UNAVAILABLE` |
| 只做「完整校验扫描」（有 1 个可读资产，无任何分析任务） | `拍摄时间=IN_PROGRESS`、`定位信息=IN_PROGRESS`（**假「处理中」**） | 两者 `NOT_STARTED` |
| 点一次「开始建库」 | 两者 `COMPLETE` | 两者 `COMPLETE`（不变，此时确实已完成） |
| 建库后再加 1 张照片（未刷新） | 两者 `IN_PROGRESS`（**假「处理中」**：队列里其实什么都没有） | 两者 `PENDING`（「待处理」：确实有未处理内容，且当前没有任何任务在跑） |

## 2. 根因

`refresh/progress.py` 的 `_enrichment_state(counts, total_assets)` 只比较 `handled` 与 `total`：

```python
if counts["pending"] > 0: return STAGE_IN_PROGRESS
if total_assets <= 0 or counts["handled"] >= total_assets: return STAGE_COMPLETE
return STAGE_IN_PROGRESS
```

* 新注册来源的可读资产为 **0** ⇒ `total_assets <= 0` 直接判 `COMPLETE`；
* 有资产但从未跑过任何分析任务 ⇒ `handled(0) < total(N)` 且 `pending(0)` ⇒ 判 `IN_PROGRESS`。

两者都缺同一件事：**这个 stage 在这个来源上到底有没有跑过**。`handled` 只统计「当前 revision 上已到终态的
job」，因此 `handled == 0` 既可能是「没跑过」也可能是「跑过但内容全变了」，无法分辨。

## 3. 修复

1. `enrichment_counts()` 增加 `attempted`：该来源当前可读资产中，**存在该处理器任意 revision 的 job 行**
   的资产数（taken-time 的 `handled` 仍用 `asset_metadata` 行，理由见 ADR-0011 §2，`attempted` 同口径）；
2. `_enrichment_state()` 改为按真实任务状态推导，顺序即语义：

| 判定顺序 | 状态 | 含义 |
| --- | --- | --- |
| `pending > 0` | `IN_PROGRESS`「处理中」 | 该 stage 有 QUEUED/RETRY_WAIT/RUNNING 的 job |
| `total == 0` | `NOT_STARTED`「未开始」 | 当前没有任何可读资产可分析 |
| `handled >= total` | `COMPLETE`「完成」 | 每个当前可读资产在本 revision 上都有终态结果 |
| `attempted == 0` | `NOT_STARTED`「未开始」 | 该来源从未跑过这个 stage（新来源/只扫描过的来源） |
| 其余 | `PENDING`「待处理」 | 跑过，但部分内容在本 revision 上还没结果，且当前没有任务在跑 |
| 权重/数据集缺失 | `UNAVAILABLE`「不可用」 | 不因缺件而谎报「完成」（修复前既有行为，未改动） |

3. `web/app.js` 的 `STAGE_STATE_TEXT` 补 `NOT_STARTED: "未开始"`、`PENDING: "待处理"`；
4. 状态字段与语义行保持一致：同一来源若语义为 `UNBUILT`（语义索引「尚未开始」），其分析阶段不会是「完成」；
5. 顺带关闭上轮复审的非阻断 **F3**（同一「状态必须解释自己」的口径）：刷新状态行在
   `search_ready=false` 时追加 `原因：<search_ready_basis>`（`SEARCH_GATE_TEXT` 把
   `GENERATION_NOT_PUBLISHED`/`MANIFEST_UNCONFIGURED`/`MODEL_*`/`semantic-*`/`projection-lagging`
   译成中文，未知键回退原字符串），不再只显示一句通用的「刷新失败」。

## 4. 验证

* 探针四情形见 §1 表（修复后无任何假「完成」/假「处理中」）。
* 新增常驻用例 `test_a_new_source_never_reports_a_finished_analysis_stage`
  （注册即断言五个 stage 只可能是 `NOT_STARTED`/`UNAVAILABLE`；扫描后仍 `NOT_STARTED`；建库后 `COMPLETE`；
  新增照片未刷新时 `PENDING` 且 `handled=1/total=2`），并在 Web 接线用例中断言两个新文案存在。
* 本卡定点 19 例、5 文件套件、B 级全量与静态检查见 §5；Web 接线用例新增对
  `NOT_STARTED: "未开始"` / `PENDING: "待处理"` / `SEARCH_GATE_TEXT` 的断言。

## 5. 证据

| 项 | 结果 |
| --- | --- |
| 本卡定点 | **19 / 0 / 0 / 0**，17.010 s（`test-results/p4-02y-i-stage-focused.xml`，JUnit 实读） |
| 5 文件套件 | **160 / 0 / 0 / 0**，19.299 s（`test-results/p4-02y-i-stage-focused5.xml`，JUnit 实读） |
| B 级全量 | **1873 / 0 failures / 0 errors / 48 skipped**，285.769 s（`test-results/p4-02y-i-stage-full.xml`，JUnit 实读；该集合含并行会话在同一工作树新增的 P4-02Y-P/K 用例，故总数高于本卡上轮的 1864） |
| 探针 | `tmp/p4-02y-i-review/probe_new_source_stage_states.py`（gitignored） |
| 静态检查 | `ruff check .` / `uv lock --check` / `git diff --check` / `node --check src/photo_search/web/app.js` 全绿 |

## 6. 口径边界（显式记录）

* 「当前可读资产为空」统一报 `NOT_STARTED`（未开始）：包括「刚注册」「只扫描过」「**原先建过库但文件现在全部
  缺失/离线**」三种。第三种下语义行仍显示历史上的 `SEARCHABLE`、来源状态显示离线/缺失数，阶段行表示
  「当前没有可分析的资产，阶段未开始」——比修复前的假「完成」诚实，也会在文件回来后的下一次刷新里恢复。
* `PENDING`（待处理）表示「有未处理内容且当前没有任何任务在跑」：个人流程里它通常出现在「加了几张照片但还没刷新」
  之后，点「快速刷新此来源」即会入队并变成「处理中」。
* face/OCR/place 在缺少权重或数据集时仍是「不可用」，与是否跑过无关（既有口径）。

## 7. 复现

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path
uv run --no-sync pytest tests/integration/test_p4_02y_i_source_build.py -q
.venv\Scripts\python.exe tmp/p4-02y-i-review/probe_new_source_stage_states.py
```
