# Development status

## Current phase

P0-01 — environment inventory and minimum runnable project skeleton.

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