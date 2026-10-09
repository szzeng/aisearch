# P4-PERSON-OPLOG-GC-A 实施记录

日期：2026-10-09。状态：**IMPLEMENTED / 定向验证通过 / 同会话复核通过（用户选择）**。

## 范围与实现

- 在 schema 27 以加性 migration 新增 `person_operation_retention`，保存紧凑历史字段和操作族安全的重放 item 回执；保留 `person_operations` 主记录、请求幂等键、revision、UNDO 双向引用与所有外键。
- 新增只读预览与需会话、Origin、CSRF 保护的手动清理 API。仅当 `committed_at` 严格早于 30 天截止点且不在最新 200 个 operation revision 中，才进入候选；每个写事务最多检查 100 条候选，响应 revision 游标以便后续确认继续越过被跳过的操作。
- 当前只允许普通人物操作与脸级纠错操作清理。拆分、重检测映射及其相关 UNDO 安全跳过并报告原因；无净 JSON 节省或无法生成回执的操作同样跳过。每个成功操作先写回执，再删除其全部快照 item；中途错误回滚整个批次。
- 历史读接口从主记录和紧凑摘要投影已过期条目；现有族的相同 request id 重放使用保存的 item 回执，不同 payload 仍走原冲突处理。过期操作的统一撤销入口在进入操作族撤销前拒绝，使用对应人物/脸级/映射冲突码，零写入。
- 人物历史页加入预览、人工确认、一批执行结果与刷新；过期条目显示“已过期·不可撤销”。清理不调用模型、不重建投影、不运行 VACUUM。

## 验证

- `PYTHONPATH=src .venv/Scripts/python.exe -m pytest -q tests/integration/test_p4_person_oplog_gc.py --junitxml=test-results/P4-PERSON-OPLOG-GC-A-junit.xml --basetemp=.pytest-tmp/P4-PERSON-OPLOG-GC-A`：**5 passed**。覆盖原有边界、跳过、单脸事实/replay/拒绝撤销、v26→v27 migration、故障回滚与 API 保护；复核修复后新增普通人物操作成功清理/replay/冲突/撤销拒绝，以及每批 100 条候选限制和续批游标。
- 修改面 Ruff：通过。
- `node --check src/photo_search/web/persons.js`：通过；页面检查为合成静态控件/API 断言，不是浏览器 DOM 或现场确认。
- `python -m py_compile src/photo_search/persons/retention.py`：通过。
- `git diff --check`：通过。
- 未运行全仓套件；未启动或重启应用服务；未读写真实 `PhotoSearchData`；没有在现有用户 catalog 上执行迁移或清理。

## 限制与交接

- 字节预览比较快照 JSON 与紧凑摘要/回执 JSON，不代表 SQLite 文件会立即缩小；本轮没有 VACUUM。
- 拆分、映射和关联 UNDO 尚不支持清理，仅经候选安全跳过用例覆盖。
- 用户要求在当前会话复核。复核发现并修复批次内扫描不限量、预览缺少原快照字节数/跳过原因、普通人物成功清理路径缺少测试；复核结果见[同会话复核记录](P4-PERSON-OPLOG-GC-A-same-session-review.md)。这不是独立 DS-H 审查，不声称 DeepSeek 审查结论。
- 初始 HEAD 为 `4440f74`。实施期间上游新增文档提交 `368d792`、`d628a87`，只添加 `P4-PERSON-INBOX-CREATE-A` 任务卡/状态索引；当前代码工作树未提交。Inbox-create 依赖本卡 schema 27 迁移先收敛，后续迁移需从实际 schema 27 递增。
- 初始状态中已有 `.tmp/` 及多份 `docs/reports/tmp-retention-*` 未跟踪材料；均保留，未纳入本任务。
