# P4-RELEASE-060 — 0.6.0 版本更新记录

日期：2026-10-22  
状态：**VERSIONED / VALIDATION SKIPPED AT USER REQUEST**

## 更新内容

按用户要求，将包、应用/API/OpenAPI、锁文件中的本地项目项、直接版本断言、README 与 CHANGELOG 标记为 0.6.0。当前代码使用 Catalog Schema 43，API v1 与第三方依赖保持现状。Release notes 总结 0.5.0 之后已合入的功能；不声称本轮重新验收这些功能。

## 0.5.0 之后的版本内容

- 增加 Qwen 1024D EXIF v2 试用配置，默认模型仍是 512D；真实图库建库/切换留待用户操作，卡片状态为 ACCEPTANCE PARTIAL。
- 智能合集 COMBINED 强匹配策略及缓存策略身份落在 Schema 43；相关任务的组合验收已完成。
- OCR v4 条件重处理与照片合集跨页面导航已合入；各自状态和证据见 [OCR 任务报告](P4-OCR-EVERYDAY-QUALITY.md) 与 [导航任务报告](P4-UI2-SHELL-COLLECTION-NAV-A.md)。

用户明确表示无需验证，因此没有为版本更新或 GitHub Release 重新运行测试、构建、锁文件检查或其他验证。既有功能任务的证据只沿用各自报告，不代表本次 Release 进行了新验证。

## Git 交付

版本更新提交：`3a23852c58266b1a2a595bc39eeb167455418f8d`。在修正 Schema 版本与 Release notes 后，0.6.0 tag 将指向最终候选提交；GitHub Release 为 Personal Beta prerelease。具体远端 tag、release URL、master 状态与后续回执以本文最终记录为准。
