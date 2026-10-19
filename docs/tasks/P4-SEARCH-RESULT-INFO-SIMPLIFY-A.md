# P4-SEARCH-RESULT-INFO-SIMPLIFY-A — 搜索结果信息精简

**日期**：2026-10-19
**状态**：PASS（限定的搜索结果展示与交互单测）
**类型/优先级**：A 级前端 UI 小修 / 低
**基线**：`docs/development-status.md`；需求与验收范围来自本任务用户卡。
**实现面**：`src/photo_search/web/search.js`、`tests/unit/test_search_text_mode_ui.py`。
**报告**：`docs/reports/P4-SEARCH-RESULT-INFO-SIMPLIFY-A.md`。

## 目标与边界

删除所有搜索模式结果卡片中的“语义就绪”和通道名次 DOM 展示；保留总体排名、照片预览、收藏、命中原因、匹配位置、拍摄时间、详情、分页和必要状态提示。保留 API 结果字段及其原映射，不改变请求、排序、搜索过滤、会话或分页协议。不改搜索结果之外的语义索引状态/故障提示。

## 性能设计与验收

- 链路与风险：搜索结果卡片首屏/续页渲染；仅移除两个展示节点及不再使用的通道标签映射，不新增计算、网络或资源工作。
- 做法与边界：保留既有 hit 对象传递和其余节点，仅精简 `buildHit()` 展示；沿用现有页面/分页工作边界。
- 验证与停止：运行目标前端单测、共享收藏 UI 单测及 Node 语法检查；无性能路径改变，不做规模测试或新增性能承诺，验证通过即停止。

## 验证计划

- 普通语义结果：断言显示“第 33 名”和匹配位置，不显示语义就绪/通道名次。
- 文字搜索和纯筛选：通过现有 `test_search_text_mode_ui.py` 覆盖文字结果、匹配位置、分页追加、预览与详情返回；两种模式均不显示技术文字。
- 收藏交互：通过共享收藏 UI 单测；搜索结果收藏挂载代码未改。
- 工程检查：`node --check src/photo_search/web/search.js`；运行 `tests/unit/test_search_text_mode_ui.py`、`tests/unit/test_api_frontend.py`、`tests/unit/test_p4_favorites_ux_b_ui.py`，使用 JUnit XML。
- 浏览器/真实图库/GPU/模型/大规模性能/全量回归：按用户卡不要求。

## 结果与交付

移除了卡片内语义就绪和通道名次 DOM 渲染及 `CHANNEL_TEXT` 常量；总体排名与其他展示保留。结果数据映射保持原样。搜索 smoke 清单同步更新该展示预期。定向验证结果见执行报告。单独本地 Git commit；不推送。
