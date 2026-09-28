# P4-05 — Sol F1R2-1 恢复一致性披露修复

日期：2026-09-28
状态：`ENGINEERING REVIEW PASS / SOL F1R2-1 FIX IMPLEMENTED / SOL SHORT REREVIEW PENDING`。本修复不授予任务 PASS，且不得生成下一卡。

## 最小修复

`restore_backup()` 的成功结果新增 `consistency`：

- `status=operator_declared` 时，`declaration` 原样携带 manifest 的一致性声明；`verification_scope` 明确它仅记录操作员的停机确认及受控 HTTP service lease，**不证明所有可能写者均已停止**。
- 旧包缺少或无法识别 `consistency` 时，返回 `status=unknown`、`declaration=null` 与“writer quiescence is unknown”说明；绝不默认补成已确认。
- CLI `backup restore --json` 直接输出该返回值，因此同样披露此信息。

未改 schema、迁移、lease 机制、checkpoint、成员集合、恢复发布逻辑、依赖或 API 搜索信封。

## 定向证据

- 新包直接恢复：保留操作员声明并披露受控边界。
- 缺少声明的旧包：明确 `unknown`，不被表述为已确认。
- CLI restore：exit 0、stderr 为空、JSON 同样带 `operator_declared` 和原始声明。
- JUnit `test-results/p4-05-sol-f1r2-disclosure-focus.xml`：**17 passed / 0 failed / 0 errors**。
- `ruff check .`、`uv lock --check`、`git diff --check`：均通过。

## 后续门禁

Sol 审查指定本轮只做返回值/CLI 出口的最小修复及直接定向验证；此前 DS-H F1-R2 完整受影响面结论仍有效。下一步仅需 Sol high 对 SOL-F1R2-1 的聚焦短复审；此前 P4-05 不得 PASS、不得生成下一卡。
