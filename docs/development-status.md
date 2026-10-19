# Development status

## Current: P4-UI2-VIEWER-PAN-AVATAR-FIX — 查看器平移与人物头像修复

2026-10-19，当前卡：[P4-UI2-VIEWER-PAN-AVATAR-FIX](tasks/P4-UI2-VIEWER-PAN-AVATAR-FIX.md)，报告：[执行报告](reports/P4-UI2-VIEWER-PAN-AVATAR-FIX.md)。状态：IMPLEMENTED / ACCEPTANCE PARTIAL。定向单测最终 28/28 通过；此前完整受影响组合 64/64 通过。最后重跑遇并行工作树新增 migration 0035 与 schema 支持版本 34 不一致，38 个 integration 在 fixture setup 报错；不把该次运行称为产品失败或 PASS。JS 语法、Ruff、diff-check 通过。按用户要求跳过浏览器，V01/V04 实际页面与图像边缘验证仍 PENDING。新增 V08 后确认 pointerdown 立即捕获舞台会重定向浏览器 click/dblclick target；现改为超过 4px 拖动阈值后才捕获，dblclick 仍限定图片目标，并抑制实际拖动形成的双击。原生事件序列模拟定向单测 1/1 通过。按用户要求不跑浏览器，真实 V08 由用户手测，仍 PENDING。前一轮提交为 `2df5fe8`；本轮 V08 修正尚未提交。

上一任务 P4-FAVORITES-CORE-A 的组合证据及原报告保留如下；不改变其既有状态。
## Current: P4-FAVORITES-CORE-A — 照片收藏核心

2026-10-19，当前卡：[P4-FAVORITES-CORE-A](tasks/P4-FAVORITES-CORE-A.md)，报告：[执行报告](reports/P4-FAVORITES-CORE-A.md)。状态：PASS（组合证据）。已新增 schema 0034、收藏服务/API、共享 viewer 操作及 `/?view=favorites` 收藏网格；收藏状态与 revision 幂等事务更新，游标 revision 失效、retention 保护和备份恢复均有覆盖。完整 unit/integration 执行 3204 项，3151 passed、51 skipped、2 failed；两条人物相册深链 Node harness 用例未改代码单独复跑 2 passed，因此如实标记组合验证，不将原整套运行写作全量 PASS。Ruff、JS 语法、diff-check 通过；内置浏览器隔离合成照片覆盖收藏、详情往返和取消后空态。无截图导出；未访问真实图库，未做收藏规模延迟结论。Luna High 独立复核无阻塞项。

下一任务卡 `P4-FAVORITES-UX-B` 保持 READY、尚未启动；本次只完成 A。

## Previous: 性能设计规范落地

2026-10-18，当前卡为 [PERF-DESIGN-GUIDANCE](tasks/PERF-DESIGN-GUIDANCE.md)，报告见[性能设计规范落地](reports/PERF-DESIGN-GUIDANCE.md)。状态：PASS — 仅文档规范及一致性核对，无产品性能实测。新增[性能规范](performance.md)及[任务模板](tasks/TASK-TEMPLATE.md)，接入 AGENTS、V3.0 §12.5、V3.1 §4.2 与测试指南。个人库默认由执行者补三行：链路与风险、做法与边界、验证与停止；用户直接安排需求，无需手动改任务卡。八项仅按需展开，改查询不自动触发30万，未承诺的容量/发行资格不阻塞局部功能。用户已授权当前目录并修正 AGENTS 旧路径。

验证：9份文档本地链接、28项入口/字段、旧路径、变更范围与diff-check通过，实际exit0；仅文档不运行pytest/浏览器/性能基准。原始命令、JSON和文件身份留在忽略的 `test-results/perf-design-guidance/`。下一产品任务未选；旧卡下次激活时补齐性能段，不自动启动性能专项。用户已授权本次本地提交，不推送、不创建PR。

引用对话最后回答未能读取（仅占位符），本次依据仓库实际优化报告，不声称已逐条核对原建议；补充正文时在同一规范/报告内对照修订。

2026-10-19 补充复核：已修正任务卡生成器旧版 B/D 全量测试规则，接入三行性能模板；高频 SQL 在编码前检查工作量和索引，并补充 `808c4f5` 时间轴、向量及统计/并发经验。仅文档收口，无产品性能新结论。详见同一[报告](reports/PERF-DESIGN-GUIDANCE.md)。

## 产品基线与未关闭边界

当前产品HEAD为 `4690bc8`（PR #7合入），源码仍为已验证的搜索质量与本地产品完善成果；既有实现、组合验证、模型实验、30万局部对照及未验边界详见[搜索质量报告](reports/P4-SEARCH-QUALITY.md)和[原任务卡](tasks/P4-SEARCH-QUALITY.md)。不把历史组合证据称为本轮全仓回归，不追认正式发行或真实图库资格。

[人物整理卡](tasks/P4-PERSON-ORGANIZE-UX-CLEANUP.md)、[合流余项卡](tasks/P4-UPSTREAM-REVIEW-REMAINDER.md)中的既有双页面版本冲突及未单独浏览器检查的合集管理等范围仍PENDING，不因本次规范落地关闭。未访问真实图库，未启动这些任务。
