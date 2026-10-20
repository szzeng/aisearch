# P4-LIBRARY-RETENTION-C2-FIX 执行报告

日期：2026-10-20
状态：**IMPLEMENTED / ACCEPTANCE PARTIAL**

## 结果

本次修复失效照片一键清理的点击范围、跨批推进、保护分类、向量删除和授权恢复语义。

- 在一次创建 run 的 SQLite 事务中固化点击时符合预筛选的 asset ID 集合；worker 按 `(run_id, asset_id)` 覆盖索引和最多 20 项批次读取。运行前与最终清理事务继续基于最新事实复核；每批游标、统计和已结算快照行在同一事务中推进。点击后状态改变、主行消失或新增资格均不会静默改变本轮范围。
- 0042 migration 为 schema 41 非终结 run 回填已有 item 身份，重置聚合计数，恢复后由批次结算重建统计；保留 migration 失败回滚及备份升级验证。
- 人工保护只针对人工归属与真实用户记录；自动人脸/派生引用可随安全资产回收。复核发现新增人工保护或副本恢复时撤销 barrier 并跳过。
- 目标 DELETE_VECTOR 继续通过 `IndexCoordinator` 协议，只处理授权资产的删除事件；每资产最多 5 次 drain、累计最多 100 个事件。未确认时记录屏障、artifact、generation、事件状态和最近错误码等具体阻塞信息，并有限重试。
- 手动与自动任务冲突返回 `RUN_IN_PROGRESS`；同模式任务可以恢复。UI 文案改为“一键清除失效照片”，显示跨批检查/完成/跳过/失败及下一批状态。

## 验证

定向命令：

```powershell
$env:UV_CACHE_DIR='.uv-cache'
uv run pytest -q tests/integration/test_p4_library_retention.py tests/unit/test_catalog_migrations.py tests/integration/test_p2_index_submission.py::test_retention_delete_vector_replays_on_real_lance_and_never_resurrects --basetemp=.pytest-tmp/P4-LIBRARY-RETENTION-C2-FIX-r10 --junitxml=test-results/P4-LIBRARY-RETENTION-C2-FIX-targeted-r10.xml
```

最终执行结果：**51 passed, 0 failures, 0 errors, 0 skipped**，耗时 20.974 秒。覆盖 150 个候选且前 50 个人工保护、65 个跨批候选、点击后资格变化、旧 schema 升级/失败回滚、目标 DELETE_VECTOR 在真实隔离小型 Lance 上的确认与重放。

以下检查通过：Ruff（所有本任务触及 Python 文件）、Python `py_compile`、`node --check src/photo_search/web/app.js`、`git diff --check`。

内置浏览器访问本机隔离合成图库，确认失效照片面板及 65 项资格统计可见。点击清理按钮后触发浏览器原生 `window.confirm`；控制工具无法继续操作原生确认对话框，故按钮确认后的跨批展示、刷新及离开后返回仍 **PENDING**。这不是产品成功证据，也不据此把 T10 标为通过。

## 独立复核

用户指定的 Luna H 完成两轮只读复核。首轮指出点击时合格资产可能在 keyset 查询中被新状态静默排除；现通过持久化点击时身份快照修复。第二轮确认快照、实时复核、结算和迁移恢复路径无安全 blocker，并指出单资产事件预算上限应为总量；最终代码已将 100 个事件作为 5 次 drain 的累计上限。复核未运行测试；验证由上述 JUnit 工件提供。

## 边界

未访问、修改或删除真实照片库及 `PhotoSearchData`；未运行全仓测试、GPU 模型、30 万图库容量认证或发布流程。逻辑清理不会触碰原始照片；应用停止后的缓存及 Artifact 物理回收仍按现有维护流程执行。浏览器确认后的 T10 路线未验证，整卡保持 **IMPLEMENTED / ACCEPTANCE PARTIAL**。
