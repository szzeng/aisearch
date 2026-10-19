# P4-SEARCH-STRONG-MATCH-EXIF-FIX — EXIF v2 高度匹配统计修复

**日期**：2026-10-20
**状态**：IMPLEMENTED / ACCEPTANCE PARTIAL
**优先级**：P2；最小 Bug Fix。
**来源**：用户本轮任务说明。

## 目标与边界

为 Chinese-CLIP EXIF v2 和 Qwen3-VL-Embedding-2B EXIF v2 增加高度匹配统计资格，分别复用 CLIP 1.3 与 Qwen 1.5 squared-L2 阈值。保留普通版行为、统计字段、Top-K 距离计数、5000 边界、搜索候选/排序/分页以及前端布局。未知身份继续不可用。不触碰真实 PhotoSearchData、模型加载、索引或向量算法。

## 性能设计

- 链路与风险：统计发生在现有 Top-K 距离结果上；新增身份判断不能增加向量查询或逐资产工作。
- 做法与边界：四个精确 provider 前缀映射到既有家族阈值，复用已返回的 vector rows；无新缓存、持久化或全图库工作。
- 验证与停止：覆盖真实 manifest 身份、阈值边界、0 值、5000+、结果顺序、API 摘要和前端文案；不新增规模性能结论。遇到 API/前端运行环境限制则记录未验证项，不扩展检索算法。

## 验证计划

受影响 unit/integration 定向测试、前端统计文案断言、Ruff、Python 编译、Node 语法、`git diff --check`。候选及分页行为通过既有检索注解与会话续页测试确认。无需迁移或全仓回归；不加载真实模型、不访问真实图库。真实浏览器页面交互未作为本次合成测试证据。

## 实施结果

`strong_match_max_distance()` 使用 `_STRONG_MATCH_THRESHOLDS` 的四项映射。每项前缀包含冒号边界；普通版与 EXIF v2 阈值按模型家族共享。`strong_match_policy()` 仍只调用该函数。新增身份测试从两个 EXIF v2 manifest 生成 `space_id`，并覆盖错拼身份和未知身份。详见[执行报告](../reports/P4-SEARCH-STRONG-MATCH-EXIF-FIX.md)。

## 验收状态

后端定向组合 39 passed；语法、Ruff 和 diff 检查通过。前端统计文案、0 值数值格式化和可用性门控静态断言通过。完整相关测试文件还包含既有前端恢复/布局断言失败，Node UI harness 另有 4 项因缺少 `window.addEventListener` 失败；没有修改无关 UI/harness。真实 EXIF v2 模型与真实浏览器展示未运行，因此保持 ACCEPTANCE PARTIAL。
