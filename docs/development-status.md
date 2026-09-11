# Development status

## Current state

**P0-07 is PASS; P0 is complete. P1-01 has started.** The P0 closing summary is
[docs/reports/P0-07-p0-summary.md](reports/P0-07-p0-summary.md). It inventories the P0 artifacts
(GPU onedir package, `uv.lock`, the four model manifests, worker/protocol, evaluation tooling and
the out-of-repo corpus), maps P0-01…P0-06 to their reports, lists the failed/limited components,
checks the nine §5.2 P0→P1 gates one by one against repository evidence, and states the invariants
and the items handed to P1/P2/P4.

Result of the gate check: all nine gates are satisfied → **P1 may start**. No P0 finding required a
change to the data identity, storage responsibilities or model route. Chinese-CLIP ViT-B/16 stays
the frozen 6 GB baseline; ViT-L/14 stays an optional high-VRAM enhancement.

**P1-01 stage 1 (C2 contract freeze) is complete.** The frozen implementation contract is
`docs/decisions/ADR-0002-catalog-identity-migration-scan-contract.md`. It fixes the content/location/
source identity rules, database unique/check constraints, read-only schema compatibility preflight,
WAL runtime requirement, migration semantics, scan/job state interaction, lease/late-submit rules and
the rule that missing conclusions are produced only by a complete successful scan. The P1-01 task
card has been synchronized to require this ADR before implementation.

P1-01 is **not PASS** yet. No P1-01 implementation, D-level fault injection, task report, or P1-02
handoff has been completed. The next execution step is the DS-H implementation stage under the
frozen ADR, followed by the C2 focused review required by the task card.

Carried forward as limits, not as passes: GTX 1660 6 GB remains **unverified on real hardware**
(P4-07/P4-08 release gate); InsightFace detection keeps 12 recorded CPU-assigned shape operators
(recognition graph is 130/130 CUDA); RapidOCR silently falls back to CPU when the CUDA DLLs are not
preloaded; indexing throughput is decode-bound (≈8.3 images/s, ≈360 ms/image vs ≈12 ms GPU
inference, ≈16.7 h for 500k with one worker); driver 616.56 exposes no per-process GPU memory; cold
CUDA start is slow and fragile (one `cuInit` took 2 m 23 s, and killing a worker mid-initialisation
blocked new CUDA contexts for ~4 minutes); the 150 evaluation queries are labelled by fixed
attribute rules, so no semantic search quality may be claimed.

Two historical record inconsistencies were found and recorded (not edited, since the evidence files
are read-only): the P0-06 report's command table shows a mid-task `44 passed, 1 skipped` snapshot
while the final count is 51 passed + 1 skipped, and `docs/reports/P0-06-model-evaluation.json` is
still the stage-A `stage B / BLOCKED` snapshot that was superseded by the stage-B PASS. Details in
the P0-07 report §3.1.

Current architecture: `个人图片搜索系统_V3.0_开发主架构.md`, requirement revision 3.
Current execution strategy: `个人图片搜索系统_V3.1_Codex_DeepSeek_开发执行策略.md`, revision 2
(2026-09-11 mandatory CUDA / GTX 1660 minimum update).

## Latest verification

| Check | Result |
|---|---|
| `uv run pytest -q tests/unit tests/integration` | P0 baseline: exit 0, 51 passed + 1 skipped (GPU-opt-in); P1-01 implementation tests not run yet |
| `uv run ruff check .` / `git diff --check` | P0 baseline clean / clean; current C2 stage changed documentation only |
| `uv lock --check` | P0 baseline: exit 0, `Resolved 95 packages` |
| P0-07 gate review (strategy §5.2, nine items) | all satisfied, each backed by a repo file |
| P1-01 C2 contract freeze | complete; ADR-0002 frozen and task card synchronized |

The C2 stage is a documentation/contract stage; backend tests and P1-01 D-level fault injection are
not claimed as completed by this stage.

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

**P1-01 — 资产/位置/来源/扫描最小持久任务/迁移与约束.**

- Stage 1 complete: **C2 / GPT-5.6 Sol / high contract freeze**.
- Frozen contract: `docs/decisions/ADR-0002-catalog-identity-migration-scan-contract.md`.
- Current execution step: **DS-H / DeepSeek V4.1 Flash / 高推理 implementation, tests and fixes**.
- Task card: `docs/tasks/P1-01.md`.
- The implementing model must not change asset/source identity, database unique/check constraints,
  schema compatibility/migration semantics, WAL requirement, lease/state-machine rules or missing
  detection boundaries. Any required change to these semantics stops local implementation and goes
  back to C2 review.
- Escalation: Windows path/permission/filesystem/long-path/sqlite native issues → C1.
- P1-01 must carry forward the P0-07 handoff: bounded decode pool and thumbnail prep are later
  concerns, originals remain read-only, the data directory stays outside the install directory, and
  the frozen 512-D/L2/CUDA contracts remain unchanged.
- P1-01 remains incomplete until implementation + required D-level validation + task report + C2
  focused review are complete.

Model switching is always manual in Codex. For the implementation stage, the task card recommends
**DS-H / DeepSeek V4.1 Flash / 高推理**. Read the current task card and ADR-0002 before coding; do
not reread the full architecture or strategy documents beyond the sections listed by the card.