# P4-LIBRARY-RETENTION-C 执行报告

**状态：SCOPED PASS / USER-DIRECTED LUNA-H REVIEW ROUTE EXCEPTION**

**日期：2026-10-11**
**基线：** `master @ cc321f71e8615cc5e611d30c13fa5aa66a9b2e8b`；开工时工作树已有 P4-LIBRARY-OPS-A / SYNC-B 的未提交改动。本轮保留这些改动，仅在 C1 任务卡范围内增量实现；未暂存或提交。

## 结果

- 新增登录保护的 `GET /api/v1/storage/library-retention` 只读入口。返回数据库计数、最多 50 条按 `asset_id` keyset 分页的失效/零位置摘要及空间估算；不返回文件绝对路径、不枚举原图库、不读向量、不修改业务表。
- 计数按唯一资产与位置分别统计，并明确不可相加。PRESENT 副本优先保护；零位置资产单独分类；来源离线/错误与首次失效时间未知不会被推断为永久删除或达到 30 天。首次失效时间无可靠历史记录，统一展示“观察期起点未知，不具备清理资格”。
- 对人工地点归属/历史、人物关系/合集/隐藏/忽略、搜索反馈、脸部记录与控制、活动 jobs、artifacts、index outbox/generation/index entries、OCR/GPS/重识别等关联保守标记保护。无法识别的新引用 schema 或引用查询失败采取 fail-closed；全局 schema 守卫拒绝未知表时返回 409。未知引用检查优先于零位置分类。
- SQLite 同时报告 catalog 文件字节和逻辑页字节，并将可回收量标为无法准确估计；LanceDB 行数及物理回收量不作不可靠估算。
- 存储维护页新增按需展开的“失效照片与数据占用”面板，明确“此处不会删除原图、索引、机器识别或人工记录”，提供只读分页，不增加删除按钮或与 GC-A/B 勾选联动。前端文本通过 `textContent` 渲染。

## 验证

任务卡定向 JUnit：**8/0/0/0**，文件 `.pytest-tmp/P4-LIBRARY-RETENTION-C-final.xml`。覆盖鉴权、共享 PRESENT 副本、MISSING 与零位置计数、保护引用、离线来源、未知引用 schema、查询错误 fail-closed、只读前后写入计数、50 条分页完整性、实际 `renderLibraryRetention()` 分类/计数/时间说明/下一页状态，以及存储 UI 入口静态契约。

最终检查均通过：

- `uv run pytest -q --junitxml=.pytest-tmp/P4-LIBRARY-RETENTION-C-final.xml tests/integration/test_p4_library_retention.py tests/integration/test_p4_storage_ui.py::test_storage_panel_exposes_separate_read_check_backup_and_maintenance_controls`
- `uv run ruff check src/photo_search/library_retention.py src/photo_search/api/routes.py tests/integration/test_p4_library_retention.py tests/integration/test_p4_storage_ui.py`
- `python -m py_compile src/photo_search/library_retention.py src/photo_search/api/routes.py tests/integration/test_p4_library_retention.py`
- `node --check src/photo_search/web/app.js`
- `git diff --check`

新增渲染用例首次运行遇到 Windows 默认 GBK 解码 Node 中文输出的问题；测试子进程改用 UTF-8 后复跑通过。最终报告不把该环境适配问题记作产品失败。

## 独立复核与边界

- 按用户明确指定的 **LUNA / High** 独立复核本 C1 变化。首轮发现零位置资产可能遮蔽未知引用保护分类，以及缺少实际前端渲染行为测试；两项均已修复并补测。复核者复查修复后 C1 diff，结论 **PASS / finding=none**。这是用户指定的**路由例外，不是 DS-H**。
- C1 未新增迁移或 30 天计时字段；30 天仅为未来可验证的保留资格规则，不是当前自动删除阈值。C2 任何业务数据、机器派生数据或向量的逻辑/物理删除仍未实现，需单独任务卡与重新授权。
- 未访问真实照片库或 `PhotoSearchData`，未做真实浏览器手测、全仓回归、GPU/权重验证或性能基准；本报告仅代表本任务卡 C1 定向验收。
- 建议在隔离图库手测面板折叠/展开、分页及安全摘要；不得以此预览直接删除资产。

下一步只有在用户查看 C1 预览并另行确认清理范围/策略后，才规划 C2 任务卡；本轮不启动 C2。
