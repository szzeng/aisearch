# P4-02Y-I 独立 DS-H 窄受影响面**复核复审**（2026-09-29）

**结论：`ENGINEERING REVIEW PASS / SOL_NOT_REQUIRED`。首次复核的阻断 B1 已关闭；F1/F2 已按诚实口径修复；
剩余 4 条为非阻断观察（F1–F4，均为文案/口径/提交信息，不影响判定）。**

被审对象：提交 `73c5f37`（`[P4-02Y-I 修复]`，工作树相对 `7387915` 的全部 diff，9 文件 +878/−65）、
[修复轮报告](P4-02Y-I-b1-fix-round.md)、我自己的[首次复核 FAIL 报告](P4-02Y-I-dsh-narrow-affected-surface-review.md)。

方式：重开真实产物核对（commit diff、`git diff b17ded6 HEAD -- pipeline.py` 的 daily 面、DDL/触发器、CLI 调用点），
并把**首次复核用过的 5 个只读探针在同一夹具上重跑**（仅产品代码不同），另加 1 个新探针补口径问题；
独立复跑 5 文件套件 **159/0/0/0**（25.8 s，`test-results/p4-02y-i-rereview-focused.xml`）与
`ruff check .` / `git diff --check` / `node --check` 全绿。既有证据实读：
`p4-02y-i-fix-focused.xml` 159/0/0/0（35.3 s）、`p4-02y-i-fix-focused-single.xml` 18/0/0/0、
`p4-02y-i-fix-full.xml` 1864/0/0/48（310.4 s）。**未重跑 30 万真实建库**，未改产品代码、未提交。
工作树里上一卡 P4-02Y-T 的未提交文件未触碰。

## 1. B1 三个入口：不再有未捕获异常，也不再有「无理由终态」（重点 1）— PASS

重跑首次复核的探针（`tmp/p4-02y-i-review/probe_no_confirmable_content.py` 等）得到：

| 入口 | 修复前（首次复核） | 现在（本次独立复跑） |
| --- | --- | --- |
| A 只有文档的来源 `mode=build` | `FAILED` / `basis=null` / `failed_stages=[]` / `errors=[IntegrityError]` / 卡片 `UNBUILT` | **`COMPLETE` / `search_ready=true` / `basis=semantic-nothing-to-index` / `failed_stages=[]` / `errors=[]`**；语义阶段 `SKIPPED_NO_TARGETS + reason=NO_INDEXABLE_CONTENT`；卡片 **`NO_IMAGES`**（`built=true`、`scan.files=1`、`total=0`） |
| B 图片全部被拒 `mode=build` | 崩溃 + 无理由 FAILED | **`FAILED` / `basis=semantic-confirmed-nothing` / `failed_stages=['semantic-content','semantic-confirmed-nothing']` / `errors=[]`**；卡片 **`UNINDEXABLE`**（`total=2 / confirmed=0 / failed=2 / pending=0`） |
| D 同 B 再点「继续建库」 | 完全相同地崩溃（永久循环） | 同一结构化结果（`failed_stages=['semantic-confirmed-nothing']`）、`errors=[]`；常驻用例断言 `image_calls` 不增长（不重复编码） |
| C 同 B 走 `mode=refresh`（daily） | 同样崩溃 | **`FAILED` / `basis=GENERATION_NOT_PUBLISHED` / `errors=[]`**（有名字的原因，不再谎报 confirm） |
| F2 变体：generation 已存在后建 documents-only 来源 | `FAILED` + `basis=semantic-confirmed-nothing` + 卡片 `UNBUILT` | **`COMPLETE` + `basis=semantic-nothing-to-index` + 卡片 `NO_IMAGES`** |

根因层修复正确且是**调用点无关**的：`ensure_search_profile()` 自己在 generation 不存在时返回
`profile_id=None + reason=GENERATION_NOT_PUBLISHED`、不抛异常、不写行（`indexing/semantic.py:719`），
因此三个调用点全部被覆盖 —— 我逐处核对了 `refresh/pipeline.py:511`（daily）、`:754`（build）与
`indexing/cli.py:400`（CLI，只把结果放进 payload 不取 `profile_id`，`None` 不会引发 TypeError）。
首次复核的四个 `sqlite3.IntegrityError` 路径在本次复跑中全部消失；`index_generations` 仍为空（证明
产品没有为了让外键通过而伪造 generation 行）。

## 2. `NO_IMAGES` / `UNINDEXABLE` 口径（重点 2）— PASS，附 2 条观察

* 状态词由持久状态推导，且与 `UNBUILT` 可区分：新增 `metadata['source_build:<id>']` 标记
  （复用既有 kv 表，`0001_initial.sql:7`；**无新表/列/迁移**），由 build 的分类扫描**完整结束后**写入
  （`_mark_scopes_built`；`cancel_check` 中断时不写）。`built = 标记 or total>0`，对改造前的库向后兼容。
  核对 `source_roots` 是 `INTEGER PRIMARY KEY AUTOINCREMENT` ⇒ 删除来源后残留的标记**不会**被新来源复用；
  `backup.py` 只校验 schema 版本、不校验 metadata 键集合 ⇒ 新键不破坏备份/恢复。
* 状态映射与卡片文案自洽：`NO_IMAGES`（total=0 且已扫过）/`BUILDING`（pending>0）/
  `SEARCHABLE`（pending=0 且 confirmed>0）/`UNINDEXABLE`（pending=0 且 confirmed=0 且 total>0）；
  卡片进度行现在渲染 `failed/queued`，`SEARCHABLE` 且有拒绝时改称「✓ 可以搜索，N 张无法编码（其余不受影响）」
  （首次复核 F1 关闭）。F1 探针复跑：数字与判定不变（`total=2/confirmed=1/failed=1/pending=0`），而卡片
  不再是光秃秃的「1 / 2 · 可以语义搜索」。
* **F1（观察，非阻断）**：标记只由 **build 档**写。实测（探针 `probe_daily_only_docs_source.py`）：只被
  **全局日常刷新**扫过的「无可索引图片」来源，run 为 `COMPLETE/semantic-up-to-date`（与 P4-02Y 一致），
  但卡片仍是 `built=false / UNBUILT`；点一次「开始建库」即变 `NO_IMAGES`。属口径选择（日常档并不按来源做
  分类扫描），不是数据错误；若希望卡片与「这一来源已被扫过」一致，可让日常档也按来源写标记或写进文档。
* **F2（观察，非阻断）**：`NO_IMAGES` 同时被计入 `progress.search_ready=true`（修复轮 §3 明确），
  含义是「backlog 为空是因为索引根本不含这些文件」。卡片以状态词呈现、不会谎报可搜索；但下游若直接读
  这个布尔而不读状态词，需要知道该口径。

## 3. daily 与 P4-02Y 是否逐行等价（重点 3）— PASS

以 `git diff b17ded6 HEAD -- src/photo_search/refresh/pipeline.py` 逐 hunk 核对，daily 面的差异**只有**：

1. `_catch_up_projection(..., round_cap=...)` —— daily 仍是 `PROJECTION_ROUND_CAP=40`（数值未变，
   只是把常数变成参数）；
2. `_search_ready(..., mode=mode)` —— build 专属分支在 `if mode == REFRESH_MODE_BUILD:` 之内，
   daily 仍走 `unconfirmed_content/required/confirmed` 那套；
3. `refresh_library` 的 `failures` 前置与 `final = "FAILED"` 前置都带 `mode == REFRESH_MODE_BUILD` 条件；
4. `_enrichment_stages(..., mode=..., scopes=...)` —— daily 的 runners 与 delta 语义未变；
5. `_semantic_stage`：新增 `published = generation_published(...)`，**published 时调用序列与 P4-02Y 逐字相同**；
   仅当 generation 不存在（= 修复前必然崩溃的那条路）改为结构化 `SKIPPED/GENERATION_NOT_PUBLISHED`；
6. 两个**纯加性**字段：`note_search_gate` 让未就绪时 `search_ready_basis` 有值（此前为 null）、
   语义 stage payload 增 `profile_reason`。

即：**所有原本能正常结束的 daily 路径逐行等价**；唯一行为变化落在「P4-02Y 起就崩溃」的那条路上。
证据：两套 P4-02Y 文件在我独立复跑中全绿；探针 C/`probe_daily_only_docs_source.py` 也显示 daily 在
无目标/无 generation 场景分别给出 `semantic-up-to-date` 与 `GENERATION_NOT_PUBLISHED`，都带原因。

## 4. union 后的 backlog 判定是否等价（重点 4）— PASS

`_backlog_report` 现在分三路：`None in scopes → 全库`、单个具体来源 → `source_id=`（与改造前完全同一查询）、
多个具体来源 → `source_ids=` **一次 union**（`progress._scope_clause` 的 `IN (...)` + `DISTINCT`）。
判定等价性（gate 只读 `backlog_pending`/`backlog_confirmed`/`backlog_total`）：

* `pending` 是「union 后的资产集里当前 revision 未确认的资产数」，每个资产最多计一次 ⇒
  `pending(union)==0 ⇔ 每个 scope 的 pending==0`（既不会假 READY 也不会假 NOT-READY）；
* `confirmed(union)>0 ⇔ 至少一个 scope 有已确认资产`，`total(union)==0 ⇔ 没有任何 scope 含被分类过的图片`；
* 唯一变化是**共享 asset 的显示分母**不再重复计数（这正是复核 §3 末尾那条观察要修的），
  常驻用例 `test_deleting_a_source_keeps_the_asset_shared_with_another_source` 里已断言
  `backlog_total == 2 / backlog_confirmed == 2`（两来源共享 2 张内容）。

## 5. 非阻断观察汇总

| 编号 | 内容 | 影响 |
| --- | --- | --- |
| F1 | `source_build:<id>` 标记只由 build 档写 ⇒ 只被全局日常刷新扫过的无力索引图片来源仍显示 `UNBUILT` | 文案/口径，点一次「开始建库」即转 `NO_IMAGES`；无数据错误 |
| F2 | `NO_IMAGES` 计入 `progress.search_ready=true` | 口径说明（卡片用状态词，不谎报）；下游读布尔时需知此定义 |
| F3 | daily 的 `GENERATION_NOT_PUBLISHED` 终态在 API JSON 里有原因，但 Web 刷新状态行只渲染 `failed_stages`/backlog ⇒ 显示成通用的「刷新失败」 | 文案；来源卡已用 `UNINDEXABLE` 解释，建议未就绪时也渲染 `search_ready_basis` |
| F4 | 修复提交信息写「定点 17/0/0/0、5 文件 158/0/0/0、B 级 1863/0/0/48」，而修复轮报告与 JUnit 实读为 18 / 159 / 1864（我的独立复跑 159/0/0/0 与报告一致） | 仅提交信息笔误，证据链本身一致 |

## 6. 未验证边界（不因本次复审而通过）

20–30 万真实建库墙钟与 `BUILD_PAGE_SIZE` 标定；face/OCR 真实权重与 place 数据集（本机
`SKIPPED_UNAVAILABLE`）；浏览器端到端计时（`BROWSER_TIMING BLOCKED`）；孤儿 asset/artifact/vector GC（卡面排除）；
跨进程 CLI 写者与 HTTP 服务并发下的删除/刷新；`source remap`/watcher/USN/ANN/A3/模型升级/installer/P4-02Z RC。

## 7. 复现

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path
uv run --no-sync pytest tests/integration/test_p4_02y_i_source_build.py `
  tests/integration/test_p4_02y_fast_scan.py tests/integration/test_p4_02y_library_refresh.py `
  tests/integration/test_api_sources_assets.py tests/unit/test_api_frontend.py -q
.venv\Scripts\python.exe tmp/p4-02y-i-review/probe_no_confirmable_content.py      # B1 A/B/D/C
.venv\Scripts\python.exe tmp/p4-02y-i-review/probe_refused_plus_overflow.py       # F1
.venv\Scripts\python.exe tmp/p4-02y-i-review/probe_docs_source_after_generation.py # F2
.venv\Scripts\python.exe tmp/p4-02y-i-review/probe_source_delete_edges.py         # 删除后事实存活
.venv\Scripts\python.exe tmp/p4-02y-i-review/probe_daily_only_docs_source.py      # F1（本轮新增）
```

## 8. 收口

无 ADR/schema/迁移/冻结信封变化（新增 `metadata` 键、`reason`/`profile_reason` 字段与 `source_ids` union 查询
均为产品内部或加性面）⇒ **`SOL_NOT_REQUIRED`**。首次复核的 1 阻断 + 2 非阻断全部关闭，剩余 F1–F4 不阻断
收口；P4-02Y-I 可记 **PASS（仅本卡范围）**。本复审不授予 30 万真实建库、face/OCR 真实权重、浏览器计时、
孤儿 GC、跨进程并发任何结论；不自动进入 P4-02Z。
