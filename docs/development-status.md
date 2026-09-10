# Development status

## Current phase

P0-02A — PASS. Windows CPython 3.12.10 (bundled SQLite 3.49.1) can create an on-disk FTS5
table, answer real `MATCH` queries for a Chinese token and an English token, and return
identical results after closing and reopening the same database file.

## Next task

P0-02B — LanceDB local persistence, restart, and CRUD probe.

- Target execution model: DeepSeek V4.1 Flash (`deepseek-flash`), DS-L, thinking enabled,
  reasoning `low`, standard speed.
- Task card: `docs/tasks/P0-02B.md`.
- The current Codex client cannot select that model. Execute the card in the DeepSeek
  client, or record `未在指定客户端执行` exactly as `docs/reports/P0-02A-sqlite-fts5.md`
  records it for P0-02A.

## Completed behavior

- P0-01: local FastAPI application exposes `GET /api/v1/health`; it loads no model,
  database, vector store, or photo source.
- P0-02A: `tools/benchmarks/sqlite_fts5_probe.py` builds a throw-away on-disk database with
  a content table plus an FTS5 table, writes two rows, runs `MATCH` for `海边日落` and
  `Sunset`, closes and reopens the file, and re-runs both queries. It prints a
  machine-readable JSON record and exits non-zero with the SQLite version, exception type,
  and message when FTS5 creation or matching fails. No product code changed.

## Files changed

P0-02A added `tools/benchmarks/sqlite_fts5_probe.py`,
`tests/integration/test_sqlite_fts5_probe.py`, and
`docs/reports/P0-02A-sqlite-fts5.md`. `pyproject.toml` and `uv.lock` are unchanged and still
lock the P0-01 dependency set; only the standard library `sqlite3` was used.

## Database/API changes

No product database, migration, or API change. `GET /api/v1/health` is unchanged. The probe
database is temporary, is deleted on exit, and is not a catalog: nothing in it implements
`search_documents`, migrations, or HTTP search.

## Commands and results

- `uv run python --version` — Python 3.12.10.
- `uv run python tools/benchmarks/sqlite_fts5_probe.py` — exit 0; `"status": "pass"`,
  `sqlite_version` 3.49.1, `fts5_available` true, `restart_query_results_match` true,
  `temp_directory_removed` true.
- `uv run pytest tests/integration/test_sqlite_fts5_probe.py` — 3 passed.
- `uv run ruff check .` — passed (one earlier round fixed two `E501` findings).
- `uv run pytest tests/unit tests/integration` — 4 passed.
- `git diff --check` (with the new files marked intent-to-add) — no whitespace errors.

The full baseline, the recorded environment values, the probe JSON excerpt, and the residual
file check are in `docs/reports/P0-02A-sqlite-fts5.md`.

## Known limits

Chinese word segmentation is not implemented or verified: the built-in `unicode61`
tokenizer stores `海边日落` as one token, so the recorded sub-string query `日落` returned
nothing. Query escaping, prefix/phrase queries, update/delete maintenance, and
external-content FTS5 trigger synchronisation are untested. WAL was observed as `wal` on a
local temporary directory only; concurrent-process and network-path WAL behaviour is not
verified. No LanceDB, ONNX Runtime, model, worker, packaging, frontend, or photo-library
operation exists yet, and no DeepSeek API request has been made.

## Next step

P0-02B — LanceDB local persistence, restart, and CRUD probe, with the DS-L card in
`docs/tasks/P0-02B.md`; then P0-02C for filter and unindexed-new-item search coverage.
