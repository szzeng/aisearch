# Development status

## Current: 性能设计规范落地

2026-10-18，当前卡为 [PERF-DESIGN-GUIDANCE](tasks/PERF-DESIGN-GUIDANCE.md)，报告见[性能设计规范落地](reports/PERF-DESIGN-GUIDANCE.md)。状态：PASS — 仅文档规范及一致性核对，无产品性能实测。新增[性能规范](performance.md)及[任务模板](tasks/TASK-TEMPLATE.md)，接入 AGENTS、V3.0 §12.5、V3.1 §4.2 与测试指南。个人库默认由执行者补三行：链路与风险、做法与边界、验证与停止；用户直接安排需求，无需手动改任务卡。八项仅按需展开，改查询不自动触发30万，未承诺的容量/发行资格不阻塞局部功能。用户已授权当前目录并修正 AGENTS 旧路径。

验证：9份文档本地链接、28项入口/字段、旧路径、变更范围与diff-check通过，实际exit0；仅文档不运行pytest/浏览器/性能基准。原始命令、JSON和文件身份留在忽略的 `test-results/perf-design-guidance/`。下一产品任务未选；旧卡下次激活时补齐性能段，不自动启动性能专项。用户已授权本次本地提交，不推送、不创建PR。

引用对话最后回答未能读取（仅占位符），本次依据仓库实际优化报告，不声称已逐条核对原建议；补充正文时在同一规范/报告内对照修订。

2026-10-19 补充复核：已修正任务卡生成器旧版 B/D 全量测试规则，接入三行性能模板；高频 SQL 在编码前检查工作量和索引，并补充 `808c4f5` 时间轴、向量及统计/并发经验。仅文档收口，无产品性能新结论。详见同一[报告](reports/PERF-DESIGN-GUIDANCE.md)。

## 产品基线与未关闭边界

当前产品HEAD为 `4690bc8`（PR #7合入），源码仍为已验证的搜索质量与本地产品完善成果；既有实现、组合验证、模型实验、30万局部对照及未验边界详见[搜索质量报告](reports/P4-SEARCH-QUALITY.md)和[原任务卡](tasks/P4-SEARCH-QUALITY.md)。不把历史组合证据称为本轮全仓回归，不追认正式发行或真实图库资格。

[人物整理卡](tasks/P4-PERSON-ORGANIZE-UX-CLEANUP.md)、[合流余项卡](tasks/P4-UPSTREAM-REVIEW-REMAINDER.md)中的既有双页面版本冲突及未单独浏览器检查的合集管理等范围仍PENDING，不因本次规范落地关闭。未访问真实图库，未启动这些任务。
