# P4-02Y-I — 来源建库与管理闭环（实现报告）

基线：`master @ 714d4ea`（`[P4-02Y-I 立卡]`；卡面基线 `b17ded6`）。schema 仍 **16**，`0017` 未实施；
ADR 未改动；模型/推理切换由用户在 Codex 中手动完成。本报告只记录实际执行过的验证。

## 1. 范围与结论

按卡面把「来源」的正常流程变成 **新增来源 → 开始建库 → 可以语义搜索 → 日常快速刷新**，并提供
**删除来源**（只删记录，绝不碰原图）。实现全部复用既有 selector / jobs / artifact / confirmation 路径，
没有第二套 pipeline；不新增持久 schema，不新增迁移。

结论：卡面 §1–§5 的实现与 §6 的 10 条验收均有可复现证据（§6/§7）。**独立 DS-H 窄受影响面工程复核尚未进行**
（按卡面由用户另开会话执行）。

## 2. 产品改动

| 文件 | 改动 |
| --- | --- |
| `refresh/pipeline.py` | 新增 **`mode="build"`**（`refresh` 为 P4-02Y 日常档，行为不变）：①语义阶段分「先分类、再编码」两段有界循环；②增强阶段改为按**持久 scope 分页**推进（`_scoped_asset_page`/`_drain_scope`/`_merge_stage_payloads`）；③`SEARCH_READY` 在 build 档由**持久 backlog** 决定（`semantic-backlog-empty`），delta 溢出不再影响结论；④`BUILD_PAGE_SIZE=5000`、`BUILD_ROUND_CAP=10000`（防御性上限，非工作量预算）；⑤`note_search_gate` 记录未就绪原因；⑥build 档终止态由核心门决定 |
| `refresh/progress.py`（新） | 来源级**持久**进度投影：`scan_progress` / `semantic_breakdown` / `enrichment_counts` / `served_binding` / `enrichment_availability` / `source_progress`（纯 SQL，无文件读、无模型） |
| `catalog/store.py` | 新增 `delete_source()`：同一事务内清 `file_locations` → 该来源 scan job → `scan_runs` → `source_roots`；先清三个 `ON DELETE RESTRICT` 的 location 引用（`asset_geo_facts`/`asset_ocr_text`/`faces` 的 `location_id` 置 NULL，**不删事实行**）；运行中的 `QUEUED/RUNNING` scan 结构化拒绝 |
| `ingestion/service.py` | `CatalogService.delete_source()` 薄封装（单事务） |
| `indexing/semantic.py` | `semantic_selection`/`semantic_targets` 新增**加性**参数 `after_asset_id`（keyset 分页），`_TARGET_SQL` 增加 `{cursor_predicate}`；无参调用逐字不变 |
| `api/routes.py` | `POST /library/refresh` 接受 `mode`；`GET /sources`、`GET /sources/{id}` 加性返回 `progress`；新增 **`DELETE /api/v1/sources/{id}`** |
| `api/models.py` | `LibraryRefreshRequest.mode`、`LibraryRefreshStatus.mode`、`SourceDetail.progress`、`SourceDeleteResponse` |
| `api/refresher.py` | `start(source_ids, mode=...)`，把 mode 透传流水线并写入状态快照（注入式 executor 的 3 参契约未改） |
| `api/context.py` | `refresh_manifest_path()` / `refresh_device_policy()`（来源卡与刷新共用同一绑定） |
| `refresh/state.py` | `RefreshStatus.mode`、`idle_status().mode=None`、`note_search_gate()` |
| `web/app.js` + `index.html` + `styles.css` | 来源卡显示 **扫描 N 个文件 / 语义索引 completed / total / 语义状态 / 拍摄时间·定位·地点·人物·文字识别**；按钮按状态切换 **开始建库 / 继续建库 / 快速刷新此来源 + 完整校验扫描 / 删除来源**；删除前 `confirm` 文案；build 未完成时显示「尚未完成建立语义索引（可再次点击继续）」 |

**加性面**：请求 `mode`（缺省即旧行为）、响应 `progress`/`mode`、新端点 `DELETE`。**未改**任何冻结信封字段、
状态词表、错误码集合或 schema。日常 `refresh` 档的终态判定、delta 语义与 P4-02Y 逐行一致（唯一加性差异：
未就绪时 `search_ready_basis` 从 `null` 变为原因字符串）。

## 3. 建库模式：有界、可中断、不因上限永久漏处理（§1/§2）

`POST /api/v1/library/refresh {"source_ids":[...],"mode":"build"}`：

1. **A 快速扫描**：与日常档同一个 `execute_scan(mode="fast")`。新来源没有可复用绑定 ⇒ 首次 fast scan
   自然确认全部文件，**不再额外先跑一次 full scan**；`full scan` 仅作「完整校验扫描」维护入口保留（§1/§9）。
2. **B 搜索投影追赶**：build 档把轮数上限提到 `BUILD_ROUND_CAP`（仍是每轮有界短事务，`CATCH_UP_BUSY_BUDGET_MS`
   不变），因此 20-30 万来源的新文件名能在一轮里追平，而不会停在 `LAGGING`。
3. **C 语义**：**先分类、再编码**。
   - 分类：用既有 `semantic_selection(after_asset_id=...)` 按页（`BUILD_PAGE_SIZE=5000`）遍历 scope，
     对可索引图片调用既有 `enqueue_targets` 落**持久 job**。分类结束即得到稳定、精确的分母；
   - 编码：循环调用既有 `semantic.build_index(limit=5000, source_id=scope)`，直到本轮选不到目标，
     或一整批 `confirmed_delta == 0`（剩余内容已被编码器终态拒绝）才停止——**不会只做第一批**；
   - `SEARCH_READY` 由持久 backlog 判定（`semantic-backlog-empty`）：`backlog_pending == 0` 且
     `backlog_confirmed > 0`。delta 超过 `DELTA_LIMIT` 被清空时结论**不变**（P4-02Y F3 在 build 档闭合）。
4. **D..G 增强**：build 档不再吃内存 delta，而是按 `asset_id` keyset 分页遍历 scope，把每页交给**同一**既有阶段函数
   （thumbnail / taken-time / GPS / place resolver / face / OCR）。已完成的工作是幂等空转（已发布 artifact 复用、
   job 五元逻辑键去重），因此既补历史 backlog 又不重复处理。
5. **中断/重启**：所有进度都在持久状态里（job 行、`index_entries`、`asset_metadata`、媒体 artifact）。
   一轮被 `BUILD_ROUND_CAP`／进程退出截断后再次点击即从同一 backlog 继续；已确认资产不会重编码。
   build 档未完成时终态为 `FAILED` + `failed_stages=["semantic-backlog"]`，前端据此显示「可再次点击继续」
   而不是与真正的扫描失败混为一谈。

## 4. 来源级进度：全部由持久状态推导（§3）

`GET /api/v1/sources`（及单来源）新增 `progress`：

```
scan     = file_locations 计数（files / present / missing）
semantic = {state, total, confirmed, pending, failed, queued, space_id, generation_id}
stages   = {taken_time, gps, place, face, ocr} → {state, handled, pending, total}
built / search_ready
```

两个刻意的定义（精确且廉价）：

* **语义分母 = 该来源被语义层分类为图片的资产集**，证据是 `image_embedding` job 行（每一条 CONFIRMED
  嵌入都来自这样一条 job）。文档/视频/压缩包永远不会进入分母 ⇒ 来源卡不会永远停在「231 400 / 250 000」。
  `total` 用「该资产存在任意 revision 的 job 行 ∪ 当前 generation 有 CONFIRMED」⇒ 文件内容变化时分母不抖动；
  `confirmed` 要求 artifact `input_revision == assets.sha256`；`failed` 只算 `FAILED/CANCELLED/BLOCKED`
  （SUCCEEDED 不是拒绝）；`pending = total - confirmed - failed`。
* **增强 stage = handled / pending**：handled 只算**当前内容 revision** 上已到终态的 job；
  taken-time 例外——它的 job revision 还摘要了位置观测（ADR-0011 §2），因此用 `asset_metadata` 行作为
  「已处理」证据（已在 `progress.py` 注释与 §7 记录此口径差异）。

状态词：`UNBUILT`（未建库，无任何已分类图片）/ `BUILDING`（正在建立语义索引）/ `SEARCHABLE`
（**✓ 可以语义搜索**，仅当 `pending == 0` 且 `confirmed > 0`）/ `UNAVAILABLE`（本机无可用语义通道）；
增强 stage 为 `IN_PROGRESS` / `COMPLETE` / `UNAVAILABLE`（无权重或无 place 数据集时**不谎报处理中**）。
全部查询是 SQL（走 `file_locations(source_id)`、`idx_file_locations_asset`、`index_entries` 主键、
`idx_jobs_subject`），**不打开任何原图、不加载任何模型**；重启后同样数字。

## 5. 删除来源：只删记录（§5）

`DELETE /api/v1/sources/{id}`（会话 + CSRF；401/409 结构化）。一次事务内：

1. 该来源 `file_locations` 的三个 RESTRICT 引用先置 NULL——`asset_geo_facts.location_id`、
   `asset_ocr_text.location_id`、`faces.location_id`。**事实行、人脸行、人工归属、artifact、向量都不删**：
   共享 asset（仍在其他来源）与孤儿 asset（只剩该来源）都保留全部派生与人工知识，只失去「原位置」这个出处；
2. `DELETE FROM file_locations`（`AFTER DELETE` 触发器把受影响资产的 `FILENAME_CHANGED` 入队 ⇒ 随后
   `search_documents.filename_text` 按剩余 PRESENT 位置重算，被删来源的文件名不再命中）；
3. 删该来源的 `catalog_scan` job → 清 `last_successful_scan` → 删 `scan_runs` → 删 `source_roots`。

响应：`locations_removed / scans_removed / scan_jobs_removed / assets_orphaned / originals_touched = 0`。
**原图零改动**由 §6 测试对目录 `path|size|mtime_ns` 摘要前后等价证明。孤儿 asset/artifact/vector
**不做物理 GC**（卡面明确排除）；它们不再属于任何来源（`source_id` 过滤、来源卡、按来源浏览都不再包含），
但目录本身及其它来源的共享内容完全不受影响。

拒绝规则：来源有 `QUEUED/RUNNING` scan ⇒ 409 `INVALID_STATE_TRANSITION` + `reason=SCAN_IN_PROGRESS`；
本进程有活动 refresh ⇒ 409 + `reason=REFRESH_IN_PROGRESS`（refresh 是第二个写者，等它结束即可）。
`BLOCKED`（离线来源）**可以**删除，否则离线目录会永远卡住。

## 6. 验证

### 6.1 定点（新卡 + 上一卡）

`tests/integration/test_p4_02y_i_source_build.py`（14 例，`test-results/p4-02y-i-focused.xml`）：

| 卡面验收 | 用例 |
| --- | --- |
| 1 只点一次「开始建库」 | `test_one_build_click_on_a_new_source_scans_indexes_and_searches`（新来源：`hash_targets=3`、`new_assets=3`、该来源只多 1 个 scan_run、真实 HTTP 搜到新照片） |
| 2 进度真实 / coverage 完整才可搜索 | `test_the_source_card_counts_only_images_in_the_semantic_denominator`（扫 3 个文件、语义 `2/2`、`.txt` 记 `out_of_scope`）、`test_the_progress_of_an_unavailable_processor_is_reported_not_guessed`（无权重 ⇒ `UNAVAILABLE`，纯 Python 两阶段 `COMPLETE`） |
| 3 不因 batch/delta 上限漏处理 | `test_a_build_drains_the_backlog_beyond_the_batch_and_delta_ceilings`（`SEMANTIC_BATCH_LIMIT=2`/`BUILD_PAGE_SIZE=2`/`DELTA_LIMIT=1`：仍 `encoded=6`、`backlog_confirmed=6`、`backlog_pending=0`、每图只编码一次、增强阶段各走 3 页） |
| 4 中断/重启可继续；6 不重复处理 | `test_an_interrupted_build_continues_without_re_encoding_confirmed_assets`（`BUILD_ROUND_CAP=2` ⇒ `backlog_confirmed=4/pending=2`、`search_ready=false`、`failed_stages=["semantic-backlog"]`；第二次只补编 2 张） |
| 5 历史 backlog 能继续追赶 | `test_a_build_catches_up_the_enrichment_backlog_even_with_no_delta`（内容 delta=0 时 taken-time/GPS 仍各建 3 个 job） |
| 7 日常快速刷新只处理变化 | `test_the_daily_source_refresh_still_only_processes_the_change`（build 后新增 1 张：`hash_targets=1`、`encoded=1`、`new_jobs=1`、只多 1 次图像编码） |
| 8 未建库/已建库均可删除 | `test_an_unbuilt_and_a_built_source_can_both_be_deleted`（删除后来源列表为空；asset/向量保留） |
| 9 删除不破坏共享 asset | `test_deleting_a_source_keeps_the_asset_shared_with_another_source`（`assets_orphaned=0`、共享 asset 仍 PRESENT/CONFIRMED，且能用第二个来源过滤搜到） |
| 10 原图 0 修改/0 删除 | `test_deleting_a_source_never_touches_a_single_original_photo`（目录 `path\|size\|mtime_ns` 前后逐项相同、文件数不变、`originals_touched=0`） |
| 安全门 | `test_deleting_a_source_is_refused_while_a_run_is_active`（409/`REFRESH_IN_PROGRESS`、匿名 401、运行结束后可删）、`test_deleting_a_source_is_refused_while_its_scan_is_running`（409/`SCAN_IN_PROGRESS`）、`test_a_source_that_became_offline_can_still_be_deleted`（BLOCKED 可删） |
| Web 入口 | `test_the_sources_page_carries_the_build_progress_and_delete_entries`（shell/script 含 开始建库·继续建库·完整校验扫描·快速刷新此来源·删除来源·确认文案·`"build"`·`semantic-backlog`·`method: "DELETE"`） |

回归：`tests/integration/test_p4_02y_fast_scan.py` + `tests/integration/test_p4_02y_library_refresh.py` +
`tests/integration/test_api_sources_assets.py` + `tests/unit/test_api_frontend.py` 全部通过（日常档行为不变）。

### 6.2 B 级回归与静态检查

* 定点 + 上一卡/来源/前端回归（5 个文件）：**155 / 0 / 0 / 0**，34.769 s（JUnit 实读，
  `test-results/p4-02y-i-focused.xml`）。
* B 级全量 `uv run --no-sync pytest tests -q`：**1860 / 0 failures / 0 errors / 48 skipped**，306.342 s
  （JUnit 实读，`test-results/p4-02y-i-full.xml`；48 skipped 为既有「无权重/无数据集即跳过」用例，与上一卡一致）。
* `ruff check .`、`uv lock --check`、`git diff --check` 全绿（exit 0）。
* `node --check src/photo_search/web/app.js` exit 0（前端脚本语法；浏览器端真实渲染仍为 `BROWSER_TIMING BLOCKED`）。
* 沙箱内 pytest basetemp `WinError 5` 为既有环境限制（AGENTS.md 已记录），按正常权限路由重跑，未改 ACL。

### 6.3 本轮提交范围

提交内容（16 个文件）：`src/photo_search/{refresh/pipeline.py,refresh/progress.py,refresh/state.py,
catalog/store.py,ingestion/service.py,indexing/semantic.py,api/{routes.py,models.py,refresher.py,context.py},
web/{app.js,index.html,styles.css}}`、`tests/integration/test_p4_02y_i_source_build.py`、
`docs/reports/P4-02Y-I-source-build-and-delete.md`、`docs/tasks/P4-02Y-I.md`。
**`docs/development-status.md` 有意不提交**：该文件同时携带上一卡 P4-02Y-T 的未提交交接文字，而
`docs/reports/P4-02Y-T-personal-trial-start.md`、`docs/tasks/P4-02Y-T.md`、`tools/diagnostics/packaged_api_smoke.py`
仍是上一卡的未提交产物；按仓内「不得代为提交」的交接约定留待用户决定，避免 HEAD 出现悬空链接。
`dist/`、`test-results/`、`tmp/` 按 `.gitignore` 不入库。

## 7. 未验证 / 限制（不授予 PASS 的项目）

* **独立 DS-H 窄受影响面工程复核尚未进行**（卡面要求由用户另开会话执行）。本报告不代替该复核。
* **20-30 万档的真实建库未在本卡实测**：本卡没有 30 万档「首次建库（含分类 + 编码 + 六阶段增强）」的真实运行；
  已证明的是机制（有界分页、持久 backlog 门禁、断点续跑、幂等复用）与小夹具上的实测。30 万档的
  `BUILD_PAGE_SIZE`/轮数/墙钟时间需要后续真实测量（属性能验收，不属本卡结论）。
* **face / OCR 真实权重与 place resolver 数据集在本机不可用**：两阶段全程 `SKIPPED_UNAVAILABLE`，
  来源卡显示「不可用」；这条路径（含 `detect()` 每页的 candidate index / association 追赶在大库上的成本）
  **未用真实权重验证**。
* 语义分母口径已注明（分类图片集）；taken-time 的 handled 用 `asset_metadata` 行，**无法区分同 asset
  的历史 revision**（粗口径，卡面允许）；这两点已在代码注释与本报告中显式记录。
* 浏览器端到端计时仍为 `BROWSER_TIMING BLOCKED`（同 P4-02W/P4-02Y）：本卡只验证被服务的 shell/script
  内容与 HTTP 层真实行为。
* 孤儿 asset/artifact/vector 的物理 GC 不在本卡（卡面 §5/§7 明确排除）；`source remap`、watcher/USN、
  ANN/A3、模型升级、installer、P4-02Z RC 亦不在本卡。
* 删除来源的**并发写者**只挡了「本进程活动 refresh」与「该来源 QUEUED/RUNNING scan」；独立 `catalog scan`
  CLI 进程与 HTTP 服务同时运行属既有边界（本卡未新增跨进程锁）。

## 8. 复现命令

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path
uv run --no-sync pytest tests/integration/test_p4_02y_i_source_build.py tests/integration/test_p4_02y_fast_scan.py `
  tests/integration/test_p4_02y_library_refresh.py tests/unit/test_api_frontend.py -q --junitxml=test-results/p4-02y-i-focused.xml
uv run --no-sync pytest tests -q --junitxml=test-results/p4-02y-i-full.xml
uv run --no-sync ruff check .
uv lock --check
git diff --check
```

## 9. 审查路由

按卡面 §8：主实现完成后由**独立新会话 DS-H** 做窄受影响面工程复核，重点为「来源级 Semantic backlog /
进度是否真实、大来源不会因 delta/batch 上限漏处理、删除来源不破坏共享 asset、原图只读、P4-02Y 日常刷新不回归」。
本卡**未**改 ADR、schema（仍 16）、依赖/`uv.lock`、任何冻结信封字段或状态词表，新增面全部为加性（请求 `mode`、
响应 `progress`/`mode`、`DELETE /api/v1/sources/{id}`）；日常 `refresh` 档终态判定与 P4-02Y 逐行一致
⇒ 若复核确认无冻结契约变化，判定目标为 **`SOL_NOT_REQUIRED`**；若复核认为「删除来源对
`asset_geo_facts`/`asset_ocr_text`/`faces` 的 `location_id` 置 NULL」或 build 档 `SEARCH_READY` 口径需要契约层裁定，
则停止扩实现并单独请求 Sol 聚焦终审。
