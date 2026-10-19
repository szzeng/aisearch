# P4-EMBEDDING-MANAGEMENT-UI-FIX

**结果：IMPLEMENTED / ACCEPTANCE PARTIAL。** 基线 `c6f4e81` 已核对；已按用户要求本地提交、未推送，也未访问或修改 `D:\SZ\project\PhotoSearchData`。

## 性能范围

- **链路与风险**：孤儿 Lance 隔离/恢复涉及 catalog 引用与磁盘身份；完整模型覆盖检查可能扫描图库并核验 artifact/Lance；建库状态轮询不应重复触发全代统计。
- **做法与边界**：复用同一隔离预检；完整检查放到线程并由该线程创建/关闭只读 SQLite 连接；高频进度只读已持久化批次状态。仅用隔离合成数据，不做 30 万图库或 GPU 基准。
- **验证与停止**：相关恢复/HTTP/资源状态/轮询定向 pytest、Ruff、Node 语法和 diff 检查；原表、备份、generation 或 catalog 身份有歧义时停止并保留文件。

## 结果

- **FIXED — FIX-1 孤儿表恢复安全**：正常隔离与恢复共用 data-root/catalog 身份、provider→generation、表名、SQLite 引用及 Lance 物理身份预检。操作日志绑定授权身份与备份哈希；恢复仅在操作阶段、移动后身份和备份一致时确认完成。源表、备份、隔离目标各级路径用 `lstat` 检查并拒绝 symlink/NTFS reparse point。合成测试覆盖正常隔离、generation 新增引用、同内容替换表、损坏备份和移动后恢复。
- **FIXED — FIX-2 覆盖检查不阻塞 HTTP**：深度 selection/artifact/Lance 检查移至 `asyncio.to_thread`；工作线程独立创建并关闭只读 SQLite 连接；同 root/provider 的并发检查复用单任务。延迟检查期间 `/health` 在限定超时内成功，重复请求只触发一次深度扫描。
- **ALREADY OK — FIX-3 候选资源生命周期**：既有候选 worker 在 `finally` 关闭候选 encoder 并释放资源门禁，暂停/终态路径及活动 profile 不自动切换保持不变。定向合成检查确认旧 query encoder 关闭、门禁释放后可重建。
- **FIXED — FIX-4 BUSY 与模型故障显示**：能力接口只将 `MODEL_RESOURCE_BUSY` 映射为 `BUSY`，并分别标记 `AVAILABLE`、`MODEL_MISSING`、`DAMAGED`、`UNAVAILABLE`；故障字段仅报告异常类型。搜索页显示建库繁忙/缺失/加载失败提示，并在 BUSY 状态的页面重新获得焦点或可见时刷新能力状态。
- **FIXED — FIX-5 轮询 SQL 负担**：增加轻量任务进度接口，使用已持久化批次确认进度，不聚合 `index_entries`；管理状态页仍仅在初次进入及任务结束后读取完整状态。高频轮询复用单定时器/单进行中请求，离开管理页或页面隐藏时停止。

## 验证与待验

- 受影响定向 pytest：**16 passed, 1 skipped**；JUnit：`test-results/P4-EMBEDDING-MANAGEMENT-UI-FIX-targeted.xml`。
- Ruff、`node --check`（`app.js`、`search.js`）及 `git diff --check` 通过。
- 额外运行的 `test_api_frontend.py` 与 `test_search_text_mode_ui.py` 共 100 项，其中 98 通过、2 失败：搜索快照测试期待无参数函数名，但 HEAD 已有 `restoreSearchSnapshot(expectedSimilarAssetId)`；布局测试期待旧 `.search-coverage-entry` 选择器，但 HEAD CSS 已用限定选择器。两处均未由本次改动引入。
- Luna High 独立复核：发现并促成修复悬空 symlink 被当作源表缺失，以及异常 `.code` 字段外露；复核修复后确认关闭，无其他可操作发现。
- 隔离内置浏览器：通过一次性会话进入合成空图库；模型管理页显示两个 provider 未安装且覆盖待检查，搜索页显示未选择模型；覆盖状态接口返回成功。没有可用模型，因此未触发建库/暂停/失败或 BUSY 页面状态。
- **PENDING**：原生 dangling symlink 用例因 Windows `WinError 1314` 无创建权限而跳过；NTFS junction 未测。真实 Qwen/CUDA 建库、暂停、完成/失败、模型切换及实际 GPU 争用未执行，不能视为通过。未访问真实图库。

## 修改文件

- `src/photo_search/models/embedding/offline_transition.py`
- `src/photo_search/api/routes.py`
- `src/photo_search/api/context.py`
- `src/photo_search/models/embedding/candidate_task.py`
- `src/photo_search/web/app.js`
- `src/photo_search/web/search.js`
- `tests/unit/test_p4_embedding_management_fix.py`

保留工作树中原有的 `src/photo_search/api/models.py` 修改，未纳入本任务。
