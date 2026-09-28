# P4-05 — Sol 聚焦终审 F1 修复轮

日期：2026-09-28
状态：`FIX IMPLEMENTED / DS-H REREVIEW PENDING / SOL REREVIEW PENDING`。

## 失败边界与最小修复

Sol 聚焦审查沿用 DS-H F1 最小反例：运行中的文件集合复制可以通过逐文件 SHA-256，却不是 SQLite 与 Lance 的同一时间点。此类包以前会静默发布并恢复成功。

`backup create` 现在强制调用方显式确认停机：库 API 必须传 `stopped=True`，CLI 必须传 `--stopped`。未确认时返回结构化 `BACKUP_STOPPED_CONFIRMATION_REQUIRED`，并且不会创建 staging 或备份目录。成功 manifest 新增：

```json
"consistency": {"mode": "stopped", "operator_confirmed": true}
```

这不是在线一致性承诺，也不假称能从 WAL/文件锁可靠推断所有运行者；它把原先未检测、未披露的人工前提变成不可遗漏的显式操作员确认和可审计备份事实。运行中备份仍不受支持。

同时将 DS-H F2 落测：生产扫描、真实 Lance generation 与 FTS 的恢复测试在恢复前后比较搜索页，并逐行比较 `places`、`asset_place_assignments` 与 append-only `catalog_filter_revision_events`。

## 验证

- 定向：`pytest -q tests/unit/test_p4_05_backup_restore.py tests/integration/test_p4_05_backup_restore.py`，JUnit `test-results/p4-05-sol-fix-focus.xml`，**9 passed / 0 failed / 0 errors**。
- B 级全量：JUnit `test-results/p4-05-sol-fix-full.xml`，**1796 tests / 0 failures / 0 errors / 48 skipped，244.226 s**。`ruff check .`、`uv lock --check` 与 `git diff --check` 均 exit 0。
- 本轮未改 schema、迁移、依赖、outbox/claim/revision、API 搜索信封或真实照片库。

待完成：B 级全量回归、独立 DS-H 对 F1/F2 直接回归的短复审，随后 Sol 只复审该修复边界；在两项审查完成前不得标记 P4-05 PASS。
