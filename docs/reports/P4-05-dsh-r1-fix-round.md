# P4-05 — DS-H R1 结构化错误边界修复记录

日期：2026-09-28
状态：`R1 FIX IMPLEMENTED / DS-H REREVIEW PENDING / SOL REREVIEW PENDING`。本记录不是验收；P4-05 仍不得标记 PASS 或生成下一卡。

## 修复范围

DS-H 失败边界复审的 R1 指出：损坏或非 SQLite 的 `catalog.db` 可在 `inspect_database()` 处抛出原始 `sqlite3.DatabaseError`，绕过产品的 `CatalogError` 出口，导致 CLI traceback 而非结构化 JSON。

在 `photo_search.backup._schema()` 中最小增加 `sqlite3.Error` 捕获，并映射为 `CatalogError(code="BACKUP_INVALID")`；保留已有 `CatalogError` 分支和其他备份/恢复语义不变。`__main__.py` 的无关格式化漂移已还原。

## 新增失败边界证据

- B15：非 SQLite catalog 的 create 返回 `BACKUP_INVALID`。
- B18：自洽但 catalog 损坏的 bundle restore 返回 `BACKUP_INVALID`。
- B19：CLI 对非 SQLite catalog 返回 exit 1、stdout 为结构化 JSON、stderr 为空。

## 验证

- 定向：`tests/unit/test_p4_05_backup_restore.py` + `tests/integration/test_p4_05_backup_restore.py`，JUnit `test-results/p4-05-dsh-r1-fix-focus.xml`：**12 passed**。
- B 级全量，JUnit `test-results/p4-05-dsh-r1-fix-full.xml`：**1799 passed / 0 failed / 0 errors / 48 skipped**，250.822 s。
- `ruff check .`、`uv lock --check`、`git diff --check`：均通过。

## 后续门禁

按任务卡，必须先进行 DS-H 的 R1 有界失败边界复审，再进行 Sol high 的一致性聚焦短复审。两项完成前不得 PASS、不得生成下一任务卡。
