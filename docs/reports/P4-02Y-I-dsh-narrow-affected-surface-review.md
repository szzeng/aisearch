# P4-02Y-I 独立 DS-H 窄受影响面工程复核（2026-09-29）

**结论：`REVIEW FAIL`。阻断问题 1 项（B1）；非阻断 2 项（F1/F2）。`SOL_NOT_REQUIRED`。**

> 修复轮指针（2026-09-29，实现方追加，未改动本报告任何原文）：B1 根因、F1/F2 与 §3 末尾的
> 「多来源 backlog 求和」观察已在 [B1 修复轮报告](P4-02Y-I-b1-fix-round.md) 中修复，四份探针脚本复跑后
> 全部给出结构化终态（本报告 §2 描述的崩溃与无理由 FAILED 已不复现）。修复后仍需一次独立窄复核。

被审对象：`docs/tasks/P4-02Y-I.md`、[实现报告](P4-02Y-I-source-build-and-delete.md)、
提交 `7387915`（工作树相对 `714d4ea` 的全部 P4-02Y-I diff）。

方式：只读逐行核对 diff + 既有证据（`test-results/p4-02y-i-{focused,full}.xml`，实读
**155/0/0/0**、**1860/0/0/48**）+ **当前工作树独立复跑**同一 5 文件套件
（`test-results/p4-02y-i-review-focused.xml`，**155/0/0/0**，34.7 s）+ 4 个只读探针
（`tmp/p4-02y-i-review/probe_*.py`，`tmp/` 已 gitignore）。未重跑 30 万真实建库、未改任何产品代码。

工作树另有上一卡未提交产物（`docs/development-status.md`、`docs/tasks/P4-02Y-T.md`、
`tools/diagnostics/packaged_api_smoke.py`、`docs/reports/P4-02Y-T-personal-trial-start.md`），本复核
**未触碰、未提交**任何东西，也未改动 `docs/development-status.md`（以免与那批交接文字混在一起）。

## 1. 逐条核对（用户点名的 5 点）

### 1.1 进度是否真实 — 数字真实，两个终态口径不诚实（→ F1、B1）

`refresh/progress.py` 全部由持久状态推导，无文件读、无模型：
`scan_progress` 取 `file_locations` 计数；`semantic_breakdown` 的分母 = 该 scope 内「被语义层分类为
图片」的资产集（`image_embedding` job 行 ∪ 当前 space 的 CONFIRMED 条目），`confirmed` 要求
`artifacts.input_revision == assets.sha256`，`failed` 只算当前 revision 的
FAILED/CANCELLED/BLOCKED，`pending = total - confirmed - failed`；`enrichment_counts` 的
`handled` 只算**当前** revision 的终态 job（taken-time 用 `asset_metadata` 行，因 ADR-0011 §2 的 job
revision 还摘要位置观测）。`GET /sources`/`GET /sources/{id}` 只加性返回 `progress`，
`node --check`、`test_the_sources_page_carries_the_build_progress_and_delete_entries` 覆盖前端接线。
实测（探针）：3 文件来源（2 图 + 1 文档）→ `scan.files=3`、语义 `total=2 / confirmed=2 / pending=0`、
`SEARCHABLE`；无权重机器 → face/ocr/place `UNAVAILABLE`、taken-time/gps `COMPLETE`（handled=2）。
**但是**：①「已拒绝的图片」被从 `pending` 里扣掉后，来源可以被标成 `SEARCHABLE`，而卡片只渲染
`confirmed / total`、不渲染 `failed`（F1）；②scope 内没有可确认内容时，终态既不是结构化失败也不是
「无需索引」，而是未捕获异常后的无理由 FAILED（B1）。两条都不影响数字本身，但影响「终态是否诚实」。

### 1.2 大来源不漏处理 — PASS

* 分页是**单调 keyset**：`_scoped_asset_page` 用 `l.asset_id > ? ORDER BY l.asset_id LIMIT ?`，
  `_semantic_build_stage` 的分类循环用 `semantic.semantic_selection(after_asset_id=cursor)`，而
  `_TARGET_SQL` 结尾是 `ORDER BY a.asset_id`（`indexing/semantic.py:96`）⇒ 每页是该 cursor 之上的
  最小一批，`cursor = max(page)` 不漏行。
* 三处上限对 30 万级都有约 100× 余量：`BUILD_PAGE_SIZE=5000`、`SEMANTIC_BATCH_LIMIT=5000`
  ⇒ 300 000 资产 ≈ 60 页/批，而 `BUILD_ROUND_CAP=10_000`（只防「不产生持久进展」的死循环）。
* 机制级证明：`test_a_build_drains_the_backlog_beyond_the_batch_and_delta_ceilings` 把
  `SEMANTIC_BATCH_LIMIT=2`、`BUILD_PAGE_SIZE=2`、`PROJECTION_BATCH_SIZE=2`、`DELTA_LIMIT=1` 全部压到
  1–2 件，仍得 `encoded=6`、`backlog_confirmed=6`、`backlog_pending=0`、`image_calls==6`（每图只编码
  一次）、taken-time/GPS 各 `pages>=3`；delta 溢出后增强阶段仍从持久 scope 追平。
* 编码循环只在「整批 `confirmed_delta == 0`」或选空时停下，且停下时 `pending>0` ⇒
  `search_ready=false`（`semantic-backlog`），下一轮继续 —— 不会静默判成可搜索。
* 30 万档**真实建库墙钟未测**（实现报告 §7 已声明，本复核按同口径不据此判错）。

### 1.3 删除不破坏共享 asset — PASS

`catalog/store.py::delete_source` 单事务内：先把 `asset_geo_facts` / `asset_ocr_text` / `faces` 的
`location_id` 置 NULL → `DELETE FROM file_locations`（`AFTER DELETE` 触发器按 `old.asset_id` 入队
FILENAME_CHANGED；`search/documents.py` 的 `filename_text` 只取剩余 PRESENT 位置的重命名）→ 删该来源
`catalog_scan` job → 清 `last_successful_scan` → 删 `scan_runs` → 删 `source_roots`。
核对：

* 三个 `location_id` 列在 DDL 里都可空（`0004:93`、`0009:29`、`0010:48`），`asset_geo_facts` 的
  CHECK 只管 status/lat/lon/raw/datum（`0004:113-121`），`asset_ocr_text`/`faces` 的 CHECK 也不含
  `location_id`，且这三张表没有阻断 UPDATE 的触发器 ⇒ 置 NULL 合法且不触发任何不变式；
  ADR-0005 §表把 `location_id` 明列为「可空」⇒ **不是契约变更**。
* 事实不被消费方按 `location_id` 过滤：`geo/extract.py`、`geo/resolver.py`、`places/filter.py`、
  `places/suggestions.py`、`search/documents.py`、`faces`/`persons` 全按 `asset_id` 查。
* 实测（探针）：删除前 `asset_geo_facts` 2 行（status=ABSENT, location_id=1/2），删除后**行数与 status
  不变、location_id→NULL**，`originals_touched=0`；`assets_orphaned=2`。
* 共享 asset 侧由 `test_deleting_a_source_keeps_the_asset_shared_with_another_source` 覆盖：剩余来源
  仍 PRESENT、CONFIRMED 仍为 1、按该来源过滤的真实搜索仍能命中；`test_an_unbuilt_and_a_built_source_can_both_be_deleted`
  证明删除后来源列表为空而 assets/向量保留（孤儿不做物理 GC，卡面 §5/§7 明确排除）。
* 安全门：该来源有 QUEUED/RUNNING scan → 409 `INVALID_STATE_TRANSITION` + `SCAN_IN_PROGRESS`（且不删
  任何行）；本进程有活动 refresh → 409 + `REFRESH_IN_PROGRESS`，刷新结束后同样删除成功；BLOCKED
  （离线）可删（否则离线目录永久卡住）。跨进程 CLI 写者仍是既有未覆盖面，实现报告 §7 已声明。

### 1.4 原图只读 — PASS

`delete_source` 全是 SQL（本复核逐行确认无任何文件系统调用），响应固定 `originals_touched = 0`；
`test_deleting_a_source_never_touches_a_single_original_photo` 对目录做
`相对路径 → (size, mtime_ns)` 摘要前后比对（4 个文件逐项相同、文件数不变）。建库/刷新路径对原图仍只有
hash 与解码读取，缩略图/预览写入 `data_root` 下的媒体缓存，不落在来源目录。

### 1.5 P4-02Y 日常档不回归 — PASS（daily 代码逐行未变）

`REFRESH_MODE_DAILY` 分支与 P4-02Y 一致：`_catch_up_projection` 仍 `PROJECTION_ROUND_CAP=40`；
`_search_ready` 只在 `mode == build` 时走持久 backlog 分支，daily 仍按 delta
（`unconfirmed_content/required/confirmed`）；`_enrichment_stages` 在 daily 下仍用 delta runners
（`content` / `content|location`）；终态优先级（`failures → PARTIAL_FAILED`、`not search_ready → FAILED`、
否则 `COMPLETE`）只有 build 分支被前置。唯一加性差异：未就绪时 `search_ready_basis` 由 `null` 变成
原因字符串（`note_search_gate`），不改变任何判定且既有测试未断言该字段。
两套 P4-02Y 测试（`test_p4_02y_fast_scan.py`、`test_p4_02y_library_refresh.py`）在本次独立复跑中全绿；
`test_the_daily_source_refresh_still_only_processes_the_change` 证明建库后日常档仍只 hash/编码 1 张变化。
**注意**：B1 在 daily 档同样存在，但那是 `b17ded6` 起就有的既有洞（见 B1 归因），本卡没有使 daily 退化。

## 2. 阻断问题

### B1：scope 内没有可确认内容时，语义阶段抛未捕获 `sqlite3.IntegrityError`，终态无理由、且「继续建库」永久重复失败

**现象**（3 个独立探针，真实 HTTP 入口 + 真实 pipeline，只有编码器是既有替身）：

```
A. documents-only 来源, mode=build（该 space 还没有 generation 行）
   state=FAILED  search_ready=false  search_ready_basis=null  failed_stages=[]
   errors=[{stage: refresh, code: IntegrityError, message: FOREIGN KEY constraint failed}]
   index_generations=[]   卡片: built=false / semantic=UNBUILT / total=0
B. 图片全部被编码器拒绝的来源, mode=build
   state=FAILED  search_ready=false  search_ready_basis=null  failed_stages=[]（同上 errors）
   卡片: built=true / semantic=BUILDING / total=2 confirmed=0 failed=2 pending=0
D. 同 B 再点一次「继续建库」→ 完全相同的 FAILED（永久循环，没有任何进展）
C. 同 B 走 mode=refresh（P4-02Y 日常档）→ 同样崩溃（见归因）
```

**根因**：`refresh/pipeline.py` 的 build 语义阶段（`_semantic_build_stage`，第 706 行
`profile = semantic.ensure_search_profile(...)`）与 daily 阶段（`_semantic_stage`，第 497 行）都在
编码循环之后**无条件**建/激活搜索 profile；`indexing/semantic.py:698` 向 `search_profiles` 插入
`image_generation_id`，而该列是 `REFERENCES index_generations(generation_id)`（`0007:18-19`），
`index_generations` 行只有在**至少发布过一次 embedding** 后才存在（`generation_summary` 对不存在的
generation 仍返回确定性 id + `state=null`）。于是「本轮确认 0 条 + 该 space 从未发布过」⇒ 外键失败。
抛出的 `sqlite3.IntegrityError` 不是 `CatalogError`，`_semantic_build_stage` 的 `except CatalogError`
接不住，最终落到 `api/refresher.py:177` 的通用 `except Exception` ⇒ 只写
`note_error(stage="refresh", code="IntegrityError")` + `finish(FAILED)`，所以
`search_ready_basis=null`、`failed_stages=[]`（前端因此显示「刷新失败」而没有任何原因），
分类阶段已提交的 job 行仍留着 ⇒ 再点一次沿同一条路再撞一次。

**为什么算阻断**：

1. 触发面是卡面主入口本身：`sourceActionMode()` 对任何 `built=false`/`UNBUILT` 的来源都只给
   「开始建库」，API 也不校验 scope 内是否有可索引图片 ⇒ 用户注册一个「还没有照片的文件夹」、
   「只有视频/文档的文件夹」、或「只有损坏/无法解码 JPEG 的文件夹」再点主按钮即可命中；
2. 结果是**未捕获异常**而不是结构化拒绝：无 `failed_stages`、无 basis，用户拿不到任何可行动信息；
3. B 情形不可自愈：卡片显示 `BUILDING` + 「继续建库」，每次点击都是同一次 FAILED；
4. 卡面 §3/§6.2 要求来源卡「进度真实、可行动」，本情形给出的终态与此不符。

**最小修复方向**（不需改 schema/ADR/冻结契约，属本卡内产品修复）：

* 两个语义阶段在 `generation_summary(...)["state"] is None`（该 space 还没发布过任何内容）时不要调用
  `ensure_search_profile`，而是返回结构化结果（既有词表足够：`SKIPPED`/`SKIPPED_NO_TARGETS` +
  一个原因，或 build 档的 `semantic-confirmed-nothing`），让 `_search_ready` 去决定终态；
* build 档需要为「scope 内没有可索引图片」给出诚实状态（见 F2），而不是让它落进 FAILED；
* 修复后需重跑本卡定点 + 两套 P4-02Y 套件，并补两个用例：documents-only 来源 build（不崩、
  状态可解释）、全部图片被拒来源 build 连点两次（不永久失败且不重复编码已确认内容）。

**归因**：daily 档同样的调用点在 `b17ded6`（P4-02Y）就存在，故这是一个**既有洞**；
P4-02Y-I 的 build 档第一次把它放到了「新增来源 → 开始建库」这条必走路径上（新来源没有任何
generation 时尤其容易命中）。本卡因此不能按现状收口，但**不是**本卡新引入的写路径缺陷。

## 3. 非阻断问题

**F1（口径 + UI：拒绝内容被隐藏）**：`pending = total - confirmed - failed` 让「已被编码器拒绝」的图片
不再算 pending，于是 `semantic_state` 可以给出 `SEARCHABLE`（要求 `confirmed > 0`，不要求
`failed == 0`）。实测（探针 `probe_refused_plus_overflow.py`：1 张可编码 + 1 张被拒 + `DELTA_LIMIT=1`
制造 delta 溢出）：run `state=COMPLETE`、`search_ready=true`、`basis=semantic-backlog-empty`、
`failed_stages=[]`、`errors=[]`，而持久 backlog 是 `total=2 / confirmed=1 / failed=1 / pending=0`，
卡片 `SEARCHABLE`。前端 `semanticProgressText` 只渲染 `confirmed / total`、从不渲染 `failed`
⇒ 用户看到的是「语义索引 1 / 2 · ✓ 可以语义搜索」，没有任何一句解释那 1 张为何缺失。
数字本身真实（都来自持久状态），且「拒绝内容不阻塞其余内容可搜索」正是 P4-02Y daily 档已冻结的口径
（被拒内容记 PARTIAL_FAILED）；建议在卡片上把 `failed`/`queued` 显示出来，并在 `failed>0` 时避免
把状态词写成无条件的「✓ 可以语义搜索」（例如「✓ 可以搜索，N 张无法编码」）。

**F2（「没有可索引图片」没有诚实状态）**：概率实测（`probe_docs_source_after_generation.py`：先建好
主库使 generation 存在，再建一个只有 `notes.txt` 的来源）⇒ run `state=FAILED`、
`basis=semantic-confirmed-nothing`、`failed_stages=[]`、`errors=[]`，卡片
`scan.files=1 / built=false / semantic=UNBUILT / total=0` ⇒ 前端继续只给「开始建库」，点一次失败一次。
来源本身没有任何问题（文件被正确归类为 out-of-scope），却拿到与「真的失败」相同的终态词。
建议在 `SEMANTIC_*` 词表加一个「已扫描，无可索引图片」状态（或让 build 档以
`SKIPPED_NO_TARGETS` + 明确原因收尾），与 B1 一并修。

（更次要、未单列的观察：多来源 build 的 `_backlog_report` 按 scope 求和，共享 asset 会被计两次
`total/confirmed`；只影响显示，`search_ready` 只看 `pending==0`，不影响判定。）

## 4. 未验证边界（不因本次复核而通过）

20–30 万真实建库墙钟与 `BUILD_PAGE_SIZE`/轮数标定；face/OCR 真实权重与 place 数据集（本机
`SKIPPED_UNAVAILABLE`，页面显示「不可用」）；浏览器端到端计时（`BROWSER_TIMING BLOCKED`）；孤儿
asset/artifact/vector 物理 GC（卡面排除）；跨进程 CLI 写者与 HTTP 服务同时删除/刷新；`source remap`、
watcher/USN、ANN/A3、模型升级、installer、P4-02Z RC。

## 5. 复现（不依赖本次临时探针）

以既有夹具为准（`tests/integration/test_p4_02y_i_source_build.py` 的 `RefreshEnv` + `StubPinnedAdapter`）：

1. `RefreshEnv.build(tmp, photos=0, adapter=StubPinnedAdapter(), manifest=MANIFEST)`（先 `mkdir` 空
   `tmp/library`），注册一个只含 `notes.txt` 的来源，`POST /api/v1/library/refresh {"mode":"build","source_ids":[该来源]}`
   ⇒ 终端 `FAILED`、`errors[0].code == "IntegrityError"`、`index_generations` 为空（B1 情形 A）。
2. 同上，但来源放 `broken-0.jpg`/`broken-1.jpg`（替身对文件名含 `broken` 的调 `encode_image` 抛错）
   ⇒ 同样 `IntegrityError`；再点一次仍相同；把 `mode` 换成 `"refresh"` 同样复现（B1 情形 B/C/D）。
3. `RefreshEnv.build(tmp, photos=1, ...)` 先 `mode=build` 建好主库（generation 存在），再注册
   documents-only 来源并 build ⇒ `FAILED` + `basis="semantic-confirmed-nothing"`、卡片 `UNBUILT`（F2）。
4. 一个可编码 + 一个 `broken*.jpg` 的来源，令 `pipeline.DELTA_LIMIT=1` 后 build ⇒
   `COMPLETE/search_ready=true/failed_stages=[]`，而 backlog `failed=1`、卡片 `SEARCHABLE`（F1）。

本次复核的探针脚本保存在 `tmp/p4-02y-i-review/`（gitignored，不进提交）：
`probe_source_delete_edges.py`、`probe_no_confirmable_content.py`、`probe_refused_plus_overflow.py`、
`probe_docs_source_after_generation.py`。

## 6. 复核路由

无 ADR/schema/既有冻结 API 契约变化（新增 `mode` 请求字段、`progress` 响应字段、`DELETE` 端点均为加性；
`search_profiles.image_generation_id` 的外键与 `index_generations` 生命周期都未改动，B1 是调用时序缺陷）
⇒ **`SOL_NOT_REQUIRED`**。B1 修复属本卡内产品修复；修好后需重跑本卡定点与两套 P4-02Y 套件并补三条用例，
再做一次窄复核即可收口。本报告不授予 30 万真实建库、face/OCR 真实权重、浏览器计时、孤儿 GC 任何结论。
