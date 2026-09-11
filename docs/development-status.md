# Development status

## Current state

**P0-06 is PASS.** Stage A (GPU runtime bring-up on the RTX 4060) and stage B (authorized sample
evaluation) both passed, so P0-07 was generated and P1 may start after P0-07.

Stage A delivered a single `onnxruntime-gpu[cuda,cudnn]` runtime with a device policy that reports
the resolved device/provider and disables the CPU execution-provider fallback, real Chinese-CLIP
image/text CUDA inference (1320/395 node events, zero CPU nodes), CPU/GPU FP32 agreement
(3.13e-07 / 1.79e-07, cosine 1.0, Top-K identical to P0-03), warm p50 11.76 ms image / 10.29 ms
text, a managed worker GPU loop with explicit CPU degradation and structured CUDA-unavailable
failure, InsightFace and RapidOCR CUDA evidence, and a 3.01 GiB packaged GPU build smoked from a
Chinese/space path offline on CUDA and on explicit CPU.

Stage B evaluated the user-authorized sample source (`D:\SZ\朋友`, `D:\SZ\canon`, `D:\SZ\vsco`,
`D:\SZ\网络`, `D:\SZ\pic`): **3231 images** (934 with faces, 1141 with OCR text) and **150 invented
Chinese daily-life queries** (50 tuning / 100 holdout) with fixed, model-independent annotation
rules. Results on CUDA: baseline Chinese-CLIP ViT-B/16 nDCG@20 0.2592 / Recall@50 0.0410 (holdout
0.2415), candidate ViT-L/14 0.2672 / 0.0417 (holdout 0.2430) at 2.3× the VRAM, random-ranking
chance level 0.1100 / 0.0157. Decision: keep ViT-B/16 as the 6 GB baseline, record ViT-L/14 as an
optional high-VRAM enhancement. CPU and GPU produced an identical ranking on a 400-image subset
(query p50 3.38 vs 17.43 ms).

Evidence: [P0-06 report](reports/P0-06-gpu-and-model-eval.md) plus the `docs/reports/P0-06-*.json`
probe and evaluation records. Corpus data, labels and OCR text stay in
`PhotoSearchData\evaluation` outside the repository; only aggregate counts are committed.

Recorded limits: the query labels are rule-derived attribute matches, not human relevance
judgements; GTX 1660 6 GB remains unverified on real hardware; per-process GPU memory is not
exposed by driver 616.56.

Current architecture: `个人图片搜索系统_V3.0_开发主架构.md`, requirement revision 3.
Current execution strategy: `个人图片搜索系统_V3.1_Codex_DeepSeek_开发执行策略.md`, revision 2
(2026-09-11 mandatory CUDA / GTX 1660 minimum update).

## Latest verification

| Check | Result |
|---|---|
| `uv run pytest -q tests/unit tests/integration` | exit 0, 51 passed + 1 skipped (GPU-opt-in) |
| `uv run ruff check .` / `git diff --check` | clean / clean |
| GPU probes (`tools/runtime/*`, `tools/packaging/*`) | all exit 0 with `status: pass` |
| Corpus build (`build_corpus.py --faces --ocr`) | 3231 images, 934 with faces, 1141 with OCR text |
| Query set (`build_query_set.py`) | 150 queries (50/100), all label bounds satisfied |
| Baseline / candidate / chance evaluation | 0.2592 / 0.2672 / 0.1100 nDCG@20 on 3231 images |
| CPU comparison (`--cpu-baseline --limit-images 400`) | identical ranking, query p50 3.38 vs 17.43 ms |

Details, per-command exit codes and the unverified list live in the report.

## GPU status

Model GPU execution is verified on this machine (RTX 4060 Laptop GPU / 8188 MiB / driver 616.56 /
CUDA UMD 13.4): Chinese-CLIP image and text towers run on `CUDAExecutionProvider` with the CPU
fallback disabled, the worker defaults to CUDA, ViT-L/14 also ran fully on CUDA (2687 MiB), and the
packaged app reproduces the CUDA path from a Chinese/space path offline.

Still open:

- GTX 1660 6 GB is the user-approved minimum target and remains **unverified on real hardware**.
  The RTX 4060 budget (≈1.2 GiB worker VRAM at batch=1 for ViT-B/16, ≈2.0 GiB for ViT-L/14) is a
  pre-check only; P4-07/P4-08 own the minimum-configuration runtime, performance, OOM and packaged
  offline validation before a full release PASS.
- Cold CUDA start on this machine is slow and fragile (one `cuInit` took 2 m 23 s; terminating a
  worker inside CUDA initialisation blocked new CUDA contexts for ~4 minutes). The P2 scheduler must
  distinguish a cold GPU start from a hung worker.
- InsightFace detection keeps 12 CPU-assigned shape operators, and RapidOCR silently degrades to
  CPU unless the runtime DLLs are preloaded and the actual provider is verified.
- Image indexing is dominated by decode (≈360 ms/image vs ≈12 ms GPU inference); a 500k library
  needs a bounded decode pool.
- Query labels are attribute-rule-derived; a human-labelled holdout is needed for semantic claims.

## Next task

**P0-07 — 汇总 P0 可行性、失败组件与进入 P1 的约束.**

- Recommended model: **DS-L / DeepSeek V4.1 Flash / 低推理** (per strategy §5 and the P0-06 card).
- Task card: `docs/tasks/P0-07.md`.
- Escalation: if the summary concludes that the core architecture or model route must change,
  submit the evidence for **C2 / GPT-5.6 Sol / high** review instead of deciding it locally.
- P0-07 must hand the GTX 1660 6 GB items and the decode-throughput finding to P4/P1 explicitly.

Do not start P1 before P0-07 passes. Do not read or scan a real photo library again without an
explicit authorization.
