# Development status

## Current state

**P0 is complete. P1-01 implementation and initial D-level validation are complete, but C2 focused review is FAIL / FIX REQUIRED. P1-01 is NOT PASS.**

DS-H delivered the initial P1-01 catalog foundation in commit `92ca7566aeeb007c1e442f19612b932412c07a73`. The implementation evidence remains [docs/reports/P1-01-catalog-identity-and-scan.md](reports/P1-01-catalog-identity-and-scan.md). The C2 review is [docs/reports/P1-01-c2-focused-review.md](reports/P1-01-c2-focused-review.md).

The C2 review found four blocking contract violations:

1. Positive persistent scan writes (`observe_location` / content binding) are not re-qualified by the current job lease/revision/processor inside each write transaction, so an expired executor can leave persistent results before the final stale-lease rejection.
2. Runtime `attempts/max_attempts` behaviour does not implement ADR-0002 §6.2: ordinary retryable failure does not increment attempts, and expired-lease takeover does not enforce the retry ceiling.
3. `Catalog.open(migrate=False)` can bypass the read-only schema compatibility preflight and reach RW/WAL initialization before a `SCHEMA_TOO_NEW` rejection.
4. A stale hash/binding observation can remain pending while the scan still reaches the final success path; stale input must instead prevent `SUCCEEDED` and be re-queued/retried under the frozen contract.

Two narrower boundaries should be fixed in the same corrective pass: remove/gate the generic `scan_runs RUNNING -> RUNNING` self-loop, and enforce that `file_locations.last_seen_scan_id` belongs to the same source in the central transaction helper.

ADR-0002 has one C2 amendment from this review: when a database already has an active `-wal` before compatibility preflight, the truly read-only preflight may use or rebuild the derived WAL index / `-shm` solely to observe the database's current WAL state. This exception does not permit main-database writes, changes to the existing WAL, journal-mode switching, metadata writes, checkpoints, business writes, or any relaxation of `SCHEMA_TOO_NEW` rejection. A clean database with no pre-existing `-wal` must still not gain `-wal/-shm` as a side effect of preflight.

Current architecture: `个人图片搜索系统_V3.0_开发主架构.md`, requirement revision 3.  
Current execution strategy: `个人图片搜索系统_V3.1_Codex_DeepSeek_开发执行策略.md`, revision 2 (2026-09-11 mandatory CUDA / GTX 1660 minimum update).

## Latest verification

The following results are the DS-H implementation baseline and **predate the required C2 fixes**. They remain useful regression evidence but do not establish P1-01 PASS.

| Check | Result |
|---|---|
| `uv run pytest -o addopts="" -q tests/unit tests/integration` | exit 0, `106 passed, 1 skipped in 72.53s` (skip = pre-existing GPU opt-in case; P0 baseline was 51 + 1) |
| `uv run ruff check .` / `git diff --check` | clean / clean |
| `python -m photo_search catalog init|add-source|scan|status --json` | exit 0, structured JSON (schema v1, `state=SUCCEEDED`, assets/locations/missing counts) |
| `python -m photo_search catalog scan --source-id 9 --json` | exit 1, `{"status":"error","error":{"code":"SOURCE_NOT_FOUND",...}}` |
| `uv lock --check` | not re-run: no dependency change (standard library only) |
| P1-01 D-level fault injection (8 required scenarios) | all reported as executed in the implementation report; C2 found that some assertions encode the wrong frozen semantics and must be corrected |
| ADR-0002 §12 focused review | **14 PASS / 2 FAIL**; see the C2 review report |

## Carried-forward limits

- GTX 1660 6 GB remains **unverified on real hardware** (P4-07/P4-08 release gate). Model GPU execution is verified on the current RTX 4060 Laptop GPU; the earlier P0 limitations for InsightFace/RapidOCR, cold CUDA start and human-labelled semantic quality remain carried forward.
- P1-01 re-hashes every enumerated file on every pass. This is not the P1-02 production scanner: no stability window, no production hash-before/after double stat, no file-id move acceleration, no directory cursor and no scale-level missing reconciliation.
- The single-writer coordinator is in-process; cross-process writers use WAL + `BEGIN IMMEDIATE` + busy timeout but have not been stress-tested.
- `format/width/height` remain NULL in P1-01 as allowed by ADR-0002 §3.5.
- Two historical P0 record inconsistencies remain recorded but unedited; see the P0-07 report §3.1.

## GPU status

Model GPU execution is verified on this machine (RTX 4060 Laptop GPU / 8188 MiB / driver 616.56 / CUDA UMD 13.4): Chinese-CLIP image and text towers run on `CUDAExecutionProvider` with CPU fallback disabled, the worker defaults to CUDA, ViT-L/14 also ran fully on CUDA, and the packaged app reproduced the CUDA path from a Chinese/space path offline.

Still open for later phases: real GTX 1660 6 GB validation; bounded decode pool and thumbnail/subsample prep; scheduler handling of cold GPU start; explicit OCR device decision; InsightFace CPU-operator re-check; and a human-labelled semantic-search holdout.

## Current task / next execution step

**P1-01 — stage 4: DS-H corrective implementation.**

- Current review result: **C2 REVIEW FAIL — FIX REQUIRED**.
- Required input: `docs/reports/P1-01-c2-focused-review.md`, `docs/decisions/ADR-0002-catalog-identity-migration-scan-contract.md`, and the existing P1-01 task card/report.
- Recommended execution model: **DS-H / DeepSeek V4.1 Flash / 高推理**.
- Scope: fix only the P1-01 C2 findings and their tests. Do not change asset/source identity, `(source_id,path_key)`, five-field job identity, missing-detection atomic boundary, original-file read-only rule, or other frozen contracts.
- After code fixes: rerun the required D-level suite and update the P1-01 evidence.
- Then manually switch back to **C2 / GPT-5.6 Sol / high** for final focused re-review.

**P1-02 is BLOCKED BY P1-01 and must not start until P1-01 receives C2 PASS.**

Model switching remains manual. The repository records the recommended role/model for each stage; it does not switch clients or models itself.
