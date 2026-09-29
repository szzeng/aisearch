# P4-02Y-I — B1 修复轮（复核异常修复 + 再验证）

被修复对象：[独立 DS-H 窄受影响面复核](P4-02Y-I-dsh-narrow-affected-surface-review.md) 的
**B1（阻断）**、**F1 / F2（复核明确要求与 B1 一并修）**，以及复核 §3 末尾那条「多来源 backlog 求和」
观察。基线 = 提交 `7387915` + 本次修复 diff（schema 仍 **16**、无新迁移、无 ADR 变更）。

结论：B1 根因（搜索 profile 发布时序缺陷）已修，四份复核探针复跑后全部给出**结构化、可解释**的终态，
不再出现 `IntegrityError`/无理由 FAILED；F1/F2 的诚实终态与卡片口径已补；多来源 backlog 改为 **union**
（共享 asset 不再重复计数）。定点 17 例（含复核要求的三条新用例）+ 5 文件 158 例 + B 级全量见 §5。
**修复后仍需一次窄复核**（本报告不自我授予复核通过）。

## 1. 复核结论 → 修复对应

| 复核项 | 原现象 | 修复 |
| --- | --- | --- |
| **B1（阻断）** scope 内没有可确认内容时语义阶段抛未捕获 `sqlite3.IntegrityError`，终态无理由、按钮永久重复失败 | `ensure_search_profile` 无条件向 `search_profiles` 插入 `image_generation_id`，而 `index_generations` 行只在**首次发布 embedding** 时产生 ⇒ 该 space 从未发布 ⇒ 外键失败；`IntegrityError` 不是 `CatalogError`，落到 `refresher` 的通用 `except` | ①`indexing/semantic.py` 新增 `generation_published()`；`ensure_search_profile()` 在 generation 不存在时**返回** `profile_id=None` + `reason=GENERATION_NOT_PUBLISHED`（不抛、不写行）；②build 档与 daily 档都在发布前判定，并按各自词表返回结构化结果（`SKIPPED_NO_TARGETS/NO_INDEXABLE_CONTENT`、`OK`、`SKIPPED/GENERATION_NOT_PUBLISHED`）；③build 档把未就绪原因写进 `failed_stages` |
| **F2** 「没有可索引图片」没有诚实状态（FAILED + 卡片 UNBUILT + 只给「开始建库」，点一次失败一次） | 来源级 durable 状态无法区分「从未看过」与「看过但没有可索引图片」：两者 job/entry 都是 0 | ①新增持久标记 `metadata['source_build:<id>']`（复用既有 kv 表，**无新表/列/迁移**），由 build 的分类扫描**完整结束后**写入；②卡片语义状态新增 `NO_IMAGES`（已扫描，无可索引图片）；③build 档以 `SKIPPED_NO_TARGETS + reason=NO_INDEXABLE_CONTENT` 收尾、终态 `COMPLETE` + `basis=semantic-nothing-to-index` |
| **F1** 拒绝内容被隐藏：`SEARCHABLE` 时卡片只渲染 `confirmed / total` | 语义状态只看 `pending`（被拒内容不算 pending 是 P4-02Y 的既有规则，但卡片不显示 `failed`） | ①卡片进度行显示 `failed`/`queued`（`1 / 2（1 张无法编码，N 项排队中）`）；②`failed > 0` 时状态词改为「✓ 可以搜索，N 张无法编码（其余不受影响）」；③全部无法编码时新增 `UNINDEXABLE`（「无法建立语义索引：N 张图片均无法编码（修好文件后再刷新）」） |
| §3 末尾观察：多来源 build 的 `_backlog_report` 按 scope 求和，共享 asset 计两次 | 只影响显示 | `progress._scope_clause` 支持 `source_ids`（`IN (...)` union）；`_backlog_report` 对多个具体来源改成一次 union 查询（`search_ready` 判定等价：union 的 `pending==0` ⇔ 各 scope 皆 0） |
| daily 档的同类崩溃（复核 B1 情形 C，归因既有洞） | 同一调用点 | 同 B1 ①③：daily 档在 generation 不存在时返回 `SKIPPED/GENERATION_NOT_PUBLISHED`，`search_ready=false`（不再谎报 `semantic-confirmed`） |

**未改动**（保持复核 PASS 的部分）：`delete_source` 全路径、原图只读、daily 档的 delta 语义与终态优先级
（`failures → PARTIAL_FAILED`、`not search_ready → FAILED`、否则 `COMPLETE`；build 档只在核心门未就绪时前置
FAILED）、`BUILD_PAGE_SIZE/ROUND_CAP`、加性 API 面（`mode`/`progress`/`DELETE`）。`search_profiles` 的外键与
`index_generations` 生命周期都**没有**改；schema/迁移/ADR 未动。

## 2. B1 根因与修复要点

* `index_generations` 的行由 `ArtifactStore._persist_embedding_event()` 在**第一条 embedding 发布**时
  `INSERT OR IGNORE` 写入（`artifacts/store.py:320-327`）；`search_profiles.image_generation_id` 是它的外键
  （`0007:18-19`）。因此「本轮确认 0 条 且 该 space 从未发布过」= 必然外键失败。
* 触发面正是卡面主入口：空文件夹、只有文档/视频的文件夹、只有损坏图片的文件夹，注册后点「开始建库」即可命中
  （复核探针 A/B/D 已 100% 复现）。
* 修复后的四份复核探针输出（同一脚本、同一夹具，仅产品代码不同）：

| 探针情形 | 修复前 | 修复后 |
| --- | --- | --- |
| A documents-only，`mode=build` | `FAILED` / `basis=null` / `failed_stages=[]` / `errors=[IntegrityError]` / 卡片 `UNBUILT` | **`COMPLETE`** / `search_ready=true` / `basis=semantic-nothing-to-index` / `errors=[]` / 语义阶段 `SKIPPED_NO_TARGETS` + `NO_INDEXABLE_CONTENT` / 卡片 **`NO_IMAGES`**（`built=true`、`scan.files=1`、`total=0`） |
| B 全部图片被拒，`mode=build` | `FAILED` / `basis=null` / `errors=[IntegrityError]` / 卡片 `BUILDING` | `FAILED` / `basis=semantic-confirmed-nothing` / `failed_stages=['semantic-content','semantic-confirmed-nothing']` / `errors=[]` / 卡片 **`UNINDEXABLE`**（`total=2/confirmed=0/failed=2/pending=0`） |
| D 同 B 再点「继续建库」 | 完全相同的崩溃 | 同一结构化结果，`errors=[]`，**没有第二次图像编码**（终态拒绝对重试不再入队） |
| C 同 B 走 daily `mode=refresh` | 同样崩溃 | `FAILED` / `basis=GENERATION_NOT_PUBLISHED` / `errors=[]`（该库确实没有任何 generation，`search_ready=false` 不谎报） |
| F1 探针（1 可编码 + 1 被拒 + delta 溢出） | `COMPLETE`/`search_ready=true`，卡片 `1 / 2` 且不显示 `failed` | 数字与判定不变（`total=2/confirmed=1/failed=1`），卡片显示 `1 / 2（1 张无法编码）`、状态词「✓ 可以搜索，1 张无法编码（其余不受影响）」 |
| F2 探针（先建主库，再建 documents-only 来源） | `FAILED` + `basis=semantic-confirmed-nothing` + 卡片 `UNBUILT` | `COMPLETE` / `search_ready=true` / `basis=semantic-nothing-to-index` / 卡片 `NO_IMAGES` |

## 3. 卡片口径（修复后）

语义状态词（`GET /sources` 的 `progress.stages.semantic.state`，全部由持久状态 + 标记推导）：

| state | 条件 | 卡片文案 | 主按钮 |
| --- | --- | --- | --- |
| `UNBUILT` | `total == 0` 且无 build 标记 | 未建库 | 开始建库 |
| `NO_IMAGES` | `total == 0` 且有 build 标记 | 已扫描，无可索引图片 | 快速刷新此来源 |
| `BUILDING` | `pending > 0` | 正在建立语义索引（进度行另显 `failed/queued`） | 继续建库 |
| `SEARCHABLE` | `pending == 0` 且 `confirmed > 0` | ✓ 可以语义搜索；`failed > 0` 时为「✓ 可以搜索，N 张无法编码（其余不受影响）」 | 快速刷新此来源 |
| `UNINDEXABLE` | `pending == 0` 且 `confirmed == 0` 且 `total > 0` | 无法建立语义索引：N 张图片均无法编码（修好文件后再刷新） | 快速刷新此来源 |
| `UNAVAILABLE` | 本进程无可用语义通道 | 语义通道不可用（`reason`） | 开始建库 |

`progress.built` = 有 build 标记 **或** `total > 0`；`progress.search_ready` = `SEARCHABLE` 或 `NO_IMAGES`
（后者的 backlog 为空是因为索引根本不含这些文件，状态词已明说）。

## 4. 验证

* 复核四份只读探针（`tmp/p4-02y-i-review/probe_*.py`，未改动脚本、未进提交）复跑：全部无异常、输出见 §2 表；
  `probe_no_confirmable_content.py` / `probe_refused_plus_overflow.py` / `probe_docs_source_after_generation.py`
  三份在修复后的 tree 上复跑输出与本表一致；`probe_source_delete_edges.py` 的 `work/facts` 目录在复核方首次运行后
  已删掉该来源，重跑会在夹具 `run_scan(1)` 处 `SOURCE_NOT_FOUND`（探针自身的目录复用问题，非产品行为）；
  它手检的删除边界已固化为常驻用例 `test_deleting_a_source_keeps_the_derived_facts_of_its_assets` 与
  `test_an_unbuilt_and_a_built_source_can_both_be_deleted`。
* 本卡定点：`tests/integration/test_p4_02y_i_source_build.py` **18 例全绿**
  （新增 3 例：`test_a_documents_only_source_builds_once_and_says_what_it_saw`、
  `test_a_source_whose_images_are_all_refused_is_reported_and_not_a_loop`、
  `test_a_refused_image_is_counted_on_the_card_without_hiding_the_rest`，另把复核探针手检的
  「删除后派生事实行仍存活、只清 location_id」固化为
  `test_deleting_a_source_keeps_the_derived_facts_of_its_assets`；并在共享 asset 用例里加了
  union 断言 `backlog_total == 2`）。
* 5 文件套件（本卡 + P4-02Y 两文件 + `test_api_sources_assets.py` + `test_api_frontend.py`）与 B 级全量：
  见 §5 表（JUnit 实读）。
* `ruff check .`、`uv lock --check`、`git diff --check`、`node --check src/photo_search/web/app.js` 全绿。
* 沙箱内 pytest basetemp `WinError 5` 仍为既有环境限制，按正常权限路由重跑，未改 ACL。

## 5. 证据与提交

| 项 | 结果 |
| --- | --- |
| 定点（新卡 18 例，含 4 例新用例） | 18 / 0 / 0 / 0（`test-results/p4-02y-i-fix-focused-single.xml`） |
| 5 文件套件 | 159 / 0 / 0 / 0，35.314 s（`test-results/p4-02y-i-fix-focused.xml`，JUnit 实读） |
| B 级全量 | **1864 / 0 failures / 0 errors / 48 skipped**，310.355 s（`test-results/p4-02y-i-fix-full.xml`，JUnit 实读） |
| 复核探针 | `tmp/p4-02y-i-review/`（gitignored） |

提交内容：`src/photo_search/{indexing/semantic.py,refresh/pipeline.py,refresh/progress.py,web/app.js}`、
`tests/integration/test_p4_02y_i_source_build.py`、本报告、`docs/reports/P4-02Y-I-dsh-narrow-affected-surface-review.md`
（复核方的 FAIL 报告，作为本卡证据链保留）、`docs/tasks/P4-02Y-I.md` 状态行。`docs/development-status.md`
仍不提交（同时携带上一卡 P4-02Y-T 的未提交交接文字）。

## 6. 未验证边界（不因本次修复而通过）

20–30 万真实建库墙钟与分页标定；face/OCR 真实权重与 place 数据集；浏览器端到端计时
（`BROWSER_TIMING BLOCKED`）；孤儿 asset/artifact/vector 物理 GC（卡面排除）；跨进程 CLI 写者与 HTTP
服务并发的删除/刷新；`source remap`/watcher/USN/ANN/A3/模型升级/installer/P4-02Z RC。

## 7. 复现

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path
uv run --no-sync pytest tests/integration/test_p4_02y_i_source_build.py `
  tests/integration/test_p4_02y_fast_scan.py tests/integration/test_p4_02y_library_refresh.py `
  tests/integration/test_api_sources_assets.py tests/unit/test_api_frontend.py -q
uv run --no-sync pytest tests -q
.venv\Scripts\python.exe tmp/p4-02y-i-review/probe_no_confirmable_content.py    # B1 A/B/D/C
.venv\Scripts\python.exe tmp/p4-02y-i-review/probe_refused_plus_overflow.py     # F1
.venv\Scripts\python.exe tmp/p4-02y-i-review/probe_docs_source_after_generation.py  # F2
```

**下一步**：请对本次修复做一次独立窄复核（重点：B1 三个入口是否都不再出现无理由终态、`NO_IMAGES`/`UNINDEXABLE`
口径是否诚实、daily 档是否仍与 P4-02Y 逐行等价、union 化后的 backlog 判定是否等价）。本报告不代替该复核。
