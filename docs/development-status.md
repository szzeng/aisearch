# Development status

## Current state

P0-02 is complete. The latest task, P0-02C, is **PASS**: LanceDB candidate filters are applied before ranking, hidden rows do not leak into visible queries, unindexed additions are immediately searchable, and invalid filter columns fail loudly.

Evidence: `docs/reports/P0-02C-lancedb-filter.md`.

## Next task

P0-03 — Chinese-CLIP image/text towers, tokenizer, ONNX export, and manifest.

- Target: C1 — GPT-5.6 Terra, `medium` reasoning, standard speed.
- Task card: `docs/tasks/P0-03.md`.
- Needs ONNX Runtime, model weights, network access, and disk space; follow the task card's authorization requirements and record the actual model used.
- Read `AGENTS.md`, this status, and the task card first; load only the architecture and execution-strategy sections listed in the task card.

## Implemented baseline

- P0-01: local FastAPI `GET /api/v1/health`; no model, database, vector store, or photo source is loaded.
- P0-02A/B/C: temporary SQLite FTS5 and LanceDB probes cover real queries, persistence, mutation, filtering, and immediate unindexed retrieval. No product module imports SQLite or LanceDB yet.
- Environment: Windows CPython 3.12.10; locked dependencies include LanceDB 0.38.0, PyArrow 25.0.1, and NumPy 2.5.3.

## Latest verification

P0-02C: probe PASS; invalid-filter mode failed as expected; integration tests 3 passed; `uv run ruff check .` passed; full unit/integration suite 10 passed; `git diff --check` passed. Full commands and outputs are in the linked report.

## Known limits

No ANN, recall, latency, scale, concurrency, maintenance-during-query, or disk-exhaustion claim exists. Chinese segmentation, models, worker, packaging, frontend, and real photo-library operations remain unimplemented. No current blocker is recorded.
