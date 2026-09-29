# P4-02Y-I 阶段状态修复 — 独立窄复核（2026-09-30）

**结论：`ENGINEERING REVIEW PASS / SOL_NOT_REQUIRED`（仅本切片）。五点复核要求逐项成立；残留 4 条
非阻断观察（1 条文档/口径、1 条边界描述、1 条相邻既有口径、1 条文案回退）。无 ADR/schema/迁移/依赖
变化，故不升级 DS-H 全受影响面复核或 Sol 终审。**

被审对象：提交 `6879a96`（`[P4-02Y-I 状态修复]`，5 文件 +227/−6）+ 该提交写入的
[阶段状态修复报告](P4-02Y-I-stage-state-fix.md) 与任务卡追加段。基线 `3a21505`（P4-02Y-I 已由
[复审](P4-02Y-I-dsh-narrow-affected-surface-rereview.md) 记 `PASS`）之后的新变更，本复核是任务卡
「需一次窄复核」指的那一次。

方式：重开 diff 与真实代码路径（`refresh/progress.py`、`web/app.js`、`api/models.py`、
`refresh/pipeline.py` 的 basis 出口、`0015/0001` DDL），独立复跑 5 文件套件，实读上一轮 JUnit，
并新写两个一次性只读探针（`tmp/p4-02y-i-review/probe_stage_state_edges.py`、
`query_attempted_rows.py`，均 gitignored）。**未改产品代码、未提交**；未重跑 30 万容量、真实权重、
浏览器计时（与本卡范围无关）。

## 0. 独立复现证据

| 项 | 结果 |
| --- | --- |
| 5 文件套件（本次独立复跑） | **160 / 0 / 0 / 0**，82.102 s（`test-results/p4-02y-i-stage-review-focused5.xml`，JUnit 实读） |
| 实现侧 JUnit 实读 | 定点 **19/0/0/0**（17.010 s）、5 文件 **160/0/0/0**（19.299 s）、B 级 **1873/0/0/48**（285.769 s） |
| 静态检查 | `ruff check .` / `uv lock --check` / `git diff --check` / `node --check app.js` 全 exit 0 |
| 新增用例的守卫性 | 修复前 `_enrichment_state` 只能返回 `COMPLETE`/`IN_PROGRESS`（`git show 6879a96^` 逐字核对），新断言 `NOT_STARTED`/`PENDING` 在旧实现下必失败 |
| 沙箱 | 首次沙箱内复跑因 `.pytest-tmp` 的 `WinError 5` 全部 error（既有环境限制），按正常权限路由重跑得 160/0 |

## 1. `attempted` 语义与五态顺序（要求 1）

**五态推导顺序本身成立，无互相冒充**：`pending > 0` 只可能来自 QUEUED/RETRY_WAIT/RUNNING 且
`input_revision == assets.sha256`、资产在当前 scope 内的 job（`LIVE_JOB_STATES`），因此先判
`IN_PROGRESS` 不会掩盖真实在跑的工作；`handled` 只数**当前 revision** 的终态 job（taken-time 例外，
见 §1.2），且 `jobs` 有 `UNIQUE(subject_type, subject_id, input_revision, task_type, processor_id)`、
`asset_metadata` 以 `asset_id` 为主键 ⇒ `handled` 不会重复计数，`handled >= total` 与
`pending > 0` 不可能同时成立（前者在后者之后判）。探针四情形（注册 → 只扫描 → 建库 → 建库后新增
未刷新）复现了修复报告 §1 表：`NOT_STARTED` → `NOT_STARTED`（`total=1`）→ `COMPLETE` → `PENDING`
（`handled=1/total=2`），无假「完成」也无假「处理中」。

**1.1 非阻断 N1 — `attempted` 实际是「任意 revision 的 job 行数」，不是「资产数」。** 代码
`SELECT COUNT(*) ... FROM jobs JOIN assets`（`progress.py:356`）对同一 asset 的多 revision 会逐行计数，
而 [修复报告 §3](P4-02Y-I-stage-state-fix.md) 第 1 条、`enrichment_counts` docstring 与提交信息都写成
「**资产数**」。实测（`query_attempted_rows.py`，单文件来源，1 个 `PRESENT` 位置）：

```
subject_id=4 taken_time_extract taken-time-exif@1  n=2  states=SUCCEEDED,SUCCEEDED
subject_id=4 geo_extract         geo-exif-gps@1    n=1  states=SUCCEEDED
PRESENT locations: asset 4 -> 1
payload: taken_time state=COMPLETE handled=1 pending=0 attempted=2 total=1   ← 同一资产、同一 stage
```

（构造法：同字节、只改 mtime → taken-time 的 `input_revision` 含 observation digest 而变化 → 第二次
建库为同一 asset 再排一条 job；gps 只 digest `sha256`，故仍为 1。）影响：状态推导只用
`attempted == 0`，**判定不受影响**；但该字段会随 `progress.stages[*]` 暴露给 Web/下游，且文档口径与
实现相反（用户本轮描述「job 行数」与代码一致，是报告/docstring/提交信息写成了「资产数」）。
处置建议：把三处文字改成「该处理器任意 revision 的 job 行数」；若本意真是资产数，则查询应改
`COUNT(DISTINCT a.asset_id)`。当前 Web 来源卡只渲染 `state` 词（`STAGE_STATE_TEXT`），不渲染
`attempted`，所以没有现实 UI 影响。非阻断。

**1.2 非阻断 N3（相邻既有口径，非本次引入）— taken-time 的 `COMPLETE` 可以比当前 revision 早一轮。**
`handled` 对 taken-time 取 `asset_metadata` 行（ADR-0011 §2 的口径，本次未改）。探针里「同字节只改
mtime」重扫后，卡片立即是 `taken_time=COMPLETE handled=1 attempted=1`，而该 revision 的 job 是在下一次
建库才产生的（重建后 `attempted=2`）。对依赖 `FILE_MTIME` 兜底的照片，阶段的「完成」在两次刷新之间
可能对应尚未重算的旧时间值。修复前后同样如此，属 P4-02Y-I §3 既有分母口径，**保留为已知边界**。

## 2. `total == 0` 一律「未开始」的边界表述（要求 2）

**判定可接受，但报告 §6 对这个具体边界的描述与代码不符（非阻断 N2）。** 探针把来源的全部文件删掉再扫描：

```
scan={files:2, present:0, missing:2}   built=True   search_ready=True
semantic=NO_IMAGES total=0 confirmed=0 pending=0 failed=0
taken_time/gps = NOT_STARTED (att=0,total=0)   place/face/ocr = UNAVAILABLE
```

即「建库后文件全部离线/缺失」实际渲染的是语义行 **`NO_IMAGES`（「已扫描，无可索引图片」）**，
而 [修复报告 §6](P4-02Y-I-stage-state-fix.md) 写的是「语义行仍显示历史上的 `SEARCHABLE`」。两者不同：
`semantic_breakdown` 只统计 `PRESENT` 位置（scope 为空 ⇒ `total=0`），`built` 又为真（`source_build:<id>`
标记）⇒ 落到 `NO_IMAGES` 而不是 `SEARCHABLE`。**§6 的边界句需要更正**（部分离线时才会是
`SEARCHABLE`，例如只缺 1/2 张）。同时该情形下 `search_ready=true`（这是复审 F2 已登记的口径）。

对「未开始」这个用词本身：把三种历史（从未注册扫描 / 只扫描过 / 曾经建过但现在全部离线）合并成
一个词，第三次的语义并不精确（stage 确实跑过）。但它比修复前的假「完成」更保守，且同一张卡片还显示
`扫描 … （可显示 0）`、`缺失位置 2`，用户能看出是文件离线而不是「从未处理」，恢复后下一次刷新即回到
`COMPLETE`。**结论：作为显式记录的边界可接受；建议把 §6 描述改成实际渲染词，并可在后续卡片里考虑给
「无可用资产」单独用词（非本卡必须）。**

## 3. 语义行 / 五阶段行 / 刷新状态行三者自洽（要求 3）

逐情形核对（探针 + 代码路径）：

| 情形 | 语义行 | 阶段行 | 刷新状态行 | 自洽 |
| --- | --- | --- | --- | --- |
| 新注册（0 可读资产） | `UNBUILT` | 5 个 `NOT_STARTED`/`UNAVAILABLE` | 未跑过 ⇒ `IDLE`、无 `原因` | ✔ |
| 只「完整校验扫描」 | `UNBUILT`（无 job 也无 CONFIRMED） | `NOT_STARTED`（`total=1`） | — | ✔ |
| 「开始建库」完成 | `SEARCHABLE` | `taken_time/gps=COMPLETE`、无权重 `UNAVAILABLE` | `COMPLETE` + `search_ready=true`（`semantic-backlog-empty`，就绪时不渲染原因） | ✔ |
| 建库后新增照片未刷新 | `SEARCHABLE`（新照片尚未分类，不进分母） | `PENDING`（`total=N+1`） | — | ✔（不同阶段，语义行的既有口径） |
| 文档来源建库 | `NO_IMAGES` | `taken_time/gps=COMPLETE`（两阶段对全部目录资产工作，非仅图片） | `COMPLETE` + `search_ready=true`（`semantic-nothing-to-index`） | ✔ |
| daily 无 generation | 卡片按来源状态 | — | `FAILED` + `原因：该向量空间还没有任何已发布的图像索引` | ✔（F3 已修） |
| 全部文件离线 | `NO_IMAGES`（见 §2） | `NOT_STARTED` | — | ⚠ 见 §2/N2 |

`SEARCH_GATE_TEXT` 的键覆盖 `_search_ready` 的全部失败出口（`projection-lagging`、`semantic-backlog`、
`semantic-pending`、`semantic-confirmed-nothing`、`GENERATION_NOT_PUBLISHED`、`MANIFEST_UNCONFIGURED`、
`MODEL_UNAVAILABLE`、`MODEL_LOAD_FAILED`、`ACTIVE_PROFILE_SPACE_MISMATCH`），未收录
`semantic-unavailable` 等兜底串 ⇒ **非阻断 N4**：该情形刷新行会原样显示英文 token（`SEARCH_GATE_TEXT[gate] || gate`
回退生效，不报错）。**要求 3 结论：三面口径一致，除 §2 的全离线用词与 N4 的兜底文案外无冲突。**

## 4. 加性面确认（要求 4）

- 契约/信封：`api/models.py` 里 `LibraryRefreshStatus.stages: dict[str, Any]`、
  `SourceRecord.progress: dict[str, Any] | None` 都是自由形态，`attempted` 是**加性键**；冻结的
  `POST/GET /library/refresh` 信封字段与 `GET /sources*` 形状未变，无删除/改名/改类型。
- schema/迁移：`6879a96` 只改 5 个文件（2 个产品文件为 `refresh/progress.py`、`web/app.js`），无
  `*.sql`、无新迁移；新库实测 `metadata.schema_version = 16`，`0017` 仍未实施。
- `SEARCH_GATE_TEXT` 是 `web/app.js` 内的前端字典，不经 API/合同暴露，且带原文回退。
- 既有实现报告 [P4-02Y-I-source-build-and-delete §stages 行](P4-02Y-I-source-build-and-delete.md)
  仍写 `{state, handled, pending, total}`，可顺手补 `attempted`（纯文档，非阻断）。

## 5. 上轮非阻断项处置（要求 5）

| 编号 | 状态（本次核对） |
| --- | --- |
| F3（刷新行只有通用「刷新失败」） | **已修**：`app.js:765` 在 `!search_ready && search_ready_basis` 时追加 `原因：<basis>`；实现报告 §3.5 与新增 Web 断言（`SEARCH_GATE_TEXT`、`GENERATION_NOT_PUBLISHED` 中文串）一致 |
| F4（提交信息例数笔误） | **新提交已无**：`6879a96` 的 19/160/1873 与 JUnit 实读逐字相同。但旧提交 `73c5f37` 的信息仍是「17/158/1863」，与其实测 18/159/1864 不符；其已在历史中，除非重写历史否则无法消除（重写不建议）。可视作「新基线已正确」 |
| F1（`source_build:<id>` 标记只由 build 档写 ⇒ 只被全局 daily 扫过的无力索引图片来源仍显示 `UNBUILT`） | **仍需保留为已知边界**：本次修复不触碰标记写入路径 |
| F2（`NO_IMAGES` 计入 `progress.search_ready=true`） | **仍需保留为已知边界**：本次 §2 探针再次证实（全部文件离线时 `search_ready=true`、语义行 `NO_IMAGES`），卡片用状态词呈现 |

## 6. 未验证 / 不授予

20–30 万真实建库墙钟与分页标定；face/OCR 真实权重与 place 数据集（本机 `UNAVAILABLE`）；浏览器端到端
计时；孤儿 asset/artifact/vector GC（卡面排除）；跨进程 CLI 写者与 HTTP 并发下的删除/刷新；
`source remap`/watcher/USN/ANN/A3/模型升级/installer/P4-02Z RC。本轮只审 `6879a96` 的窄受影响面。

## 7. 复现

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path
uv run --no-sync pytest tests/integration/test_p4_02y_i_source_build.py `
  tests/integration/test_p4_02y_fast_scan.py tests/integration/test_p4_02y_library_refresh.py `
  tests/integration/test_api_sources_assets.py tests/unit/test_api_frontend.py `
  -q --junitxml test-results/p4-02y-i-stage-review-focused5.xml
.venv\Scripts\python.exe tmp/p4-02y-i-review/probe_stage_state_edges.py   # 四情形 + attempted vs total + 全离线
.venv\Scripts\python.exe tmp/p4-02y-i-review/query_attempted_rows.py      # 只读核对同一 asset 的两条 job
```
