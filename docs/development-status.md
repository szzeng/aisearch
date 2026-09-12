# Development status

## Current state

**P0 is complete. P1-01 is PASS (C2 final short re-review). P1-02 is PASS. P1-03 is now READY / CURRENT TASK.**

P1-01 history (frozen contract `docs/decisions/ADR-0002-catalog-identity-migration-scan-contract.md`):

- Implementation `92ca7566`, three C2 review rounds (`83adb65` FAIL, `8393583` FAIL) and two corrective passes
  (`ce6f56c5`, `f0b4f402`), final C2 short re-review PASS in [docs/reports/P1-01-c2-final-pass.md](reports/P1-01-c2-final-pass.md).
  Accepted closing baseline: `e39a40c`.
- Persistent positive writes are submission-qualified, retry/lease exhaustion commits job/scan_run terminal state in one
  transaction, schema compatibility cannot be bypassed, stale hashes cannot produce success, and same-scan recovery no
  longer inherits observations from an older execution.

P1-02 (streaming scan, stability waiting, hash before/after stat, missing rules) is recorded in
[docs/reports/P1-02-streaming-scan-stability-and-missing.md](reports/P1-02-streaming-scan-stability-and-missing.md).

Current architecture: `个人图片搜索系统_V3.0_开发主架构.md`, requirement revision 3.
Current execution strategy: `个人图片搜索系统_V3.1_Codex_DeepSeek_开发执行策略.md`, revision 2 (2026-09-11 mandatory CUDA / GTX 1660 minimum update).

## Latest accepted verification

Produced by DS-H on the P1-02 implementation (see the P1-02 report for per-scenario evidence; no C2 review was run for P1-02).

| Check | Result |
|---|---|
| `uv run pytest tests/unit tests/integration` (equivalent to the card's `-q` form) | exit 0, `133 passed, 2 skipped in 98.21s` |
| `.venv\Scripts\python.exe -m pytest -o addopts="" -q tests/unit tests/integration` | exit 0, `134 passed, 1 skipped in 87.11s` (junction fixture only creatable outside the `uv run pytest` process context) |
| `uv run ruff check .` / `git diff --check` | clean / clean |
| P1-02 D-level scenarios 1-8 (stability wait, hash before/after, kill+resume, enumeration failure, missing atomicity, move/rename, streaming scale, path-key collision) | all executed on temporary fixtures; mapped to named tests in the report |
| Scale/memory (`tools/diagnostics/scan_scale_probe.py`) | 5k files: 29.2 MB peak working set, 379 files/s; 20k files: 30.1 MB peak, 374.6 files/s; `reset_execution_coverage` over 20000 positions = 31 ms |

P1-02 shipped defaults: stability window **1500 ms**, wait limit **30000 ms**, chunked position reads (256 rows),
progress 0.0-1.0 monotone, `MISSING`/`last_successful_scan` still only in the single final success transaction.
No schema change (still `schema_version = 1`), no new dependency.

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
- `assets.width/height/format` remain NULL (ADR-0002 §3.5); P1-03 owns orientation/decoding.
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

**P1-03 — 方向、缩略图/预览、缓存分片、坏图隔离.**

- Status: **READY / CURRENT TASK**. P1-01 and P1-02 are accepted; P1-03 has no unfinished dependency.
- Task card: `docs/tasks/P1-03.md`.
- Recommended execution model: **DS-L / DeepSeek V4.1 Flash / 低推理** (complex media compatibility can escalate to DS-H).
- Goal: turn catalogued assets into browseable, orientation-correct thumbnails and previews with a sharded rebuildable
  cache, atomic replace and honest isolation of unreadable/unsupported files — without touching the frozen P1-01/P1-02
  identity, lease, scan and missing semantics.
- Validation level: **B** (`pytest -q tests/unit tests/integration` + ruff + diff-check); if the task adds a schema
  migration it must additionally run the D-level migration/interrupted-write fault injections.
- Every verification fixture must stay inside `%TEMP%`; never read a real photo library without explicit user authorization.

After P1-03 implementation and validation, persist its report/status and create/update the P1-04 task card per
`docs/tasks/P1-03.md`. Model switching remains manual; the repository only records the recommended model per stage.
