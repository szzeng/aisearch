# P4-LIBRARY-RETENTION-C2-FINAL-FIX 执行报告

**结果**：PASS（本卡限定修复与隔离合成验收范围）
**基线**：`5dcb43f`；执行前确认本地 `master` 与 `origin/master` 一致。
**提交**：未提交、未推送。

## 实现

- 批次准备阶段将 `attempt_count >= 3` 且仍为 QUEUED/RUNNING 的异常项目归一化为 FAILED；保留既有错误原因，无原因时写入明确的尝试耗尽/执行中断原因。未耗尽次数的 RUNNING 恢复流程保持可重试，批次结算不会重复执行资产删除或累计统计。
- Runner 在单一线程中串行接续待执行 run；旧任务正在退出时新 run 持久排队并由同一 worker 接手。runner 已关闭时 API 明确返回 503 和 run 状态，避免把排队状态伪报成已启动。
- 保留 ACTIVE barrier、DELETE_VECTOR、tombstone 及手动/自动互斥与授权路径。

## 验证

- 受影响集成测试：**42 passed, 0 failed, 0 errors, 0 skipped**。JUnit：`test-results/P4-LIBRARY-RETENTION-C2-FINAL-FIX-focused-r6.xml`。
- Ruff、Python `py_compile`、Node `--check src/photo_search/web/app.js`、`git diff --check`：通过。
- 覆盖 T1–T6：第三次 RUNNING 崩溃恢复、同批次后续资产继续处理、达到上限后不重复执行/计数、runner 退出竞态接续、并发启动单 worker，以及 PARTIAL 重试和 ACTIVE barrier 授权回归。
- Luna H 独立复核完整改动面与新 API 错误断言，无 blocker。自动启用和 context 启动恢复中 `start=False` 的 warning 分支未单独断言；已核对调用实现，任务接续状态仍持久化。

## 浏览器验收

使用项目内隔离合成 `PhotoSearchData`，真实应用通过内置浏览器和原生确认框完成操作：

1. 打开「图库管理 → 失效照片」；取消一键清除确认后，数据库 run 数为 0。
2. 再次点击并确认，跨多个批次结束为 PARTIAL：run `23133cc64ff8425faf263ca29879b133`，checked 65、completed 64、failed 1、skipped 0。
3. 刷新页面、切换至照片页再返回，仍显示该 run 的最终状态与统计。
4. 关闭合成故障注入后再次点击确认，创建新 run `6d77855ce25e4b859c19b3122ecea82c` 并 COMPLETE：checked 1、completed 1、failed 0、skipped 0。
5. 最终只读数据库核对：active barriers 为 0，剩余合成失效资产为 0。

截图未能通过当前浏览器工具导出。未访问真实 PhotoSearchData。

## 状态边界

本卡验收通过。父任务 `P4-LIBRARY-RETENTION-C2` 仍为 **IMPLEMENTED / ACCEPTANCE PARTIAL**，因为 C2-E 媒体 unlink 与 SQLite 记账间的崩溃窗口可能保守低估累计字节；该问题不属于本卡范围。没有运行 GPU、真实图库、全仓测试或 30 万照片性能测试。
