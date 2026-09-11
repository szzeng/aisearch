# Development status

## Current state

P0-05 is complete: **PASS**. Chinese-CLIP runs in a managed spawn worker with bounded 1 MiB JSON messages, file-path image inputs, structured failure propagation, timeout cleanup and an offline CPU demo. A PyInstaller onedir package was built and its executable completed the demo from a temporary directory containing Chinese characters and spaces.

P0-05A housekeeping is also complete: Python/build ignore rules were restored, tracked `.pyc` files were removed, and the worker stderr temporary file descriptor/file lifecycle was fixed.

Evidence: `docs/reports/P0-05-worker-and-packaging.md` plus the P0-05A housekeeping commits.

Current execution strategy: `个人图片搜索系统_V3.1_Codex_DeepSeek_开发执行策略.md`.

## Next task

P0-06 — establish at least 2,000 authorized samples and 100–200 queries, then compare the frozen baseline with one stronger feasible candidate.

- Target: **DS-H / DeepSeek V4.1 Flash / 高推理**.
- Environment/Windows/ONNX failures: escalate to **C1 / GPT-5.6 Terra / medium**.
- Architecture/model-route decision triggered by evidence: **C2 / GPT-5.6 Sol / high** review.
- Task card: `docs/tasks/P0-06.md`.
- Do not read a real photo library until the user explicitly authorizes the sample source.
- If no authorized evaluation set is available, evaluator tooling may proceed but quality validation must be marked **BLOCKED**, not PASS.
