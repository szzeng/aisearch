# Changelog

## 0.6.0 — 2026-10-22 — Personal Beta（个人源码测试版）

- 统一应用、API 和包版本为 0.6.0；Catalog Schema 42、API 路径 v1 保持不变。
- 本次仅整理版本标记；未执行验证，详见 [版本更新记录](docs/reports/P4-RELEASE-060.md)。

## 0.5.0 — 2026-10-20 — Personal Beta（个人源码测试版）

- 统一照片时间轴、人物/地点浏览、地图、照片查看器和返回导航；增加照片收藏及收藏范围搜索。
- 普通照片合集支持保存搜索结果；智能合集保存筛选/搜索规则，支持刷新、排除与恢复、另存普通合集，并显示旧缓存、索引处理中及模型变化状态。
- 改进搜索条件、分页、立即保存、合集命名和地点词典查询；高度匹配统计识别 EXIF v2 模型身份。
- 照片查看器与完整详情可查看已保存的 OCR 原文；模型管理可检查覆盖率、安排建库及受控切换。
- 失效照片维护按持久候选分批复核，保留人工保护，支持中断恢复与失败后再次清理。
- 修复 Schema 40–42 下有效旧地点包被兼容上限误拒绝的问题；包文件、校验和、地点身份与人工归属保持原有规则。
- 当前 Catalog Schema 为 **42**，API 路径仍为 **v1**。应用、API 和包版本统一为 0.5.0。

这是 Windows 本机源码运行的个人测试版；提供 GitHub 源码归档，不提供已验收的 Windows 安装包。完整 CR2 人脸/OCR/语义建库链路仍属实验范围；真实图库 Qwen 性能、30 万容量 SLA、GTX 1660、干净机离线安装及部分浏览器边缘交互未认证，不作性能或正式发行承诺。已知轻微限制和本次验证结果见 [发布报告](docs/reports/P4-RELEASE-050.md)。

升级前停止应用并备份当前数据根，另行备份原图与模型权重；内置 Catalog 备份不包含它们。Schema 升级与软件版本独立，升级后的数据库不能靠检出旧 Git 版本降级；需要旧版时使用升级前的独立数据根备份。备份恢复要求与运行代码支持的 Schema 匹配，恢复命令本身不执行迁移。

## 0.3.0 — 2026-10-16 — UI 2.0 Personal Preview (draft)

- Introduces the UI 2.0 interface across photo timelines, people and collections, places and maps, search, photo viewing, and library maintenance.
- Adds face-level collection organization and applies collection membership consistently to photo counts, search scopes, and timelines.
- Updates photo viewing and navigation flows for the unified timeline experience.

Acceptance is still pending for the final face-collection browser write/read flow. Several UI browser, visual, and scale gates also remain pending. Catalog Schema is now 32; this preview is not a formal Windows release, and no real user catalog has been migrated as part of this preparation. Keep an independent backup of original photos and model weights; the built-in catalog backup does not include them.

## 0.2.0 — 2026-10-13 — Personal preview

- Local photo catalog with text and semantic search, uploaded-image search, and explicit model-profile and catalog-schema status.
- People, places, OCR, and collection browsing and management.
- Storage diagnostics, manual catalog backups, and bounded maintenance actions. Catalog backups do not include original photos or model weights.
- Video files are excluded from new photo scans and photo listings. Existing video-only records become missing after a successful refresh.
- People with no effective photo assets are hidden from person listings.
- Escape exits person batch management without discarding already saved operations.
- Pending-face suggestion cards now show the suggested person's reference avatar. This has synthetic UI test coverage; visual behavior on the user's library remains unverified.

This is a personal preview, not a formal Windows release. UI 2.0 and the unified photo timeline remain plans and are not included as delivered features. Real-library interaction checks and formal release gates remain separate.
