# Development status

## Current state

**P0 is complete. P1-01 has completed two DS-H implementation passes, but the C2 final focused re-review found 2 remaining recovery-semantic blockers. P1-01 is NOT PASS.**

History:

- Initial P1-01 implementation: `92ca7566aeeb007c1e442f19612b932412c07a73`, evidence in [docs/reports/P1-01-catalog-identity-and-scan.md](reports/P1-01-catalog-identity-and-scan.md).
- First C2 focused review: `83adb65`, report [docs/reports/P1-01-c2-focused-review.md](reports/P1-01-c2-focused-review.md), result FAIL / FIX REQUIRED.
- DS-H corrective pass: `ce6f56c57fbf96203a0d81d1eb975fa27f469ac5`, report [docs/reports/P1-01-c2-remediation.md](reports/P1-01-c2-remediation.md), recorded D-level result `117 passed, 1 skipped`.
- C2 final focused re-review: [docs/reports/P1-01-c2-final-rereview.md](reports/P1-01-c2-final-rereview.md), result **FAIL / FIX REQUIRED**.

The `ce6f56c5` pass successfully closed the previous B1 write-qualification issue, B3 schema-preflight bypass, R1 generic scan-run self-loop and R2 cross-source `last_seen_scan_id` validation. Stale hashes are now discarded after a post-hash stat and retryable instability is represented explicitly.

The final C2 re-review found two remaining blockers:

1. **Retry/lease exhaustion terminal persistence and scan/job linkage.** In the real `execute_scan()` path, expired-lease takeover can write job `FAILED` and then raise inside `Catalog.write()`, causing that FAILED update to be rolled back. Separately, when `jobs.finish(... RETRY_WAIT)` reaches `max_attempts`, it can internally choose `FAILED` while `_requeue()` leaves the corresponding scan_run `RUNNING`. Exhaustion must persist atomically and catalog_scan job/scan_run terminal states must remain linked.
2. **Same-scan recovery coverage is not execution-specific.** A recovered execution reuses the same logical `scan_id`, while `file_locations.last_seen_scan_id` also uses that scan_id as the only coverage marker. Positive facts written by an earlier partial execution can therefore make a later complete recovery execution incorrectly treat deleted/renamed files as seen, or leave stale unbound rows that block final success. Missing reconciliation must be based on the current complete execution, not any previous execution of the same scan_run.

ADR-0002 is unchanged by this re-review; these are implementation defects under the existing frozen contract.

Current architecture: `个人图片搜索系统_V3.0_开发主架构.md`, requirement revision 3.  
Current execution strategy: `个人图片搜索系统_V3.1_Codex_DeepSeek_开发执行策略.md`, revision 2 (2026-09-11 mandatory CUDA / GTX 1660 minimum update).

## Latest verification

The following results are the **DS-H `ce6f56c5` pre-final-review baseline**. They remain regression evidence but do not establish P1-01 PASS because C2 found the two recovery-semantic blockers above.

| Check | Result |
|---|---|
| `uv run pytest -o addopts="" -q tests/unit tests/integration` | exit 0, `117 passed, 1 skipped in 77.50s` (DS-H post-fix baseline; final C2 re-review found uncovered production-path recovery cases) |
| `uv run ruff check .` / `git diff --check` | clean / clean at DS-H baseline |
| `python -m photo_search catalog init|add-source|scan|status --json` | exit 0 at DS-H baseline |
| `python -m photo_search catalog scan --source-id 9 --json` | exit 1, `SOURCE_NOT_FOUND` at DS-H baseline |
| ADR-0002 §12 original minimum matrix | the previous explicit #6/#13 failures are materially fixed; final PASS is still blocked by the newly exposed recovery correctness issues |
| C2 final focused re-review | **FAIL / FIX REQUIRED — F1 exhaustion persistence/linkage, F2 same-scan execution coverage** |

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

**P1-01 — stage 6: DS-H final recovery correction.**

- Current C2 result: **FINAL RE-REVIEW FAIL / FIX REQUIRED**.
- Required input: `docs/reports/P1-01-c2-final-rereview.md`, then the prior remediation report and ADR-0002 only as needed.
- Recommended execution model: **DS-H / DeepSeek V4.1 Flash / 高推理**.
- Scope is intentionally narrow: fix only **F1 exhaustion persistence + job/scan_run terminal linkage** and **F2 per-execution recovery coverage under the same logical scan_id**, plus the required regression tests.
- Minimum new tests: real `execute_scan()` expired-lease exhaustion to persistent FAILED; repeated `INPUT_UNSTABLE` exhaustion to job+scan FAILED; interrupted partial scan followed by deletion/rename and same-scan-id recovery with correct MISSING outcome (including stale unbound variant if practical).
- Do not change asset/source identity, `(source_id,path_key)`, five-field job identity, schema compatibility/migration rules, original-file read-only rule, or the rule that only a complete successful scan produces missing conclusions.
- After DS-H fixes: rerun the D-level suite, update P1-01 evidence, then manually switch back to **C2 / GPT-5.6 Sol / high** for a final short re-review.

**P1-02 is BLOCKED BY P1-01 and must not start until C2 explicitly marks P1-01 PASS.**

Model switching remains manual. The repository records the recommended role/model for each stage; it does not switch clients or models itself.
