# Development status

## Current state

**P0 is complete. P1-01 is PASS (C2 final short re-review). P1-02 is PASS. P1-03 is PASS. P1-04 is PASS
(B level plus a real-port smoke and a C-level packaged smoke). P1-05 is now READY / CURRENT TASK.**

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

Current architecture: `个人图片搜索系统_V3.0_开发主架构.md`, requirement revision 3.
Current execution strategy: `个人图片搜索系统_V3.1_Codex_DeepSeek_开发执行策略.md`, revision 2 (2026-09-11 mandatory CUDA / GTX 1660 minimum update).

## Latest accepted verification

Produced by DS-H on the P1-04 implementation (see the P1-04 report for per-scenario evidence; C1 review of the
packaging fix in §6 is still recommended, no C2 review was run).

| Check | Result |
|---|---|
| `uv run pytest -q tests/unit tests/integration` | exit 0, `252 passed, 1 skipped in 122.25s` |
| focused P1-04 suites (`tests/unit/test_api_*.py`, `tests/integration/test_api_*.py`) | exit 0, `72 passed` |
| `uv run ruff check .` / `git diff --check` | clean / clean |
| `uv lock --check` | exit 0, `Resolved 95 packages` |
| Measured versions (`uv run python -c ...`) | Python 3.12.10, fastapi 0.141.1, starlette 1.6.0, pydantic 2.13.5, uvicorn 0.52.4, httpx 0.28.1 |
| P1-04 API scenarios 1-10 (session/CSRF/Origin/Host, sources incl. overlap + data-root rejection, 202 scan + reuse + interrupted-lease recovery, 26-asset keyset paging, thumbnail/preview/original bytes and Range, `..`/absolute/UNC/drive/junction traversal refusals, offline + replaced original, Chinese/space/>220-char paths with read-only originals, `SCHEMA_TOO_NEW` without write side effects, real-port smoke) | all executed on `%TEMP%` fixtures; mapped to named tests in report §5 |
| Real-port launcher smoke (`tests/integration/test_api_server.py`) | health 200, session 200 with `HttpOnly; Path=/`, unauthenticated read 401, CSRF-protected `POST /sources` 201, access log `token=<redacted>`, non-loopback bind exits 2 without creating a credential |
| C-level packaged smoke (`tools/packaging/build_onedir.py` + `tools/diagnostics/packaged_api_smoke.py`) | build exit 0 in 57 s; bundle copied to `%TEMP%\P1-04 打包 API 验收\PhotoSearch 中文 空格`; `status=ok` with health/session/scan/thumbnail/offline-original/capabilities all as specified; startup 1.02 s |
| C-gate finding fixed | the PyInstaller build did not collect `catalog/migrations/*.sql`, so a frozen build silently created an **empty catalog** and failed on the first query; `build_onedir.py` now uses `--collect-data photo_search` and `schema.migration_files()` requires the full `0001..supported` set (new unit test) |

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

## GPU status

Model GPU execution is verified on this machine (RTX 4060 Laptop GPU / 8188 MiB / driver 616.56 / CUDA UMD 13.4):
Chinese-CLIP image and text towers run on `CUDAExecutionProvider` with CPU fallback disabled, the worker defaults to CUDA,
ViT-L/14 also ran fully on CUDA, and the packaged app reproduced the CUDA path from a Chinese/space path offline.

Still open for later phases: real GTX 1660 6 GB validation; bounded decode pool and thumbnail/subsample prep (P1-03);
scheduler handling of cold GPU start; explicit OCR device decision; InsightFace CPU-operator re-check; and a
human-labelled semantic-search holdout.

## Current task / next execution step

**P1-05 — 来源页、时间浏览、详情、离线提示.**

- Status: **READY / CURRENT TASK**. P1-01 … P1-04 are accepted; P1-05 has no unfinished dependency.
- Task card: `docs/tasks/P1-05.md`.
- Recommended execution model: **DS-L / DeepSeek V4.1 Flash / 低推理**.
- Goal: ship the first usable local frontend over the accepted P1-04 API — a source page (register / scan /
  progress), a time-ordered browsing grid, an asset detail view (locations + media state), and honest offline
  hints — as a dependency-free static bundle served by the app, without weakening the session/CSRF/path boundary.
- Validation level: **B** (`uv run pytest -q tests/unit tests/integration` + ruff + diff-check) plus a real-port
  browser smoke; the static assets change the packaged bundle as well, so the C-level packaged smoke from P1-04 may
  be re-run (`tools/diagnostics/packaged_api_smoke.py` is the entry point).
- Every verification fixture must stay inside `%TEMP%`; never read a real photo library without explicit user authorization.

An optional **C1 / GPT-5.6 Terra / medium** review of the P1-04 packaging/migration-check fix (report §6) is
recommended but is not a dependency of P1-05.

Model switching remains manual; the repository only records the recommended model per stage.
