# Development status

## Current state

**P0 is complete. P1-01 implementation and D-level validation are complete; P1-01 is NOT PASS yet.**
The DS-H stage delivered the catalog foundation under the frozen contract
`docs/decisions/ADR-0002-catalog-identity-migration-scan-contract.md` (unchanged), and the evidence
package is [docs/reports/P1-01-catalog-identity-and-scan.md](reports/P1-01-catalog-identity-and-scan.md).

Delivered in the DS-H stage: versioned migration with read-only compatibility preflight, atomic
per-version transactions, pre-migration backup and disk budget (`src/photo_search/catalog/**`),
catalog schema v1 with six tables and database-level identity/CHECK constraints
(`metadata`, `source_roots`, `scan_runs`, `assets`, `file_locations`, `jobs`), Windows comparison keys
and source-overlap/data-root boundaries (`catalog/paths.py`), the minimal persistent scan task with
claim/heartbeat/expiry-takeover/late-submit rules (`jobs/queue.py`), the minimal deterministic scan
with positive-fact commits and one final atomic missing reconciliation (`ingestion/**`), and an
observable internal CLI entry (`python -m photo_search catalog init|info|add-source|list-sources|scan|status`
with `--json`). No HTTP business API was added; `GET /api/v1/health` keeps its P0-01 behaviour.
55 new tests were added and all eight required fault-injection/recovery scenarios were really executed
(migration interruption, scan kill + restart, lease expiry/late submit, same-path content replacement,
shared assets across locations and sources, Chinese/space/long paths and overlap rejection, offline
source, WAL + `BLOCKED -> QUEUED` scan state).

What is still missing before P1-01 can be marked PASS: the **C2 focused review** required by the task
card and ADR-0002 §12 (16 items). The report lists the evidence per item together with the seven
implementation decisions DS-H made inside the ADR's allowed space (for example the `immutable=1`
read-only preflight, the extra error codes, the `RUNNING -> RUNNING` scan-run recovery self-loop and the
source-scoped `assets` count). Any review finding that would change asset/source identity, unique
constraints, schema compatibility or migration semantics, the WAL requirement, the state machine,
lease/late-submit rules or the missing-detection boundary must go back to C2 and update ADR-0002; the
implementation model must not change those semantics on its own.

Current architecture: `个人图片搜索系统_V3.0_开发主架构.md`, requirement revision 3.
Current execution strategy: `个人图片搜索系统_V3.1_Codex_DeepSeek_开发执行策略.md`, revision 2
(2026-09-11 mandatory CUDA / GTX 1660 minimum update).

Carried forward as limits, not as passes:

- GTX 1660 6 GB remains **unverified on real hardware** (P4-07/P4-08 release gate); InsightFace
  detection keeps 12 recorded CPU-assigned shape operators; RapidOCR silently falls back to CPU
  without preloaded CUDA DLLs; indexing throughput is decode-bound; driver 616.56 exposes no
  per-process GPU memory; cold CUDA start is slow and fragile; the 150 evaluation queries are
  rule-labelled, so no semantic search quality may be claimed.
- P1-01's scan re-hashes every enumerated file on every pass. That is deliberately stronger than the
  minimal contract (it detects same-size/same-mtime replacements) but it is **not** the P1-02
  production scanner: no stability window, no hash-before/after double stat, no file-id move
  acceleration, no directory cursor, no scale-level missing reconciliation.
- P1-01's single-writer coordinator is in-process; cross-process writers are protected by WAL +
  `BEGIN IMMEDIATE` + busy timeout but were not stress-tested.
- The read-only preflight reads a clean database (no `-wal` sidecar) with `immutable=1` so no sidecar
  is created; when a `-wal` file exists it uses a plain read-only connection (documented exception).
- `format/width/height` stay NULL in P1-01 (allowed by ADR-0002 §3.5).
- Two historical record inconsistencies remain recorded but unedited (P0-06 report command table and
  `docs/reports/P0-06-model-evaluation.json`); details in the P0-07 report §3.1.

## Latest verification

| Check | Result |
|---|---|
| `uv run pytest -o addopts="" -q tests/unit tests/integration` | exit 0, `106 passed, 1 skipped in 72.53s` (skip = pre-existing GPU opt-in case; P0 baseline was 51 + 1) |
| `uv run ruff check .` / `git diff --check` | clean / clean |
| `python -m photo_search catalog init|add-source|scan|status --json` | exit 0, structured JSON (schema v1, `state=SUCCEEDED`, assets/locations/missing counts) |
| `python -m photo_search catalog scan --source-id 9 --json` | exit 1, `{"status":"error","error":{"code":"SOURCE_NOT_FOUND",...}}` |
| `uv lock --check` | not re-run: no dependency change (standard library only) |
| P1-01 D-level fault injection (8 required scenarios) | all really executed; evidence table in the P1-01 report §4 |
| ADR-0002 §12 focused review (16 items) | evidence listed in the P1-01 report §4; **review itself still pending C2** |

## GPU status

Model GPU execution is verified on this machine (RTX 4060 Laptop GPU / 8188 MiB / driver 616.56 /
CUDA UMD 13.4): Chinese-CLIP image and text towers run on `CUDAExecutionProvider` with the CPU
fallback disabled, the worker defaults to CUDA, ViT-L/14 also ran fully on CUDA (2687 MiB), and the
packaged app reproduces the CUDA path from a Chinese/space path offline.

Still open (handed to later phases, see the P0-07 report §6):

- GTX 1660 6 GB is the user-approved minimum target and remains **unverified on real hardware**.
  Runtime pinning, performance, OOM/bounded-batch behaviour and the packaged offline path must be
  validated at P4-07/P4-08; this item is BLOCKED until then and prevents a full release PASS.
- P1/P2 must plan a bounded decode pool and thumbnail/subsample prep ahead of the AI worker, because
  decoding dominates indexing cost.
- P2's scheduler must distinguish a cold GPU start from a hung worker instead of killing a starting
  worker.
- P3 owns the explicit OCR device decision and the InsightFace CPU-operator re-check.
- A human-labelled holdout is required before any semantic search quality claim.

## Current task / next execution step

**P1-01 — stage 3: C2 focused review** (task card `docs/tasks/P1-01.md`, contract
`docs/decisions/ADR-0002-catalog-identity-migration-scan-contract.md`).

- Recommended execution model for this step: **C2 / GPT-5.6 Sol / high** (high-risk protocol review).
- Inputs: the P1-01 report §2 (implementation decisions) and §4 (ADR §12 evidence table), plus
  `src/photo_search/catalog/**`, `src/photo_search/jobs/queue.py`,
  `src/photo_search/ingestion/**` and the new tests.
- Output: the focused review conclusion recorded in `docs/reports/P1-01-*.md` (or a new follow-up
  report) plus, if needed, an ADR-0002 update. Only then can P1-01 move to PASS.
- The reviewer must not re-implement P1-01; DS-H owns implementation, C2 owns the rules and review.

**P1-02 — 流式扫描、稳定等待、hash 前后校验、缺失判定** is the next implementation task after the
review; its card is already generated at `docs/tasks/P1-02.md` and recommends
**DS-H / DeepSeek V4.1 Flash / 高推理**.

P1-01 must carry forward the P0-07 handoff: bounded decode pool and thumbnail prep are later concerns,
originals remain read-only, the data directory stays outside the install directory, and the frozen
512-D/L2/CUDA contracts remain unchanged.

Model switching is always manual in Codex. The current step recommends **C2 / GPT-5.6 Sol / high**;
the implementation stage that follows recommends **DS-H / DeepSeek V4.1 Flash / 高推理**. Codex only
records and reminds; it never switches models, clients or agents.
