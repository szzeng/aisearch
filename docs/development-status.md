# Development status

## Current state

P0-04 is complete: **PASS**. Face (InsightFace `buffalo_l` detection + recognition) and OCR (RapidOCR PP-OCRv4 mobile) weights are pinned in the user data directory with sources, sizes, SHA-256 digests, measured ONNX I/O, preprocessing and license records, and a real CPU probe recognized Chinese/ASCII text fixtures and detected one face with a 512-dimension embedding. P0-03 was re-verified in the same session: its report carried a wrong revision string and the status file had never been updated.

Evidence: `docs/reports/P0-04-face-ocr-load.md` and `docs/reports/P0-03-chinese-clip-onnx.md`.

## Next task

P0-05 — AI subprocess spawn, bounded messages, exit and error propagation, plus a minimal onedir package.

- Target: C1 — GPT-5.6 Terra, `medium`, standard speed.
- Task card: `docs/tasks/P0-05.md`.
- Workspace session note: this session ran as DS-L (`deepseek-flash`, reasoning `low`), matching the P0-04 card; P0-05 is assigned to Terra, so switch the client before starting it.
- Needs PyInstaller, the P0-03 ONNX weights, and a directory containing Chinese characters and spaces for the launch check.

## Implemented baseline

- P0-01: local FastAPI `GET /api/v1/health`; no model, database, vector store or photo source is loaded.
- P0-02A/B/C: temporary SQLite FTS5 and LanceDB probes cover real queries, persistence, mutation, filtering and immediate unindexed retrieval. No product module imports SQLite or LanceDB yet.
- P0-03: Chinese-CLIP preparation tools and manifest under `tools/prepare_models/`; weights and ONNX exports live in `PhotoSearchData\models\chinese_clip\vit-b-16`, outside the repository.
- P0-04: `tools/prepare_models/{face_ocr_common,prepare_face_ocr,verify_face_ocr}.py` plus `manifests/face-ocr.json`; OCR and face weights live in `PhotoSearchData\models\{ocr,face}`. No product module imports them yet.
- Environment: Windows CPython 3.12.10, uv 0.11.0; `uv.lock` holds 88 packages including onnxruntime 1.30.0, torch 2.14.0, transformers 4.46.3, Pillow 11.3.0, rapidocr-onnxruntime 1.4.4, insightface 2.0 and LanceDB 0.38.0.

## Latest verification

2026-09-11 (DS-L session). P0-03 re-verification: `verify_chinese_clip_v2.py` exit 0 with values identical to the committed record (image max abs diff 1.9e-06, text 1.5e-05, Top-K identical). P0-04: `uv lock` 88 packages; prepare script exit 0; probe exit 0 (OCR cjk 2 boxes at 0.9991/0.9999, ascii 2 boxes, blank empty; face sample 1 detection with `det_score` 0.8304 and a `[512]` embedding); `uv run pytest tests/unit tests/integration` 13 passed; `uv run ruff check .` passed; `git diff --check` passed. Full commands and outputs are in the two linked reports.

## Known limits

Unverified: OCR accuracy and reading order on real screenshots, face-recognition accuracy and thresholds, GPU execution, batching, quantization, worker and packaging integration, the `rapidocr` 2.x/3.x line, and the redistribution terms of the InsightFace weights. No real photo library has been read in any task. Two environment defects are recorded: the managed sandbox helper cannot start on this machine (`SetNamedSecurityInfoW` error 5 on `.codex/.sandbox-bin`), so every command ran escalated, and the `apply_patch` tool fails for the same reason.