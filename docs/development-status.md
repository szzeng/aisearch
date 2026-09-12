# Development status

## Current state

**P0 is complete. P1-01 C2 findings are fixed and re-verified at D level, but P1-01 is NOT PASS yet: the C2 final focused re-review is still pending.**

DS-H delivered the initial P1-01 catalog foundation in commit `92ca7566aeeb007c1e442f19612b932412c07a73`, the C2 review found four blocking violations plus two narrower boundaries (`83adb65`, [docs/reports/P1-01-c2-focused-review.md](reports/P1-01-c2-focused-review.md)), and the corrective pass is [docs/reports/P1-01-c2-remediation.md](reports/P1-01-c2-remediation.md). The original implementation evidence remains [docs/reports/P1-01-catalog-identity-and-scan.md](reports/P1-01-catalog-identity-and-scan.md).

Fixed in the corrective pass:

1. Every positive persistent write (`observe_location`, asset upsert + content binding) now re-qualifies the execution inside its own short transaction; late/expired lease, stale revision and processor mismatch abort without leaving results **and** without changing scan/job state. Lease renewal is now time-based (renews when less than half the lease remains).
2. `attempts/max_attempts` follows ADR-0002 §6.2: retryable failure and deterministic failure both consume an attempt, a retryable failure at `max_attempts` becomes `FAILED`, expired-lease takeover counts the lost execution and enforces the same ceiling, `BLOCKED` still consumes nothing.
3. The `migrate=False` bypass is removed: every catalog open path starts with the read-only compatibility preflight, so a higher `schema_version` is rejected before any RW/WAL initialisation.
4. A change detected after hashing discards that hash and re-observes the file; persistent instability now re-queues the scan request (`RETRY_WAIT`, scan_run stays `RUNNING`) instead of reaching `SUCCEEDED`, and the final success transaction refuses to run while any seen position is still unbound.
5. The generic `scan_runs RUNNING -> RUNNING` self-loop is gone (lease takeover keeps the state without a transition), and `last_seen_scan_id` / missing reconciliation now verify that the scan belongs to the same source.

ADR-0002 has one C2 amendment from this review: when a database already has an active `-wal` before compatibility preflight, the truly read-only preflight may use or rebuild the derived WAL index / `-shm` solely to observe the database's current WAL state. This exception does not permit main-database writes, changes to the existing WAL, journal-mode switching, metadata writes, checkpoints, business writes, or any relaxation of `SCHEMA_TOO_NEW` rejection. A clean database with no pre-existing `-wal` must still not gain `-wal/-shm` as a side effect of preflight.

Current architecture: `个人图片搜索系统_V3.0_开发主架构.md`, requirement revision 3.  
Current execution strategy: `个人图片搜索系统_V3.1_Codex_DeepSeek_开发执行策略.md`, revision 2 (2026-09-11 mandatory CUDA / GTX 1660 minimum update).

## Latest verification

| Check | Result |
|---|---|
| `uv run pytest -o addopts="" -q tests/unit tests/integration` | exit 0, `117 passed, 1 skipped in 77.50s` (post-fix; skip = pre-existing GPU opt-in case; pre-fix baseline was 106 + 1, P0 baseline 51 + 1) |
| `uv run ruff check .` / `git diff --check` | clean / clean |
| `python -m photo_search catalog init|add-source|scan|status --json` | exit 0, structured JSON (schema v1, `state=SUCCEEDED`, assets/locations/missing counts, `unstable_retries`) |
| `python -m photo_search catalog scan --source-id 9 --json` | exit 1, `{"status":"error","error":{"code":"SOURCE_NOT_FOUND",...}}` |
| `uv lock --check` | not re-run: no dependency change (standard library only) |
| P1-01 D-level fault injection (8 required scenarios) | all executed; the C2-corrected semantics (write-time qualification, lease expiry leaving no results, stale hash re-queue, attempt ceiling) now have dedicated tests in the remediation report §2 |
| ADR-0002 §12 focused review | C2 initial review **14 PASS / 2 FAIL** (B1/B4) → fixed; final focused re-review still pending |
| C2 findings B1–B4 and R1/R2 | fixed with per-item evidence in [docs/reports/P1-01-c2-remediation.md](reports/P1-01-c2-remediation.md) |

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

**P1-01 — stage 5: C2 final focused re-review.**

- Current review state: C2 initial review FAIL (B1/B4 blocking) → **all findings fixed and re-verified at D level**; PASS still requires this short re-review.
- Required input: `docs/reports/P1-01-c2-remediation.md` (per-item fix + evidence), `docs/reports/P1-01-c2-focused-review.md` (the findings), `docs/decisions/ADR-0002-catalog-identity-migration-scan-contract.md`, and the touched code (`catalog/{errors,store,db}.py`, `jobs/queue.py`, `ingestion/{service,scanner}.py`).
- Recommended execution model: **C2 / GPT-5.6 Sol / high**.
- Scope: verify that B1–B4 and R1/R2 are closed and that no frozen semantics were relaxed. Do not re-implement; a finding that needs a semantic change goes back into ADR-0002 and then to DS-H.
- Output: the re-review conclusion recorded in the P1-01 reports; only then can P1-01 move to PASS.

**P1-02 is BLOCKED BY P1-01 and must not start until the C2 re-review passes.** Its card is ready at
`docs/tasks/P1-02.md` and recommends **DS-H / DeepSeek V4.1 Flash / 高推理**.

Model switching remains manual. The repository records the recommended role/model for each stage; it does not switch clients or models itself.
