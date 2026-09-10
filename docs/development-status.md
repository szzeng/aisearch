# Development status

## Current phase

P0-02B — PASS. Embedded LanceDB 0.38.0 on Windows CPython 3.12.10 writes an on-disk table
with an explicit `fixed_size_list<float>[4]` schema, answers a real nearest-neighbour search,
survives reopening from a separate Python process, and performs add / update / delete with the
expected ranking and stored state after every operation.

## Next task

P0-02C — LanceDB candidate filtering and unindexed-new-item retrieval probe.

- Target execution model: DeepSeek V4.1 Flash (`deepseek-flash`), DS-L, thinking enabled,
  reasoning `low`, standard speed.
- Task card: `docs/tasks/P0-02C.md`.
- The current Codex client cannot select that model. Execute the card in the DeepSeek client,
  or record `未在指定客户端执行` exactly as `docs/reports/P0-02A-sqlite-fts5.md` and
  `docs/reports/P0-02B-lancedb.md` record it.

## Completed behavior

- P0-01: local FastAPI application exposes `GET /api/v1/health`; it loads no model, database,
  vector store, or photo source.
- P0-02A: `tools/benchmarks/sqlite_fts5_probe.py` proves on-disk SQLite FTS5 creation, real
  `MATCH` queries for a Chinese and an English token, and identical results after reopening.
- P0-02B: `tools/benchmarks/lancedb_probe.py` proves LanceDB table creation with an explicit
  Arrow schema, real vector search ordering, cross-process persistence, and add/update/delete
  with read-back and ranking assertions. It prints a machine-readable JSON record and exits
  non-zero with the real stage, exception type, and message on failure. No product code changed.

## Files changed

P0-02B added `tools/benchmarks/lancedb_probe.py`, `tests/integration/test_lancedb_probe.py`,
and `docs/reports/P0-02B-lancedb.md`, and changed `pyproject.toml` and `uv.lock` to add
`lancedb>=0.38.0` with its required transitive dependencies. The lock now resolves 34 packages
(24 before); `lancedb 0.38.0`, `pyarrow 25.0.1`, and `numpy 2.5.3` are the notable additions.
No product module imports LanceDB yet.

## Database/API changes

No product database, migration, or API change. `GET /api/v1/health` is unchanged. The LanceDB
directory and the SQLite file used by the probes are temporary, are deleted on exit, and are
not a catalog: nothing in them implements `search_documents`, migrations, indexes, filters, or
HTTP search.

## Commands and results

- `uv run python --version` — Python 3.12.10.
- `uv lock` — resolved 34 packages; `uv sync --all-groups` — checked 34 packages.
- `uv run python tools/benchmarks/lancedb_probe.py` — exit 0; `"status": "pass"`,
  `lancedb_version` 0.38.0, two cross-process reopen checks passed, `temp_directory_removed`
  true, `residual_files` empty.
- `uv run pytest tests/integration/test_lancedb_probe.py` — 3 passed.
- `uv run ruff check .` — passed (18 earlier `E501` findings fixed with `ruff format`).
- `uv run pytest tests/unit tests/integration` — 7 passed.
- `git diff --check` (with the new files marked intent-to-add) — no whitespace errors.

Dependency versions, the search/CRUD evidence tables, the probe JSON excerpt, the failure-path
check, and the residue check are in `docs/reports/P0-02B-lancedb.md`.

## Known limits

Candidate filtering and unindexed-new-item retrieval are not tested (P0-02C). No ANN index,
recall, or performance claim exists: the probe runs brute force over three or four rows. Cosine
and dot metrics, `merge_insert`, schema evolution, concurrent writers, and disk-exhaustion
behaviour are untested. Only LanceDB 0.38.0 on Windows CPython 3.12.10 with pyarrow 25.0.1 was
observed. Chinese word segmentation for FTS remains unimplemented, and no model, worker,
packaging, frontend, or photo-library operation exists yet. No DeepSeek API request has been made.

## Next step

P0-02C — LanceDB candidate filtering and unindexed-new-item retrieval probe, with the DS-L card
in `docs/tasks/P0-02C.md`; afterwards P0-03 (Chinese-CLIP ONNX) stays with C1 / Terra.
