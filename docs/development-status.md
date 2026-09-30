# Development status

更新：2026-10-01。

当前任务：[整批改动检查与上游 PR](tasks/upstream-pr.md)，实现者整合检查完成，已提交 [上游 PR #1](https://github.com/szzeng/aisearch/pull/1)（Draft，未合并）。范围：FG-CLIP 2 / OCR 和搜索可靠性、Apple Silicon macOS、Vue 界面、离线行政层级与跨区景区。数据库 schema 18；地点语义和迁移 0017/0018 见 [ADR-0014](decisions/ADR-0014-offline-place-boundaries.md)。已安装公开中国 2,901 个历史区县边界和西湖景区包，不宣称最新全国覆盖。

最新验证：整体后端 1,857 项中 1,850 通过、6 项环境跳过；1 项旧 OCR 断言修正后相关 13 项复测全部通过，无未解决自检失败。前端 15 项、类型检查、生产构建、Ruff、差异检查、依赖锁及 wheel 资源一致性通过。公开 2,000 图目录已备份升级至 schema 18，地点页正常。准确命令、JUnit、修复和验证边界见[整合记录](tasks/upstream-pr.md)。

下一任务：[整批变更独立工程审查](tasks/frontend-review.md)，建议用户手动切换 DS-H 完整检查受影响面，再由 Sol high 聚焦高风险不变量；未执行，不标正式 PASS。本轮不自动切换模型。

此前 [macOS 实测](tasks/macos-benchmark.md)完成 Apple M4 / 16GB 的 MPS 图文、CPU OCR/人脸及 2,000 张公开图片测试；数据和模型在独立应用数据目录。十万/三十万真实图库、Windows/GTX 1660、冻结安装包和独立工程验收仍待完成。未扫描私人图库。
