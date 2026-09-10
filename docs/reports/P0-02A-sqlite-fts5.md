# P0-02A — Windows SQLite FTS5 capability probe

**Status:** PASS, executed 2026-09-10. One fix round (Ruff line length only; no SQL or
Windows failure).

## Task record

| Field | Value |
|---|---|
| Task | P0-02A — Windows SQLite FTS5 capability probe |
| Planned model | DeepSeek V4.1 Flash, official API model `deepseek-flash`, tier DS-L, thinking enabled, reasoning `low`, standard speed |
| Actual execution | **未在指定客户端执行.** DeepSeek V4.1 Flash was not selectable in this Codex session, so the probe was executed locally by the current Codex agent. No DeepSeek API request was made and no model switch is claimed. The client did not expose a model identifier, so the executed model id is recorded as unavailable rather than guessed. |
| Git baseline at start | `e0f54c1` (`增加P0-02A的任务卡`). The task card named `692a52c`; the only difference is the card commit itself. Working tree was clean; there were no user modifications to preserve. |
| Previous task | P0-01, PASS, `docs/reports/P0-01-environment-and-skeleton.md` |

## Files changed

- `tools/benchmarks/sqlite_fts5_probe.py` (new) — runnable probe.
- `tests/integration/test_sqlite_fts5_probe.py` (new) — regression tests.
- `docs/reports/P0-02A-sqlite-fts5.md` (new) — this report.
- `docs/development-status.md` (updated) — phase, commands, next task.

`src/photo_search/`, `pyproject.toml`, and `uv.lock` were not touched. Only the standard
library `sqlite3` is used.

## Observed environment

| Item | Observed value |
|---|---|
| Interpreter | CPython 3.12.10 (`.venv/Scripts/python.exe`), 64-bit |
| `sqlite3.sqlite_version` | `3.49.1` |
| `sqlite_source_id()` | `2025-02-18 13:38:58 873d4e274b4988d260ba8354a9718324a1c26187a4ab4c1cc0227c03d0f10e70` |
| `PRAGMA journal_mode = WAL` | observed `wal` |
| Probe database size | 28,672 bytes |
| Threadsafety reported by `sqlite3` | 3 |

## What the probe actually does

On one fresh on-disk database in a temporary directory it:

1. creates a plain content table `probe_documents(id, title, body)` and an independent FTS5
   table `CREATE VIRTUAL TABLE probe_fts USING fts5(title, body)`;
2. inserts two rows in one transaction — row 1 holds the Chinese token `海边日落`, row 2
   holds the English token `Sunset`;
3. runs `INSERT INTO probe_fts(probe_fts) VALUES('integrity-check')`;
4. runs real `SELECT rowid FROM probe_fts WHERE probe_fts MATCH ?` queries on both terms;
5. closes the connection, reopens the same database file, and repeats both queries;
6. asserts the reopened results are identical and records every result as JSON.

FTS5 creation failure, `MATCH` failure, an unexpected assertion, and leftover temporary
files all produce `"status": "fail"` with a `failure` block and a non-zero exit code. The
probe never prints PASS without real SQL behind it.

## Commands, exit codes, results

| Command | Exit | Result |
|---|---|---|
| `uv run python --version` | 0 | `Python 3.12.10` |
| `uv run python tools/benchmarks/sqlite_fts5_probe.py` | 0 | JSON record with `"status": "pass"`, `fts5_available: true`, `restart_query_results_match: true`, `temp_directory_removed: true` |
| `uv run pytest tests/integration/test_sqlite_fts5_probe.py` | 0 | `3 passed in 0.33s` |
| `uv run ruff check .` | 0 | `All checks passed!` (first run: `E501` x2, fixed before this run) |
| `uv run pytest tests/unit tests/integration` | 0 | `4 passed in 0.60s` |
| `git add -N tools tests` then `git diff --check` | 0 | no whitespace errors, including the new files |

Key `MATCH` results, both before and after reopening the database:

| Stage | Query | Expected rowid | Actual rowid |
|---|---|---|---|
| first_connection | `海边日落` | `[1]` | `[1]` |
| first_connection | `Sunset` | `[2]` | `[2]` |
| after_reopen | `海边日落` | `[1]` | `[1]` |
| after_reopen | `Sunset` | `[2]` | `[2]` |

Recorded boundary observation: the query `日落` returned `[]` against the stored token
`海边日落`. The built-in `unicode61` tokenizer keeps a run of CJK characters as a single
token, so this probe claims **no** Chinese word segmentation. Production text search needs
the segmentation and query constructor fixed by the architecture (§4.3, main §9.4) before
sub-word Chinese queries can work.

## Probe output (excerpt, temp path shortened)

```json
{
  "task": "P0-02A",
  "status": "pass",
  "python_version": "3.12.10",
  "python_implementation": "CPython",
  "sqlite_version": "3.49.1",
  "database_path": "%TEMP%\\photo_search_fts5_probe_02p1y_u5\\fts5_probe.db",
  "database_size_bytes": 28672,
  "journal_mode": "wal",
  "fts5_available": true,
  "fts5_create_sql": "CREATE VIRTUAL TABLE probe_fts USING fts5(title, body)",
  "steps": [
    {"name": "create_documents_table", "ok": true, "sql": "CREATE TABLE probe_documents (id INTEGER PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL)"},
    {"name": "create_fts5_table", "ok": true, "sql": "CREATE VIRTUAL TABLE probe_fts USING fts5(title, body)"},
    {"name": "seed_rows", "ok": true, "rowids": [1, 2], "integrity_check": "INSERT INTO probe_fts(probe_fts) VALUES('integrity-check')"},
    {"name": "close_and_reopen", "ok": true}
  ],
  "queries": [
    {"stage": "first_connection", "match": "海边日落", "expected_rowids": [1], "actual_rowids": [1], "passed": true},
    {"stage": "first_connection", "match": "Sunset", "expected_rowids": [2], "actual_rowids": [2], "passed": true},
    {"stage": "after_reopen", "match": "海边日落", "expected_rowids": [1], "actual_rowids": [1], "passed": true},
    {"stage": "after_reopen", "match": "Sunset", "expected_rowids": [2], "actual_rowids": [2], "passed": true}
  ],
  "boundary_observations": [
    {"stage": "first_connection", "match": "日落", "actual_rowids": [], "note": "sub-string of a stored CJK token; no word segmentation is claimed"}
  ],
  "restart_query_results_match": true,
  "temp_directory_removed": true,
  "residual_files": [],
  "failure": null
}
```

## Residual files

Robustness check after the documented runs:

- the probe-managed temp directory is removed (`temp_directory_removed: true`, `residual_files: []`);
- `Get-ChildItem -Recurse -Include *.db,*.db-wal,*.db-shm` inside the repository returned
  nothing;
- `%TEMP%\photo_search_fts5_probe_*` contained 0 directories.

The integration test writes its database under pytest `tmp_path`, and `--database` refuses
to overwrite an existing file, so the probe cannot silently clobber a real catalog.

## Fix rounds

1. Ruff reported two `E501` line-length violations in the new probe (the `seed_rows` field
   and the `PRAGMA journal_mode` call). Both were reformatted; Ruff then passed. No SQL or
   Windows failure occurred, so no C1 (GPT-5.6 Terra / medium) handoff was needed, and no
   protocol ambiguity required C2.

## Not verified here

- Chinese word segmentation (`jieba`) and the controlled `MATCH` query constructor; the
  probe intentionally exercises only one exact Chinese token query.
- Query escaping, prefix/phrase/boolean queries, and sub-string Chinese search.
- External-content FTS5 tables with transaction-maintained trigger synchronisation
  (architecture §4.3), and update/delete maintenance.
- WAL behaviour under concurrent processes or on network paths; only a clean open/close on
  a local NTFS temporary directory was observed.
- Any product schema, migration, `search_documents` table, FTS index of real photos, or
  HTTP search endpoint. `GET /api/v1/health` is unchanged and was re-run by the test suite.
- LanceDB persistence/restart/CRUD/filter/unindexed-new-item probes (P0-02B, P0-02C).

## Usage and cost

No DeepSeek API request was made, so there is no DeepSeek request, cache, token, or cost
record to report. No GPT API key was used either; the work ran inside the current client
session.

## Handoff record (strategy §8.4)

```text
任务 ID / 状态：P0-02A / PASS（lint 修复 1 轮，非模型失败）
实际模型 / reasoning / 速度：未在指定客户端执行（DeepSeek V4.1 Flash 不可选）；实际由当前 Codex 代理执行，模型 ID 未暴露，未做切换声明
基线提交号：e0f54c1（任务卡原写 692a52c，差异仅为任务卡提交本身）
已完成行为：临时落盘库上创建内容表 + FTS5 表，写入中英两条记录，执行真实 MATCH，关闭重开后结果一致；失败路径非零退出且不谎报 PASS
修改文件与接口/迁移变化：tools/benchmarks/sqlite_fts5_probe.py、tests/integration/test_sqlite_fts5_probe.py、docs/reports/P0-02A-sqlite-fts5.md、docs/development-status.md；无接口、无迁移、无依赖变化
实际命令和结果：uv run python --version(0) / uv run python tools/benchmarks/sqlite_fts5_probe.py(0, status=pass) / uv run pytest tests/integration/test_sqlite_fts5_probe.py(0, 3 passed) / uv run ruff check .(0) / uv run pytest tests/unit tests/integration(0, 4 passed) / git diff --check(0)
报告/详细日志路径：docs/reports/P0-02A-sqlite-fts5.md
未验证项和具体阻塞：中文分词与受控 MATCH 构造器、外部内容表触发器同步、并发 WAL、LanceDB 相关探针
失败修复轮次：1 轮（Ruff E501 x2）
下一任务及必须读取的文件：P0-02B（LanceDB 本地落盘、重启与增删改探针），任务卡 docs/tasks/P0-02B.md；需读 AGENTS.md、docs/development-status.md、主架构 §2、§7、§12.4、§14、§15 与开发策略 §2.1-2.3、§6.1、§8.2、本报告
可见 GPT 额度观察：未测量
DS 全任务请求用量/缓存/费用：无（未发起 DeepSeek 请求）
独立审查者与结果：本任务不需要独立审查
```

## Next task

**P0-02B — LanceDB local persistence, restart, and CRUD probe** (`docs/tasks/P0-02B.md`).
Planned model per strategy §6.1: DS-L / DeepSeek V4.1 Flash / thinking `low`. The current
Codex client cannot select that model, so the card must be executed in the DeepSeek client,
or the deviation must be recorded the same way this report records it.
