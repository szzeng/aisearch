# P4-SCAN-RUNNER-REGISTRATION-DESIGN

状态：**PROPOSED / NOT ACCEPTED / DESIGN REVIEW PENDING**（2026-11-27）。来源：[P4-FOREGROUND-API-WRITE-ISOLATION-A](P4-FOREGROUND-API-WRITE-ISOLATION-A.md)；静态盘点及设计详见[报告](../reports/P4-FOREGROUND-API-WRITE-ISOLATION-A.md#2026-11-27-设计复核范围候选方案与待验证边界)。本卡是待用户和独立审查确认的较小提案，不授权自动实施，也不替代父卡。

## 问题和范围

验证 ScanRunner 是否能在自己的线程串行登记、提交并执行扫描，使 SQLite writer 等待不阻塞 API，且不修改 `ApiContext.close()`、通用 lease 或其他 Worker 的生命周期。按父报告核验同源请求、HTTP 取消、正在登记/等待/执行时关闭、持久任务的明确恢复路径及 lease 所有权；确认非 daemon 的影响。可审查 Runner 拥有独立 CatalogWriteWorker 的组合资源方案，但该扩展只应在满足所有权/失败/重试后保留；若复杂或不能证明安全，停止并提出更小替代方案。不要顺带迁移其他路由、实施 B2 或修改事务协议。

## 性能和验收

- 链路与风险：扫描登记可能因写锁阻塞 API；单线程登记可能等待现有扫描完成，需明确队列和用户体验边界。
- 做法与边界：使用现有 Runner 自有 service；有界预留、数据库事务不变；不做容量承诺。
- 验证与停止：合成 ASGI、受控持锁、并发同源、断连、关闭、提交前后故障、重启后 QUEUED/RETRY_WAIT/有效及过期 RUNNING lease、线程与数据根 lease；DS-H 全影响面独立审查。无法避免修改通用关闭/lease 或现有事务协议时停止。

当前仅提出设计和验证问题，未实现、未复现、未测试；现有源码只支持后续请求恢复的证据，不假定所有持久任务自动重启执行。

## 独立 Sol 复核

**REVIEW CHANGES REQUIRED**。扫描和登记共用同一线程不能满足现有 202 与同源复用；source ID 去重、关闭时 retention 回调、崩溃恢复和 lease 安全均有待解决的静态时序问题。后续设计应考虑有界独立登记通道与明确持久身份，不得视为已授权或已验证。详细 findings 见[父报告](../reports/P4-FOREGROUND-API-WRITE-ISOLATION-A.md#独立-sol-复核2026-11-27)。
