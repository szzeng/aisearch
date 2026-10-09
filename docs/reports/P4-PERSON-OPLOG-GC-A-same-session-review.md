# P4-PERSON-OPLOG-GC-A — 同会话复核记录

日期：2026-10-09。结论：**同会话聚焦复核通过；用户选择此复核方式，未执行独立 DS-H 审查**。

## 复核范围

按实施卡和原复核卡检查 schema 27 新表迁移、候选筛选和批次事务、人物与单脸重放/历史/撤销调用链、API 会话与 Origin/CSRF 测试、人物历史页面，以及任务定向测试和报告。复核限合成 catalog 与工程实现；未启动用户服务，未读写真实 `PhotoSearchData`。

## 复核发现及处理

1. **批次扫描范围超过 100 条。** 原实现先在写事务内读取并逐条检查全部候选，即使最终只清理 100 条。现改为每次写事务只读取最多 100 条候选，返回 `next_after_operation_revision` 与 `has_more`；页面将游标传回下一次经人工确认的 POST，跳过的操作不会令后续候选永久堵塞。
2. **预览缺少原快照大小和跳过原因。** 页面现在展示 `snapshot_json_bytes` 与按固定类别归一化的跳过原因；净节省估算和不可撤销提示仍显示。
3. **普通人物成功清理路径没有正向用例。** 新增合成 rename 操作的清理、事实保留、同 payload 回放、异 payload 冲突和过期撤销零写入用例。

复核亦确认迁移只新增伴随表、清理保留 operation 主记录、回执写入与快照删除处于同一写事务、旧人物/单脸操作族的回执读取路径保留、过期撤销在族分发前拒绝；受保护写路由由定向 API 测试覆盖。

## 验证

- `PYTHONPATH=src .venv/Scripts/python.exe -m pytest -q tests/integration/test_p4_person_oplog_gc.py --junitxml=test-results/P4-PERSON-OPLOG-GC-A-junit.xml --basetemp=.pytest-tmp/P4-PERSON-OPLOG-GC-A`：**5 passed**。
- 修改面 Ruff：通过。
- `node --check src/photo_search/web/persons.js`：通过。
- `python -m py_compile src/photo_search/persons/retention.py`：通过。
- `git diff --check`：通过。

## 边界

这是同会话复核，不是独立 DS-H 或 GPT-5.6 Sol 审查；不据此声称模型切换或独立审查。没有运行全仓测试、真实浏览器矩阵、正式 catalog 迁移/清理、服务重启或 PhotoSearchData 操作。预览的 JSON 字节节省不表示 SQLite 文件立即缩小，仍不运行 VACUUM。
