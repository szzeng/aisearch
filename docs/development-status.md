# Development status

## Current state

P0-05 is complete: **PASS**. Chinese-CLIP runs in a managed spawn worker with bounded 1 MiB JSON messages, file-path image inputs, structured failure propagation, timeout cleanup and an offline CPU demo. A PyInstaller onedir package was built and its executable completed the demo from a temporary directory containing Chinese characters and spaces.

Evidence: `docs/reports/P0-05-worker-and-packaging.md`.

## Next task

P0-06 — establish at least 2,000 authorized samples and 100–200 queries, then compare the frozen baseline with one stronger candidate.

- Target: DS-L (`deepseek-flash`), reasoning low; C1 / Terra reviews method and results.
- Task card: `docs/tasks/P0-06.md`.
- Do not read a real photo library until the user explicitly authorizes the sample source.
