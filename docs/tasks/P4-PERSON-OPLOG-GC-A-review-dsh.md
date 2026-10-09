# P4-PERSON-OPLOG-GC-A — 独立 DS-H 数据安全复核

日期：2026-10-09。状态：**用户改选同会话复核；本 DS-H 卡未执行**。

用户要求“这里复核下就好了”，随后要求修复并再次审核。按此选择完成的记录见[同会话复核报告](../reports/P4-PERSON-OPLOG-GC-A-same-session-review.md)。此记录不得描述为独立 DS-H 结论；以下原 DS-H 检查清单作为未执行的独立审查范围留档。

推荐模型：**DeepSeek V4.1 Flash / high（DS-H）**。模型由用户在 Codex 中手动切换。本卡仅复核 P4-PERSON-OPLOG-GC-A，不实施 Inbox-create 或其他产品任务。

## 复核入口

按顺序读取 `AGENTS.md` → `docs/development-status.md` → [实施任务卡](P4-PERSON-OPLOG-GC-A.md) → [实施报告](../reports/P4-PERSON-OPLOG-GC-A-implementation.md) → 实际 Git 状态和改动。实施开始时 HEAD 为 `4440f74`；期间 `master` 前进到 `d628a87`，新增的 `368d792`/`d628a87` 仅涉及 Inbox-create 文档。以当前仓库文件和实际 diff 为准，不假设文件内容。

复核本任务完整受影响面：

- migration `0027_person_operation_retention.sql`、schema 版本注册；
- `persons/retention.py`、人物操作/脸级/映射重放读取、历史投影、撤销拒绝；
- PersonService、两个新 API 路由、API summary model、`persons.html`/`persons.js`；
- `tests/integration/test_p4_person_oplog_gc.py`、JUnit 产物以及报告列出的实际命令。

按任务卡核对 ADR-0006/0007/0008/0010 的操作身份、幂等、撤销、映射与快照边界。仅读已列条款和直接调用链，不重读整份架构文档或全仓。

## 必审问题

1. `committed_at < now - 30 days` 与最新 200 个 revision 必须同时成立；UNDO 计入最近 200 条；预览和 POST 在事务内一致重算。
2. operation 主记录、request_id/payload、主键、revision、UNDO/alias 引用和人物/脸事实保持不变；一条 operation 的全部 items 同事务删除。
3. person 与 face 的重放 envelope、同 payload replay、异 payload 冲突在删除 items 后仍完整；过期撤销对应原有业务错误码并零写入。
4. 压缩摘要/回执确实保留 UI 历史所需字段且无敏感快照；仅当估算节省为正时清理。检查预览大小口径及 SQLite 物理文件限制是否说清楚。
5. 一批至多 100 条、事务故障整体回滚、重试不重复清理；失败后更早批次可保留。核对跳过类型不会阻塞其他可清理历史。
6. 新路由沿用会话、Origin、CSRF 边界；页面只有明确预览与确认后才写入，并在过期状态隐藏撤销按钮。
7. schema 26 到 27 只加新表，既有 migration 未改；测试报告与 JUnit 真实对应当前 diff。

拆分和 face-redetection mapping 家族以及其关联 UNDO 在实现中安全跳过；确认该边界和报告/UI 文案一致。不要因为任务卡“简单测试”要求扩跑全仓、真实图库、GPU、模型或打包。若现有 JUnit/命令可核验，复用成功证据；只补缺失或矛盾的定向证据。不得接触真实 `PhotoSearchData`、服务或模型。

## 结论与升级

检查完整受影响面并提交本阶段独立报告到 `docs/reports/P4-PERSON-OPLOG-GC-A-review-dsh.md`。明确列出结论、缺陷/最小反例、实际复用或补跑的测试、未验证项及 `SOL_NOT_REQUIRED` 或 Sol high 复核需求。只有出现 V3.1 §2.2 明确条件且证据成立才建议 DeepSeek max；若留下高风险数据语义未决项，交 GPT-5.6 Sol / high 做聚焦终审，禁止在本阶段自行改产品代码。

复核结束前不标记 P4-PERSON-OPLOG-GC-A 总 PASS，不开始 `P4-PERSON-INBOX-CREATE-A`。完成复核后按 `docs/development-status.md` 与 task-launcher 规则交接；后续 Inbox-create 开工前以 schema 27 为当前基线，迁移需继续递增。
