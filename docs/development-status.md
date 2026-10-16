# Development status

## Current user-directed task: P4-PERSON-AVATAR-CLICK-A — IMPLEMENTED / BASIC VALIDATED (2026-10-16)

Luna High 已补齐“还没认出是谁 → 已忽略的人脸”：与待整理人脸、合集独立人脸一样，头像看照片、底框选择；人物级卡片保持原模式。最新定向 JUnit **4/0/0/0**，Node、Ruff、diff-check 通过，资源版本 `p4-person-avatar-click-a-3`。按要求未跑浏览器（`BROWSER_NOT_RUN_USER_REQUEST`），不标整体 PASS；保留共享提交 `7330bfd`，交付方式按用户追加要求为本地范围提交，不推送。下一步用户日常试用反馈。[任务卡](tasks/P4-PERSON-AVATAR-CLICK-A.md) / [报告](reports/P4-PERSON-AVATAR-CLICK-A.md)。

## Previous task: P4-PERSON-AVATAR-HOVER-A — IMPLEMENTED / BROWSER ACCEPTANCE PENDING (2026-10-16)

人物页头像（主头像、小头像及旧版人物工具头像/占位图）统一增加轻微悬停边框强调与柔和光圈，尊重减少动态效果设置。CSS 资源版本与断言已同步；未运行测试。用户要求跳过后续浏览器验证，单头像 DOM 探查不作为视觉验收通过。[任务卡](tasks/P4-PERSON-AVATAR-HOVER-A.md) / [报告](reports/P4-PERSON-AVATAR-HOVER-A.md)。

## Previous task: P4-PERSON-NAV-IMPLEMENT-A — IMPLEMENTED / BASIC VALIDATED (2026-10-16)

已接入生产分类：人物／合集／待整理，待整理下未命名人物／未识别人脸分别显示和加载，保留原功能；分类切换清选择并恢复各自筛选。追加修复图片关闭误退列表：同 URL 图片历史不触发分类切换。定向 JUnit **28/0/0/0**、Node、Ruff、diff-check 通过；隔离合成库内置浏览器完成分类、筛选/选择、合集返回、前进/后退及人物／未命名／合集图片关闭（关闭按钮、Esc、浏览器返回）。Luna High 实现与 JS 限定复核不代替完整独立工程审查；长列表、200% 缩放、完整写入/撤销和性能未验收，不标整体 PASS。本轮仅本地范围提交，不推送。[任务卡](tasks/P4-PERSON-NAV-IMPLEMENT-A.md) / [报告与截图](reports/P4-PERSON-NAV-IMPLEMENT-A.md) / [后续审查卡](tasks/P4-PERSON-NAV-IMPLEMENT-A-review.md)。

## Product context: P4-PERSON-FAST-ORGANIZE-B — ACCEPTANCE PARTIAL (2026-10-16)

A 的 Schema v32/合集实现已在 `b389f8d`；B 增补新版 source-crop worker 合成链路和合集限定搜索/时间轴测试。定向 JUnit **4/0/0/0**，Ruff、`git diff --check` 通过；source-crop 与 search/timeline 状态 **PASS**。Luna High 子代理独立复核已完成、无 finding；这是 LUNA High review，不是 DS-H review。

合集“人物”页读取 person members；“单独收录的人脸”仅读独立 face members。`CLIENT_ERROR` 的候选组渲染已修复。用户复测发现切换合集后会残留上一合集的旧卡片：现切换时作废旧请求并清理卡片/加载状态；后端查询本身按 collection_id 限定。进入“人物”页自动展开人脸区。隔离浏览器验证甲/乙各显示不同 face_id。用户反馈多张脸竖向堆叠后，确认原因为按候选 person_id 分全宽行；现将人脸卡直接放入统一响应式网格，辅助标签保留建议名，页面只显示一次说明；头像卡悬停显示手型鼠标，不改变边框或背景；进入合集人物相册会隐藏合集专属的人脸区域。8 张合成脸在内置浏览器显示为同一画廊，定向 UI JUnit **1 passed**；截图及原因见 [B 验收报告](reports/P4-PERSON-FAST-ORGANIZE-B.md)。Node、Ruff、diff-check 通过。截图与报告见 [B 验收报告](reports/P4-PERSON-FAST-ORGANIZE-B.md)。脸卡移出、原图/crop、忽略状态及合集搜索/时间轴交互仍 **PENDING**，B 和 Fast Organize 整体不标 PASS。[B 任务卡](tasks/P4-PERSON-FAST-ORGANIZE-B.md) / [B 报告](reports/P4-PERSON-FAST-ORGANIZE-B.md)。

## Context: P4-PERSON-FAST-ORGANIZE-A

A 的 M1/M2 实施与定向 JUnit **31/0/0/0**、Luna High review 记录见 [A 任务卡](tasks/P4-PERSON-FAST-ORGANIZE-A.md) / [A 报告](reports/P4-PERSON-FAST-ORGANIZE-A.md)。B 已关闭其 source-crop synthetic 与搜索/时间轴证据缺口；浏览器闭环未完成。

上次状态已归档至 [development-status-archive-2026-10-15-before-fast-organize.md](development-status-archive-2026-10-15-before-fast-organize.md)；较早状态历史仍见 [development-status-archive-2026-10-15.md](development-status-archive-2026-10-15.md)。
