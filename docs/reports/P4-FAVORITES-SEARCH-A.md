# P4-FAVORITES-SEARCH-A 执行报告

日期：2026-10-19
状态：**IMPLEMENTED / ACCEPTANCE PARTIAL（浏览器组合筛选闭环仍待补）**。代码、受影响测试与用户指定的 Luna High 独立复核已完成；因本轮内置浏览器未能操作折叠的高级地点筛选，按任务卡要求不标 PASS。

## 实现

- 在 place-aware 搜索请求增加严格布尔 `favorites_only`，以现有 `asset_favorites.asset_id` 在 FTS、向量召回、FILTER_ONLY、地点相册与结果复核前限定候选；冻结的基础 SearchRequest 合同、表结构和 migration 未改。
- 为收藏范围锚定并回显独立 `favorites_revision`；搜索发布、FILTER/地点游标、排名续页和候选时间浏览均拒绝跨版本结果，使用结构化 `FAVORITES_REVISION_STALE`。
- 收藏页增加“搜索收藏”一次性交接；搜索表单增加独立 checkbox/chip，与既有范围条件求交。取消或收藏状态变化时清空旧结果与续页，不自动扩大范围。
- 修复复核发现的 viewer 边界：在收藏限定结果预览器中取消收藏后立即失效；收到 revision 409 时清理会话/游标并保留明确重搜入口。

## 验证

- 受影响 pytest 组合：**160 passed，0 failed，0 errors，0 skipped**；JUnit：[P4-FAVORITES-SEARCH-A-affected-final.xml](../../test-results/P4-FAVORITES-SEARCH-A-affected-final.xml)。覆盖收藏范围空/非空分页、幂等与 revision 失效、FTS/向量 TopK 前过滤、50,000 收藏密度计划、人物交集、搜索续页/时间浏览、人物地点与搜索 UI 既有调用面，以及 viewer 取消收藏和 revision 409 回归。
- Ruff、相关 Python `py_compile`、`node --check`（search.js/app.js/search-scope-entry.js）、`git diff --check` 均通过。初次运行中出现的测试夹具和游标断言问题均已修复；中间 JUnit 留在忽略的 `test-results/`，以最终组合结果为准。
- 性能检查只验证合成 N≈50,001、F=0/100/2,000/50,000 的 SQLite `EXPLAIN QUERY PLAN` 与成员索引方向；无全表资产扫描。未测端到端延迟，不据此声明 30 万容量、模型/GPU 或发布资格。

## 内置浏览器

使用 `127.0.0.1`、隔离合成图库及新生成的一次性本地会话链接；未访问真实 `PhotoSearchData`，服务已停止。

- 收藏页点击“搜索收藏”进入搜索页并勾选限定；空词返回两张收藏结果。输入 `sunset` 后语义模式显示“尚未启用画面搜索模型”，切换“按文字找”命中收藏文件名，未把模型缺失算作功能失败。
- 在收藏限定结果预览器中取消收藏后，更新后的页面显示“收藏已变化，请点击搜索重新搜索；旧收藏结果和分页已失效。”，旧结果清空；单元回归另验证外部写入触发的 409 清理。
- 普通结果预览关闭后搜索状态保持。单测覆盖收藏范围 + 固定人物的 AND 交集。
- **待补项**：本轮内置浏览器自动化未能展开“高级地点条件”折叠区（可见 summary 的普通点击、键盘操作均超时，强制点击未改变展开状态）。因此“全局搜索勾选收藏 + 页面人物/地点控件 + 改词/取消限定不自动重搜”未完成浏览器验收；没有证据表明这是产品失败，也不能用 API/单测替代该浏览器门槛。截图仅能在浏览器工具输出为内存图像，本轮未能导出到忽略目录。

## 独立复核

按用户要求，由 **Luna High 子代理**独立检查本卡受影响面。初审指出 viewer 取消收藏未及时清理结果及 `FAVORITES_REVISION_STALE` 未清游标的 P2；已修复并加入回归测试。Luna High 跟进复核确认修复成立，**无新增 P0–P3 finding**。先前关于 FILTER_ONLY offset COUNT 的疑虑在核对调用方发布前版本复核后撤回。

## 收尾边界

需要补成 PASS 的唯一必需验收项是内置浏览器完成固定人物或地点与收藏条件的组合、改词/取消限定且不自动搜索。完成前本卡维持 **IMPLEMENTED / ACCEPTANCE PARTIAL**。本轮未提交代码、未跑全仓回归、未访问真实图库、未进行 30 万容量或模型/GPU验证。
