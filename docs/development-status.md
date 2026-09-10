# Development status

## Current phase

P0-01 — PASS. Environment inventory and minimum runnable project skeleton are complete.

## Next task

P0-02A — Windows SQLite FTS5 capability probe.

- Target execution model: DeepSeek V4.1 Flash (`deepseek-flash`), DS-L, reasoning `low`.
- Task card: `docs/tasks/P0-02A.md`.
- The current Codex client has not switched to the target DeepSeek client; execute the
  card there, or record C1/Terra handoff if that client is unavailable.

## Completed behavior

The local FastAPI application exposes `GET /api/v1/health`. It loads no model,
database, vector store, or photo source.

## Files changed

`pyproject.toml`, `uv.lock`, `README.md`, `AGENTS.md`, `.gitignore`,
`src/photo_search/`, `tests/unit/`, and `docs/` (including the P0-01 report).

## Database/API changes

No database changes. Added `GET /api/v1/health`, returning process readiness only.

## Commands and results

- `uv lock; uv sync --all-groups` — resolved and installed 24 packages with CPython 3.12.10.
- `uv run ruff check .` — passed.
- `uv run pytest tests/unit` — passed (one health-endpoint test).
- `uv run python -c "... TestClient(app).get('/api/v1/health') ..."` — HTTP 200 with `{"status":"ok","service":"photo-search"}`.

Full system inventory, exact resolved top-level dependencies, and frontend-tooling
status are in `docs/reports/P0-01-environment-and-skeleton.md`.

## Known limits

P0-02 probes (SQLite FTS5 and LanceDB persistence) have not run. The available Node
binary has no `npm` or `corepack` on PATH, so no frontend project or package lock has
been fabricated. No model, worker, packaging, frontend UI, database schema, or
photo-library operation exists.

## Next step

P0-02 — SQLite FTS5 and LanceDB Windows probes with locked dependencies.