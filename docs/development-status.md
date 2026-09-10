# Development status

## Current phase

P0-02C — PASS; **P0-02 is complete.** On Windows CPython 3.12.10 with LanceDB 0.38.0, a
`where()` filter restricts candidates before ranking (a `limit = 3` query returns a row whose
global rank is 6, which "global top-k then filter" could not produce), hidden rows never leak
into a `visible = true` query, a newly added row is immediately searchable with no ANN index,
no maintenance call, and `fast_search` disabled, and an invalid filter column raises
`ValueError` with a non-zero exit instead of silently returning unfiltered rows.

## Next task

P0-03 — Chinese-CLIP image/text towers, tokenizer, ONNX export, and manifest.

- Target execution model: C1 — GPT-5.6 Terra, `medium` reasoning, standard speed.
- Task card: `docs/tasks/P0-03.md`.
- P0-03 needs ONNX Runtime, model weights, network access, and disk space; follow the card's
  scope and authorization requirements, and record the actual model used.

## Completed behavior

- P0-01: local FastAPI application exposes `GET /api/v1/health`; it loads no model, database,
  vector store, or photo source.
- P0-02A: `tools/benchmarks/sqlite_fts5_probe.py` proves on-disk SQLite FTS5 creation, real
  `MATCH` queries for a Chinese and an English token, and identical results after reopening.
- P0-02B: `tools/benchmarks/lancedb_probe.py` proves LanceDB table creation with an explicit
  Arrow schema, real vector-search ordering, cross-process persistence, and add/update/delete
  with read-back and ranking assertions.
- P0-02C: `tools/benchmarks/lancedb_filter_probe.py` proves pre-filtered candidate retrieval,
  hidden-row exclusion, AND-condition narrowing, and immediate visibility of an unindexed new
  row; its `--invalid-filter-check` mode proves an invalid filter fails loudly.

All three probes print machine-readable JSON, exit non-zero with the real stage and exception
on failure, and delete their temporary databases. No product code changed in P0-02.

## Files changed

P0-02C added `tools/benchmarks/lancedb_filter_probe.py`,
`tests/integration/test_lancedb_filter_probe.py`, and `docs/reports/P0-02C-lancedb-filter.md`.
`pyproject.toml` and `uv.lock` are unchanged since P0-02B and still resolve 34 packages
(`lancedb 0.38.0`, `pyarrow 25.0.1`, `numpy 2.5.3`, plus the P0-01 set). No product module
imports LanceDB or SQLite yet.

## Database/API changes

No product database, migration, or API change. `GET /api/v1/health` is unchanged. All probe
databases are temporary and deleted on exit; none of them is a catalog.

## Commands and results

- `uv run python --version` — Python 3.12.10.
- `uv lock` — resolved 34 packages with no lock change.
- `uv run python tools/benchmarks/lancedb_filter_probe.py` — exit 0; `"status": "pass"`, eight
  queries passed, `row_count` 8, `temp_directory_removed` true, `residual_files` empty.
- `uv run python tools/benchmarks/lancedb_filter_probe.py --invalid-filter-check` — exit 1 with
  `failure.stage = invalid_filter`, `ValueError`, and `invalid_filter.returned_ids = null`.
- `uv run pytest tests/integration/test_lancedb_filter_probe.py` — 3 passed.
- `uv run ruff check .` — passed.
- `uv run pytest tests/unit tests/integration` — 10 passed.
- `git diff --check` (with new files marked intent-to-add) — no whitespace errors.

Filter tables, exclusion and pre-filter evidence, the invalid-filter record, and the probe JSON
excerpt are in `docs/reports/P0-02C-lancedb-filter.md`.

## Known limits

No ANN index, recall, latency, or scale claim exists: P0-02C ran on an 8-row table with no
index. Filter pushdown efficiency, negation/NULL semantics, `OR`/`IN`/`LIKE` combinations,
concurrent writers, maintenance-during-query, and disk-exhaustion behaviour are untested. Cosine
and dot metrics, `merge_insert`, projections, and schema evolution are untested. Only LanceDB
0.38.0 and SQLite 3.49.1 on Windows CPython 3.12.10 were observed. Chinese word segmentation is
still unimplemented, and no model, worker, packaging, frontend, or photo-library operation
exists yet. No DeepSeek or GPT API request has been made.

## Next step

P0-03 — Chinese-CLIP image/text towers, tokenizer, ONNX export, and manifest, with the C1 card in
`docs/tasks/P0-03.md`; then P0-04 (Face/RapidOCR runtime probes), P0-05 (AI subprocess and minimal
onedir packaging), P0-06 (sample evaluation), and P0-07 (P0 summary).
