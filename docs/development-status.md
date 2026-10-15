# Development status

## Current user-directed task: P4-PERSON-FAST-ORGANIZE-A — IMPLEMENTED / ACCEPTANCE PARTIAL (2026-10-15)

Schema v31→v32 增加脸级软锚点合集成员及批量 API；共享照片过滤与去重、独立保留忽略状态、People 整理入口和默认折叠脸级归档视图已实施。定向 JUnit **31/0/0/0**，受影响 Python Ruff、persons-albums.js node --check、git diff --check 通过。Luna High 全受影响面 review 的 finding 已修复，repair-only 复核关闭最终 finding；这不是 DS-H review。

浏览器认证及候选组明确选择已观察，但确认提交触发 Codex CUA/CDP 超时、后端没有收到写入，浏览器写入/回显仍 **PENDING**。来源 A→B 测试使用合成 FaceDocument 经过 persist_face_document、FaceIndexCoordinator 与 reconcile_asset，真实 source-crop worker 路径仍 **PENDING**。未标整卡 PASS。[任务卡](tasks/P4-PERSON-FAST-ORGANIZE-A.md) / [报告](reports/P4-PERSON-FAST-ORGANIZE-A.md)。

## Next task: P4-PERSON-FAST-ORGANIZE-B

只补隔离数据浏览器写入/查看路径、合集搜索/时间轴受影响证据及新版 source-crop worker 合成验收。若 CUA/CDP 超时重现，记录并停止重试；不接触真实图库、不下载新模型。[任务卡](tasks/P4-PERSON-FAST-ORGANIZE-B.md)。

上次状态已归档至 [development-status-archive-2026-10-15-before-fast-organize.md](development-status-archive-2026-10-15-before-fast-organize.md)；较早状态历史仍见 [development-status-archive-2026-10-15.md](development-status-archive-2026-10-15.md)。
