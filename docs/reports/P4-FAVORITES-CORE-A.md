# P4-FAVORITES-CORE-A 执行报告

日期：2026-10-19
状态：PASS（组合证据；完整 unit/integration 执行记录见下）

## 交付

- 新增 schema 0034 `asset_favorites`，收藏绑定完整 `asset_id`，设置独立 `favorites_revision`；索引按最近收藏与 ID 稳定排序。列表与批量状态 API 使用显式模型和有界读取，游标校验收藏 revision，过期返回 409。单张 PUT/DELETE 幂等，写入和 revision 同一事务。
- 共享照片查看器新增可访问收藏按钮与权威状态恢复；首页通过 `/?view=favorites` 复用照片网格和分页。加入/取消收藏会刷新列表并保留范围及滚动；收藏页隐藏普通日期和时间轴控件。五个照片入口可到收藏页。
- Retention 将收藏列作人工保护引用。备份/恢复、迁移升级和失败回滚覆盖新增状态。schema 34 是仅添加独立状态的 migration；兼容的地点数据包桥接上限扩至 34，显式 v32 诊断克隆工具同步升级到当前 schema。

## 验证

- 收藏专项 API、迁移、回滚、备份恢复、retention、索引计划与 viewer 单测：19 passed，JUnit：`test-results/P4-FAVORITES-CORE-A-focused-final-20261019-092306-008.xml`。
- 完整 `tests/unit tests/integration`：3204 tests，3151 passed，51 skipped，2 failed，pytest exit 1；原始 JUnit：`test-results/P4-FAVORITES-CORE-A-full-final.xml`。两条失败是人物相册深链 UI 的异步 Node harness 检查；不改代码单独复跑两种参数均通过（2 passed），JUnit：`test-results/P4-FAVORITES-CORE-A-person-route-rerun.xml`。此前完整回归中暴露的 schema 33 固定假设均已修正并纳入本轮完整运行。按组合证据记录，不称为一次全量 suite PASS。
- 受影响旧迁移/收藏专项模块复跑通过；最后修复检查：Ruff、`node --check`（app.js、photo-info.js）、`git diff --check` 均通过。
- 内置浏览器使用项目 `.tmp/p4-favorites-browser/` 的隔离合成照片，通过一次性登录会话验证：收藏网格显示、打开查看器、进入完整详情并返回、取消收藏后空态及关闭返回收藏范围。浏览器截图导出不受支持，未保存截图；此项记录实际交互，不作为性能证据。
- Luna High 独立只读复核（含兼容修复二次复核）：无阻塞 finding。

## 边界

未访问真实照片库；未做收藏 30 万规模压测、GPU、模型或发行资格验证。SQL 计划与有界查询验证不构成延迟或规模保证。`P4-FAVORITES-UX-B` 未启动。
