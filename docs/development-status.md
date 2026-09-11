# Development status

## Current state

P0-05 is complete: **PASS**. Chinese-CLIP runs in a managed spawn worker with bounded 1 MiB JSON messages, file-path image inputs, structured failure propagation, timeout cleanup and an offline CPU demo. A PyInstaller onedir package was built and its executable completed the demo from a temporary directory containing Chinese characters and spaces.

P0-05A housekeeping is also complete: Python/build ignore rules were restored, tracked `.pyc` files were removed, and the worker stderr temporary file descriptor/file lifecycle was fixed.

Evidence: `docs/reports/P0-05-worker-and-packaging.md` plus the P0-05A housekeeping commits.

Current execution strategy: `个人图片搜索系统_V3.1_Codex_DeepSeek_开发执行策略.md`, revision 1 (2026-09-11 GPU hard gate update).

## GPU status

Hardware visibility is verified, but model GPU execution is **not yet verified**.

- P0-01 observed an NVIDIA GeForce RTX 4060 Laptop GPU and a working NVIDIA driver through `nvidia-smi`.
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

1. GPU bring-up must pass first: `CUDAExecutionProvider`, Chinese-CLIP image/text CUDA inference, CPU/GPU consistency, measured GPU latency/VRAM, managed worker GPU encode/demo-search, CPU fallback, and InsightFace detection/recognition CUDA smoke.
2. Only after that may the formal authorized model-quality evaluation begin.
3. If no authorized 2,000-image evaluation set is available, the GPU stage still proceeds, but the quality stage is marked **BLOCKED**.
4. P0-06 cannot be marked PASS and P0-07/P1 cannot start until both the GPU hard gate and the authorized quality evaluation pass.

Do not read a real photo library until the user explicitly authorizes the sample source.
