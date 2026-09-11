# P0-03 — Chinese-CLIP ONNX

**Status:** PASS (2026-09-11).

Actual executor: current Codex agent; the requested `gpt-5.6-terra` / medium selection is not exposed in this session, so no model switch is claimed. Baseline: `63b2f714f1c2c6fea576e0f4379803e5eee15bee`.

## Delivered and verified

- Official `OFA-Sys/chinese-clip-vit-base-patch16`, revision `36e679e65c2a2fead755ae21162091293ad37834`, was downloaded outside the repository. (The string originally written here, `d5e62df2419c7f5794ce0c4f3be9872a2ee13c8d`, was wrong; corrected 2026-09-11 against the Hugging Face cache metadata and `manifests/chinese-clip-vit-b-16.json`.)
- Original checkpoint: 753,196,934 bytes; Transformers checkpoint: 753,177,983 bytes. The manifest records each SHA-256, source URL, tokenizer vocabulary, preprocessing, license link, inputs, outputs and runtime versions.
- FP32 batch=1 ONNX: image tower 344,912,315 bytes; text tower 408,416,523 bytes.
- CPU fixture: generated RGB gradient/checkerboard plus five fixed Chinese queries. Both routes produce 512-D vectors; L2 normalization is required before cosine retrieval.
- Image maximum absolute difference: `1.9073486328125e-06`; text maximum: `1.52587890625e-05`; lowest cosine: `0.9999998807907104`; Top-K is identical: `[0, 1, 2, 4, 3]`.
- ONNX Runtime providers: `AzureExecutionProvider`, `CPUExecutionProvider`; sessions explicitly used CPU. No CUDA provider was available or exercised.

## Dependencies and commands

Locked: torch `2.14.0+cpu`, torchvision `0.29.0+cpu`, transformers `4.46.3`, onnx `1.22.0`, onnxruntime `1.30.0`, Pillow `11.3.0`.

`uv lock`, `uv sync --all-groups`, `uv run python tools/prepare_models/prepare_chinese_clip_v2.py`, and `uv run python tools/prepare_models/verify_chinese_clip_v2.py --model-dir <external-model-dir>` succeeded. `uv run pytest tests/unit tests/integration` passed (11). `uv run ruff check .` passed. `git diff --check` passed.

The initial Transformers `4.57.6` release returned no text pooler for this checkpoint; pinning `4.46.x` restored the checkpoint's documented CLS text feature path. A first assumed Safetensors/tokenizer-file layout was corrected to the actual published files. No real photo library was read.

## Not verified

Dynamic batching, FP16/quantization, CUDA execution/performance, retrieval quality, concurrency, full-library indexing, and runtime product integration are deliberately out of scope.

## Post-completion re-verification (2026-09-11, DS-L session)

The status file had not been updated by the P0-03 session and this report's revision string did not
match the manifest, so the artifacts were re-checked from the repository and from disk:

- `%LOCALAPPDATA%\PhotoSearchData\models\chinese_clip\vit-b-16` still holds the five repository
  files (753,196,934 / 3,008 / 342 / 753,177,983 / 109,540 bytes) plus `image_tower.fp32.onnx`
  (344,912,315 bytes) and `text_tower.fp32.onnx` (408,416,523 bytes).
- The Hugging Face cache metadata (`\.cache\huggingface\download\*.metadata`) records revision
  `36e679e65c2a2fead755ae21162091293ad37834` for every file, matching
  `manifests/chinese-clip-vit-b-16.json`; the `d5e62df...` string previously printed here was
  wrong and is corrected above.
- `uv lock` re-resolved 88 packages with no change (the count grew from 34 as P0-04 added the OCR
  and face dependencies).
- `uv run python tools/prepare_models/verify_chinese_clip_v2.py --model-dir <model dir>` exited 0
  with `status: pass` and values identical to the committed validation record: image `[1,512]`
  maximum absolute difference 1.9073486328125e-06, text `[5,512]` maximum absolute difference
  1.52587890625e-05, lowest cosine 0.9999998807907104, Top-K identical `[0, 1, 2, 4, 3]`,
  providers `['AzureExecutionProvider', 'CPUExecutionProvider']` and CPU-only sessions.
- `uv run ruff check .` passed, `uv run pytest tests/unit tests/integration` passed (11 tests at
  that moment, 13 after P0-04), `git diff --check` passed and the working tree was clean.