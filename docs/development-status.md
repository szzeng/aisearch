# Development status

## Current user-directed task: P4-PERSON-NAV-IMPLEMENT-A — IMPLEMENTED / BASIC VALIDATED (2026-10-16)

已接入生产分类：人物／合集／待整理，待整理下未命名人物／未识别人脸分别显示和加载，保留原功能；分类切换清选择并恢复各自筛选。追加修复图片关闭误退列表：同 URL 图片历史不触发分类切换。最终定向 JUnit **28/0/0/0**、Node、Ruff、diff-check 通过；隔离合成库内置浏览器完成分类、筛选/选择、合集返回、前进/后退及人物／未命名／合集图片关闭（关闭按钮、Esc、浏览器返回）。Luna High 实现与 JS 限定复核不代替完整独立工程审查；长列表、200% 缩放、完整写入/撤销和性能未验收，不标整体 PASS。本轮仅本地范围提交，不推送。[任务卡](tasks/P4-PERSON-NAV-IMPLEMENT-A.md) / [报告与截图](reports/P4-PERSON-NAV-IMPLEMENT-A.md) / [后续审查卡](tasks/P4-PERSON-NAV-IMPLEMENT-A-review.md)。

## Product context: P4-PERSON-FAST-ORGANIZE-B — ACCEPTANCE PARTIAL (2026-10-16)

A 的 Schema v32/合集实现已在 `b389f8d`；B 增补新版 source-crop worker 合成链路和合集限定搜索/时间轴测试。定向 JUnit **4/0/0/0**，Ruff、`git diff --check` 通过；source-crop 与 search/timeline 状态 **PASS**。Luna High 子代理独立复核已完成、无 finding；这是 LUNA High review，不是 DS-H review。

浏览器新会话在隔离合成库成功认证、展开候选组并明确选中 2 张脸、选择现有测试合集。提交确认处重现 `Emulation.setFocusEmulationEnabled` 超时，停止交互；数据库核对无写入。浏览器写入/回显、单脸区域、原图/crop、移出计数与忽略状态仍 **PENDING**，B 和 Fast Organize 整体均不标 PASS。[B 任务卡](tasks/P4-PERSON-FAST-ORGANIZE-B.md) / [B 报告](reports/P4-PERSON-FAST-ORGANIZE-B.md)。

## Context: P4-PERSON-FAST-ORGANIZE-A

A 的 M1/M2 实施与定向 JUnit **31/0/0/0**、Luna High review 记录见 [A 任务卡](tasks/P4-PERSON-FAST-ORGANIZE-A.md) / [A 报告](reports/P4-PERSON-FAST-ORGANIZE-A.md)。B 已关闭其 source-crop synthetic 与搜索/时间轴证据缺口；浏览器闭环未完成。

上次状态已归档至 [development-status-archive-2026-10-15-before-fast-organize.md](development-status-archive-2026-10-15-before-fast-organize.md)；较早状态历史仍见 [development-status-archive-2026-10-15.md](development-status-archive-2026-10-15.md)。
