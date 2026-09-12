# Development status

## Current state

**P0 is complete. P1-01 is PASS after the C2 final short re-review. P1-02 is now READY / CURRENT TASK.**

P1-01 history:

- Initial implementation: `92ca7566aeeb007c1e442f19612b932412c07a73`, evidence in [docs/reports/P1-01-catalog-identity-and-scan.md](reports/P1-01-catalog-identity-and-scan.md).
- First C2 focused review: `83adb65`, [docs/reports/P1-01-c2-focused-review.md](reports/P1-01-c2-focused-review.md), result FAIL / FIX REQUIRED.
- First DS-H corrective pass: `ce6f56c57fbf96203a0d81d1eb975fa27f469ac5`, [docs/reports/P1-01-c2-remediation.md](reports/P1-01-c2-remediation.md), recorded `117 passed, 1 skipped`.
- C2 final focused re-review: `8393583`, [docs/reports/P1-01-c2-final-rereview.md](reports/P1-01-c2-final-rereview.md), found F1/F2 and returned FAIL / FIX REQUIRED.
- Final DS-H corrective pass: `f0b4f402e020e327748d9feef292447cd6301757`, [docs/reports/P1-01-c2-final-remediation.md](reports/P1-01-c2-final-remediation.md), recorded `122 passed, 1 skipped`.
- C2 final short re-review: [docs/reports/P1-01-c2-final-pass.md](reports/P1-01-c2-final-pass.md), result **PASS**.

P1-01's frozen contract is `docs/decisions/ADR-0002-catalog-identity-migration-scan-contract.md`. The final accepted implementation closes B1-B4/R1-R2 and F1/F2: persistent positive writes are submission-qualified, retry/lease exhaustion commits job/scan_run terminal state consistently, schema compatibility cannot be bypassed, stale hashes cannot produce success, and same-scan recovery coverage no longer inherits observations from an older execution.

Current architecture: `个人图片搜索系统_V3.0_开发主架构.md`, requirement revision 3.  
Current execution strategy: `个人图片搜索系统_V3.1_Codex_DeepSeek_开发执行策略.md`, revision 2 (2026-09-11 mandatory CUDA / GTX 1660 minimum update).

## Latest accepted verification

The execution results below were produced by DS-H on the final P1-01 corrective pass. C2 performed the final static focused review and did not independently rerun the suite on the user's Windows machine.

| Check | Result |
|---|---|
| `uv run pytest -o addopts="" -q tests/unit tests/integration` | exit 0, `122 passed, 1 skipped in 75.38s` (final P1-01 DS-H baseline) |
| `uv run ruff check .` / `git diff --check` | clean / clean |
| F1 production-path tests | expired-lease exhaustion, unstable-input exhaustion and generic scheduler exhaustion all keep job/scan_run terminal state linked |
| F2 production-path tests | same-scan recovery correctly marks deleted positions MISSING and ignores stale unbound coverage from a killed execution |
| C2 final short re-review | **PASS** |

## Carried-forward limits / follow-ups

- P1-02 must measure the cost of `reset_execution_coverage()` at realistic catalog sizes. It currently performs a source/scan-marker UPDATE when a new execution is claimed; correctness is accepted for P1-01, while large-catalog transaction duration remains a performance follow-up.
- New P1-02 code that directly uses `jobs.claim_job()` must preserve the accepted exhaustion transaction boundary; consider hardening the API if additional callers make misuse likely.
- GTX 1660 6 GB remains **unverified on real hardware** (P4-07/P4-08 release gate). Model GPU execution is verified on the current RTX 4060 Laptop GPU; earlier P0 limitations for InsightFace/RapidOCR, cold CUDA start and human-labelled semantic quality remain carried forward.
- The single-writer coordinator is in-process; cross-process writers use WAL + `BEGIN IMMEDIATE` + busy timeout but have not been stress-tested.
- `format/width/height` remain NULL in P1-01 as allowed by ADR-0002 §3.5.
- Two historical P0 record inconsistencies remain recorded but unedited; see the P0-07 report §3.1.

## GPU status

Model GPU execution is verified on this machine (RTX 4060 Laptop GPU / 8188 MiB / driver 616.56 / CUDA UMD 13.4): Chinese-CLIP image and text towers run on `CUDAExecutionProvider` with CPU fallback disabled, the worker defaults to CUDA, ViT-L/14 also ran fully on CUDA, and the packaged app reproduced the CUDA path from a Chinese/space path offline.

Still open for later phases: real GTX 1660 6 GB validation; bounded decode pool and thumbnail/subsample prep; scheduler handling of cold GPU start; explicit OCR device decision; InsightFace CPU-operator re-check; and a human-labelled semantic-search holdout.

## Current task / next execution step

**P1-02 — 流式扫描、稳定等待、hash 前后校验、缺失判定.**

- Status: **READY / CURRENT TASK**. The P1-01 C2 gate is satisfied.
- Task card: `docs/tasks/P1-02.md`.
- Recommended execution model: **DS-H / DeepSeek V4.1 Flash / 高推理**.
- Goal: upgrade the P1-01 minimal deterministic scanner into a real-library scanner with streaming/bounded-memory enumeration, stability waiting, hash-before/after stat validation, production recovery and complete-scan-only missing reconciliation.
- Keep ADR-0002 identity, unique-key, lease, submission-qualification and missing atomicity semantics frozen. Any required semantic change stops implementation and returns to **C2 / GPT-5.6 Sol / high**.
- Validation level: **D**. Use temporary fixtures only; never scan a real photo library without explicit user authorization.

After P1-02 implementation and D-level validation, persist its report/status and create/update the P1-03 task card per `docs/tasks/P1-02.md`.

Model switching remains manual. The repository records the recommended role/model for each stage; it does not switch clients or models itself.
