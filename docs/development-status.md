# Development status

## Current state

**P0 is complete. P1-01 is PASS (C2 final short re-review). P1-02 is PASS. P1-03 is PASS. P1-04 is PASS
(B level plus real-port/C-level packaged smokes and the C1 Windows-path/local-access review). P1-05 is PASS
(B level + C-level packaged smoke + a recorded real-browser smoke); its non-blocking follow-up P1-05B
(front-end verification base) is also PASS. P1-06 is now READY / CURRENT TASK.**

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
[P1-05A 报告](reports/P1-05A-local-path-redaction.md)。主线当前任务仍为 P1-06。

Current architecture: `个人图片搜索系统_V3.0_开发主架构.md`, requirement revision 3.
Current execution strategy: `个人图片搜索系统_V3.1_Codex_DeepSeek_开发执行策略.md`, revision 2 (2026-09-11 mandatory CUDA / GTX 1660 minimum update).

## Latest accepted verification

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
- **P1-05**: `GET /assets/{id}` media failure messages can still carry an absolute path because the stored
  Pillow message uses a repr-escaped Windows path (`C:\\Users\\…`) that `errors.Redactor` does not match; the page
  withholds such messages, but a server-side fix needs a C1/C2-reviewed follow-up (P1-05 report §8.1; task card
  `docs/tasks/P1-05A.md`). The front end has no automated DOM tests (no Node/npm chain allowed); its behaviour
  rests on the static contract assertions consolidated by P1-05B in `tests/unit/test_api_frontend.py` plus the
  repeatable manual checklist `docs/diagnostics/frontend-smoke-checklist.md`. Narrow-viewport/200% zoom was
  proxied by a 640×900 viewport (no horizontal overflow,
  3-column reflow), not by real OS zoom. `time_desc` sorts by insertion time only (`taken_at` would need an
  ADR-0002 §7 additive migration + C2). A fully offline asset shows no relative path in the grid
  (`current_location` is null once every position is MISSING); the detail view still lists every position.

  两个**非阻塞后续项**已按上述分工写成正式任务卡（都不改变 P1-06 作为下一主线任务）：

  - `docs/tasks/P1-05B.md` — 前端静态契约断言集中化 + `%TEMP%` 夹具脚本 + 浏览器 smoke 核对清单
    （不引入 Node/npm），验收等级 A。**状态：PASS**，见
    [docs/reports/P1-05B-frontend-verification-base.md](reports/P1-05B-frontend-verification-base.md)。
  - `docs/tasks/P1-05A.md` — 本机路径脱敏补强（repr 转义与多拼写 + 未登记路径兜底），推荐
    **C1 / GPT-5.6 Terra / medium**，验收等级 **B**；唯一允许改动的生产文件是
    `src/photo_search/api/errors.py` 的 `Redactor`，页面守卫保留并加回归断言。**状态：PASS**；见
    [P1-05A 报告](reports/P1-05A-local-path-redaction.md)。
  - `taken_at`（EXIF 拍摄时间）不写成实现卡：它属于数据语义决策，必须先交
    **C2 / GPT-5.6 Sol / high** 决策并通过 ADR-0002 §7 的加列迁移路径。

## GPU status

Model GPU execution is verified on this machine (RTX 4060 Laptop GPU / 8188 MiB / driver 616.56 / CUDA UMD 13.4):
Chinese-CLIP image and text towers run on `CUDAExecutionProvider` with CPU fallback disabled, the worker defaults to CUDA,
ViT-L/14 also ran fully on CUDA, and the packaged app reproduced the CUDA path from a Chinese/space path offline.

Still open for later phases: real GTX 1660 6 GB validation; bounded decode pool and thumbnail/subsample prep (P1-03);
scheduler handling of cold GPU start; explicit OCR device decision; InsightFace CPU-operator re-check; and a
human-labelled semantic-search holdout.

## Current task / next execution step

**P1-06 — 重复、移动、替换、断盘、重映射、重启验收.**

- Status: **READY / CURRENT TASK**. P1-01 … P1-05 are accepted; P1-06 has no unfinished dependency.
- Task card: `docs/tasks/P1-06.md`.
- Recommended execution model: **DS-H / DeepSeek V4.1 Flash / 高推理**.
- Goal: prove the frozen ADR-0002 identity/location/missing rules end-to-end on real fixtures: duplicate content
  shares one asset with several positions, a move leaves "new position PRESENT + old position MISSING" on the same
  `asset_id`, a replaced file is not silently re-bound, an unmounted/offline volume never turns into a deletion
  conclusion, a re-mapped root keeps identity and position binding, and a restart mid-scan recovers the same
  `scan_run` without duplicating rows.
- Validation level: **D** (`uv run pytest -q tests/unit tests/integration` + ruff + diff-check + the required
  fault-injection/recovery scenarios). Packaging is only required if the task touches packaged resources.
- Every verification fixture must stay inside `%TEMP%`; never read a real photo library without explicit user authorization.

Optional follow-ups recorded but not dependencies of P1-06: a **C1 / GPT-5.6 Terra / medium** review of the P1-05
§8.1 redaction gap (and, if still open, the P1-04 packaging/migration-check fix), and a **C2 / GPT-5.6 Sol / high**
decision before adding any `taken_at`/EXIF-capture-time column.

Model switching remains manual; the repository only records the recommended model per stage.
