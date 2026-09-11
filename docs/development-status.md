# Development status

## Current state

P0-05 is complete: **PASS**. Chinese-CLIP runs in a managed spawn worker with bounded 1 MiB JSON messages, file-path image inputs, structured failure propagation, timeout cleanup and an offline CPU demo. A PyInstaller onedir package was built and its executable completed the demo from a temporary directory containing Chinese characters and spaces.

P0-05A housekeeping is also complete: Python/build ignore rules were restored, tracked `.pyc` files were removed, and the worker stderr temporary file descriptor/file lifecycle was fixed.

Evidence: `docs/reports/P0-05-worker-and-packaging.md` plus the P0-05A housekeeping commits.

Current architecture: `个人图片搜索系统_V3.0_开发主架构.md`, requirement revision 3.
Current execution strategy: `个人图片搜索系统_V3.1_Codex_DeepSeek_开发执行策略.md`, revision 2 (2026-09-11 mandatory CUDA / GTX 1660 minimum update).

## Latest documentation verification

GPU requirement alignment: **PASS** (documentation/link checks, Ruff and diff-check). Architecture, strategy and P0-06 now require default CUDA with a GTX 1660 6 GB minimum support target; CPU is a reference/explicit degradation path. Decision: [ADR-0001](decisions/ADR-0001-gpu-required.md). Verification and handoff: [GPU architecture report](reports/2026-09-11-gpu-architecture-alignment.md).
This documentation change does not complete P0-06 or GPU runtime validation.

## GPU status

Hardware visibility is verified, but model GPU execution is **not yet verified**.

- Current read-only hardware check: NVIDIA GeForce RTX 4060 Laptop GPU, 8188 MiB VRAM, driver 616.56; consistent with the P0-01 hardware family.
- P0-06 A0 preserves the user's independent GPU probe where CUDAExecutionProvider was listed; that environment is not the formal project runtime.
- GTX 1660 6 GB is the user-approved minimum target, not an observed device. Its real-device compatibility/performance/package validation is pending, required by P4-07/P4-08 before full release PASS; RTX 4060 memory limiting is not equivalent evidence.
- P0-03 Chinese-CLIP used CPU-only ONNX Runtime sessions; no `CUDAExecutionProvider` was available or exercised.
- P0-04 InsightFace and RapidOCR probes also ran on CPU-only ONNX Runtime.
- P0-05 worker/package integration is currently a CPU Chinese-CLIP path.

Therefore there is still no committed evidence that Chinese-CLIP or InsightFace has executed through `CUDAExecutionProvider`, and no GPU worker/VRAM/latency validation has passed yet.

## Next task

P0-06 — **first complete GPU runtime bring-up, then perform the authorized 2,000-image / 100–200-query model evaluation**.

- Target: **DS-H / DeepSeek V4.1 Flash / 高推理**.
- GPU runtime / Windows / ONNX / CUDA provider / DLL / driver / dependency problems: recommend manual switch to **C1 / GPT-5.6 Terra / medium** while continuing the same task.
- Architecture/model-route decision triggered by evidence: **C2 / GPT-5.6 Sol / high** review.
- Task card: `docs/tasks/P0-06.md`.

### P0-06 hard ordering

1. GPU bring-up must pass first: `CUDAExecutionProvider`, Chinese-CLIP image/text CUDA inference, CPU/GPU consistency, measured GPU latency/VRAM, managed worker default GPU encode/demo-search, no silent full-model CPU fallback, explicit CPU degradation, GPU packaged offline/path smoke, 6 GB budget/handoff, and InsightFace detection/recognition CUDA smoke.
2. Only after that may the formal authorized model-quality evaluation begin.
3. If no authorized 2,000-image evaluation set is available, the GPU stage still proceeds, but the quality stage is marked **BLOCKED**.
4. P0-06 cannot be marked PASS and P0-07/P1 cannot start until both the GPU hard gate and the authorized quality evaluation pass.

Do not read a real photo library until the user explicitly authorizes the sample source.
