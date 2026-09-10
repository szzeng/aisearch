# P0-02C — LanceDB candidate filtering and unindexed-new-item retrieval probe

**Status:** PASS, executed 2026-09-10. One in-task fix round (a test assertion about temp-dir
bookkeeping); the probe itself passed on its first run. No LanceDB capability or environment
failure, so no C1 handoff and no C2 protocol review were needed.

## Task record

| Field | Value |
|---|---|
| Task | P0-02C — LanceDB candidate filtering and unindexed-new-item retrieval probe |
| Planned model | DeepSeek V4.1 Flash, official API model `deepseek-flash`, tier DS-L, thinking enabled, reasoning `low`, standard speed |
| Actual execution | **未在指定客户端执行.** DeepSeek V4.1 Flash is not selectable in this Codex session, so the probe was executed locally by the current Codex agent. No DeepSeek API request was made and no model switch is claimed. The executed model id is not exposed by the client and is recorded as unavailable rather than guessed. |
| Git baseline at start | `72ddf15` (`增加P0-02C的任务卡`). The card named `03027bf` (the P0-02B commit); the only extra commit is the card itself. Working tree was clean. |
| Previous task | P0-02B, PASS, `docs/reports/P0-02B-lancedb.md` |

With this task the whole of **P0-02 is complete**: SQLite FTS5 (P0-02A), LanceDB
persistence/restart/CRUD (P0-02B), and LanceDB filtering plus unindexed-new-item visibility
(P0-02C).

## Files changed

- `tools/benchmarks/lancedb_filter_probe.py` (new) — runnable probe.
- `tests/integration/test_lancedb_filter_probe.py` (new) — regression tests.
- `docs/reports/P0-02C-lancedb-filter.md` (new) — this report.
- `docs/development-status.md` (updated).

`pyproject.toml` and `uv.lock` were **not** touched: `git status` after the task lists only the
two new files, and `uv lock` re-resolved the same 34 packages. No product code changed; no
index, filter API, migration, or HTTP endpoint was added.

## Observed environment

| Item | Observed value |
|---|---|
| Interpreter | CPython 3.12.10 (`.venv/Scripts/python.exe`) |
| LanceDB | 0.38.0 |
| pyarrow | 25.0.1 |
| Metric | LanceDB default L2; `_distance` is squared L2 |
| ANN index on the probe table | none (`list_indices()` returned `[]` before and after the new row) |
| `fast_search` | never enabled (`fast_search_enabled: false`) |
| Index/maintenance calls | none (`maintenance_calls: []`) |

## Probe design

Explicit Arrow schema: `id: int64`, `vector: fixed_size_list<float>[4]`, `space_id: int64`,
`source_id: int64`, `visible: bool`. Seven rows span two `space_id` groups (10 and 20), two
`source_id` values (100 and 200), and one hidden row (`id 14`, `visible = false`). The query
vector is `[1.0, 0.0, 0.0, 0.0]`, and every distance in the fixture is distinct so the expected
ranking is unambiguous.

Two modes are provided:

- default — the full filtered-search scenario;
- `--invalid-filter-check` — the failure path, which searches with the filter
  `no_such_column = 1`.

A throwaway scratch script (created and deleted outside the repository, so it left no repo
artifact) first confirmed on this machine that `where()` really pre-filters, that
`list_indices()` is empty for a fresh table, and that an unknown column raises rather than
being ignored. The probe was then written against that verified behaviour.

## Filter evidence

Query vector `[1.0, 0.0, 0.0, 0.0]`; "global rank" is the row's position in the unfiltered
ranking of the same table state.

| Stage | Filter | Limit | Returned ids | Global ranks | Expected |
|---|---|---|---|---|---|
| unfiltered_all | none | 7 | `[11, 12, 1, 14, 2, 13, 3]` | 0,1,2,3,4,5,6 | same |
| filtered_group_a | `space_id = 10` | 3 | `[1, 2, 3]` | 2,4,6 | same |
| filtered_space_and_source | `space_id = 10 AND source_id = 100` | 3 | `[1, 2]` | 2,4 | same |
| filtered_visible_only | `visible = true` | 6 | `[11, 12, 1, 2, 13, 3]` | 0,1,2,4,5,6 | same |
| filtered_group_b | `space_id = 20` | 4 | `[11, 12, 14, 13]` | 0,1,3,5 | same |
| after_add_unfiltered_all | none | 8 | `[11, 21, 12, 1, 14, 2, 13, 3]` | 0..7 | same |
| after_add_filtered_group_a | `space_id = 10` | 4 | `[21, 1, 2, 3]` | 1,3,5,7 | same |
| after_add_filtered_space_and_source | `space_id = 10 AND source_id = 100` | 3 | `[21, 1, 2]` | 1,3,5 | same |

Filter behaviour checks:

- **Hidden rows never leak.** `filtered_visible_only` returns six rows and omits `id 14`
  (`visible = false`), while `filtered_group_b` still returns it when `visible` is not part of
  the filter.
- **The global nearest neighbour cannot be reached through a narrower filter.** The unfiltered
  winner is `id 11` (`space_id = 20`); with `space_id = 10` the winner becomes `id 1` (the
  in-group nearest), and `exclusion_evidence.excluded_id_present` is `false`.
- **The filter is applied before ranking, not after a global top-k.** The `space_id = 10`
  query with `limit = 3` returns `id 3`, whose global rank is 6 — a row a "fetch global top-3,
  then filter" implementation could never produce. `pre_filter_evidence.max_global_rank = 6`
  with `limit = 3`, and the probe fails the run if `max_global_rank < limit`. That guards
  architecture §9.3 item 4 ("不允许仅全库取 Top 100 再过滤后声称结果完整").
- **AND conditions narrow rather than widen.** `space_id = 10 AND source_id = 100` returns two
  rows, not the three of the single-condition query.

## Unindexed-new-item evidence

Row `id 21` (`space_id = 10`, `source_id = 100`, `visible = true`,
vector `[0.95, 0.15, 0.05, 0.0]`, squared L2 distance 0.0275) is appended with `table.add`
after the initial queries. No index was ever created (`index_state` is
`{"after_create": [], "after_add": []}`), no maintenance call was made
(`maintenance_calls: []`), and `fast_search` was never enabled. The new row is immediately
retrievable:

- unfiltered: rank 1 of 8 (`[11, 21, 12, ...]`);
- filtered by `space_id = 10`: top-1, `[21, 1, 2, 3]`;
- filtered by `space_id = 10 AND source_id = 100`: top-1, `[21, 1, 2]`.

The probe fails if any post-add query omits `id 21`, so a future change that starts skipping
unindexed data (for example an enabled `fast_search` path) cannot pass silently. This is the
small-data exact-search baseline of architecture §7.3, not an ANN claim.

## Invalid-filter evidence

`--invalid-filter-check` issues a real search with `where("no_such_column = 1")`:

```text
status: fail, exit code 1
failure.stage: invalid_filter
exception_type: ValueError
message: Invalid input, Schema error: No field named no_such_column.
         Valid fields are `id`, `vector`, `space_id`, `source_id`, `visible`.
invalid_filter.raised: true
invalid_filter.returned_ids: null
```

The probe exits non-zero in both possible outcomes; if LanceDB ever silently ignored the
filter and returned rows, the record would carry `raised: false` with the returned ids and the
failure message "invalid filter was silently ignored". No unfiltered fallback is possible, and
"returned some rows" is never treated as success.

## Commands, exit codes, results

| Command | Exit | Result |
|---|---|---|
| `uv run python --version` | 0 | `Python 3.12.10` |
| `uv lock` | 0 | `Resolved 34 packages` (no lock change) |
| `uv run python tools/benchmarks/lancedb_filter_probe.py` | 0 | `"status": "pass"`, 8 queries passed, `row_count` 8, `temp_directory_removed: true` |
| `uv run pytest tests/integration/test_lancedb_filter_probe.py` | 0 | `3 passed in 3.53s` |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run pytest tests/unit tests/integration` | 0 | `10 passed in 12.06s` |
| `git add -N tools tests` then `git diff --check` | 0 | no whitespace errors |

The invalid-filter mode was also run directly and returned exit code 1 with the record above.

## Probe output (excerpt; values unchanged, layout compacted)

```json
{
  "task": "P0-02C",
  "mode": "full",
  "status": "pass",
  "python_version": "3.12.10",
  "lancedb_version": "0.38.0",
  "pyarrow_version": "25.0.1",
  "database_path": "%TEMP%\\photo_search_lancedb_filter_0uhushmx\\probe.lance",
  "table": "probe_vectors",
  "vector_column": "vector",
  "vector_dim": 4,
  "query_vector": [1.0, 0.0, 0.0, 0.0],
  "filter_semantics": "LanceDB LanceQueryBuilder.where() pre-filter",
  "fast_search_enabled": false,
  "maintenance_calls": [],
  "schema": [
    {"name": "id", "type": "int64", "nullable": false},
    {"name": "vector", "type": "fixed_size_list<item: float>[4]", "nullable": false},
    {"name": "space_id", "type": "int64", "nullable": false},
    {"name": "source_id", "type": "int64", "nullable": false},
    {"name": "visible", "type": "bool", "nullable": false}
  ],
  "index_state": {"after_create": [], "after_add": []},
  "queries": [
    {"stage": "unfiltered_all", "filter": null, "limit": 7, "ids": [11, 12, 1, 14, 2, 13, 3], "distances": [0.0025, 0.0825, 0.1, 0.58, 0.8, 1.81, 2.0], "global_ranks": [0, 1, 2, 3, 4, 5, 6], "passed": true},
    {"stage": "filtered_group_a", "filter": "space_id = 10", "limit": 3, "ids": [1, 2, 3], "distances": [0.1, 0.8, 2.0], "global_ranks": [2, 4, 6], "passed": true},
    {"stage": "filtered_space_and_source", "filter": "space_id = 10 AND source_id = 100", "limit": 3, "ids": [1, 2], "distances": [0.1, 0.8], "global_ranks": [2, 4], "passed": true},
    {"stage": "filtered_visible_only", "filter": "visible = true", "limit": 6, "ids": [11, 12, 1, 2, 13, 3], "distances": [0.0025, 0.0825, 0.1, 0.8, 1.81, 2.0], "global_ranks": [0, 1, 2, 4, 5, 6], "passed": true},
    {"stage": "filtered_group_b", "filter": "space_id = 20", "limit": 4, "ids": [11, 12, 14, 13], "distances": [0.0025, 0.0825, 0.58, 1.81], "global_ranks": [0, 1, 3, 5], "passed": true},
    {"stage": "after_add_unfiltered_all", "filter": null, "limit": 8, "ids": [11, 21, 12, 1, 14, 2, 13, 3], "distances": [0.0025, 0.0275, 0.0825, 0.1, 0.58, 0.8, 1.81, 2.0], "global_ranks": [0, 1, 2, 3, 4, 5, 6, 7], "passed": true},
    {"stage": "after_add_filtered_group_a", "filter": "space_id = 10", "limit": 4, "ids": [21, 1, 2, 3], "distances": [0.0275, 0.1, 0.8, 2.0], "global_ranks": [1, 3, 5, 7], "passed": true},
    {"stage": "after_add_filtered_space_and_source", "filter": "space_id = 10 AND source_id = 100", "limit": 3, "ids": [21, 1, 2], "distances": [0.0275, 0.1, 0.8], "global_ranks": [1, 3, 5], "passed": true}
  ],
  "exclusion_evidence": {"global_nearest_id": 11, "filter": "space_id = 10", "top_id": 1, "returned_ids": [1, 2, 3], "excluded_id_present": false},
  "pre_filter_evidence": {"stage": "filtered_group_a", "filter": "space_id = 10", "limit": 3, "returned_ids": [1, 2, 3], "global_ranks": [2, 4, 6], "max_global_rank": 6},
  "row_count": 8,
  "temp_directory_removed": true,
  "residual_files": [],
  "failure": null
}
```

## Residual files

- probe-managed temp directory removed: `temp_directory_removed: true`, `residual_files: []`;
- `%TEMP%\photo_search_lancedb_filter_*` directories: 0;
- no `*.lance` directory anywhere under the repository;
- the integration tests use pytest `tmp_path`, and `--database` refuses to overwrite an
  existing path.

## Fix rounds

1. The first integration-test run failed on `record["temp_directory_removed"] is True` for the
   case where the caller supplies `--database`. The probe was right (it reports `null` when it
   does not own the directory) and the test assertion was wrong; the assertion now expects
   `None` for a caller-supplied path while the temp-mode test still requires `True`. No probe
   logic changed.

Otherwise the probe, its assertions, and Ruff passed on the first attempt. Two probe design
choices came from the throwaway scratch check described above rather than from failures.

## Not verified here

- ANN index creation, training, `IVF_PQ` parameters, recall, latency, or any performance claim;
  the table has no index and only 8 rows.
- Filter pushdown efficiency, negation and NULL semantics, `OR`/`IN`/`LIKE` combinations, or
  filters on nested/array columns.
- `fast_search` (never enabled), `merge_insert`, projections, cosine/dot metrics, schema
  evolution, and LanceDB version compatibility beyond 0.38.0 on Windows CPython 3.12.10.
- Concurrent writers, maintenance while querying, and disk-space exhaustion (architecture
  §7.3 still requires a later P0/P4 verification of index maintenance under concurrency).
- The SQLite-side authority check and the candidate-ID retrieval path of architecture §9.3
  items 2-3, which belong to P2 once the catalog and projections exist.
- Real image vectors: the probe vectors are hand-written and only exercise ranking and
  filtering.

## Usage and cost

No dependency downloads and no network access in this task (`uv lock` re-resolved from cache).
No DeepSeek API request was made, so there is no DeepSeek token, cache, or cost record; no GPT
API key was used either.

## Handoff record (strategy §8.4)

```text
任务 ID / 状态：P0-02C / PASS（1 个测试断言修复轮次，探针本身首轮通过）
实际模型 / reasoning / 速度：未在指定客户端执行（DeepSeek V4.1 Flash 不可选）；由当前 Codex 代理执行，模型 ID 未暴露，未做切换声明
基线提交号：72ddf15（任务卡原写 03027bf，差异仅为任务卡提交本身）
已完成行为：显式 schema + 7 行两分组夹具上执行真实 where 预过滤检索；隐藏行不漏出；过滤排除全表最近邻后组内最近邻成为第一；limit=3 命中全局第 6 名证明非"全库 Top-K 后过滤"；新增未索引行（无 ANN 索引、无维护调用、未开 fast_search）在过滤与不过滤查询中均立即可检索；非法过滤列非零退出且不返回结果
修改文件与接口/迁移变化：tools/benchmarks/lancedb_filter_probe.py、tests/integration/test_lancedb_filter_probe.py、docs/reports/P0-02C-lancedb-filter.md、docs/development-status.md；pyproject.toml 与 uv.lock 未改动（仍 34 包）；无接口、无迁移、无产品代码变化
实际命令和结果：uv run python --version(0) / uv lock(0, 34 包无变化) / uv run python tools/benchmarks/lancedb_filter_probe.py(0, status=pass, 8 查询全通过) / --invalid-filter-check(1, invalid_filter 未静默退化) / uv run pytest tests/integration/test_lancedb_filter_probe.py(0, 3 passed) / uv run ruff check .(0) / uv run pytest tests/unit tests/integration(0, 10 passed) / git diff --check(0)
报告/详细日志路径：docs/reports/P0-02C-lancedb-filter.md
未验证项和具体阻塞：ANN 索引与 recall/性能、过滤下推效率、否定与 NULL 语义、并发写入与维护窗口、cosine/dot 度量、SQLite 权威校验与候选 ID 路径（属 P2）
失败修复轮次：1 轮（集成测试对调用方提供 --database 时的临时目录字段断言错误）
下一任务及必须读取的文件：P0-03（Chinese-CLIP 图像/文本塔、tokenizer、ONNX 导出与 manifest），任务卡 docs/tasks/P0-03.md，执行模型 C1 / GPT-5.6 Terra / medium；需读 AGENTS.md、docs/development-status.md、主架构 §2.1、§8.1、§11.3、§14、§15 与开发策略 §2.1、§6.1、§7.2、§8.1
可见 GPT 额度观察：未测量
DS 全任务请求用量/缓存/费用：无（未发起 DeepSeek 请求）
独立审查者与结果：本任务不需要独立审查
```

## Next task

**P0-03 — Chinese-CLIP image/text towers, tokenizer, ONNX export, and manifest**
(`docs/tasks/P0-03.md`), planned model **C1 / GPT-5.6 Terra / medium** per strategy §6.1. That
card requires ONNX Runtime and model weights, so it needs the network, disk space, and CPU/GPU
budget — check the card's own authorization and verification requirements before starting.
