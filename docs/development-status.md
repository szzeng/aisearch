# Development status

## Current state

**P0 is complete. P1-01 is PASS (C2 final short re-review). P1-02 is PASS. P1-03 is PASS. P1-04 is now
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

Current architecture: `个人图片搜索系统_V3.0_开发主架构.md`, requirement revision 3.
Current execution strategy: `个人图片搜索系统_V3.1_Codex_DeepSeek_开发执行策略.md`, revision 2 (2026-09-11 mandatory CUDA / GTX 1660 minimum update).

## Latest accepted verification

Produced by DS-L on the P1-03 implementation (see the P1-03 report for per-scenario evidence; no C2 review was run for P1-03).

| Check | Result |
|---|---|
| `uv run pytest -q tests/unit tests/integration` | exit 0, `180 passed, 1 skipped in 111.20s` |
| focused P1-03 suites (`tests/unit/test_media_decode.py`, `test_media_cache.py`, `tests/integration/test_catalog_media.py`) | exit 0, `27 passed` / `19 passed` |
| `uv run ruff check .` / `git diff --check` | clean / clean |
| P1-03 media scenarios 1-10 (EXIF 1-8 with mirror variants, truncated/0-byte/non-image/TIFF-RAW, kill+resume, cache wipe, idempotency, all-missing/offline, Chinese+space+>220-char paths and read-only originals, disk shortage, 200-1000 file scale) | all executed on `%TEMP%` fixtures; mapped to named tests in the report §6 |
| P1-03 artifact-publish fault injections (`os.replace` failure, `interrupt_after`, cancel/Ctrl-C, leftover temp, rerun) | all PASS: no READY half-product, no duplicate or recomputed artifact |
| Pillow capability probe (Pillow 11.3.0, `probe_capabilities()`) | WebP save True, TIFF read True (refused by policy), JPEG True, zlib True, `exif_transpose` True, `MAX_IMAGE_PIXELS` = 89,478,485 |
| Scale/memory (`tools/diagnostics/media_scale_probe.py`) | 200 files 1600x1200: 8.88 ms/file, 112.6 files/s, 3,499 B/artifact, host peak 43.5 MiB. 500 files 1280x960: 7.17 ms/file, 3,587 B/artifact. 20 files 4032x3024 grainy: 39.25 ms/file, 6,285 B/artifact, preview 167.55 ms / 597 KB. 108 MP JPEG: thumbnail 36 ms, preview 97 ms, media-stage peak delta 53.6 MiB |

P1-03 shipped contract: thumbnail `catalog-thumbnail@1` (long edge 320, JPEG q85, LANCZOS, never enlarged) and preview
`catalog-preview@1` (long edge 1600, JPEG q88, lazy). Layout
`<data_root>/{thumbnails,previews}/<processor_id>/<sha[:2]>/<sha[2:4]>/<sha>.jpg`, failure records under
`<data_root>/cache/media/...`, atomic same-directory write + fsync + `os.replace`. No schema change (still
`schema_version = 1`), no new dependency.

## Carried-forward limits / follow-ups

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

**P1-04 — sources/assets API、受控文件访问、本机会话保护.**

- Status: **READY / CURRENT TASK**. P1-01, P1-02 and P1-03 are accepted; P1-04 has no unfinished dependency.
- Task card: `docs/tasks/P1-04.md`.
- Recommended execution model: **DS-H / DeepSeek V4.1 Flash / 高推理**; the Windows path and local-access boundaries
  should get a focused **C1 / GPT-5.6 Terra / medium** check.
- Goal: expose the accepted catalog and media results over the local HTTP API (`sources`, `assets`, `stats`,
  `capabilities`, thumbnail/preview/original byte endpoints) behind a local session, with controlled file access that
  only ever resolves registered source positions — without touching the frozen ADR-0002 identity/lease/scan semantics.
- Validation level: **B** (`uv run pytest -q tests/unit tests/integration` + ruff + diff-check); packaging or launcher
  changes would additionally require C-level packaging smoke tests.
- Every verification fixture must stay inside `%TEMP%`; never read a real photo library without explicit user authorization.

Model switching remains manual; the repository only records the recommended model per stage.
