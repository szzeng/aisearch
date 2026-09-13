# Development status

## Current state

**P0 is complete. P1-01 is PASS (C2 final short re-review). P1-02 is PASS. P1-03 is PASS. P1-04 is PASS
(B level plus real-port/C-level packaged smokes and the C1 Windows-path/local-access review). P1-05 is PASS
(B level + C-level packaged smoke + a recorded real-browser smoke); its non-blocking follow-up P1-05B
(front-end verification base) is also PASS. P1-06 is PASS (D level: 16 new adversarial acceptance cases for
duplicate / move / replace / offline / remap / restart, with no production change). **P2-01 is now
READY / CURRENT TASK.**

P1-01 history (frozen contract `docs/decisions/ADR-0002-catalog-identity-migration-scan-contract.md`):

- Implementation `92ca7566`, three C2 review rounds (`83adb65` FAIL, `8393583` FAIL) and two corrective passes
  (`ce6f56c5`, `f0b4f402`), final C2 short re-review PASS in [docs/reports/P1-01-c2-final-pass.md](reports/P1-01-c2-final-pass.md).
  Accepted closing baseline: `e39a40c`.
- Persistent positive writes are submission-qualified, retry/lease exhaustion commits job/scan_run terminal state in one
  transaction, schema compatibility cannot be bypassed, stale hashes cannot produce success, and same-scan recovery no
  longer inherits observations from an older execution.

P1-02 (streaming scan, stability waiting, hash before/after stat, missing rules) is recorded in
[docs/reports/P1-02-streaming-scan-stability-and-missing.md](reports/P1-02-streaming-scan-stability-and-missing.md).

P1-03 (EXIF orientation, thumbnails/previews, sharded rebuildable cache, bad-media isolation) is recorded in
[docs/reports/P1-03-orientation-thumbnails-cache-and-isolation.md](reports/P1-03-orientation-thumbnails-cache-and-isolation.md).
It ships `catalog thumbnail` / `catalog preview` / `catalog media-status`, keeps `schema_version = 1` (no new table, no
new dependency) and treats the atomically published artifact file itself as the READY marker.

P1-04 (sources/assets API, controlled file access, local session protection) is recorded in
[docs/reports/P1-04-api-controlled-files-and-local-session.md](reports/P1-04-api-controlled-files-and-local-session.md).
It adds `src/photo_search/api/**` (`/api/v1/{health,session,capabilities,stats,sources,assets,jobs}` plus
thumbnail/preview/original), a loopback-only binding policy with a single-use bootstrap credential, HttpOnly
session + double-submit CSRF + Host/Origin checks, four-layer controlled path resolution, and one background
scan executor that reuses the production `catalog scan` path. `schema_version` is still 1, ADR-0002 is unchanged,
and the only dependency edit is declaring the already-resolved `pydantic` range explicitly.

P1-05 (sources page, insertion-time browsing, detail view, offline hints) is recorded in
[docs/reports/P1-05-local-frontend-sources-browse-detail-offline.md](reports/P1-05-local-frontend-sources-browse-detail-offline.md).
It ships a dependency-free static front end (`src/photo_search/web/**` served from package data by
`src/photo_search/api/web.py`), an additive `order=time_desc` keyset with its own cursor space, an additive
`next=` redirect on the session exchange, and a launcher URL that lands on the page. `schema_version` is still 1,
ADR-0002 is unchanged, `pyproject.toml`/`uv.lock` are untouched, and every existing P1-04 test still passes
unmodified.

P1-05A 的服务端 Windows 路径脱敏补强为 **PASS**：完整 B 级测试（303 passed、1 skipped）、专项回归、
真实端口损坏图片场景、Ruff 与差异检查均通过；详见
[P1-05A 报告](reports/P1-05A-local-path-redaction.md)。

P1-06 (the duplicate / move / replace / offline / remap / restart acceptance closed loop) is recorded in
[docs/reports/P1-06-identity-lifecycle-acceptance.md](reports/P1-06-identity-lifecycle-acceptance.md). 它新增
一个测试文件 `tests/integration/test_catalog_identity_lifecycle.py`（16 个用例），**未改任何生产代码**：
`schema_version` 仍为 1，ADR-0002、`0001_initial.sql`、`pyproject.toml`、`uv.lock` 均未改动，既有测试
全部原样通过。主线当前任务为 P2-01。

Current architecture: `个人图片搜索系统_V3.0_开发主架构.md`, requirement revision 4 (2026-09-13 actual-place planning).
Current execution strategy: `个人图片搜索系统_V3.1_Codex_DeepSeek_开发执行策略.md`, revision 3 (2026-09-13 location task chain and current-state reconciliation).

## Latest planning closure — P2-00 (2026-09-13)

地点规划已完成（文档交付，**地点功能尚未实现，P2-01 尚未执行**）。范围已由用户确认：国家／城市／景点／
自定义地点，GPS 与手动归类。GPS 事实、机器建议、人工归属分离；取消后的人工锁保留；首版提供离线附近城市
建议供确认，不把最近点当准确行政区。沿用静态前端，schema_version 仍为 1。

决策：[ADR-0004](decisions/ADR-0004-place-classification-plan.md)。
全项目规划：[P2-00 路线图](plans/P2-00-location-project-roadmap.md)。
核对与验证：[P2-00 报告](reports/P2-00-location-project-planning.md)。
本次文档闭环 PASS：既有迁移测试 11/11 通过、Ruff／diff-check clean、7 份文档链接通过；
测试临时目录 ACL 限制经隔离目录和工具提权解决，完整命令在报告。未执行地点功能测试或 P2-01 契约测试。

下一任务仍为 **P2-01 / C2 / GPT-5.6 Sol / high**；增加无模型 CPU／非向量产物和权威筛选版本外壳。
随后 **P2-01A → P2-02 → P2-03 → P2-03A/B/C → P2-04～08**。地点实现不提前到本次规划。
拍摄时间、HEIC 与 remap 产品欠项分别排入 P4-00A/B、P4-00C、P4-05A/B。

## Latest accepted verification

P1-06 的完整 D 级验收（`docs/tasks/P1-06.md` 要求的基础等级 + 6 类故障注入/恢复场景）：

| Check | Result |
|---|---|
| `uv run pytest -q tests/integration/test_catalog_identity_lifecycle.py` | exit 0, `16 passed`（本次新增用例） |
| `uv run pytest -q tests/unit tests/integration` | exit 0, `tests=337 failures=0 errors=0 skipped=1`（336 passed / 1 skipped，127.111 s） |
| `uv run ruff check .` / `git diff --check` | clean / clean |
| `uv lock --check` | exit 0, `Resolved 95 packages`（无依赖变更） |
| 场景：重复内容（同来源 3 份含中文/空格、>220 字符长路径、只读原文件 + 跨来源 1 份） | 1 个 `asset_id`、4 个位置、1 个内容寻址缩略图；删除同来源 1 份后其余 `PRESENT`、来源内 `assets` 仍为 1、产物字节/时间戳不变；删除另一来源唯一位置后资产行仍保留 |
| 场景：移动（`a.jpg` → `2024 年/照 片 一.jpg`） | 新位置 `PRESENT` + 旧位置 `MISSING`、`asset_id` 不变、位置数 +1、资产数不变、缩略图路径与 `(size, mtime_ns)` 完全不变（未重算） |
| 场景：替换（4 种） | 不同长度覆盖 → 新内容身份、旧资产仍 `ACTIVE`；覆盖成既有位置的内容 → 重绑该既有资产、不新增；同尺寸 + 还原 mtime → 仍重新 hash 识别；两位置互换 → 各自跟随内容身份；覆盖后中断 → 位置 `PRESENT` 但 `asset_id IS NULL`（待确认）、`RUNNING`、无缺失、`last_successful_scan` 不变 |
| 场景：断盘/离线（成功后拔盘 + 扫描中掉盘 + 枚举↔确认窗口） | 一律 `BLOCKED`、`locations_missing == 0`、`last_successful_scan` 不变；2 个已发布缩略图字节不变且 `media-status` 仍 `published == 2`；未生成的 preview 报 `source_missing`/`SOURCE_FILE_MISSING`；原文件仅被夹具移动、字节未变 |
| 场景：重映射（真实改名挂载点 + 单写事务更新 `root_path`） | 位置行零改写（`location_id`/`path_key`/`asset_id`/`size`/`mtime_ns`/`availability` 投影逐项不变）、重扫 0 缺失、资产数与产物不变；产品 remap 入口实测不存在（OpenAPI 14 条路径，无 `remap`） |
| 场景：重启/中断恢复 | 关闭服务 → 停机期间替换 1 个已绑定文件 + 删除 1 个未确认文件 → 新进程接管**同一** `scan_run`（`scans == 1`、`jobs == 1`）；`attempts == 1`、无重复位置、被替换位置绑定新内容、旧内容资产仍 `ACTIVE`、被删位置 `MISSING` 且 `asset_id IS NULL`；多次接管 `attempts` 0→1→2 单调 |
| 边界 | 0 文件/空子目录、1 文件、1001 文件（1001 位置 / 1001 资产 / `path_key` 全不重复 / 0 缺失）、只读原文件、中文/空格/长路径 |

Produced by DS-L on the P1-05 implementation and its P1-05B follow-up (see the P1-05 and P1-05B reports for
per-scenario evidence).

### P1-05B (front-end verification base, A level)

| Check | Result |
|---|---|
| `uv run pytest -q tests/unit/test_api_frontend.py` | exit 0, `46 passed` (42 existing + 4 new contract cases) |
| `uv run ruff check .` / `git diff --check` | clean / clean |
| `uv run python tools/diagnostics/frontend_smoke_fixture.py --photos 60 --bulk 2000 --corrupt --reset` | exit 0, 2061 synthetic files under `%TEMP%`, prints the source root, the real-port start command and the 10 expected observations |
| `--too-new` / `--offline` branches (separate `%TEMP%` dir) | verified: deletes the named original, reports the missing ones, builds a `schema_version = 2` catalog |
| Production code | untouched (`src/photo_search/**` unchanged); the only test edit is 4 added cases in `tests/unit/test_api_frontend.py` |
| New artifacts | `tools/diagnostics/frontend_smoke_fixture.py`, `docs/diagnostics/frontend-smoke-checklist.md` |

| Check | Result |
|---|---|
| `uv run pytest -q tests/unit tests/integration` | exit 0, 304 collected, `303 passed, 1 skipped` |
| focused P1-05 suites | exit 0, `50 passed` (`test_api_frontend.py` 42, `test_api_frontend_routes.py` 4, `test_api_time_order.py` 4) |
| `uv run ruff check .` / `git diff --check` | clean / clean |
| `uv lock --check` | exit 0, `Resolved 95 packages` (no dependency change) |
| P1-05 API/page scenarios 1-9 (public shell + static assets with traversal refusals, session `next` 303 + unsafe-`next` 422 without burning the credential, `time_desc` 26-asset paging with tie-break stability and cross-cursor `INVALID_CURSOR`, sources/scan flow, detail/offline with `SOURCE_FILE_MISSING`, bad-media codes, empty/single/multi-page/unicode/read-only boundaries, higher `schema_version`) | all executed on `%TEMP%` fixtures; mapped to named tests in report §5 |
| Recorded real-browser smoke (Codex in-app browser, real port, 2061-file `%TEMP%` fixture) | 10 steps observed: 303 landing, source registration without rendering absolute paths, live `RUNNING 14% → 65% → SUCCEEDED 100%`, 50 → 100 grid paging with lazy thumbnails, detail with 7 locations, `原文件不可用` badge with the thumbnail still shown, real `409 SOURCE_FILE_MISSING` from `/original`, `DECODE_FAILED` with the path withheld, narrow 640×900 viewport + Tab/Enter, and the `SCHEMA_TOO_NEW` notice (no empty list, no `-wal`/`-shm` written) |
| C-level packaged smoke (`tools/packaging/build_onedir.py` + `tools/diagnostics/packaged_api_smoke.py`) | build exit 0 (~62 s, 34,020,895 B); bundle copied to `%TEMP%\P1-05 打包页面验收\PhotoSearch 中文 空格`; `status=ok`/`problems=[]`: `GET /` 200 `text/html` `no-cache` with no external reference or local path, `/web/app.js` 200 (33,516 B) and `/web/styles.css` 200 inside the frozen bundle, unknown asset 404, printed link → 303 `Location: /`, plus the P1-04 session/scan/thumbnail/offline scenarios; startup 1.0 s |
| New finding (carried forward) | the media failure records embed a **repr-escaped** path (`C:\\Users\\…`) that `errors.Redactor` does not match, so `GET /assets/{id}` messages can still contain an absolute path; the page now withholds such messages (unit-asserted) and a C1/C2-reviewed server-side fix is recommended (report §8.1) |

P1-03 shipped contract: thumbnail `catalog-thumbnail@1` (long edge 320, JPEG q85, LANCZOS, never enlarged) and preview
`catalog-preview@1` (long edge 1600, JPEG q88, lazy). Layout
`<data_root>/{thumbnails,previews}/<processor_id>/<sha[:2]>/<sha[2:4]>/<sha>.jpg`, failure records under
`<data_root>/cache/media/...`, atomic same-directory write + fsync + `os.replace`. No schema change (still
`schema_version = 1`), no new dependency.

## Carried-forward limits / follow-ups

- **P1-06**: 对抗性验收**未发现**与 ADR-0002 冲突的真实缺陷，故无生产代码改动。三项残留边界已实测记录：
  (a) 若内容被替换但字节数不变且 `mtime` 被还原，扫描层的变化提示不会触发，此时若扫描被中断，位置会
  在**下一次完整扫描**前保留上一次观测（旧 `asset_id`/旧 size/mtime）；ADR-0002 §5.1 明确 size/mtime
  只是快速提示，且每次完整扫描都重算完整 SHA-256（`test_same_size_replacement_window_*` 已实测纠正），
  该窗口内 `last_successful_scan` 不前进、不会静默成功；若要缩短到「中断窗口内立即解除绑定」，属于改变
  缺失/身份判定边界，必须先交 C2 更新 ADR-0002。
  (b) 「断盘」用真实重命名/移走来源根目录模拟，**不是**真实外接盘/网络盘的卷级故障
  （`ERROR_NOT_READY`、USB 意外拔出、SMB 断链仍未在真实硬件上验证）。
  (c) 重映射只验证到夹具层（受控 `source_roots.root_path` 更新 + 重扫的身份/位置/产物不变）；
  `POST /sources/{id}/remap` 的**产品能力**（新根校验、来源重叠复查、映射审计、API 契约）仍缺，
  该项产品层入口标记为 `VALIDATION PENDING — 缺产品能力，不缺验收能力`。
  跨进程写者约束沿用 P1-02 记录，本任务未新增承诺。
- **P1-04**: the local session lives in memory for the process lifetime (no TTL, no logout, no LAN auth); the
  bootstrap credential file's Windows DACL could not be verified here (`icacls` was denied in this sandbox, so
  the tightening stays best-effort); the API and the scan worker use two SQLite connections in one process and only
  a sequential-overlap window was stress-tested; media generation still runs on the event loop thread; `GET /stats`
  is O(assets). A C1 review of the packaging/migration-check fix is recommended; see report §6 and §8.
- No file-id move acceleration: a move is only proven by full SHA-256, so it leaves "new location PRESENT + old position
  MISSING" with the same `asset_id`. Explicitly allowed by the P1-02 card; revisit only with a sound identity argument.
- A file that disappears between enumeration and content confirmation still classifies as `FILE_READ_ERROR` → `BLOCKED`
  (conservative: never converted into a deletion conclusion). A re-run of the same `scan_run` completes it.
- Files whose `mtime` is later than the wall clock (clock skew, wrong camera date) skip the quiet-period check and rely on
  the stat pair around the hash only.
- Throughput is bounded by per-file short write transactions (2 observe/bind writes + throttled progress): ~375 files/s on
  4 KB synthetic files in this environment. Batching commits is a future tuning item that must not break ADR-0002 §8.
- Single-writer coordinator is in-process; cross-process writers use WAL + `BEGIN IMMEDIATE` + busy timeout but have not
  been stress-tested. Recovery always re-enumerates the whole source (no directory cursor), as ADR-0002 §5.3 allows.
- `assets.width/height/format` are still NULL: P1-03 delivers the orientation-normalised size and source format in the
  batch summary and the derived facts can be read from the artifact; persisting them needs a documented additive
  migration and a C2 decision (P1-03 report §2/§8).
- P1-03 refuses TIFF/RAW/HEIF/AVIF as `FORMAT_UNSUPPORTED` even though Pillow can read TIFF; multi-page, RAW and colour
  semantics are unverified, so no support may be claimed before a dedicated task verifies it.
- No colour management in the media path: ICC is not applied, CMYK/16-bit inputs are converted naively to RGB and
  transparency is composited on white.
- Media generation decodes one asset at a time on one thread and uses JPEG DCT-scaled decode (intermediate always at
  least the target long edge). A bounded decode pool per architecture §6.4 is still a P2 item.
- `catalog media-status` is an O(assets) diagnostic (one reachability probe per pending asset plus a walk of the current
  cache version); no incremental status index yet. Media batches are untested under cross-process concurrency (the
  catalog is never written by the media path; identical content-addressed artifacts rely on atomic replace).
- GTX 1660 6 GB remains **unverified on real hardware** (P4-07/P4-08 release gate). Model GPU execution is verified on the
  current RTX 4060 Laptop GPU; earlier P0 limits for InsightFace/RapidOCR, cold CUDA start and human-labelled semantic
  quality remain carried forward.
- Two historical P0 record inconsistencies remain recorded but unedited; see the P0-07 report §3.1.
- **P1-05 historical redaction gap is closed by P1-05A** (`b708847`); its earlier report remains historical evidence.
  The front end has no automated DOM tests (no Node/npm chain introduced); its behaviour
  rests on the static contract assertions consolidated by P1-05B in `tests/unit/test_api_frontend.py` plus the
  repeatable manual checklist `docs/diagnostics/frontend-smoke-checklist.md`. Narrow-viewport/200% zoom was
  proxied by a 640×900 viewport (no horizontal overflow,
  3-column reflow), not by real OS zoom. `time_desc` sorts by insertion time only (`taken_at` would need an
  ADR-0002 §7 additive migration + C2). A fully offline asset shows no relative path in the grid
  (`current_location` is null once every position is MISSING); the detail view still lists every position.

  两个历史非阻塞后续项均已完成，保留任务卡／报告供追溯（不再作为下一任务）：

  - `docs/tasks/P1-05B.md` — 前端静态契约断言集中化 + `%TEMP%` 夹具脚本 + 浏览器 smoke 核对清单
    （不引入 Node/npm），验收等级 A。**状态：PASS**，见
    [docs/reports/P1-05B-frontend-verification-base.md](reports/P1-05B-frontend-verification-base.md)。
  - `docs/tasks/P1-05A.md` — 本机路径脱敏补强（repr 转义与多拼写 + 未登记路径兜底），推荐
    **C1 / GPT-5.6 Terra / medium**，验收等级 **B**；唯一允许改动的生产文件是
    `src/photo_search/api/errors.py` 的 `Redactor`，页面守卫保留并加回归断言。**状态：PASS**；见
    [P1-05A 报告](reports/P1-05A-local-path-redaction.md)。
  - `taken_at`（EXIF 拍摄时间）排入 **P4-00A（C2 契约）→ P4-00B（DS-H 实现）**；
    通过 ADR-0002 §7 的增量迁移路径，不改变现有加入时间排序。

## GPU status

Model GPU execution is verified on this machine (RTX 4060 Laptop GPU / 8188 MiB / driver 616.56 / CUDA UMD 13.4):
Chinese-CLIP image and text towers run on `CUDAExecutionProvider` with CPU fallback disabled, the worker defaults to CUDA,
ViT-L/14 also ran fully on CUDA, and the packaged app reproduced the CUDA path from a Chinese/space path offline.

Still open for later phases: real GTX 1660 6 GB validation; bounded decode pool and thumbnail/subsample prep (P1-03);
scheduler handling of cold GPU start; explicit OCR device decision; InsightFace CPU-operator re-check; and a
human-labelled semantic-search holdout.

## Current task / next execution step

**P1-06 — 重复、移动、替换、断盘、重映射、重启验收：PASS.** 见
[P1-06 报告](reports/P1-06-identity-lifecycle-acceptance.md)。

**P2-01 — JobRequest/Result、ProcessorSpec、device_policy、RuntimeCapabilities、EmbeddingAdapter、
SearchProfile 契约.**

- Status: **READY / CURRENT TASK**. P1-01 … P1-06 are accepted; P2-01 has no unfinished dependency.
- Task card: `docs/tasks/P2-01.md`.
- Recommended execution model: **C2 / GPT-5.6 Sol / high**（V3.1 §7「C2 冻结契约」，§11 高风险任务）。
- Goal: freeze the minimum P2 protocol before any implementation — JobRequest/JobResult identity, ProcessorSpec
  fields, the three `device_policy` values with the no-implicit-fallback device eligibility rules,
  RuntimeCapabilities, the EmbeddingAdapter/RerankerAdapter method contracts, and the immutable SearchProfile with
  its atomic active pointer and `space_id` isolation — as `docs/decisions/ADR-0003-*.md` plus a machine-readable
  JSON Schema companion and a contract test. **No production code and no table/migration change in this task.**
- 地点前置扩展：无模型 CPU_PREFERRED processor、非向量 artifact、CatalogFilterRevision；地点详细
  DDL/API/人工状态在下一任务 **P2-01A** 冻结，本卡完成后生成 `docs/tasks/P2-01A.md`，不直接生成 P2-02。
- Validation level: **A** (`uv run pytest -q tests/unit/test_p2_contracts.py` + `uv run ruff check .` +
  `git diff --check`); it escalates to D if any production code, migration or dependency is touched.
- Every verification fixture must stay inside `%TEMP%`; never read a real photo library without explicit user authorization.

P1-05A 已关闭脱敏缺口；P1-04 打包/迁移检查的建议复核保留到发行检查。P1-06 §5 product remap
排入 P4-05A/B，拍摄时间排入 P4-00A/B，HEIC 排入 P4-00C；均不是 P2-01 的前置依赖。

Model switching remains manual; the repository only records the recommended model per stage.
