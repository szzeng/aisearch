# Development status

## Current state

**P0 is complete. P1-01 has completed three DS-H implementation passes; both C2 review rounds' findings are fixed and re-verified at D level. P1-01 is NOT PASS yet — the C2 final short re-review is pending.**

History:

- Initial P1-01 implementation: `92ca7566aeeb007c1e442f19612b932412c07a73`, evidence in [docs/reports/P1-01-catalog-identity-and-scan.md](reports/P1-01-catalog-identity-and-scan.md).
- First C2 focused review: `83adb65`, report [docs/reports/P1-01-c2-focused-review.md](reports/P1-01-c2-focused-review.md), result FAIL / FIX REQUIRED.
- DS-H corrective pass: `ce6f56c57fbf96203a0d81d1eb975fa27f469ac5`, report [docs/reports/P1-01-c2-remediation.md](reports/P1-01-c2-remediation.md), recorded D-level result `117 passed, 1 skipped`.
- C2 final focused re-review: [docs/reports/P1-01-c2-final-rereview.md](reports/P1-01-c2-final-rereview.md), result **FAIL / FIX REQUIRED**.
- DS-H second corrective pass (this pass): report [docs/reports/P1-01-c2-final-remediation.md](reports/P1-01-c2-final-remediation.md), result `122 passed, 1 skipped`, F1/F2 fixed.

The `ce6f56c5` pass successfully closed the previous B1 write-qualification issue, B3 schema-preflight bypass, R1 generic scan-run self-loop and R2 cross-source `last_seen_scan_id` validation. Stale hashes are now discarded after a post-hash stat and retryable instability is represented explicitly.

The two blockers reported by the final C2 re-review are now fixed:

1. **Retry/lease exhaustion terminal persistence and scan/job linkage (F1).** `execute_scan()` now catches `RETRY_EXHAUSTED` inside the claim transaction, writes `scan_run FAILED` there, commits, and re-raises only afterwards, so the job `FAILED` written by the queue is never rolled back. `_requeue()` reads the real state returned by `jobs.finish(...)` and mirrors a ceiling-triggered `FAILED` onto the `scan_run` in the same commit; the generic `claim_next()` path does the same through `jobs.fail_exhausted_leases()`. `QUEUED -> FAILED` / `BLOCKED -> FAILED` are legal scan_run transitions so the §3.3 linkage can always be satisfied.
2. **Same-scan recovery coverage is now execution-specific (F2).** Claiming an execution clears only the inherited `last_seen_scan_id` markers of that logical `scan_id` (`store.reset_execution_coverage`), so `last_seen_scan_id = scan_id` can only mean "seen by the current execution". Positions deleted before the recovery execution now reconcile to `MISSING`, and unbound leftovers from a killed execution no longer block the final success guard. Location rows, stat values, availability and content bindings are untouched, no schema change was needed, and the per-execution marker is machine recovery metadata only.

ADR-0002 is unchanged by this re-review; these are implementation defects under the existing frozen contract.

Current architecture: `个人图片搜索系统_V3.0_开发主架构.md`, requirement revision 3.  
Current execution strategy: `个人图片搜索系统_V3.1_Codex_DeepSeek_开发执行策略.md`, revision 2 (2026-09-11 mandatory CUDA / GTX 1660 minimum update).

## Latest verification

| Check | Result |
|---|---|
| `uv run pytest -o addopts="" -q tests/unit tests/integration` | exit 0, `122 passed, 1 skipped in 75.38s` (current pass; skip = pre-existing GPU opt-in case; earlier baselines were 117+1 and 106+1) |
| `uv run ruff check .` / `git diff --check` | clean / clean |
| `python -m photo_search catalog init|add-source|scan|status --json` | exit 0, structured JSON (schema v1, `state=SUCCEEDED`, counts, `unstable_retries`) |
| `python -m photo_search catalog scan --source-id 9 --json` | exit 1, `SOURCE_NOT_FOUND` |
| F1 production-path tests (C2 §5.1/§5.2) | `test_expired_lease_exhaustion_fails_job_and_scan_on_the_production_path`, `test_unstable_input_ceiling_fails_job_and_scan_together`, `test_scheduler_fails_an_exhausted_scan_job_and_syncs_the_scan_run` |
| F2 production-path tests (C2 §5.3) | `test_resume_marks_positions_deleted_before_recovery_as_missing`, `test_resume_ignores_unbound_leftovers_from_a_killed_execution` |
| C2 reviews | round 1 FAIL (B1–B4/R1/R2) and round 2 FAIL (F1/F2) both fixed; evidence in the two remediation reports |
| Remaining gate | C2 **final short re-review** must confirm F1/F2 closure before P1-01 can be PASS |

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

**P1-01 — stage 7: C2 final short re-review.**

- Current state: F1 and F2 are fixed with production-path tests; D-level suite green (`122 passed, 1 skipped`).
- Required input: `docs/reports/P1-01-c2-final-remediation.md` (fix + evidence), `docs/reports/P1-01-c2-final-rereview.md` (the findings), and the touched code (`catalog/store.py`, `jobs/queue.py`, `ingestion/scanner.py`).
- Recommended execution model: **C2 / GPT-5.6 Sol / high**.
- Scope: confirm F1/F2 closure and that no frozen semantics were relaxed. Do not re-implement; a finding that needs a semantic change goes back into ADR-0002 and then to DS-H.
- Output: the re-review conclusion recorded in the P1-01 reports; only then can P1-01 move to PASS.

**P1-02 is BLOCKED BY P1-01 and must not start until C2 explicitly marks P1-01 PASS.**

Model switching remains manual. The repository records the recommended role/model for each stage; it does not switch clients or models itself.
