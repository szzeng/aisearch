# P4-LIBRARY-RETENTION-C2-RETRY-FIX 执行报告

日期：2026-10-20
状态：**IMPLEMENTED / ACCEPTANCE PARTIAL**
范围：终结 PARTIAL 生命周期、再次点击创建新 run、ACTIVE barrier 授权复核及失败恢复；不触及真实图库。

## 实现

- 以共享 SQL 未结算谓词判定 PARTIAL 是否仍有候选/待结算 item，并复用于创建互斥、启动恢复、worker 和进度 API。游标已结算的 FAILED 不会被当作活动 run；完成前缀不因旧 `has_more_candidates` 重新扫描。
- 完整结算的 PARTIAL 保留历史统计并允许新点击建立新 run。测试覆盖 25 项候选、最后一项三次失败、首轮 24 成功/1 失败，第二轮只重试仍安全的失败项。
- 每次授权重试都重新检查当前来源、扫描、有效副本、观察期和人工引用。MANUAL barrier 不会授权未满期的 AUTOMATIC 清理。恢复 PRESENT 副本沿用原撤销路径。
- 修复 Luna H 发现的人工保护竞态：旧 ACTIVE barrier 下新收藏或其他人工引用出现时，begin 路径撤销 barrier、删除陈旧 DELETE_VECTOR 并调用现有 IndexCoordinator 调度索引 UPSERT；人工记录保留。finalize 对人工保护撤销并修复，对来源离线/扫描证据不足等阻断保留 barrier，供诊断和后续复核。
- UI 明确区分仍有未结算工作的 PARTIAL 与已终结的 PARTIAL，并显示当前 run 标识，提示可再次点击创建新 run。
- 未改 schema/migration、照片文件或 `PhotoSearchData`。

## 验证

- 定向 pytest：`tests/integration/test_p4_library_retention.py` 与 `tests/integration/test_p4_storage_ui.py`，**40 passed, 0 failed, 0 errors, 0 skipped**。最终 JUnit：`test-results/P4-LIBRARY-RETENTION-C2-RETRY-FIX-final-r4.xml`。
- 第一次并行 pytest 遇到共享 `.pytest-tmp` 创建冲突（WinError 183）；使用独立项目内 basetemp 串行重跑，最终 JUnit 全绿。保留了原始首轮 JUnit 与最终 JUnit。
- Ruff、相关 Python `py_compile`、`node --check src/photo_search/web/app.js`、`git diff --check` 通过。
- 恢复查询 EXPLAIN 断言使用 `idx_library_retention_runs_resume` 及 run/item、run/candidate 主键范围查询，无 `TEMP B-TREE` 排序；不据此宣称大图库容量或 SLA。
- Luna H 首轮独立复核发现人工保护重试 blocker，已按建议补充实现和回归；跟进复核确认 blocker 已关闭。复核建议明确断言旧 DELETE_VECTOR 清除，该断言已加入回归并在最终测试中验证。

## 未完成验收

- 浏览器已用新的一次性会话打开隔离 synthetic 图库，页面显示 schema v42 与失效照片面板 65 张统计，证明已登录 API 正常。点击确认入口时 CUA 报浏览器焦点控制超时；随后检查确认框未发现活动 dialog，其他控件操作无可验证状态变化，因此未继续确认或清理。只读检查隔离 catalog 显示 65 assets、0 retention runs、0 ACTIVE barriers，确认未产生清理任务。浏览器 T10（取消/确认、跨批进度、刷新返回、新 run）仍 PENDING；不将 API/静态测试代替浏览器验收。
- 因真实浏览器取消/确认、刷新返回和再次点击流程未完成，本卡保留 **IMPLEMENTED / ACCEPTANCE PARTIAL**。
- 按用户指令提交本地 commit；未推送。
