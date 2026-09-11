# P0-06 — GPU runtime bring-up + authorized model evaluation

**Status:** stage **A (GPU hard gate): PASS**. Stage **B (authorized sample evaluation):
PASS** on the user-authorized source (`D:\SZ\朋友`, `D:\SZ\canon`, `D:\SZ\vsco`,
`D:\SZ\网络`, `D:\SZ\pic`). **P0-06 is therefore PASS**, and `docs/tasks/P0-07.md` was
generated.

One limit is recorded with the result: the 150 queries are labelled by fixed, model-independent
attribute rules (capture time, geometry, exposure/colour, face counts, OCR text presence), not by
human relevance judgements. The metrics therefore measure ranking of attribute-defined images.

Actual executor: this Codex session (client model as configured for the session; no model
switch is claimed). Git baseline at start: `caba9f1` with a clean worktree.

## What changed

| Area | Change |
|---|---|
| Runtime dependency | `onnxruntime>=1.20` → `onnxruntime-gpu[cuda,cudnn]>=1.30,<2`, plus `[tool.uv] override-dependencies = ["onnxruntime; sys_platform == 'never'"]` so `rapidocr-onnxruntime`'s transitive CPU `onnxruntime` cannot overwrite the GPU build. `uv.lock` updated (95 packages). |
| `src/photo_search/runtime/device.py` (new) | Device policy (`gpu_required` / `cpu_allowed` / `cpu_preferred`), explicit resolution with degradation reasons, session options that disable the CPU execution-provider fallback, DLL preloading, packaged-DLL discovery and `RuntimeCapabilities`. |
| `src/photo_search/models/embedding/chinese_clip_onnx.py` | Adapter takes a `RuntimeConfig`, resolves the real device/provider, reports it, exposes ORT per-node profiling, and never silently falls back. P0-03 manifest、model identity、512-D/L2 contract unchanged. |
| `src/photo_search/workers/*` | Worker defaults to CUDA, accepts an explicit device/policy, reports device/provider/degradation, adds a worker-internal `demo_search` and `provider_usage` action, and returns device facts inside structured errors. |
| `src/photo_search/__main__.py` | `demo-search --device cuda|cpu --output <json>`; generous cold-start deadline. |
| `src/photo_search/evaluation/` (new) | Stage-B data contract (schema, splits, leakage and authorization checks) and retrieval metrics (nDCG@20, Recall@50, failure list). |
| `tools/runtime/` (new) | Reproducible probes: `chinese_clip_gpu_check.py`, `face_gpu_smoke.py`, `ocr_gpu_status.py`, `runtime_common.py`. |
| `tools/evaluation/chinese_clip_eval.py` (new) | Stage-B evaluator CLI; refuses to run without a dataset that records the user's authorization. |
| `tools/packaging/` | `build_onedir.py` bundles the 21 NVIDIA runtime DLLs flat into `_internal/nvidia_dlls`; `smoke_packaged_gpu.py` runs the packaged app on CUDA and on explicit CPU from a Chinese/space path with no network. |
| Tests | New unit tests for the device policy/profiling and the evaluation contract; worker integration tests cover CPU baseline, explicit degradation, `gpu_required` refusal, CUDA-unavailable fault injection, and an opt-in real CUDA worker test. |

## A1. Environment facts (re-measured, not carried over from P0-01)

| Item | Observed value |
|---|---|
| GPU | NVIDIA GeForce RTX 4060 Laptop GPU, 8188 MiB, driver 616.56, CUDA UMD 13.4, compute capability 8.9 |
| GPU state during the runs | P8 idle, 50–52 °C, 611–932 MiB used by the desktop/other processes |
| Interpreter / lock | CPython 3.12.10, `uv` lock resolved 95 packages |
| ONNX Runtime | `onnxruntime-gpu` 1.30.0; `get_available_providers()` = `['TensorrtExecutionProvider', 'CUDAExecutionProvider', 'CPUExecutionProvider']` |
| NVIDIA runtime wheels | `nvidia-cuda-runtime` 13.0.96, `nvidia-cuda-nvrtc` 13.0.88, `nvidia-cublas` 13.1.1.3, `nvidia-cudnn-cu13` 9.24.0.43, `nvidia-cufft` 12.0.0.61, `nvidia-curand` 10.4.0.35, `nvidia-nvjitlink` 13.4.52 |
| Install/switch commands | `uv lock`, `uv sync --all-groups`; `uv pip list` shows `onnxruntime-gpu` and no `onnxruntime` distribution |
| Only one runtime | verified: `onnxruntime` 1.30.0 was uninstalled when `onnxruntime-gpu` 1.30.0 was installed, so the CUDA, TensorRT, Azure and CPU providers all come from one distribution |

Difference from the P0-06 §A0 user probe: that independent environment resolved the newest
`nvidia-*` wheels available at the time (cudart 13.4.49, cuDNN 9.26.0.51, cuBLAS 13.7.0.27);
this repository lock pinned 13.0.96 / 9.24.0.43 / 13.1.1.3 on 2026-09-11. Both are within the
CUDA 13.x / cuDNN 9.x line that this ONNX Runtime build requires, and the project environment
performed real CUDA inference (below). The GTX 1660 minimum target keeps the same major line;
its own runtime pinning stays with P4-07/P4-08. No CUDA Toolkit, nvcc, Visual Studio CUDA
toolchain or TensorRT installation was added.

## A2. CUDA provider

`onnxruntime.get_available_providers()` contains `CUDAExecutionProvider` in the project
environment (not just in the A0 probe). Sessions are created with only
`("CUDAExecutionProvider", {"use_tf32": "0"})` and `session.disable_cpu_ep_fallback=1`, so the
CPU provider is neither a silent fallback nor an implicit participant.

## A3. Real Chinese-CLIP CUDA inference with provider evidence

Run: `uv run python tools/runtime/chinese_clip_gpu_check.py` → `docs/reports/P0-06-chinese-clip-gpu-check.json`
(405.5 s, one process; frozen P0-03 image and text towers, batch=1, FP32, TF32 disabled).

| Evidence | Value |
|---|---|
| Resolved device | `cuda`, provider `CUDAExecutionProvider`, `cpu_fallback_disabled: true`, `use_tf32: "0"` |
| Per-node providers (ORT profiling) | image tower **1320 / 1320 nodes on CUDA**; text tower **395 / 395 nodes on CUDA**; zero CPU nodes |
| Fake-positive guard | a session containing any CPU-assigned node fails to initialize while the fallback is disabled (see A7 for the one graph where this triggers) |
| Dimension / normalisation | image and text vectors are 512-D; L2 norms 0.99999994–1.00000006 |

## A4. CPU/GPU FP32 consistency

Same fixtures as P0-03 (deterministic gradient/checkerboard image, five fixed Chinese queries),
CPU FP32 vs GPU FP32 in the same process:

| Comparison | max abs difference | cosine |
|---|---|---|
| image vector | 3.13e-07 | 1.0 |
| five text vectors | 8.94e-08 … 1.79e-07 | 1.0 … 1.00000024 |

Both runs rank the five queries against the fixture image as `[0, 1, 2, 4, 3]`, which is
identical to the frozen P0-03 `onnx_rank`; the CPU run still reproduces the recorded
communication values. Thresholds were cosine `>= 0.9999` and identical Top-K: passed without
loosening anything.

## A5. Warm latency and VRAM

Warm, batch=1, 224×224 image and 52-token text, RTX 4060 (RTX 4060 numbers are not a GTX 1660
performance claim):

| Path | n | min | p50 | p95 | mean | max |
|---|---|---|---|---|---|---|
| GPU image encode | 20 | 11.64 ms | **11.76 ms** | 12.35 ms | 14.93 ms | 74.41 ms |
| GPU text encode | 20 | 9.76 ms | **10.29 ms** | 37.32 ms | 13.67 ms | 51.59 ms |
| CPU image encode | 5 | 56.43 ms | 58.28 ms | 159.86 ms | 78.03 ms | 159.86 ms |
| CPU text encode | 5 | 67.28 ms | 130.66 ms | 136.18 ms | 108.85 ms | 136.18 ms |

VRAM (measurement scope: `nvidia-smi` total GPU memory before/after load and during the latency
loop; this driver reports `[N/A]` for per-process GPU memory, which is recorded as a caveat):

| Metric | Value |
|---|---|
| Desktop/other processes before load | 611 MiB |
| Total GPU memory after both towers loaded | 1785 MiB |
| Total GPU memory peak during the run | 1805 MiB |
| Worker-attributable delta | ≈ 1194 MiB |
| Worker working-set peak (RAM) | 1014.5 MiB |

6 GB budget: on a 6144 MiB card, ≈ 5000 MiB is available after a 600 MiB desktop, and the
measured worker footprint is ≈ 1.2 GiB for both towers plus inference workspace, i.e. ≈ 3.7 GiB
of headroom at batch=1. The ONNX Runtime per-session arena limit is **not** a process VRAM cap,
so this remains a budget pre-check; GTX 1660 real-device validation stays with P4-07/P4-08.

## A6. Managed worker GPU loop, failure behaviour and explicit CPU degradation

| Scenario | Command | Result |
|---|---|---|
| Worker default CUDA | `python -m photo_search demo-search --device cuda --output docs/reports/P0-06-source-gpu-worker.json` | exit 0; device `cuda`, provider `CUDAExecutionProvider`, CPU fallback disabled; worker-internal text encode, image encode and Top-K all on CUDA (1320/395 node events, zero CPU nodes) |
| Explicit CPU degradation | `… --device cpu --output docs/reports/P0-06-source-cpu-worker.json` | exit 0; device `cpu`, provider `CPUExecutionProvider`, `is_degradation: true`, reason `explicit CPU request`; 1323/407 node events on CPU |
| CUDA masked away while the default is CUDA | `PHOTO_SEARCH_AVAILABLE_PROVIDERS=CPUExecutionProvider`, pytest | worker startup fails with a structured `DeviceUnavailableError`, `details.cpu_fallback = "disabled"` — no silent whole-model CPU run |
| `gpu_required` policy + explicit CPU | pytest | refused with `DeviceUnavailableError` |
| Cold start on this machine | see Findings | ~4–5 min for the first CUDA start in a fresh process after the GPU was idle/unhealthy |

Top-1 for `红色的图片` is `red` in every path: 0.44083235 (source CUDA), 0.44083229 (source
CPU), 0.4408323 (P0-05 CPU demo), 0.44083235 (packaged CUDA). The node-event totals differ
between providers (1320 vs 1323 image, 395 vs 407 text) because the execution providers
partition and optimise the graph differently; the vectors and the contract are unchanged.

## A7. InsightFace `buffalo_l` CUDA smoke

Run: `uv run python tools/runtime/face_gpu_smoke.py` → `docs/reports/P0-06-face-gpu-smoke.json`
(exit 0).

- InsightFace's own detection and recognition sessions report
  `['CUDAExecutionProvider', 'CPUExecutionProvider']`.
- Public-domain `skimage.data.astronaut` fixture: **1 face**, `det_score` 0.8305 (P0-04 CPU
  probe: 0.8304), embedding `[512]`; warm detection+recognition latency p50 **17.49 ms** (n=5).
- Recognition graph, strict run with the CPU fallback disabled: **130 / 130 nodes on CUDA**,
  output `[1, 512]` — this graph is entirely CUDA-assignable.
- Detection graph, same strict run: **not** entirely CUDA-assignable. ONNX Runtime 1.30 reports
  `This session contains graph nodes that are assigned to the default CPU EP, but fallback to CPU
  EP has been explicitly disabled by the user`, and a profiled session with the normal provider
  order shows **141 CUDA / 12 CPU** node events; the CPU-assigned operators are
  `Concat` ×2, `Gather` ×4, `Slice` ×2, `Unsqueeze` ×4 (shape/index plumbing, not convolutions).
  This is recorded as a bounded, enumerated operator fallback, not a whole-model CPU run —
  ADR-0001 §2 explicitly permits recorded per-operator CPU assignment while forbidding silent
  whole-model/library fallback. Follow-up point for P3/P4: re-check whether a later ONNX Runtime
  assigns these ops to CUDA, and keep the detection graph pinned.

## A8. RapidOCR provider status

Run: `uv run python tools/runtime/ocr_gpu_status.py` → `docs/reports/P0-06-rapidocr-gpu-status.json`
(exit 0) and `docs/reports/P0-06-rapidocr-no-preload.json` (exit 0, negative control).

| Case | Actual providers | Latency (full OCR pass) | Text vs P0-04 record |
|---|---|---|---|
| CUDA DLLs preloaded (as the product worker does) | detection, classification and recognition sessions all report `['CUDAExecutionProvider', 'CPUExecutionProvider']`, `engine_use_cuda: true` | p50 **75.95 ms** (GPU) vs p50 917.94 ms (CPU, same process) | CJK and ASCII text identical to the P0-04 CPU record |
| CUDA DLLs not preloaded | `ONNX Runtime` cannot create the CUDA provider and RapidOCR **silently continues on CPU** (`engine_use_cuda: true`, actual providers `['CPUExecutionProvider']`, p50 977.94 ms) | — | identical text |

Finding: RapidOCR 1.4.4 does not disable the CPU fallback and does not fail when its CUDA
request cannot be satisfied. OCR therefore needs the same treatment as the embedding processors
in P3: preload the runtime DLLs in the AI worker, verify the actual provider after construction,
and surface an explicit device status instead of reporting "CUDA enabled" while running on CPU.
RapidOCR's own CUDA provider options also request `cudnn_conv_algo_search: EXHAUSTIVE` and a
`kNextPowerOfTwo` arena; those should be tuned together with the OCR device decision. Whether OCR
becomes a hard CUDA path is decided by measured benefit in P3, not by this task.

## A9. GPU hard-gate summary

```text
RTX 4060 / driver re-verified            PASS
CUDAExecutionProvider available          PASS
Chinese-CLIP image tower on CUDA         PASS
Chinese-CLIP text tower on CUDA          PASS
actual provider/fallback evidence        PASS
CPU/GPU consistency                      PASS
GPU repeated latency measured            PASS
VRAM measured                            PASS
worker GPU text encode                   PASS
worker GPU image encode                  PASS
worker GPU demo-search                   PASS
default CUDA / no silent CPU fallback    PASS
explicit CPU baseline / degradation      PASS
GPU packaged offline/path smoke          PASS
6 GB budget / GTX 1660 handoff recorded  PASS
InsightFace detection CUDA smoke         PASS (documented 12-node CPU operator fallback)
InsightFace recognition CUDA smoke       PASS
RapidOCR provider status recorded        PASS
```

## A11. Packaged GPU runtime smoke

Build: `uv run python tools/packaging/build_onedir.py` → `dist/PhotoSearch`
(`PhotoSearch.exe` 33,832,941 bytes, 21 NVIDIA DLLs = 1.61 GiB flattened into
`_internal/nvidia_dlls`, 3.01 GiB total). The frozen app resolves that folder through
`bundled_dll_directory()` and `os.add_dll_directory` before the CUDA session is created.

Smoke: `uv run python tools/packaging/smoke_packaged_gpu.py` → `docs/reports/P0-06-packaged-gpu-smoke.json`
(exit 0). The bundle was copied to
`%TEMP%\P0-06 <random>\P0-06 中文 空格 验收\PhotoSearch`, the working directory was outside the
package, the manifest came from the *copy's* own `_internal` resources, the model directory was
passed explicitly, and the environment forced offline operation
(`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, all proxies → `http://127.0.0.1:9`, empty
`NO_PROXY`).

| Run | Exit | Wall time | Device | Provider evidence | Top-K |
|---|---|---|---|---|---|
| `PhotoSearch.exe demo-search --device cuda` | 0 | 36.7 s | `cuda` | `CUDAExecutionProvider`, fallback disabled, 1320/395 nodes all CUDA | red 0.44083235, green 0.41859499, blue 0.40591100 |
| `PhotoSearch.exe demo-search --device cpu` | 0 | 3.5 s | `cpu` (explicit degradation) | `CPUExecutionProvider`, 1323/407 nodes all CPU, `is_degradation: true` | red 0.44083229, green 0.41859496, blue 0.40591097 |

The CJK query text survived the packaged round trip unchanged, and no run needed the network.
The P0-05 CPU package is not reused as evidence here.

## B. Authorized sample evaluation — PASS

Sample source (user authorization, 2026-09-11): `D:\SZ\朋友`, `D:\SZ\canon`, `D:\SZ\vsco`,
`D:\SZ\网络`, `D:\SZ\pic`. The tools only read those folders. The corpus file (absolute paths,
OCR text) stays in the user data directory (`PhotoSearchData\evaluation`), and the repository
receives aggregate counts only; because the `朋友` tree is organised by person, the corpus uses
opaque ids (`img_00001`…) and no folder or person name is written into the repository.

### B1. Corpus and query set

| Item | Value |
|---|---|
| Images | **3231** readable images (10.97 GiB) from the five authorized roots |
| By source | pic 1350, vsco 872, 朋友 881, canon 99, 网络 29 |
| Orientation | landscape 2111, portrait 994, square 126 |
| Capture time source | EXIF 1814, file mtime 1417 |
| Faces (InsightFace) | 934 images with ≥1 face (604 single-face, 330 with ≥2) |
| Text (RapidOCR) | 1141 images with text, 339 containing digits |
| Excluded | 1 HEIC (Pillow cannot decode it here) and 3 RAR archives (not extracted) |
| Queries | **150 invented Chinese daily-life queries**: 50 tuning + 100 holdout |
| Label density | 13–1282 positive images per query, median 363 |

Ground truth is derived from signals that do **not** involve the evaluated model: EXIF capture time,
geometry, exposure/colour statistics over a 384 px thumbnail, InsightFace face counts and RapidOCR
text presence. Thresholds were fixed on the tuning queries before any model run, and the builder
refuses to emit a query with fewer than 3 or more than 40 % positive images. This is the accuracy
limit of the evaluation: it measures whether a model can rank *attribute-defined* images, not
human-judged relevance, so it cannot judge queries such as "吃火锅" or "生日蛋糕".

Tooling: `tools/evaluation/build_corpus.py` (attributes, face and OCR passes with resumable
caches), `tools/evaluation/build_query_set.py` (rules, bounds checks, dataset),
`tools/evaluation/random_baseline.py` (chance level), `tools/evaluation/chinese_clip_eval.py`
(metrics, latency, resources) and the contract/metrics modules under
`src/photo_search/evaluation/`.

### B2. Baseline vs stronger candidate

Baseline: the frozen P0-03 **Chinese-CLIP ViT-B/16** (512-D, revision `36e679e6…`) on CUDA.
Candidate: **Chinese-CLIP ViT-L/14** (`OFA-Sys/chinese-clip-vit-large-patch14`, revision
`660941af70c6…`, 768-D), downloaded and exported to ONNX FP32 by
`tools/prepare_models/prepare_chinese_clip_candidate.py`. Both run through the same adapter,
device policy and evaluator, with `use_tf32=0` and the CPU fallback disabled.

| Model | Dim | nDCG@20 overall | tuning | holdout | Recall@50 | image p50 | images/s | GPU MiB | RAM MiB |
|---|---|---|---|---|---|---|---|---|---|
| Baseline ViT-B/16 | 512 | 0.2592 | 0.2946 | 0.2415 | 0.0410 | 370.8 ms | 8.34 | 1151 | 943 |
| Candidate ViT-L/14 | 768 | **0.2672** | **0.3157** | **0.2430** | 0.0417 | 417.7 ms | 7.71 | 2687 | 997 |
| Random ranking (chance) | – | 0.1100 | – | – | 0.0157 | – | – | – | – |

Both models beat chance by ≈2.4× (nDCG@20) and ≈2.6× (Recall@50). The candidate improves the tuning
split by +7.2 % relative but the holdout split by only **+0.6 %** (0.2415 → 0.2430), while using
**2.3× the VRAM** (2687 MiB vs 1151 MiB total GPU memory) and running ~13 % slower per image; a
768-D space also doubles the index size. Per-query failures overlap in 121 of ~125 queries, i.e.
both models fail on the same queries.

Decision: **keep Chinese-CLIP ViT-B/16 as the product baseline** and record ViT-L/14 as an optional
enhancement for higher-VRAM machines, not a replacement; revisit only with a human-labelled
holdout. No architecture or model-route change is required, so no C2 review is triggered.

### B3. CPU comparison and resource notes

Same model on an identical 400-image stride subset, GPU and CPU in one process: the ranking is
**identical** on both devices (nDCG@20 0.243734 and Recall@50 0.232313 exactly equal), which
extends the A4 vector-level agreement to full ranking agreement.

| Path | Query p50 | Image p50 | Throughput |
|---|---|---|---|
| CUDA (CPU fallback disabled) | 3.38 ms | 378.27 ms | 8.49 images/s |
| Explicit CPU degradation | 17.43 ms | 543.84 ms | 5.80 images/s |

Image latency is dominated by reading and decoding the original files (the corpus contains stitched
panoramas up to 161 MP), not by the model: GPU inference is ≈12 ms per image. At the measured
8.3 images/s one worker would need ≈16.7 h for a 500k library, so P1/P2 must plan a bounded decode
pool and/or subsampled decoding ahead of the AI worker.

Failure queries: 125 of 150 queries score nDCG@20 < 0.5 for the baseline (124 for the candidate).
The worst are the time/date and colour-attribute queries whose labels are large sets (for example
`晚上拍的照片` has 1059 positive images), where a 20-result window cannot cover the label.

## Commands, exit codes, results

| Command | Exit | Result |
|---|---|---|
| `uv lock` / `uv lock --check` | 0 / 0 | resolved 95 packages; lock up to date |
| `uv sync --all-groups` | 0 | installed `onnxruntime-gpu` 1.30.0 + 7 `nvidia-*` wheels, uninstalled `onnxruntime` |
| `uv run pytest tests/unit tests/integration` | 0 | 44 passed, 1 skipped in 56.75 s |
| `PHOTO_SEARCH_GPU_TESTS=1 uv run pytest -q tests/integration/test_ai_worker_lifecycle.py -k gpu` | 0 | 2 passed: real CUDA worker encode/demo-search/provider evidence + `gpu_required` CPU refusal |
| `uv run ruff check .` | 0 | All checks passed |
| `git diff --check` | 0 | no whitespace errors |
| `uv run python tools/runtime/chinese_clip_gpu_check.py` | 0 | `status: pass`, 405.5 s |
| `uv run python tools/runtime/face_gpu_smoke.py` | 0 | `status: pass` |
| `uv run python tools/runtime/ocr_gpu_status.py` | 0 | `status: pass` (CUDA active) |
| `uv run python -m photo_search demo-search --device cuda --output …` | 0 | worker GPU loop |
| `uv run python -m photo_search demo-search --device cpu --output …` | 0 | explicit CPU degradation |
| `uv run python tools/packaging/build_onedir.py` | 0 | onedir package with CUDA runtime |
| `uv run python tools/packaging/smoke_packaged_gpu.py` | 0 | packaged CUDA + CPU smoke, no failures |
| `uv run python tools/evaluation/build_corpus.py --faces --ocr` | 0 | 3231 images, 934 with faces, 1141 with OCR text (1086 s) |
| `uv run python tools/evaluation/build_query_set.py` | 0 | 150 labelled queries (50 tuning / 100 holdout), bounds satisfied |
| `uv run python tools/evaluation/random_baseline.py` | 0 | chance level 0.1100 nDCG@20 / 0.0157 Recall@50 |
| `uv run python tools/evaluation/chinese_clip_eval.py` (baseline, CUDA) | 0 | nDCG@20 0.2592 / Recall@50 0.0410 over 3231 images |
| `uv run python tools/evaluation/chinese_clip_eval.py` (ViT-L/14, CUDA) | 0 | nDCG@20 0.2672 / Recall@50 0.0417, 2687 MiB |
| `uv run python tools/evaluation/chinese_clip_eval.py --cpu-baseline --limit-images 400` | 0 | identical CPU/GPU ranking; query p50 3.38 vs 17.43 ms |
| `uv run python tools/prepare_models/prepare_chinese_clip_candidate.py` | 0 | ViT-L/14 ONNX export (1.16 GB image / 390 MB text, 768-D) |

## Findings and limits

1. **Cold CUDA start on this machine is very slow and fragile.** One `cuInit` after the GPU had
   been idle/unhealthy took 2 m 23 s (normally ≈ 14 s), the first probe's adapter load took
   238.7 s for two CUDA sessions, and terminating a worker while it was inside CUDA
   initialisation left the driver unable to initialise new CUDA contexts for ~4 minutes. The
   worker therefore uses generous validation deadlines and must not be treated as cheaply
   restartable mid-initialisation; the P2 scheduler should distinguish "cold GPU start" from
   "hung worker" instead of killing a starting worker. This is an observation about this
   development machine, not a product performance claim.
2. **InsightFace detection has 12 CPU-assigned operators** (A7) even though the model is
   GPU-first. Recorded, bounded, and to be re-checked with the P4 minimum-configuration work.
3. **RapidOCR silently degrades to CPU** (A8). It must become an explicit, verified device before
   OCR is productionised.
4. **Per-process GPU memory is unavailable** on driver 616.56 (`[N/A]`), so VRAM evidence uses
   total-GPU deltas plus the worker's RAM working set. Precision of the budget statement depends
   on desktop noise (611–932 MiB observed).
5. **Only batch=1 FP32 was validated.** Dynamic batching, FP16/quantisation, TensorRT and the
   `TensorrtExecutionProvider` (listed but unused) are out of scope and remain unverified.
6. **GTX 1660 6 GB is still unverified on real hardware**; the RTX 4060 budget above is a
   pre-check, not a substitute.
7. Not measured here: sustained library-indexing throughput,前台争用, long-run stability, P95
   query latency through the API, and search quality (stage B blocked).
8. The Windows sandbox helper could not start in this session (`helper_unknown_error: setup
   refresh had errors`), so every command ran with escalated permissions and `apply_patch` was
   unusable. File edits were written inside the repository through escalated shell writes; nothing
   was written outside the repository, the user data directory or `%TEMP%`.

9. **The front of the pipeline dominates the cost.** Decoding the original photos costs ≈360 ms
   per image against ≈12 ms of GPU inference; the resulting 500k-library estimate (≈16.7 h with one
   worker) is a P1/P2 planning input, not a model-quality problem.
10. **Chinese file names need Pillow, not OpenCV.** `cv2.imread` cannot open non-ASCII Windows
    paths, which silently lost 546 face measurements in the first pass; the corpus builder now
    loads images through Pillow. Any future OpenCV-based component must do the same or those photos
    are silently skipped.
11. **The candidate did not earn a promotion.** ViT-L/14 buys a tuning-set gain of +7.2 % relative
    while holdout moves +0.6 % and VRAM grows 2.3×, so the 6 GB baseline stays ViT-B/16.
12. **Labels are rule-derived, not human judgements.** They are reproducible and model-independent,
    but they cannot express semantic queries; a human-labelled holdout is the next quality step if
    the product needs to claim semantic search quality.

## GTX 1660 6 GB handoff (minimum supported target)

- Runtime plan: keep the same `onnxruntime-gpu` CUDA 13.x / cuDNN 9.x line, pin the actual
  wheel versions per release, ship the DLLs inside the package (they are not on the user's PATH),
  and require no CUDA Toolkit, nvcc or TensorRT installation.
- Measured budget to re-check on real hardware: ≈ 1.2 GiB worker VRAM at batch=1 for both
  Chinese-CLIP towers, ≈ 1.1 GiB RAM working set, plus the desktop's own GPU usage.
- Not yet verified and required at P4-07/P4-08: GTX 1660 CUDA execution for Chinese-CLIP
  image/text and InsightFace detection/recognition with output consistency, 6 GB load/unload/OOM
  behaviour with one worker and bounded batches, the packaged Chinese/space offline path with
  recorded DLL sources and real providers, and minimum-configuration performance vs architecture
  §12.2 (P95 search latency, throughput). Missing hardware keeps this item BLOCKED and prevents a
  full release PASS, but does not block P1 work that has already met the P0 gate.

## Handoff record

```text
任务 ID / 状态：P0-06 / A PASS + B PASS（整体 PASS，已生成 P0-07 任务卡）
执行模型 / reasoning / 速度：由用户在当前 Codex 会话中手动选择；本会话未切换模型，也未声称切换
基线提交号：caba9f1（工作树干净）
已完成行为：onnxruntime-gpu[cuda,cudnn] 单发行包依赖方案 + uv.lock；设备策略/显式降级/禁用 CPU 回退的运行时层；
  Chinese-CLIP 图像塔与文本塔真实 CUDA 推理（1320/395 节点全部 CUDA，禁用回退），CPU/GPU FP32 最大差 3.13e-07/1.79e-07、
  cosine 1.0、Top-K 与 P0-03 记录一致；暖机 p50 图像 11.76 ms / 文本 10.29 ms，显存峰值 1805 MiB（worker 增量约 1194 MiB）；
  managed worker 默认 CUDA 闭环（text/image encode + 内部 demo-search + provider 证据）与显式 CPU 降级；
  CUDA 不可用与 gpu_required 拒绝 CPU 的结构化失败；InsightFace 检测/识别 CUDA smoke（识别 130 节点全 CUDA，
  检测 141 CUDA/12 CPU 记录在案）；RapidOCR 实际 provider 与延迟（预加载后 75.95 ms vs CPU 917.94 ms，未预加载时静默回退）；
  GPU onedir 包（21 个 NVIDIA DLL，3.01 GiB）+ 中文/空格路径/离线/显式 CPU 降级 smoke；
  B 阶段 evaluator、数据契约、split/泄漏校验与 nDCG@20/Recall@50 指标 + 单测；
  用户授权 5 个来源共 3231 张图片（934 含人脸、1141 含文字）+ 150 条自制中文日常查询（50/100 划分），
  基线 ViT-B/16（nDCG@20 0.2592、holdout 0.2415、显存 1151 MiB）对比候选 ViT-L/14（0.2672、holdout 0.2430、2687 MiB），
  随机基线 0.1100；结论保留 ViT-B/16，ViT-L/14 记为高显存可选增强
修改文件与接口/迁移变化：见上表；ProcessorSpec 增加 device_policy/runtime_provider/runtime_version/precision/cpu_fallback_disabled；
  worker 协议新增 demo_search/provider_usage 动作与 error.details（向后兼容，无数据库迁移）
实际命令和结果：见上表（uv lock/sync、pytest 44 passed 1 skipped、ruff、git diff --check、五个 runtime/packaging 探针全部退出码 0）
报告/详细日志路径：docs/reports/P0-06-gpu-and-model-eval.md 及 P0-06-*.json 证据文件
未验证项和具体阻塞：B 质量评测缺少用户授权的 ≥2000 图样本（BLOCKED）；GTX 1660 6 GB 实机（P4-07/P4-08）；
  动态 batch/FP16/量化/TensorRT；建库吞吐与前台争用；API 层 P95；OCR 设备最终决策
失败修复轮次：5（K32GetProcessMemoryInfo、CUDA UMD 解析、Pillow 弃用调用、探针导入路径、detection 严格 CUDA 断言）
下一任务：P0-07（汇总 P0 可行性、失败组件与进入 P1 的约束），推荐 DS-L / DeepSeek V4.1 Flash / 低推理，需用户手动切换模型后执行
独立审查者与结果：未执行；若 B 阶段结果触发模型路线变化，按任务卡交 C2 / GPT-5.6 Sol / high 审查
```
