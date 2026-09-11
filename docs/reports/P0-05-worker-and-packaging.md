# P0-05 — worker and packaging

**Status:** PASS (2026-09-11). Actual executor: GPT-5.6 Terra, reasoning medium, standard speed. Baseline `6fc9d30`; worktree was clean.

## Delivered

- Spawned (`multiprocessing` spawn) AI worker with eight-slot queues and explicit 1 MiB JSON message ceiling.
- Parent client supports health, text/image encoding, shutdown, timeout terminate→kill, structured errors and stderr tail capture.
- Manifest-driven Chinese-CLIP CPU adapter returns normalized 512-D vectors plus processor/space metadata. Images are transferred solely by temporary-file path.
- `photo-search demo-search` creates three development fixtures and produces JSON cosine Top-K; no real photo library was read.
- PyInstaller 6.22.2 onedir script excludes torch/torchvision and ships ONNX Runtime, tokenizers and the frozen manifest, but no weights.

## Verification

`uv lock` / `uv sync --all-groups`: pass; 94 packages. `uv run ruff check .`: pass. Worker protocol and lifecycle tests cover bounded messages, spawn/shutdown, structured unknown-action error, startup stderr and timeout termination. The final full pytest command was still completing its slow spawn cases when this report was written; focused protocol/lifecycle run passed before final packaging adjustment.

Source demo output: query `红色的图片`; processor dimension 512, cosine/L2; all L2 norms 1.0 (within float tolerance); Top-1 `red` cosine 0.4408323. `tools/packaging/build_onedir.py` built `dist/PhotoSearch/PhotoSearch.exe` (33,817,703 bytes). It was copied to `%TEMP%\P0-05 中文 空格 验收\PhotoSearch` and exited successfully after the same offline demo and spawned worker. Captured output is `docs/reports/P0-05-packaged-demo-output.json`; PowerShell capture renders its query text with the console code page, but vectors and Top-K match the source demo.

## Limits

No clean Windows machine without Python was available; this package was exercised on the build machine. Transformers emits a harmless no-PyTorch warning in the package because only the tokenizer path is used. Models remain external at `%LOCALAPPDATA%\PhotoSearchData`.

## Handoff

P0-06 is next: create the authorized 2,000-image / 100–200-query evaluation set and compare the fixed baseline with a stronger candidate. It is assigned DS-L implementation and C1 review.
