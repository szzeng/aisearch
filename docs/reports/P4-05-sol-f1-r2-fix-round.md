# P4-05 — Sol F1 第二修复轮：受控停机边界

日期：2026-09-28
状态：`F1 R2 FIX IMPLEMENTED / DS-H FULL AFFECTED-SURFACE REREVIEW PENDING / SOL REREVIEW PENDING`。本轮不授予 P4-05 PASS，也不得生成下一卡。

## F1 根因

此前 `--stopped` 与 `stopped=True` 只是调用方自述：正在运行的受控 HTTP 服务没有持有可供备份检查的跨进程租约，备份仍可能开始文件复制。manifest 也没有记录架构 §11.4 所要求的 generation/pending 停机边界。

## 最小修复

- 新增 `runtime/data_root_lease.py`：以操作系统释放的非阻塞文件锁表示一个数据根的受控进程所有权；遗留 lease 文件本身不代表进程仍运行。
- HTTP 应用 lifespan 在启动时持有该 lease，关闭时先关闭 API 的 catalog/Lance 资源、再释放 lease。
- `backup create` 在 `--stopped` 明示之外必须获得同一 lease；被持有时结构化拒绝 `BACKUP_SERVICE_ACTIVE`，不创建备份。
- 获得 lease 后执行 `PRAGMA wal_checkpoint(TRUNCATE)`；若 SQLite 仍报告 busy，结构化拒绝 `BACKUP_SERVICE_ACTIVE`。清单记录 checkpoint、generation、未确认 index outbox 与文字投影队列计数。
- lease 文件及 SQLite `-wal`/`-shm` 瞬态文件不纳入恢复集；checkpoint 后仅复制静止的 catalog 主文件，避免 Windows 复制锁定 lease 或恢复过期 sidecar。

受控服务以外的未接入 lease 的任意外部写者仍不被承诺支持；这是本卡“受控维护入口/停机备份”的明确边界，不是在线备份能力。

## 新增反例与验证

- 直接持有 data-root lease 时 create → `BACKUP_SERVICE_ACTIVE`，目标不存在。
- 在实际 HTTP lifespan 运行期间 create → `BACKUP_SERVICE_ACTIVE`，目标不存在。
- 清单断言 checkpoint + generation/pending snapshot；现有恢复、schema、损坏 catalog、故障注入与搜索等价性断言保持通过。
- 定向 JUnit `test-results/p4-05-sol-f1-r2-focus.xml`：**14 passed / 0 failed / 0 errors**（Starlette/httpx 既有弃用警告，不影响结果）。
- B 级 JUnit `test-results/p4-05-sol-f1-r2-full.xml`：**1801 passed / 0 failed / 0 errors / 48 skipped**，246.770 s。
- `ruff check .`、`uv lock --check`、`git diff --check`：均通过。

## 必经后续

本轮新增应用生命周期、跨进程租约和 SQLite checkpoint 语义，属于恢复/并发状态面的产品改动；按 V3.1 §2.4 和任务卡，必须重新进行 **DS-H 完整受影响面工程复审**，再由 Sol high 仅复审 F1 修复边界。此前不得 PASS、不得生成下一卡。
