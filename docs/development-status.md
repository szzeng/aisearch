# Development status

## Current state

P0-06 stage **A (GPU runtime bring-up on the RTX 4060) is PASS**; stage **B (authorized
2,000-image quality evaluation) is BLOCKED** because no user-authorized sample source exists.
P0-06 is therefore **not PASS**, and `docs/tasks/P0-07.md` was deliberately not generated.

Delivered by P0-06 stage A:

- one ONNX Runtime distribution: `onnxruntime-gpu[cuda,cudnn]` 1.30.0 with matching `nvidia-*`
  wheels pinned in `uv.lock` (95 packages), and an override that keeps `rapidocr-onnxruntime`
  from reinstalling the CPU `onnxruntime`;
- a runtime device layer (`photo_search.runtime.device`) with an explicit device policy, a
  reportable degradation reason, `session.disable_cpu_ep_fallback`, CUDA DLL preloading and
  packaged-DLL discovery;
- real Chinese-CLIP CUDA inference: image tower **1320/1320** and text tower **395/395** node
  events on `CUDAExecutionProvider`, zero CPU nodes; CPU/GPU FP32 max abs difference
  3.13e-07 (image) / 1.79e-07 (text), cosine 1.0, Top-K identical to the frozen P0-03 record;
- warm batch=1 latency p50 11.76 ms (image) / 10.29 ms (text) on GPU vs 58.28 ms / 130.66 ms on
  CPU; worker VRAM ≈ 1.2 GiB with both towers loaded;
- managed worker: default CUDA loop (text encode, image encode, worker-internal demo-search,
  provider evidence), reproducible explicit CPU degradation, structured failure when CUDA is
  unavailable or `gpu_required` forbids CPU, and no silent whole-model CPU fallback;
- InsightFace `buffalo_l` detection/recognition CUDA smoke (recognition 130/130 nodes on CUDA;
  detection documented as 141 CUDA / 12 CPU shape-operator nodes);
- RapidOCR provider status: CUDA after DLL preload (p50 75.95 ms vs 917.94 ms CPU), with its
  silent CPU fallback when the DLLs are not preloaded recorded as a defect to fix in P3;
- GPU onedir package (21 NVIDIA DLLs, 3.01 GiB) smoked from a Chinese/space path offline on
  CUDA and on explicit CPU, plus stage-B evaluator/data-contract/metrics with unit tests.

Evidence: [P0-06 report](reports/P0-06-gpu-and-model-eval.md) and the `docs/reports/P0-06-*.json`
probe records.

Current architecture: `个人图片搜索系统_V3.0_开发主架构.md`, requirement revision 3.
Current execution strategy: `个人图片搜索系统_V3.1_Codex_DeepSeek_开发执行策略.md`, revision 2
(2026-09-11 mandatory CUDA / GTX 1660 minimum update).

## Latest verification

| Check | Result |
|---|---|
| `uv lock` / `uv lock --check` | resolved 95 packages, lock up to date |
| `uv run pytest tests/unit tests/integration` | 44 passed, 1 skipped (56.75 s) |
| `PHOTO_SEARCH_GPU_TESTS=1 uv run pytest -q tests/integration/test_ai_worker_lifecycle.py -k gpu` | 2 passed (real CUDA worker path) |
| `uv run ruff check .` / `git diff --check` | clean / clean |
| GPU probes (`tools/runtime/*`, `tools/packaging/*`) | all exit 0 with `status: pass` |

P0-06 stage A (GPU hard gate) is verified end-to-end; stage B quality numbers do not exist yet,
so no token/quality claim is made. Details and per-command exit codes live in the report.

## GPU status

Model GPU execution is now **verified on this machine** (RTX 4060 Laptop GPU / 8188 MiB /
driver 616.56 / CUDA UMD 13.4): Chinese-CLIP image and text towers really run on
`CUDAExecutionProvider` with the CPU fallback disabled, the worker default is CUDA, and the
packaged app reproduces it from a Chinese/space path offline.

Still open:

- GTX 1660 6 GB is the user-approved minimum target and remains **unverified on real hardware**;
  the RTX 4060 budget (≈ 1.2 GiB worker VRAM at batch=1, ≈ 3.7 GiB headroom on a 6 GB card) is a
  pre-check only. P4-07/P4-08 must verify runtime, models, performance, OOM/load-unload, the
  packaged offline path and minimum-configuration performance before a full release PASS.
- Cold CUDA start on this machine is slow and fragile (one `cuInit` took 2 m 23 s; terminating a
  worker inside CUDA initialisation blocked new CUDA contexts for ~4 minutes). The worker must not
  be treated as cheaply restartable mid-initialisation; the P2 scheduler should distinguish a cold
  GPU start from a hung worker.
- InsightFace detection keeps 12 CPU-assigned shape operators, and RapidOCR silently degrades to
  CPU unless the runtime DLLs are preloaded and the actual provider is verified.

## Next task

**P0-06 stage B — same task, no new task card.** P0-07 stays ungenerated until both stages pass.

- Blocking condition: the user must explicitly authorize a sample source giving **≥ 2000 images
  and 100–200 Chinese queries** with graded relevance (tuning and holdout separated). Until then
  the quality stage stays **BLOCKED**.
- Recommended model: **DS-H / DeepSeek V4.1 Flash / 高推理** (same task, same session is fine; no
  switch is required to unblock).
- If stage B results would change the core model route or architecture constraints, submit the
  evidence for **C2 / GPT-5.6 Sol / high** review.
- GPU/Windows/ONNX/DLL/driver problems during stage B: recommend a manual switch to
  **C1 / GPT-5.6 Terra / medium** while staying on P0-06.

Do not read or scan a real photo library until the user explicitly authorizes the sample source.
P1 must not start before P0-06 passes both stages.
