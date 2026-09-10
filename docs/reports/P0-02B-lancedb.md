# P0-02B — LanceDB local persistence, restart, and CRUD probe

**Status:** PASS, executed 2026-09-10. Three in-task fix rounds (one probe assertion,
Ruff line length, one snapshot-ordering fix); no LanceDB capability or environment failure,
so no C1 handoff was needed.

## Task record

| Field | Value |
|---|---|
| Task | P0-02B — LanceDB local persistence, restart, and CRUD probe |
| Planned model | DeepSeek V4.1 Flash, official API model `deepseek-flash`, tier DS-L, thinking enabled, reasoning `low`, standard speed |
| Actual execution | **未在指定客户端执行.** DeepSeek V4.1 Flash is not selectable in this Codex session, so the probe was executed locally by the current Codex agent. No DeepSeek API request was made and no model switch is claimed. The executed model id is not exposed by the client and is recorded as unavailable rather than guessed. |
| Git baseline at start | `e7429bb` (`增加通用接手指令`). The card named `0baec27` (the P0-02A commit); the only extra commit is the user's instruction file. Working tree was clean. |
| Previous task | P0-02A, PASS, `docs/reports/P0-02A-sqlite-fts5.md` |

## Files changed

- `pyproject.toml`, `uv.lock` — added `lancedb>=0.38.0` and its required transitive
  dependencies (the only dependency change of P0-02).
- `tools/benchmarks/lancedb_probe.py` (new) — runnable probe.
- `tests/integration/test_lancedb_probe.py` (new) — regression tests.
- `docs/reports/P0-02B-lancedb.md` (new) — this report.
- `docs/development-status.md` (updated).

No product code changed: `src/photo_search/app.py` and `GET /api/v1/health` are untouched,
and no migration, schema, index, or `search/` module was created.

## Dependency change (locked)

`uv add lancedb` resolved 34 packages (24 before) and installed 11:

| Package | Version |
|---|---|
| lancedb | 0.38.0 |
| pyarrow | 25.0.1 |
| numpy | 2.5.3 |
| lance-namespace | 0.12.0 |
| lance-namespace-urllib3-client | 0.12.0 |
| deprecation | 2.1.0 |
| tqdm | 4.70.0 |
| urllib3 | 2.7.0 |
| python-dateutil | 2.9.0.post0 |
| six | 1.17.0 |

Top-level dependencies are now `fastapi 0.141.1`, `lancedb 0.38.0`, `uvicorn 0.52.4`, plus the
unchanged dev group. `uv add` also added the missing final newline to `pyproject.toml`; no
other project metadata changed.

## What the probe actually does

On one fresh LanceDB directory in a temporary folder it uses an explicit Arrow schema
(`id: int64`, `vector: fixed_size_list<float>[4]`, `label: string`), then:

1. creates the table with three seed rows (`alpha`, `beta`, `gamma`);
2. runs a real nearest-neighbour search for `[0.9, 0.4, 0.1, 0.0]` and asserts the id order;
3. reopens the **same directory from a separate Python process** (`--reopen-check` child mode)
   and asserts the child sees the same ids and row count;
4. adds a fourth row, re-searches, and asserts the extended order;
5. updates row 3's vector and label, re-searches, and reads the stored rows back;
6. deletes row 4, re-searches, scans the table, and asserts the final stored ids and labels;
7. reopens from a second separate process and asserts the post-CRUD state persists.

Failures (import, connect, create/open table, search, assertion, leftover temp files) produce
`"status": "fail"` with a `failure` block naming the stage, the exception type, and the message,
plus a non-zero exit code.

## Search and CRUD evidence

Query vector `[0.9, 0.4, 0.1, 0.0]`; distances are LanceDB's default squared-L2 `_distance`.

| Stage | Returned ids | Labels | Distances | Expected |
|---|---|---|---|---|
| after_seed | `[1, 2, 3]` | alpha, beta, gamma | 0.18, 1.18, 1.78 | `[1, 2, 3]` |
| after_add | `[1, 2, 3, 4]` | alpha, beta, gamma, delta | 0.18, 1.18, 1.78, 1.98 | `[1, 2, 3, 4]` |
| after_update | `[3, 1, 2, 4]` | gamma-moved, alpha, beta, delta | 0.0075, 0.18, 1.18, 1.98 | `[3, 1, 2, 4]` |
| after_delete | `[3, 1, 2]` | gamma-moved, alpha, beta | 0.0075, 0.18, 1.18 | `[3, 1, 2]` |

Row counts recorded per operation: `create_table` 3, `add` 4, `update` 4, `delete` 3
(`num_deleted_rows: 1`). Read-back after the update returned `1: alpha, 2: beta,
3: gamma-moved, 4: delta`; the post-delete stored state is `1: alpha, 2: beta,
3: gamma-moved`, so the deleted id is really gone from the table, not only from the ranking.

Restart evidence: both reopen checks ran in a separate Python process with exit code 0 —
`after_seed` saw `[1, 2, 3]` with 3 rows, `after_crud` saw `[3, 1, 2]` with 3 rows.

## Commands, exit codes, results

| Command | Exit | Result |
|---|---|---|
| `uv run python --version` | 0 | `Python 3.12.10` |
| `uv lock` | 0 | `Resolved 34 packages` |
| `uv sync --all-groups` | 0 | `Checked 34 packages` |
| `uv run python tools/benchmarks/lancedb_probe.py` | 0 | `"status": "pass"`, `lancedb_version 0.38.0`, `temp_directory_removed: true` |
| `uv run pytest tests/integration/test_lancedb_probe.py` | 0 | `3 passed in 7.70s` |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run pytest tests/unit tests/integration` | 0 | `7 passed in 8.33s` |
| `git add -N tools tests` then `git diff --check` | 0 | no whitespace errors |

## Probe output (excerpt; values unchanged, layout compacted)

```json
{
  "task": "P0-02B",
  "mode": "full",
  "status": "pass",
  "python_version": "3.12.10",
  "lancedb_version": "0.38.0",
  "pyarrow_version": "25.0.1",
  "database_path": "%TEMP%\\photo_search_lancedb_probe_8894_hd1\\probe.lance",
  "table": "probe_vectors",
  "vector_column": "vector",
  "vector_dim": 4,
  "schema": [
    {"name": "id", "type": "int64", "nullable": false},
    {"name": "vector", "type": "fixed_size_list<item: float>[4]", "nullable": false},
    {"name": "label", "type": "string", "nullable": false}
  ],
  "operations": [
    {"op": "create_table", "rows": [1, 2, 3], "row_count": 3},
    {"op": "add", "rows": [4], "row_count": 4},
    {"op": "update", "where": "id = 3", "values": {"vector": [0.95, 0.35, 0.05, 0.0], "label": "gamma-moved"}, "row_count": 4, "result_type": "UpdateResult"},
    {"op": "delete", "where": "id = 4", "row_count": 3, "result_type": "DeleteResult", "num_deleted_rows": 1}
  ],
  "searches": [
    {"stage": "after_seed", "limit": 3, "ids": [1, 2, 3], "distances": [0.18, 1.18, 1.78], "expected_ids": [1, 2, 3], "passed": true},
    {"stage": "after_add", "limit": 4, "ids": [1, 2, 3, 4], "distances": [0.18, 1.18, 1.78, 1.98], "expected_ids": [1, 2, 3, 4], "passed": true},
    {"stage": "after_update", "limit": 4, "ids": [3, 1, 2, 4], "distances": [0.0075, 0.18, 1.18, 1.98], "expected_ids": [3, 1, 2, 4], "passed": true},
    {"stage": "after_delete", "limit": 4, "ids": [3, 1, 2], "distances": [0.0075, 0.18, 1.18], "expected_ids": [3, 1, 2], "passed": true}
  ],
  "reopen_checks": [
    {"stage": "after_seed", "process": "separate_python_process", "exit_code": 0, "expected_ids": [1, 2, 3], "expected_row_count": 3, "actual_ids": [1, 2, 3], "actual_row_count": 3, "passed": true, "child_status": "pass"},
    {"stage": "after_crud", "process": "separate_python_process", "exit_code": 0, "expected_ids": [3, 1, 2], "expected_row_count": 3, "actual_ids": [3, 1, 2], "actual_row_count": 3, "passed": true, "child_status": "pass"}
  ],
  "row_ids": [1, 2, 3],
  "labels_after_update": {"1": "alpha", "2": "beta", "3": "gamma-moved", "4": "delta"},
  "stored_labels": {"1": "alpha", "2": "beta", "3": "gamma-moved"},
  "row_count": 3,
  "temp_directory_removed": true,
  "residual_files": [],
  "failure": null
}
```

Failure-path evidence: pointing `--database` at `<temp>\\p002b_blocked.txt\\probe.lance`
(a file used as a directory) returned exit code 1, `"status": "fail"`,
`failure.stage = "connect"`, `exception_type = "FileExistsError"`, and the Windows message
`[WinError 183]`, with `lancedb_version 0.38.0` still recorded.

## Residual files

- probe-managed temp directory removed: `temp_directory_removed: true`, `residual_files: []`;
- `%TEMP%\photo_search_lancedb_probe_*` directories: 0;
- no `*.lance` directory anywhere under the repository;
- the integration test uses pytest `tmp_path`, and `--database` refuses to overwrite an
  existing path, so the probe cannot silently clobber a real store.

## Fix rounds

1. The first probe run reported `"status": "fail"` at `scan_after_delete` because the probe
   compared an id-sorted table scan with the distance-ordered search result. The probe
   reported the mismatch honestly instead of passing; the comparison was corrected to
   `sorted(...)`.
2. The connect path raised a raw `OSError` and was recorded under `stage: "unexpected"`.
   `lancedb.connect`, `create_table`, `open_table`, and the search call are now wrapped so
   failures name their real stage.
3. Ruff reported 18 `E501` findings in the new probe and the file was reformatted with
   `uv run ruff format`. The integration test then exposed that `stored_labels` was captured
   before the delete; the probe now records `labels_after_update` and post-delete
   `stored_labels` separately, and the test asserts both.

All three rounds were fixed inside this task from concrete evidence; LanceDB itself worked on
Windows CPython 3.12.10 on the first attempt, so no C1 (GPT-5.6 Terra / medium) handoff and no
C2 protocol review were needed.

## Not verified here

- Candidate filtering and unindexed-new-item retrieval (P0-02C).
- ANN index creation, tuning, recall, or performance; the probe uses a 3-4 row brute-force
  table with no index and makes no timing claim.
- Filter pushdown, `merge_insert`, cosine/dot metrics, FTS integration, schema evolution.
- Concurrent writers, multiple processes writing the same directory, or long-running
  connections; only sequential single-process writes with read-only child reopens.
- LanceDB version compatibility: only `0.38.0` on Windows CPython 3.12.10 with
  `pyarrow 25.0.1`.
- Real image vectors, embedding models, catalog schema, migrations, or HTTP search; the probe
  vectors are hand-written and exist only for ranking evidence.
- Disk-space exhaustion, backup/restore, and upgrade/rollback behaviour.

## Usage and cost

`uv add lancedb` downloaded `lancedb 99.3 MiB`, `pyarrow 26.7 MiB`, and `numpy 12.0 MiB`
from PyPI plus the smaller packages above; the wheels are cached by `uv`. No DeepSeek API
request was made, so there is no DeepSeek token, cache, or cost record; no GPT API key was
used either.

## Handoff record (strategy §8.4)

```text
任务 ID / 状态：P0-02B / PASS（3 个任务内修复轮次，均为自身缺陷，非 LanceDB 环境失败）
实际模型 / reasoning / 速度：未在指定客户端执行（DeepSeek V4.1 Flash 不可选）；由当前 Codex 代理执行，模型 ID 未暴露，未做切换声明
基线提交号：e7429bb（任务卡原写 0baec27，差异仅为用户新增的通用接手指令提交）
已完成行为：真实落盘 LanceDB 目录 + 显式 schema（fixed_size_list[4]），写入 3 行并执行真实最近邻检索；独立 Python 进程重开同目录复核；新增/更新/删除各一次，每次后真实检索与扫描断言；失败路径非零退出且不谎报 PASS
修改文件与接口/迁移变化：pyproject.toml、uv.lock（仅新增 lancedb 及必需传递依赖，锁文件 24 → 34 包）、tools/benchmarks/lancedb_probe.py、tests/integration/test_lancedb_probe.py、docs/reports/P0-02B-lancedb.md、docs/development-status.md；无接口、无迁移、无产品代码变化
实际命令和结果：uv run python --version(0) / uv lock(0, 34 包) / uv sync --all-groups(0) / uv run python tools/benchmarks/lancedb_probe.py(0, status=pass) / uv run pytest tests/integration/test_lancedb_probe.py(0, 3 passed) / uv run ruff check .(0) / uv run pytest tests/unit tests/integration(0, 7 passed) / git diff --check(0)
报告/详细日志路径：docs/reports/P0-02B-lancedb.md
未验证项和具体阻塞：候选过滤与未维护新增检索（P0-02C）、ANN 索引与性能、并发写入、余弦/点积度量、合并与 schema 演进
失败修复轮次：3 轮（扫描顺序断言、失败阶段包装、Ruff E501 + stored_labels 快照时机）
下一任务及必须读取的文件：P0-02C（LanceDB 候选过滤与未维护新增可检索探针），任务卡 docs/tasks/P0-02C.md；需读 AGENTS.md、docs/development-status.md、主架构 §7、§9.3、§12.4、§14、§15 与开发策略 §2.1-2.3、§6.1、§8.2、本报告与 P0-02A 报告
可见 GPT 额度观察：未测量
DS 全任务请求用量/缓存/费用：无（未发起 DeepSeek 请求）
独立审查者与结果：本任务不需要独立审查
```

## Next task

**P0-02C — LanceDB candidate filtering and unindexed-new-item retrieval probe**
(`docs/tasks/P0-02C.md`). Planned model per strategy §6.1: DS-L / DeepSeek V4.1 Flash /
thinking `low`. The current Codex client cannot select that model, so the card must be
executed in the DeepSeek client, or the deviation recorded the same way this report does.
