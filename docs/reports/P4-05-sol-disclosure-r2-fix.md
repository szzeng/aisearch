# P4-05 — Sol 披露短审 R2 修复

日期：2026-09-28
状态：`ENGINEERING REVIEW PASS / SOL-F1R2-1 R2 FIX IMPLEMENTED / SOL SHORT REREVIEW PENDING`。本轮仍不得标 P4-05 PASS 或生成下一卡。

## 修复的两条反例

`_restored_consistency()` 不再把任意字典视为有效停机声明。只有同时满足 `mode="stopped"` 与 `operator_confirmed is true` 的记录才输出 `operator_declared`；空对象或未知字典形状输出 `unknown`，并保留字典原文供审计。

输出说明完全取决于 manifest 实际记录：

- 记录 `managed_service_lease="acquired"` 时才说明受控 HTTP service lease。
- 合法旧式声明仅说明操作员停机确认，并明确“no managed HTTP service lease was recorded”。
- 两种有效声明均明确不证明所有可能写者均已停止。

未修改 lease、checkpoint、恢复发布、schema、迁移、成员集合、依赖或搜索/API 协议。

## 直接验证

- 空 `consistency={}` 的恢复 API 输出为 `unknown`，不表述已确认。
- 旧式 `{mode: stopped, operator_confirmed: true}` 经 restore CLI JSON 输出时不声称未记录的 HTTP lease。
- JUnit `test-results/p4-05-sol-disclosure-r2-focus.xml`：**19 passed / 0 failed / 0 errors**。
- `ruff check .`、`uv lock --check`、`git diff --check`：均通过。

## 后续门禁

Sol 短审指定只需对 SOL-F1R2-1 的声明识别和说明边界复核；此前 DS-H 结论维持。本修复后仅需 Sol high 聚焦短复审，不扩大到维护 CLI、checkpoint、remap、升级回退、打包或真实图库。任务此前不得 PASS、不得生成下一卡。
